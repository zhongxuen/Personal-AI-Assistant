"""
`ResponseCache` tests (§41 Rule 1, file 08 prompt 4).

Three layers:
  - `ResponseCache` itself in isolation: get/set/has/clear, TTL expiry, and that
    different params for the same tool are different cache entries.
  - `ToolExecutor` wiring: a `cacheable` tool's handler runs exactly once across
    repeated identical calls (a fresh cache miss re-runs it); a non-cacheable tool
    always re-runs; a failed result is never cached.
  - `CommandRouter` wiring: a route to a cacheable tool is labeled
    `CommandClassification.CACHED` once (and only once) the cache already holds a
    fresh entry for that exact tool+params.
"""

from __future__ import annotations

import time
from typing import Any

from app.core.cache import ResponseCache
from app.core.command_router import CommandClassification, CommandRouter
from app.core.permissions import PermissionLevel, RequesterContext
from app.core.tool_executor import ToolExecutor
from app.tools.base import ToolResult
from app.tools.registry import ToolRegistry


# --- ResponseCache in isolation -------------------------------------------------------


def test_miss_on_empty_cache():
    cache = ResponseCache()

    assert cache.get("get_system_info", {}) is None
    assert cache.has("get_system_info", {}) is False


def test_set_then_get_returns_the_same_value():
    cache = ResponseCache()
    value = ToolResult(success=True, data={"os": "Windows"})

    cache.set("get_system_info", {}, value)

    assert cache.has("get_system_info", {}) is True
    assert cache.get("get_system_info", {}) == value


def test_different_params_are_different_entries():
    cache = ResponseCache()
    cache.set("some_tool", {"a": 1}, "result-a")

    assert cache.get("some_tool", {"a": 1}) == "result-a"
    assert cache.get("some_tool", {"a": 2}) is None
    assert cache.has("some_tool", {"a": 2}) is False


def test_entry_expires_after_its_ttl():
    cache = ResponseCache()
    cache.set("get_system_info", {}, "stale-safe-for-now", ttl_seconds=0.01)

    assert cache.has("get_system_info", {}) is True
    time.sleep(0.02)

    assert cache.get("get_system_info", {}) is None
    assert cache.has("get_system_info", {}) is False


def test_clear_drops_every_entry():
    cache = ResponseCache()
    cache.set("tool_a", {}, "a")
    cache.set("tool_b", {}, "b")

    cache.clear()

    assert cache.get("tool_a", {}) is None
    assert cache.get("tool_b", {}) is None


# --- ToolExecutor wiring ---------------------------------------------------------------


class _FakeTool:
    def __init__(self, name: str, *, cacheable: bool, result: ToolResult | None = None) -> None:
        self.name = name
        self.description = f"Fake tool '{name}'."
        self.parameters: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
        self.permission = PermissionLevel.SAFE
        self.platforms = ["desktop"]
        self.requires_confirmation = False
        self.cacheable = cacheable
        self._result = result or ToolResult(success=True, data={"call": 1})
        self.calls = 0

    def handler(self, **kwargs: Any) -> ToolResult:
        self.calls += 1
        return self._result


def _context() -> RequesterContext:
    return RequesterContext(user_id="u1", platform="desktop")


def test_cacheable_tool_handler_runs_once_across_repeated_calls():
    registry = ToolRegistry()
    tool = _FakeTool("get_system_info", cacheable=True)
    registry.register(tool)
    executor = ToolExecutor(registry, cache=ResponseCache())

    first = executor.execute(tool.name, {}, _context())
    second = executor.execute(tool.name, {}, _context())

    assert first == second
    assert tool.calls == 1  # second call served from cache, handler not re-run


def test_non_cacheable_tool_runs_every_call():
    registry = ToolRegistry()
    tool = _FakeTool("get_time", cacheable=False)
    registry.register(tool)
    executor = ToolExecutor(registry, cache=ResponseCache())

    executor.execute(tool.name, {}, _context())
    executor.execute(tool.name, {}, _context())

    assert tool.calls == 2


def test_failed_result_is_not_cached_and_is_retried():
    registry = ToolRegistry()
    tool = _FakeTool("get_system_info", cacheable=True, result=ToolResult(success=False, error="boom"))
    registry.register(tool)
    executor = ToolExecutor(registry, cache=ResponseCache())

    executor.execute(tool.name, {}, _context())
    executor.execute(tool.name, {}, _context())

    assert tool.calls == 2  # every call re-runs since nothing successful was cached


def test_different_params_bypass_a_cached_entry_for_the_same_tool():
    registry = ToolRegistry()
    tool = _FakeTool("cacheable_with_params", cacheable=True)
    tool.parameters = {
        "type": "object",
        "properties": {"n": {"type": "integer"}},
        "required": [],
    }
    registry.register(tool)
    executor = ToolExecutor(registry, cache=ResponseCache())

    executor.execute(tool.name, {"n": 1}, _context())
    executor.execute(tool.name, {"n": 1}, _context())
    executor.execute(tool.name, {"n": 2}, _context())

    assert tool.calls == 2  # {"n": 1} hit cache the second time; {"n": 2} is a fresh key


# --- CommandRouter wiring --------------------------------------------------------------


def test_route_to_cacheable_tool_is_deterministic_until_cache_is_warm():
    registry = ToolRegistry()
    tool = _FakeTool("get_system_info", cacheable=True)
    registry.register(tool)
    cache = ResponseCache()
    router = CommandRouter(registry, cache=cache)

    first = router.route("get_system_info")
    assert first.classification == CommandClassification.DETERMINISTIC

    # Warm the cache the same way ToolExecutor would after a real handler call.
    cache.set(tool.name, {}, ToolResult(success=True, data={}))

    second = router.route("get_system_info")
    assert second.classification == CommandClassification.CACHED
    assert second.tool_name == tool.name


def test_route_to_non_cacheable_tool_is_never_labeled_cached():
    registry = ToolRegistry()
    tool = _FakeTool("get_time", cacheable=False)
    registry.register(tool)
    cache = ResponseCache()
    # Even if something happened to populate the cache under this key, a non-cacheable
    # tool must never be relabeled CACHED -- ToolExecutor would never actually serve it.
    cache.set(tool.name, {}, ToolResult(success=True, data={}))
    router = CommandRouter(registry, cache=cache)

    result = router.route("get_time")

    assert result.classification == CommandClassification.DETERMINISTIC
