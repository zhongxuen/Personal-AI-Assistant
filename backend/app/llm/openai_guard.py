"""
The ONE OpenAI client (OPENAI-KEY-RULES.md rule 4, local). Do not loosen.

The only module that imports the `openai` package; everything else gets the client and
the SDK's error types from here, and every call goes through `app.llm.openai`, which
budget-checks and cost-logs it. `scripts/check_openai.py` (and the frontend's
`scripts/check-openai.mjs`) fail the build if any other file imports `openai` or calls
`api.openai.com` directly.

Below the SDK, at the HTTP transport, so no future edit to `app.llm.openai` can get
around it:

1. Only `api.openai.com` and only the allowed endpoints (`ALLOWED`); anything else is
   refused before it leaves the process (the key can't be sent anywhere else).
2. `metadata`, `user`, `safety_identifier` and `prompt_cache_key` are stripped.
3. A request carrying `previous_response_id`, `conversation`, `prompt` or `background`
   is refused (raised, never sent) -- this app keeps no state on OpenAI's side.
4. `store: false` is set on `/responses` and `/chat/completions`. The current key rules
   allow storage, so this is no longer checked anywhere; it's kept because it is
   harmless and nothing here needs a stored response.
"""

from __future__ import annotations

import json

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


class OpenAIPolicyError(Exception):
    """A request broke the guard's rules. Never retried."""


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

        headers = [(k, v) for k, v in request.headers.multi_items() if k.lower() != "content-length"]
        safe = httpx.Request(
            request.method,
            request.url,
            headers=headers,
            content=json.dumps(body).encode(),
            extensions=request.extensions,  # carries the per-request timeouts
        )
        return await self._inner.handle_async_request(safe)

    async def aclose(self) -> None:
        await self._inner.aclose()


def guarded_async_client(
    *, api_key: str, timeout: float, inner: httpx.AsyncBaseTransport | None = None
) -> AsyncOpenAI:
    """THE way to get an OpenAI client (the one-client rule). `inner` is a test seam (e.g. an
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
