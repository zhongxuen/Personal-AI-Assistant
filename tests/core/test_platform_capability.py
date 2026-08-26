"""
Platform capability tests (§22, extended file 11 prompt 3).

Registers a desktop-only tool and a tool available on desktop/web/discord,
and asserts ToolExecutor rejects the desktop-only tool for an unsupported
platform (with a clear explanatory error) while accepting it for a
supported one.

`test_real_desktop_tool_rejected_on_unsupported_platform` extends this with one of
file 11's actual desktop-agent tools (`clipboard_read`, platforms=["desktop"]) rather
than only `FakeTool`, so this doesn't just prove the executor's platform-check logic in
the abstract -- it proves a genuine desktop-only tool from the Desktop Agent Expansion
is unreachable from a non-desktop platform, with the same explanatory message pattern
(§22: "I can ... but this Discord instance cannot control your PC" -- i.e. the error
names the rejected platform, not a generic "forbidden").

`test_open_application_rejected_on_web_platform` (file 12 prompt 4) is the specific
scenario development-plan.md §22's own example names -- a web-originated request for
`open_application` -- run against the real tool, not `FakeTool`, and additionally
proves the rejection isn't just *labeled* a failure but actually never attempts to
launch anything (`os.startfile` is patched and asserted uncalled).
"""

from __future__ import annotations

from typing import Any

from app.core.permissions import PermissionLevel, RequesterContext
from app.core.tool_executor import ToolExecutor
from app.tools.applications import open_application_tool
from app.tools.base import ToolResult
from app.tools.clipboard import clipboard_read_tool
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


def test_real_desktop_tool_rejected_on_unsupported_platform():
    """`clipboard_read` (file 11, app/tools/clipboard.py) declares platforms=
    ["desktop"] like every other Desktop Agent Expansion tool -- registering the real
    tool (not FakeTool) proves ToolExecutor's platform gate actually covers it, with
    the same explanatory-message pattern as the FakeTool tests above.
    """
    registry = ToolRegistry()
    registry.register(clipboard_read_tool)
    executor = ToolExecutor(registry)

    result = executor.execute(clipboard_read_tool.name, {}, _context("discord"))

    assert result.success is False
    assert result.error is not None
    assert "discord" in result.error.lower()


def test_open_application_rejected_on_web_platform(monkeypatch):
    """§22's own example (development-plan.md: "I can open applications when you are
    connected to your desktop Jarvis agent...") run against the real `open_application`
    tool with `platform="web"` -- the exact caller file 12's deployed dashboard is.
    `os.startfile` is patched so a regression that skips the platform check and
    actually tries to launch something is caught here, not just a success/failure flag.
    """
    launched: list[str] = []
    monkeypatch.setattr("os.startfile", lambda path: launched.append(path), raising=False)

    registry = ToolRegistry()
    registry.register(open_application_tool)
    executor = ToolExecutor(registry)

    result = executor.execute(open_application_tool.name, {"app_name": "vscode"}, _context("web"))

    assert result.success is False
    assert result.error is not None
    assert "web" in result.error.lower()
    assert launched == []
