"""
The ONLY place allowed to talk to OpenAI. Do not loosen.

Enforces the owner's OpenAI API key rules (`OPENAI-RULES.md` at the repo root, section 3)
at the HTTP transport, below the SDK, so no caller -- and no future edit to
`app.llm.openai` -- can send a request that breaks them:

1. Only `api.openai.com` and only the allowed endpoints (`ALLOWED`); anything else is
   refused before it leaves the process.
2. `store: false` is forced on `/responses` and `/chat/completions`.
3. `metadata`, `user`, `safety_identifier` and `prompt_cache_key` are stripped.
4. A request carrying `previous_response_id`, `conversation`, `prompt` or `background`
   is refused (raised, never sent).
5. A `/responses` reply that doesn't say `"store": false` fails the call. For a streamed
   reply the check runs on the terminal `response.completed`/`response.incomplete`
   event, and the stream raises there instead of handing that event to the SDK.

This is also the only module that imports the `openai` package; everything else gets
the client and the SDK's error types from here. `scripts/check_openai.py` fails the
build if any other file imports `openai`, calls `api.openai.com` or sets `store=True`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

# The openai SDK (3.x) runs on its own `httpx2` package, not `httpx`, so the transport
# must subclass httpx2's classes or the SDK's client won't accept it.
import httpx2 as httpx
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    DefaultAsyncHttpxClient,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)

# The SDK's error types are re-exported so callers (and their tests) never need to
# import `openai` themselves.
__all__ = [
    "APIConnectionError",
    "APIStatusError",
    "APITimeoutError",
    "AsyncOpenAI",
    "AuthenticationError",
    "BadRequestError",
    "InternalServerError",
    "NotFoundError",
    "PermissionDeniedError",
    "RateLimitError",
    "OPENAI_BASE_URL",
    "OpenAIPolicyError",
    "guarded_async_client",
    "is_policy_error",
]

OPENAI_HOST = "api.openai.com"
# Pinned rather than left to the SDK, which would otherwise honour an OPENAI_BASE_URL
# env var and send the key (and every prompt) wherever that points.
OPENAI_BASE_URL = f"https://{OPENAI_HOST}/v1"

ALLOWED = {"POST /responses", "POST /chat/completions", "POST /embeddings", "POST /moderations"}
STORE_FLAG = {"/responses", "/chat/completions"}
FORBIDDEN = ["previous_response_id", "conversation", "prompt", "background"]
STRIPPED = ["metadata", "user", "safety_identifier", "prompt_cache_key"]

_TERMINAL_EVENTS = {"response.completed", "response.incomplete"}


class OpenAIPolicyError(Exception):
    """A request or reply broke the OpenAI key rules. Never retried."""


def is_policy_error(exc: BaseException) -> bool:
    """True if `exc` is, or was caused by, an `OpenAIPolicyError`. The SDK wraps
    anything a transport raises in `APIConnectionError`, so check the cause chain."""
    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, OpenAIPolicyError):
            return True
        seen = seen.__cause__ or seen.__context__
    return False


def _endpoint(path: str) -> str:
    return path.removeprefix("/v1").rstrip("/") or "/"


def _check_stored(reply: object) -> None:
    if not isinstance(reply, dict) or reply.get("store") is not False:
        raise OpenAIPolicyError("OpenAI reported the response as stored")


class _StoreCheckedStream(httpx.AsyncByteStream):
    """A streamed `/responses` reply, passed through chunk by chunk, that raises on a
    terminal event whose `response.store` isn't `false` (rule 5 for streaming)."""

    def __init__(self, inner: httpx.AsyncByteStream) -> None:
        self._inner = inner
        self._buffer = b""

    def _check_events(self) -> None:
        self._buffer = self._buffer.replace(b"\r\n", b"\n")
        while b"\n\n" in self._buffer:
            event, self._buffer = self._buffer.split(b"\n\n", 1)
            for line in event.split(b"\n"):
                if not line.startswith(b"data:"):
                    continue
                try:
                    data = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if isinstance(data, dict) and data.get("type") in _TERMINAL_EVENTS:
                    _check_stored(data.get("response"))

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._inner:
            self._buffer += chunk
            self._check_events()
            yield chunk

    async def aclose(self) -> None:
        await self._inner.aclose()


class _GuardTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self._inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != OPENAI_HOST:
            raise OpenAIPolicyError(f"host {request.url.host} not allowed")
        ep = _endpoint(request.url.path)
        if f"{request.method} {ep}" not in ALLOWED:
            raise OpenAIPolicyError(f"{request.method} {ep} not allowed")
        try:
            body = json.loads(await request.aread())
        except ValueError:
            raise OpenAIPolicyError("JSON body required") from None
        if not isinstance(body, dict):
            raise OpenAIPolicyError("JSON object body required")
        for field in FORBIDDEN:
            if body.get(field) not in (None, False):
                raise OpenAIPolicyError(f'"{field}" not allowed')
        for field in STRIPPED:
            body.pop(field, None)
        if ep in STORE_FLAG:
            body["store"] = False

        streaming = body.get("stream") is True
        headers = [(k, v) for k, v in request.headers.multi_items() if k.lower() != "content-length"]
        if streaming:
            # The stream check reads the raw SSE bytes, so they must arrive uncompressed.
            headers = [(k, v) for k, v in headers if k.lower() != "accept-encoding"]
            headers.append(("accept-encoding", "identity"))
        safe = httpx.Request(
            request.method,
            request.url,
            headers=headers,
            content=json.dumps(body).encode(),
            extensions=request.extensions,  # carries the per-request timeouts
        )
        response = await self._inner.handle_async_request(safe)

        if ep == "/responses" and 200 <= response.status_code < 300:
            if streaming:
                return httpx.Response(
                    status_code=response.status_code,
                    headers=response.headers,
                    stream=_StoreCheckedStream(response.stream),
                    extensions=response.extensions,
                )
            await response.aread()
            try:
                reply = response.json()
            except ValueError:
                reply = None
            _check_stored(reply)
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


def guarded_async_client(
    *, api_key: str, timeout: float, inner: httpx.AsyncBaseTransport | None = None
) -> AsyncOpenAI:
    """THE way to get an OpenAI client. `inner` is a test seam (e.g. an
    `httpx.MockTransport`); production always uses the real network transport.

    SDK retries are off: retry policy belongs to the caller (`app.llm.openai`), which
    must never retry quota or permanent errors."""
    return AsyncOpenAI(
        api_key=api_key,
        base_url=OPENAI_BASE_URL,
        max_retries=0,
        timeout=timeout,
        http_client=DefaultAsyncHttpxClient(transport=_GuardTransport(inner)),
    )
