"""
`run_terminal_command` allow-list tests (§23, §33, §41 Rule 6, file 11 prompt 3).

Covers the core promise of `app/tools/terminal.py`: only an exact, allow-listed alias
ever reaches `subprocess.run`, arguments are validated against each alias's `params`
regex before substitution, and anything else -- an unknown alias, an attempted raw
shell string, an argument that fails its regex -- is rejected with a clear
`ToolResult(success=False, ...)`, never silently run. `subprocess.run` is mocked
throughout: no test here executes a real OS command.
"""

from __future__ import annotations

import subprocess
from unittest.mock import Mock, patch

from app.tools.terminal import ALLOWED_COMMANDS, RunTerminalCommandTool


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> Mock:
    completed = Mock(spec=subprocess.CompletedProcess)
    completed.returncode = returncode
    completed.stdout = stdout
    completed.stderr = stderr
    return completed


def test_runs_an_allow_listed_command_with_no_params():
    tool = RunTerminalCommandTool()

    with patch("app.tools.terminal.subprocess.run", return_value=_completed(0, "ok", "")) as mock_run:
        result = tool.handler(alias="list_directory")

    assert result.success is True
    assert result.data["returncode"] == 0
    mock_run.assert_called_once()
    called_argv, called_kwargs = mock_run.call_args
    assert called_argv[0] == ALLOWED_COMMANDS["list_directory"]["template"]
    assert called_kwargs["shell"] is False


def test_alias_lookup_is_case_insensitive():
    tool = RunTerminalCommandTool()

    with patch("app.tools.terminal.subprocess.run", return_value=_completed(0)) as mock_run:
        result = tool.handler(alias="LIST_DIRECTORY")

    assert result.success is True
    mock_run.assert_called_once()


def test_unknown_alias_is_rejected_without_running_anything():
    tool = RunTerminalCommandTool()

    with patch("app.tools.terminal.subprocess.run") as mock_run:
        result = tool.handler(alias="rm -rf /")

    assert result.success is False
    assert "not an allow-listed command" in result.error
    mock_run.assert_not_called()


def test_substitutes_a_valid_placeholder_argument():
    tool = RunTerminalCommandTool()

    with patch("app.tools.terminal.subprocess.run", return_value=_completed(0)) as mock_run:
        result = tool.handler(alias="ping_host", args={"host": "8.8.8.8"})

    assert result.success is True
    called_argv = mock_run.call_args[0][0]
    assert called_argv == ["ping", "-n", "4", "8.8.8.8"]


def test_rejects_a_placeholder_argument_that_fails_its_regex():
    """A value that doesn't look like a hostname/IP -- including an attempted shell
    injection payload -- must be rejected before it ever reaches subprocess.run, not
    merely fail once passed through.
    """
    tool = RunTerminalCommandTool()

    with patch("app.tools.terminal.subprocess.run") as mock_run:
        result = tool.handler(alias="ping_host", args={"host": "8.8.8.8; rm -rf /"})

    assert result.success is False
    assert "invalid value" in result.error
    mock_run.assert_not_called()


def test_rejects_missing_required_argument():
    tool = RunTerminalCommandTool()

    with patch("app.tools.terminal.subprocess.run") as mock_run:
        result = tool.handler(alias="ping_host", args={})

    assert result.success is False
    assert "Missing required argument" in result.error
    mock_run.assert_not_called()


def test_rejects_unrecognized_argument_name():
    tool = RunTerminalCommandTool()

    with patch("app.tools.terminal.subprocess.run") as mock_run:
        result = tool.handler(alias="ping_host", args={"host": "8.8.8.8", "extra": "x"})

    assert result.success is False
    assert "Unrecognized argument" in result.error
    mock_run.assert_not_called()


def test_never_invoked_with_shell_true():
    """Every allow-listed command must run as an argv list with shell=False -- the
    whole point of the allow-list design is that there's no shell to inject into.
    """
    tool = RunTerminalCommandTool()

    with patch("app.tools.terminal.subprocess.run", return_value=_completed(0)) as mock_run:
        tool.handler(alias="disk_usage")

    assert mock_run.call_args.kwargs["shell"] is False


def test_timeout_is_reported_as_a_tool_failure():
    tool = RunTerminalCommandTool()

    with patch(
        "app.tools.terminal.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="ping", timeout=15.0),
    ):
        result = tool.handler(alias="ping_host", args={"host": "8.8.8.8"})

    assert result.success is False
    assert "timed out" in result.error


def test_output_is_truncated_to_the_max_output_cap():
    from app.tools.terminal import _MAX_OUTPUT_CHARS

    huge_output = "x" * (_MAX_OUTPUT_CHARS + 500)
    tool = RunTerminalCommandTool()

    with patch("app.tools.terminal.subprocess.run", return_value=_completed(0, huge_output, "")):
        result = tool.handler(alias="list_directory")

    assert len(result.data["stdout"]) == _MAX_OUTPUT_CHARS
