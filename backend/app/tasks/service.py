"""
Task service (§37 Phase 3 / file 04 prompt 1).

Upgrades file 03's minimal CRUD (`app.database.models.Task`) with: natural-language
due-date parsing (`dateparser`, a lightweight local parser -- no LLM call), categories,
priority, filtering (status/category/due-range/overdue), overdue detection, and
edit/delete. `app.tools.tasks` wraps this as `Tool` implementations and
`app.core.command_router` calls `split_title_and_due` directly for the "remind me to X
[time phrase]" alias; nothing else should touch the `Task`/`TaskReminder` models
directly.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import dateparser
from dateparser.search import search_dates
from sqlalchemy.orm import Session

from app.core.clock import local_now
from app.database.models import Task, TaskReminder

VALID_PRIORITIES = ("low", "medium", "high")
DEFAULT_PRIORITY = "medium"

# Sentinel distinguishing "field not supplied -- leave unchanged" from "field supplied
# as None/empty -- clear it" in TaskService.edit(). A plain `None` default can't do
# this since `due=None` is itself a meaningful ("no due date") value.
UNSET: Any = object()

_DATEPARSER_SETTINGS: dict[str, Any] = {
    "PREFER_DATES_FROM": "future",
    "RETURN_AS_TIMEZONE_AWARE": False,
}


def parse_due(text: str | None, *, base: datetime | None = None) -> datetime | None:
    """Parse a single due-date/time phrase -- "tomorrow at 8pm", "next friday", "in 2
    hours", or a plain ISO 8601 string -- into a `datetime`. Relative phrases resolve
    against `base` (defaults to now).

    Tries `dateparser.parse` first (handles most phrasings and ISO strings directly),
    then falls back to `dateparser.search.search_dates` for phrasings it recognizes but
    doesn't parse standalone (e.g. "next friday" resolves via search but not via a bare
    `.parse()` call, a known dateparser quirk). Returns None -- never raises -- if
    nothing looks like a date, so a bad phrase just means "no due date" rather than a
    500.
    """
    if not text or not text.strip():
        return None
    text = text.strip()
    settings = dict(_DATEPARSER_SETTINGS)
    # `local_now()`, not `datetime.now()`: "tomorrow at 8pm" means 8pm on the
    # user's clock, and the host's clock is only the same thing on a desktop
    # install (deployed, it's UTC -- see `app.core.clock`).
    settings["RELATIVE_BASE"] = base or local_now()

    parsed = dateparser.parse(text, settings=settings)
    if parsed is not None:
        return parsed

    found = search_dates(text, settings=settings, languages=["en"])
    return found[-1][1] if found else None


def split_title_and_due(text: str, *, base: datetime | None = None) -> tuple[str, datetime | None]:
    """Split a raw phrase like "buy milk tomorrow at 8pm" into ("buy milk",
    datetime(...)) by locating an embedded date/time phrase anywhere in the text (via
    `search_dates`) and stripping it back out. Used by `CommandRouter` for the "remind
    me to X [time phrase]" alias instead of dumping the whole remainder -- time phrase
    included -- into the task title (file 03's naive behavior).

    Falls back to `(text, None)` unchanged if no date phrase is found, so "buy milk"
    stays exactly "buy milk" rather than losing words to a false-positive match.
    """
    text = text.strip()
    if not text:
        return text, None
    settings = dict(_DATEPARSER_SETTINGS)
    # `local_now()`, not `datetime.now()`: "tomorrow at 8pm" means 8pm on the
    # user's clock, and the host's clock is only the same thing on a desktop
    # install (deployed, it's UTC -- see `app.core.clock`).
    settings["RELATIVE_BASE"] = base or local_now()

    found = search_dates(text, settings=settings, languages=["en"])
    if not found:
        return text, None

    phrase, due = found[-1]
    remainder = text.replace(phrase, "", 1)
    remainder = re.sub(r"\s+", " ", remainder).strip(" ,.-")
    if not remainder:
        remainder = text  # whole message was the date phrase -- don't leave an empty title
    return remainder, due


def _coerce_due(value: datetime | str | None) -> datetime | None:
    """Accept either an already-parsed `datetime` or a raw phrase/ISO string."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return parse_due(value)


def _normalize_priority(priority: str | None) -> str:
    priority = (priority or DEFAULT_PRIORITY).strip().lower()
    if priority not in VALID_PRIORITIES:
        raise ValueError(f"Priority must be one of: {', '.join(VALID_PRIORITIES)}.")
    return priority


class TaskService:
    """create / list / edit / complete / delete against a caller-supplied `Session`."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        title: str,
        due: datetime | str | None = None,
        category: str | None = None,
        priority: str | None = None,
    ) -> Task:
        task = Task(
            title=title,
            due_at=_coerce_due(due),
            category=(category.strip() or None) if category else None,
            priority=_normalize_priority(priority),
            status="pending",
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        self._sync_reminder(task)
        return task

    def get(self, task_id: int) -> Task | None:
        return self.db.get(Task, task_id)

    def list(
        self,
        *,
        status: str | None = None,
        category: str | None = None,
        due_before: datetime | str | None = None,
        due_after: datetime | str | None = None,
        overdue_only: bool = False,
    ) -> list[Task]:
        query = self.db.query(Task)
        if status:
            query = query.filter(Task.status == status)
        if category:
            query = query.filter(Task.category == category)

        before = _coerce_due(due_before)
        after = _coerce_due(due_after)
        if before is not None:
            query = query.filter(Task.due_at.isnot(None), Task.due_at <= before)
        if after is not None:
            query = query.filter(Task.due_at.isnot(None), Task.due_at >= after)

        if overdue_only:
            query = query.filter(
                Task.status == "pending",
                Task.due_at.isnot(None),
                # Same clock the due date was parsed against, so "overdue" means
                # overdue for the user, not for the server's timezone.
                Task.due_at < local_now(),
            )

        return query.order_by(Task.created_at).all()

    def is_overdue(self, task: Task) -> bool:
        return task.status == "pending" and task.due_at is not None and task.due_at < local_now()

    def edit(
        self,
        task_id: int,
        *,
        title: str = UNSET,
        due: datetime | str | None = UNSET,
        category: str | None = UNSET,
        priority: str | None = UNSET,
        status: str = UNSET,
    ) -> Task | None:
        task = self.db.get(Task, task_id)
        if task is None:
            return None

        if title is not UNSET:
            task.title = title
        if due is not UNSET:
            task.due_at = _coerce_due(due)
        if category is not UNSET:
            task.category = category or None
        if priority is not UNSET:
            task.priority = _normalize_priority(priority)
        if status is not UNSET:
            task.status = status

        self.db.commit()
        self.db.refresh(task)
        self._sync_reminder(task)
        return task

    def complete(self, task_id: int) -> Task | None:
        return self.edit(task_id, status="completed")

    def delete(self, task_id: int) -> bool:
        task = self.db.get(Task, task_id)
        if task is None:
            return False
        self.db.query(TaskReminder).filter(TaskReminder.task_id == task_id).delete()
        self.db.delete(task)
        self.db.commit()
        return True

    def _sync_reminder(self, task: Task) -> None:
        """Keep `task_reminders` in sync with a task's due date: exactly one pending
        (unsent) reminder firing at `due_at`, or none if the task has no due date or is
        no longer pending. Called after create/edit so `scheduler.py` always has an
        accurate, deduplicated queue to poll -- callers never touch `TaskReminder`
        directly.
        """
        self.db.query(TaskReminder).filter(
            TaskReminder.task_id == task.id, TaskReminder.sent.is_(False)
        ).delete()
        if task.due_at is not None and task.status == "pending":
            self.db.add(TaskReminder(task_id=task.id, remind_at=task.due_at, sent=False))
        self.db.commit()
