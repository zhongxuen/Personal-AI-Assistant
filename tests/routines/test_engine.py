"""
RoutineEngine tests (§37 Phase 3 / file 04 prompt 2).

Verifies `RoutineEngine.run()` loads a routine's steps from `RoutineRegistry` and
dispatches each one *through* `ToolExecutor` (never a tool's `.handler()` directly,
§41 Rule 6) in order, aggregating results, and stops at the first failing step. No LLM
call is possible anywhere in this path -- there's nothing here that could make one.
"""

from __future__ import annotations

from unittest.mock import Mock

from app.core.permissions import PermissionLevel
from app.routines.engine import RoutineEngine
from app.routines.registry import RoutineRegistry
from app.tools.base import ToolResult
from app.tools.registry import ToolRegistry


class _StubTool:
    """A minimal SAFE tool whose handler is a Mock, so tests can assert call order/args
    without depending on any real tool's side effects.
    """

    def __init__(self, name: str, handler: Mock) -> None:
        self.name = name
        self.description = "stub"
        self.parameters: dict = {"type": "object", "properties": {}, "required": []}
        self.permission = PermissionLevel.SAFE
        self.platforms = ["desktop"]
        self.requires_confirmation = False
        self.handler = handler


def _registry_with_stub_tools() -> tuple[ToolRegistry, Mock, Mock]:
    registry = ToolRegistry()
    first = Mock(return_value=ToolResult(success=True, data={"step": "first"}))
    second = Mock(return_value=ToolResult(success=True, data={"step": "second"}))
    registry.register(_StubTool("stub_first", first))
    registry.register(_StubTool("stub_second", second))
    return registry, first, second


def test_run_executes_steps_in_order_and_aggregates_results(test_db):
    db = test_db()
    RoutineRegistry(db).create_routine(
        "demo",
        [("stub_first", {"a": 1}), ("stub_second", {"b": 2})],
    )
    db.close()

    tool_registry, first, second = _registry_with_stub_tools()
    result = RoutineEngine(tool_registry).run("demo")

    assert result.success is True
    assert result.data["routine"] == "demo"
    steps = result.data["steps"]
    assert [s["tool_name"] for s in steps] == ["stub_first", "stub_second"]
    first.assert_called_once_with(a=1)
    second.assert_called_once_with(b=2)


def test_run_stops_at_first_failing_step(test_db):
    db = test_db()
    RoutineRegistry(db).create_routine(
        "demo",
        [("stub_first", {}), ("stub_second", {})],
    )
    db.close()

    tool_registry, first, second = _registry_with_stub_tools()
    first.return_value = ToolResult(success=False, error="boom")

    result = RoutineEngine(tool_registry).run("demo")

    assert result.success is False
    assert "demo" in result.error
    assert "stub_first" in result.error
    second.assert_not_called()
    assert len(result.data["steps"]) == 1


def test_run_unknown_routine_fails(test_db):
    tool_registry, _first, _second = _registry_with_stub_tools()

    result = RoutineEngine(tool_registry).run("nope")

    assert result.success is False
    assert "nope" in result.error


def test_run_routine_with_no_steps_fails(test_db):
    db = test_db()
    RoutineRegistry(db).create_routine("empty", [])
    db.close()

    tool_registry, _first, _second = _registry_with_stub_tools()
    result = RoutineEngine(tool_registry).run("empty")

    assert result.success is False
    assert "empty" in result.error
