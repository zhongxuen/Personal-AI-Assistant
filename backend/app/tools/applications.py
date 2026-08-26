"""
Application launch/terminate tools (§37 Phase 2 / file 03, promoted file 09 prompt 2).

`open_application` (SAFE) and `close_application` (CONFIRM) resolve a user-facing app
name/alias against `MemoryService`'s "applications" category (`app/memory/service.py`)
instead of an in-code lookup table -- so a user can add/edit application mappings
(e.g. via the settings UI file 09 prompt 3 adds) without a code change.
`seed_default_applications()` persists this project's original aliases (file 03's
former hardcoded `APP_MAP`) as real rows the first time the app starts, the same
idempotent seed-once pattern `seed_default_routines()`/`seed_default_memory()` use --
after that first seed, only the `memories` table is ever read; this module has no
in-code map left to fall back on.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import psutil

from app.core.permissions import PermissionLevel
from app.database.database import SessionLocal
from app.memory.service import APPLICATIONS, MemoryService
from app.tools.base import ToolResult

# Seed value only -- read once by seed_default_applications(), never at call time. From
# then on the real, user-editable value lives in the `memories` table; open_application/
# close_application never see this constant.
_DEFAULT_PORTFOLIO_FOLDER = Path.home() / "Projects" / "portfolio"

# Process names close_application must never terminate, regardless of what memory says
# or how the tool is invoked. This assistant is developed and run from inside VS Code
# (Code.exe), and Windows Electron apps run every one of their processes -- main,
# renderers, GPU, extension host, utility -- under this exact same image name. An
# unmocked call/test of close_application("vscode") loops psutil.process_iter() and
# terminates all of them simultaneously, which kills the very editor/session the
# assistant is running in.
NEVER_CLOSE = {"code.exe", "code"}

# alias (lowercased) -> {"command": <argv for subprocess.Popen / os.startfile>,
# "process_names": [...]}. `process_names` is what we look for via psutil.process_iter()
# to close the app -- it can differ from `command` (e.g. "code" launches VS Code, which
# then runs as "Code.exe"). For a folder (e.g. "portfolio"), `command[0]` is the path
# itself -- `os.startfile` opens a directory path in Explorer the same way it launches
# a registered app, and there's no process to look for on close, hence the empty list.
#
# Seed data for the "applications" memory category only -- consumed exactly once by
# seed_default_applications(). Resolution at call time always goes through
# MemoryService, never this dict directly.
DEFAULT_APPLICATION_ALIASES: dict[str, dict[str, Any]] = {
    "vscode": {"command": ["code"], "process_names": ["Code.exe", "code"]},
    "vs code": {"command": ["code"], "process_names": ["Code.exe", "code"]},
    "visual studio code": {"command": ["code"], "process_names": ["Code.exe", "code"]},
    "chrome": {"command": ["chrome"], "process_names": ["chrome.exe", "chrome"]},
    "google chrome": {"command": ["chrome"], "process_names": ["chrome.exe", "chrome"]},
    "portfolio": {
        "command": [str(_DEFAULT_PORTFOLIO_FOLDER)],
        "process_names": ["explorer.exe"],
    },
    "portfolio folder": {
        "command": [str(_DEFAULT_PORTFOLIO_FOLDER)],
        "process_names": ["explorer.exe"],
    },
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


def seed_default_applications() -> None:
    """Create each default application-alias entry in the "applications" memory
    category if it doesn't already exist.

    Idempotent -- safe to call on every startup, mirroring `seed_default_memory()`/
    `seed_default_routines()`: it only seeds an alias that's missing, never overwriting
    one a user has since edited via `MemoryService.set()` or the settings UI.
    """
    db = SessionLocal()
    try:
        service = MemoryService(db)
        for alias, entry in DEFAULT_APPLICATION_ALIASES.items():
            if service.get(APPLICATIONS, alias) is None:
                service.set(APPLICATIONS, alias, entry)
    finally:
        db.close()


def _resolve(app_name: str) -> tuple[str, dict[str, Any]] | None:
    """Case/whitespace-insensitive lookup into the "applications" memory category ->
    (canonical_key, entry)."""
    key = app_name.strip().lower()
    db = SessionLocal()
    try:
        entry = MemoryService(db).get(APPLICATIONS, key)
    finally:
        db.close()
    if entry is None:
        return None
    return key, entry


def _known_apps() -> str:
    db = SessionLocal()
    try:
        return ", ".join(sorted(MemoryService(db).list(APPLICATIONS)))
    finally:
        db.close()


class OpenApplicationTool:
    """Launches a desktop application resolved from the "applications" memory category."""

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
                os.startfile(command[0])  # noqa: S606 - command comes from our own memory-backed map
            else:
                subprocess.Popen(command)
        except Exception as exc:
            return ToolResult(success=False, error=f"Failed to open '{app_name}': {exc}")

        return ToolResult(success=True, data={"message": f"Opening {key}."})


class CloseApplicationTool:
    """Terminates a running process resolved from the "applications" memory category
    via `psutil`."""

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
