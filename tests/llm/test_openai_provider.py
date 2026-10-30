"""
OpenAIProvider tests -- classification, retries, parsing, usage logging, streaming.

Same approach as tests/llm/test_gemini_provider.py: the SDK is never reached. Each test
injects a stub client whose only attribute is `responses.create` (an AsyncMock), so
everything downstream of that one seam -- `_build_params`, `_to_result`, the error
classification and retry loops, `_log_usage` -- runs for real. The privacy guarantees
(store=False, banned params) have their own file, test_openai_privacy.py.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx2
import pytest

from app.config.settings import Settings
from app.database.models import LLMUsage
from app.llm.base import LLMRequest, ToolCallRequest, ToolExchange
from app.llm import openai_guard as openai  # the only module allowed to import the SDK
from app.llm.openai import OpenAIProvider, _strict_object

_REQUEST = httpx2.Request("POST", f"{openai.OPENAI_BASE_URL}/responses")


def _settings(**overrides) -> Settings:
    defaults: dict = dict(
        openai_api_key="test-api-key",
        openai_model="gpt-test-mini",
        openai_max_retries=2,
        openai_retry_base_delay_seconds=0.0,
        openai_input_price_per_mtok=1.0,
        openai_cached_input_price_per_mtok=0.1,
        openai_output_price_per_mtok=4.0,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def _provider(create: AsyncMock, settings: Settings | None = None, db=None) -> OpenAIProvider:
    provider = OpenAIProvider(settings=settings or _settings(), db=db)
    provider._client = SimpleNamespace(responses=SimpleNamespace(create=create))
    return provider


def _message(text: str):
    return SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text=text)])


def _function_call(name: str, arguments: dict | str, call_id: str = "call_1"):
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments)
    return SimpleNamespace(type="function_call", name=name, arguments=arguments, call_id=call_id)


def _response(*output, input_tokens=100, output_tokens=20, cached_tokens=0, status="completed"):
    return SimpleNamespace(
        status=status,
        output=list(output),
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_tokens_details=SimpleNamespace(cached_tokens=cached_tokens),
        ),
    )


def _status_error(cls, status_code: int, code: str | None = None):
    body = {"message": "boom", "code": code} if code else {"message": "boom"}
    return cls("boom", response=httpx2.Response(status_code, request=_REQUEST), body=body)


# --- availability ---------------------------------------------------------------------


def test_is_available_is_a_pure_key_check():
    assert OpenAIProvider(settings=_settings()).is_available() is True
    assert OpenAIProvider(settings=_settings(openai_api_key=None)).is_available() is False


@pytest.mark.asyncio
async def test_missing_key_is_permanent_and_never_calls_the_api():
    create = AsyncMock()
    result = await _provider(create, settings=_settings(openai_api_key=None)).generate(LLMRequest(message="hi"))

    assert result.status == "PERMANENT_ERROR"
    assert result.error_type == "missing_api_key"
    create.assert_not_awaited()


# --- success parsing ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_records_tokens_cached_tokens_and_cost(test_db):
    session = test_db()
    create = AsyncMock(
        return_value=_response(_message("hi there"), input_tokens=1_000, cached_tokens=400, output_tokens=200)
    )

    result = await _provider(create, db=session).generate(LLMRequest(message="hello"))

    assert result.status == "SUCCESS"
    assert result.text == "hi there"
    assert (result.request_tokens, result.response_tokens, result.cached_tokens) == (1_000, 200, 400)

    row = session.query(LLMUsage).one()
    assert row.provider == "openai"
    assert row.model == "gpt-test-mini"
    assert row.cached_tokens == 400
    # 600 uncached x $1 + 400 cached x $0.10 + 200 output x $4, per million tokens.
    assert row.cost_usd == pytest.approx((600 * 1.0 + 400 * 0.1 + 200 * 4.0) / 1_000_000)


@pytest.mark.asyncio
async def test_function_calls_become_tool_calls_with_nulls_dropped_and_json_objects_decoded():
    tools = [
        {
            "name": "run_terminal_command",
            "description": "Run an alias.",
            "parameters": {
                "type": "object",
                "properties": {"alias": {"type": "string"}, "args": {"type": "object"}},
                "required": ["alias"],
            },
        },
        {
            "name": "create_task",
            "description": "Create a task.",
            "parameters": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "due": {"type": "string"}},
                "required": ["title"],
            },
        },
    ]
    create = AsyncMock(
        return_value=_response(
            _function_call("run_terminal_command", {"alias": "ping_host", "args": '{"host": "example.com"}'}, "c1"),
            _function_call("create_task", {"title": "milk", "due": None}, "c2"),
        )
    )

    result = await _provider(create).generate(LLMRequest(message="do things", tools=tools))

    assert [c.tool_name for c in result.tool_calls] == ["run_terminal_command", "create_task"]
    assert result.tool_calls[0].params == {"alias": "ping_host", "args": {"host": "example.com"}}
    assert result.tool_calls[0].call_id == "c1"
    # `due` was null only because strict mode makes optional fields nullable -- it must
    # be absent, not None, or ToolExecutor's type check would reject the call.
    assert result.tool_calls[1].params == {"title": "milk"}
    # The raw function_call items are kept for replay on the next tool-loop step.
    assert [item["type"] for item in result.provider_items] == ["function_call", "function_call"]
    assert result.provider_items[0]["arguments"] == json.dumps(
        {"alias": "ping_host", "args": '{"host": "example.com"}'}
    )


@pytest.mark.asyncio
async def test_encrypted_reasoning_items_are_kept_for_replay():
    reasoning = SimpleNamespace(type="reasoning", id="rs_1", summary=[], encrypted_content="opaque-blob")
    create = AsyncMock(return_value=_response(reasoning, _function_call("get_time", {}, "c1")))

    result = await _provider(create).generate(
        LLMRequest(message="time?", tools=[{"name": "get_time", "parameters": {}}])
    )

    assert result.provider_items[0] == {
        "type": "reasoning",
        "id": "rs_1",
        "summary": [],
        "encrypted_content": "opaque-blob",
    }


# --- strict tool schemas --------------------------------------------------------------


def test_strict_schema_closes_objects_requires_everything_and_nulls_optionals():
    schema, json_encoded = _strict_object(
        {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "priority": {"type": "string", "enum": ["low", "high"]},
                "args": {"type": "object", "description": "Named args."},
            },
            "required": ["title"],
        }
    )

    assert schema["additionalProperties"] is False
    assert schema["required"] == ["title", "priority", "args"]
    assert schema["properties"]["title"] == {"type": "string"}
    assert schema["properties"]["priority"]["type"] == ["string", "null"]
    assert schema["properties"]["priority"]["enum"] == ["low", "high", None]
    # A free-form object can't be strict, so it travels as a JSON string.
    assert schema["properties"]["args"]["type"] == ["string", "null"]
    assert json_encoded == {"args"}


def test_empty_parameter_schema_is_still_a_valid_strict_object():
    schema, _ = _strict_object({})
    assert schema == {"type": "object", "properties": {}, "required": [], "additionalProperties": False}


# --- error classification -------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status", "error_type"),
    [
        (_status_error(openai.RateLimitError, 429, "rate_limit_exceeded"), "QUOTA_EXHAUSTED", "rate_limit_exceeded"),
        (_status_error(openai.RateLimitError, 429, "insufficient_quota"), "QUOTA_EXHAUSTED", "insufficient_quota"),
        (_status_error(openai.AuthenticationError, 401), "PERMANENT_ERROR", "http_401"),
        (_status_error(openai.PermissionDeniedError, 403), "PERMANENT_ERROR", "http_403"),
        (_status_error(openai.BadRequestError, 400, "invalid_value"), "PERMANENT_ERROR", "invalid_value"),
        (_status_error(openai.NotFoundError, 404), "PERMANENT_ERROR", "model_not_found:gpt-test-mini"),
    ],
)
async def test_non_retryable_errors_are_classified_and_never_retried(error, status, error_type):
    create = AsyncMock(side_effect=error)

    result = await _provider(create).generate(LLMRequest(message="hi"))

    assert result.status == status
    assert result.error_type == error_type
    assert create.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "error_type"),
    [
        (openai.APITimeoutError(request=_REQUEST), "timeout"),
        (openai.APIConnectionError(request=_REQUEST), "network_error:APIConnectionError"),
        (_status_error(openai.InternalServerError, 500), "server_error_500"),
        (_status_error(openai.InternalServerError, 503), "server_error_503"),
    ],
)
async def test_retryable_errors_are_retried_up_to_max_retries(error, error_type):
    create = AsyncMock(side_effect=error)

    result = await _provider(create, settings=_settings(openai_max_retries=2)).generate(LLMRequest(message="hi"))

    assert result.status == "RETRYABLE_ERROR"
    assert result.error_type == error_type
    assert create.await_count == 3


@pytest.mark.asyncio
async def test_retry_recovers_when_a_later_attempt_succeeds():
    create = AsyncMock(side_effect=[openai.APITimeoutError(request=_REQUEST), _response(_message("ok"))])

    result = await _provider(create).generate(LLMRequest(message="hi"))

    assert result.status == "SUCCESS"
    assert result.text == "ok"
    assert create.await_count == 2


@pytest.mark.asyncio
async def test_failed_call_still_logs_one_usage_row_with_zero_cost(test_db):
    session = test_db()
    create = AsyncMock(side_effect=_status_error(openai.RateLimitError, 429, "rate_limit_exceeded"))

    await _provider(create, db=session).generate(LLMRequest(message="hi"))

    row = session.query(LLMUsage).one()
    assert row.status == "QUOTA_EXHAUSTED"
    assert row.cost_usd == 0.0


# --- tool-loop input ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_exchanges_are_sent_back_as_function_call_outputs():
    create = AsyncMock(return_value=_response(_message("It's 9pm.")))
    reasoning = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "blob"}
    function_call = {"type": "function_call", "call_id": "c1", "name": "get_time", "arguments": "{}"}
    exchange = ToolExchange(
        calls=[ToolCallRequest(tool_name="get_time", params={}, call_id="c1")],
        outputs=['{"success": true, "data": {"message": "21:00"}}'],
        provider="openai",
        provider_items=[reasoning, function_call],
    )

    await _provider(create).generate(LLMRequest(message="time?", tool_exchanges=[exchange]))

    items = create.await_args.kwargs["input"]
    assert items[-3:] == [
        reasoning,
        function_call,
        {"type": "function_call_output", "call_id": "c1", "output": '{"success": true, "data": {"message": "21:00"}}'},
    ]


@pytest.mark.asyncio
async def test_exchange_from_another_provider_is_synthesized_from_calls():
    create = AsyncMock(return_value=_response(_message("done")))
    exchange = ToolExchange(
        calls=[ToolCallRequest(tool_name="list_tasks", params={"status": "pending"})],
        outputs=['{"success": true}'],
        provider="ollama",
    )

    await _provider(create).generate(LLMRequest(message="tasks?", tool_exchanges=[exchange]))

    items = create.await_args.kwargs["input"]
    assert items[-2]["type"] == "function_call"
    assert items[-2]["name"] == "list_tasks"
    assert json.loads(items[-2]["arguments"]) == {"status": "pending"}
    assert items[-1] == {"type": "function_call_output", "call_id": items[-2]["call_id"], "output": '{"success": true}'}


@pytest.mark.asyncio
async def test_final_step_keeps_tools_declared_but_forbids_calls():
    create = AsyncMock(return_value=_response(_message("ok")))

    await _provider(create).generate(
        LLMRequest(message="hi", tools=[{"name": "get_time", "parameters": {}}], allow_tool_calls=False)
    )

    kwargs = create.await_args.kwargs
    assert [tool["name"] for tool in kwargs["tools"]] == ["get_time"]
    assert kwargs["tool_choice"] == "none"


# --- prompt layout --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_is_static_instructions_then_context_then_turns_then_message():
    create = AsyncMock(return_value=_response(_message("ok")))
    context = {
        "user_id": "alice",
        "platform": "web",
        "current_time": "Monday",
        "recent_turns": [{"role": "user", "content": "earlier q"}, {"role": "assistant", "content": "earlier a"}],
    }

    await _provider(create).generate(LLMRequest(message="now", context=context))
    first = create.await_args.kwargs
    await _provider(create).generate(LLMRequest(message="other", context={**context, "current_time": "Tuesday"}))
    second = create.await_args.kwargs

    # Static prefix: byte-identical instructions regardless of the request.
    assert first["instructions"] == second["instructions"]
    assert "Monday" not in first["instructions"]

    items = first["input"]
    assert items[0]["role"] == "developer" and "Monday" in items[0]["content"]
    assert "alice" not in items[0]["content"]  # the model has no use for the username
    assert items[1] == {"role": "user", "content": "earlier q"}
    assert items[2] == {"role": "assistant", "content": "earlier a"}
    assert items[3] == {"role": "user", "content": "now"}

    # OPENAI-RULES.md 3.2: no prompt_cache_key (it would carry an identifier).
    assert "prompt_cache_key" not in first


@pytest.mark.asyncio
async def test_tools_are_sorted_for_a_stable_prefix():
    create = AsyncMock(return_value=_response(_message("ok")))
    tools = [{"name": "zeta", "parameters": {}}, {"name": "alpha", "parameters": {}}]

    await _provider(create).generate(LLMRequest(message="hi", tools=tools))

    assert [t["name"] for t in create.await_args.kwargs["tools"]] == ["alpha", "zeta"]
    assert all(t["strict"] is True for t in create.await_args.kwargs["tools"])


@pytest.mark.asyncio
async def test_reasoning_models_request_encrypted_reasoning():
    create = AsyncMock(return_value=_response(_message("ok")))

    await _provider(create, settings=_settings(openai_model="gpt-5.4-mini", openai_reasoning_effort="low")).generate(
        LLMRequest(message="hi")
    )
    kwargs = create.await_args.kwargs
    assert kwargs["include"] == ["reasoning.encrypted_content"]
    assert kwargs["reasoning"] == {"effort": "low"}

    await _provider(create, settings=_settings(openai_model="gpt-4.1-mini")).generate(LLMRequest(message="hi"))
    assert "include" not in create.await_args.kwargs


# --- streaming ------------------------------------------------------------------------


class _Stream:
    """An async iterable of stream events, optionally raising part-way through."""

    def __init__(self, events, raise_after: Exception | None = None) -> None:
        self._events = events
        self._raise_after = raise_after

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for event in self._events:
            yield event
        if self._raise_after is not None:
            raise self._raise_after


def _delta(text: str):
    return SimpleNamespace(type="response.output_text.delta", delta=text)


def _completed(response):
    return SimpleNamespace(type="response.completed", response=response)


async def _collect(provider: OpenAIProvider, request: LLMRequest):
    deltas, final = [], None
    async for chunk in provider.generate_stream(request):
        if chunk.final is not None:
            final = chunk.final
        else:
            deltas.append(chunk.delta)
    return deltas, final


@pytest.mark.asyncio
async def test_stream_yields_deltas_then_one_final_and_logs_once(test_db):
    session = test_db()
    create = AsyncMock(
        return_value=_Stream([_delta("Hel"), _delta("lo"), _completed(_response(_message("Hello"), input_tokens=7))])
    )

    deltas, final = await _collect(_provider(create, db=session), LLMRequest(message="hi"))

    assert deltas == ["Hel", "lo"]
    assert final.status == "SUCCESS"
    assert final.text == "Hello"
    assert final.request_tokens == 7
    assert create.await_args.kwargs["stream"] is True
    assert session.query(LLMUsage).count() == 1


@pytest.mark.asyncio
async def test_stream_surfaces_tool_calls_in_the_final_chunk():
    create = AsyncMock(return_value=_Stream([_completed(_response(_function_call("get_time", {}, "c9")))]))

    deltas, final = await _collect(
        _provider(create), LLMRequest(message="time?", tools=[{"name": "get_time", "parameters": {}}])
    )

    assert deltas == []
    assert final.tool_calls == [ToolCallRequest(tool_name="get_time", params={}, call_id="c9")]


@pytest.mark.asyncio
async def test_stream_retries_a_retryable_failure_before_any_output():
    create = AsyncMock(
        side_effect=[openai.APITimeoutError(request=_REQUEST), _Stream([_delta("ok"), _completed(_response(_message("ok")))])]
    )

    deltas, final = await _collect(_provider(create), LLMRequest(message="hi"))

    assert deltas == ["ok"]
    assert final.status == "SUCCESS"
    assert create.await_count == 2


@pytest.mark.asyncio
async def test_stream_never_retries_after_text_was_emitted():
    create = AsyncMock(return_value=_Stream([_delta("partial")], raise_after=openai.APIConnectionError(request=_REQUEST)))

    deltas, final = await _collect(_provider(create), LLMRequest(message="hi"))

    assert deltas == ["partial"]
    assert final.status == "RETRYABLE_ERROR"
    assert create.await_count == 1


@pytest.mark.asyncio
async def test_stream_failed_event_is_classified():
    failed = SimpleNamespace(type="response.failed", response=SimpleNamespace(error=SimpleNamespace(code="rate_limit_exceeded")))
    create = AsyncMock(return_value=_Stream([failed]))

    _deltas, final = await _collect(_provider(create), LLMRequest(message="hi"))

    assert final.status == "QUOTA_EXHAUSTED"
    assert create.await_count == 1


@pytest.mark.asyncio
async def test_stream_quota_error_at_connect_is_not_retried():
    create = AsyncMock(side_effect=_status_error(openai.RateLimitError, 429, "insufficient_quota"))

    _deltas, final = await _collect(_provider(create), LLMRequest(message="hi"))

    assert final.status == "QUOTA_EXHAUSTED"
    assert create.await_count == 1
