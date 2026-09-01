"""
Streaming LLM path (`generate_stream` / `AIRouter.route_stream`, file 05-06 + streaming).

Covers the two contracts everything above this layer relies on:

  1. A stream always terminates with exactly one `LLMStreamChunk(final=...)` -- on
     success *and* on every failure mode. Nothing may raise where the non-streaming
     `generate()` would have returned a classified non-SUCCESS `LLMResult`, because
     `AssistantCore.handle_stream` has already sent HTTP 200 and can't turn an
     exception into an error response any more.
  2. Failover may only happen *before* a provider has emitted text. Once a delta has
     reached the client it has been rendered, so silently restarting on another
     provider would duplicate or contradict what the user is looking at.

The Gemini SDK is mocked at `client.aio.models.generate_content_stream`, the same seam
`test_gemini_provider.py` uses for the non-streaming call -- no network is reachable
from these tests.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from google.genai import errors as genai_errors

from app.config.settings import Settings
from app.llm.ai_router import NO_PROVIDER_AVAILABLE_ERROR_TYPE, AIRouter
from app.llm.base import LLMRequest, LLMResult, LLMStreamChunk
from app.llm.gemini import GeminiProvider
from app.llm.health import HealthManager
from app.llm.provider_manager import ProviderEntry, ProviderManager
from app.llm.quota_manager import QuotaManager


def _settings(**overrides) -> Settings:
    defaults: dict = dict(
        gemini_api_key="test-api-key",
        gemini_model="gemini-3.6-flash",
        gemini_timeout_seconds=30.0,
        gemini_max_retries=1,
        gemini_retry_base_delay_seconds=0.0,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def _chunk(text: str | None = None, function_calls=None, usage=None):
    """A duck-typed stand-in for one `GenerateContentResponse` from the stream."""
    return SimpleNamespace(text=text, function_calls=function_calls or [], usage_metadata=usage)


def _stream_of(*chunks):
    """Wraps canned chunks in the shape the SDK returns: `generate_content_stream` is
    a coroutine that resolves to an async iterator (verified against google-genai
    2.20), so the mock must be awaited before it can be iterated.
    """

    async def _iterator():
        for chunk in chunks:
            yield chunk

    return AsyncMock(return_value=_iterator())


def _provider(stream_mock, settings: Settings | None = None) -> GeminiProvider:
    provider = GeminiProvider(settings=settings or _settings())
    provider._client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content_stream=stream_mock))
    )
    return provider


async def _collect(stream) -> tuple[list[str], LLMResult | None]:
    deltas: list[str] = []
    final: LLMResult | None = None
    async for chunk in stream:
        if chunk.final is not None:
            final = chunk.final
        elif chunk.delta:
            deltas.append(chunk.delta)
    return deltas, final


# --- GeminiProvider.generate_stream -------------------------------------------------


@pytest.mark.asyncio
async def test_text_arrives_as_deltas_and_the_final_result_is_the_whole_reply():
    provider = _provider(_stream_of(_chunk("Hello"), _chunk(" there"), _chunk("!")))

    deltas, final = await _collect(provider.generate_stream(LLMRequest(message="hi")))

    # Incremental, never cumulative -- a consumer concatenates.
    assert deltas == ["Hello", " there", "!"]
    assert final is not None
    assert final.status == "SUCCESS"
    # ...and the terminal result still carries the complete text, so a caller that
    # ignored every delta gets exactly what non-streaming `generate()` would return.
    assert final.text == "Hello there!"


@pytest.mark.asyncio
async def test_tool_calls_and_token_counts_are_collected_across_chunks():
    """Function calls and usage metadata can land on any chunk (usage typically only
    the last), so every chunk is folded in rather than just the final one.
    """
    call = SimpleNamespace(name="get_time", args={"tz": "utc"})
    usage = SimpleNamespace(prompt_token_count=11, candidates_token_count=4)
    provider = _provider(
        _stream_of(_chunk("ok"), _chunk(function_calls=[call]), _chunk(usage=usage))
    )

    _deltas, final = await _collect(provider.generate_stream(LLMRequest(message="time?")))

    assert final is not None
    assert [(c.tool_name, c.params) for c in final.tool_calls] == [("get_time", {"tz": "utc"})]
    assert (final.request_tokens, final.response_tokens) == (11, 4)


@pytest.mark.asyncio
async def test_a_text_less_chunk_does_not_break_the_stream():
    """`chunk.text` raises on a chunk that carries only function calls. That is normal
    mid-stream, so it must be absorbed rather than ending the turn.
    """

    class _Raising:
        function_calls: list = []
        usage_metadata = None

        @property
        def text(self):
            raise ValueError("no text part in this chunk")

    provider = _provider(_stream_of(_chunk("a"), _Raising(), _chunk("b")))

    deltas, final = await _collect(provider.generate_stream(LLMRequest(message="hi")))

    assert deltas == ["a", "b"]
    assert final is not None and final.status == "SUCCESS"


@pytest.mark.asyncio
async def test_missing_api_key_yields_a_classified_final_and_never_raises():
    provider = GeminiProvider(settings=_settings(gemini_api_key=None))

    deltas, final = await _collect(provider.generate_stream(LLMRequest(message="hi")))

    assert deltas == []
    assert final is not None
    assert (final.status, final.error_type) == ("PERMANENT_ERROR", "missing_api_key")


@pytest.mark.asyncio
async def test_a_quota_error_is_classified_not_raised():
    """Same §5 classification as the non-streaming path -- streaming must not turn a
    known error taxonomy into an exception.
    """
    stream = AsyncMock(
        side_effect=genai_errors.ClientError(
            code=429, response_json={"status": "RESOURCE_EXHAUSTED", "message": "boom"}
        )
    )

    deltas, final = await _collect(_provider(stream).generate_stream(LLMRequest(message="hi")))

    assert deltas == []
    assert final is not None and final.status == "QUOTA_EXHAUSTED"


@pytest.mark.asyncio
async def test_a_transport_failure_mid_stream_ends_with_a_retryable_final():
    """Text already emitted is kept, and the failure is reported as the terminal chunk
    rather than propagating out of the generator.
    """

    async def _explodes():
        yield _chunk("partial")
        raise ConnectionResetError("dropped")

    stream = AsyncMock(return_value=_explodes())

    deltas, final = await _collect(_provider(stream).generate_stream(LLMRequest(message="hi")))

    assert deltas == ["partial"]
    assert final is not None
    assert final.status == "RETRYABLE_ERROR"
    assert final.error_type == "stream_error:ConnectionResetError"


@pytest.mark.asyncio
async def test_every_stream_writes_exactly_one_usage_row(test_db, monkeypatch):
    """`generate_stream` must log to `llm_usage` exactly once, same as `generate()` --
    otherwise streaming would be a way to spend provider quota without it being counted
    against the internal budget.
    """
    from app.database.models import LLMUsage

    session = test_db()
    provider = _provider(_stream_of(_chunk("hi")))
    provider._db = session

    await _collect(provider.generate_stream(LLMRequest(message="hi")))

    rows = session.query(LLMUsage).all()
    assert len(rows) == 1
    assert rows[0].provider == "gemini"
    assert rows[0].status == "SUCCESS"
    session.close()


# --- OllamaProvider.generate_stream -------------------------------------------------
#
# Streaming matters more on this provider than on Gemini, not less: Ollama serves
# exactly the turns Gemini couldn't, and a local CPU model routinely takes 15-20s to
# produce a couple of sentences. Those are the slowest replies the assistant ever gives,
# and without streaming they are also the ones that show no sign of life at all.


def _ollama_settings(**overrides) -> Settings:
    defaults: dict = dict(
        ollama_base_url="http://localhost:11434",
        ollama_model="llama3.2",
        ollama_timeout_seconds=30.0,
        ollama_max_retries=1,
        ollama_retry_base_delay_seconds=0.0,
        ollama_availability_timeout_seconds=0.1,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def _patch_ollama_probe_ok(monkeypatch):
    """Make `_probe()` report the configured model as present, without a network call."""
    monkeypatch.setattr(
        "app.llm.ollama.httpx.get",
        lambda *a, **k: SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"models": [{"name": "llama3.2:latest"}]},
        ),
    )


def _patch_ollama_stream(monkeypatch, lines: list[str], status_code: int = 200, text: str = ""):
    """Patch `httpx.AsyncClient` with a fake whose `.stream(...)` replays `lines` as
    Ollama's newline-delimited JSON.
    """

    class _FakeResponse:
        def __init__(self) -> None:
            self.status_code = status_code
            self.text = text

        async def aread(self) -> bytes:
            return text.encode()

        async def aiter_lines(self):
            for line in lines:
                yield line

    class _FakeStreamCtx:
        async def __aenter__(self):
            return _FakeResponse()

        async def __aexit__(self, *exc_info):
            return False

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        def stream(self, method, url, json=None):
            return _FakeStreamCtx()

    monkeypatch.setattr("app.llm.ollama.httpx.AsyncClient", _FakeAsyncClient)


@pytest.mark.asyncio
async def test_ollama_streams_ndjson_content_as_deltas(monkeypatch):
    from app.llm.ollama import OllamaProvider

    _patch_ollama_probe_ok(monkeypatch)
    _patch_ollama_stream(
        monkeypatch,
        [
            '{"message":{"role":"assistant","content":"The sky "},"done":false}',
            '{"message":{"role":"assistant","content":"is blue."},"done":false}',
            '{"message":{"role":"assistant","content":""},"done":true,'
            '"prompt_eval_count":9,"eval_count":5}',
        ],
    )

    provider = OllamaProvider(settings=_ollama_settings())
    deltas, final = await _collect(provider.generate_stream(LLMRequest(message="why?")))

    assert deltas == ["The sky ", "is blue."]
    assert final is not None
    assert final.status == "SUCCESS"
    assert final.text == "The sky is blue."
    assert (final.request_tokens, final.response_tokens) == (9, 5)


@pytest.mark.asyncio
async def test_ollama_stream_requests_streaming_mode(monkeypatch):
    """`_build_payload` hardcodes `"stream": False` for the buffered call -- the
    streaming path has to override it, or Ollama returns one object and the whole
    feature silently degrades to non-streaming.
    """
    from app.llm.ollama import OllamaProvider

    _patch_ollama_probe_ok(monkeypatch)
    sent: dict = {}

    class _FakeResponse:
        status_code = 200
        text = ""

        async def aiter_lines(self):
            yield '{"message":{"content":"hi"},"done":true}'

    class _FakeStreamCtx:
        async def __aenter__(self):
            return _FakeResponse()

        async def __aexit__(self, *exc_info):
            return False

    class _FakeAsyncClient:
        def __init__(self, *a, **k) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        def stream(self, method, url, json=None):
            sent.update(json or {})
            return _FakeStreamCtx()

    monkeypatch.setattr("app.llm.ollama.httpx.AsyncClient", _FakeAsyncClient)

    await _collect(OllamaProvider(settings=_ollama_settings()).generate_stream(LLMRequest(message="hi")))

    assert sent["stream"] is True


@pytest.mark.asyncio
async def test_ollama_tool_calls_are_collected_from_the_stream(monkeypatch):
    from app.llm.ollama import OllamaProvider

    _patch_ollama_probe_ok(monkeypatch)
    _patch_ollama_stream(
        monkeypatch,
        [
            '{"message":{"role":"assistant","content":"",'
            '"tool_calls":[{"function":{"name":"list_tasks","arguments":{"status":"open"}}}]},'
            '"done":true}',
        ],
    )

    _deltas, final = await _collect(
        OllamaProvider(settings=_ollama_settings()).generate_stream(LLMRequest(message="tasks?"))
    )

    assert final is not None
    assert [(c.tool_name, c.params) for c in final.tool_calls] == [
        ("list_tasks", {"status": "open"})
    ]


@pytest.mark.asyncio
async def test_ollama_unreachable_yields_a_permanent_final(monkeypatch):
    from app.llm.ollama import OllamaProvider

    def _refused(*args, **kwargs):
        raise ConnectionError("connection refused")

    monkeypatch.setattr("app.llm.ollama.httpx.get", _refused)

    deltas, final = await _collect(
        OllamaProvider(settings=_ollama_settings()).generate_stream(LLMRequest(message="hi"))
    )

    assert deltas == []
    assert final is not None
    assert (final.status, final.error_type) == ("PERMANENT_ERROR", "ollama_unreachable")


@pytest.mark.asyncio
async def test_ollama_a_malformed_line_does_not_end_the_turn(monkeypatch):
    """A partial/garbled line mid-stream must be skipped -- the terminal `done` object
    may still be on its way.
    """
    from app.llm.ollama import OllamaProvider

    _patch_ollama_probe_ok(monkeypatch)
    _patch_ollama_stream(
        monkeypatch,
        [
            '{"message":{"content":"good"},"done":false}',
            "{not json at all",
            "",
            '{"message":{"content":" end"},"done":true}',
        ],
    )

    deltas, final = await _collect(
        OllamaProvider(settings=_ollama_settings()).generate_stream(LLMRequest(message="hi"))
    )

    assert deltas == ["good", " end"]
    assert final is not None and final.status == "SUCCESS"


@pytest.mark.asyncio
async def test_ollama_stream_logs_exactly_one_usage_row(test_db, monkeypatch):
    from app.database.models import LLMUsage
    from app.llm.ollama import OllamaProvider

    _patch_ollama_probe_ok(monkeypatch)
    _patch_ollama_stream(monkeypatch, ['{"message":{"content":"hi"},"done":true}'])

    session = test_db()
    provider = OllamaProvider(settings=_ollama_settings(), db=session)

    await _collect(provider.generate_stream(LLMRequest(message="hi")))

    rows = session.query(LLMUsage).all()
    assert len(rows) == 1 and rows[0].provider == "ollama"
    session.close()


# --- AIRouter.route_stream ----------------------------------------------------------


class _FakeStreamingProvider:
    """A provider whose stream is scripted: text deltas, then a terminal result."""

    def __init__(self, name: str, deltas: list[str], final: LLMResult) -> None:
        self.name = name
        self._deltas = deltas
        self._final = final
        self.called = False

    def is_available(self) -> bool:
        return True

    async def generate(self, request, *, fallback_used=False):  # pragma: no cover
        raise AssertionError("route_stream must prefer generate_stream when present")

    async def generate_stream(self, request, *, fallback_used=False):
        self.called = True
        for delta in self._deltas:
            yield LLMStreamChunk(delta=delta)
        yield LLMStreamChunk(final=self._final)


class _NonStreamingProvider:
    """A provider with no `generate_stream` at all -- must still take part in the chain."""

    def __init__(self, name: str, result: LLMResult) -> None:
        self.name = name
        self._result = result
        self.called = False

    def is_available(self) -> bool:
        return True

    async def generate(self, request, *, fallback_used=False) -> LLMResult:
        self.called = True
        return self._result


def _router(*providers) -> AIRouter:
    entries = [
        ProviderEntry(provider=provider, priority=index, enabled=True)
        for index, provider in enumerate(providers, start=1)
    ]
    return AIRouter(
        provider_manager=ProviderManager(entries=entries),
        quota_manager=QuotaManager(db=None),
        health_manager=HealthManager(),
    )


@pytest.mark.asyncio
async def test_a_failure_before_any_text_fails_over_silently():
    """Nothing has been shown to the user yet, so switching providers is invisible --
    exactly the behavior the non-streaming `route()` already has.
    """
    broken = _FakeStreamingProvider("gemini", [], LLMResult(status="RETRYABLE_ERROR"))
    backup = _FakeStreamingProvider("ollama", ["from "], LLMResult(status="SUCCESS", text="from ollama"))

    deltas, final = await _collect(_router(broken, backup).route_stream(LLMRequest(message="hi")))

    assert broken.called and backup.called
    assert deltas == ["from "]
    assert final is not None and final.status == "SUCCESS"
    assert final.provider == "ollama"  # stamped with whoever actually answered


@pytest.mark.asyncio
async def test_a_failure_after_text_was_emitted_does_not_fail_over():
    """The critical streaming-specific rule. Restarting on another provider here would
    make the user watch a reply get replaced or duplicated mid-sentence, so the turn
    ends honestly with the failure instead.
    """
    half = _FakeStreamingProvider("gemini", ["I was saying"], LLMResult(status="RETRYABLE_ERROR"))
    backup = _FakeStreamingProvider("ollama", ["ignored"], LLMResult(status="SUCCESS"))

    deltas, final = await _collect(_router(half, backup).route_stream(LLMRequest(message="hi")))

    assert deltas == ["I was saying"]
    assert backup.called is False
    assert final is not None and final.status == "RETRYABLE_ERROR"


@pytest.mark.asyncio
async def test_a_provider_without_generate_stream_is_used_via_generate():
    """Streaming support is optional -- a provider lacking it must not be skipped, it
    just contributes no early text.
    """
    plain = _NonStreamingProvider("ollama", LLMResult(status="SUCCESS", text="buffered reply"))

    deltas, final = await _collect(_router(plain).route_stream(LLMRequest(message="hi")))

    assert plain.called is True
    assert deltas == []
    assert final is not None and final.text == "buffered reply"
    assert final.provider == "ollama"


@pytest.mark.asyncio
async def test_an_exhausted_chain_yields_a_marked_final_rather_than_raising():
    broken = _FakeStreamingProvider("gemini", [], LLMResult(status="PERMANENT_ERROR"))

    _deltas, final = await _collect(_router(broken).route_stream(LLMRequest(message="hi")))

    assert final is not None
    assert final.error_type == NO_PROVIDER_AVAILABLE_ERROR_TYPE


@pytest.mark.asyncio
async def test_quota_and_health_gate_streaming_exactly_as_they_gate_route():
    """Streaming must not become a way around the pre-call budget/health checks."""

    class _OverBudget(QuotaManager):
        def within_budget(self, provider: str) -> bool:
            return provider != "gemini"

    skipped = _FakeStreamingProvider("gemini", ["x"], LLMResult(status="SUCCESS"))
    used = _FakeStreamingProvider("ollama", ["y"], LLMResult(status="SUCCESS"))

    router = AIRouter(
        provider_manager=ProviderManager(
            entries=[
                ProviderEntry(provider=skipped, priority=1, enabled=True),
                ProviderEntry(provider=used, priority=2, enabled=True),
            ]
        ),
        quota_manager=_OverBudget(db=None),
        health_manager=HealthManager(),
    )

    deltas, _final = await _collect(router.route_stream(LLMRequest(message="hi")))

    assert skipped.called is False  # never called, not merely ignored
    assert deltas == ["y"]


@pytest.mark.asyncio
async def test_a_stream_that_ends_without_a_final_is_treated_as_a_failed_attempt():
    """A provider breaking the contract must not produce a half-finished turn that
    silently reads as success.
    """

    class _NoFinal:
        name = "gemini"

        def is_available(self) -> bool:
            return True

        async def generate(self, request, *, fallback_used=False):  # pragma: no cover
            raise AssertionError("not reached")

        async def generate_stream(self, request, *, fallback_used=False):
            yield LLMStreamChunk(delta="oops")

    _deltas, final = await _collect(_router(_NoFinal()).route_stream(LLMRequest(message="hi")))

    assert final is not None
    assert final.error_type == "stream_ended_without_result"
