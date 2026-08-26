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

## Deployment shape today, and the future cloud/desktop split (§32)

The current deploy (`docs/deployment.md`, file 12 prompt 3) is **one backend process** serving
both roles at once: it answers web requests (Tasks/Routines/Provider Status dashboards, deployed
to Render) *and* still contains every desktop-only tool (`open_application`,
`run_terminal_command`, clipboard, file ops, notifications, voice STT/TTS) gated at the HTTP
layer by `app.api.local_only.enforce_desktop_local_only` rather than by being a physically
separate process. That single-process shape is deliberate for now — it's what let file 12 ship
real auth and a real cloud deployment without also having to stand up a second service.

§32's target shape splits this one process into two:

```
JARVIS
                       |
          +------------+------------+
          |                         |
       Cloud Core              Desktop Agent
          |                         |
     Tasks/Memory              Windows
     Discord/Web               Files
     API                       Apps
          |
       AI Router
          |
     +----+----+
     |         |
  Gemini    Ollama
```

Nothing built here forecloses that split — it's the reason the boundary was drawn as a *tool*
capability check (`platforms=["desktop"]` on each tool, `app.core.assistant`/`ToolExecutor`) plus
a *network* check (`app.api.local_only`) instead of, say, an `if platform == "desktop"` branch
scattered through route handlers. Concretely, today's single process already contains the seam
the split would run along:

- **Cloud Core** would keep `AssistantCore`, `AIRouter`/`HealthManager`, the task/routine/memory
  services, the auth layer, and every route in `backend/app/api/routes/` *except* the
  desktop-tool handlers themselves — i.e. everything already safe to run on Render today.
- **Desktop Agent** would keep only the tools in `backend/app/tools/{system,applications,
  clipboard,files,path_safety,terminal}.py`, `app/tools/notifications.py`, and
  `app/voice/{stt,tts}.py` — the modules that already assume they're running on the user's own
  Windows machine.
- The two would talk over the network instead of an in-process function call: Cloud Core's
  `ToolExecutor` would dispatch a desktop-scoped tool call to the user's own running Desktop
  Agent (authenticated per-client, replacing `app.api.local_only`'s current loopback-address
  check — that module's docstring already flags this as the thing to replace, not widen, once a
  real remote caller exists) instead of calling the Python handler directly in-process.
- `AIRouter`/Gemini/Ollama stay reachable from Cloud Core only, same as today — Desktop Agent
  would never hold `GEMINI_API_KEY` itself, consistent with the secrets boundary in
  `docs/deployment.md`.

This is *not* built now — today's deployment is still the one-process shape above, and the
desktop-only tools simply sit dormant (never reachable) on the Render deployment. The split is
future work, tracked here so a future change doesn't have to rediscover this seam.
