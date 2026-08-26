"""
Provider health tracking (§6, §37 Phase 5, file 06).

`AIRouter` (file 06) needs to know, before it ever calls a provider, whether that
provider is worth calling at all -- this module is the provider-agnostic bookkeeping
that answers that question. It has no opinion on *which* provider to pick or how to
fail over; it only tracks, per provider name, "is this thing currently healthy" based
on the `LLMResult`s `AIRouter` feeds it after each call.

Nothing here makes network calls or knows about a specific vendor's SDK -- that
classification already happened in the provider module (e.g. `app.llm.gemini`'s
`_classify_client_error`) before an `LLMResult` ever reaches `record_result()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum

from app.config.settings import Settings, get_settings
from app.llm.base import LLMResult


class ProviderHealthState(str, Enum):
    """§6's taxonomy. `AIRouter` uses this (via `is_usable()`) to decide which
    provider to try next -- it never inspects `LLMResult.status` itself.
    """

    AVAILABLE = "AVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    UNAVAILABLE = "UNAVAILABLE"
    MISCONFIGURED = "MISCONFIGURED"
    DISABLED = "DISABLED"


# States a provider only leaves via an explicit fix (a successful call, for
# MISCONFIGURED, or `HealthManager.reset()`, for DISABLED) -- time passing alone never
# clears them, unlike QUOTA_EXHAUSTED/RATE_LIMITED/UNAVAILABLE's `cooldown_until`.
_STICKY_STATES = {ProviderHealthState.MISCONFIGURED, ProviderHealthState.DISABLED}


@dataclass
class ProviderStatus:
    """Current health snapshot for one provider. `healthy`/`quota_state` are the
    coarse, human-facing summary (see §6's example `Gemini: healthy: true, quota_state:
    NORMAL`); `state` is what `AIRouter`/`is_usable()` actually key off of.
    """

    provider: str
    state: ProviderHealthState = ProviderHealthState.AVAILABLE
    healthy: bool = True
    quota_state: str = "NORMAL"
    last_error: str | None = None
    cooldown_until: datetime | None = None

    # Internal counters -- not part of §6's example, but needed to tell "one blip"
    # from "repeated failures" without a second data structure per provider. Reset to
    # zero on every SUCCESS.
    consecutive_retryable_errors: int = field(default=0, repr=False)
    consecutive_permanent_errors: int = field(default=0, repr=False)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class HealthManager:
    """Tracks one `ProviderStatus` per provider name. Stateless across process
    restarts by design (in-memory only) -- health is a live signal about the current
    process's recent calls, not something that needs to survive a restart.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._statuses: dict[str, ProviderStatus] = {}

    def get_status(self, provider_name: str) -> ProviderStatus:
        """Never raises for an unknown provider -- one hasn't failed yet, so it
        starts out AVAILABLE, same as `ProviderStatus`'s defaults.
        """
        return self._statuses.setdefault(provider_name, ProviderStatus(provider=provider_name))

    def record_result(self, provider_name: str, llm_result: LLMResult) -> ProviderStatus:
        """Update `provider_name`'s status from one `LLMResult`. Called once per
        `AIRouter` call attempt, success or failure alike -- mirrors
        `GeminiProvider.generate()`'s "log every outcome" convention in
        `app.llm.gemini`.
        """
        status = self.get_status(provider_name)

        if llm_result.status == "SUCCESS":
            self._record_success(status)
        elif llm_result.status == "QUOTA_EXHAUSTED":
            self._record_quota_exhausted(status, llm_result)
        elif llm_result.status == "RETRYABLE_ERROR":
            self._record_retryable_error(status, llm_result)
        elif llm_result.status == "PERMANENT_ERROR":
            self._record_permanent_error(status, llm_result)

        return status

    def _record_success(self, status: ProviderStatus) -> None:
        # A successful call is unambiguous proof the provider works right now --
        # clears every failure signal, including a still-sticky MISCONFIGURED (a
        # success can only happen if whatever was misconfigured got fixed).
        status.state = ProviderHealthState.AVAILABLE
        status.healthy = True
        status.quota_state = "NORMAL"
        status.last_error = None
        status.cooldown_until = None
        status.consecutive_retryable_errors = 0
        status.consecutive_permanent_errors = 0

    def _record_quota_exhausted(self, status: ProviderStatus, llm_result: LLMResult) -> None:
        # §5: never retry QUOTA_EXHAUSTED -- fail over instead, and don't try this
        # provider again until the cooldown window elapses.
        status.state = ProviderHealthState.QUOTA_EXHAUSTED
        status.healthy = False
        status.quota_state = "EXHAUSTED"
        status.last_error = llm_result.error_type
        status.cooldown_until = _utcnow() + timedelta(
            seconds=self._settings.llm_quota_cooldown_seconds
        )
        # A quota result doesn't imply anything about transient/config health --
        # leave those counters alone so an unrelated later error still counts fresh.

    def _record_retryable_error(self, status: ProviderStatus, llm_result: LLMResult) -> None:
        status.last_error = llm_result.error_type
        status.consecutive_retryable_errors += 1

        if status.consecutive_retryable_errors >= self._settings.llm_retryable_error_threshold:
            # One timeout/network blip is noise; repeated ones mean the provider
            # itself is currently unreliable -- park it for a short cooldown rather
            # than let `AIRouter` keep spending latency/budget retrying it.
            status.state = ProviderHealthState.UNAVAILABLE
            status.healthy = False
            status.cooldown_until = _utcnow() + timedelta(
                seconds=self._settings.llm_retryable_cooldown_seconds
            )
        # Below threshold: record the error but don't flip the provider's state --
        # it's still the preferred choice until it proves itself unreliable.

    def _record_permanent_error(self, status: ProviderStatus, llm_result: LLMResult) -> None:
        status.last_error = llm_result.error_type
        status.consecutive_permanent_errors += 1
        status.healthy = False
        # §5: never retry PERMANENT_ERROR -- and unlike QUOTA_EXHAUSTED/
        # RETRYABLE_ERROR, there's no cooldown to wait out (a bad API key doesn't fix
        # itself with time), so `cooldown_until` stays unset here.

        if status.consecutive_permanent_errors >= self._settings.llm_permanent_error_threshold:
            # Kept failing the same way even after we'd have expected a fix (or a
            # human noticing) -- stop bothering this provider until `reset()`.
            status.state = ProviderHealthState.DISABLED
        else:
            status.state = ProviderHealthState.MISCONFIGURED

    def is_usable(self, provider_name: str) -> bool:
        """True unless `provider_name` is mid-cooldown or in a bad (non-AVAILABLE)
        state. `AIRouter` calls this *before* attempting a provider (§41 Rule 3: never
        assume the LLM is available) -- it's a pure in-memory check, no network I/O.
        """
        status = self.get_status(provider_name)

        if status.cooldown_until is not None:
            if _utcnow() < status.cooldown_until:
                return False
            # Cooldown elapsed -- give the provider another chance rather than
            # leaving it stuck in a stale bad state forever. Sticky states
            # (MISCONFIGURED/DISABLED) never carry a cooldown, so this only ever
            # restores QUOTA_EXHAUSTED/RATE_LIMITED/UNAVAILABLE.
            status.state = ProviderHealthState.AVAILABLE
            status.healthy = True
            status.quota_state = "NORMAL"
            status.cooldown_until = None
            status.consecutive_retryable_errors = 0

        return status.state not in _STICKY_STATES

    def reset(self, provider_name: str) -> None:
        """Manually clear `provider_name` back to a fresh AVAILABLE status -- the
        only way out of DISABLED short of a process restart (e.g. an operator fixing
        config and explicitly asking the router to give it another try).
        """
        self._statuses[provider_name] = ProviderStatus(provider=provider_name)
