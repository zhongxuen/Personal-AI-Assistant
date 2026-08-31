"""
Task tools (§37 Phase 3 / file 04 prompt 1).

Thin `Tool` wrappers around `TaskService` (`app/tasks/service.py`): create_task,
list_tasks, complete_task, edit_task, delete_task. All SAFE except `delete_task`
(CONFIRM -- deleting a task is destructive and hard to undo, §19) and platform-agnostic
(§22) -- tasks aren't tied to any one surface. `ToolExecutor` only ever calls
`tool.handler(**params)` (§18), so each handler opens and closes its own short-lived DB
session rather than expecting one to be injected.

`due` accepts either an ISO 8601 string or a natural-language phrase ("tomorrow at
8pm", "next friday", "in 2 hours") -- `TaskService`/`dateparser` handle both, no LLM
call involved.
"""

from __future__ import annotations

from typing import Any

from app.core.permissions import PermissionLevel
from app.database.database import SessionLocal
from app.database.models import Task
from app.tasks.service import UNSET, VALID_PRIORITIES, TaskService
from app.tools.base import ToolResult

# Every surface a task command can legitimately come from. `"whatsapp"` (file 18) is on
# this list for the same reason `"discord"` and `"mobile"` are: a task lives in the
# database, not on a machine, so "add a task"/"what are my tasks" means the same thing
# from any authenticated conversation. WhatsApp reaches this list only for a *linked*
# number -- an unlinked sender never gets as far as ToolExecutor at all
# (`app/api/routes/whatsapp_webhook.py`).
PLATFORMS = ["desktop", "web", "discord", "mobile", "whatsapp"]

_PRIORITY_ERROR = f"Priority must be one of: {', '.join(VALID_PRIORITIES)}."


def _task_to_dict(task: Task, service: TaskService) -> dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "due": task.due_at.isoformat() if task.due_at else None,
        "category": task.category,
        "priority": task.priority,
        "overdue": service.is_overdue(task),
    }


class CreateTaskTool:
    """Creates a task from a title plus optional due date/time, category, and priority."""

    name = "create_task"
    description = (
        "Create a new task with a title and optional due date/time (ISO 8601 or a "
        "natural phrase like 'tomorrow at 8pm'), category, and priority (low/medium/high)."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "What the task is."},
            "due": {
                "type": "string",
                "description": "Optional due date/time -- ISO 8601 or a natural phrase.",
            },
            "category": {"type": "string", "description": "Optional free-text category."},
            "priority": {
                "type": "string",
                "description": "Optional priority: low, medium, or high. Defaults to medium.",
            },
        },
        "required": ["title"],
    }
    permission = PermissionLevel.SAFE
    platforms = PLATFORMS
    requires_confirmation = False

    def handler(
        self,
        title: str,
        due: str | None = None,
        category: str | None = None,
        priority: str | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        title = title.strip()
        if not title:
            return ToolResult(success=False, error="A task needs a non-empty title.")

        db = SessionLocal()
        try:
            service = TaskService(db)
            try:
                task = service.create(title=title, due=due, category=category, priority=priority)
            except ValueError:
                return ToolResult(success=False, error=_PRIORITY_ERROR)

            data = _task_to_dict(task, service)
            data["message"] = f"Created task: '{task.title}'." + (
                f" Due {data['due']}." if data["due"] else ""
            )
            return ToolResult(success=True, data=data)
        finally:
            db.close()


class ListTasksTool:
    """Lists tasks, optionally filtered by status, category, and due-date range."""

    name = "list_tasks"
    description = "List tasks, optionally filtered by status, category, or due-date range."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "status": {"type": "string", "description": "Filter by status, e.g. 'pending' or 'completed'."},
            "category": {"type": "string", "description": "Filter by category (exact match)."},
            "due_before": {
                "type": "string",
                "description": "Only tasks due at/before this date/time (ISO or natural phrase).",
            },
            "due_after": {
                "type": "string",
                "description": "Only tasks due at/after this date/time (ISO or natural phrase).",
            },
            "overdue_only": {
                "type": "boolean",
                "description": "Only include overdue tasks (pending, with a due date in the past).",
            },
        },
        "required": [],
    }
    permission = PermissionLevel.SAFE
    platforms = PLATFORMS
    requires_confirmation = False

    def handler(
        self,
        status: str | None = None,
        category: str | None = None,
        due_before: str | None = None,
        due_after: str | None = None,
        overdue_only: bool = False,
        **kwargs: Any,
    ) -> ToolResult:
        db = SessionLocal()
        try:
            service = TaskService(db)
            tasks = service.list(
                status=status,
                category=category,
                due_before=due_before,
                due_after=due_after,
                overdue_only=bool(overdue_only),
            )
            items = [_task_to_dict(task, service) for task in tasks]
            if items:
                message = "; ".join(
                    f"[{item['id']}] {item['title']} ({item['status']}"
                    + (", OVERDUE" if item["overdue"] else "")
                    + ")"
                    for item in items
                )
            else:
                message = "No tasks match those filters." if any(
                    v is not None and v is not False for v in (status, category, due_before, due_after, overdue_only)
                ) else "You have no tasks."
            return ToolResult(success=True, data={"message": message, "tasks": items})
        finally:
            db.close()


class CompleteTaskTool:
    """Marks a task as completed by id."""

    name = "complete_task"
    description = "Mark a task as completed by its id."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task_id": {"type": "integer", "description": "The id of the task to complete."}
        },
        "required": ["task_id"],
    }
    permission = PermissionLevel.SAFE
    platforms = PLATFORMS
    requires_confirmation = False

    def handler(self, task_id: int, **kwargs: Any) -> ToolResult:
        db = SessionLocal()
        try:
            service = TaskService(db)
            task = service.complete(task_id)
            if task is None:
                return ToolResult(success=False, error=f"No task found with id {task_id}.")
            data = _task_to_dict(task, service)
            data["message"] = f"Completed task: '{task.title}'."
            return ToolResult(success=True, data=data)
        finally:
            db.close()


class EditTaskTool:
    """Edits a task's title, due date, category, priority, and/or status by id.

    Only fields actually present in `params` are changed -- `TaskService.edit` uses a
    sentinel (`UNSET`) to tell "not supplied" apart from "supplied as empty", so passing
    `due=""` or `category=""` explicitly clears that field instead of leaving it alone.
    """

    name = "edit_task"
    description = "Edit a task's title, due date, category, priority, or status by id."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task_id": {"type": "integer", "description": "The id of the task to edit."},
            "title": {"type": "string", "description": "New title."},
            "due": {
                "type": "string",
                "description": "New due date/time (ISO or natural phrase). Pass '' to clear it.",
            },
            "category": {"type": "string", "description": "New category. Pass '' to clear it."},
            "priority": {"type": "string", "description": "New priority: low, medium, or high."},
            "status": {"type": "string", "description": "New status, e.g. 'pending' or 'completed'."},
        },
        "required": ["task_id"],
    }
    permission = PermissionLevel.SAFE
    platforms = PLATFORMS
    requires_confirmation = False

    def handler(
        self,
        task_id: int,
        title: str = UNSET,
        due: str = UNSET,
        category: str = UNSET,
        priority: str = UNSET,
        status: str = UNSET,
        **kwargs: Any,
    ) -> ToolResult:
        if title is not UNSET and not title.strip():
            return ToolResult(success=False, error="Title can't be blank.")
        if priority is not UNSET and priority.strip().lower() not in VALID_PRIORITIES:
            return ToolResult(success=False, error=_PRIORITY_ERROR)

        db = SessionLocal()
        try:
            service = TaskService(db)
            task = service.edit(
                task_id,
                title=title.strip() if title is not UNSET else UNSET,
                due=(due or None) if due is not UNSET else UNSET,
                category=(category or None) if category is not UNSET else UNSET,
                priority=priority if priority is not UNSET else UNSET,
                status=status.strip().lower() if status is not UNSET else UNSET,
            )
            if task is None:
                return ToolResult(success=False, error=f"No task found with id {task_id}.")
            data = _task_to_dict(task, service)
            data["message"] = f"Updated task: '{task.title}'."
            return ToolResult(success=True, data=data)
        finally:
            db.close()


class DeleteTaskTool:
    """Permanently deletes a task (and any pending reminder for it) by id."""

    name = "delete_task"
    description = "Permanently delete a task by its id."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task_id": {"type": "integer", "description": "The id of the task to delete."}
        },
        "required": ["task_id"],
    }
    permission = PermissionLevel.CONFIRM
    platforms = PLATFORMS
    requires_confirmation = True

    def handler(self, task_id: int, **kwargs: Any) -> ToolResult:
        db = SessionLocal()
        try:
            service = TaskService(db)
            task = service.get(task_id)
            if task is None:
                return ToolResult(success=False, error=f"No task found with id {task_id}.")
            title = task.title
            service.delete(task_id)
            return ToolResult(success=True, data={"message": f"Deleted task: '{title}'.", "id": task_id})
        finally:
            db.close()


create_task_tool = CreateTaskTool()
list_tasks_tool = ListTasksTool()
complete_task_tool = CompleteTaskTool()
edit_task_tool = EditTaskTool()
delete_task_tool = DeleteTaskTool()
