"""
Memory settings routes (§37 Phase 8 / file 09 prompt 3).

Thin HTTP wrappers around `MemoryService` (`app/memory/service.py`) -- same split as
`app/api/routes/tasks.py`/`app/api/routes/routines.py` (§41 Rule 7): every route here
only validates the request, calls into `MemoryService`, and shapes the response.
JSON encode/decode and persistence stay inside `MemoryService`; nothing here
re-implements them.

Scope is deliberately narrow, matching file 09's own "settings UI can stay minimal"
call: application mappings (the `applications` category `app/tools/applications.py`
resolves `open_application`/`close_application` against) and the "coding" routine's
`default_project` (the `routines` category's `coding` key, per
`app/tools/routines.py`). Not a general key/value editor over every memory category --
if a future settings surface needs more, it can add routes here without touching this
module's existing ones.

Every route here requires a valid bearer token (§34, file 12 prompt 1, router-level
`get_current_user` dependency) -- this settings surface is reachable over the web, not
gated by `app.api.local_only`'s loopback check.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.database.database import get_db
from app.memory.service import (
    APPLICATIONS,
    DEFAULT_CODING_CATEGORY,
    DEFAULT_CODING_KEY,
    DEFAULT_CODING_VALUE,
    MemoryService,
)

router = APIRouter(prefix="/memory", tags=["memory"], dependencies=[Depends(get_current_user)])


class ApplicationMappingOut(BaseModel):
    command: list[str]
    process_names: list[str] = []


class ApplicationMappingIn(BaseModel):
    command: list[str]
    process_names: list[str] = []


class DefaultProjectOut(BaseModel):
    default_project: str


class DefaultProjectIn(BaseModel):
    default_project: str


@router.get("/applications", response_model=dict[str, ApplicationMappingOut])
def list_application_mappings(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Every alias -> {command, process_names} entry under the "applications" memory
    category -- exactly what `open_application`/`close_application` resolve against
    (`app/tools/applications.py`).
    """
    return MemoryService(db).list(APPLICATIONS)


@router.put("/applications/{alias}", response_model=ApplicationMappingOut)
def set_application_mapping(
    alias: str, payload: ApplicationMappingIn, db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Create or overwrite a single alias's mapping. Whole-value replacement (matches
    `RoutineRegistry.update_routine`'s steps-replacement convention) -- there's no
    partial-field PATCH here since an application mapping is only two fields, both
    required for the mapping to resolve to anything.
    """
    key = alias.strip().lower()
    if not key:
        raise HTTPException(status_code=422, detail="An application alias can't be blank.")
    if not payload.command:
        raise HTTPException(status_code=422, detail="A mapping needs a non-empty command.")

    MemoryService(db).set(APPLICATIONS, key, payload.model_dump())
    return payload.model_dump()


@router.delete("/applications/{alias}", status_code=204, response_model=None)
def delete_application_mapping(alias: str, db: Session = Depends(get_db)) -> None:
    key = alias.strip().lower()
    if not MemoryService(db).delete(APPLICATIONS, key):
        raise HTTPException(status_code=404, detail=f"No application mapping for '{alias}'.")


@router.get("/default-project", response_model=DefaultProjectOut)
def get_default_project(db: Session = Depends(get_db)) -> dict[str, Any]:
    """The "coding" routine's configured default project alias -- what "Start coding"
    resolves `coding.default_project` to (`app/tools/routines.py`), falling back to
    `DEFAULT_CODING_VALUE` if the "coding" entry hasn't been seeded/set yet.
    """
    coding = MemoryService(db).get(DEFAULT_CODING_CATEGORY, DEFAULT_CODING_KEY, DEFAULT_CODING_VALUE)
    return {"default_project": coding.get("default_project", DEFAULT_CODING_VALUE["default_project"])}


@router.put("/default-project", response_model=DefaultProjectOut)
def set_default_project(payload: DefaultProjectIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Update only `default_project` on the "coding" memory entry, leaving
    editor/browser untouched -- a merge into the existing entry, not a whole-value
    replacement, since this route's only field is one of three on that entry (see
    module docstring on scope).
    """
    default_project = payload.default_project.strip()
    if not default_project:
        raise HTTPException(status_code=422, detail="default_project can't be blank.")

    service = MemoryService(db)
    coding = dict(service.get(DEFAULT_CODING_CATEGORY, DEFAULT_CODING_KEY, DEFAULT_CODING_VALUE))
    coding["default_project"] = default_project
    service.set(DEFAULT_CODING_CATEGORY, DEFAULT_CODING_KEY, coding)
    return {"default_project": default_project}
