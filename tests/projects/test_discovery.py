"""
`app/projects/discovery.py` tests -- the Coding Routine template's project picker.

Every test builds its own temp folder tree (`tmp_path`) and points the "projects"
memory entry at it via `set_project_roots`, rather than touching whatever the real
`~/Coding`/`~/Dev` folders happen to contain on the machine running the suite.
"""

from __future__ import annotations

from app.memory.service import PROJECTS, MemoryService
from app.projects.discovery import (
    DEFAULT_PROJECT_ROOTS,
    ROOTS_KEY,
    list_project_roots,
    list_projects,
    seed_default_project_roots,
    set_project_roots,
)


def _db(test_db):
    return test_db()


def test_seed_default_project_roots_persists_defaults(test_db):
    seed_default_project_roots()

    db = _db(test_db)
    try:
        assert MemoryService(db).get(PROJECTS, ROOTS_KEY) == DEFAULT_PROJECT_ROOTS
    finally:
        db.close()


def test_seed_default_project_roots_is_idempotent(test_db):
    db = _db(test_db)
    try:
        MemoryService(db).set(PROJECTS, ROOTS_KEY, ["/custom/root"])
    finally:
        db.close()

    seed_default_project_roots()  # must not overwrite the user-edited value

    db = _db(test_db)
    try:
        assert MemoryService(db).get(PROJECTS, ROOTS_KEY) == ["/custom/root"]
    finally:
        db.close()


def test_list_project_roots_falls_back_to_defaults_when_unseeded(test_db):
    db = _db(test_db)
    try:
        assert list_project_roots(db) == DEFAULT_PROJECT_ROOTS
    finally:
        db.close()


def test_set_project_roots_strips_dedupes_and_persists(test_db):
    db = _db(test_db)
    try:
        result = set_project_roots(db, [" /a ", "/b", "/a", "  "])
        assert result == ["/a", "/b"]
        assert list_project_roots(db) == ["/a", "/b"]
    finally:
        db.close()


def test_list_projects_scans_immediate_subdirectories(tmp_path, test_db):
    root = tmp_path / "Coding"
    root.mkdir()
    (root / "portfolio").mkdir()
    (root / "warren-mak").mkdir()
    (root / "notes.txt").write_text("not a folder")
    (root / "node_modules").mkdir()  # ignored
    (root / ".git").mkdir()  # ignored (hidden)

    db = _db(test_db)
    try:
        set_project_roots(db, [str(root)])
        projects = list_projects(db)
    finally:
        db.close()

    assert [p["name"] for p in projects] == ["portfolio", "warren-mak"]
    assert all(p["root"] == str(root) for p in projects)
    assert {p["path"] for p in projects} == {str(root / "portfolio"), str(root / "warren-mak")}


def test_list_projects_scans_multiple_roots(tmp_path, test_db):
    coding = tmp_path / "Coding"
    dev = tmp_path / "Dev"
    coding.mkdir()
    dev.mkdir()
    (coding / "portfolio").mkdir()
    (dev / "Personal-AI-Assistant").mkdir()

    db = _db(test_db)
    try:
        set_project_roots(db, [str(coding), str(dev)])
        projects = list_projects(db)
    finally:
        db.close()

    assert {p["name"] for p in projects} == {"portfolio", "Personal-AI-Assistant"}


def test_list_projects_silently_skips_a_missing_root(tmp_path, test_db):
    """A root that doesn't exist (e.g. the temporary Dev root, once this repo moves
    back to Coding and Dev is gone) contributes zero projects instead of raising."""
    existing = tmp_path / "Coding"
    existing.mkdir()
    (existing / "portfolio").mkdir()
    missing = tmp_path / "does-not-exist"

    db = _db(test_db)
    try:
        set_project_roots(db, [str(existing), str(missing)])
        projects = list_projects(db)
    finally:
        db.close()

    assert [p["name"] for p in projects] == ["portfolio"]
