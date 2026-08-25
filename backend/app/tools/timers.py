"""
Timer tool (§37 Phase 2 / file 03).

`start_timer` schedules a non-blocking countdown and fires a notification when it
expires. A log line (plus a console bell) stands in for a real desktop toast for this
phase -- file 11 wires up proper OS-level notification tooling; don't build that here
(§41 Rule 1).
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.core.permissions import PermissionLevel
from app.tools.base import ToolResult

logger = logging.getLogger("jarvis.timers")


def _fire_notification(minutes: int, label: str | None) -> None:
    """Runs on the background timer thread once the countdown expires."""
    what = label or f"{minutes}-minute timer"
    logger.info("Timer finished: %s", what)
    print(f"\a[Timer] {what} is up!")  # console bell + message -- placeholder for a real toast


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
    platforms = ["desktop", "web", "discord"]
    requires_confirmation = False

    def handler(self, minutes: int, label: str | None = None, **kwargs: Any) -> ToolResult:
        if minutes <= 0:
            return ToolResult(success=False, error="Timer duration must be a positive number of minutes.")

        timer = threading.Timer(minutes * 60, _fire_notification, args=(minutes, label))
        timer.daemon = True  # never block process shutdown on a pending timer
        timer.start()

        what = label or f"{minutes}-minute timer"
        return ToolResult(success=True, data={"message": f"Started {what}."})


start_timer_tool = StartTimerTool()
