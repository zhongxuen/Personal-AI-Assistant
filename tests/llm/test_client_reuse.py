"""
Event-loop-scoped LLM client caching (`app.llm.clients`, performance).

The behavior under test is the fix for the single largest avoidable cost in a turn.
`AssistantCore` builds a fresh `AIRouter` -> `ProviderManager` -> `GeminiProvider` on
every request, so a client cached on the provider instance was rebuilt per message --
and rebuilding an HTTP client means a new connection pool, so a fresh DNS lookup, TCP
handshake and TLS negotiation before any of the actual request goes out.

These tests never construct a real `google.genai.Client`; the factory is a counter, so
what's asserted is precisely the caching contract (`get_loop_client`'s call count and
identity guarantees), not anything about the SDK.
"""

from __future__ import annotations

import asyncio

import pytest

from app.config.settings import Settings
from app.llm.clients import get_loop_client, reset_clients
from app.llm.gemini import GeminiProvider


@pytest.fixture(autouse=True)
def _clean_client_cache():
    """The cache is process-wide by design, so tests must not inherit each other's
    entries (or leak into the rest of the suite).
    """
    reset_clients()
    yield
    reset_clients()


class _CountingFactory:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> object:
        self.calls += 1
        return object()


@pytest.mark.asyncio
async def test_same_loop_reuses_one_client():
    """The point of the whole module: repeated lookups on one loop build the client
    once. In production this is what keeps the provider connection warm across
    requests instead of re-handshaking per message.
    """
    factory = _CountingFactory()

    first = get_loop_client("gemini", factory)
    second = get_loop_client("gemini", factory)
    third = get_loop_client("gemini", factory)

    assert first is second is third
    assert factory.calls == 1


@pytest.mark.asyncio
async def test_namespaces_do_not_share_a_client():
    """Unrelated clients sharing a loop must stay separate -- Gemini's SDK client and
    an Ollama HTTP client are not interchangeable just because they were built on the
    same loop.
    """
    factory = _CountingFactory()

    gemini = get_loop_client("gemini", factory)
    ollama = get_loop_client("ollama", factory)

    assert gemini is not ollama
    assert factory.calls == 2


def test_a_different_loop_gets_its_own_client():
    """Clients must never be shared *across* loops: an async client's pooled
    connections are registered with the loop that opened them, so handing one to a
    different loop surfaces as "Event loop is closed" or a silent hang.

    Two separate `asyncio.run()` calls are exactly the throwaway-loop case the sync
    `AssistantCore.handle()` shim produces, so this pins the behavior that keeps that
    path safe rather than merely slow.
    """
    factory = _CountingFactory()
    seen = []

    async def _lookup():
        seen.append(get_loop_client("gemini", factory))

    asyncio.run(_lookup())
    asyncio.run(_lookup())

    assert factory.calls == 2
    assert seen[0] is not seen[1]


def test_closed_loops_do_not_accumulate_entries():
    """Evicting entries for dead loops is what stops the throwaway-loop path from
    leaking a client per call over a long-running process (or a long test run).
    """
    factory = _CountingFactory()

    async def _lookup():
        get_loop_client("gemini", factory)

    for _ in range(5):
        asyncio.run(_lookup())

    # Every lookup was on a since-closed loop, so at most the most recent entry can
    # still be held -- not one per call.
    from app.llm.clients import _clients

    assert len(_clients) <= 1


@pytest.mark.asyncio
async def test_gemini_provider_instances_share_the_loop_client():
    """The end-to-end version of the first test, at the level that actually matters:
    two *separate* `GeminiProvider` instances -- what two consecutive requests really
    produce -- must resolve to the same underlying client.

    `_get_client` is exercised for real here; only `Client` itself is replaced, so no
    SDK object is constructed and no network call is possible.
    """
    built = []

    class _FakeClient:
        def __init__(self, api_key: str) -> None:
            built.append(api_key)

    import app.llm.gemini as gemini_module

    original = gemini_module.Client
    gemini_module.Client = _FakeClient
    try:
        settings = Settings(_env_file=None, gemini_api_key="k")
        first = GeminiProvider(settings=settings)._get_client()
        second = GeminiProvider(settings=settings)._get_client()
    finally:
        gemini_module.Client = original

    assert first is second
    assert built == ["k"]  # constructed exactly once across both providers


@pytest.mark.asyncio
async def test_an_injected_client_still_wins():
    """`self._client` remains an override seam -- the whole test suite stubs the SDK
    through it, and caching must not quietly bypass an explicitly-provided client.
    """
    provider = GeminiProvider(settings=Settings(_env_file=None, gemini_api_key="k"))
    stub = object()
    provider._client = stub  # type: ignore[assignment]

    assert provider._get_client() is stub


@pytest.mark.asyncio
async def test_a_changed_api_key_does_not_reuse_the_old_key_s_client():
    """The cache key includes the API key, so rotating `GEMINI_API_KEY` can't leave
    requests going out authenticated with the previous one.
    """
    built = []

    class _FakeClient:
        def __init__(self, api_key: str) -> None:
            built.append(api_key)

    import app.llm.gemini as gemini_module

    original = gemini_module.Client
    gemini_module.Client = _FakeClient
    try:
        old = GeminiProvider(settings=Settings(_env_file=None, gemini_api_key="old"))._get_client()
        new = GeminiProvider(settings=Settings(_env_file=None, gemini_api_key="new"))._get_client()
    finally:
        gemini_module.Client = original

    assert old is not new
    assert built == ["old", "new"]
