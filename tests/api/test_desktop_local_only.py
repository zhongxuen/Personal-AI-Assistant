"""
Local-only boundary tests for platform="desktop" requests (§23, file 11 prompt 3).

Two layers of coverage:
  - Unit tests directly against `enforce_desktop_local_only()` -- proves the policy
    itself (desktop + non-loopback -> reject; desktop + loopback -> allow; any other
    platform -> always allow) without needing the app or a DB.
  - Integration tests against the real routes through FastAPI's TestClient -- proves
    the check is actually wired into `POST /api/assistant/message` and
    `POST /api/voice/message`, not just implemented and never called. Starlette's
    TestClient reports a fixed synthetic client host ("testclient", not real
    loopback -- see `app.api.local_only`'s docstring), so hitting these routes with
    platform="desktop" and no override is exactly the "request claims desktop from
    somewhere that isn't this machine" case this boundary exists to reject; monkeypatching
    `LOCAL_CLIENT_HOSTS` to include "testclient" simulates the allowed case.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.dependencies import get_optional_current_user
from app.api.local_only import LOCAL_CLIENT_HOSTS, enforce_desktop_local_only
from app.core.models import AssistantResponse
from app.database.database import get_db
from main import app


def _request_from(host: str | None):
    request = Mock()
    request.client = Mock(host=host) if host is not None else None
    return request


def test_non_desktop_platform_never_rejected_regardless_of_host():
    for platform in ["web", "discord", "some-future-platform"]:
        enforce_desktop_local_only(_request_from("203.0.113.5"), platform)  # must not raise


def test_desktop_platform_from_loopback_is_allowed():
    for host in ["127.0.0.1", "::1", "localhost"]:
        enforce_desktop_local_only(_request_from(host), "desktop")  # must not raise


def test_desktop_platform_from_non_loopback_is_rejected():
    with pytest.raises(HTTPException) as exc_info:
        enforce_desktop_local_only(_request_from("203.0.113.5"), "desktop")

    assert exc_info.value.status_code == 403
    assert "localhost" in exc_info.value.detail.lower()


def test_desktop_platform_with_no_client_info_is_rejected():
    """A request with no discoverable client (request.client is None) must fail
    closed, never be treated as trusted by default.
    """
    with pytest.raises(HTTPException) as exc_info:
        enforce_desktop_local_only(_request_from(None), "desktop")

    assert exc_info.value.status_code == 403


@pytest.fixture()
def client(test_db, monkeypatch):
    def override_get_db():
        db = test_db()
        try:
            yield db
        finally:
            db.close()

    # Stub out AssistantCore entirely -- these tests only care whether the
    # local-only boundary (checked before AssistantCore is ever constructed) let the
    # request through, not what AssistantCore does with it. Without this, an
    # unrecognized message with an empty process-wide tool registry would fall
    # through to real classification/AIRouter, which is exactly what
    # tests/core/test_zero_llm.py's `no_network` fixture exists to prevent -- simplest
    # to just not reach that code path here at all.
    #
    # It must be `handle_async` that's patched, since that's what the route awaits
    # (app/api/routes/assistant.py); patching the sync `handle` intercepts nothing and
    # lets exactly the real network calls described above happen anyway.
    async def _stub_handle_async(self, request):
        return AssistantResponse(text="stub response")

    monkeypatch.setattr(
        "app.api.routes.assistant.AssistantCore.handle_async", _stub_handle_async
    )

    # §34, file 12 prompt 1: platform="web" now also requires authentication --
    # stubbed here to a fixed user so these tests keep exercising only the local-only
    # boundary, not the (separately-tested) auth boundary. Needs a real `.username`
    # attribute, unlike the bare `object()` stub other route tests use, since
    # `post_message` reads `current_user.username` for a non-desktop request.
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_optional_current_user] = lambda: SimpleNamespace(
        id=1, username="stub-user"
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_assistant_message_rejects_desktop_platform_from_non_local_client(client):
    response = client.post(
        "/api/assistant/message",
        json={"user_id": "u1", "platform": "desktop", "message": "hello"},
    )

    assert response.status_code == 403
    assert "localhost" in response.json()["detail"].lower()


def test_assistant_message_does_not_block_non_desktop_platforms(client):
    response = client.post(
        "/api/assistant/message",
        json={"user_id": "u1", "platform": "web", "message": "hello"},
    )

    # Not blocked by the local-only boundary -- AssistantCore.handle is stubbed above
    # and auth is stubbed to a fixed user above, so a 200 here confirms only that
    # platform="web" was never subject to the *local-only* check.
    assert response.status_code == 200


def test_voice_message_rejects_desktop_platform_from_non_local_client(client):
    # Voice always runs as platform="desktop" (DesktopAdapter) -- the boundary applies
    # unconditionally, before any STT work, regardless of what's in the form body.
    response = client.post("/api/voice/message", data={"text": "hello"})

    assert response.status_code == 403
    assert "localhost" in response.json()["detail"].lower()


def test_assistant_message_allows_desktop_platform_from_trusted_host(client, monkeypatch):
    monkeypatch.setattr("app.api.local_only.LOCAL_CLIENT_HOSTS", LOCAL_CLIENT_HOSTS | {"testclient"})

    response = client.post(
        "/api/assistant/message",
        json={"user_id": "u1", "platform": "desktop", "message": "hello"},
    )

    assert response.status_code == 200
