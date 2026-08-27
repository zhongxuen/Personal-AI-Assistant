"""
Routine route tests (Routine Dashboard backend).

Exercises the `/api/routines` and `/api/tools` routes through FastAPI's TestClient.
`get_db` is overridden to the same throwaway in-memory SQLite database the `test_db`
fixture wires into `RoutineEngine` (tests/conftest.py), so `POST /routines/{name}/run`
-- which runs through `RoutineEngine`'s own session, not the request's -- sees the same
routines these tests create via the CRUD routes. `get_tool_registry` is overridden to a
registry of stub tools (same pattern as tests/routines/test_engine.py) so tests don't
depend on any real tool's side effects. `get_current_user` is overridden to a fixed
stub user (§34, file 12 prompt 1) -- these routes now require authentication, covered
separately by tests/api/test_auth.py.

`client` also monkeypatches `LOCAL_CLIENT_HOSTS` to accept the TestClient's synthetic
"testclient" host (same trick tests/api/test_auth.py uses) so `run_routine`'s
`is_local_client` check (file 12 prompt 2) treats these tests as a same-machine caller
by default -- most of these tests are about routine CRUD/run mechanics, not platform
capability, and the stub tools below declare `platforms=["desktop"]`, so without this
every `POST /routines/{name}/run` call would now correctly, but irrelevantly to what
these tests check, be rejected as a "remote" caller.
`test_run_routine_desktop_only_step_rejected_for_non_local_caller` below is the one
test that deliberately does *not* get that treatment, to prove the opposite case.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user, get_tool_registry
from app.api.local_only import LOCAL_CLIENT_HOSTS
from app.core.permissions import PermissionLevel
from app.database.database import get_db
from app.tools.base import ToolResult
from app.tools.registry import ToolRegistry
from main import app


class _StubUser:
    """Minimal stand-in for `app.database.models.User` -- `run_routine` (file 12
    prompt 2) reads `.username` off whatever `get_current_user` resolves to, so the
    override below needs at least that attribute, unlike the bare `object()` this
    stubbed out before that route read anything off it.
    """

    username = "stub-user"


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
def client(test_db, monkeypatch):
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

    # See module docstring -- treats the TestClient as a same-machine caller so
    # run_routine's inferred platform is "desktop" (matching the stub tools'
    # platforms=["desktop"]) by default.
    monkeypatch.setattr("app.api.local_only.LOCAL_CLIENT_HOSTS", LOCAL_CLIENT_HOSTS | {"testclient"})

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_tool_registry] = lambda: registry
    # §34, file 12 prompt 1: this router now requires authentication -- stub it out
    # here since these tests exercise the routine CRUD/run contract, not auth itself
    # (tests/api/test_auth.py covers that).
    app.dependency_overrides[get_current_user] = lambda: _StubUser()
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


def test_set_enabled_stops_and_starts_routine(client):
    """The "Stop"/"Start" toggle: `PATCH /routines/{name}` flips `enabled` without
    touching steps, and a disabled routine is refused by `/run` (RoutineEngine.run()'s
    own `if not routine.enabled` check) rather than silently running anyway.
    """
    client.post(
        "/api/routines",
        json={"name": "demo", "steps": [{"tool_name": "stub_first", "params": {"a": 1}}]},
    )

    response = client.patch("/api/routines/demo", json={"enabled": False})
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["steps"] == [{"tool_name": "stub_first", "params": {"a": 1}}]

    run_response = client.post("/api/routines/demo/run")
    assert run_response.status_code == 200
    run_body = run_response.json()
    assert run_body["success"] is False
    assert "disabled" in run_body["error"]

    response = client.patch("/api/routines/demo", json={"enabled": True})
    assert response.status_code == 200
    assert response.json()["enabled"] is True

    run_response = client.post("/api/routines/demo/run")
    assert run_response.json()["success"] is True


def test_set_enabled_unknown_routine_404(client):
    assert client.patch("/api/routines/nope", json={"enabled": False}).status_code == 404


def test_rename_routine(client):
    client.post(
        "/api/routines",
        json={"name": "demo", "steps": [{"tool_name": "stub_first", "params": {"a": 1}}]},
    )

    response = client.patch("/api/routines/demo", json={"name": "demo-renamed"})
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "demo-renamed"
    assert body["steps"] == [{"tool_name": "stub_first", "params": {"a": 1}}]

    assert client.get("/api/routines/demo").status_code == 404
    assert client.get("/api/routines/demo-renamed").status_code == 200


def test_rename_and_set_enabled_together(client):
    client.post("/api/routines", json={"name": "demo", "steps": []})

    response = client.patch("/api/routines/demo", json={"name": "demo-renamed", "enabled": False})
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "demo-renamed"
    assert body["enabled"] is False


def test_rename_routine_duplicate_name_rejected(client):
    client.post("/api/routines", json={"name": "demo", "steps": []})
    client.post("/api/routines", json={"name": "taken", "steps": []})

    response = client.patch("/api/routines/demo", json={"name": "taken"})
    assert response.status_code == 422


def test_rename_routine_blank_name_rejected(client):
    client.post("/api/routines", json={"name": "demo", "steps": []})

    response = client.patch("/api/routines/demo", json={"name": "  "})
    assert response.status_code == 422


def test_rename_unknown_routine_404(client):
    assert client.patch("/api/routines/nope", json={"name": "still-nope"}).status_code == 404


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


def test_run_routine_desktop_only_step_rejected_for_non_local_caller(client, monkeypatch):
    """file 12 prompt 2: `run_routine` infers `platform="web"` for a caller that
    doesn't look local, rather than `RoutineEngine.run()`'s old unconditional
    `platform="desktop"` default -- undoes the `client` fixture's own
    `LOCAL_CLIENT_HOSTS` patch so the TestClient's "testclient" host goes back to being
    treated as non-local, then asserts the desktop-only stub step (`stub_first`,
    `platforms=["desktop"]`) is rejected with the §22-style explanation instead of
    running.
    """
    monkeypatch.setattr("app.api.local_only.LOCAL_CLIENT_HOSTS", LOCAL_CLIENT_HOSTS)

    client.post(
        "/api/routines",
        json={"name": "demo", "steps": [{"tool_name": "stub_first", "params": {"a": 1}}]},
    )

    response = client.post("/api/routines/demo/run")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert "web" in body["error"].lower()
