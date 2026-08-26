"""
Gemini -> Ollama failover matrix (§5, §6, §8, files 06-07).

Exercises `AIRouter.route()` against the *real* `GeminiProvider`/`OllamaProvider`
classes (not `FakeProvider` stand-ins like `test_ai_router.py` uses) so the chain
walking and each provider's own availability/classification logic are proven to work
together end to end. Both providers' underlying HTTP calls are always mocked out --
Gemini's `client.aio.models.generate_content` (same seam as `test_gemini_provider.py`)
and Ollama's `httpx.get`/`httpx.AsyncClient.post` (same seam as
`test_ollama_provider.py`) -- so none of this ever requires a real `GEMINI_API_KEY` or
an actual Ollama server running, in CI or locally.

Covers the four required scenarios:
  1. Gemini healthy -> Gemini answers, Ollama is never called, fallback_used=False.
  2. Gemini QUOTA_EXHAUSTED -> AIRouter automatically tries Ollama next in the same
     request (no user intervention), Ollama succeeds, fallback_used=True.
  3. Gemini.is_available() is False -> AIRouter/GeminiProvider never attempt the
     underlying Gemini HTTP call at all, Ollama is used directly.
  4. Both providers unavailable/failing -> AIRouter returns a graceful, clearly-marked
     result without raising and without a blank SUCCESS.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from google.genai import errors as genai_errors

from app.config.settings import Settings
from app.database.models import LLMUsage
from app.llm.ai_router import NO_PROVIDER_AVAILABLE_ERROR_TYPE, AIRouter
from app.llm.base import LLMRequest
from app.llm.gemini import GeminiProvider
from app.llm.health import HealthManager
from app.llm.ollama import OllamaProvider
from app.llm.provider_manager import ProviderEntry, ProviderManager
from app.llm.quota_manager import QuotaManager

# ---------------------------------------------------------------------------
# Settings / provider construction helpers -- same conventions as
# test_gemini_provider.py / test_ollama_provider.py, duplicated here rather than
# imported so this file's scenarios stay self-contained and readable in isolation.
# ---------------------------------------------------------------------------


def _settings(**overrides) -> Settings:
    defaults: dict = dict(
        gemini_api_key="test-api-key",
        gemini_model="gemini-2.5-flash",
        gemini_timeout_seconds=30.0,
        gemini_max_retries=2,
        gemini_retry_base_delay_seconds=0.0,
        ollama_enabled=True,
        ollama_base_url="http://localhost:11434",
        ollama_model="llama3.2",
        ollama_timeout_seconds=30.0,
        ollama_availability_timeout_seconds=2.0,
        ollama_max_retries=2,
        ollama_retry_base_delay_seconds=0.0,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def _fake_gemini_response(text: str = "hello", request_tokens: int = 10, response_tokens: int = 5):
    """Duck-typed stand-in for `genai_types.GenerateContentResponse` -- see
    `test_gemini_provider.py` for what `_to_result` actually reads off it.
    """
    return SimpleNamespace(
        text=text,
        function_calls=[],
        usage_metadata=SimpleNamespace(
            prompt_token_count=request_tokens,
            candidates_token_count=response_tokens,
        ),
    )


def _client_error(code: int, status: str) -> genai_errors.ClientError:
    return genai_errors.ClientError(code=code, response_json={"status": status, "message": "boom"})


def _gemini_provider(settings: Settings, generate_content: AsyncMock, db=None) -> GeminiProvider:
    """A `GeminiProvider` whose SDK client is a stub -- setting `_client` directly
    skips `_get_client()`'s lazy `Client(...)` construction, so no real SDK client (and
    no real network transport) is ever built, matching `test_gemini_provider.py`.
    """
    provider = GeminiProvider(settings=settings, db=db)
    provider._client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )
    return provider


def _tags_response(model_names: list[str]):
    return SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"models": [{"name": name} for name in model_names]},
    )


def _patch_ollama_tags_probe(monkeypatch, response=None, exc: Exception | None = None):
    def fake_get(*args, **kwargs):
        if exc is not None:
            raise exc
        return response

    monkeypatch.setattr("app.llm.ollama.httpx.get", fake_get)


class _FakeOllamaChatResponse:
    def __init__(self, status_code: int, json_data: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text

    def json(self):
        return self._json_data


def _patch_ollama_chat_endpoint(monkeypatch, responses: list):
    queue = list(responses)

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *exc_info) -> bool:
            return False

        async def post(self, url, json):
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    monkeypatch.setattr("app.llm.ollama.httpx.AsyncClient", _FakeAsyncClient)


def _block_ollama_network(monkeypatch):
    """Ollama must never be reached at all in scenario 1 -- any attempted call to its
    availability probe or chat endpoint fails the test instead of silently succeeding.
    """

    def _blocked_get(*args, **kwargs):
        raise AssertionError("Ollama should never have been probed in this scenario.")

    class _BlockedAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self) -> "_BlockedAsyncClient":
            return self

        async def __aexit__(self, *exc_info) -> bool:
            return False

        async def post(self, *args, **kwargs):
            raise AssertionError("Ollama should never have been called in this scenario.")

    monkeypatch.setattr("app.llm.ollama.httpx.get", _blocked_get)
    monkeypatch.setattr("app.llm.ollama.httpx.AsyncClient", _BlockedAsyncClient)


def _router(gemini: GeminiProvider, ollama: OllamaProvider) -> AIRouter:
    manager = ProviderManager(
        entries=[
            ProviderEntry(provider=gemini, priority=1, enabled=True),
            ProviderEntry(provider=ollama, priority=2, enabled=True),
        ]
    )
    return AIRouter(
        provider_manager=manager,
        quota_manager=QuotaManager(db=None),  # no db -> always within budget
        health_manager=HealthManager(),
    )


# ---------------------------------------------------------------------------
# 1. Gemini healthy -> Gemini is used, Ollama is never called.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gemini_healthy_is_used_and_ollama_never_called(test_db, monkeypatch):
    session = test_db()
    settings = _settings()
    _block_ollama_network(monkeypatch)

    generate_content = AsyncMock(return_value=_fake_gemini_response(text="gemini answer"))
    gemini = _gemini_provider(settings, generate_content, db=session)
    ollama = OllamaProvider(settings=settings, db=session)
    router = _router(gemini, ollama)

    result = await router.route(LLMRequest(message="hello"))

    assert result.status == "SUCCESS"
    assert result.text == "gemini answer"
    generate_content.assert_awaited_once()

    rows = session.query(LLMUsage).all()
    assert len(rows) == 1
    assert rows[0].provider == "gemini"
    assert rows[0].fallback_used is False


# ---------------------------------------------------------------------------
# 2. Gemini QUOTA_EXHAUSTED -> AIRouter automatically calls Ollama in the same
#    request, no user intervention/manual provider switch required.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gemini_quota_exhausted_fails_over_to_ollama_in_same_request(test_db, monkeypatch):
    session = test_db()
    settings = _settings()

    generate_content = AsyncMock(side_effect=_client_error(429, "RESOURCE_EXHAUSTED"))
    gemini = _gemini_provider(settings, generate_content, db=session)

    ollama = OllamaProvider(settings=settings, db=session)
    _patch_ollama_tags_probe(monkeypatch, response=_tags_response(["llama3.2:latest"]))
    _patch_ollama_chat_endpoint(
        monkeypatch,
        [_FakeOllamaChatResponse(200, {"message": {"content": "ollama answer"}})],
    )

    router = _router(gemini, ollama)

    result = await router.route(LLMRequest(message="hello"))

    assert result.status == "SUCCESS"
    assert result.text == "ollama answer"

    rows = {row.provider: row for row in session.query(LLMUsage).all()}
    assert rows["gemini"].status == "QUOTA_EXHAUSTED"
    assert rows["gemini"].fallback_used is False
    assert rows["ollama"].status == "SUCCESS"
    assert rows["ollama"].fallback_used is True


# ---------------------------------------------------------------------------
# 3. Gemini is_available() is False -> AIRouter/GeminiProvider skip straight to
#    Ollama without ever attempting the underlying Gemini call.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gemini_unavailable_skips_straight_to_ollama(test_db, monkeypatch):
    session = test_db()
    # No API key configured -> GeminiProvider.is_available() is False, a pure local
    # check (§41 Rule 3) with no network call involved.
    settings = _settings(gemini_api_key=None)

    generate_content = AsyncMock(side_effect=AssertionError("Gemini should never be called."))
    gemini = _gemini_provider(settings, generate_content, db=session)
    assert gemini.is_available() is False

    ollama = OllamaProvider(settings=settings, db=session)
    _patch_ollama_tags_probe(monkeypatch, response=_tags_response(["llama3.2:latest"]))
    _patch_ollama_chat_endpoint(
        monkeypatch,
        [_FakeOllamaChatResponse(200, {"message": {"content": "ollama answer"}})],
    )

    router = _router(gemini, ollama)

    result = await router.route(LLMRequest(message="hello"))

    assert result.status == "SUCCESS"
    assert result.text == "ollama answer"
    generate_content.assert_not_awaited()

    rows = {row.provider: row for row in session.query(LLMUsage).all()}
    assert rows["gemini"].status == "PERMANENT_ERROR"
    assert rows["gemini"].error_type == "missing_api_key"
    assert rows["ollama"].status == "SUCCESS"


# ---------------------------------------------------------------------------
# 4. Both providers unavailable/failing -> a graceful, clearly-marked result --
#    never an exception, never a silent/blank SUCCESS.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_both_providers_down_returns_graceful_result_without_raising(test_db, monkeypatch):
    session = test_db()
    settings = _settings(gemini_api_key=None)

    generate_content = AsyncMock(side_effect=AssertionError("Gemini should never be called."))
    gemini = _gemini_provider(settings, generate_content, db=session)

    ollama = OllamaProvider(settings=settings, db=session)
    _patch_ollama_tags_probe(monkeypatch, exc=httpx.ConnectError("connection refused"))

    router = _router(gemini, ollama)

    result = await router.route(LLMRequest(message="hello"))  # must not raise

    assert result.status != "SUCCESS"
    assert result.text == ""
    assert result.error_type == NO_PROVIDER_AVAILABLE_ERROR_TYPE

    rows = {row.provider: row for row in session.query(LLMUsage).all()}
    assert rows["gemini"].status == "PERMANENT_ERROR"
    assert rows["ollama"].status == "PERMANENT_ERROR"
    assert rows["ollama"].error_type == "ollama_unreachable"
