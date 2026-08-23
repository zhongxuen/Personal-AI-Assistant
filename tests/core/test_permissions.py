"""
Permission system tests (§19, §41 Rule 6).

Runs fake tools of each PermissionLevel through ToolExecutor (no db session,
so nothing is persisted) and asserts:
  - a SAFE tool executes without needing confirmation
  - a CONFIRM tool is rejected without confirmation, and succeeds with it
  - a BLOCKED tool always fails, confirmed or not
"""

from __future__ import annotations

from typing import Any

from app.core.permissions import PermissionLevel, RequesterContext
from app.core.tool_executor import ToolExecutor
from app.tools.base import ToolResult
from app.tools.registry import ToolRegistry


class FakeTool:
    """Minimal Tool implementation with no real system side effects."""

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


def _executor_and_tool(permission: PermissionLevel) -> tuple[ToolExecutor, FakeTool]:
    registry = ToolRegistry()
    tool = FakeTool(name=f"fake_{permission.value}", permission=permission)
    registry.register(tool)
    executor = ToolExecutor(registry)  # no db -- nothing persisted
    return executor, tool


def _context(**kwargs: Any) -> RequesterContext:
    return RequesterContext(user_id="u1", platform="desktop", **kwargs)


def test_safe_tool_executes_without_confirmation():
    executor, tool = _executor_and_tool(PermissionLevel.SAFE)

    result = executor.execute(tool.name, {}, _context())

    assert result.success is True
    assert tool.calls == 1


def test_confirm_tool_rejected_without_confirmation():
    executor, tool = _executor_and_tool(PermissionLevel.CONFIRM)

    result = executor.execute(tool.name, {}, _context(confirmed=False))

    assert result.success is False
    assert "confirm" in (result.error or "").lower()
    assert tool.calls == 0


def test_confirm_tool_succeeds_with_confirmation():
    executor, tool = _executor_and_tool(PermissionLevel.CONFIRM)

    result = executor.execute(tool.name, {}, _context(confirmed=True))

    assert result.success is True
    assert tool.calls == 1


def test_blocked_tool_always_fails():
    executor, tool = _executor_and_tool(PermissionLevel.BLOCKED)

    result_plain = executor.execute(tool.name, {}, _context())
    result_confirmed = executor.execute(tool.name, {}, _context(confirmed=True))

    assert result_plain.success is False
    assert result_confirmed.success is False
    assert tool.calls == 0
