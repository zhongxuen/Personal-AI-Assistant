"""
Default tool registration (§37 Phase 2 / file 03, extended file 04 prompt 1).

`register_default_tools` wires every built-in `Tool` into a `ToolRegistry`, including
the aliases `CommandRouter` needs for deterministic phrasings like "open vscode",
"quit chrome", "remind me to buy milk", "show my tasks", or "set a timer for 10
minutes" (§11). Called once from `main.py`'s lifespan against the process-wide registry
(`app/api/dependencies`) so every request routes against the same fully populated set
of tools. `edit_task`/`delete_task`/`show_notification` (file 04) have no dedicated
alias -- they're reached via exact tool-name match or a future LLM-routed call, same as
`complete_task`.
"""

from __future__ import annotations

from app.tools.applications import close_application_tool, open_application_tool
from app.tools.notifications import show_notification_tool
from app.tools.registry import ToolRegistry
from app.tools.routines import RunRoutineTool
from app.tools.system import get_system_info_tool, get_time_tool
from app.tools.tasks import (
    complete_task_tool,
    create_task_tool,
    delete_task_tool,
    edit_task_tool,
    list_tasks_tool,
)
from app.tools.timers import start_timer_tool


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
    registry.register(start_timer_tool, aliases=["set a timer for"])
    # RunRoutineTool is built here (not as a module-level singleton like the other
    # tools) because it needs the same registry it's being registered into, to build
    # its own ToolExecutor for dispatching each routine step (§41 Rule 6).
    registry.register(RunRoutineTool(registry), aliases=["start coding"])
