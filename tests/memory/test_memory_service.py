"""
MemoryService tests (§37 Phase 8 / file 09 prompt 1).

Exercises `MemoryService` directly against a throwaway in-memory SQLite session (same
pattern as tests/tasks/test_task_service.py) -- get/set/list/delete round-tripping JSON-
shaped values, the `default` fallback for a missing key, set() overwriting in place
rather than duplicating a row, and `seed_default_memory()`'s idempotent first-startup
behavior.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import models  # noqa: F401 -- registers tables on Base.metadata
from app.database.database import Base
from app.database.models import Memory
from app.memory.service import (
    DEFAULT_CODING_CATEGORY,
    DEFAULT_CODING_KEY,
    DEFAULT_CODING_VALUE,
    MemoryService,
    seed_default_memory,
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


# --- get / set / list / delete ---------------------------------------------------------


def test_get_missing_key_returns_default(db):
    service = MemoryService(db)
    assert service.get("applications", "vscode") is None
    assert service.get("applications", "vscode", default="fallback") == "fallback"


def test_set_then_get_round_trips_a_dict_value(db):
    service = MemoryService(db)
    value = {"editor": "VS Code", "browser": "Chrome", "default_project": "portfolio"}

    service.set("routines", "coding", value)

    assert service.get("routines", "coding") == value


def test_set_round_trips_scalar_values(db):
    service = MemoryService(db)

    service.set("settings", "voice_enabled", True)
    service.set("settings", "volume", 7)
    service.set("settings", "greeting", "hello")

    assert service.get("settings", "voice_enabled") is True
    assert service.get("settings", "volume") == 7
    assert service.get("settings", "greeting") == "hello"


def test_set_on_existing_key_overwrites_in_place(db):
    service = MemoryService(db)
    service.set("applications", "vscode", {"command": ["code"]})
    service.set("applications", "vscode", {"command": ["code-insiders"]})

    assert service.get("applications", "vscode") == {"command": ["code-insiders"]}
    # Overwritten, not duplicated -- exactly one row for this category/key pair.
    assert db.query(Memory).filter(Memory.category == "applications", Memory.key == "vscode").count() == 1


def test_list_returns_all_keys_in_a_category_decoded(db):
    service = MemoryService(db)
    service.set("applications", "vscode", {"command": ["code"]})
    service.set("applications", "chrome", {"command": ["chrome"]})
    service.set("routines", "coding", {"editor": "VS Code"})

    result = service.list("applications")

    assert result == {
        "vscode": {"command": ["code"]},
        "chrome": {"command": ["chrome"]},
    }


def test_list_empty_category_returns_empty_dict(db):
    service = MemoryService(db)
    assert service.list("projects") == {}


def test_delete_existing_key_returns_true_and_removes_it(db):
    service = MemoryService(db)
    service.set("applications", "vscode", {"command": ["code"]})

    assert service.delete("applications", "vscode") is True
    assert service.get("applications", "vscode") is None


def test_delete_missing_key_returns_false(db):
    service = MemoryService(db)
    assert service.delete("applications", "nope") is False


# --- seed_default_memory() --------------------------------------------------------------


def test_seed_default_memory_creates_coding_entry(db, monkeypatch):
    monkeypatch.setattr("app.memory.service.SessionLocal", lambda: db)
    # SessionLocal is called, then .close()d by seed_default_memory() -- don't let it
    # close the fixture's session out from under later assertions in this test.
    monkeypatch.setattr(db, "close", lambda: None)

    seed_default_memory()

    service = MemoryService(db)
    assert service.get(DEFAULT_CODING_CATEGORY, DEFAULT_CODING_KEY) == DEFAULT_CODING_VALUE


def test_seed_default_memory_is_idempotent_and_does_not_clobber_edits(db, monkeypatch):
    monkeypatch.setattr("app.memory.service.SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)

    service = MemoryService(db)
    seed_default_memory()
    edited = {"editor": "Neovim", "browser": "Firefox", "default_project": "jarvis"}
    service.set(DEFAULT_CODING_CATEGORY, DEFAULT_CODING_KEY, edited)

    seed_default_memory()  # simulate a second startup

    assert service.get(DEFAULT_CODING_CATEGORY, DEFAULT_CODING_KEY) == edited
