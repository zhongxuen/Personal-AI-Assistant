"""
SQLite engine/session setup.

Per the development plan (Phase 0), this only wires up the connection.
No tables/models are defined yet — those arrive with the task/routine/
memory subsystems in later phases.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config.settings import get_settings

settings = get_settings()

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """Base class for future ORM models (tasks, routines, memory, usage logs, ...)."""


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a DB session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_indexes() -> None:
    """Create any index defined on a model that doesn't exist in the database yet.

    `Base.metadata.create_all()` (main.py's startup) only builds tables it finds
    missing -- for a table that already exists it does nothing at all, indexes included.
    So an index added to a model after the first run reaches a fresh install and no
    existing one, which is precisely backwards: the long-lived database with the most
    accumulated rows is the one that needs it.

    This is a stopgap for the lack of migrations, not a substitute for them. It only
    ever *adds* indexes (`checkfirst=True`, so re-running is a no-op) and never touches
    columns, constraints or data, which keeps it safe to run unconditionally at startup
    -- but the moment a change needs anything beyond that, it needs Alembic instead
    (already tracked as tech debt against `create_all` in md-files/01-project-
    foundation.md).
    """
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            for index in table.indexes:
                index.create(bind=connection, checkfirst=True)
