"""
AIRouter tests (§6, §8, §37 Phase 5, file 06).

Exercises the chain-walking/failover logic in isolation, against fake providers (no
real Gemini/network calls -- `test_gemini_provider.py` covers that provider's own
classification logic). Covers: first-success short-circuits the chain, a failing
provider fails over to the next, quota/health checks skip a provider without ever
calling it, an exhausted chain returns a clearly-marked result instead of raising, and
`fallback_used` is only True for provider calls beyond the first one actually
attempted for a request.
"""

from __future__ import annotations

import pytest

from app.llm.ai_router import NO_PROVIDER_AVAILABLE_ERROR_TYPE, AIRouter
from app.llm.base import LLMRequest, LLMResult
from app.llm.health import HealthManager, ProviderHealthState
from app.llm.provider_manager import ProviderEntry, ProviderManager
from app.llm.quota_manager import QuotaManager


class FakeProvider:
    """Records every `generate()` call's `fallback_used` flag and returns queued
    results in order -- a stand-in `LLMProvider` with no real SDK/network behind it.
    """

    def __init__(self, name: str, results: list[LLMResult]) -> None:
        self.name = name
        self._results = list(results)
        self.calls: list[bool] = []

    def is_available(self) -> bool:
        return True

    async def generate(self, request: LLMRequest, *, fallback_used: bool = False) -> LLMResult:
        self.calls.append(fallback_used)
        return self._results.pop(0)


def _manager(*providers: FakeProvider) -> ProviderManager:
    entries = [
        ProviderEntry(provider=provider, priority=index, enabled=True)
        for index, provider in enumerate(providers, start=1)
    ]
    return ProviderManager(entries=entries)


def _router(*providers: FakeProvider, quota: QuotaManager | None = None, health: HealthManager | None = None) -> AIRouter:
    return AIRouter(
        provider_manager=_manager(*providers),
        quota_manager=quota or QuotaManager(db=None),  # no db -> always within budget
        health_manager=health or HealthManager(),
    )


@pytest.mark.asyncio
async def test_first_provider_success_short_circuits_the_chain():
    gemini = FakeProvider("gemini", [LLMResult(status="SUCCESS", text="hi")])
    unused = FakeProvider("unused", [LLMResult(status="SUCCESS", text="should never run")])
    router = _router(gemini, unused)

    result = await router.route(LLMRequest(message="hello"))

    assert result.status == "SUCCESS"
    assert result.text == "hi"
    assert gemini.calls == [False]
    assert unused.calls == []


@pytest.mark.asyncio
async def test_failing_first_provider_fails_over_to_the_next_with_fallback_used_true():
    first = FakeProvider("first", [LLMResult(status="RETRYABLE_ERROR", error_type="timeout")])
    second = FakeProvider("second", [LLMResult(status="SUCCESS", text="recovered")])
    router = _router(first, second)

    result = await router.route(LLMRequest(message="hello"))

    assert result.status == "SUCCESS"
    assert result.text == "recovered"
    assert first.calls == [False]
    assert second.calls == [True]


@pytest.mark.asyncio
async def test_every_provider_failing_returns_no_provider_available_without_raising():
    first = FakeProvider("first", [LLMResult(status="PERMANENT_ERROR", error_type="bad_key")])
    second = FakeProvider("second", [LLMResult(status="QUOTA_EXHAUSTED", error_type="resource_exhausted")])
    router = _router(first, second)

    result = await router.route(LLMRequest(message="hello"))

    assert result.status == "PERMANENT_ERROR"
    assert result.error_type == NO_PROVIDER_AVAILABLE_ERROR_TYPE


@pytest.mark.asyncio
async def test_provider_over_quota_budget_is_skipped_not_called():
    over_budget = FakeProvider("over_budget", [LLMResult(status="SUCCESS", text="should never run")])
    fallback = FakeProvider("fallback", [LLMResult(status="SUCCESS", text="ok")])

    class _AlwaysOverBudget:
        def within_budget(self, provider: str) -> bool:
            return provider != "over_budget"

    router = _router(over_budget, fallback, quota=_AlwaysOverBudget())

    result = await router.route(LLMRequest(message="hello"))

    assert result.status == "SUCCESS"
    assert result.text == "ok"
    assert over_budget.calls == []
    # The provider actually called is still the first *attempted* one -- not a
    # fallback from a real call, since nothing was ever called before it.
    assert fallback.calls == [False]


@pytest.mark.asyncio
async def test_unhealthy_provider_is_skipped_not_called():
    unhealthy = FakeProvider("unhealthy", [LLMResult(status="SUCCESS", text="should never run")])
    healthy = FakeProvider("healthy", [LLMResult(status="SUCCESS", text="ok")])
    health = HealthManager()
    health.get_status("unhealthy").state = ProviderHealthState.DISABLED

    router = _router(unhealthy, healthy, health=health)

    result = await router.route(LLMRequest(message="hello"))

    assert result.status == "SUCCESS"
    assert unhealthy.calls == []
    assert healthy.calls == [False]


@pytest.mark.asyncio
async def test_result_is_recorded_with_health_manager_for_every_attempted_provider():
    first = FakeProvider("first", [LLMResult(status="RETRYABLE_ERROR", error_type="timeout")])
    second = FakeProvider("second", [LLMResult(status="SUCCESS", text="ok")])
    health = HealthManager()
    router = _router(first, second, health=health)

    await router.route(LLMRequest(message="hello"))

    assert health.get_status("first").consecutive_retryable_errors == 1
    assert health.get_status("second").state == ProviderHealthState.AVAILABLE


# Today's real chain (`ProviderManager`'s default) is Gemini alone -- these three
# cover that exact shape: no second provider exists yet for the router to fail over
# to, so a skip or a failure must still come back as a graceful, non-crashing result
# rather than raising or hanging (§41 Rule 3).
class TestOnlyGeminiRegistered:
    @pytest.mark.asyncio
    async def test_successful_call_returns_immediately_with_fallback_used_false(self):
        gemini = FakeProvider("gemini", [LLMResult(status="SUCCESS", text="hi")])
        router = _router(gemini)

        result = await router.route(LLMRequest(message="hello"))

        assert result.status == "SUCCESS"
        assert result.text == "hi"
        assert gemini.calls == [False]

    @pytest.mark.asyncio
    async def test_over_budget_gemini_is_never_called_and_result_is_graceful(self):
        gemini = FakeProvider("gemini", [LLMResult(status="SUCCESS", text="should never run")])

        class _AlwaysOverBudget:
            def within_budget(self, provider: str) -> bool:
                return False

        router = _router(gemini, quota=_AlwaysOverBudget())

        result = await router.route(LLMRequest(message="hello"))

        assert gemini.calls == []
        assert result.status == "PERMANENT_ERROR"
        assert result.error_type == NO_PROVIDER_AVAILABLE_ERROR_TYPE

    @pytest.mark.asyncio
    async def test_quota_exhausted_updates_health_manager_and_result_is_graceful(self):
        gemini = FakeProvider(
            "gemini", [LLMResult(status="QUOTA_EXHAUSTED", error_type="resource_exhausted")]
        )
        health = HealthManager()
        router = _router(gemini, health=health)

        result = await router.route(LLMRequest(message="hello"))

        assert gemini.calls == [False]
        assert health.get_status("gemini").state == ProviderHealthState.QUOTA_EXHAUSTED
        assert health.is_usable("gemini") is False
        # No second provider exists yet -- the chain is exhausted, but the router
        # still returns a clearly-marked result instead of raising.
        assert result.status == "PERMANENT_ERROR"
        assert result.error_type == NO_PROVIDER_AVAILABLE_ERROR_TYPE
