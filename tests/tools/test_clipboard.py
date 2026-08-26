"""
Clipboard tool tests (§19, file 11 prompt 3).

`pyperclip` is fully mocked throughout -- no test here touches the real system
clipboard. Covers `clipboard_read`'s SAFE read path and `clipboard_write`'s CONFIRM
write path (including exception handling from `pyperclip`, which raises its own
`PyperclipException` subclasses rather than returning an error value).
"""

from __future__ import annotations

from unittest.mock import patch

from app.core.permissions import PermissionLevel
from app.tools.clipboard import ClipboardReadTool, ClipboardWriteTool


def test_clipboard_read_returns_current_text():
    tool = ClipboardReadTool()

    with patch("app.tools.clipboard.pyperclip.paste", return_value="hello world") as mock_paste:
        result = tool.handler()

    assert result.success is True
    assert result.data["text"] == "hello world"
    mock_paste.assert_called_once_with()


def test_clipboard_read_reports_empty_clipboard():
    tool = ClipboardReadTool()

    with patch("app.tools.clipboard.pyperclip.paste", return_value=""):
        result = tool.handler()

    assert result.success is True
    assert result.data["text"] == ""
    assert "empty" in result.data["message"].lower()


def test_clipboard_read_failure_is_reported_not_raised():
    tool = ClipboardReadTool()

    with patch("app.tools.clipboard.pyperclip.paste", side_effect=RuntimeError("no display")):
        result = tool.handler()

    assert result.success is False
    assert "Failed to read clipboard" in result.error


def test_clipboard_write_copies_given_text():
    tool = ClipboardWriteTool()

    with patch("app.tools.clipboard.pyperclip.copy") as mock_copy:
        result = tool.handler(text="copied text")

    assert result.success is True
    mock_copy.assert_called_once_with("copied text")


def test_clipboard_write_failure_is_reported_not_raised():
    tool = ClipboardWriteTool()

    with patch("app.tools.clipboard.pyperclip.copy", side_effect=RuntimeError("no display")):
        result = tool.handler(text="copied text")

    assert result.success is False
    assert "Failed to write clipboard" in result.error


def test_clipboard_read_is_safe_and_clipboard_write_is_confirm():
    """§19: reading only observes state (SAFE); writing destroys whatever's currently
    on the clipboard, so it needs the same confirmed=True gate as create_file/
    close_application (CONFIRM), not SAFE.
    """
    assert ClipboardReadTool().permission == PermissionLevel.SAFE
    assert ClipboardReadTool().requires_confirmation is False

    assert ClipboardWriteTool().permission == PermissionLevel.CONFIRM
    assert ClipboardWriteTool().requires_confirmation is True


def test_both_clipboard_tools_are_desktop_only():
    assert ClipboardReadTool().platforms == ["desktop"]
    assert ClipboardWriteTool().platforms == ["desktop"]
