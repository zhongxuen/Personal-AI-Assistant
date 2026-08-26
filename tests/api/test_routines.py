"""
Routine route tests (Routine Dashboard backend).

Exercises the `/api/routines` and `/api/tools` routes through FastAPI's TestClient.
`get_db` is overridden to the same throwaway in-memory SQLite database the `test_db`
fixture wires into `RoutineEngine` (tests/conftest.py), so `POST /routines/{name}/run`
-- which runs through `RoutineEngine`'s own session, not the request's -- sees the same
routines these tests create via the CRUD routes. `get_tool_registry` is overridden to a
registry of stub tools (same pattern as tests/routines/test_engine.py) so tests don't
depend on any real tool's side effects.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_tool_registry
from app.core.permissions import PermissionLevel
from app.database.database import get_db
from app.tools.base import ToolResult
from app.tools.registry import ToolRegistry
from main import app


class _StubTool:
    """A minimal SAFE tool with a Mock handler -- see tests/routines/test_engine.py."""

    def __init__(self, name: str, handler: Mock) -> None:
        self.name = name
        self.description = "stub"
        self.parameters: dict = {"type": "object", "properties": {}, "required": []}
        self.permission = PermissionLevel.SAFE
        self.platforms = ["desktop"]
        self.requires_confirmation = False
        self.handler = handler


@pytest.fixture()
def client(test_db):
    def override_get_db():
        db = test_db()
        try:
            yield db
        finally:
            db.close()

    registry = ToolRegistry()
    registry.register(
        _StubTool("stub_first", Mock(return_value=ToolResult(success=True, data={"step": "first"})))
    )
    registry.register(
        _StubTool("stub_second", Mock(return_value=ToolResult(success=True, data={"step": "second"})))
    )

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_tool_registry] = lambda: registry
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_list_tools(client):
    response = client.get("/api/tools")
    assert response.status_code == 200
    assert {t["name"] for t in response.json()} == {"stub_first", "stub_second"}


def test_create_list_get_routine(client):
    response = client.post(
        "/api/routines",
        json={"name": "demo", "steps": [{"tool_name": "stub_first", "params": {"a": 1}}]},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "demo"
    assert body["enabled"] is True
    assert body["steps"] == [{"tool_name": "stub_first", "params": {"a": 1}}]

    response = client.get("/api/routines")
    assert [r["name"] for r in response.json()] == ["demo"]

    response = client.get("/api/routines/demo")
    assert response.status_code == 200
    assert response.json()["name"] == "demo"


def test_create_routine_unknown_tool_rejected(client):
    response = client.post("/api/routines", json={"name": "bad", "steps": [{"tool_name": "nope"}]})
    assert response.status_code == 422
    assert "nope" in response.json()["detail"]


def test_create_duplicate_name_rejected(client):
    client.post("/api/routines", json={"name": "demo", "steps": []})
    response = client.post("/api/routines", json={"name": "demo", "steps": []})
    assert response.status_code == 422


def test_get_unknown_routine_404(client):
    assert client.get("/api/routines/nope").status_code == 404


def test_update_steps_reorders_and_replaces(client):
    client.post("/api/routines", json={"name": "demo", "steps": [{"tool_name": "stub_first"}]})

    response = client.put(
        "/api/routines/demo/steps",
        json={"steps": [{"tool_name": "stub_second"}, {"tool_name": "stub_first"}]},
    )
    assert response.status_code == 200
    assert [s["tool_name"] for s in response.json()["steps"]] == ["stub_second", "stub_first"]


def test_update_steps_unknown_routine_404(client):
    assert client.put("/api/routines/nope/steps", json={"steps": []}).status_code == 404


def test_update_steps_unknown_tool_rejected(client):
    client.post("/api/routines", json={"name": "demo", "steps": []})
    response = client.put("/api/routines/demo/steps", json={"steps": [{"tool_name": "nope"}]})
    assert response.status_code == 422


def test_delete_routine(client):
    client.post("/api/routines", json={"name": "demo", "steps": []})
    assert client.delete("/api/routines/demo").status_code == 204
    assert client.get("/api/routines/demo").status_code == 404


def test_delete_unknown_routine_404(client):
    assert client.delete("/api/routines/nope").status_code == 404


def test_run_routine(client):
    client.post(
        "/api/routines",
        json={"name": "demo", "steps": [{"tool_name": "stub_first", "params": {"a": 1}}]},
    )

    response = client.post("/api/routines/demo/run")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["steps"][0]["tool_name"] == "stub_first"


def test_run_unknown_routine(client):
    response = client.post("/api/routines/nope/run")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert "nope" in body["error"]
