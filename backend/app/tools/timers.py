"""
Timer tool (§37 Phase 2 / file 03, updated file 04 prompt 3).

`start_timer` schedules a non-blocking countdown and, when it expires, fires a
`show_notification` tool call *through* `ToolExecutor` -- the same choke point the
reminder scheduler (`app/tasks/scheduler.py`) uses -- instead of printing/logging
directly, so every timer notification still gets validated, permission-checked, and
logged like any other tool call (§41 Rule 6). `StartTimerTool` takes the process-wide
`ToolRegistry` in its constructor (same pattern as `RunRoutineTool`) so its background
thread can build that `ToolExecutor` against the same registry every other tool call
uses.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.core.permissions import PermissionLevel
from app.tools.base import ToolResult
from app.tools.registry import ToolRegistry

logger = logging.getLogger("jarvis.timers")


def _fire_notification(registry: ToolRegistry, minutes: int, label: str | None) -> None:
    """Runs on the background timer thread once the countdown expires. Builds its own
    db session and ToolExecutor -- this runs well after `start_timer`'s handler has
    already returned, so it can't reuse anything scoped to that request.
    """
    # Imported lazily (not at module level) to break the app.tools <-> app.core.tool_executor
    # import cycle -- see RunRoutineTool.handler in app/tools/routines.py for the same reasoning.
    from app.core.permissions import RequesterContext
    from app.core.tool_executor import ToolExecutor
    from app.database.database import SessionLocal

    what = label or f"{minutes}-minute timer"
    db = SessionLocal()
    try:
        executor = ToolExecutor(registry, db=db)
        context = RequesterContext(platform="desktop", scope="timer")
        result = executor.execute(
            "show_notification",
            {"title": "Timer finished", "message": f"{what} is up!"},
            context,
        )
        if not result.success:
            logger.warning("Timer notification for %r failed: %s", what, result.error)
    except Exception:  # a bad notification must not crash the background timer thread
        logger.exception("Timer notification for %r raised.", what)
    finally:
        db.close()


class StartTimerTool:
    """Starts a background countdown; `_fire_notification` runs when it expires."""

    name = "start_timer"
    description = "Start a countdown timer for N minutes; notifies when it expires."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "minutes": {
                "type": "integer",
                "description": "How many minutes until the timer fires.",
            },
            "label": {
                "type": "string",
                "description": "Optional label describing what the timer is for.",
            },
        },
        "required": ["minutes"],
    }
    permission = PermissionLevel.SAFE
    platforms = ["desktop", "web", "discord", "mobile"]
    requires_confirmation = False

    def __init__(self, registry: ToolRegistry) -> None:
        # Held onto so the background thread's ToolExecutor (built lazily in
        # _fire_notification) dispatches through the same process-wide ToolRegistry
        # every other tool call uses.
        self._registry = registry

    def handler(self, minutes: int, label: str | None = None, **kwargs: Any) -> ToolResult:
        if minutes <= 0:
            return ToolResult(success=False, error="Timer duration must be a positive number of minutes.")

        timer = threading.Timer(minutes * 60, _fire_notification, args=(self._registry, minutes, label))
        timer.daemon = True  # never block process shutdown on a pending timer
        timer.start()

        what = label or f"{minutes}-minute timer"
        return ToolResult(success=True, data={"message": f"Started {what}."})
