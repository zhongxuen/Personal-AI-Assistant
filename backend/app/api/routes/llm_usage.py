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

Read-only: this route cannot reset a provider's health or change its budget --
`HealthManager.reset()` and quota config stay code-level operator actions, not
exposed over HTTP.

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
    failures: int
    fallback_count: int
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


def _usage_row(db: Session, provider: str, since: datetime) -> dict[str, int]:
    (requests, request_tokens, response_tokens, failures, fallback_count) = (
        db.query(
            func.count(LLMUsage.id),
            func.coalesce(func.sum(LLMUsage.request_tokens), 0),
            func.coalesce(func.sum(LLMUsage.response_tokens), 0),
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
        providers.append(
            ProviderUsageOut(
                provider=name,
                enabled=name in enabled_names,
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
