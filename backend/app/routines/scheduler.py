"""
Scheduled-routine trigger (§37 Phase 3 / file 04 prompt 2, optional).

Reuses the exact APScheduler `BackgroundScheduler` instance `ReminderScheduler` (file 04
prompt 1, `app/tasks/scheduler.py`) already runs -- via its `.scheduler` property --
rather than starting a second background thread. `RoutineScheduler` adds a small
`scheduled_routines` concept on top: an in-memory mapping of routine name -> cron
expression, each one registered as its own APScheduler cron job that calls
`RoutineEngine.run(name)` when it fires.

Deliberately in-memory, not a persisted `scheduled_routines` table: nothing in this
phase needs a schedule to survive a restart, and adding schema for cron config before
anything writes to it would be over-engineering ahead of need (§41 Rule 1 -- see the
same call made for `routines.trigger_type`/`routine_steps` themselves back in file 01).
If a later phase needs persisted schedules (configured via an API/UI, surviving
restarts), promote this mapping to a table then -- `(routine_name, cron_expression)` is
already the right column pair for that migration when it's actually needed.

Every fire goes through `RoutineEngine.run()` -> `ToolExecutor`, so a scheduled routine
gets the exact same validation/permission/logging path a manually-triggered one does
(§41 Rule 6) -- no LLM call anywhere in this path either. Nothing is scheduled by
default; this class only exists to be called by whatever wires up cron config later
(an API route, a settings file, ...) -- until something does, this is manual-trigger-
only, same as before this file existed.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.permissions import RequesterContext
from app.routines.engine import RoutineEngine
from app.tools.registry import ToolRegistry

logger = logging.getLogger("jarvis.routine_scheduler")


class RoutineScheduler:
    """Registers/removes cron-triggered routine runs against an existing APScheduler
    instance -- pass `ReminderScheduler.scheduler`, never a fresh `BackgroundScheduler`.
    """

    def __init__(self, scheduler: BackgroundScheduler, tool_registry: ToolRegistry) -> None:
        self._scheduler = scheduler
        self._engine = RoutineEngine(tool_registry)
        self._jobs: dict[str, str] = {}  # routine_name -> APScheduler job id

    def schedule(self, routine_name: str, cron_expression: str) -> str:
        """Register `routine_name` to run on `cron_expression` (standard 5-field cron,
        e.g. "0 9 * * mon-fri"). Replaces any existing schedule for the same routine
        name -- one schedule per routine, matching the "cron expression -> routine
        name" shape the prompt describes.
        """
        job_id = f"routine:{routine_name}"
        self._scheduler.add_job(
            self._run,
            CronTrigger.from_crontab(cron_expression),
            args=[routine_name],
            id=job_id,
            replace_existing=True,
        )
        self._jobs[routine_name] = job_id
        logger.info("Scheduled routine '%s' on cron '%s'.", routine_name, cron_expression)
        return job_id

    def unschedule(self, routine_name: str) -> bool:
        job_id = self._jobs.pop(routine_name, None)
        if job_id is None:
            return False
        self._scheduler.remove_job(job_id)
        logger.info("Unscheduled routine '%s'.", routine_name)
        return True

    def scheduled_routines(self) -> list[str]:
        """Names of routines currently registered with a cron schedule."""
        return sorted(self._jobs)

    def _run(self, routine_name: str) -> None:
        context = RequesterContext(platform="desktop", scope="scheduled_routine")
        result = self._engine.run(routine_name, context)
        if not result.success:
            logger.warning("Scheduled routine '%s' failed: %s", routine_name, result.error)
