"""
Memory settings route tests (§37 Phase 8 / file 09 prompt 3).

Exercises `/api/memory/applications` and `/api/memory/default-project` through
FastAPI's `TestClient`, same `get_db` override pattern as tests/api/test_routines.py.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.database.database import get_db
from app.memory.service import DEFAULT_CODING_CATEGORY, DEFAULT_CODING_KEY, MemoryService
from main import app


@pytest.fixture()
def client(test_db):
    def override_get_db():
        db = test_db()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


# --- applications --------------------------------------------------------------------


def test_list_applications_starts_empty(client):
    assert client.get("/api/memory/applications").json() == {}


def test_put_then_list_application_mapping(client):
    response = client.put(
        "/api/memory/applications/vscode",
        json={"command": ["code"], "process_names": ["Code.exe"]},
    )
    assert response.status_code == 200
    assert response.json() == {"command": ["code"], "process_names": ["Code.exe"]}

    assert client.get("/api/memory/applications").json() == {
        "vscode": {"command": ["code"], "process_names": ["Code.exe"]}
    }


def test_put_application_mapping_lowercases_alias(client):
    client.put("/api/memory/applications/VS%20Code", json={"command": ["code"]})

    assert client.get("/api/memory/applications").json() == {
        "vs code": {"command": ["code"], "process_names": []}
    }


def test_put_application_mapping_overwrites_in_place(client):
    client.put("/api/memory/applications/vscode", json={"command": ["code"]})
    client.put("/api/memory/applications/vscode", json={"command": ["code-insiders"]})

    assert client.get("/api/memory/applications").json() == {
        "vscode": {"command": ["code-insiders"], "process_names": []}
    }


def test_put_application_mapping_empty_command_rejected(client):
    response = client.put("/api/memory/applications/vscode", json={"command": []})
    assert response.status_code == 422


def test_delete_application_mapping(client):
    client.put("/api/memory/applications/vscode", json={"command": ["code"]})

    response = client.delete("/api/memory/applications/vscode")
    assert response.status_code == 204
    assert client.get("/api/memory/applications").json() == {}


def test_delete_unknown_application_mapping_404(client):
    assert client.delete("/api/memory/applications/nope").status_code == 404


# --- default project -------------------------------------------------------------------


def test_get_default_project_falls_back_to_seed_value_when_unset(client):
    response = client.get("/api/memory/default-project")
    assert response.status_code == 200
    assert response.json() == {"default_project": "portfolio"}


def test_put_default_project_updates_it(client):
    response = client.put("/api/memory/default-project", json={"default_project": "jarvis"})
    assert response.status_code == 200
    assert response.json() == {"default_project": "jarvis"}

    assert client.get("/api/memory/default-project").json() == {"default_project": "jarvis"}


def test_put_default_project_preserves_editor_and_browser(client, test_db):
    db = test_db()
    MemoryService(db).set(
        DEFAULT_CODING_CATEGORY,
        DEFAULT_CODING_KEY,
        {"editor": "Neovim", "browser": "Firefox", "default_project": "portfolio"},
    )
    db.close()

    client.put("/api/memory/default-project", json={"default_project": "jarvis"})

    db = test_db()
    coding = MemoryService(db).get(DEFAULT_CODING_CATEGORY, DEFAULT_CODING_KEY)
    db.close()
    assert coding == {"editor": "Neovim", "browser": "Firefox", "default_project": "jarvis"}


def test_put_default_project_blank_rejected(client):
    response = client.put("/api/memory/default-project", json={"default_project": "  "})
    assert response.status_code == 422
