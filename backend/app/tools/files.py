"""
File operation tools (§18 "later" tools list, file 11 prompt 1, Desktop Agent Expansion).

`search_files` and `open_file` are SAFE (read-only-ish: they list matches / hand a file
off to its default OS application, neither reads nor mutates file contents themselves).
`create_file` is CONFIRM (writes new file content to disk). Every path any of these three
touches is resolved through `app.tools.path_safety.sanitize_path` first (§33 "Sanitize
file paths") -- none of them ever passes a raw user/LLM-supplied path straight to the
filesystem (§41 Rule 6).
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

from app.core.permissions import PermissionLevel
from app.tools.base import ToolResult
from app.tools.path_safety import PathSafetyError, allowed_roots, sanitize_path

# Hard cap on search_files results regardless of what max_results the caller asks for --
# an unbounded rglob() over a large allowed directory (e.g. a whole Documents folder)
# could otherwise return thousands of matches for one tool call.
_DEFAULT_MAX_RESULTS = 50
_HARD_MAX_RESULTS = 200


class SearchFilesTool:
    """Finds files by a case-insensitive filename substring within the allowed directories."""

    name = "search_files"
    description = "Search for files by name (case-insensitive substring match) within the allowed directories."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Filename substring to search for."},
            "directory": {
                "type": "string",
                "description": (
                    "Optional base directory to search within (must resolve inside the allowed "
                    "directories). Defaults to searching every allowed directory."
                ),
            },
            "max_results": {
                "type": "integer",
                "description": f"Maximum number of matches to return (default {_DEFAULT_MAX_RESULTS}).",
            },
        },
        "required": ["query"],
    }
    permission = PermissionLevel.SAFE
    platforms = ["desktop"]
    requires_confirmation = False

    def handler(
        self,
        query: str,
        directory: str | None = None,
        max_results: int | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        query = query.strip()
        if not query:
            return ToolResult(success=False, error="A search query is required.")

        limit = max_results if isinstance(max_results, int) and max_results > 0 else _DEFAULT_MAX_RESULTS
        limit = min(limit, _HARD_MAX_RESULTS)

        if directory is not None:
            try:
                roots = [sanitize_path(directory)]
            except PathSafetyError as exc:
                return ToolResult(success=False, error=str(exc))
        else:
            roots = allowed_roots()

        query_lower = query.lower()
        matches: list[str] = []
        for root in roots:
            if not root.is_dir():
                continue
            for candidate in root.rglob("*"):
                if query_lower in candidate.name.lower():
                    matches.append(str(candidate))
                    if len(matches) >= limit:
                        break
            if len(matches) >= limit:
                break

        message = (
            f"Found {len(matches)} file(s) matching '{query}'."
            if matches
            else f"No files matching '{query}' found."
        )
        return ToolResult(success=True, data={"message": message, "matches": matches})


class OpenFileTool:
    """Opens a file with its default OS application (like `open_application`, for files)."""

    name = "open_file"
    description = "Open a file with its default OS application. The file must be within the allowed directories."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Path to the file to open."}},
        "required": ["path"],
    }
    permission = PermissionLevel.SAFE
    platforms = ["desktop"]
    requires_confirmation = False

    def handler(self, path: str, **kwargs: Any) -> ToolResult:
        try:
            resolved = sanitize_path(path)
        except PathSafetyError as exc:
            return ToolResult(success=False, error=str(exc))

        if not resolved.exists():
            return ToolResult(success=False, error=f"File not found: '{path}'.")
        if not resolved.is_file():
            return ToolResult(success=False, error=f"'{path}' is not a file.")

        try:
            if sys.platform == "win32":
                os.startfile(str(resolved))  # noqa: S606 - path passed through sanitize_path()
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(resolved)])
            else:
                subprocess.Popen(["xdg-open", str(resolved)])
        except Exception as exc:
            return ToolResult(success=False, error=f"Failed to open '{path}': {exc}")

        return ToolResult(success=True, data={"message": f"Opening {resolved.name}."})


class CreateFileTool:
    """Creates a new file (optionally with text content) within the allowed directories."""

    name = "create_file"
    description = "Create a new file, optionally with text content, within the allowed directories."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path of the file to create."},
            "content": {"type": "string", "description": "Optional text content to write into the file."},
            "overwrite": {
                "type": "boolean",
                "description": "Allow overwriting an existing file at that path. Defaults to false.",
            },
        },
        "required": ["path"],
    }
    permission = PermissionLevel.CONFIRM
    platforms = ["desktop"]
    requires_confirmation = True

    def handler(self, path: str, content: str = "", overwrite: bool = False, **kwargs: Any) -> ToolResult:
        try:
            resolved = sanitize_path(path)
        except PathSafetyError as exc:
            return ToolResult(success=False, error=str(exc))

        if resolved.is_dir():
            return ToolResult(success=False, error=f"'{path}' is a directory, not a file.")
        if resolved.exists() and not overwrite:
            return ToolResult(
                success=False,
                error=f"'{path}' already exists. Pass overwrite=true to replace it.",
            )

        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
        except OSError as exc:
            return ToolResult(success=False, error=f"Failed to create '{path}': {exc}")

        return ToolResult(
            success=True, data={"message": f"Created file: '{resolved.name}'.", "path": str(resolved)}
        )


search_files_tool = SearchFilesTool()
open_file_tool = OpenFileTool()
create_file_tool = CreateFileTool()
