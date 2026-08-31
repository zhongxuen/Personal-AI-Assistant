"""
PushSubscriptionService tests (file 17 -- web push subscription store).

Covers the three things `app/api/routes/push.py` actually calls into: create (an
upsert, not a plain insert), list-for-user (the fan-out list `WebPushSender` reads),
and delete (scoped to the owning user). Injected `Session` throughout, same as the
`TaskService`/`MemoryService` tests -- the service takes a session rather than opening
its own, so there's no module-level `SessionLocal` to patch here; `test_db` is used
only for the throwaway in-memory engine.
"""

from __future__ import annotations

from app.auth.service import AuthService
from app.database.models import PushSubscription
from app.push.service import PushSubscriptionService

ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123"
OTHER_ENDPOINT = "https://updates.push.services.mozilla.com/wpush/v2/xyz789"


def _user(db, username: str = "zhongxuen"):
    return AuthService(db).create_user(username, "correct horse battery staple")


def _subscribe(service, user_id: int, endpoint: str = ENDPOINT, p256dh="p256dh-key", auth="auth-key"):
    return service.subscribe(
        user_id=user_id, endpoint=endpoint, keys_p256dh=p256dh, keys_auth=auth
    )


# --- create (subscribe) ---------------------------------------------------------------


def test_subscribe_creates_a_row(test_db):
    db = test_db()
    user = _user(db)

    subscription = _subscribe(PushSubscriptionService(db), user.id)

    assert subscription.id is not None
    assert subscription.user_id == user.id
    assert subscription.endpoint == ENDPOINT
    assert subscription.keys_p256dh == "p256dh-key"
    assert subscription.keys_auth == "auth-key"
    assert db.query(PushSubscription).count() == 1
    db.close()


def test_subscribe_twice_with_the_same_endpoint_updates_in_place(test_db):
    """The frontend re-subscribes on every page load, so a repeat call for a known
    endpoint must update the existing row (possibly with rotated keys) rather than
    inserting a duplicate that pushes to the same browser twice.
    """
    db = test_db()
    user = _user(db)
    service = PushSubscriptionService(db)

    first = _subscribe(service, user.id)
    second = _subscribe(service, user.id, p256dh="rotated-p256dh", auth="rotated-auth")

    assert second.id == first.id
    assert db.query(PushSubscription).count() == 1
    assert second.keys_p256dh == "rotated-p256dh"
    assert second.keys_auth == "rotated-auth"
    db.close()


def test_subscribe_reassigns_a_shared_browser_to_the_current_user(test_db):
    """Same endpoint, different user -- a shared browser follows whoever is logged in
    now instead of continuing to deliver to the previous user.
    """
    db = test_db()
    first_user = _user(db, "zhongxuen")
    second_user = _user(db, "someone-else")
    service = PushSubscriptionService(db)

    _subscribe(service, first_user.id)
    reassigned = _subscribe(service, second_user.id)

    assert reassigned.user_id == second_user.id
    assert db.query(PushSubscription).count() == 1
    assert service.list_for_user(first_user.id) == []
    db.close()


def test_get_by_endpoint_returns_none_for_an_unknown_endpoint(test_db):
    db = test_db()
    assert PushSubscriptionService(db).get_by_endpoint("https://nope.example/none") is None
    db.close()


# --- list ------------------------------------------------------------------------------


def test_list_for_user_starts_empty(test_db):
    db = test_db()
    user = _user(db)
    assert PushSubscriptionService(db).list_for_user(user.id) == []
    db.close()


def test_list_for_user_returns_every_browser_that_user_subscribed_from(test_db):
    """One user with a phone and a laptop is two rows, and delivery fans out over
    both -- see `PushSubscription`'s docstring.
    """
    db = test_db()
    user = _user(db)
    service = PushSubscriptionService(db)

    _subscribe(service, user.id, endpoint=ENDPOINT)
    _subscribe(service, user.id, endpoint=OTHER_ENDPOINT)

    endpoints = {s.endpoint for s in service.list_for_user(user.id)}
    assert endpoints == {ENDPOINT, OTHER_ENDPOINT}
    db.close()


def test_list_for_user_excludes_other_users_subscriptions(test_db):
    db = test_db()
    mine = _user(db, "zhongxuen")
    theirs = _user(db, "someone-else")
    service = PushSubscriptionService(db)

    _subscribe(service, mine.id, endpoint=ENDPOINT)
    _subscribe(service, theirs.id, endpoint=OTHER_ENDPOINT)

    assert [s.endpoint for s in service.list_for_user(mine.id)] == [ENDPOINT]
    db.close()


# --- delete (unsubscribe) --------------------------------------------------------------


def test_unsubscribe_deletes_the_row_and_reports_true(test_db):
    db = test_db()
    user = _user(db)
    service = PushSubscriptionService(db)
    _subscribe(service, user.id)

    assert service.unsubscribe(ENDPOINT, user.id) is True
    assert db.query(PushSubscription).count() == 0
    db.close()


def test_unsubscribe_unknown_endpoint_reports_false(test_db):
    db = test_db()
    user = _user(db)
    assert PushSubscriptionService(db).unsubscribe("https://nope.example/none", user.id) is False
    db.close()


def test_unsubscribe_another_users_endpoint_is_refused_and_deletes_nothing(test_db):
    """Scoped to `user_id` so one authenticated user can't unsubscribe another's
    browser by guessing an endpoint -- reported the same as "no such endpoint".
    """
    db = test_db()
    owner = _user(db, "zhongxuen")
    attacker = _user(db, "someone-else")
    service = PushSubscriptionService(db)
    _subscribe(service, owner.id)

    assert service.unsubscribe(ENDPOINT, attacker.id) is False
    assert db.query(PushSubscription).count() == 1
    assert len(service.list_for_user(owner.id)) == 1
    db.close()
