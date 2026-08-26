"""
Restricted terminal command tool (§23 Desktop Agent Expansion, file 11 prompt 2).

`run_terminal_command` is the closest thing this codebase has to a shell, so it is
deliberately the most locked-down tool here: PermissionLevel.RESTRICTED (denied by
default -- needs `requester_context.override = True`, a stronger gate than CONFIRM's
`confirmed = True`; see `app/core/permissions.py`) *and* allow-listed against
`ALLOWED_COMMANDS` below, never handed a raw shell string assembled from LLM/user input.
See docs/security.md for the full rationale on why this shape and not an open shell.

Allow-list design:
  - `alias` must be an exact key in `ALLOWED_COMMANDS`; unknown aliases are rejected
    outright with the list of known aliases, never coerced or passed through.
  - Each entry's `template` is a literal argv list executed with `shell=False`
    (`subprocess.run`), not a shell string -- there is no shell to inject into even if
    a substituted value contained shell metacharacters like `;`, `|`, or `&`.
  - A template may contain `{name}` placeholders (e.g. `"{host}"` as its own argv
    element). Every such name must have a matching entry in that command's `params`
    dict -- a compiled regex the caller-supplied value must *fully* match before
    substitution. Unrecognized `args` keys and missing required ones are both rejected
    before anything is built, let alone run (§41 Rule 6: never trust LLM/user input
    blindly).
  - Runs with a hard timeout and truncates captured stdout/stderr, so one call can't
    hang the executor or flood the response with runaway output.
"""

from __future__ import annotations

import re
import subprocess
from typing import Any

from app.core.permissions import PermissionLevel
from app.tools.base import ToolResult

_TIMEOUT_SECONDS = 15.0
_MAX_OUTPUT_CHARS = 4000

# alias (lowercase) -> {
#   "template": argv list, may contain "{name}" placeholders as standalone elements,
#   "params": {name: compiled regex the substituted value must fully match},
#   "description": human-readable summary (also surfaced in the tool's own schema),
# }
#
# This dict IS the allow-list -- run_terminal_command executes nothing that isn't a key
# here, and never substitutes a placeholder value that doesn't match its regex. Extend
# it in code (or move it to config later) rather than accepting a raw command string
# from anywhere -- that's the whole point of this tool being RESTRICTED instead of open.
ALLOWED_COMMANDS: dict[str, dict[str, Any]] = {
    "list_directory": {
        "template": ["cmd", "/c", "dir"],
        "params": {},
        "description": "List files in the assistant process's current working directory.",
    },
    "disk_usage": {
        "template": ["wmic", "logicaldisk", "get", "Caption,FreeSpace,Size"],
        "params": {},
        "description": "Show free/total space for each local drive.",
    },
    "network_config": {
        "template": ["ipconfig", "/all"],
        "params": {},
        "description": "Show network adapter configuration.",
    },
    "ping_host": {
        "template": ["ping", "-n", "4", "{host}"],
        # Hostnames/IPv4 only -- deliberately excludes shell metacharacters, spaces,
        # and flags (a value like "-t" or "&& calc" both fail this regex and are
        # therefore never substituted in, regardless of shell=False already blocking
        # shell interpretation).
        "params": {"host": re.compile(r"^[A-Za-z0-9.\-]{1,253}$")},
        "description": "Ping a hostname or IP address 4 times.",
    },
}


def _known_aliases() -> str:
    return ", ".join(sorted(ALLOWED_COMMANDS))


def _build_argv(
    template: list[str], params: dict[str, re.Pattern[str]], args: dict[str, Any]
) -> tuple[list[str] | None, str | None]:
    """Validate `args` against `params` and substitute into `template`.

    Returns (argv, None) on success or (None, error_message) on failure. Every
    placeholder name in `template` must have a matching entry in `params`, and every
    supplied value must fully match its regex before being substituted in -- nothing
    reaches `subprocess.run` unvalidated.
    """
    required = set(params)
    supplied = set(args)
    unknown = supplied - required
    if unknown:
        return None, f"Unrecognized argument(s): {', '.join(sorted(unknown))}."
    missing = required - supplied
    if missing:
        return None, f"Missing required argument(s): {', '.join(sorted(missing))}."

    for key, pattern in params.items():
        value = args[key]
        if not isinstance(value, str) or not pattern.fullmatch(value):
            return None, f"Argument '{key}' has an invalid value."

    argv = [piece.format(**args) for piece in template]
    return argv, None


class RunTerminalCommandTool:
    """Runs one allow-listed command by alias -- never a raw shell string."""

    name = "run_terminal_command"
    description = (
        "Run one allow-listed terminal command by alias. Cannot run arbitrary shell "
        f"commands. Known aliases: {_known_aliases()}."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "alias": {
                "type": "string",
                "description": f"Command alias to run. Known: {_known_aliases()}.",
            },
            "args": {
                "type": "object",
                "description": "Named argument values the alias's template placeholders require, if any.",
            },
        },
        "required": ["alias"],
    }
    permission = PermissionLevel.RESTRICTED
    platforms = ["desktop"]
    requires_confirmation = True

    def handler(self, alias: str, args: dict[str, Any] | None = None, **kwargs: Any) -> ToolResult:
        key = alias.strip().lower()
        entry = ALLOWED_COMMANDS.get(key)
        if entry is None:
            return ToolResult(
                success=False,
                error=f"'{alias}' is not an allow-listed command. Known: {_known_aliases()}.",
            )

        argv, error = _build_argv(entry["template"], entry["params"], args or {})
        if error:
            return ToolResult(success=False, error=error)

        try:
            completed = subprocess.run(
                argv,
                shell=False,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(success=False, error=f"'{key}' timed out after {_TIMEOUT_SECONDS}s.")
        except OSError as exc:
            return ToolResult(success=False, error=f"Failed to run '{key}': {exc}")

        stdout = (completed.stdout or "")[:_MAX_OUTPUT_CHARS]
        stderr = (completed.stderr or "")[:_MAX_OUTPUT_CHARS]
        message = f"Ran '{key}' (exit code {completed.returncode})."
        return ToolResult(
            success=True,
            data={
                "message": message,
                "returncode": completed.returncode,
                "stdout": stdout,
                "stderr": stderr,
            },
        )


run_terminal_command_tool = RunTerminalCommandTool()
