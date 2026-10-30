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


def test_ensure_columns_adds_new_nullable_columns_to_an_existing_table():
    """An install whose llm_usage table predates cached_tokens/cost_usd gets them added
    in place at startup -- existing rows keep their data and read NULL for the new
    columns."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from app.database.database import ensure_columns

    old = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    with old.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE llm_usage (id INTEGER PRIMARY KEY, provider VARCHAR(100) NOT NULL, "
            "model VARCHAR(100) NOT NULL, timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "request_tokens INTEGER, response_tokens INTEGER, status VARCHAR(50) NOT NULL, "
            "error_type VARCHAR(100), latency INTEGER, fallback_used BOOLEAN NOT NULL)"
        )
        conn.exec_driver_sql(
            "INSERT INTO llm_usage (provider, model, status, fallback_used) VALUES ('gemini', 'm', 'SUCCESS', 0)"
        )

    added = ensure_columns(old)

    assert set(added) == {"llm_usage.cached_tokens", "llm_usage.cost_usd"}
    columns = {c["name"] for c in inspect(old).get_columns("llm_usage")}
    assert {"cached_tokens", "cost_usd"} <= columns
    with old.connect() as conn:
        assert conn.execute(text("SELECT provider, cost_usd FROM llm_usage")).one() == ("gemini", None)
    # Idempotent: a second startup adds nothing.
    assert ensure_columns(old) == []
