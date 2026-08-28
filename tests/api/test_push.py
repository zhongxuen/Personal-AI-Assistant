"""
Push subscription route tests (file 17 -- `/api/push/subscribe`).

Two fixtures, mirroring the split the existing route tests already use:

  * `client` overrides `get_current_user` to a fixed, real `User` row (the
    tests/api/test_memory.py pattern -- a real row rather than `object()` because
    these routes read `current_user.id` and store it on the subscription). Used for
    the behavioural tests.
  * `auth_client` overrides nothing but `get_db` and drives the real
    `get_current_user` end to end (the tests/api/test_auth.py pattern), proving both
    routes are actually gated. Both are "non-desktop-local" in §34's sense: a
    subscription is stored *against the authenticated user*, so there is no meaningful
    unauthenticated version of either call.

`DELETE /api/push/subscribe` takes a request body (an endpoint is a long push-service
URL), so it's issued via `client.request("DELETE", ..., json=...)` -- httpx's
`.delete()` helper takes no `json=`.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user
from app.auth.service import AuthService
from app.database.database import get_db
from app.database.models import PushSubscription
from app.push.service import PushSubscriptionService
from main import app

USERNAME = "zhongxuen"
PASSWORD = "correct horse battery staple"
OTHER_USERNAME = "someone-else"

ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123"
OTHER_ENDPOINT = "https://updates.push.services.mozilla.com/wpush/v2/xyz789"

SUBSCRIPTION_BODY = {
    "endpoint": ENDPOINT,
    "keys": {"p256dh": "p256dh-key", "auth": "auth-key"},
}


def _override_get_db(test_db):
    def override():
        db = test_db()
        try:
            yield db
        finally:
            db.close()

    return override


@pytest.fixture()
def client(test_db):
    """Authenticated as a fixed real user, auth itself stubbed out."""
    seed_db = test_db()
    user = AuthService(seed_db).create_user(USERNAME, PASSWORD)
    user_id = user.id
    seed_db.close()

    app.dependency_overrides[get_db] = _override_get_db(test_db)

    def current_user(db=None):
        session = test_db()
        try:
            from app.database.models import User

            return session.get(User, user_id)
        finally:
            session.close()

    app.dependency_overrides[get_current_user] = current_user
    client = TestClient(app)
    client.user_id = user_id
    yield client
    app.dependency_overrides.clear()


@pytest.fixture()
def auth_client(test_db):
    """No auth override -- the real `get_current_user` runs, so a token is required."""
    seed_db = test_db()
    AuthService(seed_db).create_user(USERNAME, PASSWORD)
    AuthService(seed_db).create_user(OTHER_USERNAME, PASSWORD)
    seed_db.close()

    app.dependency_overrides[get_db] = _override_get_db(test_db)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _auth_headers(client: TestClient, username: str = USERNAME) -> dict[str, str]:
    response = client.post("/api/auth/login", data={"username": username, "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# --- auth required (both routes) -------------------------------------------------------


def test_subscribe_rejects_request_with_no_token(auth_client):
    assert auth_client.post("/api/push/subscribe", json=SUBSCRIPTION_BODY).status_code == 401


def test_subscribe_rejects_invalid_token(auth_client):
    response = auth_client.post(
        "/api/push/subscribe",
        json=SUBSCRIPTION_BODY,
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401


def test_subscribe_accepts_request_with_valid_token(auth_client):
    response = auth_client.post(
        "/api/push/subscribe", json=SUBSCRIPTION_BODY, headers=_auth_headers(auth_client)
    )
    assert response.status_code == 201


def test_unsubscribe_rejects_request_with_no_token(auth_client):
    response = auth_client.request(
        "DELETE", "/api/push/subscribe", json={"endpoint": ENDPOINT}
    )
    assert response.status_code == 401


def test_unsubscribe_rejects_invalid_token(auth_client):
    response = auth_client.request(
        "DELETE",
        "/api/push/subscribe",
        json={"endpoint": ENDPOINT},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401


def test_unsubscribe_accepts_request_with_valid_token(auth_client):
    headers = _auth_headers(auth_client)
    auth_client.post("/api/push/subscribe", json=SUBSCRIPTION_BODY, headers=headers)

    response = auth_client.request(
        "DELETE", "/api/push/subscribe", json={"endpoint": ENDPOINT}, headers=headers
    )
    assert response.status_code == 204


def test_an_unauthenticated_request_stores_nothing(auth_client, test_db):
    auth_client.post("/api/push/subscribe", json=SUBSCRIPTION_BODY)

    db = test_db()
    assert db.query(PushSubscription).count() == 0
    db.close()


def test_a_subscription_is_stored_against_the_token_holder(auth_client, test_db):
    auth_client.post(
        "/api/push/subscribe",
        json=SUBSCRIPTION_BODY,
        headers=_auth_headers(auth_client, OTHER_USERNAME),
    )

    db = test_db()
    other_user = AuthService(db).get_by_username(OTHER_USERNAME)
    stored = db.query(PushSubscription).one()
    assert stored.user_id == other_user.id
    db.close()


def test_one_user_cannot_unsubscribe_anothers_browser(auth_client, test_db):
    """Scoped to the authenticated user in the service -- reported as 404, the same as
    an endpoint that doesn't exist, so a caller can't probe for others' endpoints.
    """
    auth_client.post(
        "/api/push/subscribe", json=SUBSCRIPTION_BODY, headers=_auth_headers(auth_client)
    )

    response = auth_client.request(
        "DELETE",
        "/api/push/subscribe",
        json={"endpoint": ENDPOINT},
        headers=_auth_headers(auth_client, OTHER_USERNAME),
    )

    assert response.status_code == 404
    db = test_db()
    assert db.query(PushSubscription).count() == 1
    db.close()


# --- POST /api/push/subscribe ----------------------------------------------------------


def test_subscribe_returns_201_with_the_stored_id_and_endpoint(client):
    response = client.post("/api/push/subscribe", json=SUBSCRIPTION_BODY)

    assert response.status_code == 201
    body = response.json()
    assert body["endpoint"] == ENDPOINT
    assert isinstance(body["id"], int)


def test_subscribe_stores_the_nested_keys_object(client, test_db):
    client.post("/api/push/subscribe", json=SUBSCRIPTION_BODY)

    db = test_db()
    stored = db.query(PushSubscription).one()
    assert stored.keys_p256dh == "p256dh-key"
    assert stored.keys_auth == "auth-key"
    assert stored.user_id == client.user_id
    db.close()


def test_subscribe_is_idempotent_by_endpoint(client, test_db):
    """The frontend calls this on every page load without tracking whether it has
    subscribed before -- a repeat call updates the row instead of duplicating it.
    """
    first = client.post("/api/push/subscribe", json=SUBSCRIPTION_BODY)
    second = client.post(
        "/api/push/subscribe",
        json={"endpoint": ENDPOINT, "keys": {"p256dh": "rotated", "auth": "rotated-auth"}},
    )

    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]

    db = test_db()
    stored = db.query(PushSubscription).one()
    assert stored.keys_p256dh == "rotated"
    db.close()


def test_subscribe_ignores_the_browsers_expiration_time_field(client):
    """`PushSubscription.toJSON()` also includes `expirationTime`; the frontend posts
    its subscription through unchanged, so an unknown field must not 422.
    """
    response = client.post(
        "/api/push/subscribe", json={**SUBSCRIPTION_BODY, "expirationTime": None}
    )
    assert response.status_code == 201


def test_subscribe_stores_one_row_per_browser(client, test_db):
    client.post("/api/push/subscribe", json=SUBSCRIPTION_BODY)
    client.post(
        "/api/push/subscribe",
        json={"endpoint": OTHER_ENDPOINT, "keys": {"p256dh": "k", "auth": "a"}},
    )

    db = test_db()
    assert db.query(PushSubscription).count() == 2
    assert len(PushSubscriptionService(db).list_for_user(client.user_id)) == 2
    db.close()


@pytest.mark.parametrize(
    "payload",
    [
        {"keys": {"p256dh": "k", "auth": "a"}},
        {"endpoint": ENDPOINT},
        {"endpoint": ENDPOINT, "keys": {"p256dh": "k"}},
        {"endpoint": ENDPOINT, "keys": "not-an-object"},
    ],
    ids=["no-endpoint", "no-keys", "incomplete-keys", "keys-wrong-type"],
)
def test_subscribe_rejects_a_malformed_payload(client, payload):
    assert client.post("/api/push/subscribe", json=payload).status_code == 422


# --- DELETE /api/push/subscribe --------------------------------------------------------


def test_unsubscribe_removes_the_row(client, test_db):
    client.post("/api/push/subscribe", json=SUBSCRIPTION_BODY)

    response = client.request("DELETE", "/api/push/subscribe", json={"endpoint": ENDPOINT})

    assert response.status_code == 204
    db = test_db()
    assert db.query(PushSubscription).count() == 0
    db.close()


def test_unsubscribe_unknown_endpoint_404s(client):
    response = client.request(
        "DELETE", "/api/push/subscribe", json={"endpoint": "https://nope.example/none"}
    )
    assert response.status_code == 404


def test_unsubscribe_leaves_this_users_other_browsers_alone(client, test_db):
    client.post("/api/push/subscribe", json=SUBSCRIPTION_BODY)
    client.post(
        "/api/push/subscribe",
        json={"endpoint": OTHER_ENDPOINT, "keys": {"p256dh": "k", "auth": "a"}},
    )

    client.request("DELETE", "/api/push/subscribe", json={"endpoint": ENDPOINT})

    db = test_db()
    remaining = db.query(PushSubscription).one()
    assert remaining.endpoint == OTHER_ENDPOINT
    db.close()


@pytest.mark.parametrize("payload", [{}, {"endpoint": ""}], ids=["no-endpoint", "blank-endpoint"])
def test_unsubscribe_rejects_a_malformed_payload(client, payload):
    response = client.request("DELETE", "/api/push/subscribe", json=payload)
    assert response.status_code == 422
