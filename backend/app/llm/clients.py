"""
Long-lived, event-loop-scoped LLM transport clients (performance; §41 Rule 1).

Every provider in `app.llm.*` used to build its HTTP client from scratch on each call:
`GeminiProvider._get_client()` cached a `google.genai.Client` on `self`, but
`AssistantCore` constructs a fresh `AIRouter` -> `ProviderManager` -> `GeminiProvider`
*per request* (see `app.api.routes.assistant`), so "cached on self" meant "rebuilt every
message". `OllamaProvider` was more explicit about it -- a brand new
`httpx.AsyncClient` inside the retry loop of every `_generate`.

That is the single largest avoidable cost in a turn. A new client means a new connection
pool, which means a fresh DNS lookup, TCP handshake and full TLS negotiation to
`generativelanguage.googleapis.com` before a single token of the actual request goes out
-- easily 150-400ms on every message, paid again on every message, and worse the further
the backend is from Google (Render -> Google is not a short hop). Reusing one client
keeps the connection pool warm, so the second and every subsequent request skip the
handshake entirely and reuse an established HTTP/2 connection.

## Why the cache is keyed on the event loop

An async HTTP client's pooled connections are bound to the event loop they were opened
on -- transports register their socket callbacks with *that* loop's selector. Handing a
client whose connections belong to a dead (or merely different) loop to a new loop
surfaces as `RuntimeError: Event loop is closed`, or worse, silent hangs. So this is
deliberately not one process-wide client: it's one client *per running event loop*,
looked up via `asyncio.get_running_loop()`.

In practice that is exactly the sharing we want, because every production caller now
runs its LLM work on a loop that lives as long as the process does:

  - web / mobile / voice / SSE -> uvicorn's loop (`app.api.routes.assistant`, `.voice`)
  - Discord -> discord.py's own loop (`app.platforms.discord`)
  - WhatsApp webhook -> uvicorn's loop, via its background task

...so each of those gets one client that is built once and reused for the life of the
process. Only the synchronous compatibility shim (`AssistantCore.handle`, used by tests
and any remaining sync caller) spins up a throwaway loop per call, and it correctly gets
a throwaway client that is discarded with it -- the old behavior, no worse than before.

Entries for closed loops are dropped on the next lookup (`_evict_closed`), so the short-
lived-loop case can't leak clients over a long test run.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# {(namespace, loop id): client}. Keyed by `id(loop)` rather than the loop itself so a
# finished loop can be garbage collected instead of being kept alive by this dict; the
# loop object is held alongside the client purely so `_evict_closed` can ask whether it
# is still open.
_clients: dict[tuple[str, int], tuple[asyncio.AbstractEventLoop, Any]] = {}


def _evict_closed() -> None:
    """Drop cached clients whose event loop has since closed.

    Called on every lookup rather than on a timer -- the dict only ever holds one entry
    per live loop (a handful at most in production, one per test in a test run), so this
    is a trivially short scan, and doing it inline means there's no background task to
    own or shut down.
    """
    for key, (loop, client) in list(_clients.items()):
        if loop.is_closed():
            del _clients[key]
            # Best-effort: the loop that owned this client's connections is already
            # gone, so there is no way left to close them gracefully. Dropping the
            # reference is all that's available, and the OS reclaims the sockets with
            # the loop. `aclose()` here would need the dead loop, so it isn't attempted.
            logger.debug("Evicted %s client for closed event loop.", key[0])


def get_loop_client(namespace: str, factory: Callable[[], _T]) -> _T:
    """The client for `namespace` on the *currently running* event loop, building it
    with `factory` on first use for that loop and reusing it on every call after.

    `namespace` separates unrelated clients that share a loop (e.g. `"gemini"` and
    `"ollama"`). `factory` is only ever called on a miss, so an expensive constructor
    costs nothing on the hot path.

    Raises `RuntimeError` (from `asyncio.get_running_loop`) if called outside a running
    loop -- every caller here is already inside `async def`, and a caller that isn't has
    a bug this should surface rather than paper over by silently building a fresh client.
    """
    loop = asyncio.get_running_loop()
    _evict_closed()

    key = (namespace, id(loop))
    cached = _clients.get(key)
    if cached is not None:
        return cached[1]

    client = factory()
    _clients[key] = (loop, client)
    logger.debug("Built a new %s client for this event loop.", namespace)
    return client


def reset_clients() -> None:
    """Drop every cached client, for tests that need a clean slate (e.g. after
    monkeypatching an API key, so the next call rebuilds against the new value rather
    than reusing a client wired to the old one). Not called in normal operation.
    """
    _clients.clear()
