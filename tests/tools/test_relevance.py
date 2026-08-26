"""
Tool relevance filter tests (file 08).

`select_relevant_tools` is pure keyword matching (see `app/tools/relevance.py`'s module
docstring for the full design rationale), so these tests exercise it against the real
tool set rather than fakes -- if a tool's name ever changes, the hand-built
`_CATEGORY_TOOL_NAMES` mapping and these tests should both fail loudly instead of
silently drifting apart.

Covers:
  - a task-related message narrows to exactly the five task tools
  - a routine-related message narrows to exactly `run_routine`
  - an ambiguous message (no category keyword matches) falls back to
    `DEFAULT_TOOL_NAMES` -- documented in `relevance.py` as list_tasks/create_task/
    get_time, the small set most likely to help on an open-ended request
"""

from __future__ import annotations

from app.tools.applications import close_application_tool, open_application_tool
from app.tools.notifications import show_notification_tool
from app.tools.registry import ToolRegistry
from app.tools.relevance import DEFAULT_TOOL_NAMES, select_relevant_tools
from app.tools.routines import RunRoutineTool
from app.tools.system import get_system_info_tool, get_time_tool
from app.tools.tasks import (
    complete_task_tool,
    create_task_tool,
    delete_task_tool,
    edit_task_tool,
    list_tasks_tool,
)
from app.tools.timers import StartTimerTool


def _all_tools() -> list:
    """Every tool this module knows about, spanning every category -- so a test
    asserting "narrows to task tools only" is actually proving the other categories
    got excluded, not just that task tools happen to be the only ones present.
    """
    registry = ToolRegistry()  # only needed to satisfy RunRoutineTool/StartTimerTool's
    # constructors -- neither tool's `.handler` is ever called in this file.
    return [
        create_task_tool,
        list_tasks_tool,
        complete_task_tool,
        edit_task_tool,
        delete_task_tool,
        RunRoutineTool(registry),
        StartTimerTool(registry),
        show_notification_tool,
        open_application_tool,
        close_application_tool,
        get_time_tool,
        get_system_info_tool,
    ]


def _names(tools: list) -> set[str]:
    return {tool.name for tool in tools}


def test_task_related_message_narrows_to_task_tools_only():
    result = select_relevant_tools("remind me to submit my assignment tomorrow", _all_tools())

    assert _names(result) == {
        "create_task",
        "list_tasks",
        "complete_task",
        "edit_task",
        "delete_task",
    }


def test_routine_related_message_narrows_to_routine_tools_only():
    result = select_relevant_tools("run my coding routine", _all_tools())

    assert _names(result) == {"run_routine"}


def test_ambiguous_message_falls_back_to_default_set():
    # No task/routine/timer/notification/application/system keyword appears anywhere
    # in this message -- every category in `_CATEGORY_KEYWORDS` misses.
    result = select_relevant_tools("tell me something interesting", _all_tools())

    assert _names(result) == set(DEFAULT_TOOL_NAMES) == {"list_tasks", "create_task", "get_time"}


def test_default_set_is_only_ever_a_subset_of_tools_actually_offered():
    # The fallback must never hand back a tool the caller didn't include in
    # `all_tools` -- confirm narrowing, not the fallback list itself, drives the
    # result (e.g. a platform that doesn't offer `get_time` shouldn't get it back).
    limited_tools = [create_task_tool, show_notification_tool]

    result = select_relevant_tools("tell me something interesting", limited_tools)

    assert _names(result) == {"create_task"}
