"""
LLM provider usage/status route (§8, §39 MVP "AI provider status" UI).

`GET /api/llm/usage` is the one place both halves of file 06's provider tracking are
reported together for the frontend's status panel:

  - **Usage** -- today's `llm_usage` rows (§8), aggregated per provider: request
    count, token totals, failure count (`status != "SUCCESS"`), and fallback count
    (`fallback_used`). This is persisted fact -- it survives a restart and is exactly
    what `QuotaManager` itself counts against each provider's daily budget.
  - **Status** -- each provider's *live* `QuotaManager.status()` (NORMAL/WARNING/
    CRITICAL/FAILOVER) plus `HealthManager` state (§6). Both are in-memory,
    process-wide trackers -- `HealthManager` via the shared instance
    `app.api.dependencies.get_health_manager` hands out (see its docstring for why
    that sharing matters), `QuotaManager` by re-deriving straight from the same
    `llm_usage` rows this route already aggregates, so it needs no shared instance to
    stay correct.

Read-only: this route reports status, it never changes it. Quota config stays a
code-level/environment concern, and the one health mutation that *is* exposed over HTTP
-- `HealthManager.reset()`, needed to clear a sticky MISCONFIGURED without restarting
the process -- lives on the diagnostics router instead
(`POST /api/diagnostics/providers/{name}/reset`, see `app.api.routes.diagnostics`),
which is where operator actions against live components already are.

Requires a valid bearer token (§34, file 12 prompt 1, router-level `get_current_user`
dependency) -- this status panel is reachable over the web, not gated by
`app.api.local_only`'s loopback check.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_health_manager
from app.database.database import get_db
from app.database.models import LLMUsage
from app.llm.health import HealthManager
from app.llm.provider_manager import ProviderManager
from app.llm.quota_manager import QuotaManager, start_of_today_utc

router = APIRouter(tags=["llm"], dependencies=[Depends(get_current_user)])


class ProviderHealthOut(BaseModel):
    state: str
    healthy: bool
    last_error: str | None = None


class ProviderUsageOut(BaseModel):
    provider: str
    enabled: bool
    requests: int
    request_tokens: int
    response_tokens: int
    # Input tokens served from the provider's prompt cache today (a subset of
    # request_tokens) and estimated spend today -- 0 for providers that don't report
    # them (only OpenAI does today).
    cached_tokens: int
    cost_usd: float
    failures: int
    fallback_count: int
    # What the used/limit bar is measured in: "usd" (OpenAI's spend budget),
    # "requests" (Gemini's request budget), or None when unmetered (Ollama -- local,
    # free, nothing to bar-chart). `used`/`limit` are today's numbers in that unit,
    # exactly what `QuotaManager` enforces; `limit` is None when unmetered.
    unit: str | None
    used: float
    limit: float | None
    # Same, for an optional monthly budget (OpenAI's `openai_monthly_budget_usd`);
    # both None when no monthly budget is configured.
    monthly_used: float | None = None
    monthly_limit: float | None = None
    # Kept for older clients: the same value as `limit`.
    budget: float | None
    quota_status: str
    # The single badge value the frontend renders (NORMAL/WARNING/CRITICAL/FAILOVER,
    # §8/§39) -- `quota_status` downgraded to FAILOVER whenever HealthManager also
    # considers this provider currently unhealthy, since AIRouter would skip it
    # either way. See `_status_badge` below.
    status: str
    health: ProviderHealthOut


class LLMUsageResponse(BaseModel):
    generated_at: str
    providers: list[ProviderUsageOut]


def _usage_row(db: Session, provider: str, since: datetime) -> dict[str, int | float]:
    (requests, request_tokens, response_tokens, cached_tokens, cost_usd, failures, fallback_count) = (
        db.query(
            func.count(LLMUsage.id),
            func.coalesce(func.sum(LLMUsage.request_tokens), 0),
            func.coalesce(func.sum(LLMUsage.response_tokens), 0),
            func.coalesce(func.sum(LLMUsage.cached_tokens), 0),
            func.coalesce(func.sum(LLMUsage.cost_usd), 0.0),
            func.coalesce(func.sum(case((LLMUsage.status != "SUCCESS", 1), else_=0)), 0),
            func.coalesce(func.sum(case((LLMUsage.fallback_used.is_(True), 1), else_=0)), 0),
        )
        .filter(LLMUsage.provider == provider, LLMUsage.timestamp >= since)
        .one()
    )
    return {
        "requests": int(requests or 0),
        "request_tokens": int(request_tokens or 0),
        "response_tokens": int(response_tokens or 0),
        "cached_tokens": int(cached_tokens or 0),
        "cost_usd": float(cost_usd or 0.0),
        "failures": int(failures or 0),
        "fallback_count": int(fallback_count or 0),
    }


def _status_badge(quota_status: str, healthy: bool) -> str:
    """The one badge value §39's UI requirement asks for. A provider AIRouter would
    currently skip (unhealthy per HealthManager) is reported as FAILOVER regardless
    of its quota headroom -- quota_status alone would otherwise under-report a
    provider that's actually out of rotation for a health reason (e.g. repeated
    timeouts), not a quota one.
    """
    return quota_status if healthy else "FAILOVER"


@router.get("/llm/usage", response_model=LLMUsageResponse)
def get_llm_usage(
    db: Session = Depends(get_db),
    health_manager: HealthManager = Depends(get_health_manager),
) -> LLMUsageResponse:
    provider_manager = ProviderManager(db=db)
    enabled_names = {provider.name for provider in provider_manager.get_chain()}
    all_names = provider_manager.all_provider_names()

    # Include any provider with historical usage rows even if it's no longer part of
    # the configured chain (e.g. renamed/removed) -- configured providers list first,
    # in their configured priority order, so the panel's order matches the chain
    # AIRouter actually walks.
    logged_names = [row[0] for row in db.query(LLMUsage.provider).distinct().all()]
    seen = set(all_names)
    ordered_names = all_names + [name for name in logged_names if name not in seen]

    quota_manager = QuotaManager(db=db)
    since = start_of_today_utc()

    providers = []
    for name in ordered_names:
        usage = _usage_row(db, name, since)
        health_status = health_manager.get_status(name)
        quota_status = quota_manager.status(name)
        unit = quota_manager.unit_for(name)
        limit = quota_manager.budget_for(name)
        monthly_limit = quota_manager.monthly_budget_for(name)
        providers.append(
            ProviderUsageOut(
                provider=name,
                enabled=name in enabled_names,
                unit=unit,
                # Unmetered: report the plain request count so the panel still has a
                # number to show; metered: exactly what QuotaManager enforces.
                used=quota_manager.current_usage(name) if unit else usage["requests"],
                limit=limit,
                monthly_used=quota_manager.monthly_usage(name) if monthly_limit is not None else None,
                monthly_limit=monthly_limit,
                budget=limit,
                quota_status=quota_status,
                status=_status_badge(quota_status, health_status.healthy),
                health=ProviderHealthOut(
                    state=health_status.state.value,
                    healthy=health_status.healthy,
                    last_error=health_status.last_error,
                ),
                **usage,
            )
        )

    return LLMUsageResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        providers=providers,
    )
