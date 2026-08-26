"""
STT/TTS provider unit tests (§24, §25, file 10 prompt 3).

Exercises `LocalWhisperSTT`/`LocalPyttsx3TTS` in isolation, against fully faked-out
`faster_whisper`/`pyttsx3` SDK surfaces -- same isolation convention as
tests/llm/test_ollama_provider.py (every real SDK/network seam is replaced with a
duck-typed stand-in) -- so these tests never load an actual Whisper model or bind to a
real OS TTS driver, and pass in CI regardless of what's installed or what audio
hardware is present.

Covers each provider's contract end to end: `transcribe()`/`synthesize()`/`speak()` are
invoked with the right text/bytes, `is_available()` never raises and correctly reflects
whether the underlying dependency is importable/bindable (§41 Rule 3).
"""

from __future__ import annotations

import pytest

from app.config.settings import Settings
from app.voice import stt as stt_module
from app.voice import tts as tts_module
from app.voice.stt import LocalWhisperSTT
from app.voice.tts import LocalPyttsx3TTS


def _settings(**overrides) -> Settings:
    """A Settings instance that never reads the repo's real `.env` -- every field
    relevant to STT/TTS is set explicitly by the caller/defaults below (same
    convention as tests/llm/test_ollama_provider.py's `_settings()`).
    """
    defaults: dict = dict(
        stt_whisper_model_size="base",
        stt_whisper_device="cpu",
        stt_whisper_compute_type="int8",
        tts_rate_wpm=175,
        tts_volume=1.0,
        tts_voice_id=None,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


# --- LocalWhisperSTT ---------------------------------------------------------------


class _FakeSegment:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeWhisperModel:
    """Stand-in for `faster_whisper.WhisperModel` -- records constructor args and
    every `transcribe()` call so tests can assert both without loading a real model.
    """

    instances: list["_FakeWhisperModel"] = []

    def __init__(self, model_size, device=None, compute_type=None) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.transcribe_calls: list = []
        _FakeWhisperModel.instances.append(self)

    def transcribe(self, audio_file):
        self.transcribe_calls.append(audio_file)
        # Two segments with stray whitespace -- proves transcribe() strips each
        # segment individually and joins with a single space (see LocalWhisperSTT's
        # docstring on why it doesn't just rely on faster-whisper's own spacing).
        return [_FakeSegment(" hello "), _FakeSegment("world ")], {"language": "en"}


@pytest.fixture(autouse=True)
def _reset_fake_whisper_instances():
    _FakeWhisperModel.instances = []
    yield
    _FakeWhisperModel.instances = []


def test_is_available_true_when_faster_whisper_importable(monkeypatch):
    monkeypatch.setattr(stt_module, "WhisperModel", _FakeWhisperModel)
    assert LocalWhisperSTT(_settings()).is_available() is True


def test_is_available_false_when_faster_whisper_not_installed(monkeypatch):
    monkeypatch.setattr(stt_module, "WhisperModel", None)
    assert LocalWhisperSTT(_settings()).is_available() is False


def test_transcribe_returns_joined_segment_text(monkeypatch):
    monkeypatch.setattr(stt_module, "WhisperModel", _FakeWhisperModel)
    provider = LocalWhisperSTT(_settings())

    result = provider.transcribe(b"raw-audio-bytes")

    assert result == "hello world"


def test_transcribe_loads_model_with_configured_settings(monkeypatch):
    monkeypatch.setattr(stt_module, "WhisperModel", _FakeWhisperModel)
    provider = LocalWhisperSTT(
        _settings(
            stt_whisper_model_size="small",
            stt_whisper_device="cuda",
            stt_whisper_compute_type="float16",
        )
    )

    provider.transcribe(b"raw-audio-bytes")

    assert len(_FakeWhisperModel.instances) == 1
    model = _FakeWhisperModel.instances[0]
    assert model.model_size == "small"
    assert model.device == "cuda"
    assert model.compute_type == "float16"


def test_transcribe_reuses_lazily_loaded_model_across_calls(monkeypatch):
    monkeypatch.setattr(stt_module, "WhisperModel", _FakeWhisperModel)
    provider = LocalWhisperSTT(_settings())

    provider.transcribe(b"first")
    provider.transcribe(b"second")

    # One model load total across two transcribe() calls -- see
    # LocalWhisperSTT._get_model()'s lazy-cache docstring.
    assert len(_FakeWhisperModel.instances) == 1
    assert len(_FakeWhisperModel.instances[0].transcribe_calls) == 2


def test_transcribe_raises_when_faster_whisper_not_installed(monkeypatch):
    monkeypatch.setattr(stt_module, "WhisperModel", None)
    provider = LocalWhisperSTT(_settings())

    with pytest.raises(RuntimeError, match="faster-whisper is not installed"):
        provider.transcribe(b"raw-audio-bytes")


# --- LocalPyttsx3TTS -----------------------------------------------------------------


class _FakeEngine:
    """Stand-in for a `pyttsx3.Engine` -- records every property set / method call
    and, for `save_to_file`, actually writes deterministic bytes to disk so
    `LocalPyttsx3TTS.synthesize()`'s real write-then-read-back-from-tmp-file path
    (see its docstring on why pyttsx3 has no in-memory render path) is exercised end
    to end rather than stubbed out.
    """

    def __init__(self) -> None:
        self.properties: dict = {}
        self.said: list[str] = []
        self.saved: list[tuple[str, str]] = []
        self.run_and_wait_calls = 0
        self.stop_calls = 0

    def setProperty(self, name, value):
        self.properties[name] = value

    def say(self, text):
        self.said.append(text)

    def save_to_file(self, text, path):
        self.saved.append((text, path))
        with open(path, "wb") as f:
            f.write(f"FAKE-AUDIO:{text}".encode())

    def runAndWait(self):
        self.run_and_wait_calls += 1

    def stop(self):
        self.stop_calls += 1


class _FakePyttsx3:
    """Stand-in for the `pyttsx3` module -- `init()` hands out a fresh `_FakeEngine`
    each call, matching `LocalPyttsx3TTS._new_engine()`'s "never reuse an engine
    instance" convention, and records every engine it created.
    """

    def __init__(self, init_side_effect=None) -> None:
        self.engines: list[_FakeEngine] = []
        self._init_side_effect = init_side_effect

    def init(self):
        if self._init_side_effect is not None:
            self._init_side_effect()
        engine = _FakeEngine()
        self.engines.append(engine)
        return engine


def test_is_available_true_when_init_succeeds(monkeypatch):
    fake = _FakePyttsx3()
    monkeypatch.setattr(tts_module, "pyttsx3", fake)

    assert LocalPyttsx3TTS(_settings()).is_available() is True
    # init() + stop() round trip, no lingering engine kept around afterward.
    assert fake.engines[0].stop_calls == 1


def test_is_available_false_when_pyttsx3_not_installed(monkeypatch):
    monkeypatch.setattr(tts_module, "pyttsx3", None)
    assert LocalPyttsx3TTS(_settings()).is_available() is False


def test_is_available_false_when_init_raises(monkeypatch):
    def _boom():
        raise RuntimeError("no driver bound")

    fake = _FakePyttsx3(init_side_effect=_boom)
    monkeypatch.setattr(tts_module, "pyttsx3", fake)

    assert LocalPyttsx3TTS(_settings()).is_available() is False


def test_synthesize_returns_bytes_written_by_the_engine(monkeypatch):
    fake = _FakePyttsx3()
    monkeypatch.setattr(tts_module, "pyttsx3", fake)
    provider = LocalPyttsx3TTS(_settings())

    result = provider.synthesize("hello there")

    assert result == b"FAKE-AUDIO:hello there"


def test_synthesize_invokes_save_to_file_with_the_given_text(monkeypatch):
    fake = _FakePyttsx3()
    monkeypatch.setattr(tts_module, "pyttsx3", fake)
    provider = LocalPyttsx3TTS(_settings())

    provider.synthesize("hello there")

    engine = fake.engines[0]
    assert engine.saved[0][0] == "hello there"
    assert engine.run_and_wait_calls == 1
    assert engine.stop_calls == 1


def test_synthesize_applies_configured_voice_properties(monkeypatch):
    fake = _FakePyttsx3()
    monkeypatch.setattr(tts_module, "pyttsx3", fake)
    provider = LocalPyttsx3TTS(
        _settings(tts_rate_wpm=200, tts_volume=0.5, tts_voice_id="voice-1")
    )

    provider.synthesize("hello there")

    engine = fake.engines[0]
    assert engine.properties == {"rate": 200, "volume": 0.5, "voice": "voice-1"}


def test_synthesize_does_not_set_voice_property_when_unconfigured(monkeypatch):
    fake = _FakePyttsx3()
    monkeypatch.setattr(tts_module, "pyttsx3", fake)
    provider = LocalPyttsx3TTS(_settings(tts_voice_id=None))

    provider.synthesize("hello there")

    assert "voice" not in fake.engines[0].properties


def test_synthesize_raises_when_pyttsx3_not_installed(monkeypatch):
    monkeypatch.setattr(tts_module, "pyttsx3", None)
    provider = LocalPyttsx3TTS(_settings())

    with pytest.raises(RuntimeError, match="pyttsx3 is not installed"):
        provider.synthesize("hello there")


def test_speak_invokes_say_with_the_given_text(monkeypatch):
    fake = _FakePyttsx3()
    monkeypatch.setattr(tts_module, "pyttsx3", fake)
    provider = LocalPyttsx3TTS(_settings())

    provider.speak("hello there")

    engine = fake.engines[0]
    assert engine.said == ["hello there"]
    assert engine.run_and_wait_calls == 1
    assert engine.stop_calls == 1


def test_speak_raises_when_pyttsx3_not_installed(monkeypatch):
    monkeypatch.setattr(tts_module, "pyttsx3", None)
    provider = LocalPyttsx3TTS(_settings())

    with pytest.raises(RuntimeError, match="pyttsx3 is not installed"):
        provider.speak("hello there")
