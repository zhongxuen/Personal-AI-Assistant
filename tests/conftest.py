"""
Shared fixtures (§37 Phase 2 / file 03, extended file 04 prompt 1).

`app/tools/tasks.py`, `app/tools/routines.py`, and `app/tasks/scheduler.py` each open
their own short-lived DB session via a module-level `SessionLocal` imported directly
from `app.database.database` -- there's no dependency injection to swap a session in.
`test_db` monkeypatches those module-level references to a throwaway in-memory SQLite
database (shared across the whole fixture via `StaticPool`, since every tool call opens
a brand new `Session`) so tests never touch the real `jarvis.db` file.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import models  # noqa: F401 -- registers tables on Base.metadata
from app.database.database import Base


@pytest.fixture()
def test_db(monkeypatch):
    """A throwaway in-memory SQLite DB wired into every tool module that opens its own
    `SessionLocal()` rather than accepting an injected `Session`. Yields the test
    sessionmaker in case a test wants to inspect state directly.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    monkeypatch.setattr("app.tools.tasks.SessionLocal", TestSessionLocal)
    monkeypatch.setattr("app.tools.routines.SessionLocal", TestSessionLocal)
    monkeypatch.setattr("app.tasks.scheduler.SessionLocal", TestSessionLocal)

    yield TestSessionLocal

    engine.dispose()
