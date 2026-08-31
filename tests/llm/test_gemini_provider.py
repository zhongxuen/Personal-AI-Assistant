"""
GeminiProvider tests (§5, file 05).

Exercises `GeminiProvider.generate()`'s error-classification/retry contract in
isolation -- the Gemini SDK/HTTP layer is always mocked out (nothing here ever makes a
real network call), so these tests assert `GeminiProvider`'s own logic: correct
`LLMStatus` classification per §5's taxonomy, that only RETRYABLE_ERROR is retried (and
only up to `gemini_max_retries`), and that `is_available()` is a pure local check.

`client.aio.models.generate_content` is the one seam mocked per test -- everything
downstream of it (`_to_result`, `_classify_client_error`, the retry loop in
`_generate()`) runs for real.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from google.genai import errors as genai_errors

from app.config.settings import Settings
from app.database.models import LLMUsage
from app.llm.base import LLMRequest
from app.llm.gemini import GeminiProvider


def _settings(**overrides) -> Settings:
    """A Settings instance that never reads the repo's real `.env` -- every field
    relevant to GeminiProvider is set explicitly by the caller/defaults below.
    """
    defaults: dict = dict(
        gemini_api_key="test-api-key",
        gemini_model="gemini-3.6-flash",
        gemini_timeout_seconds=30.0,
        gemini_max_retries=2,
        # Zero backoff -- these tests assert retry *counts*, not real wall-clock delay.
        gemini_retry_base_delay_seconds=0.0,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def _fake_response(text: str = "hello", tool_calls: list | None = None, request_tokens: int = 10, response_tokens: int = 5):
    """A duck-typed stand-in for `genai_types.GenerateContentResponse` -- `_to_result`
    only ever reads `.text`, `.function_calls`, and `.usage_metadata.*_token_count`, so
    a `SimpleNamespace` is enough without constructing the real (heavier) SDK type.
    """
    return SimpleNamespace(
        text=text,
        function_calls=tool_calls or [],
        usage_metadata=SimpleNamespace(
            prompt_token_count=request_tokens,
            candidates_token_count=response_tokens,
        ),
    )


def _client_error(code: int, status: str) -> genai_errors.ClientError:
    return genai_errors.ClientError(code=code, response_json={"status": status, "message": "boom"})


def _provider(settings: Settings, generate_content: AsyncMock, db=None) -> GeminiProvider:
    """A GeminiProvider whose SDK client is a stub -- setting `_client` directly skips
    `_get_client()`'s lazy `Client(...)` construction so no real SDK client (and no
    real network transport) is ever built.
    """
    provider = GeminiProvider(settings=settings, db=db)
    provider._client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )
    return provider


@pytest.mark.asyncio
async def test_success_path_records_correct_token_counts(test_db):
    session = test_db()
    settings = _settings()
    generate_content = AsyncMock(
        return_value=_fake_response(text="hi there", request_tokens=42, response_tokens=17)
    )
    provider = _provider(settings, generate_content, db=session)

    result = await provider.generate(LLMRequest(message="hello"))

    assert result.status == "SUCCESS"
    assert result.text == "hi there"
    assert result.request_tokens == 42
    assert result.response_tokens == 17
    assert generate_content.await_count == 1

    rows = session.query(LLMUsage).all()
    assert len(rows) == 1
    assert rows[0].status == "SUCCESS"
    assert rows[0].request_tokens == 42
    assert rows[0].response_tokens == 17
    assert rows[0].provider == "gemini"


@pytest.mark.asyncio
async def test_success_path_surfaces_tool_calls():
    settings = _settings()
    call = SimpleNamespace(name="get_time", args={"tz": "UTC"})
    generate_content = AsyncMock(return_value=_fake_response(tool_calls=[call]))
    provider = _provider(settings, generate_content)

    result = await provider.generate(LLMRequest(message="what time is it"))

    assert result.status == "SUCCESS"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].tool_name == "get_time"
    assert result.tool_calls[0].params == {"tz": "UTC"}


@pytest.mark.asyncio
async def test_quota_exhausted_is_not_retried():
    settings = _settings(gemini_max_retries=2)
    generate_content = AsyncMock(side_effect=_client_error(429, "RESOURCE_EXHAUSTED"))
    provider = _provider(settings, generate_content)

    result = await provider.generate(LLMRequest(message="hello"))

    assert result.status == "QUOTA_EXHAUSTED"
    # Only the one attempt -- §5: quota errors fail over, they never retry.
    assert generate_content.await_count == 1


@pytest.mark.asyncio
async def test_timeout_is_retried_up_to_configured_limit_then_surfaces_failure():
    settings = _settings(gemini_max_retries=2)  # 1 initial attempt + 2 retries = 3 total
    generate_content = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
    provider = _provider(settings, generate_content)

    result = await provider.generate(LLMRequest(message="hello"))

    assert result.status == "RETRYABLE_ERROR"
    assert generate_content.await_count == 3


@pytest.mark.asyncio
async def test_timeout_that_recovers_before_the_limit_still_succeeds():
    settings = _settings(gemini_max_retries=2)
    generate_content = AsyncMock(
        side_effect=[httpx.TimeoutException("timed out"), _fake_response(text="recovered")]
    )
    provider = _provider(settings, generate_content)

    result = await provider.generate(LLMRequest(message="hello"))

    assert result.status == "SUCCESS"
    assert result.text == "recovered"
    assert generate_content.await_count == 2


@pytest.mark.parametrize("missing_key", [None, ""])
@pytest.mark.asyncio
async def test_missing_api_key_is_permanent_error_without_network_call(missing_key):
    settings = _settings(gemini_api_key=missing_key)
    generate_content = AsyncMock()
    provider = GeminiProvider(settings=settings)
    # Never assign a stub client -- if generate() reached the SDK it would try to
    # build a real `Client` and fail/hang; asserting the mock is never touched proves
    # is_available()'s False short-circuits before any network call.
    provider._get_client = generate_content  # type: ignore[method-assign]

    assert provider.is_available() is False

    result = await provider.generate(LLMRequest(message="hello"))

    assert result.status == "PERMANENT_ERROR"
    assert result.error_type == "missing_api_key"
    generate_content.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_api_key_client_error_is_permanent_and_not_retried():
    settings = _settings(gemini_max_retries=2)
    generate_content = AsyncMock(side_effect=_client_error(401, "UNAUTHENTICATED"))
    provider = _provider(settings, generate_content)

    result = await provider.generate(LLMRequest(message="hello"))

    assert result.status == "PERMANENT_ERROR"
    assert generate_content.await_count == 1


@pytest.mark.asyncio
async def test_unknown_model_404_is_permanent_and_names_the_configured_model():
    """A 404 means `gemini_model` names something this key can't reach -- the one 4xx
    that points at our own config, so `error_type` has to carry the model name or the
    status page just shows an unactionable "NOT_FOUND".
    """
    settings = _settings(gemini_model="gemini-does-not-exist", gemini_max_retries=2)
    generate_content = AsyncMock(side_effect=_client_error(404, "NOT_FOUND"))
    provider = _provider(settings, generate_content)

    result = await provider.generate(LLMRequest(message="hello"))

    assert result.status == "PERMANENT_ERROR"
    assert result.error_type == "model_not_found:gemini-does-not-exist"
    # Never retried: the same model name against the same key can't start existing.
    assert generate_content.await_count == 1
