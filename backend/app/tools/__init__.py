"""
Default tool registration (§37 Phase 2 / file 03, extended file 04 prompts 1 & 2).

`register_default_tools` wires every built-in `Tool` into a `ToolRegistry`, including
the aliases `CommandRouter` needs for deterministic phrasings like "open vscode",
"quit chrome", "remind me to buy milk", "show my tasks", or "set a timer for 10
minutes" (§11). Called once from `main.py`'s lifespan against the process-wide registry
(`app/api/dependencies`) so every request routes against the same fully populated set
of tools. `edit_task`/`delete_task`/`show_notification` (file 04) have no dedicated
alias -- they're reached via exact tool-name match or a future LLM-routed call, same as
`complete_task`. Also seeds, in order: the "coding" memory entry -- editor/browser/
default_project (file 09 prompt 1) -- then the "applications" memory category (file 09
prompt 2, promoting file 03's former hardcoded `APP_MAP`), then the "coding" routine as
a persisted row (file 04 prompt 2, file 09 prompt 2) whose middle step now reads its
default-project app_name back off memory. Memory must be seeded first: the routine seed
reads `coding.default_project` from it while building the routine's steps.
"""

from __future__ import annotations

from app.memory.service import seed_default_memory
from app.tools.applications import (
    close_application_tool,
    open_application_tool,
    seed_default_applications,
)
from app.tools.notifications import show_notification_tool
from app.tools.registry import ToolRegistry
from app.tools.routines import RunRoutineTool, seed_default_routines
from app.tools.system import get_system_info_tool, get_time_tool
from app.tools.tasks import (
    complete_task_tool,
    create_task_tool,
    delete_task_tool,
    edit_task_tool,
    list_tasks_tool,
)
from app.tools.timers import StartTimerTool


def register_default_tools(registry: ToolRegistry) -> None:
    registry.register(get_time_tool, aliases=["what time is it"])
    registry.register(get_system_info_tool)
    registry.register(open_application_tool, aliases=["open", "launch", "start"])
    registry.register(close_application_tool, aliases=["close", "quit", "kill"])
    registry.register(create_task_tool, aliases=["remind me to"])
    registry.register(list_tasks_tool, aliases=["show my tasks", "list tasks"])
    registry.register(complete_task_tool)
    registry.register(edit_task_tool)
    registry.register(delete_task_tool)
    registry.register(show_notification_tool)
    # StartTimerTool (like RunRoutineTool below) is built here rather than as a
    # module-level singleton because it needs the same registry it's being registered
    # into, to build its own ToolExecutor for firing the expiry notification (§41 Rule 6).
    registry.register(StartTimerTool(registry), aliases=["set a timer for"])
    # RunRoutineTool is built here (not as a module-level singleton like the other
    # tools) because it needs the same registry it's being registered into, to build
    # its own ToolExecutor for dispatching each routine step (§41 Rule 6).
    registry.register(RunRoutineTool(registry), aliases=["start coding"])
    # Seed order matters here: each is idempotent (see its own docstring), but
    # seed_default_routines() reads the "coding" memory entry while building the
    # routine's steps, so memory must exist first. Called here, not only from main.py,
    # so every path that builds a full tool set (production startup and tests alike)
    # gets everything seeded consistently.
    seed_default_memory()
    seed_default_applications()
    seed_default_routines()
