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


def _add_usage_rows(
    session,
    *,
    provider: str,
    count: int,
    timestamp: datetime | None = None,
    status: str = "SUCCESS",
) -> None:
    for _ in range(count):
        row = LLMUsage(provider=provider, model="gemini-3.6-flash", status=status)
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


def test_current_usage_ignores_permanent_errors_the_provider_never_billed(test_db):
    """A PERMANENT_ERROR is a config fault on our side (no/bad API key, a model name
    the key can't reach) -- Google charges none of those against the daily quota this
    budget shadows, so neither do we. Otherwise a single misconfigured GEMINI_MODEL
    would eat the whole day's budget one failed request at a time and trip FAILOVER
    for a reason that has nothing to do with quota.
    """
    session = test_db()
    _add_usage_rows(session, provider="gemini", count=2, status="SUCCESS")
    _add_usage_rows(session, provider="gemini", count=5, status="PERMANENT_ERROR")

    manager = QuotaManager(settings=_settings(), db=session)

    assert manager.current_usage("gemini") == 2


@pytest.mark.parametrize("status", ["QUOTA_EXHAUSTED", "RETRYABLE_ERROR"])
def test_current_usage_still_counts_failures_that_reached_the_provider(test_db, status):
    """The other two failure statuses stay counted: QUOTA_EXHAUSTED is the provider
    itself saying the quota went, and a RETRYABLE_ERROR (timeout/5xx) may well have
    been metered server-side before it failed.
    """
    session = test_db()
    _add_usage_rows(session, provider="gemini", count=3, status=status)

    manager = QuotaManager(settings=_settings(), db=session)

    assert manager.current_usage("gemini") == 3


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


def test_budget_for_reports_configured_budget_and_none_when_unmetered(test_db):
    manager = QuotaManager(settings=_settings(), db=test_db())

    assert manager.budget_for("gemini") == 10
    assert manager.budget_for("ollama") is None


def test_unmetered_provider_is_always_normal_and_within_budget(test_db):
    session = test_db()
    _add_usage_rows(session, provider="ollama", count=1000)
    manager = QuotaManager(settings=_settings(), db=session)

    assert manager.status("ollama") == "NORMAL"
    assert manager.within_budget("ollama") is True


# --- USD-metered providers (OpenAI) ---------------------------------------------------


def _add_cost_rows(session, *, costs: list[float], provider: str = "openai", timestamp: datetime | None = None) -> None:
    for cost in costs:
        row = LLMUsage(provider=provider, model="gpt-test", status="SUCCESS", cost_usd=cost)
        session.add(row)
        session.flush()
        if timestamp is not None:
            row.timestamp = timestamp
    session.commit()


def _usd_settings(**overrides) -> Settings:
    defaults: dict = dict(
        openai_daily_budget_usd=1.00,
        openai_monthly_budget_usd=None,
        openai_warning_threshold=0.80,
        openai_critical_threshold=0.90,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def test_openai_is_metered_in_usd_and_gemini_in_requests():
    manager = QuotaManager(settings=_usd_settings(), db=None)

    assert manager.unit_for("openai") == "usd"
    assert manager.budget_for("openai") == 1.00
    assert manager.unit_for("gemini") == "requests"
    assert manager.unit_for("ollama") is None
    assert manager.budget_for("ollama") is None


def test_usd_usage_sums_todays_cost_only(test_db):
    session = test_db()
    _add_cost_rows(session, costs=[0.10, 0.25])
    _add_cost_rows(session, costs=[5.0], timestamp=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1))
    _add_cost_rows(session, costs=[9.0], provider="gemini")

    manager = QuotaManager(settings=_usd_settings(), db=session)

    assert manager.current_usage("openai") == pytest.approx(0.35)


@pytest.mark.parametrize(
    ("spent", "expected"),
    [(0.10, "NORMAL"), (0.80, "WARNING"), (0.95, "CRITICAL"), (1.00, "FAILOVER"), (1.50, "FAILOVER")],
)
def test_usd_thresholds(test_db, spent, expected):
    session = test_db()
    _add_cost_rows(session, costs=[spent])

    manager = QuotaManager(settings=_usd_settings(), db=session)

    assert manager.status("openai") == expected
    assert manager.within_budget("openai") is (expected != "FAILOVER")


def test_monthly_budget_can_trip_failover_even_with_daily_headroom(test_db):
    session = test_db()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _add_cost_rows(session, costs=[0.10])
    # Earlier this month (not today) -- only the monthly window sees it. On the 1st of a
    # month there is no "earlier this month", so the spend is put in today instead.
    earlier = now.replace(day=1, hour=0, minute=0, second=1) if now.day > 1 else now
    _add_cost_rows(session, costs=[4.95 if now.day > 1 else 0.0], timestamp=earlier)

    manager = QuotaManager(settings=_usd_settings(openai_daily_budget_usd=1.0, openai_monthly_budget_usd=5.0), db=session)

    if now.day > 1:
        assert manager.current_usage("openai") == pytest.approx(0.10)
        assert manager.monthly_usage("openai") == pytest.approx(5.05)
        assert manager.status("openai") == "FAILOVER"
    else:  # pragma: no cover -- calendar-dependent branch
        assert manager.status("openai") == "NORMAL"
