"""
MemoryService (§37 Phase 8 / file 09 prompt 1).

CRUD over the persisted `memories` table (schema from file 01: id, user_id, category,
key, value, created_at, updated_at) -- the structured, non-vector memory store that file
09's later prompts promote file 03's hardcoded `APP_MAP` (`app/tools/applications.py`)
and file 04's hardcoded `PORTFOLIO_FOLDER`/routine steps into. Takes an injected
`Session`, the same pattern `TaskService`/`RoutineRegistry` use: callers own the
session's lifecycle (open, commit/rollback, close); this class only reads and writes
through it.

No migration needed: file 01's `Memory.value` column is already `Text`, which is exactly
what a JSON-encoded value needs. `get`/`set`/`list` transparently JSON-encode going in
and decode coming out, so callers work with real Python values (dicts, lists, strings,
numbers, bools) -- e.g. the "coding" entry's `{"editor": ..., "browser": ...,
"default_project": ...}` shape from development-plan.md §15 -- instead of raw JSON
strings. Same encode/decode-at-the-edges pattern `RoutineRegistry` uses for
`RoutineStep.action_payload`.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.database.database import SessionLocal
from app.database.models import Memory

# Namespaced categories from development-plan.md §15 ("memory/" breakdown). Not
# enforced -- a caller can still write an ad-hoc category -- these just name the
# buckets this file's other prompts (hardcoded-alias promotion, selective retrieval)
# are expected to read/write.
USER_PREFERENCES = "user_preferences"
APPLICATIONS = "applications"
PROJECTS = "projects"
ROUTINES = "routines"
SETTINGS = "settings"
IMPORTANT_CONTEXT = "important_context"

# Seeded once at first startup via seed_default_memory() -- see its docstring. Matches
# development-plan.md §15's exact example, with placeholder values a user can edit once
# the settings UI (file 09 prompt 3) exists; until then, MemoryService.set(...) works
# just as well.
DEFAULT_CODING_CATEGORY = ROUTINES
DEFAULT_CODING_KEY = "coding"
DEFAULT_CODING_VALUE: dict[str, str] = {
    "editor": "VS Code",
    "browser": "Chrome",
    "default_project": "portfolio",
}


class MemoryService:
    """get / set / list / delete against a caller-supplied `Session`."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, category: str, key: str, default: Any = None) -> Any:
        row = self._find(category, key)
        if row is None:
            return default
        return json.loads(row.value)

    def set(self, category: str, key: str, value: Any) -> Memory:
        row = self._find(category, key)
        encoded = json.dumps(value)
        if row is None:
            row = Memory(category=category, key=key, value=encoded)
            self.db.add(row)
        else:
            row.value = encoded
        self.db.commit()
        self.db.refresh(row)
        return row

    def list(self, category: str) -> dict[str, Any]:
        """All key/value pairs in `category`, decoded, keyed by `key`."""
        rows = (
            self.db.query(Memory)
            .filter(Memory.category == category)
            .order_by(Memory.key)
            .all()
        )
        return {row.key: json.loads(row.value) for row in rows}

    def delete(self, category: str, key: str) -> bool:
        row = self._find(category, key)
        if row is None:
            return False
        self.db.delete(row)
        self.db.commit()
        return True

    def _find(self, category: str, key: str) -> Memory | None:
        return (
            self.db.query(Memory)
            .filter(Memory.category == category, Memory.key == key)
            .one_or_none()
        )


def seed_default_memory() -> None:
    """Create the "coding" memory entry (editor/browser/default_project) if it doesn't
    already exist.

    Idempotent -- safe to call on every startup, mirroring
    `app.tools.routines.seed_default_routines()`: it only seeds a key that's missing,
    never overwriting values a user has since edited via `MemoryService.set()` or the
    settings UI. Requires the `memories` table to already exist (`main.py`'s lifespan
    runs `Base.metadata.create_all` before `register_default_tools`, which calls this).
    """
    db = SessionLocal()
    try:
        service = MemoryService(db)
        if service.get(DEFAULT_CODING_CATEGORY, DEFAULT_CODING_KEY) is None:
            service.set(DEFAULT_CODING_CATEGORY, DEFAULT_CODING_KEY, DEFAULT_CODING_VALUE)
    finally:
        db.close()
