"""
Notification tool (§37 Phase 3 / file 04 prompt 3).

`show_notification` fires a real Windows toast via `winotify` instead of the
console-bell placeholder file 03/file 04 prompt 1 shipped. It's still the one and only
place that talks to the OS notification layer -- `start_timer` (`app/tools/timers.py`)
and the reminder scheduler (`app/tasks/scheduler.py`) both call it *through*
`ToolExecutor` rather than importing `winotify` themselves, so every notification (timer
or reminder) still gets validated, permission-checked, and logged like any other tool
call (§41 Rule 6).

`winotify` shells out to PowerShell to raise a native toast and only works on Windows;
if it's unavailable (missing dependency, non-Windows host, no `powershell.exe`) the
handler falls back to logging + a console bell rather than raising, so a CI box or a
future non-desktop platform doesn't crash a tool call over a notification.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.permissions import PermissionLevel
from app.tools.base import ToolResult

logger = logging.getLogger("jarvis.notifications")


def _show_toast(title: str, body: str) -> None:
    """Raises a native Windows toast via `winotify`. Raises on any failure -- the
    caller decides how to degrade.
    """
    from winotify import Notification  # imported lazily so importing this module never requires winotify

    toast = Notification(app_id="Jarvis", title=title, msg=body)
    toast.show()


class ShowNotificationTool:
    """Shows a desktop notification (Windows toast) with a title and body."""

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

        try:
            _show_toast(title, body)
        except Exception as exc:
            # Degrade to a console bell rather than failing the tool call -- a missing
            # winotify install or a non-Windows host shouldn't take down timers/reminders.
            logger.warning("Toast notification failed (%s); falling back to console.", exc)
            print(f"\a[Notification] {title}" + (f": {body}" if body else ""))

        return ToolResult(success=True, data={"message": f"Notification shown: {title}"})


show_notification_tool = ShowNotificationTool()
