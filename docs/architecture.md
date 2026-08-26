# Architecture

TBD — filled in as Phases 1–2 (AssistantCore, CommandRouter, ToolExecutor) and Phase 5–6
(AI Router) land. See `md-files/00-overview-and-architecture.md` for the target diagram in the
meantime.

## Persistence choices

**Routines (`md-files/04-task-and-routine-system.md` §3):** routines are stored in the database
(`routines` / `routine_steps` tables, `backend/app/database/models.py`, created in file 01), not a
versioned config file. `RoutineRegistry` (`backend/app/routines/registry.py`) is the only code that
reads/writes those tables — a routine's ordered steps (`tool_name`, JSON-encoded `params`) live in
`routine_steps.action_type`/`action_payload`. Chosen over a config file so the Routine Dashboard
(file 04 prompt 4) can create/edit/delete routines at runtime through a normal CRUD API
(`/api/routines`) without a redeploy or a file write from the running process.

**Scheduled routines (`md-files/04-task-and-routine-system.md` §6):** deliberately *not*
persisted. `RoutineScheduler` (`backend/app/routines/scheduler.py`) holds its
`routine_name -> cron_expression` mapping in memory only and registers each one as a job on the
same APScheduler instance `ReminderScheduler` (`backend/app/tasks/scheduler.py`) already runs.
Nothing is scheduled by default and nothing calls `RoutineScheduler.schedule()` yet — no UI or API
route exists to configure a cron schedule, so schedules don't currently survive a process restart.
If/when that's needed, promote the in-memory mapping to a `scheduled_routines` table
(`routine_name`, `cron_expression`) and load it back into `RoutineScheduler` on startup; the class
is already shaped for that migration.
