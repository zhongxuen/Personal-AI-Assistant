"""
Deterministic tool tests (§37 Phase 2 / file 03, §38, promoted file 09 prompt 2).

One test per tool built in this file, plus the `run_routine` end-to-end path:
  - get_time returns a value
  - open_application resolves an alias to the right launch command (the real OS launch
    call is mocked -- nothing actually opens). Resolution now goes through
    `MemoryService`'s "applications" category (`seed_default_applications()`) against a
    throwaway test DB (`test_db`, from tests/conftest.py) instead of the removed
    hardcoded `APP_MAP` dict -- see tests/memory/test_memory_service.py for
    `MemoryService` CRUD coverage in isolation.
  - close_application resolves an alias to the right process names and calls
    psutil.Process.terminate() on matches (psutil.process_iter is fully mocked -- no
    test in this file ever enumerates or touches a real running process). Also covers
    the NEVER_CLOSE guard: close_application("vscode") must refuse rather than loop
    real processes, since every VS Code process (main, renderers, GPU, extension host)
    shares the image name "Code.exe" -- an unmocked call here would terminate the very
    editor/session running these tests. Keep this regression test even if it looks
    redundant; it's the one guarding against a repeat of that.
  - create_task / list_tasks / complete_task round-trip against a throwaway test DB
    (`test_db`, from tests/conftest.py)
  - run_routine("coding") triggers its three open_application steps, in order, through
    the same ToolExecutor path every standalone tool call uses (§41 Rule 6) -- the OS
    launch call is mocked so nothing actually launches VS Code/Chrome in CI. The
    "coding" routine itself is now a persisted row (via `seed_default_routines`,
    `app/routines/registry.py`/`engine.py`, file 04 prompt 2) rather than the hardcoded
    dict file 03 originally used here, and its middle step's `app_name` now comes from
    `MemoryService.get("routines", "coding")["default_project"]` (file 09 prompt 2)
    rather than a hardcoded "portfolio folder" string -- see tests/routines/ for
    registry/engine coverage in isolation.
"""

from __future__ import annotations

import sys
from unittest.mock import Mock, call

from app.memory.service import APPLICATIONS, DEFAULT_CODING_VALUE, MemoryService
from app.tools import applications as applications_module
from app.tools.applications import (
    DEFAULT_APPLICATION_ALIASES,
    close_application_tool,
    open_application_tool,
    seed_default_applications,
)
from app.tools.registry import ToolRegistry
from app.tools.routines import RunRoutineTool, seed_default_routines
from app.tools.system import get_time_tool
from app.tools.tasks import (
    complete_task_tool,
    create_task_tool,
    delete_task_tool,
    edit_task_tool,
    list_tasks_tool,
)


def _patch_launch(monkeypatch) -> tuple[Mock, Mock]:
    """Mock out both OS-launch paths `open_application` can take, so no test in this
    file ever actually opens an application. Returns (mock_startfile, mock_popen).
    """
    mock_startfile = Mock()
    mock_popen = Mock()
    monkeypatch.setattr(applications_module.os, "startfile", mock_startfile, raising=False)
    monkeypatch.setattr(applications_module.subprocess, "Popen", mock_popen)
    return mock_startfile, mock_popen


def _launched_mock(mock_startfile: Mock, mock_popen: Mock) -> Mock:
    """Whichever of the two launch mocks `open_application` actually calls, per
    `sys.platform` -- matches the branch in `OpenApplicationTool.handler`.
    """
    return mock_startfile if sys.platform == "win32" else mock_popen


def _expected_launch_call(app_key: str):
    command = DEFAULT_APPLICATION_ALIASES[app_key]["command"]
    return call(command[0]) if sys.platform == "win32" else call(command)


# --- get_time ---------------------------------------------------------------------


def test_get_time_returns_a_value():
    result = get_time_tool.handler()

    assert result.success is True
    assert result.data is not None
    assert result.data["message"]
    assert result.data["iso"]


# --- open_application ---------------------------------------------------------------


def test_open_application_resolves_alias_via_memory_service(monkeypatch, test_db):
    mock_startfile, mock_popen = _patch_launch(monkeypatch)
    seed_default_applications()  # persists the "applications" aliases into test_db

    result = open_application_tool.handler(app_name="VS Code")  # alias, mixed case

    assert result.success is True
    assert "vs code" in result.data["message"]
    launched = _launched_mock(mock_startfile, mock_popen)
    launched.assert_called_once_with(*_expected_launch_call("vs code").args)

    unlaunched = mock_popen if launched is mock_startfile else mock_startfile
    unlaunched.assert_not_called()

    # And the entry really did come from the memories table, not an in-code dict.
    db = test_db()
    try:
        assert MemoryService(db).get(APPLICATIONS, "vs code") == DEFAULT_APPLICATION_ALIASES["vs code"]
    finally:
        db.close()


def test_open_application_unknown_app_fails_without_touching_the_system(monkeypatch, test_db):
    mock_startfile, mock_popen = _patch_launch(monkeypatch)
    seed_default_applications()

    result = open_application_tool.handler(app_name="not a real app")

    assert result.success is False
    assert "not a real app" in (result.error or "")
    mock_startfile.assert_not_called()
    mock_popen.assert_not_called()


# --- close_application ---------------------------------------------------------------


def _fake_proc(monkeypatch, pid: int, name: str) -> Mock:
    proc = Mock()
    proc.info = {"pid": pid, "name": name}
    proc.terminate = Mock()
    return proc


def _patch_process_iter(monkeypatch, procs: list[Mock]) -> None:
    """Mock psutil.process_iter so close_application never enumerates or terminates a
    real system process. `procs` stands in for the whole process table.
    """
    monkeypatch.setattr(
        applications_module.psutil, "process_iter", Mock(return_value=procs)
    )


def test_close_application_terminates_matching_process(monkeypatch, test_db):
    seed_default_applications()
    chrome = _fake_proc(monkeypatch, 4242, "chrome.exe")
    other = _fake_proc(monkeypatch, 4243, "notepad.exe")
    _patch_process_iter(monkeypatch, [chrome, other])

    result = close_application_tool.handler(app_name="Chrome")  # alias, mixed case

    assert result.success is True
    assert "4242" in result.data["message"]
    chrome.terminate.assert_called_once()
    other.terminate.assert_not_called()


def test_close_application_no_matching_process_fails(monkeypatch, test_db):
    seed_default_applications()
    other = _fake_proc(monkeypatch, 4243, "notepad.exe")
    _patch_process_iter(monkeypatch, [other])

    result = close_application_tool.handler(app_name="chrome")

    assert result.success is False
    assert "chrome" in (result.error or "").lower()
    other.terminate.assert_not_called()


def test_close_application_unknown_app_fails_without_touching_the_system(monkeypatch, test_db):
    seed_default_applications()
    mock_iter = Mock()
    monkeypatch.setattr(applications_module.psutil, "process_iter", mock_iter)

    result = close_application_tool.handler(app_name="not a real app")

    assert result.success is False
    assert "not a real app" in (result.error or "")
    mock_iter.assert_not_called()


def test_close_application_refuses_to_close_vscode(monkeypatch, test_db):
    """Regression test for the NEVER_CLOSE guard (see applications.py). Every VS Code
    process shares the image name "Code.exe", so close_application("vscode") must
    refuse before ever calling psutil.process_iter -- looping real processes here would
    terminate the editor/session running this test suite itself.
    """
    seed_default_applications()
    mock_iter = Mock()
    monkeypatch.setattr(applications_module.psutil, "process_iter", mock_iter)

    result = close_application_tool.handler(app_name="vscode")

    assert result.success is False
    assert "protected" in (result.error or "").lower()
    mock_iter.assert_not_called()


# --- create_task / list_tasks / complete_task ---------------------------------------


def test_task_tools_round_trip_against_test_db(test_db):
    created = create_task_tool.handler(title="Finish portfolio")
    assert created.success is True
    task_id = created.data["id"]

    listed = list_tasks_tool.handler()
    assert listed.success is True
    assert any(
        item["id"] == task_id and item["status"] == "pending" for item in listed.data["tasks"]
    )

    completed = complete_task_tool.handler(task_id=task_id)
    assert completed.success is True
    assert completed.data["status"] == "completed"

    listed_again = list_tasks_tool.handler()
    assert any(
        item["id"] == task_id and item["status"] == "completed"
        for item in listed_again.data["tasks"]
    )


def test_complete_task_unknown_id_fails(test_db):
    result = complete_task_tool.handler(task_id=999)

    assert result.success is False
    assert "999" in (result.error or "")


# --- run_routine("coding") -----------------------------------------------------------


def test_run_routine_coding_triggers_three_open_application_calls_in_order(monkeypatch, test_db):
    mock_startfile, mock_popen = _patch_launch(monkeypatch)
    seed_default_applications()  # persists the "applications" aliases into test_db
    seed_default_routines()  # persists the "coding" routine into test_db, reading
    # coding.default_project (unset here, so falls back to DEFAULT_CODING_VALUE) off
    # MemoryService rather than a hardcoded "portfolio folder" string.

    registry = ToolRegistry()
    registry.register(open_application_tool)
    routine_tool = RunRoutineTool(registry)

    result = routine_tool.handler(routine_name="coding")

    assert result.success is True
    steps = result.data["steps"]
    assert [step["tool_name"] for step in steps] == ["open_application"] * 3
    assert [step["params"]["app_name"] for step in steps] == [
        "vscode",
        DEFAULT_CODING_VALUE["default_project"],
        "chrome",
    ]
    assert all(step["result"]["success"] for step in steps)

    launched = _launched_mock(mock_startfile, mock_popen)
    assert launched.call_args_list == [
        _expected_launch_call("vscode"),
        _expected_launch_call(DEFAULT_CODING_VALUE["default_project"]),
        _expected_launch_call("chrome"),
    ]


def test_run_routine_coding_uses_default_project_configured_in_memory(monkeypatch, test_db):
    """The routine's middle step really does read `coding.default_project` off
    `MemoryService` at seed time, not just fall back to the same value the hardcoded
    dict used to have -- seed a *different* default_project first and confirm the
    seeded routine's step reflects it.
    """
    _patch_launch(monkeypatch)
    seed_default_applications()
    db = test_db()
    try:
        MemoryService(db).set(
            "routines", "coding", {**DEFAULT_CODING_VALUE, "default_project": "file explorer"}
        )
    finally:
        db.close()

    seed_default_routines()

    registry = ToolRegistry()
    registry.register(open_application_tool)
    routine_tool = RunRoutineTool(registry)

    result = routine_tool.handler(routine_name="coding")

    assert result.success is True
    assert [step["params"]["app_name"] for step in result.data["steps"]] == [
        "vscode",
        "file explorer",
        "chrome",
    ]


def test_run_routine_defaults_to_coding(monkeypatch, test_db):
    """`routine_name` defaults to "coding" so the router alias "start coding" (which
    carries no params) still resolves to the right routine (§11 exact-alias match).
    """
    _patch_launch(monkeypatch)
    seed_default_applications()
    seed_default_routines()  # persists the "coding" routine into test_db

    registry = ToolRegistry()
    registry.register(open_application_tool)
    routine_tool = RunRoutineTool(registry)

    result = routine_tool.handler()

    assert result.success is True
    assert result.data["routine"] == "coding"


def test_run_routine_unknown_name_fails(test_db):
    registry = ToolRegistry()
    registry.register(open_application_tool)
    routine_tool = RunRoutineTool(registry)

    result = routine_tool.handler(routine_name="not-a-routine")

    assert result.success is False
    assert "not-a-routine" in (result.error or "")
