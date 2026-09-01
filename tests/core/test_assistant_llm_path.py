"""
AssistantCore's LLM_REQUIRED path (§20, §41 Rule 6, file 05/06, file 08 prompt 1).

`test_zero_llm.py` covers every deterministic command staying off the LLM path
entirely; this file covers the other side: once `CommandRouter` returns classification
`LLM_REQUIRED`,
`AssistantCore` calls `AIRouter.route()` (mocked here -- no real network call; AIRouter's
own chain-walking/failover behavior is covered separately in tests/llm/test_ai_router.py),
and:

  1. the LLM is actually the thing consulted for an unresolved message,
  2. any tool call Gemini asks for still runs through the *same* `ToolExecutor` as the
     deterministic path -- a RESTRICTED tool stays blocked even though "the LLM asked
     for it", because permission is decided from `RequesterContext` (platform-supplied
     `override`/`confirmed`), never from anything the model said (§19's whole point), and
  3. a QUOTA_EXHAUSTED result produces a clear, non-crashing response instead of an
     exception or a silent no-op.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.assistant import AssistantCore
from app.core.models import AssistantRequest
from app.core.permissions import PermissionLevel
from app.llm.base import LLMResult, ToolCallRequest
from app.tools.base import ToolResult
from app.tools.registry import ToolRegistry
from app.tools.tasks import create_task_tool

# Never matches any trigger/alias registered below -- CommandRouter always falls
# through to classification LLM_REQUIRED for it, regardless of what's in the registry.
UNRESOLVABLE_MESSAGE = "what's the weather like tomorrow?"


class FakeTool:
    """Minimal Tool implementation with no real system side effects (same pattern as
    tests/core/test_permissions.py / test_platform_capability.py).
    """

    def __init__(self, name: str, permission: PermissionLevel) -> None:
        self.name = name
        self.description = f"Fake tool '{name}' for tests."
        self.parameters: dict[str, Any] = {}
        self.permission = permission
        self.platforms = ["desktop", "web", "discord"]
        self.requires_confirmation = permission == PermissionLevel.CONFIRM
        self.calls = 0

    def handler(self, **kwargs: Any) -> ToolResult:
        self.calls += 1
        return ToolResult(success=True, data={"ran": True})


def _core_with_restricted_tool() -> tuple[AssistantCore, FakeTool]:
    registry = ToolRegistry()
    restricted = FakeTool(name="restricted_tool", permission=PermissionLevel.RESTRICTED)
    registry.register(restricted)
    core = AssistantCore(registry, db=None)  # no db -- nothing persisted
    return core, restricted


def _request(**metadata: Any) -> AssistantRequest:
    return AssistantRequest(
        user_id="u1", platform="desktop", message=UNRESOLVABLE_MESSAGE, metadata=metadata
    )


def test_unresolved_message_is_routed_to_gemini_provider():
    core, _restricted = _core_with_restricted_tool()
    core.ai_router.route = AsyncMock(
        # `provider` is stamped by `AIRouter.route` from the chain entry that actually
        # answered (app/llm/ai_router.py) -- mocking `route` itself bypasses that, so
        # the stub has to supply it the same way the real router would. Asserting
        # `response.provider` below is then a real check that AssistantCore reports
        # whoever answered, rather than the hardcoded "gemini" it used to return
        # regardless of whether Ollama was the one that served the reply.
        return_value=LLMResult(status="SUCCESS", text="a reasoned answer", provider="gemini")
    )

    response = core.handle(_request())

    core.ai_router.route.assert_awaited_once()
    llm_request = core.ai_router.route.await_args.args[0]
    assert llm_request.message == UNRESOLVABLE_MESSAGE

    assert response.used_llm is True
    assert response.provider == "gemini"
    assert response.text == "a reasoned answer"


def test_llm_requested_tool_call_still_goes_through_tool_executor_and_is_blocked():
    core, restricted = _core_with_restricted_tool()
    core.ai_router.route = AsyncMock(
        return_value=LLMResult(
            status="SUCCESS",
            tool_calls=[ToolCallRequest(tool_name="restricted_tool", params={})],
        )
    )

    # No `override` in metadata -- nothing the LLM said can supply it (§19); the tool
    # call must be denied exactly as it would be on the deterministic path.
    response = core.handle(_request())

    assert response.used_llm is True
    assert len(response.tool_calls) == 1
    tool_call_result = response.tool_calls[0]["result"]
    assert tool_call_result["success"] is False
    assert "permission denied" in tool_call_result["error"].lower()
    # The handler itself must never have run.
    assert restricted.calls == 0


def test_llm_requested_tool_call_runs_when_override_is_explicitly_granted():
    core, restricted = _core_with_restricted_tool()
    core.ai_router.route = AsyncMock(
        return_value=LLMResult(
            status="SUCCESS",
            tool_calls=[ToolCallRequest(tool_name="restricted_tool", params={})],
        )
    )

    # `override` comes from the platform/human-facing layer via request.metadata, not
    # from the LLM's response -- this is the control case proving the block above is a
    # real permission decision, not e.g. a bug that always fails the tool call.
    response = core.handle(_request(override=True))

    assert response.tool_calls[0]["result"]["success"] is True
    assert restricted.calls == 1


@pytest.mark.parametrize("status", ["QUOTA_EXHAUSTED", "RETRYABLE_ERROR", "PERMANENT_ERROR"])
def test_non_success_llm_status_produces_clear_non_crashing_response(status):
    core, restricted = _core_with_restricted_tool()
    core.ai_router.route = AsyncMock(
        return_value=LLMResult(status=status, error_type="whatever")
    )

    response = core.handle(_request())

    assert response.used_llm is False
    assert response.tool_calls == []
    assert isinstance(response.text, str) and response.text.strip() != ""
    # No tool call was ever attempted on a non-SUCCESS result.
    assert restricted.calls == 0


def test_quota_exhausted_message_is_specific_to_quota():
    core, _restricted = _core_with_restricted_tool()
    core.ai_router.route = AsyncMock(
        return_value=LLMResult(status="QUOTA_EXHAUSTED", error_type="resource_exhausted")
    )

    response = core.handle(_request())

    assert "quota" in response.text.lower()


def test_dod_create_a_task_phrase_resolves_via_llm_and_actually_creates_a_task(test_db):
    """development-plan.md §40's literal DoD phrasing, "Create a task to finish my
    portfolio.", has no matching CommandRouter alias -- only "remind me to ..." is
    registered for `create_task` (see `app/tools/__init__.py`) -- so, unlike every other
    create_task test in this suite (tests/core/test_zero_llm.py,
    tests/api/test_tasks.py, tests/platforms/test_discord_capability.py, all of which go
    through the deterministic "remind me to" alias), this message genuinely falls
    through to classification LLM_REQUIRED and depends on Gemini choosing to call
    `create_task` as a tool -- the same LLM-requests-a-tool-call path this file already
    proves for a RESTRICTED fake tool above, exercised here against the real
    `create_task_tool` (+ `test_db`, so it actually persists) to prove the DoD scenario
    itself, not just the plumbing in the abstract.
    """
    registry = ToolRegistry()
    registry.register(create_task_tool)
    core = AssistantCore(registry, db=None)
    core.ai_router.route = AsyncMock(
        return_value=LLMResult(
            status="SUCCESS",
            tool_calls=[
                ToolCallRequest(tool_name="create_task", params={"title": "finish my portfolio"})
            ],
        )
    )

    request = AssistantRequest(
        user_id="u1", platform="desktop", message="Create a task to finish my portfolio."
    )
    response = core.handle(request)

    assert response.used_llm is True
    call = response.tool_calls[0]
    assert call["tool_name"] == "create_task"
    assert call["result"]["success"] is True
    assert call["result"]["data"]["title"] == "finish my portfolio"
