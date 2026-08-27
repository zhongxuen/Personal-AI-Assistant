"""
Discord bot control route tests (`GET/POST /api/discord/*`, web dashboard follow-up
to file 13).

Exercises the routes through FastAPI's TestClient against a fake `DiscordBotManager`
(overridden via `get_discord_bot_manager`, same "override the FastAPI dependency, no
real SDK object involved" approach tests/api/test_routines.py takes with
`get_tool_registry`) -- `tests/platforms/test_discord_manager.py` already covers the
real manager's start/stop/status logic in isolation, so these tests only need to prove
the routes call through to it correctly, shape the response right, and are gated on
`get_current_user` like every other route in this file's module.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user
from app.platforms.discord import get_discord_bot_manager
from main import app


class _StubUser:
    username = "stub-user"


class _FakeManager:
    """Records start()/stop() calls and returns a scripted status() -- no real
    discord.py Client involved, matching this file's docstring.
    """

    def __init__(self, status: dict) -> None:
        self._status = status
        self.start_calls = 0
        self.stop_calls = 0

    def status(self) -> dict:
        return self._status

    async def start(self) -> None:
        self.start_calls += 1
        self._status = {"configured": True, "state": "connected", "username": "TestBot#0001", "error": None}

    async def stop(self) -> None:
        self.stop_calls += 1
        self._status = {"configured": True, "state": "stopped", "username": None, "error": None}


@pytest.fixture()
def fake_manager() -> _FakeManager:
    return _FakeManager({"configured": False, "state": "disabled", "username": None, "error": None})


@pytest.fixture()
def client(fake_manager: _FakeManager):
    app.dependency_overrides[get_current_user] = lambda: _StubUser()
    app.dependency_overrides[get_discord_bot_manager] = lambda: fake_manager
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_get_status_requires_auth():
    # No override for get_current_user here -- a bare, unauthenticated TestClient
    # request must 401, same boundary every other route in app.api.routes checks.
    response = TestClient(app).get("/api/discord/status")
    assert response.status_code == 401


def test_get_status_returns_manager_snapshot(client: TestClient, fake_manager: _FakeManager):
    response = client.get("/api/discord/status")
    assert response.status_code == 200
    assert response.json() == {
        "configured": False,
        "state": "disabled",
        "username": None,
        "error": None,
    }


def test_start_calls_manager_and_returns_new_status(client: TestClient, fake_manager: _FakeManager):
    response = client.post("/api/discord/start")
    assert response.status_code == 200
    assert fake_manager.start_calls == 1
    assert response.json()["state"] == "connected"
    assert response.json()["username"] == "TestBot#0001"


def test_stop_calls_manager_and_returns_new_status(client: TestClient, fake_manager: _FakeManager):
    response = client.post("/api/discord/stop")
    assert response.status_code == 200
    assert fake_manager.stop_calls == 1
    assert response.json()["state"] == "stopped"
