"""
AI Router -- provider chain failover (§6, §8, §37 Phase 5, file 06).

`AssistantCore` no longer calls a single provider directly (that was file 05's
temporary shortcut); it calls `AIRouter.route()`, which walks `ProviderManager`'s
chain in priority order and returns the first `SUCCESS`, failing over to the next
provider on any other status. This is the single choke point where quota (§8,
`QuotaManager`) and health (§6, `HealthManager`) checks happen *before* a provider is
ever called, and where every attempt's outcome is fed back into `HealthManager` and
logged to `llm_usage` (via each provider's own `generate()`, see `app.llm.gemini`).

Only `GeminiProvider` is registered today, so failover never actually triggers yet --
but the chain-walking/skip/fallback-logging logic here must already be correct for
when file 07 adds `OllamaProvider` as a second chain entry.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.llm.base import LLMRequest, LLMResult
from app.llm.health import HealthManager
from app.llm.provider_manager import ProviderManager
from app.llm.quota_manager import QuotaManager

logger = logging.getLogger(__name__)

# Returned when every provider in the chain was skipped or failed. Not a real
# provider's classification -- a chain-level condition `AssistantCore` must still turn
# into an honest, non-crashing message (§41 Rule 3), same as any other non-SUCCESS
# status.
NO_PROVIDER_AVAILABLE_ERROR_TYPE = "no_provider_available"


class AIRouter:
    """Provider-agnostic failover across whatever chain `ProviderManager` hands back.
    Holds no provider-specific logic itself -- that all lives in the provider modules
    (`app.llm.gemini`, etc.) and in `QuotaManager`/`HealthManager`.
    """

    def __init__(
        self,
        provider_manager: ProviderManager | None = None,
        quota_manager: QuotaManager | None = None,
        health_manager: HealthManager | None = None,
        settings=None,
        db: Session | None = None,
    ) -> None:
        self._provider_manager = provider_manager or ProviderManager(settings=settings, db=db)
        self._quota_manager = quota_manager or QuotaManager(settings=settings, db=db)
        self._health_manager = health_manager or HealthManager(settings=settings)

    async def route(self, request: LLMRequest) -> LLMResult:
        """Try each enabled provider in priority order; return the first SUCCESS.

        For each provider:
          1. Skip (never call) it if it's over its internal quota budget or
             currently unhealthy (§41 Rule 3 -- never assume a provider is usable).
          2. Otherwise call `generate()` -- which logs to `llm_usage` itself (file
             05's convention) -- marking `fallback_used=True` for every provider
             beyond the first one actually attempted for this request.
          3. Feed the result into `HealthManager` regardless of outcome.
          4. Return immediately on SUCCESS; otherwise continue to the next provider.

        If the chain is exhausted (every provider skipped or failed), returns a
        clearly-marked "no provider available" result instead of raising.
        """
        chain = self._provider_manager.get_chain()
        attempts = 0

        for provider in chain:
            if not self._quota_manager.within_budget(provider.name):
                logger.info("Skipping provider '%s': over its internal quota budget.", provider.name)
                continue
            if not self._health_manager.is_usable(provider.name):
                logger.info("Skipping provider '%s': currently unhealthy.", provider.name)
                continue

            # Every provider actually attempted after the first counts as a fallback,
            # regardless of *why* earlier providers were skipped/failed.
            fallback_used = attempts > 0
            attempts += 1

            result = await provider.generate(request, fallback_used=fallback_used)
            self._health_manager.record_result(provider.name, result)

            if result.status == "SUCCESS":
                return result
            # else: continue to the next provider in the chain.

        logger.warning("AIRouter exhausted the provider chain -- no provider available.")
        return LLMResult(status="PERMANENT_ERROR", error_type=NO_PROVIDER_AVAILABLE_ERROR_TYPE)
