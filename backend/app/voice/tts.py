"""
Text-to-speech provider interface + local implementation (§24, §25, file 10).

Same split as `app.voice.stt` (and `LLMProvider` before it, `app.llm.base`, file 05):
calling code depends only on `TextToSpeechProvider` below, never on `LocalPyttsx3TTS`
or any vendor SDK directly, so a future cloud TTS provider can be added later without
touching a single call site --

    TextToSpeechProvider
    |
    +-- Local provider   (this module's LocalPyttsx3TTS)
    +-- Future cloud provider

The protocol's one required method is `synthesize(text) -> bytes` rather than a
playback-only `speak(text)`: bytes is the shape a *future cloud provider* naturally
returns (an HTTP response body), and it's also what today's desktop caller needs if
audio is to be sent to a frontend to play (§37 Phase 9 / file 10 prompt 2's voice
endpoint returns "both the text and audio"). Keeping playback-only local-speaker
convenience separate (see `LocalPyttsx3TTS.speak()` below) means calling code that
stays on the `TextToSpeechProvider` contract is automatically portable to a provider
that has no local speaker to play through at all.

`LocalPyttsx3TTS` wraps `pyttsx3`, which drives each OS's native offline TTS engine
(SAPI5 on Windows, NSSpeechSynthesizer on macOS, espeak on Linux) -- no API key, no
per-request cost, preserving the zero-cost objective (§25).
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Protocol, runtime_checkable

from app.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

try:
    import pyttsx3
except ImportError:  # pragma: no cover - exercised only when the optional dep is absent
    pyttsx3 = None  # type: ignore[assignment]


@runtime_checkable
class TextToSpeechProvider(Protocol):
    """Contract every text-to-speech provider satisfies. `is_available()` must never
    raise and never assume availability (§41 Rule 3)."""

    name: str

    def is_available(self) -> bool: ...

    def synthesize(self, text: str) -> bytes:
        """Render `text` to audio bytes (WAV) that a caller can play back locally or
        return over HTTP to a client -- this call does not itself play any audio.
        """
        ...


class LocalPyttsx3TTS:
    """`TextToSpeechProvider` backed by `pyttsx3`'s native OS voice. Fully offline --
    no API key, no per-request cost.
    """

    name = "pyttsx3_local"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def is_available(self) -> bool:
        """Attempts (and immediately tears down) a real `pyttsx3.init()` -- unlike
        `LocalWhisperSTT.is_available()`'s pure import check, this is cheap enough to
        actually run: pyttsx3's init just binds to whichever native driver the OS
        already has installed (no model download, no long-running load), closer in
        spirit to `OllamaProvider`'s "actually probe, but keep it fast" convention
        than to a pure import check. A driver that can't be found/bound (e.g. no TTS
        voice installed on a minimal Linux image) is exactly the "not available"
        case this exists to catch -- never raised, always folded into `False`.
        """
        if pyttsx3 is None:
            return False
        try:
            engine = pyttsx3.init()
        except Exception as exc:  # noqa: BLE001 -- any init failure means "unavailable"
            logger.info("pyttsx3 availability probe failed: %s", exc)
            return False
        engine.stop()
        return True

    def _new_engine(self) -> "pyttsx3.Engine":
        # A fresh engine per call, not one cached on self -- pyttsx3's SAPI5 driver is
        # known to misbehave (hang or ignore subsequent calls) when the same engine
        # instance runs say()/save_to_file() + runAndWait() more than once, so a new
        # engine per synthesize()/speak() call is the safe convention rather than an
        # optimization worth second-guessing.
        engine = pyttsx3.init()
        engine.setProperty("rate", self._settings.tts_rate_wpm)
        engine.setProperty("volume", self._settings.tts_volume)
        if self._settings.tts_voice_id:
            engine.setProperty("voice", self._settings.tts_voice_id)
        return engine

    def synthesize(self, text: str) -> bytes:
        if pyttsx3 is None:
            raise RuntimeError(
                "pyttsx3 is not installed -- add it to requirements.txt and run "
                "`pip install -r backend/requirements.txt`."
            )
        engine = self._new_engine()
        # pyttsx3 has no in-memory render path -- save_to_file() always writes to a
        # real path on disk. mkstemp + an immediate os.close() (rather than
        # NamedTemporaryFile's context manager) avoids holding our own open handle on
        # the file while pyttsx3's driver writes to that same path, which Windows'
        # exclusive file locking would otherwise reject.
        fd, tmp_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            engine.save_to_file(text, tmp_path)
            engine.runAndWait()
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            engine.stop()
            os.unlink(tmp_path)

    def speak(self, text: str) -> None:
        """Desktop convenience: play `text` directly through the local speakers
        without the temp-file round trip `synthesize()` needs to produce bytes. Not
        part of `TextToSpeechProvider` -- calling code that wants to stay
        provider-agnostic (and portable to a future cloud provider, which has no
        local speaker to play through) should call `synthesize()` and play the
        returned bytes instead. This exists purely as a lower-overhead path for
        today's desktop-only use case (§25/file 10).
        """
        if pyttsx3 is None:
            raise RuntimeError(
                "pyttsx3 is not installed -- add it to requirements.txt and run "
                "`pip install -r backend/requirements.txt`."
            )
        engine = self._new_engine()
        try:
            engine.say(text)
            engine.runAndWait()
        finally:
            engine.stop()
