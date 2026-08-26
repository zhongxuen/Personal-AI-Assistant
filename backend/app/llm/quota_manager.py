"""
LLM usage budget tracking (§8, §37 Phase 5, file 06).

Implements an *internal* request budget, deliberately kept below whatever the
provider's real quota happens to be (§41 Rule 9: never hardcode the actual provider
quota -- it can change without notice). `AIRouter` uses `within_budget()` *before*
attempting a call, so a day that's already near its self-imposed budget fails over to
the next provider instead of spending real quota finding that out the hard way.

Only Gemini has a configured budget right now (`gemini_daily_request_budget` and
friends in `app.config.settings`) -- Ollama is local/free per the development plan
("no cloud request quota"), so any provider this module doesn't recognize is treated
as unmetered (`status()` always NORMAL, `within_budget()` always True) rather than
raising. When Ollama or another cloud provider grows its own budget, extend
`_budget_for()` rather than hardcoding "gemini" deeper into this module.

Nothing here makes network calls -- like `HealthManager`, this is pure bookkeeping
against `llm_usage` rows `GeminiProvider`/`AIRouter` already write.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from sqlalchemy.orm import Session

from app.config.settings import Settings, get_settings
from app.database.models import LLMUsage

# §8's example table: NORMAL -> WARNING (80%) -> CRITICAL (90%) -> FAILOVER (100%+).
QuotaStatus = Literal["NORMAL", "WARNING", "CRITICAL", "FAILOVER"]


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


class QuotaManager:
    """Tracks today's request usage per provider against its configured internal
    budget. Stateless itself (no in-memory counters, unlike `HealthManager`) -- every
    call re-derives usage from `llm_usage`, so it stays correct across process
    restarts and multiple workers without any coordination.
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

    def _budget_for(self, provider: str) -> int | None:
        """The configured daily request budget for `provider`, or None if this
        provider isn't budget-tracked (currently: anything other than "gemini").
        """
        if provider == "gemini":
            return self._settings.gemini_daily_request_budget
        return None

    def budget_for(self, provider: str) -> int | None:
        """Public wrapper around `_budget_for` -- lets callers outside this module
        (e.g. `app.api.routes.llm_usage`'s usage panel) report the raw budget number
        alongside `status()`, so the frontend can render an actual used/limit bar
        instead of just a NORMAL/WARNING/CRITICAL/FAILOVER badge. None means
        unmetered (e.g. Ollama, which is local/free per the development plan), not
        "zero budget".
        """
        return self._budget_for(provider)

    def current_usage(self, provider: str) -> int:
        """Count of `provider`'s `llm_usage` rows since midnight UTC today -- every
        `generate()` call logs a row regardless of outcome (§5), so this counts
        *attempts*, matching what the real provider quota would have charged for.
        """
        if self._db is None:
            return 0
        return (
            self._db.query(LLMUsage)
            .filter(LLMUsage.provider == provider, LLMUsage.timestamp >= start_of_today_utc())
            .count()
        )

    def status(self, provider: str) -> QuotaStatus:
        """Today's usage against `provider`'s internal budget, per §8's thresholds.
        An unmetered provider (no configured budget) is always NORMAL.
        """
        budget = self._budget_for(provider)
        if budget is None:
            return "NORMAL"

        usage = self.current_usage(provider)

        # budget<=0 means "no cloud requests allowed today" -- treat that as an
        # immediate failover rather than dividing by zero.
        if budget <= 0 or usage >= budget:
            return "FAILOVER"

        used_fraction = usage / budget
        if used_fraction >= self._settings.gemini_critical_threshold:
            return "CRITICAL"
        if used_fraction >= self._settings.gemini_warning_threshold:
            return "WARNING"
        return "NORMAL"

    def within_budget(self, provider: str) -> bool:
        """True unless `provider` has hit FAILOVER -- this is the cheap pre-call
        check `AIRouter` uses to decide whether to even attempt `provider` (§41 Rule
        3: never assume the LLM is available, extended here to "never assume there's
        budget left").
        """
        return self.status(provider) != "FAILOVER"
