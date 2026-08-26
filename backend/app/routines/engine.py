"""
Routine engine (§37 Phase 3 / file 04 prompt 2).

`RoutineEngine.run(name, context)` loads a routine's persisted steps via
`RoutineRegistry` and executes each one, in order, through `ToolExecutor` -- the single
choke point every tool call must pass through (§18-19, §41 Rule 6) -- collecting each
step's `ToolResult` into one aggregated result. A routine is just a name -> ordered
list of (tool_name, params); this module never calls an LLM and never calls a tool's
`.handler()` directly, so a routine gets exactly the same validation, permission check,
and `tool_execution_logs` row for each step that a standalone tool call would.

`app/tools/routines.py`'s `RunRoutineTool` (manual trigger) and
`app/routines/scheduler.py`'s `RoutineScheduler` (optional cron trigger) are the only
two callers -- this module has no tool-schema/FastAPI awareness of its own.
"""

from __future__ import annotations

from typing import Any

from app.core.permissions import RequesterContext
from app.core.tool_executor import ToolExecutor
from app.database.database import SessionLocal
from app.routines.registry import RoutineRegistry
from app.tools.base import ToolResult
from app.tools.registry import ToolRegistry


class RoutineEngine:
    """Loads a routine from `RoutineRegistry` and runs its steps through `ToolExecutor`."""

    def __init__(self, tool_registry: ToolRegistry) -> None:
        self._tool_registry = tool_registry

    def run(self, name: str, context: RequesterContext | None = None) -> ToolResult:
        # One session for the whole run: loading the routine's steps and logging every
        # step's ToolExecutor attempt all happen in the same transaction/connection,
        # same as file 03's hardcoded RunRoutineTool.handler() did before this file
        # replaced it.
        db = SessionLocal()
        try:
            routine = RoutineRegistry(db).get_routine(name)
            if routine is None:
                return ToolResult(success=False, error=f"No routine named '{name}'.")
            if not routine.enabled:
                return ToolResult(success=False, error=f"Routine '{name}' is disabled.")
            if not routine.steps:
                return ToolResult(success=False, error=f"Routine '{name}' has no steps.")

            executor = ToolExecutor(self._tool_registry, db=db)
            context = context or RequesterContext(platform="desktop", scope="routine")

            completed: list[dict[str, Any]] = []
            for step in routine.steps:
                result = executor.execute(step.tool_name, step.params, context)
                completed.append(
                    {
                        "tool_name": step.tool_name,
                        "params": step.params,
                        "result": result.model_dump(),
                    }
                )
                if not result.success:
                    return ToolResult(
                        success=False,
                        error=f"Routine '{name}' failed at step '{step.tool_name}': {result.error}",
                        data={"routine": name, "steps": completed},
                    )

            return ToolResult(
                success=True,
                data={
                    "message": f"Ran routine '{name}' ({len(completed)} step(s)).",
                    "routine": name,
                    "steps": completed,
                },
            )
        finally:
            db.close()
