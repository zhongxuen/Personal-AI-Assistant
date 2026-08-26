"""
Selective memory retrieval tests (§16, §37 Phase 8 / file 09 prompt 3).

Exercises `retrieve_relevant` directly against a throwaway in-memory SQLite session
(same pattern as tests/memory/test_memory_service.py) with every memory category
populated, so a test asserting "routine pulls routines+applications" is actual proof
those categories were selected and everything else was excluded -- not just that the
only populated categories happen to be the right ones.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import models  # noqa: F401 -- registers tables on Base.metadata
from app.database.database import Base
from app.memory.retrieval import retrieve_relevant
from app.memory.service import (
    APPLICATIONS,
    IMPORTANT_CONTEXT,
    PROJECTS,
    ROUTINES,
    SETTINGS,
    USER_PREFERENCES,
    MemoryService,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = session_factory()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture(autouse=True)
def _seed_every_category(db, monkeypatch):
    """Populate every memory category `retrieve_relevant` could possibly return, and
    point `app.memory.retrieval.SessionLocal` at this test's session -- same monkeypatch
    pattern `test_memory_service.py` uses for `seed_default_memory()`. Every test in
    this file therefore proves *selection*, not merely "the populated categories
    happened to be empty everywhere else".
    """
    service = MemoryService(db)
    service.set(ROUTINES, "coding", {"editor": "VS Code", "browser": "Chrome", "default_project": "portfolio"})
    service.set(APPLICATIONS, "vscode", {"command": ["code"], "process_names": ["Code.exe"]})
    service.set(USER_PREFERENCES, "task_priority_default", "medium")
    service.set(PROJECTS, "portfolio", {"path": "/home/user/portfolio"})
    service.set(SETTINGS, "voice_enabled", True)
    service.set(IMPORTANT_CONTEXT, "note", "some durable context")

    monkeypatch.setattr("app.memory.retrieval.SessionLocal", lambda: db)
    # SessionLocal() is .close()d inside retrieve_relevant() -- don't let that close
    # the fixture's own session out from under it.
    monkeypatch.setattr(db, "close", lambda: None)


def test_routine_classified_request_pulls_routines_and_applications_only(db):
    result = retrieve_relevant("run my coding routine")

    assert set(result) == {ROUTINES, APPLICATIONS}
    assert result[ROUTINES] == {
        "coding": {"editor": "VS Code", "browser": "Chrome", "default_project": "portfolio"}
    }
    assert result[APPLICATIONS] == {"vscode": {"command": ["code"], "process_names": ["Code.exe"]}}


def test_application_classified_request_pulls_applications_only(db):
    result = retrieve_relevant("open vscode")

    assert set(result) == {APPLICATIONS}


def test_plain_task_request_pulls_no_memory(db):
    # References the task category (via "remind"/"task" keywords) but no priority or
    # preference -- app.memory.retrieval's task gate should stay closed.
    result = retrieve_relevant("remind me to buy milk tomorrow")

    assert result == {}


def test_task_request_referencing_priority_pulls_user_preferences_only(db):
    result = retrieve_relevant("make my submit-report task high priority")

    assert set(result) == {USER_PREFERENCES}
    assert result[USER_PREFERENCES] == {"task_priority_default": "medium"}


def test_unrelated_category_message_pulls_no_memory(db):
    # Matches the "timer" tool category, which maps to no memory category at all.
    result = retrieve_relevant("set a timer for 10 minutes")

    assert result == {}


def test_ambiguous_message_pulls_no_memory(db):
    result = retrieve_relevant("tell me something interesting")

    assert result == {}


def test_matched_but_empty_category_is_omitted(db):
    # "applications" is a matched category for a routine-classified message, but has
    # nothing persisted under it here -- it must not show up as `{"applications": {}}`.
    service = MemoryService(db)
    service.delete(APPLICATIONS, "vscode")

    result = retrieve_relevant("run my coding routine")

    assert set(result) == {ROUTINES}
