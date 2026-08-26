"""
OllamaProvider tests (§5, §6, file 07).

Exercises `OllamaProvider`'s availability probe and `generate()`'s error-classification
/retry contract in isolation -- both of Ollama's HTTP seams (`httpx.get` for the
`/api/tags` availability probe, `httpx.AsyncClient.post` for `/api/chat`) are always
faked out here, so these tests never make a real network call and never require an
actual Ollama server running.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.config.settings import Settings
from app.database.models import LLMUsage
from app.llm.base import LLMRequest
from app.llm.ollama import OllamaProvider


def _settings(**overrides) -> Settings:
    """A Settings instance that never reads the repo's real `.env` -- every field
    relevant to OllamaProvider is set explicitly by the caller/defaults below.
    """
    defaults: dict = dict(
        ollama_base_url="http://localhost:11434",
        ollama_model="llama3.2",
        ollama_timeout_seconds=30.0,
        ollama_availability_timeout_seconds=2.0,
        ollama_max_retries=2,
        # Zero backoff -- these tests assert retry *counts*, not real wall-clock delay.
        ollama_retry_base_delay_seconds=0.0,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def _tags_response(model_names: list[str]):
    """A duck-typed stand-in for the `httpx.Response` `is_available()`/`_probe()`
    read via `.raise_for_status()` and `.json()` -- shaped like Ollama's real
    `/api/tags` payload (`{"models": [{"name": ...}, ...]}`).
    """
    return SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"models": [{"name": name} for name in model_names]},
    )


def _patch_tags_probe(monkeypatch, response=None, exc: Exception | None = None):
    """Patches the sync `httpx.get` call `_probe()` makes. Exactly one of
    `response`/`exc` should be given -- a canned response, or an error to raise
    (simulating "Ollama isn't running").
    """

    def fake_get(*args, **kwargs):
        if exc is not None:
            raise exc
        return response

    monkeypatch.setattr("app.llm.ollama.httpx.get", fake_get)


class _FakeChatResponse:
    """A duck-typed stand-in for the `httpx.Response` `_generate()` reads from
    `/api/chat` -- only `.status_code`, `.json()`, and `.text` are ever touched.
    """

    def __init__(self, status_code: int, json_data: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text

    def json(self):
        return self._json_data


def _patch_chat_endpoint(monkeypatch, responses: list):
    """Patches `httpx.AsyncClient` so successive `POST /api/chat` calls (across
    retry attempts) return/raise each item of `responses` in order. Returns the list
    of payloads actually posted, for assertions on retry behavior (e.g. that a
    tool-calling 400 triggers a retry without `tools`).
    """
    queue = list(responses)
    posted_payloads: list[dict] = []

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *exc_info) -> bool:
            return False

        async def post(self, url, json):
            posted_payloads.append(json)
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    monkeypatch.setattr("app.llm.ollama.httpx.AsyncClient", _FakeAsyncClient)
    return posted_payloads


@pytest.mark.asyncio
async def test_success_path_records_correct_token_counts(test_db, monkeypatch):
    session = test_db()
    settings = _settings()
    _patch_tags_probe(monkeypatch, response=_tags_response(["llama3.2:latest"]))
    _patch_chat_endpoint(
        monkeypatch,
        [
            _FakeChatResponse(
                200,
                {
                    "message": {"role": "assistant", "content": "hi there"},
                    "prompt_eval_count": 42,
                    "eval_count": 17,
                },
            )
        ],
    )
    provider = OllamaProvider(settings=settings, db=session)

    result = await provider.generate(LLMRequest(message="hello"))

    assert result.status == "SUCCESS"
    assert result.text == "hi there"
    assert result.request_tokens == 42
    assert result.response_tokens == 17

    rows = session.query(LLMUsage).all()
    assert len(rows) == 1
    assert rows[0].status == "SUCCESS"
    assert rows[0].provider == "ollama"


@pytest.mark.asyncio
async def test_success_path_surfaces_tool_calls(monkeypatch):
    settings = _settings()
    _patch_tags_probe(monkeypatch, response=_tags_response(["llama3.2:latest"]))
    _patch_chat_endpoint(
        monkeypatch,
        [
            _FakeChatResponse(
                200,
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"function": {"name": "get_time", "arguments": {"tz": "UTC"}}}
                        ],
                    }
                },
            )
        ],
    )
    provider = OllamaProvider(settings=settings)

    result = await provider.generate(LLMRequest(message="what time is it"))

    assert result.status == "SUCCESS"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].tool_name == "get_time"
    assert result.tool_calls[0].params == {"tz": "UTC"}


@pytest.mark.asyncio
async def test_server_unreachable_is_permanent_error_without_generate_attempt(monkeypatch):
    settings = _settings()
    _patch_tags_probe(monkeypatch, exc=httpx.ConnectError("connection refused"))
    posted = _patch_chat_endpoint(monkeypatch, [])
    provider = OllamaProvider(settings=settings)

    assert provider.is_available() is False

    result = await provider.generate(LLMRequest(message="hello"))

    assert result.status == "PERMANENT_ERROR"
    assert result.error_type == "ollama_unreachable"
    # Never even attempted /api/chat -- the pre-flight probe short-circuited first.
    assert posted == []


@pytest.mark.asyncio
async def test_model_not_pulled_is_permanent_error(monkeypatch):
    settings = _settings(ollama_model="llama3.2")
    _patch_tags_probe(monkeypatch, response=_tags_response(["mistral:latest"]))
    posted = _patch_chat_endpoint(monkeypatch, [])
    provider = OllamaProvider(settings=settings)

    assert provider.is_available() is False

    result = await provider.generate(LLMRequest(message="hello"))

    assert result.status == "PERMANENT_ERROR"
    assert result.error_type == "model_not_found"
    assert posted == []


@pytest.mark.asyncio
async def test_timeout_is_retried_up_to_configured_limit_then_surfaces_failure(monkeypatch):
    settings = _settings(ollama_max_retries=2)  # 1 initial attempt + 2 retries = 3 total
    _patch_tags_probe(monkeypatch, response=_tags_response(["llama3.2:latest"]))
    posted = _patch_chat_endpoint(
        monkeypatch,
        [httpx.TimeoutException("timed out")] * 3,
    )
    provider = OllamaProvider(settings=settings)

    result = await provider.generate(LLMRequest(message="hello"))

    assert result.status == "RETRYABLE_ERROR"
    assert result.error_type == "timeout"
    assert len(posted) == 3


@pytest.mark.asyncio
async def test_timeout_that_recovers_before_the_limit_still_succeeds(monkeypatch):
    settings = _settings(ollama_max_retries=2)
    _patch_tags_probe(monkeypatch, response=_tags_response(["llama3.2:latest"]))
    posted = _patch_chat_endpoint(
        monkeypatch,
        [
            httpx.TimeoutException("timed out"),
            _FakeChatResponse(200, {"message": {"content": "recovered"}}),
        ],
    )
    provider = OllamaProvider(settings=settings)

    result = await provider.generate(LLMRequest(message="hello"))

    assert result.status == "SUCCESS"
    assert result.text == "recovered"
    assert len(posted) == 2


@pytest.mark.asyncio
async def test_5xx_is_retryable_error(monkeypatch):
    settings = _settings(ollama_max_retries=1)
    _patch_tags_probe(monkeypatch, response=_tags_response(["llama3.2:latest"]))
    posted = _patch_chat_endpoint(
        monkeypatch,
        [
            _FakeChatResponse(503, text="service unavailable"),
            _FakeChatResponse(503, text="service unavailable"),
        ],
    )
    provider = OllamaProvider(settings=settings)

    result = await provider.generate(LLMRequest(message="hello"))

    assert result.status == "RETRYABLE_ERROR"
    assert result.error_type == "server_error_503"
    assert len(posted) == 2


@pytest.mark.asyncio
async def test_tool_calling_unsupported_falls_back_to_plain_text(monkeypatch):
    settings = _settings()
    _patch_tags_probe(monkeypatch, response=_tags_response(["llama3.2:latest"]))
    posted = _patch_chat_endpoint(
        monkeypatch,
        [
            _FakeChatResponse(400, text='{"error":"llama3.2 does not support tools"}'),
            _FakeChatResponse(200, {"message": {"content": "plain text reply"}}),
        ],
    )
    provider = OllamaProvider(settings=settings)
    request = LLMRequest(
        message="what time is it",
        tools=[{"name": "get_time", "description": "Get the time", "parameters": {}}],
    )

    result = await provider.generate(request)

    assert result.status == "SUCCESS"
    assert result.text == "plain text reply"
    assert result.tool_calls == []
    # First attempt included tools, the retry after the 400 dropped them.
    assert "tools" in posted[0]
    assert "tools" not in posted[1]
