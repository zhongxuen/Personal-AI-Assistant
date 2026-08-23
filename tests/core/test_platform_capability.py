"""
Platform capability tests (§22).

Registers a desktop-only tool and a tool available on desktop/web/discord,
and asserts ToolExecutor rejects the desktop-only tool for an unsupported
platform (with a clear explanatory error) while accepting it for a
supported one.
"""

from __future__ import annotations

from typing import Any

from app.core.permissions import PermissionLevel, RequesterContext
from app.core.tool_executor import ToolExecutor
from app.tools.base import ToolResult
from app.tools.registry import ToolRegistry


class FakeTool:
    """Minimal Tool implementation with no real system side effects."""

    def __init__(self, name: str, platforms: list[str]) -> None:
        self.name = name
        self.description = f"Fake tool '{name}' for tests."
        self.parameters: dict[str, Any] = {}
        self.permission = PermissionLevel.SAFE
        self.platforms = platforms
        self.requires_confirmation = False
        self.calls = 0

    def handler(self, **kwargs: Any) -> ToolResult:
        self.calls += 1
        return ToolResult(success=True, data={"ran": True})


def _context(platform: str) -> RequesterContext:
    return RequesterContext(user_id="u1", platform=platform)


def _executor() -> tuple[ToolExecutor, FakeTool, FakeTool]:
    registry = ToolRegistry()
    desktop_only = FakeTool(name="desktop_only_tool", platforms=["desktop"])
    all_platforms = FakeTool(
        name="cross_platform_tool", platforms=["desktop", "web", "discord"]
    )
    registry.register(desktop_only)
    registry.register(all_platforms)
    executor = ToolExecutor(registry)  # no db -- nothing persisted
    return executor, desktop_only, all_platforms


def test_desktop_only_tool_rejected_on_unsupported_platform():
    executor, desktop_only, _all_platforms = _executor()

    result = executor.execute(desktop_only.name, {}, _context("discord"))

    assert result.success is False
    assert result.error is not None
    assert "discord" in result.error.lower()
    assert desktop_only.calls == 0


def test_desktop_only_tool_accepted_on_desktop():
    executor, desktop_only, _all_platforms = _executor()

    result = executor.execute(desktop_only.name, {}, _context("desktop"))

    assert result.success is True
    assert desktop_only.calls == 1


def test_cross_platform_tool_accepted_on_every_declared_platform():
    executor, _desktop_only, all_platforms = _executor()

    for platform in ["desktop", "web", "discord"]:
        result = executor.execute(all_platforms.name, {}, _context(platform))
        assert result.success is True

    assert all_platforms.calls == 3
