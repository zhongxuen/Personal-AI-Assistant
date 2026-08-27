"""
Diagnostics route tests (`GET/POST /api/diagnostics/*`, the Status tab's "Run system
test" button).

Exercises the routes through FastAPI's TestClient with every `DiagnosticsService`
dependency overridden to a fake/stub (same "override the FastAPI dependency, no real
provider/manager involved" approach `tests/api/test_discord.py` and
`tests/api/test_routines.py` take) -- these tests only need to prove the routes call
through to `DiagnosticsService` correctly, validate `checks` names, and are gated on
`get_current_user`, not that any individual check's underlying logic is correct (that's
`tests/diagnostics/test_service.py`'s job, if/when one exists).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_current_user,
    get_health_manager,
    get_stt_provider,
    get_tool_registry,
    get_tts_provider,
)
from app.llm.health import HealthManager
from app.platforms.discord import get_discord_bot_manager
from app.tools.registry import ToolRegistry
from main import app


class _StubUser:
    username = "stub-user"


class _FakeDiscordManager:
    def status(self) -> dict:
        return {"configured": False, "state": "disabled", "username": None, "error": None}


class _FakeVoiceProvider:
    def __init__(self, available: bool) -> None:
        self._available = available

    def is_available(self) -> bool:
        return self._available


@pytest.fixture()
def client():
    app.dependency_overrides[get_current_user] = lambda: _StubUser()
    app.dependency_overrides[get_tool_registry] = lambda: ToolRegistry()
    app.dependency_overrides[get_discord_bot_manager] = lambda: _FakeDiscordManager()
    app.dependency_overrides[get_stt_provider] = lambda: _FakeVoiceProvider(True)
    app.dependency_overrides[get_tts_provider] = lambda: _FakeVoiceProvider(False)
    app.dependency_overrides[get_health_manager] = lambda: HealthManager()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_list_checks_requires_auth():
    response = TestClient(app).get("/api/diagnostics/checks")
    assert response.status_code == 401


def test_list_checks_returns_catalog(client: TestClient):
    response = client.get("/api/diagnostics/checks")
    assert response.status_code == 200
    names = {c["name"] for c in response.json()}
    assert "database" in names
    assert "voice_stt" in names
    assert "discord_bot" in names


def test_run_requires_auth():
    response = TestClient(app).post("/api/diagnostics/run", json={})
    assert response.status_code == 401


def test_run_all_returns_one_result_per_check(client: TestClient):
    response = client.post("/api/diagnostics/run", json={})
    assert response.status_code == 200
    body = response.json()
    checks_response = client.get("/api/diagnostics/checks")
    assert len(body["results"]) == len(checks_response.json())


def test_run_reflects_fake_provider_availability(client: TestClient):
    response = client.post("/api/diagnostics/run", json={"checks": ["voice_stt", "voice_tts"]})
    assert response.status_code == 200
    results = {r["name"]: r for r in response.json()["results"]}
    assert results["voice_stt"]["ok"] is True
    assert results["voice_tts"]["ok"] is False
    assert response.json()["ok"] is False  # overall ok is False when any check fails


def test_run_with_unknown_check_name_returns_422(client: TestClient):
    response = client.post("/api/diagnostics/run", json={"checks": ["not_a_real_check"]})
    assert response.status_code == 422


def test_run_can_be_narrowed_to_one_check(client: TestClient):
    response = client.post("/api/diagnostics/run", json={"checks": ["database"]})
    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["name"] == "database"
