"""
TaskService tests (§37 Phase 3 / file 04 prompt 1).

Exercises `TaskService` directly against a throwaway in-memory SQLite session (not
through the tool layer, unlike tests/tools/test_deterministic_tools.py) -- natural-
language due-date parsing for several phrasings, categories, priority validation,
filtering (status/category/due-range/overdue), overdue detection, and edit/delete,
including that editing/deleting keeps `task_reminders` in sync.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import models  # noqa: F401 -- registers tables on Base.metadata
from app.database.database import Base
from app.database.models import TaskReminder
from app.tasks.service import TaskService, parse_due, split_title_and_due


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = session_factory()
    yield session
    session.close()
    engine.dispose()


BASE = datetime(2026, 8, 25, 10, 0, 0)  # a Tuesday


# --- parse_due / split_title_and_due (pure functions, no DB) --------------------------


def test_parse_due_tomorrow_at_8pm():
    assert parse_due("tomorrow at 8pm", base=BASE) == datetime(2026, 8, 26, 20, 0, 0)


def test_parse_due_next_friday():
    assert parse_due("next friday", base=BASE) == datetime(2026, 8, 28, 0, 0, 0)


def test_parse_due_in_2_hours():
    assert parse_due("in 2 hours", base=BASE) == datetime(2026, 8, 25, 12, 0, 0)


def test_parse_due_iso_string():
    assert parse_due("2026-09-01T09:30:00", base=BASE) == datetime(2026, 9, 1, 9, 30, 0)


def test_parse_due_garbage_or_empty_returns_none():
    assert parse_due("not a date at all", base=BASE) is None
    assert parse_due("", base=BASE) is None
    assert parse_due(None, base=BASE) is None


def test_split_title_and_due_strips_embedded_phrase():
    title, due = split_title_and_due("submit my assignment tomorrow at 8pm", base=BASE)
    assert title == "submit my assignment"
    assert due == datetime(2026, 8, 26, 20, 0, 0)


def test_split_title_and_due_no_phrase_leaves_text_unchanged():
    title, due = split_title_and_due("buy milk", base=BASE)
    assert title == "buy milk"
    assert due is None


# --- TaskService.create ----------------------------------------------------------------


def test_create_parses_natural_language_due(db):
    task = TaskService(db).create(title="Submit assignment", due="tomorrow at 8pm")
    assert task.due_at is not None
    assert task.status == "pending"
    assert task.priority == "medium"  # default


def test_create_with_category_and_priority(db):
    task = TaskService(db).create(title="Renew passport", category="admin", priority="high")
    assert task.category == "admin"
    assert task.priority == "high"


def test_create_invalid_priority_raises(db):
    with pytest.raises(ValueError):
        TaskService(db).create(title="Bad priority", priority="urgent")


def test_create_with_due_date_seeds_a_reminder(db):
    service = TaskService(db)
    task = service.create(title="Call dentist", due="in 2 hours")

    reminders = db.query(TaskReminder).filter(TaskReminder.task_id == task.id).all()
    assert len(reminders) == 1
    assert reminders[0].remind_at == task.due_at
    assert reminders[0].sent is False


def test_create_without_due_date_seeds_no_reminder(db):
    task = TaskService(db).create(title="No due date")

    assert db.query(TaskReminder).filter(TaskReminder.task_id == task.id).all() == []


# --- TaskService.list filters ------------------------------------------------------------


def test_list_filters_by_status_and_category(db):
    service = TaskService(db)
    service.create(title="A", category="work")
    b = service.create(title="B", category="home")
    service.complete(b.id)

    assert [t.title for t in service.list(status="pending")] == ["A"]
    assert [t.title for t in service.list(category="home")] == ["B"]


def test_list_filters_by_due_range(db):
    service = TaskService(db)
    service.create(title="Near", due=BASE + timedelta(hours=1))
    service.create(title="Far", due=BASE + timedelta(days=10))
    service.create(title="No due date")

    in_range = service.list(due_before=(BASE + timedelta(days=1)).isoformat())
    assert [t.title for t in in_range] == ["Near"]

    later = service.list(due_after=(BASE + timedelta(days=5)).isoformat())
    assert [t.title for t in later] == ["Far"]


def test_list_overdue_only(db):
    service = TaskService(db)
    overdue = service.create(title="Overdue", due=datetime.now() - timedelta(days=1))
    service.create(title="Future", due=datetime.now() + timedelta(days=1))
    service.create(title="No due date")

    assert [t.id for t in service.list(overdue_only=True)] == [overdue.id]


def test_is_overdue(db):
    service = TaskService(db)
    overdue = service.create(title="Overdue", due=datetime.now() - timedelta(days=1))
    future = service.create(title="Future", due=datetime.now() + timedelta(days=1))
    completed_but_late = service.create(title="Late but done", due=datetime.now() - timedelta(days=1))
    service.complete(completed_but_late.id)

    assert service.is_overdue(overdue) is True
    assert service.is_overdue(future) is False
    assert service.is_overdue(completed_but_late) is False  # completed tasks are never "overdue"


# --- TaskService.edit / delete -----------------------------------------------------------


def test_edit_updates_only_supplied_fields(db):
    service = TaskService(db)
    task = service.create(title="Original", category="work", priority="low")

    edited = service.edit(task.id, title="Renamed")

    assert edited.title == "Renamed"
    assert edited.category == "work"  # untouched
    assert edited.priority == "low"  # untouched


def test_edit_can_clear_due_date_and_category(db):
    service = TaskService(db)
    task = service.create(title="Task", due="tomorrow at 8pm", category="work")

    edited = service.edit(task.id, due="", category="")

    assert edited.due_at is None
    assert edited.category is None
    # Clearing the due date removes the now-stale reminder.
    assert db.query(TaskReminder).filter(TaskReminder.task_id == task.id).all() == []


def test_edit_reparses_natural_language_due(db):
    service = TaskService(db)
    task = service.create(title="Task")

    edited = service.edit(task.id, due="in 2 hours")

    assert edited.due_at is not None


def test_edit_unknown_task_returns_none(db):
    assert TaskService(db).edit(999, title="x") is None


def test_edit_invalid_priority_raises(db):
    service = TaskService(db)
    task = service.create(title="Task")
    with pytest.raises(ValueError):
        service.edit(task.id, priority="urgent")


def test_delete_removes_task_and_its_reminders(db):
    service = TaskService(db)
    task = service.create(title="Task", due="in 2 hours")

    assert service.delete(task.id) is True
    assert service.get(task.id) is None
    assert db.query(TaskReminder).filter(TaskReminder.task_id == task.id).all() == []


def test_delete_unknown_task_returns_false(db):
    assert TaskService(db).delete(999) is False
