"""Phase 0 sanity check: the engine connects and every §26 table gets created."""

from sqlalchemy import inspect, text

from app.database import models  # noqa: F401  (registers models on Base.metadata)
from app.database.database import Base, engine

EXPECTED_TABLES = {
    "users",
    "settings",
    "tasks",
    "task_reminders",
    "routines",
    "routine_steps",
    "applications",
    "conversations",
    "conversation_messages",
    "memories",
    "llm_providers",
    "llm_usage",
    "tool_execution_logs",
    "permissions",
}


def test_engine_connects():
    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1


def test_all_tables_created():
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    missing = EXPECTED_TABLES - table_names
    assert not missing, f"Missing tables: {missing}"
