"""
Project discovery routes (Coding Routine template, follow-up to files 04/09/12).

Thin HTTP wrappers around `app/projects/discovery.py` -- same split as
`app/api/routes/routines.py`/`app/api/routes/memory.py` (§41 Rule 7): folder scanning
and root-list persistence stay in `discovery.py`; this module only validates the
request and shapes the response.

Every route here requires a valid bearer token (§34, file 12 prompt 1), matching every
other settings/routine route -- there's nothing in a folder *name* that needs a
stricter (e.g. local-only) gate than what already protects `/routines`/`/memory/*`, and
actually *opening* a discovered project still goes through `open_application`
(`platforms=["desktop"]`), which `ToolExecutor` already refuses for a non-desktop
caller regardless of what this file returns.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.database.database import get_db
from app.projects.discovery import list_project_roots, list_projects, set_project_roots

router = APIRouter(prefix="/projects", tags=["projects"], dependencies=[Depends(get_current_user)])


class ProjectOut(BaseModel):
    name: str
    path: str
    root: str


class ProjectRootsOut(BaseModel):
    roots: list[str]


class ProjectRootsIn(BaseModel):
    roots: list[str]


@router.get("", response_model=list[ProjectOut])
def get_projects(db: Session = Depends(get_db)) -> list[dict[str, str]]:
    """Every immediate subdirectory of every configured root -- the Coding Routine
    builder's project picker (`frontend/src/components/CodingRoutinePanel.tsx`)."""
    return list_projects(db)


@router.get("/roots", response_model=ProjectRootsOut)
def get_project_roots(db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"roots": list_project_roots(db)}


@router.put("/roots", response_model=ProjectRootsOut)
def put_project_roots(payload: ProjectRootsIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    roots = [r.strip() for r in payload.roots if r.strip()]
    if not roots:
        raise HTTPException(status_code=422, detail="At least one project folder is required.")
    return {"roots": set_project_roots(db, roots)}
