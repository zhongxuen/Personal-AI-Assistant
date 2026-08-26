"""
Task routes (Task Dashboard backend).

Thin HTTP wrappers around `TaskService` (`app/tasks/service.py`) -- same split as
`app/api/routes/assistant.py` (§41 Rule 7): every route here only (1) validates the
request, (2) calls into `TaskService`, and (3) shapes the response. Due-date parsing,
priority validation, overdue detection, and reminder sync all stay inside `TaskService`;
nothing here re-implements them.

Distinct from `app/tools/tasks.py` (the LLM/CommandRouter-facing `Tool` wrappers around
the same service) -- these routes exist for the Task Dashboard frontend to hit directly
over plain REST, without going through `ToolExecutor`/`PermissionChecker`. Both layers
are thin shells around `TaskService`; neither duplicates its logic.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.database.models import Task
from app.tasks.service import TaskService

router = APIRouter(tags=["tasks"])


class TaskOut(BaseModel):
    id: int
    title: str
    status: str
    due: str | None = None
    category: str | None = None
    priority: str
    overdue: bool


class TaskCreate(BaseModel):
    title: str
    due: str | None = None
    category: str | None = None
    priority: str | None = None


class TaskUpdate(BaseModel):
    """All fields optional; only fields actually present in the request body are
    changed (mirrors `EditTaskTool`/`TaskService.edit`'s UNSET-vs-None distinction --
    see `_to_edit_kwargs` below).
    """

    title: str | None = None
    due: str | None = None
    category: str | None = None
    priority: str | None = None
    status: str | None = None


def _serialize(task: Task, service: TaskService) -> dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "due": task.due_at.isoformat() if task.due_at else None,
        "category": task.category,
        "priority": task.priority,
        "overdue": service.is_overdue(task),
    }


def _to_edit_kwargs(payload: TaskUpdate) -> dict[str, Any]:
    """Only the fields the client actually sent, so unsent fields fall through to
    `TaskService.edit`'s UNSET default (leave unchanged) instead of being overwritten
    with None. A sent `null` (e.g. `{"due": null}`) comes through as an explicit None,
    which `TaskService.edit` treats as "clear this field".
    """
    return payload.model_dump(exclude_unset=True)


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(
    status: str | None = None,
    category: str | None = None,
    due_before: str | None = None,
    due_after: str | None = None,
    overdue_only: bool = False,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    service = TaskService(db)
    tasks = service.list(
        status=status,
        category=category,
        due_before=due_before,
        due_after=due_after,
        overdue_only=overdue_only,
    )
    return [_serialize(task, service) for task in tasks]


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(task_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    service = TaskService(db)
    task = service.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"No task found with id {task_id}.")
    return _serialize(task, service)


@router.post("/tasks", response_model=TaskOut, status_code=201)
def create_task(payload: TaskCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="A task needs a non-empty title.")

    service = TaskService(db)
    try:
        task = service.create(
            title=title, due=payload.due, category=payload.category, priority=payload.priority
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _serialize(task, service)


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(task_id: int, payload: TaskUpdate, db: Session = Depends(get_db)) -> dict[str, Any]:
    fields = _to_edit_kwargs(payload)
    if "title" in fields and not (fields["title"] or "").strip():
        raise HTTPException(status_code=422, detail="Title can't be blank.")
    if "status" in fields and not fields["status"]:
        raise HTTPException(status_code=422, detail="Status can't be blank.")

    service = TaskService(db)
    try:
        task = service.edit(task_id, **fields)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=404, detail=f"No task found with id {task_id}.")
    return _serialize(task, service)


@router.post("/tasks/{task_id}/complete", response_model=TaskOut)
def complete_task(task_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    service = TaskService(db)
    task = service.complete(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"No task found with id {task_id}.")
    return _serialize(task, service)


@router.delete("/tasks/{task_id}", status_code=204, response_model=None)
def delete_task(task_id: int, db: Session = Depends(get_db)) -> None:
    service = TaskService(db)
    if not service.delete(task_id):
        raise HTTPException(status_code=404, detail=f"No task found with id {task_id}.")
