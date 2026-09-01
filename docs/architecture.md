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

## How to add a new platform adapter

This is the pattern files 12 (web) and 13 (Discord) both followed to plug a new client into
`AssistantCore` without adding any platform-specific business logic (§41 Rule 7). The contract is
`PlatformAdapter` (`backend/app/platforms/base.py`):

```python
class PlatformAdapter(Protocol):
    def to_request(self, raw_input: Any) -> AssistantRequest: ...
    def to_platform_output(self, response: AssistantResponse) -> Any: ...
```

`AssistantCore.handle()` only ever sees an `AssistantRequest` in, `AssistantResponse` out — it has
no idea what platform called it. Everything platform-specific (parsing a native message shape,
stripping a bot-mention prefix, rendering a reply, enforcing the right trust boundary) lives in the
adapter or the route/bot wiring around it, never inside `AssistantCore`/`ToolExecutor`.

Two adapter shapes exist today, and a new platform is usually closer to one or the other:

- **Server-side adapter class** — `DiscordAdapter` (`backend/app/platforms/discord.py`). The
  native input (a `discord.py` `Message`) isn't JSON the client controls, so a real class
  implements `to_request`/`to_platform_output`, and a bot event handler (`build_discord_client`'s
  `on_message`) calls it and then `AssistantCore.handle()` directly — no HTTP hop.
- **Thin client + shared HTTP route** — the web platform. `backend/app/platforms/web.py` is
  intentionally a stub; the "adapter" is the frontend itself (`frontend/src/pages/Chat.tsx` /
  `frontend/src/services/api.ts`) building an already-`AssistantRequest`-shaped JSON body with
  `platform="web"` and POSTing it to the one shared `POST /api/assistant/message` route
  (`backend/app/api/routes/assistant.py`), the same endpoint desktop and Discord ultimately funnel
  through. Use this shape when the native input is already something a browser/HTTP client can
  produce directly — don't write a no-op server-side class just to satisfy the pattern.

  The web chat actually calls `POST /api/assistant/stream`, the Server-Sent Events
  sibling of that route: same request body, same auth boundary, same `AssistantCore`,
  same final answer — the reply is just delivered in pieces so it can be rendered while
  it's still being generated. It is deliberately a *second* route rather than a
  replacement: Discord, WhatsApp, the desktop agent and the mobile client all consume a
  single JSON body and have no use for partial output, so they keep using
  `/api/assistant/message` unchanged. Its terminal `done` event carries exactly the
  `AssistantResponse` the JSON route would have returned, which is what keeps one
  orchestrator rendered two ways from becoming two orchestrators (§41 Rule 7).

### Concrete steps

1. **Implement `to_request`/`to_platform_output` for the new native format.** Convert the
   platform's native message shape into an `AssistantRequest` (`backend/app/core/models.py`):
   `user_id` from whatever identifies the sender on that platform, `conversation_id` from
   whatever scopes a conversation there (channel/thread/session id), `message` with any
   platform chrome stripped (Discord strips a leading `@mention` or `"Jarvis,"` prefix via
   `_strip_bot_prefix`). `to_platform_output` renders `AssistantResponse.text` back into
   whatever the platform can send — respect any hard native limits (Discord's 2000-character
   message cap gets truncated in `DiscordAdapter.to_platform_output`).
2. **Set the platform name on `AssistantRequest`.** `platform` is a plain string
   (`"desktop" | "web" | "discord" | ...`, `backend/app/core/models.py`) — pick the new
   platform's literal here; it's what every downstream check (`ToolExecutor`, `local_only`,
   the tests) matches against. Don't invent a second field for this.
3. **Wire the right auth boundary — public vs. local-only.** `backend/app/api/routes/assistant.py`
   splits into exactly two boundaries and a new platform must land in the public one:
   - `platform="desktop"` is the only local-only case, gated by
     `enforce_desktop_local_only` (`backend/app/api/local_only.py`) — loopback-only, no
     bearer token. This boundary is not something a new remote platform should ever join.
   - every other platform (web, discord, and your new one) requires a valid bearer token via
     `get_optional_current_user`, and the route overwrites `request.user_id` with the
     authenticated user's own identity rather than trusting the client-supplied value. If the
     new platform talks over the shared HTTP route (the "thin client" shape above), this
     happens automatically. If it's a server-side adapter calling `AssistantCore.handle()`
     directly (the Discord shape), the adapter's own entry point is responsible for
     establishing who the caller is *before* building the request — Discord does this via the
     bot token/Discord's own auth, not this route's bearer-token check.
4. **Extend relevant tools' `platforms` lists.** Each tool declares
   `platforms: list[str]` (`backend/app/tools/base.py`); `ToolExecutor` rejects any call where
   `context.platform not in tool.platforms` with `"This action isn't available on
   {platform}."` (`backend/app/core/tool_executor.py`) — that's the actual capability-rejection
   message; don't invent friendlier flavor text elsewhere. Go through the existing tools
   (`backend/app/tools/*.py`) and add the new platform's name to the ones that make sense for a
   chat-based/remote client (tasks, timers, routine status, `get_time`, etc. already list
   `["desktop", "web", "discord"]` or similar). Do **not** add it to tools that stay
   `platforms = ["desktop"]` — `applications.py`, `clipboard.py`, `files.py`, `terminal.py`,
   `notifications.py`, and the process-control tools in `system.py` — those are the desktop-only
   set from file 11 and must stay unreachable from any remote platform.
5. **Add adapter + capability tests.** Follow the two-file split from
   `tests/platforms/test_discord_adapter.py` / `test_discord_capability.py`:
   - a pure adapter unit test (no `AssistantCore`, no DB) that mocks the native input and
     asserts `to_request()` maps fields correctly (platform, user_id, conversation_id, cleaned
     message) and `to_platform_output()` renders correctly, including any native limits;
   - an end-to-end capability test that runs a real request through the adapter and
     `AssistantCore`, asserting an allowed action (e.g. "what are my tasks?") resolves normally
     and a desktop-only action (e.g. "open VS Code") gets the exact
     `"This action isn't available on {platform}."` rejection, never a silent no-op or an
     actual execution attempt.
   Also extend `tests/core/test_platform_capability.py` with a case for the new platform if it
   isn't already covered there generically.

### Checklist

- [ ] `to_request`/`to_platform_output` implemented (or the thin-client shape used, if the
      native input is already producible as JSON)
- [ ] `AssistantRequest.platform` set to the new platform's literal string
- [ ] New platform wired to the **public** auth boundary (`get_optional_current_user`) — never
      folded into `enforce_desktop_local_only`
- [ ] `platforms` list extended on tools that make sense for the new platform; **not** added to
      any `platforms = ["desktop"]` tool
- [ ] Adapter unit tests (field mapping, native-format rendering) added
- [ ] End-to-end capability test added: one allowed action resolves normally, one desktop-only
      action gets the `"This action isn't available on {platform}."` rejection
- [ ] No new business logic added inside the adapter or route — everything still routes through
      the same `AssistantCore.handle()` every other platform calls

## Deliberate abstraction fixes

Files 13/14/16 all carry the same rule: adding a platform must not require editing
`AssistantCore`, `CommandRouter`, `ToolExecutor`, `AIRouter`, `TaskService`, or `RoutineEngine` —
and if some core file *does* have to change, that's a signal the abstraction wasn't generic
enough, so fix the abstraction and record it here as a deliberate fix rather than leaving a
platform-specific patch in place. This section is where those get recorded. Files 13/14/16 each
finished with nothing to record (`git diff --stat` against the six was empty). File 17 has one
entry.

### `ReminderScheduler` is multi-channel by design (file 17)

**What changed.** `ReminderScheduler._poll` (`backend/app/tasks/scheduler.py`) used to deliver a
due reminder over exactly one channel: a `show_notification` tool call through `ToolExecutor`
(Windows toast via `winotify`, `platforms = ["desktop"]`). It now delivers over two — that same
tool call, unchanged and still first, followed by a Web Push message to every browser the
reminding user has subscribed from (`WebPushSender.send_to_user`, `backend/app/push/sender.py`).

**Why this is an abstraction fix, not a mobile patch.** The old single-channel shape encoded an
assumption that was true only while the assistant had exactly one client: *the user is sitting at
the machine the scheduler is running on*. That was never a property of reminders — it was a
property of there being nowhere else to send them. A reminder is a message to a **user**, and a
user is reachable on however many surfaces they've registered. So the fix is to make the delivery
step fan out over the user's channels instead of hardcoding the one that happened to exist first.
Nothing in the new code mentions mobile, PWAs, or phones; `push_subscriptions` rows come from
desktop Chrome exactly as readily as from a phone. Had this been written as "if the request came
from mobile, also push," that would have been the platform-specific patch this rule exists to
prevent.

**Why it lives in `ReminderScheduler` and not behind a new abstraction.** The obvious alternative
was a `NotificationChannel` protocol with `DesktopToastChannel`/`WebPushChannel` implementations
and a registry — the shape `PlatformAdapter` uses for inbound messages. Rejected for now (§41
Rule 1): there are two channels and one caller, so the protocol would have exactly two
implementations and no second consumer, and the two aren't actually symmetric — the toast goes
through `ToolExecutor` (validated, permission-checked, logged as a tool call, §41 Rule 6) while
push is a direct outbound HTTP send with no tool semantics at all. Forcing both behind one
interface would mean either dragging push through a fake tool or dropping the toast's audit
trail. The seam that matters is already drawn correctly: `ReminderScheduler` owns *when* to
notify and knows nothing about *how* either channel works, and each channel's mechanics live in
its own module (`app/tools/notifications.py`, `app/push/sender.py`). If a third channel lands and
the `for reminder in ...` body starts growing per-channel branches, that's the point to promote
the fan-out to a real `NotificationChannel` list — not before.

**Invariants this change preserves, and how.**

- *The desktop toast can never be weakened by push.* The push send runs strictly after the
  `show_notification` call has already returned. `WebPushSender.send_to_user` cannot raise —
  every failure mode (VAPID keys unset, subscription lookup failure, a push service returning
  404/410/429/5xx, a socket timeout, a malformed row) is caught, logged, and skipped
  per-subscription — so a dead device cannot stop the other devices, the toast, the remaining
  reminders in the batch, or the background poll thread.
- *A user with zero subscriptions gets exactly the pre-file-17 behaviour.* `send_to_user` returns
  0 immediately on an empty list and on `user_id is None` (every user-owned table here is
  nullable `user_id`; pre-auth rows have nobody to deliver to). One toast, nothing else, no crash.
- *VAPID keys unset → the feature no-ops*, same "absence is a valid, non-crashing state"
  convention as `discord_bot_token`/`gemini_api_key` (`backend/app/config/settings.py`).
- *The VAPID private key stays in one module.* `app/push/sender.py` is the only file that reads
  `settings.vapid_private_key` or imports `pywebpush`; `settings.vapid_public_key` remains the
  one deliberately frontend-exposed secret-shaped value (`docs/security.md`).
- *The six core files are untouched.* `git diff --stat` against `backend/app/core/assistant.py`,
  `command_router.py`, `tool_executor.py`, `backend/app/llm/ai_router.py`,
  `backend/app/tasks/service.py`, and `backend/app/routines/engine.py` is empty. `ReminderScheduler`
  is deliberately not one of the six — it's the delivery edge, which is exactly why the fan-out
  belongs there.

**Where the next channel plugs in.** The same loop body. File 18's template-reminder phase
(`md-files/18-whatsapp-adapter.md`) sends a due reminder as a WhatsApp template message; that is a
third call alongside the toast and the push, reading its own per-user destination the way
`WebPushSender` reads `push_subscriptions` — and the point at which the `NotificationChannel`
promotion above is worth doing.
