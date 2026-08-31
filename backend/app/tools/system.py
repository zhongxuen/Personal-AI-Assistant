"""
System info tools (§37 Phase 2 / file 03; extended file 11 prompt 1).

`get_time` and `get_system_info` are the two simplest possible `Tool` implementations --
no params, no side effects beyond reading the local clock / OS state -- used to prove the
zero-LLM CommandRouter -> ToolExecutor -> Tool.handler path end to end (§9, §41 Rule 4).

`list_processes`/`get_process_info` (file 11, Desktop Agent Expansion, §23) are read-only
`psutil` process inspection -- SAFE like the two above, since listing/inspecting processes
by itself changes nothing on the machine (unlike `close_application`'s CONFIRM-gated
`terminate()`). They stay `platforms = ["desktop"]`: process inspection is about *this
machine*, the same category of capability as `open_application`/file operations/
clipboard/terminal (§22) -- a web or Discord requester has no local machine to ask about.

`get_time`/`get_system_info` are different in kind: they answer a question ("what time
is it", "how's this machine doing") with plain text, no control over anything, so they're
also reachable from `["desktop", "web", "discord", "mobile", "whatsapp"]` (§22) -- a
chat-based interface can ask either one same as a task/timer command. `"whatsapp"`
(file 18) joins that list for exactly the reason `"mobile"` did: it is another
text-in/text-out conversational surface whose sender resolves to a real `User`
(`app/whatsapp/linking.py`), so the question "what can this platform do?" gets the same
answer chat surfaces already get -- while `list_processes`/`get_process_info` above stay
desktop-only, because a WhatsApp sender has no local machine to inspect any more than a
Discord one does.
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
    platforms = ["desktop", "web", "discord", "mobile", "whatsapp"]
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
    platforms = ["desktop", "web", "discord", "mobile", "whatsapp"]
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


class ListProcessesTool:
    """Lists running processes (pid/name/status/memory), optionally filtered by name."""

    name = "list_processes"
    description = "List running processes, optionally filtered by a name substring."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name_filter": {
                "type": "string",
                "description": "Optional case-insensitive substring to filter process names by.",
            }
        },
        "required": [],
    }
    permission = PermissionLevel.SAFE
    platforms = ["desktop"]
    requires_confirmation = False

    def handler(self, name_filter: str | None = None, **kwargs: Any) -> ToolResult:
        filter_lower = name_filter.strip().lower() if name_filter else None
        processes: list[dict[str, Any]] = []
        for proc in psutil.process_iter(["pid", "name", "status", "memory_percent"]):
            try:
                info = proc.info
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            proc_name = info.get("name") or ""
            if filter_lower and filter_lower not in proc_name.lower():
                continue
            processes.append(
                {
                    "pid": info.get("pid"),
                    "name": proc_name,
                    "status": info.get("status"),
                    "memory_percent": round(info.get("memory_percent") or 0.0, 2),
                }
            )
        processes.sort(key=lambda p: p["name"].lower())

        message = f"{len(processes)} process(es)"
        if name_filter:
            message += f" matching '{name_filter}'"
        message += "."
        return ToolResult(success=True, data={"message": message, "processes": processes})


class GetProcessInfoTool:
    """Reports detailed info for a single process, looked up by pid or by name."""

    name = "get_process_info"
    description = "Get detailed info (status, CPU, memory, start time) for a running process by pid or name."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pid": {"type": "integer", "description": "Process id to look up."},
            "name": {
                "type": "string",
                "description": "Process name to look up (first case-insensitive match) if pid isn't given.",
            },
        },
        "required": [],
    }
    permission = PermissionLevel.SAFE
    platforms = ["desktop"]
    requires_confirmation = False

    def handler(self, pid: int | None = None, name: str | None = None, **kwargs: Any) -> ToolResult:
        if pid is None and not name:
            return ToolResult(success=False, error="Provide a 'pid' or a 'name' to look up.")

        proc: psutil.Process | None = None
        if pid is not None:
            try:
                proc = psutil.Process(pid)
            except psutil.NoSuchProcess:
                return ToolResult(success=False, error=f"No process found with pid {pid}.")
        else:
            name_lower = name.strip().lower()  # type: ignore[union-attr]
            for candidate in psutil.process_iter(["pid", "name"]):
                try:
                    if (candidate.info.get("name") or "").lower() == name_lower:
                        proc = candidate
                        break
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            if proc is None:
                return ToolResult(success=False, error=f"No running process found named '{name}'.")

        try:
            with proc.oneshot():
                try:
                    exe = proc.exe()
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    exe = None
                info = {
                    "pid": proc.pid,
                    "name": proc.name(),
                    "status": proc.status(),
                    "cpu_percent": proc.cpu_percent(interval=0.1),
                    "memory_percent": round(proc.memory_percent(), 2),
                    "create_time": datetime.fromtimestamp(proc.create_time()).isoformat(),
                    "exe": exe,
                }
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            return ToolResult(success=False, error=f"Failed to read process info: {exc}")

        message = (
            f"{info['name']} (pid {info['pid']}): {info['status']}, "
            f"CPU {info['cpu_percent']}%, memory {info['memory_percent']}%."
        )
        return ToolResult(success=True, data={"message": message, **info})


get_time_tool = GetTimeTool()
get_system_info_tool = GetSystemInfoTool()
list_processes_tool = ListProcessesTool()
get_process_info_tool = GetProcessInfoTool()
