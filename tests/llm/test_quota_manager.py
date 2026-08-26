"""
QuotaManager tests (§8, file 06).

Exercises the internal-budget bookkeeping in isolation from `AIRouter`: correct
`current_usage()` counts (today only, per provider), the NORMAL/WARNING/CRITICAL/
FAILOVER thresholds from §8, and that an unmetered provider (no configured budget)
never blocks a call.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config.settings import Settings
from app.database.models import LLMUsage
from app.llm.quota_manager import QuotaManager


def _settings(**overrides) -> Settings:
    defaults: dict = dict(
        gemini_daily_request_budget=10,
        gemini_warning_threshold=0.80,
        gemini_critical_threshold=0.90,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def _add_usage_rows(session, *, provider: str, count: int, timestamp: datetime | None = None) -> None:
    for _ in range(count):
        row = LLMUsage(provider=provider, model="gemini-2.5-flash", status="SUCCESS")
        session.add(row)
        session.flush()
        if timestamp is not None:
            # `LLMUsage.timestamp` has a server_default -- overwrite it directly so
            # tests can simulate rows from a different day.
            row.timestamp = timestamp
    session.commit()


def test_current_usage_counts_only_todays_rows_for_the_given_provider(test_db):
    session = test_db()
    _add_usage_rows(session, provider="gemini", count=3)
    _add_usage_rows(session, provider="ollama", count=5)
    yesterday = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    _add_usage_rows(session, provider="gemini", count=2, timestamp=yesterday)

    manager = QuotaManager(settings=_settings(), db=session)

    assert manager.current_usage("gemini") == 3
    assert manager.current_usage("ollama") == 5


def test_current_usage_without_a_db_session_is_zero():
    manager = QuotaManager(settings=_settings(), db=None)

    assert manager.current_usage("gemini") == 0


@pytest.mark.parametrize(
    "usage,expected_status",
    [
        (0, "NORMAL"),
        (7, "NORMAL"),  # 70% < 80% warning threshold
        (8, "WARNING"),  # 80% hits the warning threshold
        (9, "CRITICAL"),  # 90% hits the critical threshold
        (10, "FAILOVER"),  # 100% -- budget fully spent
        (11, "FAILOVER"),  # over budget
    ],
)
def test_status_thresholds_match_budget_percentage(test_db, usage, expected_status):
    session = test_db()
    _add_usage_rows(session, provider="gemini", count=usage)
    manager = QuotaManager(settings=_settings(), db=session)

    assert manager.status("gemini") == expected_status


def test_within_budget_is_false_only_at_failover(test_db):
    session = test_db()
    _add_usage_rows(session, provider="gemini", count=10)
    manager = QuotaManager(settings=_settings(), db=session)

    assert manager.status("gemini") == "FAILOVER"
    assert manager.within_budget("gemini") is False


def test_zero_budget_fails_over_immediately_with_no_usage(test_db):
    session = test_db()
    manager = QuotaManager(settings=_settings(gemini_daily_request_budget=0), db=session)

    assert manager.status("gemini") == "FAILOVER"
    assert manager.within_budget("gemini") is False


def test_unmetered_provider_is_always_normal_and_within_budget(test_db):
    session = test_db()
    _add_usage_rows(session, provider="ollama", count=1000)
    manager = QuotaManager(settings=_settings(), db=session)

    assert manager.status("ollama") == "NORMAL"
    assert manager.within_budget("ollama") is True
