"""
In-memory response cache (§41 Rule 1 -- no over-engineering; file 08 prompt 4).

A plain dict-backed, per-process TTL cache keyed on `(tool_name, params)`. No
persistence, no cross-process sharing, nothing survives a restart -- this is
deliberately the simplest thing that satisfies "don't re-run a tool call whose answer
hasn't changed" for the narrow set of calls where re-running really is wasted work.
`ToolExecutor` (`app.core.tool_executor`) is the only thing that reads/writes it for
real; `CommandRouter` only *peeks* at it (via `has()`) to label a route
`CommandClassification.CACHED` for observability -- it never stores or serves a value
itself, so there is exactly one place a cached `ToolResult` actually gets reused.

## What qualifies for caching

A tool is only ever looked up here if it opts in with `cacheable = True` (an optional
attribute on the `Tool` instance -- see `app.tools.base.Tool`); everything else always
misses, regardless of what's in the dict. Set `cacheable = True` on a tool only if its
result is:

  - **Safe and side-effect-free**: `permission == PermissionLevel.SAFE`, and the
    handler never mutates state -- no task/routine create/edit/delete, no
    notifications, no launching applications, no writing anything a repeat call
    should actually perform again. Caching a mutation would silently turn a repeated
    command into a no-op the second time.
  - **Deterministic and effectively static** for the length of the cache's TTL:
    calling it twice a few seconds apart should read as "the same answer" to the
    user. `get_system_info` qualifies -- an OS/CPU/memory snapshot is fine to be a
    few seconds stale. `get_time` deliberately does NOT qualify even though it's
    SAFE and side-effect-free: its entire purpose is to be exact to the second, so
    caching it would serve a wrong answer by design.
  - **Not user-specific-and-mutable data without an explicit invalidation hook**:
    `list_tasks`, `list_routines`, and anything reading rows another request can
    create/edit/delete must never be marked cacheable -- this cache has no
    invalidation wired to any write path. If a future tool like that genuinely needs
    caching, it must come with an explicit `response_cache.clear()` (or a
    per-key `invalidate`) call added to every mutation path that can change its
    answer, not just a `cacheable = True` flag.

Params are part of the cache key, so two calls to the same cacheable tool with
different params are unrelated cache entries.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


def _cache_key(tool_name: str, params: dict[str, Any]) -> str:
    # Every cacheable tool's params today are a flat JSON-schema "object" of
    # primitives (in practice: no params at all -- get_system_info takes none), so a
    # sorted repr is a stable-enough key. Swap for a real canonical-JSON serializer
    # first if a cacheable tool ever grows nested/unhashable params.
    return f"{tool_name}:{sorted(params.items())!r}"


@dataclass
class _Entry:
    value: Any
    expires_at: float


class ResponseCache:
    """Process-wide, in-memory TTL cache. Not thread-safe beyond CPython's GIL --
    fine for this project's single-process dev/desktop deployment (§41 Rule 1);
    revisit (e.g. Redis) only if a multi-worker deployment ever needs a shared cache.
    """

    def __init__(self, default_ttl_seconds: float = 30.0) -> None:
        self._default_ttl = default_ttl_seconds
        self._store: dict[str, _Entry] = {}

    def get(self, tool_name: str, params: dict[str, Any]) -> Any | None:
        """The cached value for `(tool_name, params)`, or `None` on a miss or an
        expired entry (which is dropped from the store here, not left to linger).
        """
        key = _cache_key(tool_name, params)
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.monotonic() >= entry.expires_at:
            del self._store[key]
            return None
        return entry.value

    def has(self, tool_name: str, params: dict[str, Any]) -> bool:
        """Whether `get()` would currently return a hit -- used by `CommandRouter` to
        label a route CACHED without taking on responsibility for serving the value
        itself (`ToolExecutor` still does that, and re-derives the identical result).
        """
        return self.get(tool_name, params) is not None

    def set(
        self, tool_name: str, params: dict[str, Any], value: Any, ttl_seconds: float | None = None
    ) -> None:
        key = _cache_key(tool_name, params)
        ttl = self._default_ttl if ttl_seconds is None else ttl_seconds
        self._store[key] = _Entry(value=value, expires_at=time.monotonic() + ttl)

    def clear(self) -> None:
        """Drop every cached entry. Nothing in the app calls this yet -- no cacheable
        tool has a mutation path that would need explicit invalidation (see the
        module docstring) -- but tests use it to isolate cases, and it's the hook any
        future cacheable-and-invalidatable tool should call into.
        """
        self._store.clear()


# Process-wide singleton -- `ToolExecutor` and `CommandRouter` share this by default
# (same convention as `HealthManager`/`QuotaManager`: in-memory process state, not
# per-request) so a result cached from one request is visible to the next.
response_cache = ResponseCache()
