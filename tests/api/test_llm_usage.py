"""
`GET /api/llm/usage` route tests (§8, §39 MVP "AI provider status" panel).

The route builds its own `ProviderManager`/`QuotaManager` per request rather than
taking them as FastAPI dependencies (see `app.api.routes.llm_usage`), so tests
monkeypatch those two names directly in the route module -- `ProviderManager` to a
fixed two-provider chain (no real Gemini/Ollama construction, no network probing),
`QuotaManager` to one with a small injected budget so WARNING/CRITICAL/FAILOVER are
reachable with a handful of rows instead of the real default budget of 80.
`HealthManager` *is* an overridable dependency (`get_health_manager`), so that one is
overridden the normal FastAPI way, same as tests/api/test_routines.py's `get_tool_registry`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user, get_health_manager
from app.config.settings import Settings
from app.database.database import get_db
from app.database.models import LLMUsage
from app.llm.base import LLMResult
from app.llm.health import HealthManager
from app.llm.provider_manager import ProviderManager, ProviderEntry
from app.llm.quota_manager import QuotaManager
from main import app


class _StubProvider:
    """Only `.name` is read by the route (`get_chain()`/`all_provider_names()`) --
    no real generate()/is_available() needed.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    def is_available(self) -> bool:
        return True

    async def generate(self, request, *, fallback_used: bool = False):  # pragma: no cover
        raise NotImplementedError


def _quota_settings(**overrides) -> Settings:
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
    status: str = "SUCCESS",
    fallback_used: bool = False,
    request_tokens: int = 10,
    response_tokens: int = 20,
    timestamp: datetime | None = None,
) -> None:
    for _ in range(count):
        row = LLMUsage(
            provider=provider,
            model="test-model",
            status=status,
            fallback_used=fallback_used,
            request_tokens=request_tokens,
            response_tokens=response_tokens,
        )
        session.add(row)
        session.flush()
        if timestamp is not None:
            row.timestamp = timestamp
    session.commit()


@pytest.fixture()
def health_manager() -> HealthManager:
    return HealthManager(settings=_quota_settings())


@pytest.fixture()
def client(test_db, monkeypatch, health_manager):
    def override_get_db():
        db = test_db()
        try:
            yield db
        finally:
            db.close()

    # Fixed two-provider chain: "gemini" enabled, "disabled_provider" configured but
    # not in the chain -- exercises the route's `enabled` flag (file 06's
    # `all_provider_names()` vs `get_chain()` distinction).
    def stub_provider_manager(*, db=None):
        return ProviderManager(
            entries=[
                ProviderEntry(provider=_StubProvider("gemini"), priority=1, enabled=True),
                ProviderEntry(provider=_StubProvider("disabled_provider"), priority=2, enabled=False),
            ]
        )

    def stub_quota_manager(*, db=None):
        return QuotaManager(settings=_quota_settings(), db=db)

    monkeypatch.setattr("app.api.routes.llm_usage.ProviderManager", stub_provider_manager)
    monkeypatch.setattr("app.api.routes.llm_usage.QuotaManager", stub_quota_manager)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_health_manager] = lambda: health_manager
    # §34, file 12 prompt 1: this router now requires authentication -- stub it out
    # here since these tests exercise the usage/status contract, not auth itself
    # (tests/api/test_auth.py covers that).
    app.dependency_overrides[get_current_user] = lambda: object()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_lists_configured_providers_including_disabled(client):
    response = client.get("/api/llm/usage")
    assert response.status_code == 200
    body = response.json()

    names = [p["provider"] for p in body["providers"]]
    assert names == ["gemini", "disabled_provider"]

    disabled = next(p for p in body["providers"] if p["provider"] == "disabled_provider")
    assert disabled["enabled"] is False
    gemini = next(p for p in body["providers"] if p["provider"] == "gemini")
    assert gemini["enabled"] is True


def test_aggregates_todays_usage_per_provider(client, test_db):
    session = test_db()
    _add_usage_rows(session, provider="gemini", count=3, request_tokens=10, response_tokens=20)
    _add_usage_rows(session, provider="gemini", count=1, status="RETRYABLE_ERROR")
    _add_usage_rows(session, provider="gemini", count=1, fallback_used=True)
    yesterday = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    _add_usage_rows(session, provider="gemini", count=5, timestamp=yesterday)

    response = client.get("/api/llm/usage")
    gemini = next(p for p in response.json()["providers"] if p["provider"] == "gemini")

    assert gemini["requests"] == 5  # today's rows only -- yesterday's 5 excluded
    assert gemini["request_tokens"] == 50
    assert gemini["response_tokens"] == 100
    assert gemini["failures"] == 1
    assert gemini["fallback_count"] == 1


@pytest.mark.parametrize(
    "usage,expected_quota_status",
    [
        (0, "NORMAL"),
        (8, "WARNING"),  # 80% of the stubbed budget of 10
        (9, "CRITICAL"),  # 90%
        (10, "FAILOVER"),  # budget fully spent
    ],
)
def test_quota_status_reflects_todays_usage(client, test_db, usage, expected_quota_status):
    session = test_db()
    _add_usage_rows(session, provider="gemini", count=usage)

    response = client.get("/api/llm/usage")
    gemini = next(p for p in response.json()["providers"] if p["provider"] == "gemini")

    assert gemini["quota_status"] == expected_quota_status
    # Healthy the whole time (no HealthManager failures recorded) -- the badge tracks
    # quota_status exactly.
    assert gemini["status"] == expected_quota_status
    assert gemini["health"]["healthy"] is True


def test_status_badge_reports_failover_when_unhealthy_even_with_quota_headroom(
    client, health_manager
):
    # No usage at all (quota_status would be NORMAL) but HealthManager considers the
    # provider unavailable -- the badge must still read FAILOVER, per
    # `_status_badge`'s docstring in the route module.
    health_manager.record_result(
        "gemini", LLMResult(status="PERMANENT_ERROR", error_type="invalid_api_key")
    )

    response = client.get("/api/llm/usage")
    gemini = next(p for p in response.json()["providers"] if p["provider"] == "gemini")

    assert gemini["quota_status"] == "NORMAL"
    assert gemini["health"]["healthy"] is False
    assert gemini["health"]["last_error"] == "invalid_api_key"
    assert gemini["status"] == "FAILOVER"


def test_budget_reported_for_metered_provider_and_null_for_unmetered(client):
    # "gemini" is metered by the stubbed settings (budget=10); "disabled_provider"
    # isn't recognized by `QuotaManager._budget_for` at all, same as Ollama in
    # production -- both should report budget=None rather than 0, since None means
    # "unmetered" and 0 would mean "no requests allowed today".
    response = client.get("/api/llm/usage")
    body = response.json()

    gemini = next(p for p in body["providers"] if p["provider"] == "gemini")
    other = next(p for p in body["providers"] if p["provider"] == "disabled_provider")

    assert gemini["budget"] == 10
    assert other["budget"] is None


def test_includes_providers_with_historical_rows_no_longer_configured(client, test_db):
    session = test_db()
    _add_usage_rows(session, provider="retired_provider", count=2)

    response = client.get("/api/llm/usage")
    names = [p["provider"] for p in response.json()["providers"]]

    # Configured providers first (in priority order), then any provider found only in
    # historical `llm_usage` rows.
    assert names == ["gemini", "disabled_provider", "retired_provider"]
    retired = next(p for p in response.json()["providers"] if p["provider"] == "retired_provider")
    assert retired["enabled"] is False
    assert retired["requests"] == 2
