"""
ReminderScheduler multi-channel fan-out tests (file 17 task 4).

A deliberately *separate* file from tests/tasks/test_scheduler.py (file 04 prompt 1),
which still owns the single-channel behaviour -- due/not-due, mark-sent-once, orphaned
reminder -- and is left unmodified. This file only covers what file 17 added: the
second channel.

The contract under test, from `_poll`'s own comments and docs/architecture.md's
"ReminderScheduler is multi-channel by design":

  * a due reminder for a user with a subscribed device fires *both* channels -- the
    `show_notification` tool call through `ToolExecutor` and a `pywebpush` send;
  * a user with zero subscriptions gets exactly the pre-file-17 behaviour: one toast,
    no push, no crash;
  * channel 2 is strictly additive -- it runs after the toast, cannot raise, and
    cannot stop the reminder being marked sent.

Same mocking discipline as file 04's tests: the notification tool is exercised through
`ToolExecutor` with only its handler mocked (never called directly, §41 Rule 6), and
`pywebpush.webpush` is mocked at `app.push.sender.webpush` so nothing signs a real
VAPID JWT or opens a socket.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pywebpush import WebPushException

from app.auth.service import AuthService
from app.database.models import Task, TaskReminder
from app.push import sender as sender_module
from app.push.service import PushSubscriptionService
from app.tasks.scheduler import ReminderScheduler
from app.tasks.service import TaskService
from app.tools.notifications import show_notification_tool
from app.tools.registry import ToolRegistry

ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123"
OTHER_ENDPOINT = "https://updates.push.services.mozilla.com/wpush/v2/xyz789"


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(show_notification_tool)
    return registry


@pytest.fixture()
def mock_handler(monkeypatch) -> Mock:
    """The `show_notification` tool's handler, mocked in place so the call still goes
    through `ToolExecutor` (validation, permissions, logging) rather than being
    bypassed.
    """
    mock = Mock(wraps=show_notification_tool.handler)
    monkeypatch.setattr(show_notification_tool, "handler", mock)
    return mock


@pytest.fixture()
def mock_webpush(monkeypatch) -> Mock:
    mock = Mock()
    monkeypatch.setattr(sender_module, "webpush", mock)
    return mock


@pytest.fixture()
def vapid_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        sender_module,
        "get_settings",
        lambda: SimpleNamespace(
            vapid_private_key="private-key",
            vapid_public_key="public-key",
            vapid_subject="mailto:jarvis@localhost",
        ),
    )


def _due_task_for(db, user_id: int | None, title: str = "Call dentist") -> Task:
    """A task whose reminder is already due. `TaskService.create` doesn't take a
    `user_id` (it predates auth), and the fan-out reads the user through the task, so
    it's set directly here.
    """
    task = TaskService(db).create(title=title, due=datetime.now() - timedelta(minutes=1))
    task.user_id = user_id
    db.commit()
    return task


def _subscribe(db, user_id: int, *endpoints: str) -> None:
    service = PushSubscriptionService(db)
    for endpoint in endpoints:
        service.subscribe(
            user_id=user_id, endpoint=endpoint, keys_p256dh="p256dh-key", keys_auth="auth-key"
        )


def _seed_user(db, username: str = "zhongxuen"):
    return AuthService(db).create_user(username, "correct horse battery staple")


# --- both channels fire ----------------------------------------------------------------


def test_due_reminder_with_a_subscribed_device_fires_toast_and_push(
    test_db, mock_handler, mock_webpush, vapid_configured
):
    db = test_db()
    user = _seed_user(db)
    task = _due_task_for(db, user.id)
    _subscribe(db, user.id, ENDPOINT)
    task_id = task.id
    db.close()

    ReminderScheduler(_registry())._poll()

    # Channel 1 -- desktop toast, through ToolExecutor.
    mock_handler.assert_called_once()
    assert mock_handler.call_args.kwargs["title"] == "Task reminder"
    assert mock_handler.call_args.kwargs["message"] == "Call dentist"

    # Channel 2 -- web push to the subscribed browser.
    mock_webpush.assert_called_once()
    assert mock_webpush.call_args.kwargs["subscription_info"]["endpoint"] == ENDPOINT

    db = test_db()
    reminder = db.query(TaskReminder).filter(TaskReminder.task_id == task_id).one()
    assert reminder.sent is True
    db.close()


def test_push_fans_out_over_every_subscribed_device(
    test_db, mock_handler, mock_webpush, vapid_configured
):
    """One user, two browsers -- one toast, two pushes."""
    db = test_db()
    user = _seed_user(db)
    _due_task_for(db, user.id)
    _subscribe(db, user.id, ENDPOINT, OTHER_ENDPOINT)
    db.close()

    ReminderScheduler(_registry())._poll()

    mock_handler.assert_called_once()
    pushed = {c.kwargs["subscription_info"]["endpoint"] for c in mock_webpush.call_args_list}
    assert pushed == {ENDPOINT, OTHER_ENDPOINT}


def test_push_only_reaches_the_reminding_users_own_devices(
    test_db, mock_handler, mock_webpush, vapid_configured
):
    db = test_db()
    owner = _seed_user(db, "zhongxuen")
    someone_else = _seed_user(db, "someone-else")
    _due_task_for(db, owner.id)
    _subscribe(db, owner.id, ENDPOINT)
    _subscribe(db, someone_else.id, OTHER_ENDPOINT)
    db.close()

    ReminderScheduler(_registry())._poll()

    mock_webpush.assert_called_once()
    assert mock_webpush.call_args.kwargs["subscription_info"]["endpoint"] == ENDPOINT


# --- no subscriptions: the pre-file-17 path, unchanged ---------------------------------


def test_user_with_zero_subscriptions_still_gets_the_desktop_toast_and_no_push(
    test_db, mock_handler, mock_webpush, vapid_configured
):
    """The no-regression case: exactly the file 04 behaviour -- one toast, nothing
    else, no crash.
    """
    db = test_db()
    user = _seed_user(db)
    task = _due_task_for(db, user.id)
    task_id = task.id
    db.close()

    ReminderScheduler(_registry())._poll()

    mock_handler.assert_called_once()
    assert mock_handler.call_args.kwargs["message"] == "Call dentist"
    mock_webpush.assert_not_called()

    db = test_db()
    reminder = db.query(TaskReminder).filter(TaskReminder.task_id == task_id).one()
    assert reminder.sent is True
    db.close()


def test_task_with_no_user_id_still_gets_the_desktop_toast_and_no_push(
    test_db, mock_handler, mock_webpush, vapid_configured
):
    """A pre-auth task row (nullable `user_id`) has nobody to push to -- the toast is
    still the whole delivery, same as before push existed.
    """
    db = test_db()
    _due_task_for(db, None)
    db.close()

    ReminderScheduler(_registry())._poll()

    mock_handler.assert_called_once()
    mock_webpush.assert_not_called()


def test_orphaned_reminder_still_toasts_and_pushes_nothing(
    test_db, mock_handler, mock_webpush, vapid_configured
):
    """A reminder whose task row is gone identifies no user, so channel 2 has no
    target -- channel 1's placeholder-title behaviour (file 04) is unaffected.
    """
    db = test_db()
    db.add(TaskReminder(task_id=999, remind_at=datetime.now() - timedelta(minutes=1), sent=False))
    db.commit()
    db.close()

    ReminderScheduler(_registry())._poll()

    mock_handler.assert_called_once()
    assert mock_handler.call_args.kwargs["message"] == "Task #999"
    mock_webpush.assert_not_called()


def test_unconfigured_vapid_leaves_the_desktop_path_untouched(
    test_db, mock_handler, mock_webpush, monkeypatch
):
    """Keys unset (a dev machine) -- subscriptions are stored but nothing sends, and
    the toast is unaffected.
    """
    monkeypatch.setattr(
        sender_module,
        "get_settings",
        lambda: SimpleNamespace(
            vapid_private_key=None, vapid_public_key=None, vapid_subject="mailto:jarvis@localhost"
        ),
    )

    db = test_db()
    user = _seed_user(db)
    _due_task_for(db, user.id)
    _subscribe(db, user.id, ENDPOINT)
    db.close()

    ReminderScheduler(_registry())._poll()

    mock_handler.assert_called_once()
    mock_webpush.assert_not_called()


# --- channel 2 is strictly additive ----------------------------------------------------


def test_a_failing_push_does_not_stop_the_toast_or_the_mark_as_sent(
    test_db, mock_handler, mock_webpush, vapid_configured
):
    """The push half can never weaken the desktop half: the toast has already fired,
    and a dead subscription must not leave the reminder to re-fire every poll.
    """
    db = test_db()
    user = _seed_user(db)
    task = _due_task_for(db, user.id)
    _subscribe(db, user.id, ENDPOINT)
    task_id = task.id
    db.close()

    mock_webpush.side_effect = WebPushException("gone", response=SimpleNamespace(status_code=410))

    ReminderScheduler(_registry())._poll()

    mock_handler.assert_called_once()
    db = test_db()
    reminder = db.query(TaskReminder).filter(TaskReminder.task_id == task_id).one()
    assert reminder.sent is True
    db.close()


def test_a_failing_toast_does_not_stop_the_push(
    test_db, mock_handler, mock_webpush, vapid_configured
):
    """The reverse direction: channel 2 runs whatever channel 1 did."""
    db = test_db()
    user = _seed_user(db)
    _due_task_for(db, user.id)
    _subscribe(db, user.id, ENDPOINT)
    db.close()

    mock_handler.side_effect = RuntimeError("no desktop session")

    ReminderScheduler(_registry())._poll()

    mock_webpush.assert_called_once()


def test_one_users_dead_subscription_does_not_block_another_reminders_push(
    test_db, mock_handler, mock_webpush, vapid_configured
):
    """Two due reminders in one poll: the first user's endpoint is gone, the second's
    still has to be delivered and both reminders still get marked sent.
    """
    db = test_db()
    first = _seed_user(db, "zhongxuen")
    second = _seed_user(db, "someone-else")
    _due_task_for(db, first.id, title="Call dentist")
    _due_task_for(db, second.id, title="Pay rent")
    _subscribe(db, first.id, ENDPOINT)
    _subscribe(db, second.id, OTHER_ENDPOINT)
    db.close()

    def fail_first(**kwargs):
        if kwargs["subscription_info"]["endpoint"] == ENDPOINT:
            raise WebPushException("gone", response=SimpleNamespace(status_code=410))

    mock_webpush.side_effect = fail_first

    ReminderScheduler(_registry())._poll()

    assert mock_handler.call_count == 2
    pushed = {c.kwargs["subscription_info"]["endpoint"] for c in mock_webpush.call_args_list}
    assert pushed == {ENDPOINT, OTHER_ENDPOINT}

    db = test_db()
    assert db.query(TaskReminder).filter(TaskReminder.sent.is_(False)).count() == 0
    db.close()


def test_push_is_not_refired_for_an_already_sent_reminder(
    test_db, mock_handler, mock_webpush, vapid_configured
):
    db = test_db()
    user = _seed_user(db)
    _due_task_for(db, user.id)
    _subscribe(db, user.id, ENDPOINT)
    db.close()

    scheduler = ReminderScheduler(_registry())
    scheduler._poll()
    scheduler._poll()

    mock_handler.assert_called_once()
    mock_webpush.assert_called_once()


def test_not_yet_due_reminder_pushes_nothing(
    test_db, mock_handler, mock_webpush, vapid_configured
):
    db = test_db()
    user = _seed_user(db)
    task = TaskService(db).create(title="Future task", due=datetime.now() + timedelta(hours=1))
    task.user_id = user.id
    db.commit()
    _subscribe(db, user.id, ENDPOINT)
    db.close()

    ReminderScheduler(_registry())._poll()

    mock_handler.assert_not_called()
    mock_webpush.assert_not_called()
