"""
Task reminder scheduler (§37 Phase 3 / file 04 prompt 1).

`ReminderScheduler` wraps an APScheduler `BackgroundScheduler` that polls the
`task_reminders` table (kept in sync by `TaskService._sync_reminder`) every
`POLL_INTERVAL_SECONDS` for reminders whose `remind_at` has arrived and haven't fired
yet, firing each one as a `show_notification` tool call *through* `ToolExecutor` --
never printing/calling the notification tool's handler directly (§41 Rule 6) -- so
every reminder still gets validated, permission-checked, and logged like any other tool
call. Started once from `main.py`'s lifespan and shut down on app shutdown so it never
outlives the process. Scheduled/triggered *routines* reusing this same scheduler
instance are file 04 Prompt 2's job -- this file only handles task reminders.
"""

from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from app.core.permissions import RequesterContext
from app.core.tool_executor import ToolExecutor
from app.database.database import SessionLocal
from app.database.models import Task, TaskReminder
from app.tools.registry import ToolRegistry

logger = logging.getLogger("jarvis.scheduler")

POLL_INTERVAL_SECONDS = 30


class ReminderScheduler:
    """Polls `task_reminders` on a background thread and fires due-but-unsent
    reminders as `show_notification` tool calls.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._scheduler = BackgroundScheduler(daemon=True)

    def start(self) -> None:
        self._scheduler.add_job(
            self._poll,
            "interval",
            seconds=POLL_INTERVAL_SECONDS,
            id="task_reminder_poll",
            replace_existing=True,
            next_run_time=datetime.now(),  # fire an immediate first check, then every interval
        )
        self._scheduler.start()
        logger.info("Reminder scheduler started (poll every %ss).", POLL_INTERVAL_SECONDS)

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)

    def _poll(self) -> None:
        db = SessionLocal()
        try:
            due_reminders = (
                db.query(TaskReminder)
                .filter(TaskReminder.sent.is_(False), TaskReminder.remind_at <= datetime.now())
                .all()
            )
            if not due_reminders:
                return

            executor = ToolExecutor(self._registry, db=db)
            context = RequesterContext(platform="desktop", scope="scheduler")

            for reminder in due_reminders:
                task = db.get(Task, reminder.task_id)
                title = task.title if task is not None else f"Task #{reminder.task_id}"
                result = executor.execute(
                    "show_notification",
                    {"title": "Task reminder", "message": title},
                    context,
                )
                if not result.success:
                    logger.warning(
                        "Reminder %s for task %s failed to notify: %s",
                        reminder.id,
                        reminder.task_id,
                        result.error,
                    )
                # Mark sent either way -- a failed notification tool call shouldn't
                # retry forever every poll interval; ToolExecutor already logged the
                # attempt (§41 Rule 6), which is the audit trail we rely on here.
                reminder.sent = True

            db.commit()
        except Exception:  # a bad poll must not kill the background scheduler thread
            logger.exception("Reminder poll failed.")
        finally:
            db.close()
