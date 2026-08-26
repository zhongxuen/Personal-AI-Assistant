"""
Rule-based tool relevance filter (file 08 -- see file 05's note in
`AssistantCore._handle_needs_llm`: passing the *entire* registered tool set to the LLM
was always meant to be temporary).

`select_relevant_tools` narrows the tools offered to an LLM call down to whichever
handful are plausibly relevant to the user's message, instead of dumping every
registered tool into the prompt on every turn. This keeps the tool list small (cheaper,
faster, less room for the model to pick the wrong tool) without any embeddings or ML
classification -- just keyword matching, so the behavior is easy to read straight out
of `_CATEGORY_KEYWORDS` below and easy to extend when a new tool category shows up.

Design: each category (task/routine/timer/notification/application/system) has a list
of keywords a real message would plausibly contain, plus the canonical tool names that
category covers. `_CATEGORY_TOOL_NAMES` was built by hand by reading each tool's
`name`/`description` in `app/tools/*.py` (not by re-matching keywords against
description text at runtime) -- tool descriptions share enough vocabulary (e.g.
`create_task`'s "optional due date/time" contains both "date" and "time") that
matching keywords against them live would pull unrelated tools into the wrong
category. An explicit, hand-built mapping is simpler, avoids that false-positive risk
entirely, and stays just as easy to explain: "task keywords -> the five task tools",
full stop.

A message can match more than one category (e.g. "open vscode and remind me to commit"
matches both "application" and "task") -- the result is the union of every matching
category's tools, still filtered down to whatever the caller actually passed in
`all_tools` (this function only narrows, it never adds a tool the caller didn't already
offer).
"""

from __future__ import annotations

from app.tools.base import Tool

# Keywords checked against the lowercased message. Plain substrings, not regex/stemmed
# -- keep entries as the obvious, common phrasing a user would actually type rather
# than trying to be clever/exhaustive.
_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "task": ["task", "todo", "to-do", "to do", "remind", "reminder", "deadline"],
    "routine": ["routine", "workflow"],
    "timer": ["timer", "countdown", "alarm"],
    "notification": ["notify", "notification", "alert", "pop up", "popup"],
    "application": ["open", "launch", "close", "quit", "application"],
    "system": ["system info", "cpu", "memory", "what time", "what day", "current time", "clock"],
}

# Canonical tool names each category covers -- see the module docstring for why this is
# a hand-built mapping rather than a live keyword-vs-description match.
_CATEGORY_TOOL_NAMES: dict[str, list[str]] = {
    "task": ["create_task", "list_tasks", "complete_task", "edit_task", "delete_task"],
    "routine": ["run_routine"],
    "timer": ["start_timer"],
    "notification": ["show_notification"],
    "application": ["open_application", "close_application"],
    "system": ["get_time", "get_system_info"],
}

# Fallback for an "ambiguous" message -- one that matches none of the categories above.
# Rather than fall back to "every registered tool" (file 05's behavior, and the whole
# reason this module exists) or "no tools at all" (which would silently strip the LLM's
# ability to act on a perfectly reasonable free-form request), default to the small set
# of tools most likely to be useful regardless of what the user is actually asking:
# reading/creating tasks (the assistant's primary job, and the most common thing an
# open-ended request turns out to want -- e.g. "what do I have going on today?") plus
# the current time (cheap, frequently-needed context for anything date/schedule-ish).
DEFAULT_TOOL_NAMES: list[str] = ["list_tasks", "create_task", "get_time"]


def select_relevant_tools(message: str, all_tools: list[Tool]) -> list[Tool]:
    """Narrow `all_tools` to the ones plausibly relevant to `message`.

    Lowercases `message` and checks it against each category's keyword list in
    `_CATEGORY_KEYWORDS`; every matching category contributes its tools (via
    `_CATEGORY_TOOL_NAMES`), and the result is their union, filtered to tools actually
    present in `all_tools`. If no category matches, falls back to `DEFAULT_TOOL_NAMES`
    (see its docstring above for what "default" means and why).
    """
    lowered = message.lower()
    matched_categories = [
        category
        for category, keywords in _CATEGORY_KEYWORDS.items()
        if any(keyword in lowered for keyword in keywords)
    ]

    if matched_categories:
        selected_names = {
            name for category in matched_categories for name in _CATEGORY_TOOL_NAMES[category]
        }
    else:
        selected_names = set(DEFAULT_TOOL_NAMES)

    return [tool for tool in all_tools if tool.name in selected_names]
