"""
Routine tool (§37 Phase 2 / file 03).

`run_routine` runs a named, hardcoded sequence of tool calls -- for this phase, exactly
one routine, "coding": open VS Code -> open the portfolio folder -> open Chrome, proving
the routine-without-LLM path from §13. Every step is dispatched through a `ToolExecutor`
(never `tool.handler(...)` directly, §41 Rule 6) so each step still gets its own params
validation, permission check, and `tool_execution_logs` row, exactly like a standalone
tool call would. The real `RoutineEngine`/registry (config-driven, multiple routines,
persisted via the `routines`/`routine_steps` tables from file 01) is file 04's job --
don't build that generality here (§41 Rule 1).
"""

from __future__ import annotations

from typing import Any

from app.core.permissions import PermissionLevel, RequesterContext
from app.core.tool_executor import ToolExecutor
from app.database.database import SessionLocal
from app.tools.base import ToolResult
from app.tools.registry import ToolRegistry

# routine name -> ordered list of (tool_name, params) steps to run via ToolExecutor.
# Hardcoded for this phase only -- file 04 replaces this with the persisted
# `routines`/`routine_steps` tables.
ROUTINES: dict[str, list[tuple[str, dict[str, Any]]]] = {
    "coding": [
        ("open_application", {"app_name": "vscode"}),
        ("open_application", {"app_name": "portfolio folder"}),
        ("open_application", {"app_name": "chrome"}),
    ],
}


def _known_routines() -> str:
    return ", ".join(sorted(ROUTINES))


class RunRoutineTool:
    """Runs a hardcoded named routine as a sequence of `ToolExecutor.execute()` calls."""

    name = "run_routine"
    description = "Run a named routine: a hardcoded sequence of tool calls (e.g. 'coding')."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "routine_name": {
                "type": "string",
                "description": (
                    "Name of the routine to run. Defaults to 'coding', the only routine "
                    "hardcoded this phase."
                ),
            }
        },
        "required": [],
    }
    permission = PermissionLevel.SAFE
    platforms = ["desktop"]
    requires_confirmation = False

    def __init__(self, registry: ToolRegistry) -> None:
        # Steps are dispatched through a ToolExecutor built on this same registry, not
        # by calling each tool's handler directly -- so logging/permissions apply to
        # every step (§41 Rule 6).
        self._registry = registry

    def handler(self, routine_name: str = "coding", **kwargs: Any) -> ToolResult:
        steps = ROUTINES.get(routine_name)
        if steps is None:
            return ToolResult(
                success=False,
                error=f"No routine named '{routine_name}'. Known routines: {_known_routines()}.",
            )

        db = SessionLocal()
        try:
            executor = ToolExecutor(self._registry, db=db)
            context = RequesterContext(platform="desktop", scope="routine")

            completed: list[dict[str, Any]] = []
            for step_tool_name, step_params in steps:
                result = executor.execute(step_tool_name, step_params, context)
                completed.append(
                    {"tool_name": step_tool_name, "params": step_params, "result": result.model_dump()}
                )
                if not result.success:
                    return ToolResult(
                        success=False,
                        error=f"Routine '{routine_name}' failed at step '{step_tool_name}': {result.error}",
                        data={"routine": routine_name, "steps": completed},
                    )

            return ToolResult(
                success=True,
                data={
                    "message": f"Ran routine '{routine_name}' ({len(completed)} step(s)).",
                    "routine": routine_name,
                    "steps": completed,
                },
            )
        finally:
            db.close()
