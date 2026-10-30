"""
OpenAI privacy guarantees -- these tests are the enforcement for the rules in
`app/llm/openai.py`'s module docstring. If one fails, treat it as a privacy bug, not a
flaky test.

  1. Every request that leaves `OpenAIProvider` carries `store=False` -- every request
     shape (plain, streaming, tools, tool-loop steps, attachments, reasoning, retries).
  2. Nothing server-side is used: no `previous_response_id`, `conversation`,
     `background`, stored `prompt`; no Files / vector stores / Conversations /
     Assistants / threads endpoints; attachments travel inline, never as a `file_id`.
  3. `metadata` is never sent.

Runtime checks use a stub client that exposes *only* `responses.create` -- touching any
other endpoint (files, vector_stores, conversations, beta, ...) fails the test -- and
inspect the exact kwargs each call received. A static check then pins the source so a
future edit can't add a second, unguarded call site.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx2
import openai
import pytest

from app.config.settings import Settings
from app.llm.base import LLMAttachment, LLMRequest, ToolCallRequest, ToolExchange
from app.llm.openai import OpenAIProvider

BACKEND_APP = Path(__file__).resolve().parents[2] / "backend" / "app"
OPENAI_MODULE = BACKEND_APP / "llm" / "openai.py"

BANNED_PARAMS = {"previous_response_id", "conversation", "background", "prompt", "metadata"}
BANNED_ENDPOINTS = {"files", "uploads", "vector_stores", "conversations", "beta", "assistants", "threads", "containers"}

_REQUEST = httpx2.Request("POST", "https://api.openai.com/v1/responses")


class _OnlyResponsesCreate:
    """A client stub with exactly one usable endpoint. Any other attribute access --
    `client.files`, `client.vector_stores`, `client.conversations`, `client.beta`, ...
    -- is a privacy violation and fails loudly."""

    def __init__(self, create: AsyncMock) -> None:
        object.__setattr__(self, "responses", _OnlyCreate(create))

    def __getattr__(self, name: str):
        raise AssertionError(f"OpenAIProvider touched client.{name} -- only responses.create is allowed.")


class _OnlyCreate:
    def __init__(self, create: AsyncMock) -> None:
        object.__setattr__(self, "create", create)

    def __getattr__(self, name: str):
        raise AssertionError(f"OpenAIProvider touched client.responses.{name} -- only create is allowed.")


def _settings(**overrides) -> Settings:
    defaults: dict = dict(
        openai_api_key="test-api-key",
        openai_model="gpt-5.4-mini",
        openai_max_retries=1,
        openai_retry_base_delay_seconds=0.0,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def _provider(create: AsyncMock, settings: Settings | None = None) -> OpenAIProvider:
    provider = OpenAIProvider(settings=settings or _settings())
    provider._client = _OnlyResponsesCreate(create)
    return provider


def _ok_response():
    return SimpleNamespace(
        status="completed",
        output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text="ok")])],
        usage=None,
    )


class _Stream:
    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        yield SimpleNamespace(type="response.completed", response=_ok_response())


def _walk_keys(value, path=""):
    """Every (path, key, value) in a nested dict/list structure."""
    if isinstance(value, dict):
        for key, inner in value.items():
            yield path, key, inner
            yield from _walk_keys(inner, f"{path}.{key}")
    elif isinstance(value, list):
        for i, inner in enumerate(value):
            yield from _walk_keys(inner, f"{path}[{i}]")


def assert_private(kwargs: dict) -> None:
    """The invariant every outgoing request must satisfy."""
    assert kwargs.get("store") is False, f"request sent without store=False: store={kwargs.get('store')!r}"
    leaked = BANNED_PARAMS.intersection(kwargs)
    assert not leaked, f"request used banned parameter(s): {sorted(leaked)}"
    for path, key, _value in _walk_keys(kwargs):
        assert key not in ("file_id", "vector_store_ids", "previous_response_id"), f"banned key {key!r} at {path}"
    # Attachments, when present, are inline data -- never a reference to an upload.
    for _path, key, value in _walk_keys(kwargs):
        if key == "file_data":
            assert str(value).startswith("data:"), "input_file must carry inline base64 data"


_TOOLS = [
    {
        "name": "create_task",
        "description": "Create a task.",
        "parameters": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "due": {"type": "string"}},
            "required": ["title"],
        },
    }
]
_ATTACHMENTS = [
    LLMAttachment(filename="report.pdf", mime_type="application/pdf", data=b"%PDF-1.4 test"),
    LLMAttachment(filename="photo.png", mime_type="image/png", data=b"\x89PNG\r\n\x1a\nrest"),
    LLMAttachment(filename="notes.md", mime_type="text/markdown", data=b"# notes"),
]
_EXCHANGE_OPENAI = ToolExchange(
    calls=[ToolCallRequest(tool_name="create_task", params={"title": "x"}, call_id="c1")],
    outputs=['{"success": true}'],
    provider="openai",
    provider_items=[
        {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "blob"},
        {"type": "function_call", "call_id": "c1", "name": "create_task", "arguments": '{"title": "x"}'},
    ],
)
_EXCHANGE_OTHER = ToolExchange(
    calls=[ToolCallRequest(tool_name="create_task", params={"title": "y"})],
    outputs=['{"success": true}'],
    provider="ollama",
)

# Every request shape OpenAIProvider can produce.
REQUEST_SHAPES = {
    "plain": LLMRequest(message="hi"),
    "with_context_and_user": LLMRequest(
        message="hi",
        context={"user_id": "alice", "platform": "web", "memory": {"likes": "tea"}, "recent_turns": [{"role": "user", "content": "q"}]},
    ),
    "with_tools": LLMRequest(message="add milk", tools=_TOOLS),
    "final_step_no_tool_calls": LLMRequest(message="add milk", tools=_TOOLS, allow_tool_calls=False),
    "tool_loop_step_openai": LLMRequest(message="add", tools=_TOOLS, tool_exchanges=[_EXCHANGE_OPENAI]),
    "tool_loop_step_failed_over": LLMRequest(message="add", tools=_TOOLS, tool_exchanges=[_EXCHANGE_OTHER]),
    "with_attachments": LLMRequest(message="summarize these", attachments=_ATTACHMENTS),
}

SETTINGS_SHAPES = {
    "reasoning_model": dict(openai_model="gpt-5.4-mini"),
    "reasoning_effort": dict(openai_model="gpt-5.4-mini", openai_reasoning_effort="high"),
    "non_reasoning_model": dict(openai_model="gpt-4.1-mini"),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("settings_name", sorted(SETTINGS_SHAPES))
@pytest.mark.parametrize("shape", sorted(REQUEST_SHAPES))
async def test_every_non_streaming_request_is_private(shape, settings_name):
    create = AsyncMock(return_value=_ok_response())

    result = await _provider(create, _settings(**SETTINGS_SHAPES[settings_name])).generate(REQUEST_SHAPES[shape])

    assert result.status == "SUCCESS"
    create.assert_awaited_once()
    assert create.await_args.args == ()  # everything is a keyword we can inspect
    assert_private(create.await_args.kwargs)
    assert create.await_args.kwargs["stream"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("settings_name", sorted(SETTINGS_SHAPES))
@pytest.mark.parametrize("shape", sorted(REQUEST_SHAPES))
async def test_every_streaming_request_is_private(shape, settings_name):
    create = AsyncMock(return_value=_Stream())
    provider = _provider(create, _settings(**SETTINGS_SHAPES[settings_name]))

    chunks = [chunk async for chunk in provider.generate_stream(REQUEST_SHAPES[shape])]

    assert chunks[-1].final.status == "SUCCESS"
    create.assert_awaited_once()
    assert_private(create.await_args.kwargs)
    assert create.await_args.kwargs["stream"] is True


@pytest.mark.asyncio
async def test_every_retry_attempt_is_private():
    create = AsyncMock(side_effect=[openai.APITimeoutError(request=_REQUEST), _ok_response()])

    await _provider(create).generate(LLMRequest(message="hi", tools=_TOOLS))

    assert create.await_count == 2
    for call in create.await_args_list:
        assert_private(call.kwargs)


@pytest.mark.asyncio
async def test_attachments_go_inline_never_uploaded():
    create = AsyncMock(return_value=_ok_response())

    await _provider(create).generate(REQUEST_SHAPES["with_attachments"])

    user_item = create.await_args.kwargs["input"][-1]
    parts = {part["type"]: part for part in user_item["content"]}
    assert parts["input_file"]["file_data"].startswith("data:application/pdf;base64,")
    assert parts["input_image"]["image_url"].startswith("data:image/png;base64,")
    assert "file_id" not in json.dumps(create.await_args.kwargs)


def test_no_setting_or_env_var_can_turn_storage_on(monkeypatch):
    """There is deliberately no setting for this. Even a plausible-looking env var or
    constructor kwarg must not change what's sent."""
    monkeypatch.setenv("OPENAI_STORE", "true")
    monkeypatch.setenv("STORE", "true")
    settings = Settings(_env_file=None, openai_api_key="k", openai_store=True, store=True)
    params = OpenAIProvider(settings=settings)._build_params(LLMRequest(message="hi"), stream=False)

    assert params["store"] is False
    assert not any("store" in name and "storage" not in name for name in Settings.model_fields)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    [
        {"store": True},
        {"store": None},
        {"previous_response_id": "resp_123"},
        {"conversation": "conv_123"},
        {"background": True},
        {"metadata": {"name": "alice"}},
        {"input": [{"role": "user", "content": [{"type": "input_file", "file_id": "file-123"}]}]},
    ],
)
async def test_send_refuses_a_tampered_request_and_never_calls_the_api(monkeypatch, tamper):
    """`_send` is the last line of defense: if `_build_params` ever regressed (or
    someone bypassed it), the request must not leave."""
    create = AsyncMock(return_value=_ok_response())
    provider = _provider(create)
    original = provider._build_params

    def tampered(request, *, stream):
        return {**original(request, stream=stream), **tamper}

    monkeypatch.setattr(provider, "_build_params", tampered)

    result = await provider.generate(LLMRequest(message="hi"))
    chunks = [chunk async for chunk in provider.generate_stream(LLMRequest(message="hi"))]

    create.assert_not_awaited()
    assert result.status == "PERMANENT_ERROR" and result.error_type == "privacy_guard"
    assert chunks[-1].final.status == "PERMANENT_ERROR"


# --- static checks: the source can't grow an unguarded path ---------------------------


def _module_ast() -> ast.Module:
    return ast.parse(OPENAI_MODULE.read_text(encoding="utf-8"))


def test_only_app_llm_openai_imports_the_openai_sdk():
    importers = []
    for path in BACKEND_APP.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            if any(name == "openai" or name.startswith("openai.") for name in names):
                importers.append(path.relative_to(BACKEND_APP).as_posix())
    assert sorted(set(importers)) == ["llm/openai.py"]


def test_exactly_one_api_call_site_and_it_is_send():
    tree = _module_ast()
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("create", "stream", "parse", "retrieve")
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "responses"
    ]
    assert len(calls) == 1, "every OpenAI request must go through OpenAIProvider._send"

    send = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_send"
    )
    assert calls[0] in list(ast.walk(send))


def test_no_banned_endpoint_or_parameter_appears_in_code():
    """String literals naming banned params are allowed (the deny-list itself); actual
    attribute access or keyword arguments are not."""
    for node in ast.walk(_module_ast()):
        if isinstance(node, ast.Attribute):
            assert node.attr not in BANNED_ENDPOINTS, f"banned endpoint access: .{node.attr}"
        if isinstance(node, ast.keyword) and node.arg:
            assert node.arg not in BANNED_PARAMS, f"banned keyword argument: {node.arg}="
