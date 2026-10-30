"""
The OpenAI transport guard (`app.llm.openai_guard`) and the build gate
(`backend/scripts/check_openai.py`) -- OPENAI-RULES.md sections 3 and 4.

Real SDK calls go through the real guard; only the network underneath is an
`httpx.MockTransport`, which records exactly what would have left the process.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx2 as httpx
import pytest

from app.config.settings import Settings
from app.llm.openai import OpenAIProvider
from app.llm.openai_guard import (
    APIConnectionError,
    OpenAIPolicyError,
    guarded_async_client,
    is_policy_error,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _block_real_openai(monkeypatch):
    """Overrides conftest's block (which stops `AsyncClient.send` outright) with one at
    the real network transport: these tests need the client's full send path, and the
    guard always forwards to an injected `httpx.MockTransport` here, never the network."""

    async def _blocked(*_args, **_kwargs):
        raise AssertionError("A guard test attempted a real network call.")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _blocked)


def _reply(store: object = False) -> dict:
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 0,
        "model": "gpt-test",
        "status": "completed",
        "store": store,
        "output": [],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
    }


class _Network:
    """Records every request the guard lets through and answers with `respond`."""

    def __init__(self, respond) -> None:
        self.sent: list[httpx.Request] = []
        self._respond = respond

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.sent.append(request)
            return self._respond(request)

        return httpx.MockTransport(handler)


def _client(network: _Network):
    return guarded_async_client(api_key="sk-test", timeout=5, inner=network.transport())


@pytest.mark.asyncio
async def test_store_is_forced_off_and_identifying_fields_are_stripped():
    network = _Network(lambda r: httpx.Response(200, json=_reply()))
    await _client(network).responses.create(
        model="gpt-test",
        input="hi",
        store=True,
        metadata={"project": "jarvis"},
        user="alice",
        safety_identifier="alice",
        prompt_cache_key="jarvis-123",
    )
    body = json.loads(network.sent[0].content)
    assert body["store"] is False
    for field in ("metadata", "user", "safety_identifier", "prompt_cache_key"):
        assert field not in body


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("previous_response_id", "resp_0"),
        ("conversation", "conv_1"),
        ("prompt", {"id": "pmpt_1"}),
        ("background", True),
    ],
)
async def test_forbidden_fields_are_refused_and_never_sent(field, value):
    network = _Network(lambda r: httpx.Response(200, json=_reply()))
    with pytest.raises(Exception) as info:  # raw or SDK-wrapped -- is_policy_error covers both
        await _client(network).responses.create(model="gpt-test", input="hi", **{field: value})
    assert is_policy_error(info.value)
    assert network.sent == []


@pytest.mark.asyncio
async def test_endpoints_outside_the_allow_list_are_refused():
    network = _Network(lambda r: httpx.Response(200, json={}))
    client = _client(network)
    with pytest.raises(Exception) as info:  # raw or SDK-wrapped -- is_policy_error covers both
        await client.post("/files", body={"purpose": "assistants"}, cast_to=httpx.Response)
    assert is_policy_error(info.value)
    with pytest.raises(Exception) as info:  # raw or SDK-wrapped -- is_policy_error covers both
        await client.get("/responses/resp_1", cast_to=httpx.Response)
    assert is_policy_error(info.value)
    assert network.sent == []


@pytest.mark.asyncio
async def test_a_reply_not_marked_unstored_fails_the_call():
    for store in (True, None):
        network = _Network(lambda r, s=store: httpx.Response(200, json=_reply(store=s)))
        with pytest.raises(Exception) as info:
            await _client(network).responses.create(model="gpt-test", input="hi")
        assert is_policy_error(info.value)


@pytest.mark.asyncio
async def test_base_url_env_var_cannot_redirect_requests(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://evil.example.com/v1")
    network = _Network(lambda r: httpx.Response(200, json=_reply()))
    await _client(network).responses.create(model="gpt-test", input="hi")
    assert network.sent[0].url.host == "api.openai.com"


def _sse(*events: dict) -> bytes:
    return b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events)


@pytest.mark.asyncio
async def test_streamed_reply_passes_when_unstored_and_fails_when_stored():
    delta = {"type": "response.output_text.delta", "delta": "hi", "item_id": "m", "output_index": 0,
             "content_index": 0, "sequence_number": 1, "logprobs": []}

    def stream_reply(store):
        completed = {"type": "response.completed", "response": _reply(store=store), "sequence_number": 2}
        return lambda r: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=_sse(delta, completed)
        )

    ok = _Network(stream_reply(False))
    events = [e async for e in await _client(ok).responses.create(model="gpt-test", input="hi", stream=True)]
    assert [e.type for e in events] == ["response.output_text.delta", "response.completed"]
    assert ok.sent[0].headers["accept-encoding"] == "identity"

    stored = _Network(stream_reply(True))
    with pytest.raises(Exception) as info:
        async for _ in await _client(stored).responses.create(model="gpt-test", input="hi", stream=True):
            pass
    assert is_policy_error(info.value)


def test_provider_treats_a_guard_refusal_as_permanent():
    provider = OpenAIProvider(settings=Settings(_env_file=None, openai_api_key="k"))
    wrapped = APIConnectionError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
    wrapped.__cause__ = OpenAIPolicyError("nope")
    assert provider._classify_exception(wrapped) == ("PERMANENT_ERROR", "privacy_guard")
    assert provider._classify_exception(OpenAIPolicyError("nope")) == ("PERMANENT_ERROR", "privacy_guard")


def test_build_gate_passes_on_this_repo():
    spec = importlib.util.spec_from_file_location("check_openai", REPO_ROOT / "backend/scripts/check_openai.py")
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    assert gate.find_violations() == []


def test_build_gate_catches_a_direct_import(tmp_path):
    spec = importlib.util.spec_from_file_location("check_openai", REPO_ROOT / "backend/scripts/check_openai.py")
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    (tmp_path / "bad.py").write_text("from openai import OpenAI\nclient.responses.create(store=True)\n")
    (tmp_path / "bad.ts").write_text("import OpenAI from 'openai'\nfetch('https://api.openai.com/v1/files')\n")
    reasons = sorted(v.split(": ", 1)[1] for v in gate.find_violations(tmp_path))
    assert reasons == [
        "calls api.openai.com directly",
        "imports the openai package",
        "imports the openai package",
        "sets store=True",
    ]
