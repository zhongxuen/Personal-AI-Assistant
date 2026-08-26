"""
Speech-to-text provider interface + local implementation (§24, §25, file 10).

Provider-agnostic contract every speech-to-text provider implements, same split as
`LLMProvider` (`app.llm.base`, file 05): calling code (the voice endpoint added in
file 10 prompt 2, and `AssistantCore` beyond it) depends only on `SpeechToTextProvider`
below, never on `LocalWhisperSTT` or any vendor SDK directly, so a future cloud STT
provider (e.g. a hosted Whisper API) can be dropped in later without touching a single
call site -- exactly the extensibility the development plan calls for in §25:

    SpeechToTextProvider
    |
    +-- Local provider   (this module's LocalWhisperSTT)
    +-- Future cloud provider

`LocalWhisperSTT` wraps `faster-whisper` (a CTranslate2 reimplementation of OpenAI's
Whisper) so transcription runs fully on-device -- no API key, no per-request cost,
preserving the zero-cost objective (§25). The one caveat worth flagging: the *model
weights* are fetched from Hugging Face Hub on first use and cached locally after that
(`~/.cache/huggingface`), so the very first `transcribe()` call needs network access
once. That mirrors `OllamaProvider`'s "must be pulled first" caveat (file 07) more than
`GeminiProvider`'s pure offline check -- see `is_available()`'s docstring for how this
module treats that distinction.

Unlike `LLMProvider`, this interface has no `LLMStatus` taxonomy (SUCCESS/RETRYABLE/
QUOTA_EXHAUSTED/PERMANENT) -- there's no failover chain or usage budget for a single
local model to reason about here (§37 Phase 9 explicitly keeps voice's LLM routing
identical to text's; it does not add new routing concepts for speech itself). Errors
(missing dependency, corrupt/unsupported audio, a failed model load) are raised
directly out of `transcribe()` as ordinary exceptions -- the caller (file 10 prompt 2's
voice endpoint) is responsible for catching and turning that into a user-facing error,
the same way it would handle any other unexpected failure.
"""

from __future__ import annotations

import io
import logging
from typing import Protocol, runtime_checkable

from app.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

try:
    from faster_whisper import WhisperModel
except ImportError:  # pragma: no cover - exercised only when the optional dep is absent
    WhisperModel = None  # type: ignore[assignment,misc]


@runtime_checkable
class SpeechToTextProvider(Protocol):
    """Contract every speech-to-text provider satisfies. `is_available()` must never
    raise and never assume availability (§41 Rule 3) -- for a local provider like
    `LocalWhisperSTT` that means "is the dependency importable", not "has the model
    already been downloaded and verified to load", which would require an expensive
    (possibly network-hitting) load on every check. That mirrors `LLMProvider`'s
    convention of keeping `is_available()` cheap: `GeminiProvider` checks for a
    configured key rather than making a network call, and this does the analogous
    "cheap local check" for an installed local package instead of a configured
    credential.
    """

    name: str

    def is_available(self) -> bool: ...

    def transcribe(self, audio_bytes: bytes) -> str:
        """Transcribe one complete utterance (WAV/MP3/etc. bytes, whatever ffmpeg/PyAV
        can decode) to text. Callers are expected to hand over one bounded recording
        per call (push-to-talk, file 10 prompt 2) -- this is not a streaming API.
        """
        ...


class LocalWhisperSTT:
    """`SpeechToTextProvider` backed by a local `faster-whisper` model. Fully offline
    once the model weights are cached -- no API key, no per-request cost.
    """

    name = "whisper_local"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        # Loaded lazily on first transcribe(), not in __init__, so constructing a
        # LocalWhisperSTT never touches disk/network -- same convention as
        # GeminiProvider's lazily-built SDK client, and it keeps is_available() cheap.
        self._model: "WhisperModel | None" = None

    def is_available(self) -> bool:
        """Cheap local check only (§41 Rule 3): is `faster-whisper` importable. This
        deliberately does *not* attempt to load the model (that can mean downloading
        hundreds of MB on first run) or otherwise probe the network -- an operator who
        `pip install`-ed the dependency but hasn't run it yet still reads as
        "available"; a first-use download failure surfaces from `transcribe()`
        itself, same as an Ollama model that turns out not to be pulled surfaces from
        `generate()`'s pre-flight probe rather than `is_available()`.
        """
        return WhisperModel is not None

    def _get_model(self) -> "WhisperModel":
        if self._model is None:
            self._model = WhisperModel(
                self._settings.stt_whisper_model_size,
                device=self._settings.stt_whisper_device,
                compute_type=self._settings.stt_whisper_compute_type,
            )
        return self._model

    def transcribe(self, audio_bytes: bytes) -> str:
        if WhisperModel is None:
            raise RuntimeError(
                "faster-whisper is not installed -- add it to requirements.txt and "
                "run `pip install -r backend/requirements.txt`."
            )
        model = self._get_model()
        # faster-whisper accepts a file path, numpy array, or any binary file-like
        # object -- a BytesIO around the raw bytes the caller received (e.g. from an
        # uploaded audio blob) needs no intermediate temp file on disk.
        segments, _info = model.transcribe(io.BytesIO(audio_bytes))
        # `segments` is a generator yielding one item per detected speech segment;
        # joining with a space and normalizing surrounding whitespace per segment
        # avoids relying on whether faster-whisper's own segment text happens to
        # already carry a leading space.
        return " ".join(segment.text.strip() for segment in segments).strip()
