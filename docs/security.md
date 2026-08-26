# Security

## Permission levels (§19)

Every `Tool` declares a `permission: PermissionLevel` (`app/core/permissions.py`), and
`PermissionChecker.check()` is the single gate `ToolExecutor` must pass a call through
before it ever calls `tool.handler(...)` (§41 Rule 6):

| Level        | Default behavior                                              |
| ------------ | --------------------------------------------------------------- |
| `SAFE`       | Always allowed.                                                 |
| `CONFIRM`    | Allowed only if the caller sets `requester_context.confirmed = True`. |
| `RESTRICTED` | Denied unless the caller sets `requester_context.override = True`. |
| `BLOCKED`    | Denied unless the caller sets `requester_context.override = True`. |

`confirmed`/`override` must be set by a human-facing step (a UI confirmation prompt, an
explicit config flag) -- never derived from LLM output. If the model could set its own
`confirmed`/`override`, it could talk its way past the exact gate this system exists to
enforce.

## Why `run_terminal_command` is RESTRICTED and allow-listed, not open

`app/tools/terminal.py`'s `run_terminal_command` is the closest thing this codebase has
to a shell, and it is deliberately the most locked-down tool here, for two independent
reasons stacked on top of each other:

1. **`RESTRICTED`, not `CONFIRM`.** A `CONFIRM` tool (like `create_file` or
   `close_application`) just needs a user to click "yes" once per call -- appropriate
   when the worst case is a known, bounded, visible action (one file written, one
   process killed). A terminal command's blast radius isn't bounded the same way: an
   arbitrary command string can read, exfiltrate, modify, or delete anything the OS
   user account can touch, chain multiple operations together, or run indefinitely.
   That category of risk gets the strictly stronger `override` gate rather than
   `confirmed`, so it can't be casually approved through the same one-click flow as a
   single file write.

2. **Allow-listed by alias/template, never a raw string.** Even with the `RESTRICTED`
   gate cleared, the tool still never accepts a free-form command string from the LLM
   or the user and hands it to a shell. Instead:
   - `alias` must be an exact key in the `ALLOWED_COMMANDS` dict defined in code
     (`app/tools/terminal.py`) -- an alias that isn't a known key is rejected outright,
     never coerced or partially matched.
   - Each allow-listed entry's `template` is a literal argv list run with
     `subprocess.run(..., shell=False)`, not a shell string -- there is no shell to
     inject into (`;`, `|`, `&&`, backticks, etc. are inert) even for a command that
     accepts a substituted argument.
   - A template's `{name}` placeholders are validated against that command's `params`
     regex *before* substitution, and become their own standalone argv element after
     substitution -- a value like `"8.8.8.8; rm -rf /"` simply fails the hostname regex
     and is rejected, and even a value that did pass would land in argv as one literal
     token, never re-parsed by a shell.
   - Unrecognized argument names and missing required ones are both rejected before
     anything is built or run.
   - The call runs with a hard timeout and truncated captured output, so it can't hang
     the executor or flood a response with runaway output.

This mirrors the same "never trust LLM/user-supplied input blindly, sanitize instead of
pass through" principle `app/tools/path_safety.py` applies to file paths (§33, §41 Rule
6) -- applied here to *commands* instead of paths. An open `run_terminal_command` that
executed whatever string it was given would turn one compromised or over-eager LLM
response, or one crafted user message, into arbitrary code execution on the host
machine. The allow-list keeps the tool useful (a fixed, auditable set of read-only
diagnostic commands: listing a directory, checking disk usage, reading network config,
pinging a host) while making that failure mode structurally impossible rather than
merely discouraged.

## Local-only boundary for `platform="desktop"` requests (§23, file 11 prompt 3)

Desktop-agent tools -- `list_processes`/`get_process_info` (`app/tools/system.py`),
`search_files`/`open_file`/`create_file` (`app/tools/files.py`),
`clipboard_read`/`clipboard_write` (`app/tools/clipboard.py`), and
`run_terminal_command` (`app/tools/terminal.py`) -- all declare `platforms =
["desktop"]` (§22). `ToolExecutor` already refuses to run any of them for a
`RequesterContext` whose `platform` isn't `"desktop"`, but that check only guards
which *tool* a request may reach once it's already been accepted -- it does nothing to
stop a request that reaches this backend over the network and simply *claims*
`platform="desktop"`, since `platform` is just a field on the request body (or, for
`/api/voice/message`, a value the route always sets internally).

`app/api/local_only.py`'s `enforce_desktop_local_only()` closes that gap at the HTTP
layer, ahead of `AssistantCore`/`ToolExecutor`: any request whose effective platform is
`"desktop"` must also arrive from a loopback client (`127.0.0.1` / `::1` /
`localhost`), or it's rejected with `403` before any tool lookup, permission check, or
handler call happens. It's wired into every route that can execute a
`platform="desktop"` request:

- `POST /api/assistant/message` (`app/api/routes/assistant.py`) -- `platform` comes
  from the request body, so the check runs conditionally on whatever value was sent.
- `POST /api/voice/message` (`app/api/routes/voice.py`) -- always builds a
  `platform="desktop"` request via `DesktopAdapter`, so the check runs unconditionally.

`POST /api/routines/{name}/run` (`app/api/routes/routines.py`) was a known gap this
check didn't originally cover: `RoutineEngine.run()` defaults to a `platform="desktop"`
`RequesterContext` when the HTTP route doesn't supply one. Requiring a bearer token
(see "Authentication" below) closed the "no auth at all" half of that gap, but on its
own it wouldn't have closed the rest: an *authenticated* remote caller could still have
triggered a routine's desktop-only steps for real, since nothing tied the context's
`platform` to where the request actually came from. File 12 prompt 2 closes that
remaining half directly -- `run_routine` now builds its `RequesterContext` with
`platform="desktop"` only when `app.api.local_only.is_local_client(request)` is true
(the same loopback check this section describes, reused as a boolean rather than a
raise), and `platform="web"` otherwise. A desktop-only routine step run from a remote
browser is now rejected by `ToolExecutor`'s platform check (§22) the same way a direct
`open_application` call from the web would be, instead of either executing for real or
depending solely on authentication to make it safe.

**This is a stopgap, not real authentication for the desktop agent itself.** "Does the
client socket look like loopback" only works because nothing today puts another host, a
mobile client, or a proxy between a legitimate desktop client and this backend -- true
only because the desktop client and this backend still run on the same machine (file
11). This remains true even now that file 12's auth layer exists (see below): a
`platform="desktop"` request is deliberately *not* required to carry a bearer token
(§34's brief keeps that a separate trust boundary), so this loopback check is still the
only thing standing between an arbitrary local process and the desktop tools. Keep
`api_host` bound to an interface you control during local development -- the loopback
check is a second, independent layer on top of that, not a substitute for it.

## Authentication (§34, file 12 prompt 1)

Every route that isn't part of the desktop-only local boundary above is a
**non-desktop-local route**, and requires a valid bearer token:

- `POST /api/tasks/*`, `POST /api/routines/*` (including `GET /api/tools`),
  `GET/PUT/DELETE /api/memory/*`, `GET /api/llm/usage` -- the Task/Routine/Memory
  dashboards and provider-status panel (file 12's web client surface). Each router
  declares `dependencies=[Depends(get_current_user)]` at the `APIRouter` level
  (`app/api/routes/tasks.py`, `routines.py`, `memory.py`, `llm_usage.py`), so every
  route on it requires a token unconditionally -- there's no route on these routers
  that's meant to be reachable without one.
- `POST /api/assistant/message` when `request.platform != "desktop"` -- this one route
  is genuinely mixed traffic (desktop *and* web/discord all funnel through it), so it
  can't use the same router-level `dependencies=[...]`: whether a token is required
  depends on the request *body*, which FastAPI dependencies can't see before they run.
  `app/api/routes/assistant.py` resolves the token via `get_optional_current_user`
  (still a normal, overridable `Depends`, just non-raising when no token is present)
  and only turns a missing token into `401` for non-`"desktop"` platforms. For those
  requests, the authenticated user's own `username` overwrites whatever `user_id` the
  client put in the request body -- a caller must not be able to claim to be a
  different user by editing the JSON.
- `POST /api/voice/message` and any `platform="desktop"` call to
  `/api/assistant/message` deliberately stay **outside** this layer -- they're gated
  solely by `enforce_desktop_local_only` above, per this task's own instruction to keep
  that a separate trust boundary rather than fold it into public auth.

### How it works

- **`users` table** (`app/database/models.py`) gained one nullable column,
  `password_hash` -- everything else about the table (an autoincrement `id`, a unique
  `username`) already supported more than one row; no schema redesign was needed to go
  from "one implicit user" to "an actual users table with credentials."
- **Password hashing** (`app/auth/security.py`): PBKDF2-HMAC-SHA256 via the stdlib
  `hashlib`/`hmac`/`os` -- no bcrypt/passlib dependency added. The stored format is
  self-describing (`"pbkdf2_sha256$<iterations>$<salt-b64>$<hash-b64>"`), so a future
  iteration-count bump doesn't break verifying hashes stored under the old one.
  `verify_password` fails closed (returns False, never raises) on any malformed hash.
- **Tokens** (`app/auth/security.py`): signed JWTs (HS256, via PyJWT -- also stdlib
  `hmac`/`hashlib` under the hood, no extra crypto backend) carrying `sub` (the user's
  numeric id) and `username`, expiring after `AUTH_TOKEN_EXPIRE_MINUTES` (default 24h
  -- a personal single-user session, not a short-lived web-SSO token). `sub` is what
  `get_current_user`/`get_optional_current_user` (`app/api/dependencies`) look the user
  back up by on every request, never `username` -- a renamed/deleted user is reflected
  immediately rather than only once the old token expires.
- **Login** (`app/api/routes/auth.py`): `POST /api/auth/login`, form-encoded
  `username`/`password` (`OAuth2PasswordRequestForm`, the standard FastAPI convention
  -- also what makes the OpenAPI docs' "Authorize" button work). Wrong password and
  unknown username return the identical `401`/message, so a caller can't enumerate
  valid usernames by the response shape.
- **No public registration endpoint.** `AuthService.create_user` exists for a future
  multi-user admin surface (or a one-off script), but nothing over HTTP calls it today.
  Instead, `main.py`'s startup calls `AuthService.seed_default_user()` with
  `AUTH_SEED_USERNAME`/`AUTH_SEED_PASSWORD` from settings -- idempotent (no-ops if
  either is unset, or if that username already exists), matching this app's "one
  personal user today" brief without opening self-service signup on an assistant with
  privileged tool access.
- **`AUTH_SECRET_KEY`** defaults to an obviously-insecure placeholder
  (`"dev-only-insecure-secret-change-me"`) so local development works out of the box.
  `main.py`'s startup logs a warning (doesn't fail) if that default is still active
  while `APP_ENV != "development"` -- it **must** be overridden via environment
  variable before this backend is reachable from anywhere but localhost (file 12's
  actual web deployment).

### What this doesn't do

Multi-user *authorization* (one user's tasks/routines/memory being scoped away from
another's) isn't implemented -- every row in `tasks`/`routines`/`memories`/etc. is
still global, not filtered by the authenticated user's id. §34 asked for a structure
that *supports* multiple users without an architecture rewrite (the `users` table and
`get_current_user` dependency both already generalize cleanly), not for that scoping to
exist today, since this remains a single-personal-user deployment. Revisit
per-user data scoping if/when a second real user is ever added.

## Other tool-level safeguards

- **File paths** (`app/tools/path_safety.py`, §33): every path any file tool
  (`search_files`/`open_file`/`create_file`) touches is resolved through
  `sanitize_path()` first -- rejects literal `..` segments, fully resolves the path
  (following symlinks, so a symlink planted inside an allowed directory that points
  outside it isn't a bypass), and requires the result to fall under one of
  `settings.allowed_file_directories_list` (§30, configurable via env). No file tool
  ever passes a raw user/LLM-supplied path straight to the filesystem.
- **`close_application`** (`app/tools/applications.py`): `NEVER_CLOSE` hardcodes
  `code.exe`/`code` as process names that are never terminated, regardless of what the
  "applications" memory category says or how the tool is invoked -- this assistant runs
  from inside VS Code, and every Electron process VS Code spawns (main, renderers, GPU,
  extension host) shares that image name, so an unguarded call would kill the very
  editor/session the assistant is running in.
- **`clipboard_write`** (`app/tools/clipboard.py`): `CONFIRM`, not `SAFE` --
  `clipboard_read` only observes clipboard state, but `clipboard_write` silently
  destroys whatever the user currently has copied, so it needs the same
  `confirmed = True` gate as `create_file`/`close_application`.
