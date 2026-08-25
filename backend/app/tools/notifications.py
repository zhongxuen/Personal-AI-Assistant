"""
Notification tool placeholder (§37 Phase 3 / file 04 prompt 1).

`show_notification` exists so the task reminder scheduler (`app/tasks/scheduler.py`)
has a tool to fire *through* `ToolExecutor` rather than printing/logging directly --
every reminder still gets validated, permission-checked, and logged like any other tool
call (§41 Rule 6). A console bell + log line stands in for a real OS-level toast for
this phase, same as file 03's `start_timer` placeholder; a real notification backend
(winotify/plyer) replacing this and taking over `start_timer` too is file 04 Prompt 3 --
don't build that here (§41 Rule 1).
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.permissions import PermissionLevel
from app.tools.base import ToolResult

logger = logging.getLogger("jarvis.notifications")


class ShowNotificationTool:
    """Shows a desktop notification (console placeholder) with a title and body."""

    name = "show_notification"
    description = "Show a desktop notification with a title and an optional message body."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Notification title."},
            "message": {"type": "string", "description": "Optional notification body text."},
        },
        "required": ["title"],
    }
    permission = PermissionLevel.SAFE
    platforms = ["desktop"]
    requires_confirmation = False

    def handler(self, title: str, message: str | None = None, **kwargs: Any) -> ToolResult:
        title = title.strip()
        if not title:
            return ToolResult(success=False, error="A notification needs a non-empty title.")

        body = (message or "").strip()
        logger.info("Notification: %s -- %s", title, body)
        print(f"\a[Notification] {title}" + (f": {body}" if body else ""))

        return ToolResult(success=True, data={"message": f"Notification shown: {title}"})


show_notification_tool = ShowNotificationTool()
