"""
Clipboard tools (§23 Desktop Agent Expansion, file 11 prompt 2).

`clipboard_read` (SAFE) and `clipboard_write` (CONFIRM) wrap `pyperclip`, the same kind
of thin cross-platform OS wrapper `pyttsx3`/`faster-whisper` are elsewhere in this repo
-- no native bindings to build, works unmodified on Windows/macOS (Linux additionally
needs xclip/xsel installed, but that's an OS-level dependency outside this module's
control, and this project targets Windows first -- see CLAUDE.md).

`clipboard_write` is CONFIRM, not SAFE: unlike `clipboard_read`, which only observes
clipboard state, `clipboard_write` destroys whatever the user currently has copied --
the same "mutates something the user didn't ask this tool to touch" reasoning that
makes `create_file`/`close_application` CONFIRM rather than SAFE (§19).
"""

from __future__ import annotations

from typing import Any

import pyperclip

from app.core.permissions import PermissionLevel
from app.tools.base import ToolResult


class ClipboardReadTool:
    """Reads the current text content of the system clipboard."""

    name = "clipboard_read"
    description = "Read the current text content of the system clipboard."
    parameters: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
    permission = PermissionLevel.SAFE
    platforms = ["desktop"]
    requires_confirmation = False

    def handler(self, **kwargs: Any) -> ToolResult:
        try:
            text = pyperclip.paste()
        except Exception as exc:  # pyperclip raises its own PyperclipException subclasses
            return ToolResult(success=False, error=f"Failed to read clipboard: {exc}")

        message = f"Clipboard contains {len(text)} character(s)." if text else "Clipboard is empty."
        return ToolResult(success=True, data={"message": message, "text": text})


class ClipboardWriteTool:
    """Overwrites the system clipboard with the given text."""

    name = "clipboard_write"
    description = "Write text to the system clipboard, replacing its current content."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "Text to copy to the clipboard."}},
        "required": ["text"],
    }
    permission = PermissionLevel.CONFIRM
    platforms = ["desktop"]
    requires_confirmation = True

    def handler(self, text: str, **kwargs: Any) -> ToolResult:
        try:
            pyperclip.copy(text)
        except Exception as exc:
            return ToolResult(success=False, error=f"Failed to write clipboard: {exc}")

        return ToolResult(success=True, data={"message": f"Copied {len(text)} character(s) to clipboard."})


clipboard_read_tool = ClipboardReadTool()
clipboard_write_tool = ClipboardWriteTool()
