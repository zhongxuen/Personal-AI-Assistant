"""
Task route tests (Task Dashboard backend).

Exercises the `/api/tasks` routes through FastAPI's TestClient, with `get_db`
overridden to the same throwaway in-memory SQLite database the `test_db` fixture wires
into the tool layer (tests/conftest.py). Verifies the HTTP contract -- status codes,
JSON shape, filtering, partial-update semantics -- sits correctly on top of
`TaskService` without re-testing `TaskService` itself (already covered by
tests/tasks/test_task_service.py).

`get_current_user` is overridden to a fixed stub user (§34, file 12 prompt 1) -- these
routes now require authentication (tests/api/test_auth.py covers that requirement
itself), and re-authenticating in every test here would just be noise unrelated to
what this file actually exercises.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user
from app.database.database import get_db
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
    app.dependency_overrides[get_current_user] = lambda: object()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_create_and_get_task(client):
    response = client.post("/api/tasks", json={"title": "Buy milk"})
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Buy milk"
    assert body["status"] == "pending"
    assert body["priority"] == "medium"

    response = client.get(f"/api/tasks/{body['id']}")
    assert response.status_code == 200
    assert response.json()["title"] == "Buy milk"


def test_create_blank_title_rejected(client):
    response = client.post("/api/tasks", json={"title": "   "})
    assert response.status_code == 422


def test_create_invalid_priority_rejected(client):
    response = client.post("/api/tasks", json={"title": "x", "priority": "urgent"})
    assert response.status_code == 422


def test_get_unknown_task_404(client):
    assert client.get("/api/tasks/999").status_code == 404


def test_list_filters_by_status_and_category(client):
    client.post("/api/tasks", json={"title": "A", "category": "work"})
    b = client.post("/api/tasks", json={"title": "B", "category": "home"}).json()
    client.post(f"/api/tasks/{b['id']}/complete")

    response = client.get("/api/tasks", params={"status": "pending"})
    assert [t["title"] for t in response.json()] == ["A"]

    response = client.get("/api/tasks", params={"category": "home"})
    assert [t["title"] for t in response.json()] == ["B"]


def test_list_overdue_only(client):
    overdue = client.post("/api/tasks", json={"title": "Late", "due": "2020-01-01T00:00:00"}).json()
    client.post("/api/tasks", json={"title": "Future", "due": "2999-01-01T00:00:00"})

    response = client.get("/api/tasks", params={"overdue_only": True})
    body = response.json()
    assert [t["id"] for t in body] == [overdue["id"]]
    assert body[0]["overdue"] is True


def test_patch_updates_only_supplied_fields(client):
    task = client.post(
        "/api/tasks", json={"title": "Original", "category": "work", "priority": "low"}
    ).json()

    response = client.patch(f"/api/tasks/{task['id']}", json={"title": "Renamed"})
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Renamed"
    assert body["category"] == "work"  # untouched
    assert body["priority"] == "low"  # untouched


def test_patch_can_clear_due_and_category(client):
    task = client.post(
        "/api/tasks", json={"title": "T", "due": "2999-01-01T00:00:00", "category": "work"}
    ).json()

    response = client.patch(f"/api/tasks/{task['id']}", json={"due": None, "category": None})
    assert response.status_code == 200
    body = response.json()
    assert body["due"] is None
    assert body["category"] is None


def test_patch_blank_title_rejected(client):
    task = client.post("/api/tasks", json={"title": "T"}).json()
    response = client.patch(f"/api/tasks/{task['id']}", json={"title": "   "})
    assert response.status_code == 422


def test_patch_null_status_rejected(client):
    task = client.post("/api/tasks", json={"title": "T"}).json()
    response = client.patch(f"/api/tasks/{task['id']}", json={"status": None})
    assert response.status_code == 422


def test_patch_invalid_priority_rejected(client):
    task = client.post("/api/tasks", json={"title": "T"}).json()
    response = client.patch(f"/api/tasks/{task['id']}", json={"priority": "urgent"})
    assert response.status_code == 422


def test_patch_unknown_task_404(client):
    assert client.patch("/api/tasks/999", json={"title": "x"}).status_code == 404


def test_complete_and_delete(client):
    task = client.post("/api/tasks", json={"title": "T"}).json()

    response = client.post(f"/api/tasks/{task['id']}/complete")
    assert response.status_code == 200
    assert response.json()["status"] == "completed"

    response = client.delete(f"/api/tasks/{task['id']}")
    assert response.status_code == 204

    assert client.get(f"/api/tasks/{task['id']}").status_code == 404


def test_complete_unknown_task_404(client):
    assert client.post("/api/tasks/999/complete").status_code == 404


def test_delete_unknown_task_404(client):
    assert client.delete("/api/tasks/999").status_code == 404
