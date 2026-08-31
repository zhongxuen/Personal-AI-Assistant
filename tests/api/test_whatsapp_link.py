"""
WhatsApp linking route tests (file 18 prompt 1 -- `/api/whatsapp/link-code`, `/link`).

Same two-fixture split as tests/api/test_push.py: `client` stubs `get_current_user` to a
fixed real `User` row (these routes read `current_user.id` and the WhatsApp columns off
it) for the behavioural tests, and `auth_client` drives the real dependency end to end to
prove every route is actually gated. Gating is the point of this half of the flow -- the
pairing code is what lets an unauthenticated webhook trust a bare phone number later, so
an unauthenticated caller must never be able to mint one.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user
from app.auth.service import AuthService
from app.database.database import get_db
from app.database.models import User
from app.api.routes import whatsapp as whatsapp_routes
from app.whatsapp.linking import LINK_CODE_LENGTH, WhatsAppLinkService
from main import app

USERNAME = "zhongxuen"
PASSWORD = "correct horse battery staple"
PHONE = "60123456789"


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
    seed_db.close()

    app.dependency_overrides[get_db] = _override_get_db(test_db)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/api/auth/login", data={"username": USERNAME, "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# --- auth required (every route) --------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", "/api/whatsapp/link-code"), ("GET", "/api/whatsapp/link"), ("DELETE", "/api/whatsapp/link")],
)
def test_routes_reject_request_with_no_token(auth_client, method, path):
    assert auth_client.request(method, path).status_code == 401


def test_link_code_rejects_invalid_token(auth_client):
    response = auth_client.post(
        "/api/whatsapp/link-code", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401


def test_link_code_accepts_request_with_valid_token(auth_client):
    response = auth_client.post("/api/whatsapp/link-code", headers=_auth_headers(auth_client))
    assert response.status_code == 201


# --- generating a code ------------------------------------------------------------------


def test_link_code_returns_a_code_stored_against_the_caller(client, test_db):
    response = client.post("/api/whatsapp/link-code")

    assert response.status_code == 201
    body = response.json()
    assert len(body["code"]) == LINK_CODE_LENGTH
    assert body["expires_in_seconds"] > 0

    session = test_db()
    try:
        assert session.get(User, client.user_id).whatsapp_link_code == body["code"]
    finally:
        session.close()


def test_link_code_can_be_consumed_by_the_webhook_path(client, test_db):
    code = client.post("/api/whatsapp/link-code").json()["code"]

    session = test_db()
    try:
        # What the webhook (file 18 prompt 2) will do with an inbound message.
        linked = WhatsAppLinkService(session).consume_link_code(f"hi {code}", PHONE)
        assert linked is not None and linked.id == client.user_id
    finally:
        session.close()


# --- status / unlink --------------------------------------------------------------------


def test_link_status_reports_unlinked_before_any_pairing(client):
    body = client.get("/api/whatsapp/link").json()

    assert body == {
        # False in the test environment: no WHATSAPP_* env vars are set, which is also
        # the state a fresh checkout is in before the Meta setup in docs/deployment.md.
        "configured": False,
        "linked": False,
        "phone_number": None,
        "code_pending": False,
        "code_expires_at": None,
    }


def test_link_status_reports_backend_configuration_separately_from_linking(
    client, monkeypatch
):
    """`configured` tracks the backend's Meta credentials, not this user's pairing.

    The settings panel needs the two apart: with no credentials a pairing code can
    never be delivered, so it points at the setup docs instead of offering the button.
    """
    monkeypatch.setattr(whatsapp_routes, "is_configured", lambda: True)

    body = client.get("/api/whatsapp/link").json()

    assert body["configured"] is True
    assert body["linked"] is False


def test_link_status_reports_a_pending_code_without_revealing_it(client):
    code = client.post("/api/whatsapp/link-code").json()["code"]

    body = client.get("/api/whatsapp/link").json()

    assert body["code_pending"] is True
    assert body["linked"] is False
    assert code not in str(body)


def test_link_status_reports_the_linked_number_after_pairing(client, test_db):
    code = client.post("/api/whatsapp/link-code").json()["code"]
    session = test_db()
    try:
        WhatsAppLinkService(session).consume_link_code(code, PHONE)
    finally:
        session.close()

    body = client.get("/api/whatsapp/link").json()

    assert body["linked"] is True
    assert body["phone_number"] == PHONE
    assert body["code_pending"] is False


def test_unlink_clears_the_link_then_404s(client, test_db):
    code = client.post("/api/whatsapp/link-code").json()["code"]
    session = test_db()
    try:
        WhatsAppLinkService(session).consume_link_code(code, PHONE)
    finally:
        session.close()

    assert client.delete("/api/whatsapp/link").status_code == 204
    assert client.get("/api/whatsapp/link").json()["linked"] is False
    assert client.delete("/api/whatsapp/link").status_code == 404
