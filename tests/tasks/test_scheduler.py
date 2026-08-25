"""
Reminder scheduler tests (§37 Phase 3 / file 04 prompt 1).

Verifies `ReminderScheduler._poll` fires a `show_notification` tool call *through*
`ToolExecutor` (mocking the notification tool's handler, never calling it or printing
directly) for every due-but-unsent `task_reminders` row, marks each as sent so it never
fires twice, and leaves not-yet-due reminders alone.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import Mock

from app.database.models import TaskReminder
from app.tasks.scheduler import ReminderScheduler
from app.tasks.service import TaskService
from app.tools.notifications import show_notification_tool
from app.tools.registry import ToolRegistry


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(show_notification_tool)
    return registry


def _mock_notification_handler(monkeypatch) -> Mock:
    mock_handler = Mock(wraps=show_notification_tool.handler)
    monkeypatch.setattr(show_notification_tool, "handler", mock_handler)
    return mock_handler


def test_poll_fires_notification_for_due_reminder_and_marks_it_sent(test_db, monkeypatch):
    mock_handler = _mock_notification_handler(monkeypatch)

    db = test_db()
    task = TaskService(db).create(title="Call dentist", due=datetime.now() - timedelta(minutes=1))
    db.close()

    ReminderScheduler(_registry())._poll()

    mock_handler.assert_called_once()
    _, kwargs = mock_handler.call_args
    assert kwargs["title"] == "Task reminder"
    assert kwargs["message"] == "Call dentist"

    db = test_db()
    reminder = db.query(TaskReminder).filter(TaskReminder.task_id == task.id).one()
    assert reminder.sent is True
    db.close()


def test_poll_leaves_not_yet_due_reminders_unsent(test_db, monkeypatch):
    mock_handler = _mock_notification_handler(monkeypatch)

    db = test_db()
    TaskService(db).create(title="Future task", due=datetime.now() + timedelta(hours=1))
    db.close()

    ReminderScheduler(_registry())._poll()

    mock_handler.assert_not_called()

    db = test_db()
    reminder = db.query(TaskReminder).one()
    assert reminder.sent is False
    db.close()


def test_poll_does_not_refire_an_already_sent_reminder(test_db, monkeypatch):
    mock_handler = _mock_notification_handler(monkeypatch)

    db = test_db()
    TaskService(db).create(title="Call dentist", due=datetime.now() - timedelta(minutes=1))
    db.close()

    scheduler = ReminderScheduler(_registry())
    scheduler._poll()
    scheduler._poll()

    mock_handler.assert_called_once()


def test_poll_falls_back_to_a_placeholder_title_for_an_orphaned_reminder(test_db, monkeypatch):
    """A reminder whose task row is gone (shouldn't normally happen -- TaskService.delete
    cleans up reminders -- but the poll loop must not crash if it does) still fires,
    using a "Task #<id>" placeholder instead of the real title.
    """
    mock_handler = _mock_notification_handler(monkeypatch)

    db = test_db()
    db.add(TaskReminder(task_id=999, remind_at=datetime.now() - timedelta(minutes=1), sent=False))
    db.commit()
    db.close()

    ReminderScheduler(_registry())._poll()

    mock_handler.assert_called_once()
    _, kwargs = mock_handler.call_args
    assert kwargs["message"] == "Task #999"
