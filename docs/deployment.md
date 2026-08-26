# Deployment

Covers `md-files/12-web-client-vercel.md` prompt 3 (§31, §32, §33). Two independent deploys:
the frontend to **Vercel**, the backend/API to **Render**'s free web-service tier. Neither one
bundles secrets into the other — see [Secrets boundary](#secrets-boundary) before anything else.

**Live instances** (personal deployment, as of this writing):
- Backend: `https://jarvis-api-hi10.onrender.com` (Render free web service, name `jarvis-api`)
- Frontend: `https://personal-ai-assistant-goh-zhong-xuen-s-projects.vercel.app` (Vercel project
  `personal-ai-assistant`)

## Secrets boundary

`Frontend → Backend/API → Gemini`, never `Frontend → Gemini` directly (§31). Concretely:

- `backend/app/config/settings.py`'s `Settings` reads every secret (`GEMINI_API_KEY`,
  `AUTH_SECRET_KEY`, `AUTH_SEED_PASSWORD`, ...) from process environment variables / a local
  `.env` file — never a hardcoded default that's actually usable (`AUTH_SECRET_KEY`'s shipped
  default is a deliberately-obvious placeholder; `main.py` logs a warning at startup if it's
  still active outside `APP_ENV=development`).
- The **only** environment variable the frontend build reads is `VITE_API_BASE_URL`
  (`frontend/src/services/api.ts`) — a plain URL, not a secret. Vite only exposes env vars
  prefixed `VITE_` to client code at all, so nothing named `GEMINI_API_KEY` etc. can leak into
  the frontend bundle by accident; nothing in `frontend/` reads a non-`VITE_`-prefixed variable.
- Verify it directly whenever you touch this: `cd frontend && npm run build && grep -ri
  "gemini_api_key\|auth_secret_key" dist/assets/*.js` should print nothing.
- `render.yaml`'s secret-bearing keys (`AUTH_SECRET_KEY`, `AUTH_SEED_PASSWORD`,
  `GEMINI_API_KEY`, `CORS_ORIGINS`) are all `sync: false` — Render prompts for these once in
  its dashboard rather than storing the values in the repo.

## Backend prep (already in place)

No code changes were needed to make the backend deployable — it already reads config the right
way. What was checked:

- **CORS** — `CORSMiddleware` in `backend/main.py` reads `settings.cors_origin_list`, itself
  parsed from the `CORS_ORIGINS` env var (comma-separated). Nothing hardcodes
  `http://localhost:5173`; production sets `CORS_ORIGINS` to the deployed frontend's exact
  origin (see [Deploy order](#deploy-order) below — this is set *after* the Vercel URL exists).
- **Port binding** — Render assigns its own `$PORT`; the start command (`uvicorn main:app --host
  0.0.0.0 --port $PORT`) binds to it directly rather than going through `settings.api_port`
  (which only applies to `python main.py`'s own `if __name__ == "__main__"` block, not this
  invocation).
- **Desktop-only tools stay dormant on the server** — `winotify`/`pyttsx3`/`faster-whisper`
  (notifications, TTS, STT) are all lazily imported inside their handler functions, never at
  module load, and every desktop tool (`open_application`, `run_terminal_command`,
  `clipboard_*`, `open_file`/`create_file`/`search_files`, `show_notification`) declares
  `platforms=["desktop"]` *and* is additionally gated at the HTTP layer by
  `app.api.local_only.enforce_desktop_local_only` — a request claiming `platform="desktop"`
  that doesn't arrive from loopback gets a 403 before any tool runs. Deployed on Render, none of
  that code path executes; `/api/voice/message` in particular always 403s remotely by design
  (voice is desktop-local only for now — see that route's docstring).
- **Tests** — `pytest` (331 tests) passes as of this deploy; run it again after any further
  change before redeploying.

## Environment variables

Set these on the **backend host** (Render dashboard → service → Environment), never in the repo:

| Variable | Required | Notes |
|---|---|---|
| `APP_ENV` | yes | `production` — silences the "still on dev DB defaults" assumptions and makes the insecure-`AUTH_SECRET_KEY` check actually fire. |
| `CORS_ORIGINS` | yes | Exact deployed frontend origin, e.g. `https://personal-ai-assistant-goh-zhong-xuen-s-projects.vercel.app`. Comma-separate if there's more than one (e.g. a Vercel preview URL too). |
| `AUTH_SECRET_KEY` | yes | Long random value, **not** the shipped dev default. Generate: `python -c "import secrets; print(secrets.token_urlsafe(48))"`. |
| `AUTH_SEED_USERNAME` / `AUTH_SEED_PASSWORD` | yes (or you can't log in) | One-time bootstrap user, created at startup if it doesn't already exist. |
| `GEMINI_API_KEY` | yes, for LLM features | Unset → `GeminiProvider.is_available()` is `False`, not an error (deterministic tool routing still works). |
| `GEMINI_MODEL` | no | Defaults to `gemini-2.5-flash`. |
| `OLLAMA_ENABLED` | recommended `false` | No Ollama server is reachable from Render; disables the per-request probe outright. |
| `DATABASE_URL` | no | Defaults to `sqlite:///./jarvis.db`. See [SQLite persistence](#known-limitations) below before depending on this for real data. |

Set on the **frontend host** (Vercel → project → Settings → Environment Variables):

| Variable | Required | Notes |
|---|---|---|
| `VITE_API_BASE_URL` | yes | The deployed backend's base URL, e.g. `https://jarvis-api-hi10.onrender.com` (no trailing slash, no `/api` suffix — routes already include it). Left unset, the frontend calls relative `/api/...` paths, which only works behind Vite's dev proxy. |

## Deploy order

The backend and frontend URLs are circular inputs to each other's config, so deploy in this
order:

1. **Backend first** (Render) — get its URL (`https://jarvis-api.onrender.com` or whatever Render
   assigns).
2. **Frontend second** (Vercel), with `VITE_API_BASE_URL` set to that backend URL.
3. **Update the backend's `CORS_ORIGINS`** to the now-known Vercel URL and let Render redeploy
   (Render redeploys automatically on an env var change).

### 1. Backend → Render

1. Push this repo to GitHub (already done — `origin` is `zhongxuen/Personal-AI-Assistant`).
2. In the [Render dashboard](https://dashboard.render.com) → **New → Blueprint**. If GitHub
   isn't connected to Render yet, it'll prompt a GitHub App install first — pick "Only select
   repositories" and choose just this repo, not blanket access to every repo in the account.
3. Select `zhongxuen/Personal-AI-Assistant`, branch `main`. Render finds `render.yaml` at the
   repo root and proposes one service, `jarvis-api`, rooted at `backend/`, on the free plan.
4. Render pauses at apply time for every `sync: false` var in `render.yaml` — fill in
   `AUTH_SECRET_KEY` (e.g. `python -c "import secrets; print(secrets.token_urlsafe(48))"`),
   `AUTH_SEED_USERNAME`, `AUTH_SEED_PASSWORD`, `GEMINI_API_KEY`. `CORS_ORIGINS` can be filled in
   now if the Vercel project (and its stable production domain) already exists — Vercel assigns
   that domain the first time a project is created, before the first successful deploy — otherwise
   leave it blank and set it in step 3 below.
5. Deploy Blueprint. Confirm `GET https://<service>.onrender.com/api/health` returns
   `{"status": "ok", "service": "jarvis-backend"}`.
6. Confirm login works: `POST /api/auth/login` (form-encoded `username`/`password` — the
   `AUTH_SEED_*` values) returns a bearer token, and a route like `GET /api/tasks` with
   `Authorization: Bearer <token>` succeeds while the same call without the header 401s.

### 2. Frontend → Vercel

1. `vercel link` **from the repo root**, not from inside `frontend/` — if a Vercel project for
   this repo already exists with Root Directory set to `frontend` (Project Settings → Build and
   Deployment → Root Directory), running the CLI from inside `frontend/` uploads that
   subdirectory's contents and then Vercel tries to apply the Root Directory *again* on top of it,
   producing `The specified Root Directory "frontend" does not exist`. Running from the repo root
   avoids the double-nesting.
2. If Root Directory isn't already set to `frontend` for the linked project, set it in the
   dashboard first (Project Settings → Build and Deployment) — a project whose Root Directory is
   left at the repo root will fail to build this monorepo layout with `vite: command not found`
   (it never `cd`s into `frontend/` before running `npm install`/`vite build`).
3. Set the env var: `vercel env add VITE_API_BASE_URL production` (paste the Render URL from
   step 1.5, no trailing slash), and again for `preview` if you want preview deploys to also hit
   the live backend.
4. Check **Project Settings → Deployment Protection**: if "Vercel Authentication" is enabled, the
   deployed URL redirects everyone (including you, outside a logged-in Vercel session) to a
   Vercel SSO wall before the page even loads — on top of, not instead of, the app's own JWT
   login. Disable it unless that extra gate is actually wanted.
5. `vercel --prod` (from the repo root) to build and deploy. Vercel runs `npm run build`
   (`tsc -b && vite build`) and serves `frontend/dist/` as a static site — no server-side Vercel
   function involved, matching `frontend/package.json`'s existing scripts unchanged.
6. Note the resulting `https://<project>.vercel.app` URL (the *stable* one, e.g.
   `https://personal-ai-assistant-goh-zhong-xuen-s-projects.vercel.app` — not the
   per-deployment `-<hash>-` one Vercel also prints, which changes every deploy).

### 3. Close the loop: backend `CORS_ORIGINS`

Back in Render → `jarvis-api` → Environment, set `CORS_ORIGINS` to the *stable* Vercel URL from
step 2.6 (exact scheme+host, no path, no trailing slash; comma-separate if you also want a
specific preview URL allowed). Render redeploys automatically. Reload the Vercel site and confirm
dashboard requests (Tasks/Routines/Provider Status) fail with `Not authenticated` (expected — see
[Known limitations](#known-limitations)) rather than a CORS error in the browser console; a CORS
error means the origin string doesn't match exactly.

## Known limitations

- **Free instance spin-down** — Render's free web service spins down after periods of
  inactivity; the first request after an idle period can take 50+ seconds while it cold-starts
  (Render's own dashboard surfaces this warning on the service). Not a bug — the alternative on
  the free tier is paying for an always-on instance.
- **SQLite persistence** — Render's free web-service plan has no persistent disk; `jarvis.db`
  lives on the container's ephemeral filesystem and is wiped on every redeploy and on the
  periodic restart free instances get after idling. Fine for demoing the deployed dashboards;
  don't treat it as the durable store for real tasks/routines/memory — that's still the local
  desktop deployment's SQLite file (file 11), which doesn't have this problem. Promoting the
  cloud deployment to a real persistent store (a paid Render disk, or an external Postgres) is
  future work, not done here.
- **No web login UI yet** — `md-files/12-web-client-vercel.md` prompt 2 (a `frontend/src/pages/
  Chat.tsx`-style login/chat page) hasn't landed. The dashboards this deploys
  (Tasks/Routines/Provider Status/Settings) call routes gated by `get_current_user`
  (`app.api.dependencies`), so until that login UI exists, the deployed frontend can't obtain a
  token itself — exercise the deployed backend's auth via `curl`/the OpenAPI docs
  (`/docs` → Authorize) in the meantime, or add the token manually to `localStorage` /
  browser devtools for now.
- **Voice and desktop-only tools stay local** — by design (§23, §33): the deployed backend
  always rejects `platform="desktop"` requests (including all of `/api/voice/message`) that
  don't arrive from loopback. This is not a bug to fix in the cloud deployment; it's the
  boundary `app.api.local_only` exists to enforce.

## Redeploying

Both hosts redeploy on push to the connected branch by default (Render: any push touching
`backend/**` given `rootDir: backend`; Vercel: any push touching `frontend/**` given the linked
project root) — no manual redeploy step needed for ordinary changes. Re-run `pytest` locally
before pushing.
