"""
System info tools (§37 Phase 2 / file 03).

`get_time` and `get_system_info` are the two simplest possible `Tool` implementations --
no params, no side effects beyond reading the local clock / OS state -- used to prove the
zero-LLM CommandRouter -> ToolExecutor -> Tool.handler path end to end (§9, §41 Rule 4).
"""

from __future__ import annotations

import platform as platform_module
from datetime import datetime
from typing import Any

import psutil

from app.core.permissions import PermissionLevel
from app.tools.base import ToolResult


class GetTimeTool:
    """Reports the current local date/time. No params, always SAFE."""

    name = "get_time"
    description = "Get the current local date and time."
    parameters: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
    permission = PermissionLevel.SAFE
    platforms = ["desktop"]
    requires_confirmation = False
    # Deliberately NOT cacheable (see app.core.cache.ResponseCache's docstring): safe
    # and side-effect-free, but its whole point is to be exact to the current second,
    # so replaying a cached answer would serve a wrong time by design.
    cacheable = False

    def handler(self, **kwargs: Any) -> ToolResult:
        now = datetime.now()
        return ToolResult(
            success=True,
            data={
                "message": f"It's currently {now.strftime('%I:%M %p on %A, %B %d, %Y')}.",
                "iso": now.isoformat(),
            },
        )


class GetSystemInfoTool:
    """Reports OS name/version plus basic CPU/memory stats via `psutil`."""

    name = "get_system_info"
    description = "Get basic OS, CPU, and memory information about this machine."
    parameters: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
    permission = PermissionLevel.SAFE
    platforms = ["desktop"]
    requires_confirmation = False
    # Cacheable (see app.core.cache.ResponseCache's docstring): safe, side-effect-free,
    # and a snapshot that's fine to serve a few seconds stale -- unlike get_time, no
    # one needs cpu_percent/memory_percent accurate to the millisecond.
    cacheable = True

    def handler(self, **kwargs: Any) -> ToolResult:
        try:
            mem = psutil.virtual_memory()
            info = {
                "os": platform_module.system(),
                "os_release": platform_module.release(),
                "os_version": platform_module.version(),
                "cpu_count": psutil.cpu_count(logical=True),
                "cpu_percent": psutil.cpu_percent(interval=0.1),
                "memory_total_gb": round(mem.total / (1024**3), 2),
                "memory_available_gb": round(mem.available / (1024**3), 2),
                "memory_percent": mem.percent,
            }
        except Exception as exc:  # psutil failures must not crash the executor
            return ToolResult(success=False, error=f"Failed to read system info: {exc}")

        message = (
            f"{info['os']} {info['os_release']} -- "
            f"CPU: {info['cpu_count']} cores at {info['cpu_percent']}% -- "
            f"Memory: {info['memory_available_gb']}GB free of {info['memory_total_gb']}GB "
            f"({info['memory_percent']}% used)."
        )
        return ToolResult(success=True, data={"message": message, **info})


get_time_tool = GetTimeTool()
get_system_info_tool = GetSystemInfoTool()
