"""
Auth route + protected-route enforcement tests (§34, file 12 prompt 1).

Two things exercised through FastAPI's `TestClient`, both against the real
`app.api.dependencies.get_current_user`/`get_optional_current_user` (no override here,
unlike tests/api/test_tasks.py etc. -- this file is what actually proves those
dependencies work end to end):

  1. `POST /api/auth/login` itself -- correct credentials issue a usable token, wrong
     credentials/unknown username are both rejected the same way (no user
     enumeration), a missing user table row is rejected.
  2. Every "non-desktop-local" route (§34: tasks/routines/memory/llm-usage dashboards,
     and platform="web" requests to /api/assistant/message) rejects an unauthenticated
     or invalid-token request and accepts one carrying a valid token from (1) --
     while `platform="desktop"` requests to the same /api/assistant/message endpoint
     stay ungated by this layer entirely (file 11's separate loopback boundary), which
     `test_desktop_local_only_endpoints_do_not_require_a_token` confirms directly.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.local_only import LOCAL_CLIENT_HOSTS
from app.auth.service import AuthService
from app.core.models import AssistantResponse
from app.database.database import get_db
from main import app

USERNAME = "zhongxuen"
PASSWORD = "correct horse battery staple"


@pytest.fixture()
def client(test_db, monkeypatch):
    def override_get_db():
        db = test_db()
        try:
            yield db
        finally:
            db.close()

    # Seed one real user so /api/auth/login has something to authenticate against.
    seed_db = test_db()
    AuthService(seed_db).create_user(USERNAME, PASSWORD)
    seed_db.close()

    # Allow the TestClient's synthetic "testclient" host through the desktop-local
    # boundary -- irrelevant to auth itself, but needed for the platform="desktop"
    # comparison test below to reach AssistantCore instead of 403ing first.
    monkeypatch.setattr("app.api.local_only.LOCAL_CLIENT_HOSTS", LOCAL_CLIENT_HOSTS | {"testclient"})
    monkeypatch.setattr(
        "app.api.routes.assistant.AssistantCore.handle",
        lambda self, request: AssistantResponse(text="stub response"),
    )

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client: TestClient, username: str = USERNAME, password: str = PASSWORD):
    return client.post("/api/auth/login", data={"username": username, "password": password})


def _auth_headers(client: TestClient) -> dict[str, str]:
    token = _login(client).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# --- POST /api/auth/login -------------------------------------------------------------


def test_login_with_correct_credentials_issues_a_token(client):
    response = _login(client)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["username"] == USERNAME
    assert isinstance(body["access_token"], str) and body["access_token"]


def test_login_with_wrong_password_is_rejected(client):
    response = _login(client, password="wrong password")
    assert response.status_code == 401


def test_login_with_unknown_username_is_rejected_the_same_way_as_wrong_password(client):
    unknown_response = _login(client, username="nobody")
    wrong_password_response = _login(client, password="wrong password")

    assert unknown_response.status_code == wrong_password_response.status_code == 401
    assert unknown_response.json()["detail"] == wrong_password_response.json()["detail"]


# --- protected dashboard routes (representative sample) -------------------------------

PROTECTED_GET_ROUTES = ["/api/tasks", "/api/routines", "/api/tools", "/api/llm/usage", "/api/memory/applications"]


@pytest.mark.parametrize("path", PROTECTED_GET_ROUTES)
def test_protected_route_rejects_request_with_no_token(client, path):
    response = client.get(path)
    assert response.status_code == 401


@pytest.mark.parametrize("path", PROTECTED_GET_ROUTES)
def test_protected_route_rejects_invalid_token(client, path):
    response = client.get(path, headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401


@pytest.mark.parametrize("path", PROTECTED_GET_ROUTES)
def test_protected_route_accepts_request_with_valid_token(client, path):
    response = client.get(path, headers=_auth_headers(client))
    assert response.status_code == 200


# --- POST /api/assistant/message: platform-conditional auth ---------------------------


def test_assistant_message_rejects_web_platform_with_no_token(client):
    response = client.post(
        "/api/assistant/message",
        json={"user_id": "someone-else", "platform": "web", "message": "hello"},
    )
    assert response.status_code == 401


def test_assistant_message_accepts_web_platform_with_valid_token(client):
    response = client.post(
        "/api/assistant/message",
        json={"user_id": "someone-else", "platform": "web", "message": "hello"},
        headers=_auth_headers(client),
    )
    assert response.status_code == 200


def test_desktop_local_only_endpoints_do_not_require_a_token(client):
    """§34's own instruction: the desktop-only local endpoints from file 11 stay on
    their separate loopback boundary, not folded into this auth layer -- a
    platform="desktop" request with no Authorization header at all must still succeed
    (from an allowed loopback-equivalent host, per the fixture's monkeypatch above).
    """
    response = client.post(
        "/api/assistant/message",
        json={"user_id": "local-user", "platform": "desktop", "message": "hello"},
    )
    assert response.status_code == 200
