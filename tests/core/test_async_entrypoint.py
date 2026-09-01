"""
`AssistantCore.handle_async` (performance).

Two things are asserted here, both of which the latency work depends on:

  1. `handle_async` and `handle` produce the same `AssistantResponse` -- adding an async
     entrypoint must not fork the orchestration into two paths that can drift (§41
     Rule 7). `handle` stays for sync callers; it is a shim, not a second implementation.

  2. Consecutive requests over one event loop share one provider client, while the sync
     `handle` shim (which builds a throwaway loop per call via `asyncio.run`) does not.
     That difference is the entire reason the routes and platform adapters were moved to
     `handle_async`: a client rebuilt per request means a connection pool rebuilt per
     request, and therefore a fresh DNS + TCP + TLS handshake in front of every single
     message.
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.assistant import AssistantCore
from app.core.models import AssistantRequest
from app.llm.ai_router import AIRouter
from app.llm.base import LLMResult
from app.llm.clients import get_loop_client, reset_clients
from app.tools import register_default_tools
from app.tools.registry import ToolRegistry

UNRESOLVABLE = "ponder the nature of a well-made cup of tea"


@pytest.fixture(autouse=True)
def _clean_client_cache():
    reset_clients()
    yield
    reset_clients()


@pytest.fixture()
def core(test_db) -> AssistantCore:
    registry = ToolRegistry()
    register_default_tools(registry)
    return AssistantCore(registry, db=None)


def _request(message: str) -> AssistantRequest:
    return AssistantRequest(user_id="u1", platform="desktop", message=message)


@pytest.mark.asyncio
async def test_handle_async_matches_handle_on_a_deterministic_command(core):
    """A direct command takes the local path in both entrypoints and must come back
    identical -- including the executed tool call, not just the text.
    """
    streamed = await core.handle_async(_request("what time is it"))
    plain = core.handle(_request("what time is it"))

    assert streamed.used_llm is plain.used_llm is False
    assert [c["tool_name"] for c in streamed.tool_calls] == [c["tool_name"] for c in plain.tool_calls]
    assert streamed.tool_calls[0]["result"]["success"] is True


# These comparisons are deliberately sync tests. `handle()` reaches the LLM through
# `asyncio.run`, which raises inside a thread that already has a running loop -- a
# pre-existing constraint of the sync shim, and the very reason production callers moved
# to `handle_async`. So the async side is driven with its own `asyncio.run` rather than
# by making the test itself a coroutine.


def test_handle_async_matches_handle_on_the_llm_path(core, monkeypatch):
    async def _route(self, request):
        return LLMResult(status="SUCCESS", text="a reasoned answer", provider="gemini")

    monkeypatch.setattr(AIRouter, "route", _route)

    from_async = asyncio.run(core.handle_async(_request(UNRESOLVABLE)))
    from_sync = core.handle(_request(UNRESOLVABLE))

    assert from_async == from_sync
    assert from_async.used_llm is True
    assert from_async.provider == "gemini"


def test_an_llm_failure_is_reported_identically_by_both_entrypoints(core, monkeypatch):
    async def _route(self, request):
        return LLMResult(status="QUOTA_EXHAUSTED", error_type="resource_exhausted")

    monkeypatch.setattr(AIRouter, "route", _route)

    from_async = asyncio.run(core.handle_async(_request(UNRESOLVABLE)))
    from_sync = core.handle(_request(UNRESOLVABLE))

    assert from_async == from_sync
    assert from_async.used_llm is False
    assert "quota" in from_async.text.lower()


@pytest.mark.asyncio
async def test_consecutive_requests_on_one_loop_share_a_provider_client(core, monkeypatch):
    """The latency claim, stated as a test: two requests handled on the same loop must
    build the provider client once, not once each.
    """
    builds = []

    async def _route_touching_the_client(self, request):
        # Stands in for the provider's `_get_client()` on the real call path.
        get_loop_client("fake-provider", lambda: builds.append(1) or object())
        return LLMResult(status="SUCCESS", text="ok", provider="gemini")

    monkeypatch.setattr(AIRouter, "route", _route_touching_the_client)

    await core.handle_async(_request(UNRESOLVABLE))
    await core.handle_async(_request(UNRESOLVABLE))
    await core.handle_async(_request(UNRESOLVABLE))

    assert len(builds) == 1  # three turns, one handshake


def test_the_sync_shim_cannot_share_a_client_across_calls(core, monkeypatch):
    """The counterpoint, and why production callers were moved off `handle`: each
    `asyncio.run` builds a throwaway loop, and pooled connections die with the loop that
    opened them -- so every call rebuilds. Pinned deliberately, so it stays visible that
    the sync path is the slow one rather than looking equivalent.
    """
    builds = []

    async def _route_touching_the_client(self, request):
        get_loop_client("fake-provider", lambda: builds.append(1) or object())
        return LLMResult(status="SUCCESS", text="ok", provider="gemini")

    monkeypatch.setattr(AIRouter, "route", _route_touching_the_client)

    core.handle(_request(UNRESOLVABLE))
    core.handle(_request(UNRESOLVABLE))

    assert len(builds) == 2


@pytest.mark.asyncio
async def test_tool_handlers_run_off_the_event_loop_thread(core, monkeypatch):
    """`handle_async` offloads tool execution to a worker thread.

    This matters because tool handlers genuinely block -- launching an application,
    reading a file, writing to SQLite. Run inline on uvicorn's shared loop, one of those
    would stall *every* in-flight request, not just the one that triggered it. Asserting
    the handler ran on a different thread than the loop is the direct evidence of the
    offload, rather than inferring it from timing.
    """
    import threading

    from app.core.permissions import PermissionLevel
    from app.tools.base import ToolResult

    class _ThreadRecordingTool:
        """Same duck-typed shape the other core tests use -- `app.tools.base.Tool` is a
        Protocol and can't be instantiated directly.
        """

        name = "records_thread"
        description = "Records the thread it was executed on."
        parameters: dict = {}
        permission = PermissionLevel.SAFE
        platforms = ["desktop", "web", "discord"]
        requires_confirmation = False

        def __init__(self) -> None:
            self.ran_on: int | None = None

        def handler(self, **kwargs) -> ToolResult:
            self.ran_on = threading.get_ident()
            return ToolResult(success=True, data={"message": "done"})

    tool = _ThreadRecordingTool()
    core.registry.register(tool)

    from app.llm.base import ToolCallRequest

    async def _route(self, request):
        return LLMResult(
            status="SUCCESS",
            provider="gemini",
            tool_calls=[ToolCallRequest(tool_name="records_thread", params={})],
        )

    monkeypatch.setattr(AIRouter, "route", _route)

    loop_thread = threading.get_ident()
    response = await core.handle_async(_request(UNRESOLVABLE))

    assert response.tool_calls[0]["result"]["success"] is True
    assert tool.ran_on is not None
    assert tool.ran_on != loop_thread
