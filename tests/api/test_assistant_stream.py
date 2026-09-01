"""
Streaming assistant endpoint end to end (POST /api/assistant/stream).

Asserts the guarantee the whole feature rests on: `/assistant/stream` is
`/assistant/message` with a different encoding on the wire. Same orchestrator, same
tool pipeline, same auth boundary, same final answer -- only the delivery differs. If
these two ever disagreed, streaming would be a second, silently-diverging code path for
answering a message, which is exactly what §41 Rule 7 forbids.

The LLM is stubbed at `AIRouter` (both `route` and `route_stream`), so nothing here
reaches a network; what runs for real is `AssistantCore.handle_stream`, `ToolExecutor`,
the route's auth/local-only checks, and the SSE framing.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_optional_current_user, get_tool_registry
from app.database.database import Base, get_db
from app.llm.ai_router import AIRouter
from app.llm.base import LLMResult, LLMStreamChunk
from app.tools import register_default_tools
from app.tools.registry import ToolRegistry
from main import app


@pytest.fixture()
def client(monkeypatch, test_db):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    registry = ToolRegistry()
    register_default_tools(registry)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_tool_registry] = lambda: registry
    # platform="web" requires a bearer token; the auth boundary itself is covered by
    # tests/api/test_auth.py, so it's satisfied here rather than re-tested.
    app.dependency_overrides[get_optional_current_user] = lambda: SimpleNamespace(
        username="web-user"
    )
    yield TestClient(app)
    app.dependency_overrides.clear()
    engine.dispose()


def _events(response) -> list[dict]:
    """Parse an SSE body into its decoded event payloads, in order."""
    parsed = []
    for frame in response.text.split("\n\n"):
        payload = "".join(
            line[len("data:") :].strip()
            for line in frame.splitlines()
            if line.startswith("data:")
        )
        if payload:
            parsed.append(json.loads(payload))
    return parsed


def _post(client: TestClient, message: str):
    return client.post(
        "/api/assistant/stream",
        json={"user_id": "web-client", "platform": "web", "message": message},
    )


def _stub_stream(monkeypatch, *chunks):
    async def _route_stream(self, request):
        for chunk in chunks:
            yield chunk

    monkeypatch.setattr(AIRouter, "route_stream", _route_stream)


def test_text_is_delivered_as_deltas_then_one_authoritative_done(client, monkeypatch):
    _stub_stream(
        monkeypatch,
        LLMStreamChunk(delta="Good "),
        LLMStreamChunk(delta="morning."),
        LLMStreamChunk(
            final=LLMResult(status="SUCCESS", text="Good morning.", provider="gemini")
        ),
    )

    events = _events(_post(client, "say something thoughtful"))

    assert [e["type"] for e in events] == ["delta", "delta", "done"]
    assert [e["text"] for e in events[:2]] == ["Good ", "morning."]

    done = events[-1]["response"]
    # The concatenated deltas and the authoritative final text agree -- a client that
    # renders deltas live never has to visibly correct itself.
    assert done["text"] == "Good morning."
    assert done["used_llm"] is True
    assert done["provider"] == "gemini"


def test_the_content_type_is_sse_and_proxy_buffering_is_disabled(client, monkeypatch):
    """`X-Accel-Buffering: no` is load-bearing, not decorative: nginx and several PaaS
    proxies (Render included) buffer responses by default, which would hold every chunk
    until the stream closed and silently undo the entire feature.
    """
    _stub_stream(monkeypatch, LLMStreamChunk(final=LLMResult(status="SUCCESS", text="hi")))

    response = _post(client, "anything unrecognized enough to need reasoning")

    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["cache-control"] == "no-cache"


def test_a_deterministic_command_emits_a_single_done_and_never_touches_the_llm(client, monkeypatch):
    """A direct command resolves locally, so there is nothing to stream -- and the LLM
    must not be consulted just because the streaming endpoint was used (§9/zero-LLM).
    """

    async def _must_not_run(self, request):  # pragma: no cover - asserted by not raising
        raise AssertionError("a deterministic command must not reach the LLM")
        yield  # make it an async generator

    monkeypatch.setattr(AIRouter, "route_stream", _must_not_run)

    events = _events(_post(client, "what time is it"))

    assert [e["type"] for e in events] == ["done"]
    assert events[0]["response"]["used_llm"] is False
    assert events[0]["response"]["tool_calls"][0]["tool_name"] == "get_time"


def test_each_tool_call_is_reported_as_it_completes(client, monkeypatch):
    """Tool events exist so a multi-tool turn shows progress instead of going quiet;
    they must also still appear in the terminal response's `tool_calls`.
    """
    from app.llm.base import ToolCallRequest

    _stub_stream(
        monkeypatch,
        LLMStreamChunk(
            final=LLMResult(
                status="SUCCESS",
                text="",
                provider="gemini",
                tool_calls=[ToolCallRequest(tool_name="get_time", params={})],
            )
        ),
    )

    events = _events(_post(client, "please reason about the time"))

    assert [e["type"] for e in events] == ["tool", "done"]
    assert events[0]["tool_call"]["tool_name"] == "get_time"
    assert events[0]["tool_call"]["result"]["success"] is True
    # ...and the same call is present in the authoritative response, so a consumer that
    # only reads `done` loses nothing.
    assert [c["tool_name"] for c in events[-1]["response"]["tool_calls"]] == ["get_time"]


def test_an_unavailable_provider_ends_in_done_with_an_honest_message(client, monkeypatch):
    """There is no `error` event type: a failed turn is a normal `done` whose text
    explains the situation (§41 Rule 3), so consumers have exactly one terminal case.
    """
    _stub_stream(
        monkeypatch,
        LLMStreamChunk(final=LLMResult(status="QUOTA_EXHAUSTED", error_type="resource_exhausted")),
    )

    events = _events(_post(client, "something needing reasoning"))

    assert [e["type"] for e in events] == ["done"]
    response = events[0]["response"]
    assert response["used_llm"] is False
    assert "quota" in response["text"].lower()


def test_streaming_and_non_streaming_return_the_same_answer(client, monkeypatch):
    """The core equivalence claim. Both endpoints, same message, same final payload."""
    result = LLMResult(status="SUCCESS", text="the same answer", provider="gemini")

    async def _route(self, request):
        return result

    _stub_stream(monkeypatch, LLMStreamChunk(delta="the same answer"), LLMStreamChunk(final=result))
    monkeypatch.setattr(AIRouter, "route", _route)

    body = {"user_id": "web-client", "platform": "web", "message": "reason about this"}
    plain = client.post("/api/assistant/message", json=body).json()
    streamed = _events(client.post("/api/assistant/stream", json=body))[-1]["response"]

    assert streamed == plain


def test_an_unauthenticated_caller_is_rejected_before_any_streaming_starts(client, monkeypatch):
    """The streaming route must not be a weaker door than the JSON one -- a 401 has to
    happen as a real HTTP status, not as an event inside a 200 stream.
    """
    app.dependency_overrides[get_optional_current_user] = lambda: None

    response = _post(client, "let me in")

    assert response.status_code == 401
    assert "text/event-stream" not in response.headers.get("content-type", "")
