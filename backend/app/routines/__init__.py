"""
Routines package (§37 Phase 3 / file 04 prompt 2).

`RoutineRegistry` (registry.py) persists named routines and their ordered steps via the
`routines`/`routine_steps` tables. `RoutineEngine` (engine.py) loads a routine from the
registry and runs its steps through `ToolExecutor`, in order, with no LLM call anywhere
in the path. `RoutineScheduler` (scheduler.py) is an optional cron trigger on top of
both, reusing the APScheduler instance `ReminderScheduler` already runs (file 04 prompt
1) rather than starting a second background scheduler.

This package is deliberately generic -- no fixed routine content lives here. The one
concrete routine this project ships with ("coding") is seeded by
`app/tools/routines.py`, which is where routine-specific, product-facing decisions
belong (§41 Rule 1).
"""

from __future__ import annotations
