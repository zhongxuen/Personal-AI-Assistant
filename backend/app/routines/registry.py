"""
Routine registry (§37 Phase 3 / file 04 prompt 2).

CRUD for named routines and their ordered steps, persisted via the `routines`/
`routine_steps` tables (schema from file 01) -- the persisted replacement for file 03's
hardcoded `ROUTINES` dict (`app/tools/routines.py`). Takes an injected `Session`, the
same pattern `TaskService` (`app/tasks/service.py`) uses: callers own the session's
lifecycle (open, commit/rollback, close); this class only reads and writes through it.

`RoutineStep.action_type` holds the tool name to call; `action_payload` is a `Text`
column, so a step's params are JSON-encoded going in and decoded back out coming out.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.database.models import Routine, RoutineStep


@dataclass
class RoutineStepData:
    """One step of a routine, decoded back into (tool_name, params)."""

    tool_name: str
    params: dict[str, Any]


@dataclass
class RoutineData:
    """A named routine and its ordered steps, as read back from the registry."""

    id: int
    name: str
    trigger_type: str
    enabled: bool
    steps: list[RoutineStepData]


class RoutineRegistry:
    """create_routine / list_routines / get_routine / update_routine / delete_routine
    against a caller-supplied `Session`.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def create_routine(self, name: str, steps: list[tuple[str, dict[str, Any]]]) -> RoutineData:
        """Create a new routine with the given ordered `(tool_name, params)` steps.

        Raises `ValueError` for a blank name or a name that's already taken -- routine
        names are how `run_routine`/`RoutineScheduler` look routines up, so silently
        overwriting one would let two different callers mean different things by the
        same name.
        """
        name = name.strip()
        if not name:
            raise ValueError("A routine needs a non-empty name.")
        if self.db.query(Routine).filter(Routine.name == name).one_or_none() is not None:
            raise ValueError(f"A routine named '{name}' already exists.")

        routine = Routine(name=name, trigger_type="manual", enabled=True)
        self.db.add(routine)
        self.db.flush()  # assign routine.id before inserting steps that FK to it
        self._write_steps(routine.id, steps)
        self.db.commit()
        self.db.refresh(routine)
        return self._to_data(routine)

    def list_routines(self) -> list[RoutineData]:
        routines = self.db.query(Routine).order_by(Routine.name).all()
        return [self._to_data(routine) for routine in routines]

    def get_routine(self, name: str) -> RoutineData | None:
        routine = self.db.query(Routine).filter(Routine.name == name).one_or_none()
        return self._to_data(routine) if routine is not None else None

    def update_routine(
        self, name: str, steps: list[tuple[str, dict[str, Any]]]
    ) -> RoutineData | None:
        """Replace a routine's steps wholesale. Returns `None` if no routine has that
        name (callers can't update a routine that was never created / already deleted).
        """
        routine = self.db.query(Routine).filter(Routine.name == name).one_or_none()
        if routine is None:
            return None
        self.db.query(RoutineStep).filter(RoutineStep.routine_id == routine.id).delete()
        self._write_steps(routine.id, steps)
        self.db.commit()
        self.db.refresh(routine)
        return self._to_data(routine)

    def set_enabled(self, name: str, enabled: bool) -> RoutineData | None:
        """Flip a routine's `enabled` flag without touching its steps -- the "Stop"/
        "Start" toggle on the Routine Dashboard. A disabled routine is left fully
        intact (steps, schedule registration) but `RoutineEngine.run()` refuses to run
        it (see its own `if not routine.enabled` check), so this is how a routine gets
        taken offline without deleting it. Returns `None` if no routine has that name.
        """
        routine = self.db.query(Routine).filter(Routine.name == name).one_or_none()
        if routine is None:
            return None
        routine.enabled = enabled
        self.db.commit()
        self.db.refresh(routine)
        return self._to_data(routine)

    def delete_routine(self, name: str) -> bool:
        routine = self.db.query(Routine).filter(Routine.name == name).one_or_none()
        if routine is None:
            return False
        self.db.query(RoutineStep).filter(RoutineStep.routine_id == routine.id).delete()
        self.db.delete(routine)
        self.db.commit()
        return True

    def _write_steps(self, routine_id: int, steps: list[tuple[str, dict[str, Any]]]) -> None:
        for order, (tool_name, params) in enumerate(steps):
            self.db.add(
                RoutineStep(
                    routine_id=routine_id,
                    step_order=order,
                    action_type=tool_name,
                    action_payload=json.dumps(params or {}),
                )
            )

    def _to_data(self, routine: Routine) -> RoutineData:
        # Queried directly (not via `routine.steps`) and ordered explicitly by
        # `step_order` -- the ORM relationship declares no ordering of its own, and
        # step order is exactly what RoutineEngine depends on to run steps correctly.
        step_rows = (
            self.db.query(RoutineStep)
            .filter(RoutineStep.routine_id == routine.id)
            .order_by(RoutineStep.step_order)
            .all()
        )
        steps = [
            RoutineStepData(
                tool_name=row.action_type,
                params=json.loads(row.action_payload) if row.action_payload else {},
            )
            for row in step_rows
        ]
        return RoutineData(
            id=routine.id,
            name=routine.name,
            trigger_type=routine.trigger_type,
            enabled=routine.enabled,
            steps=steps,
        )
