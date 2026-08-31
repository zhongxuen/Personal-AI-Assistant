"""
RoutineRegistry tests (§37 Phase 3 / file 04 prompt 2).

Covers create/list/get/update/delete against a throwaway in-memory DB, plus the two
guardrails: a blank/duplicate name is rejected on create, and step order round-trips
exactly (registry.py explicitly orders by `step_order` rather than trusting relationship
ordering).
"""

from __future__ import annotations

import pytest

from app.routines.registry import RoutineRegistry


def _steps():
    return [
        ("open_application", {"app_name": "vscode"}),
        ("open_application", {"app_name": "chrome"}),
    ]


def test_create_and_get_routine_round_trips_steps_in_order(test_db):
    db = test_db()
    registry = RoutineRegistry(db)

    created = registry.create_routine("coding", _steps())
    assert created.name == "coding"
    assert created.enabled is True
    assert created.trigger_type == "manual"

    fetched = registry.get_routine("coding")
    assert fetched is not None
    assert [s.tool_name for s in fetched.steps] == ["open_application", "open_application"]
    assert [s.params for s in fetched.steps] == [
        {"app_name": "vscode"},
        {"app_name": "chrome"},
    ]
    db.close()


def test_get_routine_unknown_name_returns_none(test_db):
    db = test_db()
    registry = RoutineRegistry(db)

    assert registry.get_routine("nope") is None
    db.close()


def test_create_routine_blank_name_raises(test_db):
    db = test_db()
    registry = RoutineRegistry(db)

    with pytest.raises(ValueError):
        registry.create_routine("   ", _steps())
    db.close()


def test_create_routine_duplicate_name_raises(test_db):
    db = test_db()
    registry = RoutineRegistry(db)
    registry.create_routine("coding", _steps())

    with pytest.raises(ValueError):
        registry.create_routine("coding", _steps())
    db.close()


def test_list_routines_returns_every_created_routine_sorted_by_name(test_db):
    db = test_db()
    registry = RoutineRegistry(db)
    registry.create_routine("zeta", _steps())
    registry.create_routine("alpha", _steps())

    names = [r.name for r in registry.list_routines()]

    assert names == ["alpha", "zeta"]
    db.close()


def test_update_routine_replaces_steps_wholesale(test_db):
    db = test_db()
    registry = RoutineRegistry(db)
    registry.create_routine("coding", _steps())

    updated = registry.update_routine("coding", [("show_notification", {"title": "hi"})])

    assert updated is not None
    assert [s.tool_name for s in updated.steps] == ["show_notification"]
    assert registry.get_routine("coding").steps[0].params == {"title": "hi"}
    db.close()


def test_update_routine_unknown_name_returns_none(test_db):
    db = test_db()
    registry = RoutineRegistry(db)

    assert registry.update_routine("nope", _steps()) is None
    db.close()


def test_set_enabled_toggles_flag_without_touching_steps(test_db):
    db = test_db()
    registry = RoutineRegistry(db)
    registry.create_routine("coding", _steps())

    disabled = registry.set_enabled("coding", False)
    assert disabled is not None
    assert disabled.enabled is False
    assert [s.tool_name for s in disabled.steps] == ["open_application", "open_application"]
    assert registry.get_routine("coding").enabled is False

    enabled = registry.set_enabled("coding", True)
    assert enabled is not None
    assert enabled.enabled is True
    db.close()


def test_set_enabled_unknown_name_returns_none(test_db):
    db = test_db()
    registry = RoutineRegistry(db)

    assert registry.set_enabled("nope", False) is None
    db.close()


def test_rename_routine_updates_name_without_touching_steps_or_enabled(test_db):
    db = test_db()
    registry = RoutineRegistry(db)
    registry.create_routine("coding", _steps())
    registry.set_enabled("coding", False)

    renamed = registry.rename_routine("coding", "coding-session")
    assert renamed is not None
    assert renamed.name == "coding-session"
    assert renamed.enabled is False
    assert [s.tool_name for s in renamed.steps] == ["open_application", "open_application"]
    assert registry.get_routine("coding") is None
    assert registry.get_routine("coding-session") is not None
    db.close()


def test_rename_routine_same_name_is_a_no_op(test_db):
    db = test_db()
    registry = RoutineRegistry(db)
    registry.create_routine("coding", _steps())

    renamed = registry.rename_routine("coding", "coding")
    assert renamed is not None
    assert renamed.name == "coding"
    db.close()


def test_rename_routine_blank_name_raises(test_db):
    db = test_db()
    registry = RoutineRegistry(db)
    registry.create_routine("coding", _steps())

    with pytest.raises(ValueError):
        registry.rename_routine("coding", "  ")
    db.close()


def test_rename_routine_duplicate_name_raises(test_db):
    db = test_db()
    registry = RoutineRegistry(db)
    registry.create_routine("coding", _steps())
    registry.create_routine("music", _steps())

    with pytest.raises(ValueError):
        registry.rename_routine("coding", "music")
    db.close()


def test_rename_routine_unknown_name_returns_none(test_db):
    db = test_db()
    registry = RoutineRegistry(db)

    assert registry.rename_routine("nope", "still-nope") is None
    db.close()


def test_delete_routine_removes_it_and_its_steps(test_db):
    db = test_db()
    registry = RoutineRegistry(db)
    registry.create_routine("coding", _steps())

    assert registry.delete_routine("coding") is True
    assert registry.get_routine("coding") is None
    db.close()


def test_delete_routine_unknown_name_returns_false(test_db):
    db = test_db()
    registry = RoutineRegistry(db)

    assert registry.delete_routine("nope") is False
    db.close()
