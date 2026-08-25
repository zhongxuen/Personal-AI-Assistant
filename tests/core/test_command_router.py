"""
Command router tests (§2, §11, extended file 04 prompt 1).

Registers a few fake tools (no real system side effects) and asserts that:
  - exact-name/alias matches resolve deterministically
  - "<verb> <remainder>" phrasings ("open vscode", "launch vscode", "start
    vscode") all resolve to the same tool + params
  - an unrelated phrase falls through to NEEDS_LLM
  - the "remind me to X [time phrase]" alias splits the trailing/embedded date phrase
    out of the title via `split_title_and_due` instead of file 03's naive
    whole-remainder-as-title capture
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.command_router import NEEDS_LLM, CommandRouter, RouteResult
from app.core.permissions import PermissionLevel
from app.tools.base import ToolResult
from app.tools.registry import ToolRegistry


class FakeTool:
    """Minimal Tool implementation with no real system side effects."""

    def __init__(
        self,
        name: str,
        parameters: dict[str, Any] | None = None,
        permission: PermissionLevel = PermissionLevel.SAFE,
        platforms: list[str] | None = None,
    ) -> None:
        self.name = name
        self.description = f"Fake tool '{name}' for tests."
        self.parameters = parameters or {}
        self.permission = permission
        self.platforms = platforms or ["desktop", "web", "discord"]
        self.requires_confirmation = permission == PermissionLevel.CONFIRM
        self.calls: list[dict[str, Any]] = []

    def handler(self, **kwargs: Any) -> ToolResult:
        self.calls.append(kwargs)
        return ToolResult(success=True, data={"echo": kwargs})


def _registry() -> tuple[ToolRegistry, FakeTool, FakeTool, FakeTool]:
    registry = ToolRegistry()

    open_app = FakeTool(
        name="open_application",
        parameters={
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
        },
    )
    registry.register(open_app, aliases=["open", "launch", "start"])

    close_app = FakeTool(
        name="close_application",
        parameters={
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
        },
    )
    registry.register(close_app, aliases=["close", "quit"])

    ping = FakeTool(name="ping", parameters={})
    registry.register(ping)

    return registry, open_app, close_app, ping


def test_open_launch_start_all_resolve_to_same_tool_and_params():
    registry, open_app, _close_app, _ping = _registry()
    router = CommandRouter(registry)

    for phrase in ["open vscode", "launch vscode", "start vscode"]:
        result = router.route(phrase)
        assert result.tool_name == open_app.name
        assert result.params == {"target": "vscode"}


def test_exact_name_match_resolves_with_no_params():
    registry, _open_app, _close_app, ping = _registry()
    router = CommandRouter(registry)

    result = router.route("ping")
    assert result == RouteResult(tool_name=ping.name, params={})


def test_alias_verb_prefix_uses_a_different_tool():
    registry, _open_app, close_app, _ping = _registry()
    router = CommandRouter(registry)

    result = router.route("close vscode")
    assert result.tool_name == close_app.name
    assert result.params == {"target": "vscode"}


def test_unrelated_phrase_returns_needs_llm():
    registry, *_ = _registry()
    router = CommandRouter(registry)

    result = router.route("what's the weather like tomorrow?")
    assert result is NEEDS_LLM


def test_empty_message_returns_needs_llm():
    registry, *_ = _registry()
    router = CommandRouter(registry)

    assert router.route("") is NEEDS_LLM
    assert router.route("   ") is NEEDS_LLM


# --- "remind me to X [time phrase]" -> create_task special-case -----------------------


def _create_task_registry() -> ToolRegistry:
    registry = ToolRegistry()
    create_task = FakeTool(
        name="create_task",
        parameters={
            "type": "object",
            "properties": {"title": {"type": "string"}, "due": {"type": "string"}},
            "required": ["title"],
        },
    )
    registry.register(create_task, aliases=["remind me to"])
    return registry


def test_remind_me_to_splits_trailing_time_phrase_into_due_param():
    router = CommandRouter(_create_task_registry())

    result = router.route("remind me to submit my assignment tomorrow at 8pm")

    assert result.tool_name == "create_task"
    assert result.params["title"] == "submit my assignment"
    # Just needs to be a real, parseable ISO datetime -- exact value depends on "now".
    assert datetime.fromisoformat(result.params["due"]).hour == 20


def test_remind_me_to_without_time_phrase_has_no_due_param():
    router = CommandRouter(_create_task_registry())

    result = router.route("remind me to buy milk")

    assert result.tool_name == "create_task"
    assert result.params == {"title": "buy milk"}
