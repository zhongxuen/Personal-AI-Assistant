"""
Voice pipeline reasoning-path test (§20, §24, §25, §41 Rule 7, file 10 prompt 3).

Companion to test_voice_pipeline_zero_llm.py: that file proves a deterministic spoken
command never reaches an LLM; this file proves the opposite case -- a reasoning-oriented
spoken command that `CommandRouter` can't resolve deterministically -- correctly falls
through to `AIRouter` (mocked here, same convention as
tests/core/test_assistant_llm_path.py/test_context_reduction.py: no real network call,
`AIRouter`'s own chain-walking/failover behavior is covered separately in
tests/llm/test_ai_router.py).

The real point of this file is the parity check: `POST /api/voice/message` (STT mocked
to return a fixed transcript) and `POST /api/assistant/message` (the same message sent
as plain text) are driven with the *same* message and asserted to produce the same
`AIRouter.route()` call and the same response shape -- proving voice doesn't get its own
routing path or a shortcut around `AssistantCore` (§41 Rule 7); it's the exact same
`DesktopAdapter.to_request()` -> `AssistantCore.handle()` pipeline the text endpoint
uses, just fed by a transcript instead of typed input.
"""

from __future__ import annotations

import io
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_health_manager,
    get_stt_provider,
    get_tool_registry,
    get_tts_provider,
)
from app.database.database import get_db
from app.llm.ai_router import AIRouter
from app.llm.base import LLMResult
from app.llm.health import HealthManager
from app.tools import register_default_tools
from app.tools.registry import ToolRegistry
from main import app

# Contains no trigger/alias any default tool registers -- CommandRouter always falls
# through to classification LLM_REQUIRED for it, same message shape as
# tests/core/test_assistant_llm_path.py's UNRESOLVABLE_MESSAGE and
# tests/core/test_zero_llm.py's final test.
REASONING_MESSAGE = "what's the weather like tomorrow?"


class _FakeSTT:
    """`SpeechToTextProvider` stand-in returning a fixed, pre-transcribed reasoning
    command regardless of the uploaded audio -- see
    test_voice_pipeline_zero_llm.py's `_FakeSTT` for the same convention.
    """

    name = "fake_stt"

    def __init__(self, transcript: str) -> None:
        self.transcript = transcript

    def is_available(self) -> bool:
        return True

    def transcribe(self, audio_bytes: bytes) -> str:
        return self.transcript


class _FakeTTS:
    """Reports itself unavailable so a synthesis call never happens -- speech
    synthesis is tests/voice/test_stt_tts.py's job, not this file's.
    """

    name = "fake_tts"

    def is_available(self) -> bool:
        return False

    def synthesize(self, text: str) -> bytes:  # pragma: no cover - never reached
        raise AssertionError("synthesize() should never be called when is_available() is False.")


@pytest.fixture()
def mocked_ai_router_route(monkeypatch):
    """Patches `AIRouter.route` at the class level (not on a specific instance) --
    `AssistantCore.__init__` builds a brand new `AIRouter` per request inside the route
    handler (see `app.core.assistant`), so there's no single instance for a test
    driving the app over HTTP to reach into and mock, unlike
    tests/core/test_assistant_llm_path.py's `core.ai_router.route = AsyncMock(...)`.
    A plain `AsyncMock` isn't a descriptor, so `some_ai_router_instance.route(...)`
    resolves to this same mock unbound (no implicit `self`), exactly like patching
    `httpx.Client.send` in tests/core/test_zero_llm.py's `no_network` fixture.
    """
    mock_route = AsyncMock(return_value=LLMResult(status="SUCCESS", text="a reasoned answer"))
    monkeypatch.setattr(AIRouter, "route", mock_route)
    return mock_route


@pytest.fixture()
def registry() -> ToolRegistry:
    registry = ToolRegistry()
    register_default_tools(registry)
    return registry


@pytest.fixture()
def client(test_db, registry, mocked_ai_router_route):
    def override_get_db():
        db = test_db()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_tool_registry] = lambda: registry
    app.dependency_overrides[get_health_manager] = lambda: HealthManager()
    app.dependency_overrides[get_stt_provider] = lambda: _FakeSTT(transcript=REASONING_MESSAGE)
    app.dependency_overrides[get_tts_provider] = lambda: _FakeTTS()
    yield TestClient(app)
    app.dependency_overrides.clear()


def _post_voice_audio(client):
    return client.post(
        "/api/voice/message",
        files={"audio": ("command.wav", io.BytesIO(b"fake wav bytes"), "audio/wav")},
    )


def _post_text_message(client):
    return client.post(
        "/api/assistant/message",
        json={"user_id": "local-user", "platform": "desktop", "message": REASONING_MESSAGE},
    )


def test_reasoning_spoken_command_reaches_ai_router(client, mocked_ai_router_route):
    response = _post_voice_audio(client)
    assert response.status_code == 200

    body = response.json()
    assert body["transcript"] == REASONING_MESSAGE
    assert body["used_llm"] is True
    assert body["provider"] == "gemini"
    assert body["text"] == "a reasoned answer"

    mocked_ai_router_route.assert_awaited_once()
    llm_request = mocked_ai_router_route.await_args.args[0]
    assert llm_request.message == REASONING_MESSAGE


def test_voice_and_text_endpoints_drive_the_same_ai_router_call(client, mocked_ai_router_route):
    voice_response = _post_voice_audio(client)
    text_response = _post_text_message(client)

    assert voice_response.status_code == text_response.status_code == 200
    # Same AIRouter.route() call shape from both entrypoints -- proves voice funnels
    # through the exact same AssistantCore.handle() pipeline, not a parallel one.
    assert mocked_ai_router_route.await_count == 2
    voice_llm_request, text_llm_request = (
        call.args[0] for call in mocked_ai_router_route.await_args_list
    )
    assert voice_llm_request.message == text_llm_request.message == REASONING_MESSAGE
    assert voice_llm_request.tools == text_llm_request.tools

    voice_body, text_body = voice_response.json(), text_response.json()
    assert voice_body["used_llm"] == text_body["used_llm"] is True
    assert voice_body["provider"] == text_body["provider"] == "gemini"
    assert voice_body["text"] == text_body["text"] == "a reasoned answer"
