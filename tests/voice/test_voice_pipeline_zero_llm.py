"""
Voice pipeline zero-LLM test (§24, §25, §38, §41 Rule 7, file 10 prompt 3).

Mirrors tests/core/test_zero_llm.py, but drives the same deterministic command through
`POST /api/voice/message` instead of calling `AssistantCore.handle()` directly --
proving `app.api.routes.voice` doesn't reimplement or shortcut any of the routing
`AssistantCore` already does for the text path (§41 Rule 7). STT itself is mocked (a
fake `SpeechToTextProvider` returning a fixed, pre-transcribed "open vscode" regardless
of the uploaded audio's actual content) since exercising a real Whisper model is
`tests/voice/test_stt_tts.py`'s job, not this file's -- what this file checks is
everything *downstream* of transcription: the transcript is handed to the exact same
`DesktopAdapter` -> `AssistantCore.handle()` path the text endpoint uses, resolves
deterministically, and never reaches an LLM/network call.

`httpx`/`requests` are monkeypatched to raise if called at all (same convention and
same rationale as test_zero_llm.py's `no_network` fixture) -- so this is a hard
assertion of "0 LLM/provider calls", not just a check of the `used_llm` flag.
"""

from __future__ import annotations

import io
from unittest.mock import Mock

import httpx
import pytest

from app.api.dependencies import (
    get_health_manager,
    get_stt_provider,
    get_tool_registry,
    get_tts_provider,
)
from app.api.local_only import LOCAL_CLIENT_HOSTS
from app.database.database import get_db
from app.llm.health import HealthManager
from app.tools import applications as applications_module
from app.tools import register_default_tools
from app.tools.registry import ToolRegistry
from main import app

try:
    import requests
except ImportError:  # pragma: no cover - not installed in this project (see requirements.txt)
    requests = None


def _blocked(*_args, **_kwargs):
    raise AssertionError("Network call attempted during a zero-LLM voice command -- see §38/§9.")


async def _blocked_async(*_args, **_kwargs):
    raise AssertionError("Network call attempted during a zero-LLM voice command -- see §38/§9.")


@pytest.fixture()
def no_network(monkeypatch):
    """Adapted from tests/core/test_zero_llm.py's fixture of the same name --
    duplicated here rather than imported so this file stays self-contained (test
    modules aren't meant to import fixtures from one another). Deliberately does *not*
    block `httpx.Client.send`: unlike test_zero_llm.py (which calls
    `AssistantCore.handle()` directly, no HTTP involved), this file drives requests
    through FastAPI's `TestClient`, which is itself an `httpx.Client` wired to an
    in-process ASGI transport -- blocking `Client.send` would also block the test's
    own request, not just a real one. `httpx.get`/`httpx.post` (Ollama's sync probe)
    and `httpx.AsyncClient.send` (Ollama's async chat call and the Gemini SDK's async
    transport, see file 05/07) cover every real outbound seam this app actually uses.
    """
    monkeypatch.setattr(httpx, "get", _blocked)
    monkeypatch.setattr(httpx, "post", _blocked)
    monkeypatch.setattr(httpx.AsyncClient, "send", _blocked_async)
    if requests is not None:  # pragma: no cover - exercised only if requests is installed
        monkeypatch.setattr(requests.sessions.Session, "request", _blocked)
        monkeypatch.setattr(requests, "get", _blocked)
        monkeypatch.setattr(requests, "post", _blocked)


class _FakeSTT:
    """`SpeechToTextProvider` stand-in: ignores whatever audio bytes it's handed and
    always returns `transcript` -- the "pre-transcribed spoken command" the module
    docstring describes -- while still recording what it was called with, so a test
    can confirm the endpoint actually reads/forwards the uploaded audio rather than
    the text path.
    """

    name = "fake_stt"

    def __init__(self, transcript: str) -> None:
        self.transcript = transcript
        self.transcribe_calls: list[bytes] = []

    def is_available(self) -> bool:
        return True

    def transcribe(self, audio_bytes: bytes) -> str:
        self.transcribe_calls.append(audio_bytes)
        return self.transcript


class _FakeTTS:
    """`TextToSpeechProvider` stand-in that reports itself unavailable -- the voice
    route already treats an unavailable TTS provider as a soft no-op (see
    `app.api.routes.voice`'s docstring), so this keeps the response text-only and
    keeps this file focused on the routing question, not speech synthesis (that's
    tests/voice/test_stt_tts.py's job).
    """

    name = "fake_tts"

    def is_available(self) -> bool:
        return False

    def synthesize(self, text: str) -> bytes:  # pragma: no cover - never reached
        raise AssertionError("synthesize() should never be called when is_available() is False.")


@pytest.fixture()
def stt(monkeypatch):
    fake_stt = _FakeSTT(transcript="open vscode")

    monkeypatch.setattr(applications_module.os, "startfile", Mock(), raising=False)
    monkeypatch.setattr(applications_module.subprocess, "Popen", Mock())

    registry = ToolRegistry()
    register_default_tools(registry)

    app.dependency_overrides[get_tool_registry] = lambda: registry
    app.dependency_overrides[get_health_manager] = lambda: HealthManager()
    app.dependency_overrides[get_stt_provider] = lambda: fake_stt
    app.dependency_overrides[get_tts_provider] = lambda: _FakeTTS()
    yield fake_stt
    app.dependency_overrides.clear()


@pytest.fixture()
def client(test_db, stt, monkeypatch):
    from fastapi.testclient import TestClient

    def override_get_db():
        db = test_db()
        try:
            yield db
        finally:
            db.close()

    # POST /api/voice/message always runs as platform="desktop" (app.api.local_only,
    # file 11 prompt 3), and Starlette's TestClient reports a fixed synthetic client
    # host ("testclient") that isn't real loopback -- trust it here so these tests
    # keep exercising the voice pipeline itself, not the local-only boundary (that
    # boundary has its own coverage in tests/api/test_desktop_local_only.py).
    monkeypatch.setattr("app.api.local_only.LOCAL_CLIENT_HOSTS", LOCAL_CLIENT_HOSTS | {"testclient"})

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def _post_audio(client) -> "httpx.Response":
    return client.post(
        "/api/voice/message",
        files={"audio": ("command.wav", io.BytesIO(b"fake wav bytes"), "audio/wav")},
    )


def test_deterministic_spoken_command_is_zero_llm(no_network, client, stt):
    response = _post_audio(client)
    assert response.status_code == 200

    body = response.json()
    assert body["transcript"] == "open vscode"
    assert body["used_llm"] is False
    assert body["tool_calls"][0]["tool_name"] == "open_application"
    assert body["tool_calls"][0]["result"]["success"] is True


def test_stt_provider_actually_receives_the_uploaded_audio(no_network, client, stt):
    _post_audio(client)

    # Proves the endpoint's audio path (not the text= path) is what's being exercised
    # -- the fake STT was invoked with the real uploaded bytes exactly once.
    assert stt.transcribe_calls == [b"fake wav bytes"]


def test_voice_dry_run_never_reaches_assistant_core_or_the_network(no_network, client):
    # dry_run=true is pure STT preview (see app.api.routes.voice's module docstring)
    # -- AssistantCore.handle() is never called, so this is zero LLM calls almost by
    # construction, but the `no_network` fixture still backs that up directly.
    response = client.post(
        "/api/voice/message",
        data={"dry_run": "true"},
        files={"audio": ("command.wav", io.BytesIO(b"fake wav bytes"), "audio/wav")},
    )
    assert response.status_code == 200

    body = response.json()
    assert body["transcript"] == "open vscode"
    assert body["tool_calls"] == []
    assert body["used_llm"] is False
