"""
Routine tool (§37 Phase 2 / file 03, replaced file 04 prompt 2, promoted file 09 prompt 2).

`run_routine` now runs any named routine persisted via `RoutineRegistry`/
`RoutineEngine` (`app/routines/`), instead of file 03's hardcoded `ROUTINES` dict.
`seed_default_routines()` creates the "coding" routine -- open VS Code -> open the
configured default project -> open Chrome -- as a real persisted row the first time
the app starts (`register_default_tools`, called once from `main.py`'s lifespan, calls
this after registering `RunRoutineTool`). It's idempotent: every call after the first
is a no-op, since it only seeds a name that's missing -- never resurrecting a routine a
user has since edited or deleted.

The middle step's `app_name` is no longer a hardcoded "portfolio folder" string: it's
read from `MemoryService.get("routines", "coding")["default_project"]` at seed time --
development-plan.md §15's exact example of "Start coding" resolving to the correct
environment via memory. Whatever a user sets `coding.default_project` to (via
`MemoryService.set()` or the settings UI) is what gets seeded into the routine, as long
as seeding happens after `seed_default_memory()` has run (see `app/tools/__init__.py`'s
call order).

`RunRoutineTool` is now a thin dispatcher onto `RoutineEngine.run()`, which is where the
actual step-by-step `ToolExecutor` dispatch (§41 Rule 6) lives.
"""

from __future__ import annotations

from typing import Any

from app.core.permissions import PermissionLevel
from app.database.database import SessionLocal
from app.memory.service import DEFAULT_CODING_CATEGORY, DEFAULT_CODING_KEY, DEFAULT_CODING_VALUE, MemoryService
from app.routines.registry import RoutineRegistry
from app.tools.base import ToolResult
from app.tools.registry import ToolRegistry

DEFAULT_ROUTINE_NAME = "coding"


def _default_routine_steps(db: Any) -> list[tuple[str, dict[str, Any]]]:
    """Open VS Code -> open the memory-configured default project -> open Chrome.

    Reads `coding.default_project` off the same session `seed_default_routines()` is
    already using, so this reflects whatever value is currently persisted (falling
    back to `DEFAULT_CODING_VALUE` if the "coding" memory entry hasn't been seeded/set
    yet) -- never a hardcoded folder alias.
    """
    coding = MemoryService(db).get(DEFAULT_CODING_CATEGORY, DEFAULT_CODING_KEY, DEFAULT_CODING_VALUE)
    default_project = coding.get("default_project", DEFAULT_CODING_VALUE["default_project"])
    return [
        ("open_application", {"app_name": "vscode"}),
        ("open_application", {"app_name": default_project}),
        ("open_application", {"app_name": "chrome"}),
    ]


def seed_default_routines() -> None:
    """Create the "coding" routine as a persisted row if it doesn't already exist.

    Idempotent -- safe to call on every startup. Requires the `routines`/
    `routine_steps` tables to already exist (`main.py`'s lifespan runs
    `Base.metadata.create_all` before `register_default_tools`, which calls this).
    """
    db = SessionLocal()
    try:
        registry = RoutineRegistry(db)
        if registry.get_routine(DEFAULT_ROUTINE_NAME) is None:
            registry.create_routine(DEFAULT_ROUTINE_NAME, _default_routine_steps(db))
    finally:
        db.close()


class RunRoutineTool:
    """Runs a named, persisted routine via `RoutineEngine`."""

    name = "run_routine"
    description = "Run a named routine: a persisted, ordered sequence of tool calls (e.g. 'coding')."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "routine_name": {
                "type": "string",
                "description": (
                    "Name of the routine to run. Defaults to 'coding', the routine "
                    "seeded at first startup."
                ),
            }
        },
        "required": [],
    }
    permission = PermissionLevel.SAFE
    # Deliberately NOT extended to "web"/"discord" (§22 review, file 12/13): unlike
    # `create_task`/`list_tasks`/`get_time`/`get_system_info`, this stays desktop-only
    # on purpose. Two reasons, either one sufficient on its own:
    #   1. The only routine that exists today ("coding") is a chain of `open_application`
    #      steps -- itself `platforms=["desktop"]` -- so a non-desktop caller could never
    #      get past step one anyway.
    #   2. Unlike `app.api.routes.routines.run_routine` (the Routine Dashboard's REST
    #      route, file 12 prompt 2), this tool's `handler()` still calls
    #      `RoutineEngine.run(routine_name)` with no `context` -- `RoutineEngine.run()`
    #      then defaults to `RequesterContext(platform="desktop", ...)` regardless of who
    #      actually called `run_routine`. That default is safe *only* because
    #      `platforms=["desktop"]` here means the real caller already had to be on
    #      desktop. Adding "discord"/"web" without first threading the real requester
    #      platform through to `RoutineEngine.run()` (the way the REST route now does)
    #      would let a non-desktop caller run a desktop-only step chain for real --
    #      exactly the gap docs/security.md flagged and file 12 prompt 2 closed for the
    #      REST route, reopened here. Fix that plumbing first if this ever needs to be
    #      chat-reachable.
    platforms = ["desktop"]
    requires_confirmation = False

    def __init__(self, registry: ToolRegistry) -> None:
        # Held onto so RoutineEngine (built lazily in handler()) dispatches steps
        # through the same process-wide ToolRegistry every other tool call uses.
        self._registry = registry

    def handler(self, routine_name: str = DEFAULT_ROUTINE_NAME, **kwargs: Any) -> ToolResult:
        # Imported lazily (not at module level) to break the app.tools <-> app.core.tool_executor
        # import cycle: ToolExecutor imports app.tools.base, which forces the whole app.tools
        # package (this module included) to finish loading first, so a top-level import here
        # would deadlock if anything imports app.core.tool_executor before app.tools.
        from app.routines.engine import RoutineEngine

        return RoutineEngine(self._registry).run(routine_name)
