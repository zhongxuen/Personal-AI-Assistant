"""
Project discovery for the Coding Routine template.

Backs the "which project am I working on today?" picker
(`frontend/src/components/CodingRoutinePanel.tsx`) with a real, low-friction answer:
scan a small set of user-configured root folders (e.g. the "Coding" folder
development-plan.md's examples live under, or this repo's own temporary `Dev` location
-- see `CLAUDE.md`) and list their immediate subdirectories as project options, instead
of requiring a manually-registered "applications" alias per project the way the
original "coding" routine's `default_project` did (`app/tools/routines.py`).

Root folders are stored in the "projects" memory category (`app/memory/service.py`,
`PROJECTS`) under a single `"roots"` key -- a plain `list[str]` of absolute folder
paths, editable via `GET/PUT /api/projects/roots` (Settings page) the same
get/set-a-key pattern `app/api/routes/memory.py` already uses for `default-project`.

Deliberately never raises on a missing/unreadable root: a root that doesn't exist yet
(or no longer does, e.g. after this repo moves back out of the temporary `Dev` location
per `CLAUDE.md`) just contributes zero projects rather than failing the whole scan --
the failure mode for a stale root should be "doesn't show up", not "breaks the picker
for every other root too".
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.database.database import SessionLocal
from app.memory.service import PROJECTS, MemoryService

ROOTS_KEY = "roots"

# Seed value only -- read once by seed_default_project_roots(). Both candidates are
# included unconditionally (list_projects() silently skips whichever doesn't exist at
# scan time, see module docstring): the user's permanent projects folder, and this
# repo's own current, documented-as-temporary location, so "Personal AI Assistant"
# itself shows up as a pickable project even while it lives outside "Coding".
DEFAULT_PROJECT_ROOTS: list[str] = [
    str(Path.home() / "Coding"),
    str(Path.home() / "Dev"),
]

# Subdirectory names never worth offering as a "project" even when they sit directly
# under a root -- tooling/VCS internals, not something a coding session opens.
_IGNORED_DIR_NAMES = {"node_modules", ".git", "__pycache__", ".venv", "venv", ".idea", ".vscode", ".pytest_cache"}


def _get_service(db: Any) -> MemoryService:
    return db if isinstance(db, MemoryService) else MemoryService(db)


def list_project_roots(db: Any) -> list[str]:
    """The configured scan roots, falling back to `DEFAULT_PROJECT_ROOTS` if the
    "projects" memory entry hasn't been seeded/set yet."""
    return list(_get_service(db).get(PROJECTS, ROOTS_KEY, DEFAULT_PROJECT_ROOTS))


def set_project_roots(db: Any, roots: list[str]) -> list[str]:
    """Whole-list replacement (matches `RoutineRegistry.update_routine`'s
    steps-replacement convention) -- stripped, de-duplicated, order-preserved."""
    cleaned: list[str] = []
    for root in roots:
        value = root.strip()
        if value and value not in cleaned:
            cleaned.append(value)
    _get_service(db).set(PROJECTS, ROOTS_KEY, cleaned)
    return cleaned


def seed_default_project_roots() -> None:
    """Create the "projects" -> "roots" memory entry if it doesn't already exist.

    Idempotent -- safe to call on every startup, mirroring
    `seed_default_applications()`/`seed_default_routines()`: only seeds when the key is
    missing, never overwriting roots a user has since edited via `PUT /api/projects/roots`.
    """
    db = SessionLocal()
    try:
        service = MemoryService(db)
        if service.get(PROJECTS, ROOTS_KEY) is None:
            service.set(PROJECTS, ROOTS_KEY, DEFAULT_PROJECT_ROOTS)
    finally:
        db.close()


def _subdirectories(root: str) -> list[Path]:
    root_path = Path(root)
    if not root_path.is_dir():
        return []
    entries: list[Path] = []
    for name in os.listdir(root_path):
        if name.startswith(".") or name in _IGNORED_DIR_NAMES:
            continue
        path = root_path / name
        if path.is_dir():
            entries.append(path)
    return entries


def list_projects(db: Any) -> list[dict[str, str]]:
    """Every immediate subdirectory of every configured root, as `{"name", "path",
    "root"}` dicts sorted by name -- the Coding Routine builder's project picker.

    A project folder that lives under two different configured roots (unlikely, but
    roots aren't validated against each other) appears once per root rather than being
    silently merged, since they're genuinely different paths a user might pick between.
    """
    projects: list[dict[str, str]] = []
    for root in list_project_roots(db):
        for path in _subdirectories(root):
            projects.append({"name": path.name, "path": str(path), "root": root})
    projects.sort(key=lambda p: (p["name"].lower(), p["path"]))
    return projects
