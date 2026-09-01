"""
Task reminder scheduler (§37 Phase 3 / file 04 prompt 1).

`ReminderScheduler` wraps an APScheduler `BackgroundScheduler` that polls the
`task_reminders` table (kept in sync by `TaskService._sync_reminder`) every
`POLL_INTERVAL_SECONDS` for reminders whose `remind_at` has arrived and haven't fired
yet, firing each one as a `show_notification` tool call *through* `ToolExecutor` --
never printing/calling the notification tool's handler directly (§41 Rule 6) -- so
every reminder still gets validated, permission-checked, and logged like any other tool
call. Started once from `main.py`'s lifespan and shut down on app shutdown so it never
outlives the process.

As of file 17 (mobile/PWA) a due reminder fans out over *two* channels rather than one:
the `show_notification` tool call above (Windows toast, desktop-only) and, additively
after it, a Web Push message to every browser the reminding user has subscribed from
(`app/push/sender.py`). The push half can never weaken the desktop half -- it runs
after the toast has already fired, it cannot raise, and a dead or expired subscription
is logged and skipped. See docs/architecture.md, "ReminderScheduler is multi-channel by
design", for why that fan-out lives here rather than being pushed into a new
abstraction. `app/routines/scheduler.py`'s `RoutineScheduler` (file 04 prompt
2, optional) registers its own cron jobs against `self.scheduler` -- the same
background thread -- instead of starting a second `BackgroundScheduler`; this file
still only handles task reminders itself.
"""

from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from app.core.clock import local_now
from app.core.permissions import RequesterContext
from app.core.tool_executor import ToolExecutor
from app.database.database import SessionLocal
from app.database.models import Task, TaskReminder
from app.push.sender import WebPushSender
from app.tools.registry import ToolRegistry

logger = logging.getLogger("jarvis.scheduler")

POLL_INTERVAL_SECONDS = 30


class ReminderScheduler:
    """Polls `task_reminders` on a background thread and delivers due-but-unsent
    reminders over every channel available to the reminding user: a `show_notification`
    tool call (desktop toast) plus a Web Push message per subscribed browser.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._scheduler = BackgroundScheduler(daemon=True)

    @property
    def scheduler(self) -> BackgroundScheduler:
        """The underlying APScheduler instance, exposed so other schedule-driven
        features (file 04 prompt 2's optional `RoutineScheduler`) can register their
        own jobs against this same background thread instead of starting a second one.
        """
        return self._scheduler

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
                # `local_now()`, not `datetime.now()`: `remind_at` is naive
                # user-local (it comes from `parse_due`), so comparing it against
                # the host clock fires every reminder in a deployed backend's UTC
                # timezone -- hours early or late. `next_run_time` above stays on
                # the real system clock: that one is APScheduler's own scheduling
                # instant, not a user-facing wall clock.
                .filter(TaskReminder.sent.is_(False), TaskReminder.remind_at <= local_now())
                .all()
            )
            if not due_reminders:
                return

            executor = ToolExecutor(self._registry, db=db)
            context = RequesterContext(platform="desktop", scope="scheduler")
            push_sender = WebPushSender(db)

            for reminder in due_reminders:
                task = db.get(Task, reminder.task_id)
                title = task.title if task is not None else f"Task #{reminder.task_id}"

                # Channel 1 -- desktop toast, unchanged from file 04. Still goes
                # through ToolExecutor, never `tool.handler(...)` directly (§41 Rule 6).
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

                # Channel 2 -- Web Push to the reminding user's subscribed browsers
                # (file 17), so a reminder reaches a phone with no tab open. Additive
                # and strictly second: it runs whatever channel 1 did, it can't raise
                # (WebPushSender swallows and logs per-subscription failures), and a
                # user with no subscriptions gets exactly the pre-file-17 behaviour --
                # one toast and nothing else. `push_subscriptions` is keyed on the
                # *user*, and a reminder identifies its user only through its task, so
                # an orphaned reminder (no task row) has no user to push to.
                push_sender.send_to_user(
                    task.user_id if task is not None else None,
                    "Task reminder",
                    title,
                )

                # Mark sent once, after both channels -- a failed notification tool
                # call or a failed push shouldn't retry forever every poll interval;
                # ToolExecutor already logged the attempt (§41 Rule 6) and
                # WebPushSender logs each skipped subscription, which is the audit
                # trail we rely on here.
                reminder.sent = True

            db.commit()
        except Exception:  # a bad poll must not kill the background scheduler thread
            logger.exception("Reminder poll failed.")
        finally:
            db.close()
