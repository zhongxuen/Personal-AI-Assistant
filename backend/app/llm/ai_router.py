"""
AI Router -- provider chain failover (§6, §8, §37 Phase 5, file 06).

`AssistantCore` no longer calls a single provider directly (that was file 05's
temporary shortcut); it calls `AIRouter.route()`, which walks `ProviderManager`'s
chain in priority order and returns the first `SUCCESS`, failing over to the next
provider on any other status. This is the single choke point where quota (§8,
`QuotaManager`) and health (§6, `HealthManager`) checks happen *before* a provider is
ever called, and where every attempt's outcome is fed back into `HealthManager` and
logged to `llm_usage` (via each provider's own `generate()`, see `app.llm.gemini`).

`ProviderManager` registers `GeminiProvider` (priority 1) and `OllamaProvider`
(priority 2, file 07) -- this module has no provider-specific branches for either;
adding a third provider requires no changes here, only another `ProviderEntry` in
`ProviderManager`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from sqlalchemy.orm import Session

from app.llm.base import LLMProvider, LLMRequest, LLMResult, LLMStreamChunk
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
                # Stamp who actually answered -- only the router knows which chain entry
                # this came from, and the caller needs it to report the right provider.
                result.provider = provider.name
                return result
            # else: continue to the next provider in the chain.

        logger.warning("AIRouter exhausted the provider chain -- no provider available.")
        return LLMResult(status="PERMANENT_ERROR", error_type=NO_PROVIDER_AVAILABLE_ERROR_TYPE)

    async def route_stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamChunk]:
        """Streaming counterpart to `route()` -- identical chain walk, identical quota
        and health gating, identical fallback accounting; the only difference is that
        text is forwarded as it arrives instead of after the call completes.

        Failover has one extra rule streaming forces: a provider can only be abandoned
        for the next one in the chain *before it has emitted any text*. Once a delta has
        gone out, the client has already rendered it, so silently restarting on another
        provider would either duplicate or contradict what the user is looking at. A
        provider that fails after emitting text therefore ends the stream with its own
        non-SUCCESS `final` chunk, and `AssistantCore` surfaces that honestly (§41 Rule
        3) rather than pretending the turn can still be rescued.

        A provider with no `generate_stream` is not skipped -- its `generate()` is
        awaited and delivered as a single terminal chunk, so it still participates fully
        in the chain and just contributes no early text (see `LLMProvider`).
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

            fallback_used = attempts > 0
            attempts += 1

            emitted_text = False
            final: LLMResult | None = None

            async for chunk in self._provider_stream(provider, request, fallback_used):
                if chunk.final is not None:
                    final = chunk.final
                    break
                if chunk.delta:
                    emitted_text = True
                    yield chunk

            if final is None:
                # A provider that ended its stream without a terminal chunk is breaking
                # the contract in `LLMProvider.generate_stream`. Treat it as a failed
                # attempt rather than trusting a half-finished turn.
                logger.warning(
                    "Provider '%s' ended its stream without a final result.", provider.name
                )
                final = LLMResult(
                    status="RETRYABLE_ERROR", error_type="stream_ended_without_result"
                )

            self._health_manager.record_result(provider.name, final)

            if final.status == "SUCCESS" or emitted_text:
                final.provider = provider.name
                yield LLMStreamChunk(final=final)
                return
            # else: nothing was shown to the user yet, so failing over to the next
            # provider is still invisible to them -- continue the chain.

        logger.warning("AIRouter exhausted the provider chain -- no provider available.")
        yield LLMStreamChunk(
            final=LLMResult(
                status="PERMANENT_ERROR", error_type=NO_PROVIDER_AVAILABLE_ERROR_TYPE
            )
        )

    @staticmethod
    def _provider_stream(
        provider: LLMProvider, request: LLMRequest, fallback_used: bool
    ) -> AsyncIterator[LLMStreamChunk]:
        """`provider.generate_stream(...)` when it has one, otherwise its plain
        `generate()` adapted into a one-chunk stream -- so `route_stream` above has a
        single shape to consume and no per-provider branching (the same reason
        `route()` has none).
        """
        streamer = getattr(provider, "generate_stream", None)
        if streamer is not None:
            return streamer(request, fallback_used=fallback_used)

        async def _buffered() -> AsyncIterator[LLMStreamChunk]:
            result = await provider.generate(request, fallback_used=fallback_used)
            yield LLMStreamChunk(final=result)

        return _buffered()
