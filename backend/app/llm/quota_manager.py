"""
LLM usage budget tracking (§8, §37 Phase 5, file 06).

Implements an *internal* budget per provider, deliberately kept below whatever the
provider's real quota or billing cap happens to be (§41 Rule 9: never hardcode the
actual provider quota -- it can change without notice). `AIRouter` uses
`within_budget()` *before* attempting a call, so a day that's already near its
self-imposed budget fails over to the next provider instead of spending real quota (or
money) finding that out the hard way.

A provider's budget is metered in one of two units:

  - `"requests"` -- Gemini (`gemini_daily_request_budget`): a count of billable calls
    today, shadowing a free tier's daily request quota.
  - `"usd"` -- OpenAI (`openai_daily_budget_usd`, optionally
    `openai_monthly_budget_usd`): the sum of `llm_usage.cost_usd` (estimated by
    `OpenAIProvider` from token counts x configured prices) today / this month. A paid
    API's real limit is money, not request count -- one long tool-loop turn can cost
    more than fifty short ones.

Both units share the same NORMAL -> WARNING -> CRITICAL -> FAILOVER thresholds. Any
provider this module doesn't recognize (Ollama -- local and free) is unmetered:
`status()` always NORMAL, `within_budget()` always True. To meter another provider,
add a `_Meter` for it in `_meter_for()` rather than hardcoding its name deeper in here.

Nothing here makes network calls -- like `HealthManager`, this is pure bookkeeping
against `llm_usage` rows the providers already write.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config.settings import Settings, get_settings
from app.database.models import LLMUsage

# §8's example table: NORMAL -> WARNING (80%) -> CRITICAL (90%) -> FAILOVER (100%+).
QuotaStatus = Literal["NORMAL", "WARNING", "CRITICAL", "FAILOVER"]
QuotaUnit = Literal["usd", "requests"]

_STATUS_SEVERITY: dict[QuotaStatus, int] = {"NORMAL": 0, "WARNING": 1, "CRITICAL": 2, "FAILOVER": 3}

# `llm_usage.status` values that never cost the provider's real request quota, and so
# must not cost our internal budget either. A PERMANENT_ERROR is always a config fault
# on our side -- no API key (`missing_api_key`, which never left the process at all),
# a rejected key, or a model name the key can't reach (`model_not_found:...`, see
# `app.llm.gemini._classify_client_error`) -- and Google bills none of those against
# the daily request quota this budget shadows. Counting them anyway burns the day's
# budget on calls that produced no reasoning, which then trips FAILOVER and takes the
# provider out of the chain for a reason that has nothing to do with quota.
#
# Everything else still counts: SUCCESS obviously, QUOTA_EXHAUSTED because it's the
# provider itself saying we spent the quota, and RETRYABLE_ERROR because a timeout or
# 5xx may well have been metered server-side before it failed. (For the USD unit this
# filter changes nothing in practice -- a failed call logs zero tokens, so zero cost.)
_UNBILLED_STATUSES = ("PERMANENT_ERROR",)


def start_of_today_utc() -> datetime:
    """Midnight UTC, naive -- matches `LLMUsage.timestamp`'s storage shape (SQLite's
    `func.now()` server_default writes a naive UTC timestamp), so this can be compared
    directly against that column without a timezone-aware/naive mismatch.

    Public (not `_`-prefixed) so `app.api.routes.llm_usage`'s usage dashboard can
    aggregate over the exact same "today" window this module uses for budget
    tracking, instead of a second, potentially-drifting definition of "today".
    """
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, now.day)


def start_of_month_utc() -> datetime:
    """The first of this month, midnight UTC, naive -- same shape as above."""
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, 1)


@dataclass(frozen=True)
class _Meter:
    unit: QuotaUnit
    daily_limit: float
    monthly_limit: float | None
    warning_threshold: float
    critical_threshold: float


class QuotaManager:
    """Tracks usage per provider against its configured internal budget. Stateless
    itself (no in-memory counters, unlike `HealthManager`) -- every call re-derives
    usage from `llm_usage`, so it stays correct across process restarts and multiple
    workers without any coordination.
    """

    def __init__(self, settings: Settings | None = None, db: Session | None = None) -> None:
        self._settings = settings or get_settings()
        # Optional, same convention as `GeminiProvider`/`AssistantCore`: when absent
        # (e.g. a provider being exercised in isolation with no DB wired up),
        # `current_usage()` reports 0 rather than raising, which makes `status()`
        # report NORMAL/`within_budget()` report True -- i.e. this fails *open*, not
        # closed. A caller that actually cares about budget enforcement must supply a
        # real session.
        self._db = db

    def _meter_for(self, provider: str) -> _Meter | None:
        """How `provider` is metered, or None if it's unmetered (e.g. Ollama)."""
        s = self._settings
        if provider == "gemini":
            return _Meter(
                unit="requests",
                daily_limit=s.gemini_daily_request_budget,
                monthly_limit=None,
                warning_threshold=s.gemini_warning_threshold,
                critical_threshold=s.gemini_critical_threshold,
            )
        if provider == "openai":
            return _Meter(
                unit="usd",
                daily_limit=s.openai_daily_budget_usd,
                monthly_limit=s.openai_monthly_budget_usd,
                warning_threshold=s.openai_warning_threshold,
                critical_threshold=s.openai_critical_threshold,
            )
        return None

    def unit_for(self, provider: str) -> QuotaUnit | None:
        """`"usd"`, `"requests"`, or None for an unmetered provider."""
        meter = self._meter_for(provider)
        return meter.unit if meter else None

    def budget_for(self, provider: str) -> float | None:
        """`provider`'s *daily* limit in its own unit (USD or requests), or None when
        unmetered -- which means "no budget applies", not "zero budget". Lets callers
        outside this module (`app.api.routes.llm_usage`'s usage panel) render a real
        used/limit bar instead of just a status badge.
        """
        meter = self._meter_for(provider)
        return meter.daily_limit if meter else None

    def monthly_budget_for(self, provider: str) -> float | None:
        meter = self._meter_for(provider)
        return meter.monthly_limit if meter else None

    def _usage_since(self, provider: str, since: datetime, unit: QuotaUnit) -> float:
        if self._db is None:
            return 0
        query = self._db.query(
            func.coalesce(func.sum(LLMUsage.cost_usd), 0.0) if unit == "usd" else func.count(LLMUsage.id)
        ).filter(
            LLMUsage.provider == provider,
            LLMUsage.timestamp >= since,
            LLMUsage.status.notin_(_UNBILLED_STATUSES),
        )
        value = query.scalar() or 0
        return float(value) if unit == "usd" else int(value)

    def current_usage(self, provider: str) -> float:
        """`provider`'s *billable* usage since midnight UTC today, in its own unit:
        USD for a USD-metered provider, otherwise a count of billable `llm_usage` rows.

        Every `generate()` call logs a row regardless of outcome (§5), so the request
        unit counts attempts -- but only the ones the real provider quota would have
        charged for. Rows in `_UNBILLED_STATUSES` are excluded: they're config faults
        that cost no provider quota, so charging them to our internal budget would be
        double-punishment (the call already failed *and* the day's budget shrank).

        Note this is deliberately narrower than the request count
        `app.api.routes.llm_usage` reports -- that panel shows every attempt, because
        "how many calls did we make and how many failed" is a different question from
        "how much budget is left".
        """
        meter = self._meter_for(provider)
        return self._usage_since(provider, start_of_today_utc(), meter.unit if meter else "requests")

    def monthly_usage(self, provider: str) -> float:
        meter = self._meter_for(provider)
        return self._usage_since(provider, start_of_month_utc(), meter.unit if meter else "requests")

    @staticmethod
    def _status_for(usage: float, limit: float, meter: _Meter) -> QuotaStatus:
        # limit<=0 means "nothing allowed" -- an immediate failover rather than a
        # division by zero.
        if limit <= 0 or usage >= limit:
            return "FAILOVER"
        used_fraction = usage / limit
        if used_fraction >= meter.critical_threshold:
            return "CRITICAL"
        if used_fraction >= meter.warning_threshold:
            return "WARNING"
        return "NORMAL"

    def status(self, provider: str) -> QuotaStatus:
        """Usage against `provider`'s internal budget, per §8's thresholds -- the
        worse of the daily and (if configured) monthly windows. An unmetered provider
        is always NORMAL.
        """
        meter = self._meter_for(provider)
        if meter is None:
            return "NORMAL"

        status = self._status_for(self.current_usage(provider), meter.daily_limit, meter)
        if meter.monthly_limit is not None and status != "FAILOVER":
            monthly = self._status_for(self.monthly_usage(provider), meter.monthly_limit, meter)
            if _STATUS_SEVERITY[monthly] > _STATUS_SEVERITY[status]:
                status = monthly
        return status

    def within_budget(self, provider: str) -> bool:
        """True unless `provider` has hit FAILOVER -- this is the cheap pre-call
        check `AIRouter` uses to decide whether to even attempt `provider` (§41 Rule
        3: never assume the LLM is available, extended here to "never assume there's
        budget left").
        """
        return self.status(provider) != "FAILOVER"
