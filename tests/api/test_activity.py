"""`GET /api/activity` route tests -- merges `tool_execution_logs` + `llm_usage` rows
into one timestamp-sorted feed (see app.api.routes.activity).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user
from app.database.database import get_db
from app.database.models import LLMUsage, ToolExecutionLog
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


def _add_tool_log(session, *, tool_name="open_app", status="ok", scope="default", when=None):
    row = ToolExecutionLog(
        tool_name=tool_name,
        params="{}",
        result=f'{{"success": {"true" if status == "ok" else "false"}, "platform": "desktop", "scope": "{scope}", "error": null}}',
        status=status,
    )
    session.add(row)
    session.flush()
    if when is not None:
        row.executed_at = when
    session.commit()
    return row


def _add_llm_usage(session, *, provider="gemini", model="gemini-2.0-flash", status="SUCCESS", when=None):
    row = LLMUsage(
        provider=provider,
        model=model,
        status=status,
        request_tokens=10,
        response_tokens=20,
    )
    session.add(row)
    session.flush()
    if when is not None:
        row.timestamp = when
    session.commit()
    return row


def test_requires_auth():
    client = TestClient(app)
    response = client.get("/api/activity")
    assert response.status_code == 401


def test_merges_tool_and_llm_rows_sorted_by_recency(client, test_db):
    session = test_db()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _add_tool_log(session, tool_name="open_app", when=now - timedelta(minutes=2))
    _add_llm_usage(session, when=now - timedelta(minutes=1))
    _add_tool_log(session, tool_name="create_task", when=now)

    response = client.get("/api/activity")
    assert response.status_code == 200
    items = response.json()["items"]

    assert [item["summary"] for item in items] == [
        "create_task",
        "gemini · gemini-2.0-flash",
        "open_app",
    ]
    assert [item["type"] for item in items] == ["tool_call", "llm_call", "tool_call"]


def test_routine_scoped_tool_call_gets_a_scope_label(client, test_db):
    session = test_db()
    _add_tool_log(session, tool_name="lock_screen", scope="routine")

    response = client.get("/api/activity")
    item = response.json()["items"][0]

    assert item["tool_name"] == "lock_screen"
    assert item["scope_label"] == "Routine (manual run)"


def test_direct_tool_call_has_no_scope_label(client, test_db):
    session = test_db()
    _add_tool_log(session, tool_name="open_app", scope="default")

    response = client.get("/api/activity")
    item = response.json()["items"][0]

    assert item["scope_label"] is None


def test_failed_llm_call_reports_error_status(client, test_db):
    session = test_db()
    _add_llm_usage(session, status="QUOTA_EXHAUSTED")

    response = client.get("/api/activity")
    item = response.json()["items"][0]

    assert item["status"] == "error"
    assert item["provider"] == "gemini"


def test_limit_caps_returned_items(client, test_db):
    session = test_db()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for i in range(5):
        _add_tool_log(session, tool_name=f"tool_{i}", when=now - timedelta(minutes=i))

    response = client.get("/api/activity?limit=2")
    assert len(response.json()["items"]) == 2
