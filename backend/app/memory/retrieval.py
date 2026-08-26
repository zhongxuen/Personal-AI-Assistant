"""
Selective memory retrieval (§16, §37 Phase 8 / file 09 prompt 3).

`retrieve_relevant` is the retrieval half of file 08's context-manager hook
(`app.core.context_manager._relevant_memory`, which was left as a guarded no-op until
this module existed -- see its own docstring): given the same free-form message
`app.tools.relevance.select_relevant_tools` classifies for tool narrowing, decide which
`MemoryService` categories (if any) are worth pulling into the LLM's context, instead
of dumping every memory category into every request regardless of relevance.

Reuses `app.tools.relevance.classify_categories` rather than re-implementing a second,
slightly-different classifier -- a message that matches the "routine" tool category is
exactly a message a routine-relevant memory pull should also fire for, so there's one
classification step driving both tool narrowing and memory retrieval, not two
independently drifting copies of the same keyword table.

Design mirrors `app.tools.relevance._CATEGORY_TOOL_NAMES`: an explicit, hand-built
`_CATEGORY_MEMORY_CATEGORIES` table naming which `MemoryService` categories
(`app/memory/service.py`) each matched tool-category pulls in, per file 09's own
example ("a routine-related request pulls routines + applications"). Not every tool
category implies a memory pull -- `timer`/`notification`/`system` messages have no
memory category that would plausibly help answer them, so they're simply absent below
(absence means "no memory relevant for this category", not an oversight).

`task` is the one category file 09's plan calls out as conditional: a task-related
message only pulls `user_preferences` when it actually references a priority/
preference (e.g. "make this high priority", "what's my default category"), not for
every task message -- most task requests (e.g. "add a task to buy milk") have no use
for stored preferences, so pulling it unconditionally would just be context-window
noise for no benefit. `_task_memory_categories` implements that gate separately from
the flat table.
"""

from __future__ import annotations

from typing import Any

from app.database.database import SessionLocal
from app.memory.service import APPLICATIONS, ROUTINES, USER_PREFERENCES, MemoryService
from app.tools.relevance import classify_categories

# Tool category (app.tools.relevance) -> MemoryService categories worth pulling for a
# message that matches it. "task" is deliberately absent -- it's conditional (see
# module docstring) and handled by _task_memory_categories instead.
_CATEGORY_MEMORY_CATEGORIES: dict[str, list[str]] = {
    "routine": [ROUTINES, APPLICATIONS],
    "application": [APPLICATIONS],
}

# Substrings that mark a task-related message as actually about a priority/preference,
# rather than just the task's own content -- gates user_preferences per the module
# docstring, instead of it firing for every task-classified message.
_PREFERENCE_KEYWORDS = ["priority", "prioritize", "preference", "prefer", "default"]


def _task_memory_categories(message: str) -> list[str]:
    lowered = message.lower()
    if any(keyword in lowered for keyword in _PREFERENCE_KEYWORDS):
        return [USER_PREFERENCES]
    return []


def _relevant_memory_categories(request_or_intent: str) -> set[str]:
    matched = classify_categories(request_or_intent)

    categories: set[str] = set()
    for category in matched:
        categories.update(_CATEGORY_MEMORY_CATEGORIES.get(category, []))
    if "task" in matched:
        categories.update(_task_memory_categories(request_or_intent))

    return categories


def retrieve_relevant(request_or_intent: str) -> dict[str, dict[str, Any]]:
    """Whichever `MemoryService` categories are relevant to `request_or_intent` (a
    free-form message -- the same input `app.tools.relevance.select_relevant_tools`
    classifies), each expanded via `MemoryService.list()` into its full key/value map,
    keyed by category name. Returns `{}` (never raises) when no category matched, or
    every matched category turned out to have nothing persisted yet.

    `request_or_intent` accepts the raw message today (matching how
    `app.core.context_manager._relevant_memory` calls it); the name leaves room for a
    caller to eventually pass a pre-classified intent instead, without changing the
    return contract.
    """
    memory_categories = _relevant_memory_categories(request_or_intent)
    if not memory_categories:
        return {}

    db = SessionLocal()
    try:
        service = MemoryService(db)
        result = {category: service.list(category) for category in memory_categories}
    finally:
        db.close()

    # Omit categories with nothing persisted -- same "don't send empty keys" rule
    # context_manager.build_context applies to recent_turns/memory as a whole.
    return {category: entries for category, entries in result.items() if entries}
