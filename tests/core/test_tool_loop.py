"""
AssistantCore's tool-result loop.

When the provider that answered supports it (`LLMResult.tool_loop`, stamped by
AIRouter), tool results are sent back to the model -- call -> execute via ToolExecutor
-> send results -> repeat -- until it answers in text or `llm_max_tool_steps` rounds
have run. A provider without tool-loop support keeps the single-shot behavior.

`core.ai_router` is replaced by a scripted fake that returns one canned `LLMResult` per
step and records every `LLMRequest` it receives, so these tests assert AssistantCore's
orchestration only. Tools are real `ToolExecutor` calls against fake tools, so the
permission/validation path is exercised for every step, not bypassed.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.config.settings import Settings
from app.core.assistant import AssistantCore
from app.core.models import AssistantRequest
from app.core.permissions import PermissionLevel
from app.llm.base import LLMRequest, LLMResult, LLMStreamChunk, ToolCallRequest
from app.tools.base import ToolResult
from app.tools.registry import ToolRegistry

MESSAGE = "what's the weather like tomorrow?"  # never matches a deterministic alias


class FakeTool:
    def __init__(self, name: str, permission: PermissionLevel = PermissionLevel.SAFE) -> None:
        self.name = name
        self.description = f"Fake tool {name}."
        self.parameters: dict[str, Any] = {"type": "object", "properties": {}}
        self.permission = permission
        self.platforms = ["desktop", "web"]
        self.requires_confirmation = False
        self.calls = 0

    def handler(self, **kwargs: Any) -> ToolResult:
        self.calls += 1
        return ToolResult(success=True, data={"message": f"{self.name} ran", "n": self.calls})


class ScriptedRouter:
    """Returns `script[i]` for the i-th step; records each request."""

    def __init__(self, script: list[LLMResult], deltas: list[list[str]] | None = None) -> None:
        self.script = list(script)
        self.deltas = deltas or [[] for _ in script]
        self.requests: list[LLMRequest] = []

    async def route(self, request: LLMRequest) -> LLMResult:
        self.requests.append(request)
        return self.script[len(self.requests) - 1].model_copy(deep=True)

    async def route_stream(self, request: LLMRequest):
        self.requests.append(request)
        step = len(self.requests) - 1
        for delta in self.deltas[step]:
            yield LLMStreamChunk(delta=delta)
        yield LLMStreamChunk(final=self.script[step].model_copy(deep=True))


def _call(name: str, call_id: str | None = None) -> ToolCallRequest:
    return ToolCallRequest(tool_name=name, params={}, call_id=call_id)


def _tool_step(*calls: ToolCallRequest, provider: str = "openai", loop: bool = True) -> LLMResult:
    return LLMResult(
        status="SUCCESS",
        tool_calls=list(calls),
        provider=provider,
        tool_loop=loop,
        provider_items=[{"type": "function_call", "call_id": c.call_id, "name": c.tool_name, "arguments": "{}"} for c in calls],
    )


def _text_step(text: str, provider: str = "openai") -> LLMResult:
    return LLMResult(status="SUCCESS", text=text, provider=provider, tool_loop=True)


def _core(*tools: FakeTool, max_steps: int = 4) -> AssistantCore:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    settings = Settings(_env_file=None, llm_max_tool_steps=max_steps)
    return AssistantCore(registry, db=None, settings=settings)


def _request(**metadata: Any) -> AssistantRequest:
    return AssistantRequest(user_id="u1", platform="web", message=MESSAGE, metadata=metadata)


def test_tool_results_are_fed_back_and_the_final_text_is_the_reply():
    weather = FakeTool("get_weather")
    core = _core(weather)
    core.ai_router = ScriptedRouter([_tool_step(_call("get_weather", "c1")), _text_step("Sunny, 25C.")])

    response = core.handle(_request())

    assert response.text == "Sunny, 25C."
    assert response.used_llm is True
    assert response.provider == "openai"
    assert [c["tool_name"] for c in response.tool_calls] == ["get_weather"]
    assert weather.calls == 1

    first, second = core.ai_router.requests
    assert first.tool_exchanges == []
    (exchange,) = second.tool_exchanges
    assert exchange.calls[0].call_id == "c1"
    assert exchange.provider == "openai"
    assert exchange.provider_items[0]["call_id"] == "c1"
    # The model sees exactly what ToolExecutor returned.
    assert json.loads(exchange.outputs[0])["data"]["message"] == "get_weather ran"


def test_multiple_rounds_accumulate_exchanges_in_order():
    a, b = FakeTool("tool_a"), FakeTool("tool_b")
    core = _core(a, b)
    core.ai_router = ScriptedRouter(
        [_tool_step(_call("tool_a", "c1")), _tool_step(_call("tool_b", "c2")), _text_step("All done.")]
    )

    response = core.handle(_request())

    assert response.text == "All done."
    assert [c["tool_name"] for c in response.tool_calls] == ["tool_a", "tool_b"]
    assert [len(r.tool_exchanges) for r in core.ai_router.requests] == [0, 1, 2]


def test_step_cap_forces_a_final_text_only_call():
    looping = FakeTool("tool_a")
    core = _core(looping, max_steps=2)
    core.ai_router = ScriptedRouter(
        [
            _tool_step(_call("tool_a", "c1")),
            _tool_step(_call("tool_a", "c2")),
            # The model still asks for tools on the final step -- they must not run.
            LLMResult(status="SUCCESS", text="Here's what I found.", tool_calls=[_call("tool_a", "c3")], provider="openai", tool_loop=True),
        ]
    )

    response = core.handle(_request())

    assert looping.calls == 2
    assert response.text == "Here's what I found."
    assert [r.allow_tool_calls for r in core.ai_router.requests] == [True, True, False]


def test_provider_without_tool_loop_support_stays_single_shot():
    tool = FakeTool("tool_a")
    core = _core(tool)
    core.ai_router = ScriptedRouter([_tool_step(_call("tool_a"), provider="ollama", loop=False)])

    response = core.handle(_request())

    assert len(core.ai_router.requests) == 1
    assert response.text == "tool_a ran"  # the tool's own message, as before the loop existed
    assert response.provider == "ollama"


def test_tools_still_go_through_tool_executor_permission_checks_in_the_loop():
    restricted = FakeTool("restricted_tool", permission=PermissionLevel.RESTRICTED)
    core = _core(restricted)
    core.ai_router = ScriptedRouter([_tool_step(_call("restricted_tool", "c1")), _text_step("I couldn't do that.")])

    response = core.handle(_request())

    assert restricted.calls == 0
    assert response.tool_calls[0]["result"]["success"] is False
    output = json.loads(core.ai_router.requests[1].tool_exchanges[0].outputs[0])
    assert "permission denied" in output["error"].lower()


def test_a_failed_later_step_still_reports_the_tools_that_ran():
    tool = FakeTool("tool_a")
    core = _core(tool)
    core.ai_router = ScriptedRouter(
        [_tool_step(_call("tool_a", "c1")), LLMResult(status="PERMANENT_ERROR", error_type="no_provider_available")]
    )

    response = core.handle(_request())

    assert tool.calls == 1
    assert response.used_llm is True
    assert response.text == "tool_a ran"
    assert [c["tool_name"] for c in response.tool_calls] == ["tool_a"]


def test_first_step_failure_is_the_usual_unavailable_message():
    core = _core()
    core.ai_router = ScriptedRouter([LLMResult(status="QUOTA_EXHAUSTED", error_type="rate_limited")])

    response = core.handle(_request())

    assert response.used_llm is False
    assert "quota" in response.text.lower()


def test_streaming_loop_emits_tool_events_and_ends_with_the_final_reply():
    tool = FakeTool("tool_a")
    core = _core(tool)
    core.ai_router = ScriptedRouter(
        [_tool_step(_call("tool_a", "c1")), _text_step("Finished.")],
        deltas=[[], ["Fini", "shed."]],
    )

    async def run():
        return [event async for event in core.handle_stream(_request())]

    events = asyncio.run(run())

    assert [e.type for e in events] == ["tool", "delta", "delta", "done"]
    assert events[0].tool_call["tool_name"] == "tool_a"
    assert events[-1].response.text == "Finished."
    assert len(core.ai_router.requests[1].tool_exchanges) == 1


def test_streaming_and_non_streaming_agree_on_the_final_response():
    script = [_tool_step(_call("tool_a", "c1")), _text_step("Same answer.")]

    core_a = _core(FakeTool("tool_a"))
    core_a.ai_router = ScriptedRouter(script)
    plain = core_a.handle(_request())

    core_b = _core(FakeTool("tool_a"))
    core_b.ai_router = ScriptedRouter(script)

    async def run():
        return [event async for event in core_b.handle_stream(_request())]

    streamed = asyncio.run(run())[-1].response

    assert streamed.model_dump() == plain.model_dump()


@pytest.mark.parametrize("max_steps", [0])
def test_zero_step_cap_means_one_text_only_call(max_steps):
    tool = FakeTool("tool_a")
    core = _core(tool, max_steps=max_steps)
    core.ai_router = ScriptedRouter([_text_step("Just text.")])

    response = core.handle(_request())

    assert response.text == "Just text."
    assert core.ai_router.requests[0].allow_tool_calls is False
