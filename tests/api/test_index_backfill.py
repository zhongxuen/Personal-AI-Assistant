"""
Index backfill on an already-created database (`app.database.database.ensure_indexes`).

`Base.metadata.create_all()` only builds *missing tables* -- for a table that already
exists it does nothing at all, indexes included. So without this step an index added to
a model reaches fresh installs and no existing one, which is backwards: the long-lived
database with the most accumulated rows is the one that needs it.

The concrete case that motivated it: `QuotaManager.current_usage` runs a COUNT filtered
on `(provider, timestamp)` before *every* LLM call, and `llm_usage` gains a row per call
and is never pruned -- so on any pre-existing `jarvis.db` that pre-flight check was a
full table scan that got slower for the life of the install.
"""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool

from app.database import models  # noqa: F401 -- registers tables on Base.metadata
from app.database.database import Base


def _index_names(engine, table: str) -> set[str]:
    return {index["name"] for index in inspect(engine).get_indexes(table)}


def test_ensure_indexes_adds_a_missing_index_to_an_existing_table(monkeypatch):
    """The real-world shape: a database created before the index existed."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)

    # Simulate the pre-existing database by dropping the index back off again, leaving
    # the table and its data in place.
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX IF EXISTS ix_llm_usage_provider_timestamp"))
    assert "ix_llm_usage_provider_timestamp" not in _index_names(engine, "llm_usage")

    # create_all alone must NOT fix it -- that gap is the reason ensure_indexes exists.
    Base.metadata.create_all(bind=engine)
    assert "ix_llm_usage_provider_timestamp" not in _index_names(engine, "llm_usage")

    import app.database.database as database_module

    monkeypatch.setattr(database_module, "engine", engine)
    database_module.ensure_indexes()

    assert "ix_llm_usage_provider_timestamp" in _index_names(engine, "llm_usage")
    engine.dispose()


def test_ensure_indexes_is_idempotent(monkeypatch):
    """It runs on every startup, so a second pass must be a silent no-op rather than
    an "index already exists" error.
    """
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)

    import app.database.database as database_module

    monkeypatch.setattr(database_module, "engine", engine)
    database_module.ensure_indexes()
    database_module.ensure_indexes()  # must not raise

    assert "ix_llm_usage_provider_timestamp" in _index_names(engine, "llm_usage")
    engine.dispose()


def test_the_quota_budget_check_uses_the_index_instead_of_scanning():
    """The behavior that actually matters -- the planner picks the index for the exact
    predicate `QuotaManager.current_usage` issues, rather than scanning `llm_usage`.
    """
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)

    with engine.begin() as connection:
        plan = connection.execute(
            text(
                "EXPLAIN QUERY PLAN SELECT count(*) FROM llm_usage "
                "WHERE provider = 'gemini' AND timestamp >= '2026-01-01'"
            )
        ).fetchall()

    detail = " ".join(str(row[-1]) for row in plan)
    assert "ix_llm_usage_provider_timestamp" in detail
    assert "SCAN llm_usage" not in detail
    engine.dispose()
