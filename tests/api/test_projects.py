"""
Project discovery route tests (`GET /api/projects`, `GET/PUT /api/projects/roots`).

Same `client` fixture shape as tests/api/test_routines.py: `get_db` overridden to the
throwaway in-memory DB, `get_current_user` stubbed out (auth itself is
tests/api/test_auth.py's job).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user
from app.database.database import get_db
from main import app


class _StubUser:
    username = "stub-user"


@pytest.fixture()
def client(test_db):
    def override_get_db():
        db = test_db()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: _StubUser()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_get_project_roots_defaults(client):
    response = client.get("/api/projects/roots")
    assert response.status_code == 200
    assert response.json()["roots"]  # non-empty defaults, see DEFAULT_PROJECT_ROOTS


def test_put_project_roots_replaces_and_strips(client, tmp_path):
    root = str(tmp_path)
    response = client.put("/api/projects/roots", json={"roots": [f" {root} ", root]})
    assert response.status_code == 200
    assert response.json()["roots"] == [root]

    response = client.get("/api/projects/roots")
    assert response.json()["roots"] == [root]


def test_put_project_roots_rejects_empty_list(client):
    response = client.put("/api/projects/roots", json={"roots": ["   "]})
    assert response.status_code == 422


def test_get_projects_lists_subdirectories_of_configured_roots(client, tmp_path):
    root = tmp_path / "Coding"
    root.mkdir()
    (root / "portfolio").mkdir()
    (root / "warren-mak").mkdir()

    client.put("/api/projects/roots", json={"roots": [str(root)]})

    response = client.get("/api/projects")
    assert response.status_code == 200
    names = {p["name"] for p in response.json()}
    assert names == {"portfolio", "warren-mak"}
    for project in response.json():
        assert project["root"] == str(root)


def test_get_projects_requires_auth():
    app.dependency_overrides.clear()
    client = TestClient(app)
    response = client.get("/api/projects")
    assert response.status_code == 401
