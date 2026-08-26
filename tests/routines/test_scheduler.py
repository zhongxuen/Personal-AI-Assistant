"""
RoutineScheduler tests (§37 Phase 3 / file 04 prompt 2, optional).

`RoutineScheduler` never gets its own `BackgroundScheduler` in production -- it's
handed `ReminderScheduler.scheduler`, the same background thread file 04 prompt 1
already runs. These tests pass a `Mock` standing in for that instance, so they can
assert *what* gets registered/removed on it without spinning up a real background
thread. `_run()` (what APScheduler would actually call when a cron job fires) is
exercised directly, going through `RoutineEngine.run()` -> `ToolExecutor`, same as
run_routine's manual-trigger path.
"""

from __future__ import annotations

from unittest.mock import Mock, call

from app.routines.registry import RoutineRegistry
from app.routines.scheduler import RoutineScheduler
from app.tools.applications import open_application_tool, seed_default_applications
from app.tools.registry import ToolRegistry


def _tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(open_application_tool)
    return registry


def test_schedule_registers_a_cron_job_on_the_shared_scheduler_instance():
    mock_scheduler = Mock()
    routine_scheduler = RoutineScheduler(mock_scheduler, _tool_registry())

    job_id = routine_scheduler.schedule("coding", "0 9 * * mon-fri")

    assert job_id == "routine:coding"
    assert mock_scheduler.add_job.call_count == 1
    _, kwargs = mock_scheduler.add_job.call_args
    assert kwargs["id"] == "routine:coding"
    assert kwargs["replace_existing"] is True
    assert routine_scheduler.scheduled_routines() == ["coding"]


def test_schedule_same_routine_twice_replaces_rather_than_duplicates():
    mock_scheduler = Mock()
    routine_scheduler = RoutineScheduler(mock_scheduler, _tool_registry())

    routine_scheduler.schedule("coding", "0 9 * * *")
    routine_scheduler.schedule("coding", "0 10 * * *")

    assert routine_scheduler.scheduled_routines() == ["coding"]
    assert mock_scheduler.add_job.call_count == 2


def test_unschedule_removes_a_registered_job():
    mock_scheduler = Mock()
    routine_scheduler = RoutineScheduler(mock_scheduler, _tool_registry())
    routine_scheduler.schedule("coding", "0 9 * * *")

    removed = routine_scheduler.unschedule("coding")

    assert removed is True
    mock_scheduler.remove_job.assert_called_once_with("routine:coding")
    assert routine_scheduler.scheduled_routines() == []


def test_unschedule_unknown_routine_returns_false():
    mock_scheduler = Mock()
    routine_scheduler = RoutineScheduler(mock_scheduler, _tool_registry())

    assert routine_scheduler.unschedule("nope") is False
    mock_scheduler.remove_job.assert_not_called()


def test_run_fires_the_routine_through_the_engine(monkeypatch, test_db):
    mock_startfile, mock_popen = Mock(), Mock()
    monkeypatch.setattr("app.tools.applications.os.startfile", mock_startfile, raising=False)
    monkeypatch.setattr("app.tools.applications.subprocess.Popen", mock_popen)
    seed_default_applications()  # "vscode" alias must resolve via MemoryService now

    db = test_db()
    RoutineRegistry(db).create_routine("coding", [("open_application", {"app_name": "vscode"})])
    db.close()

    mock_scheduler = Mock()
    routine_scheduler = RoutineScheduler(mock_scheduler, _tool_registry())

    routine_scheduler._run("coding")

    import sys

    launched = mock_startfile if sys.platform == "win32" else mock_popen
    launched.assert_called_once()
