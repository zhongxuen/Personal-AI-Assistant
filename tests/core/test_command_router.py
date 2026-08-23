"""
Command router tests (§2, §11).

Registers a few fake tools (no real system side effects) and asserts that:
  - exact-name/alias matches resolve deterministically
  - "<verb> <remainder>" phrasings ("open vscode", "launch vscode", "start
    vscode") all resolve to the same tool + params
  - an unrelated phrase falls through to NEEDS_LLM
"""

from __future__ import annotations

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
