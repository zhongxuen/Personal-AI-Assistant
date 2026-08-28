"""
WebPushSender tests (file 17 -- the delivery half of web push).

`pywebpush.webpush` is mocked at `app.push.sender.webpush` throughout: nothing here
signs a real VAPID JWT, encrypts a payload, or opens a socket. `get_settings` is
stubbed the same way tests/platforms/test_discord_manager.py stubs it, since it is
`@lru_cache`d and the real one reads the developer's `.env`.

The point of this file is the guarantee `ReminderScheduler` leans on (see
tests/push/test_reminder_fanout.py): `send_to_user` never raises and never lets one
dead subscription stop the others.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pywebpush import WebPushException

from app.auth.service import AuthService
from app.database.models import PushSubscription
from app.push import sender as sender_module
from app.push.sender import WebPushSender
from app.push.service import PushSubscriptionService

ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123"
OTHER_ENDPOINT = "https://updates.push.services.mozilla.com/wpush/v2/xyz789"


def _configure(monkeypatch, *, private="private-key", public="public-key"):
    monkeypatch.setattr(
        sender_module,
        "get_settings",
        lambda: SimpleNamespace(
            vapid_private_key=private,
            vapid_public_key=public,
            vapid_subject="mailto:jarvis@localhost",
        ),
    )


@pytest.fixture()
def mock_webpush(monkeypatch) -> Mock:
    mock = Mock()
    monkeypatch.setattr(sender_module, "webpush", mock)
    return mock


def _user_with_subscriptions(db, *endpoints: str):
    user = AuthService(db).create_user("zhongxuen", "correct horse battery staple")
    service = PushSubscriptionService(db)
    for endpoint in endpoints:
        service.subscribe(
            user_id=user.id, endpoint=endpoint, keys_p256dh="p256dh-key", keys_auth="auth-key"
        )
    return user


# --- configured / unconfigured ---------------------------------------------------------


def test_configured_is_true_only_when_both_halves_of_the_vapid_pair_are_set(test_db, monkeypatch):
    db = test_db()
    _configure(monkeypatch)
    assert WebPushSender(db).configured is True

    _configure(monkeypatch, private=None)
    assert WebPushSender(db).configured is False

    _configure(monkeypatch, public=None)
    assert WebPushSender(db).configured is False
    db.close()


def test_send_to_user_no_ops_when_vapid_is_unconfigured(test_db, monkeypatch, mock_webpush):
    """A dev machine with no keys keeps its desktop toasts and simply never pushes --
    the "absence is a valid, non-crashing state" convention, not an error.
    """
    db = test_db()
    user = _user_with_subscriptions(db, ENDPOINT)
    _configure(monkeypatch, private=None, public=None)

    assert WebPushSender(db).send_to_user(user.id, "Task reminder", "Call dentist") == 0
    mock_webpush.assert_not_called()
    db.close()


def test_send_to_user_with_no_user_id_sends_nothing(test_db, monkeypatch, mock_webpush):
    """Pre-auth rows have a nullable `user_id` and nobody to deliver to."""
    db = test_db()
    _configure(monkeypatch)

    assert WebPushSender(db).send_to_user(None, "Task reminder", "Call dentist") == 0
    mock_webpush.assert_not_called()
    db.close()


def test_send_to_user_with_no_subscriptions_sends_nothing(test_db, monkeypatch, mock_webpush):
    db = test_db()
    user = _user_with_subscriptions(db)
    _configure(monkeypatch)

    assert WebPushSender(db).send_to_user(user.id, "Task reminder", "Call dentist") == 0
    mock_webpush.assert_not_called()
    db.close()


# --- delivery --------------------------------------------------------------------------


def test_send_to_user_posts_the_expected_payload_and_subscription_info(
    test_db, monkeypatch, mock_webpush
):
    db = test_db()
    user = _user_with_subscriptions(db, ENDPOINT)
    _configure(monkeypatch)

    assert WebPushSender(db).send_to_user(user.id, "Task reminder", "Call dentist") == 1

    mock_webpush.assert_called_once()
    kwargs = mock_webpush.call_args.kwargs
    assert kwargs["subscription_info"] == {
        "endpoint": ENDPOINT,
        "keys": {"p256dh": "p256dh-key", "auth": "auth-key"},
    }
    assert json.loads(kwargs["data"]) == {
        "title": "Task reminder",
        "body": "Call dentist",
        "url": "/tasks",
    }
    assert kwargs["vapid_private_key"] == "private-key"
    assert kwargs["vapid_claims"] == {"sub": "mailto:jarvis@localhost"}
    db.close()


def test_send_to_user_fans_out_over_every_subscribed_browser(test_db, monkeypatch, mock_webpush):
    db = test_db()
    user = _user_with_subscriptions(db, ENDPOINT, OTHER_ENDPOINT)
    _configure(monkeypatch)

    assert WebPushSender(db).send_to_user(user.id, "Task reminder", "Call dentist") == 2

    pushed = {c.kwargs["subscription_info"]["endpoint"] for c in mock_webpush.call_args_list}
    assert pushed == {ENDPOINT, OTHER_ENDPOINT}
    db.close()


def test_send_to_user_truncates_an_oversized_body(test_db, monkeypatch, mock_webpush):
    """Push services cap an encrypted payload at ~4KB -- truncating beats the whole
    send failing.
    """
    db = test_db()
    user = _user_with_subscriptions(db, ENDPOINT)
    _configure(monkeypatch)

    WebPushSender(db).send_to_user(user.id, "Task reminder", "x" * 5000)

    body = json.loads(mock_webpush.call_args.kwargs["data"])["body"]
    assert len(body) == sender_module._MAX_BODY_CHARS
    db.close()


# --- failure isolation: send_to_user never raises ---------------------------------------


def test_an_expired_subscription_is_skipped_without_raising(test_db, monkeypatch, mock_webpush):
    """404/410 means the browser dropped the subscription. Logged distinctly and
    skipped -- and deliberately *not* pruned from the table by the delivery path.
    """
    db = test_db()
    user = _user_with_subscriptions(db, ENDPOINT)
    _configure(monkeypatch)
    mock_webpush.side_effect = WebPushException("gone", response=SimpleNamespace(status_code=410))

    assert WebPushSender(db).send_to_user(user.id, "Task reminder", "Call dentist") == 0
    assert db.query(PushSubscription).count() == 1
    db.close()


def test_one_dead_subscription_does_not_stop_the_others(test_db, monkeypatch, mock_webpush):
    db = test_db()
    user = _user_with_subscriptions(db, ENDPOINT, OTHER_ENDPOINT)
    _configure(monkeypatch)

    def fail_first(**kwargs):
        if kwargs["subscription_info"]["endpoint"] == ENDPOINT:
            raise WebPushException("gone", response=SimpleNamespace(status_code=410))

    mock_webpush.side_effect = fail_first

    assert WebPushSender(db).send_to_user(user.id, "Task reminder", "Call dentist") == 1
    assert mock_webpush.call_count == 2
    db.close()


@pytest.mark.parametrize(
    "error",
    [
        WebPushException("rate limited", response=SimpleNamespace(status_code=429)),
        WebPushException("no response object attached"),
        TimeoutError("push service timed out"),
        ValueError("malformed vapid key"),
    ],
)
def test_send_to_user_swallows_every_delivery_error(test_db, monkeypatch, mock_webpush, error):
    """The first caller is a background poll loop that must survive anything this
    does, so every failure mode returns 0 rather than propagating.
    """
    db = test_db()
    user = _user_with_subscriptions(db, ENDPOINT)
    _configure(monkeypatch)
    mock_webpush.side_effect = error

    assert WebPushSender(db).send_to_user(user.id, "Task reminder", "Call dentist") == 0
    db.close()


def test_send_to_user_survives_a_failing_subscription_lookup(test_db, monkeypatch, mock_webpush):
    db = test_db()
    _configure(monkeypatch)
    sender = WebPushSender(db)
    monkeypatch.setattr(
        sender._subscriptions, "list_for_user", Mock(side_effect=RuntimeError("db is gone"))
    )

    assert sender.send_to_user(1, "Task reminder", "Call dentist") == 0
    mock_webpush.assert_not_called()
    db.close()
