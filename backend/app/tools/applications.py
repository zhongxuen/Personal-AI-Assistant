"""
Application launch/terminate tools (§37 Phase 2 / file 03).

`open_application` (SAFE) and `close_application` (CONFIRM) resolve a user-facing app
name/alias against a small hardcoded lookup table. File 09 (Memory) promotes this into
real persisted, user-configurable memory -- don't build that generality here (§41 Rule 1).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import psutil

from app.core.permissions import PermissionLevel
from app.tools.base import ToolResult

# Placeholder path for the "coding" routine's portfolio-folder step (file 03/§37 Phase
# 2). Not a real configured project location yet -- file 09 (Memory) promotes this into
# real persisted, user-configurable memory (e.g. a `default_project` value); don't build
# that generality here (§41 Rule 1).
PORTFOLIO_FOLDER = Path.home() / "Projects" / "portfolio"

# Process names close_application must never terminate, regardless of what APP_MAP says
# or how the tool is invoked. This assistant is developed and run from inside VS Code
# (Code.exe), and Windows Electron apps run every one of their processes -- main,
# renderers, GPU, extension host, utility -- under this exact same image name. An
# unmocked call/test of close_application("vscode") loops psutil.process_iter() and
# terminates all of them simultaneously, which kills the very editor/session the
# assistant is running in. Revisit only once this ships as a standalone service that
# doesn't live inside the dev editor (file 09 Memory territory, not here -- §41 Rule 1).
NEVER_CLOSE = {"code.exe", "code"}

# alias (lowercased) -> {"command": <argv for subprocess.Popen / os.startfile>,
# "process_names": [...]}. `process_names` is what we look for via psutil.process_iter()
# to close the app -- it can differ from `command` (e.g. "code" launches VS Code, which
# then runs as "Code.exe"). For a folder (e.g. "portfolio"), `command[0]` is the path
# itself -- `os.startfile` opens a directory path in Explorer the same way it launches
# a registered app, and there's no process to look for on close, hence the empty list.
APP_MAP: dict[str, dict[str, Any]] = {
    "vscode": {"command": ["code"], "process_names": ["Code.exe", "code"]},
    "vs code": {"command": ["code"], "process_names": ["Code.exe", "code"]},
    "visual studio code": {"command": ["code"], "process_names": ["Code.exe", "code"]},
    "chrome": {"command": ["chrome"], "process_names": ["chrome.exe", "chrome"]},
    "google chrome": {"command": ["chrome"], "process_names": ["chrome.exe", "chrome"]},
    "portfolio": {"command": [str(PORTFOLIO_FOLDER)], "process_names": ["explorer.exe"]},
    "portfolio folder": {"command": [str(PORTFOLIO_FOLDER)], "process_names": ["explorer.exe"]},
    "notepad": {"command": ["notepad"], "process_names": ["notepad.exe"]},
    "calculator": {"command": ["calc"], "process_names": ["Calculator.exe", "calc.exe"]},
    "calc": {"command": ["calc"], "process_names": ["Calculator.exe", "calc.exe"]},
    "explorer": {"command": ["explorer"], "process_names": ["explorer.exe"]},
    "file explorer": {"command": ["explorer"], "process_names": ["explorer.exe"]},
    "spotify": {"command": ["spotify"], "process_names": ["Spotify.exe", "spotify"]},
    "terminal": {"command": ["wt"], "process_names": ["WindowsTerminal.exe", "wt.exe"]},
    "cmd": {"command": ["cmd"], "process_names": ["cmd.exe"]},
    "discord": {"command": ["discord"], "process_names": ["Discord.exe", "discord"]},
}


def _resolve(app_name: str) -> tuple[str, dict[str, Any]] | None:
    """Case/whitespace-insensitive lookup into APP_MAP -> (canonical_key, entry)."""
    entry = APP_MAP.get(app_name.strip().lower())
    if entry is None:
        return None
    return app_name.strip().lower(), entry


def _known_apps() -> str:
    return ", ".join(sorted(set(APP_MAP)))


class OpenApplicationTool:
    """Launches a desktop application resolved from `APP_MAP`."""

    name = "open_application"
    description = "Open/launch a desktop application by name (e.g. 'vscode', 'chrome')."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "app_name": {"type": "string", "description": "Name or alias of the application to open."}
        },
        "required": ["app_name"],
    }
    permission = PermissionLevel.SAFE
    platforms = ["desktop"]
    requires_confirmation = False

    def handler(self, app_name: str, **kwargs: Any) -> ToolResult:
        resolved = _resolve(app_name)
        if resolved is None:
            return ToolResult(
                success=False,
                error=f"Don't know how to open '{app_name}'. Known apps: {_known_apps()}.",
            )
        key, entry = resolved
        command: list[str] = entry["command"]

        try:
            if sys.platform == "win32":
                # os.startfile resolves PATH/registered-app launches (e.g. "notepad",
                # "calc") the same way the Windows "Run" dialog would.
                os.startfile(command[0])  # noqa: S606 - command comes from our own hardcoded map
            else:
                subprocess.Popen(command)
        except Exception as exc:
            return ToolResult(success=False, error=f"Failed to open '{app_name}': {exc}")

        return ToolResult(success=True, data={"message": f"Opening {key}."})


class CloseApplicationTool:
    """Terminates a running process resolved from `APP_MAP` via `psutil`."""

    name = "close_application"
    description = "Close/terminate a running desktop application by name."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "app_name": {"type": "string", "description": "Name or alias of the application to close."}
        },
        "required": ["app_name"],
    }
    permission = PermissionLevel.CONFIRM
    platforms = ["desktop"]
    requires_confirmation = True

    def handler(self, app_name: str, **kwargs: Any) -> ToolResult:
        resolved = _resolve(app_name)
        if resolved is None:
            return ToolResult(
                success=False,
                error=f"Don't know how to close '{app_name}'. Known apps: {_known_apps()}.",
            )
        key, entry = resolved
        process_names = {name.lower() for name in entry["process_names"]} - NEVER_CLOSE
        if not process_names:
            return ToolResult(
                success=False,
                error=f"Refusing to close '{app_name}': every matching process name is protected.",
            )

        killed: list[str] = []
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                proc_name = (proc.info.get("name") or "").lower()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if proc_name not in process_names:
                continue
            try:
                proc.terminate()
                killed.append(str(proc.info.get("pid")))
            except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
                return ToolResult(success=False, error=f"Failed to close '{app_name}': {exc}")

        if not killed:
            return ToolResult(success=False, error=f"No running process found for '{key}'.")

        return ToolResult(success=True, data={"message": f"Closed {key} (pid(s): {', '.join(killed)})."})


open_application_tool = OpenApplicationTool()
close_application_tool = CloseApplicationTool()
