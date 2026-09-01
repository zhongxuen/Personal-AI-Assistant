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
- **Tests** — `pytest` (335 tests) passes as of this deploy; run it again after any further
  change before redeploying.

## Environment variables

Set these on the **backend host** (Render dashboard → service → Environment), never in the repo:

| Variable | Required | Notes |
|---|---|---|
| `APP_ENV` | yes | `production` — silences the "still on dev DB defaults" assumptions and makes the insecure-`AUTH_SECRET_KEY` check actually fire. |
| `CORS_ORIGINS` | yes | Exact deployed frontend origin, e.g. `https://personal-ai-assistant-goh-zhong-xuen-s-projects.vercel.app`. Comma-separate if there's more than one (e.g. a Vercel preview URL too). |
| `ASSISTANT_TIMEZONE` | yes (deployed) | IANA name of **the user's** timezone, e.g. `Asia/Kuala_Lumpur`. Render containers run in UTC, so without this the assistant answers "what time is it" hours off (with a right-looking date) and parses "remind me tomorrow at 8pm" into the wrong zone. Pinned in `render.yaml`; the code default matches. Set to an empty value only for a desktop install, where the host clock *is* the user's clock. |
| `AUTH_SECRET_KEY` | yes | Long random value, **not** the shipped dev default. Generate: `python -c "import secrets; print(secrets.token_urlsafe(48))"`. |
| `AUTH_SEED_USERNAME` / `AUTH_SEED_PASSWORD` | yes (or you can't log in) | One-time bootstrap user, created at startup if it doesn't already exist. |
| `GEMINI_API_KEY` | yes, for LLM features | Unset → `GeminiProvider.is_available()` is `False`, not an error (deterministic tool routing still works). |
| `GEMINI_MODEL` | no | Pinned to `gemini-3.6-flash` in `render.yaml`; same value is the code default if neither is set (`gemini-2.5-flash` was retired by Google on 2026-08-30). **Must be a model `GEMINI_API_KEY` can actually reach** — see [Gemini says MISCONFIGURED — model_not_found](#gemini-says-misconfigured--model_not_found) below. |
| `OLLAMA_ENABLED` | recommended `false` | No Ollama server is reachable from Render; disables the per-request probe outright. |
| `DATABASE_URL` | no | Defaults to `sqlite:///./jarvis.db`. See [SQLite persistence](#known-limitations) below before depending on this for real data. |
| `DISCORD_BOT_TOKEN` | no | Unset -> the Discord bot never starts (`DiscordBotManager.start()` no-ops); the rest of the backend is unaffected. |
| `WHATSAPP_ACCESS_TOKEN` / `WHATSAPP_PHONE_NUMBER_ID` / `WHATSAPP_VERIFY_TOKEN` / `WHATSAPP_APP_SECRET` | no | All four are needed together for WhatsApp; any one unset -> the feature stays off. See [WhatsApp Cloud API setup](#whatsapp-cloud-api-setup-file-18) below for where each value comes from. |

Set on the **frontend host** (Vercel → project → Settings → Environment Variables):

| Variable | Required | Notes |
|---|---|---|
| `VITE_API_BASE_URL` | yes | The deployed backend's base URL, e.g. `https://jarvis-api-hi10.onrender.com` (no trailing slash, no `/api` suffix — routes already include it). Left unset, the frontend calls relative `/api/...` paths, which only works behind Vite's dev proxy. |

## Web client (file 12 prompt 2)

`frontend/src/pages/Login.tsx` gates the whole app: `App.tsx` renders it until
`services/auth.ts` has a token (from a successful `POST /api/auth/login`, or a previous
session's token still in `localStorage`), then unlocks the Chat/Tasks/Routines/Provider
Status/Settings tabs. Logging out, or any protected request coming back 401 (an
expired/invalid token — see `services/api.ts`'s `ensureOk`), clears the token and drops back to
the login screen.

`frontend/src/pages/Chat.tsx` is the web platform's chat adapter — it POSTs to the exact same
`/api/assistant/message` endpoint desktop/voice already use, with `platform="web"`, so a message
that resolves to a desktop-only tool (e.g. "open vscode") comes back with `ToolExecutor`'s §22
rejection (`"This action isn't available on web."`) rendered in the chat like any other reply,
not a silent no-op. The Routine Dashboard's "Run now" button shows the same warning proactively,
before a run is even attempted, for any routine containing a step whose tool doesn't declare
`"web"` in its `platforms` (`frontend/src/pages/Routines.tsx`'s `desktopOnlyStepNames`) — purely
advisory client-side, since `POST /api/routines/{name}/run` (`app/api/routes/routines.py`) is
what actually enforces it, by inferring `RequesterContext.platform` from whether the request
itself arrived from loopback (`app.api.local_only.is_local_client`) rather than always assuming
`"desktop"` the way it used to.

## WhatsApp Cloud API setup (file 18)

Account/dashboard work, done once, in Meta's own UI — nothing here is scriptable from this repo,
which is why it's written down rather than automated. The equivalent for Discord is a single value
(`DISCORD_BOT_TOKEN`, created in the Discord Developer Portal — see
`md-files/13-discord-adapter.md`, which references the setup the same way); WhatsApp needs four
values plus a webhook Meta has to be able to reach, hence the longer walkthrough.

Two things to keep in mind before starting:

- **Don't hardcode Meta's pricing or free-tier terms anywhere** (development plan §3 — the same
  rule Gemini quotas get). Conversation allowances and rates change; read the current numbers off
  Meta's docs when you need them, and keep any budget/threshold in `Settings` rather than in code
  or in this file.
- **Free-form replies only work inside WhatsApp's 24-hour customer-service window**, which resets
  each time the user messages in. That covers ordinary chat (someone asks, the assistant answers).
  Proactive messages *outside* that window — a reminder nobody just asked for — need a
  Meta-approved message template, which is why reminder delivery over WhatsApp is a separate,
  later phase (file 18 task 8) rather than part of this setup.

### 1. Create the Meta app and add WhatsApp

1. At [developers.facebook.com](https://developers.facebook.com) → **My Apps → Create App**. Pick
   the type that offers the WhatsApp product (Meta labels this "Business" today; the label moves
   around — what matters is that **WhatsApp** appears in the product list afterwards).
2. In the new app → **Add product → WhatsApp → Set up**. Meta provisions a test WhatsApp Business
   Account (WABA) and a **free test phone number** with it. That test number is enough for all of
   development: no purchased number, no business verification.
3. Open **WhatsApp → API Setup**. This one panel has most of what's needed:
   - **Phone number ID** — a numeric id shown *under* the test number. This, not the number
     itself, is `WHATSAPP_PHONE_NUMBER_ID`; outbound sends go to `/{phone_number_id}/messages`.
   - **WhatsApp Business Account ID** (WABA id) — note it down. The backend doesn't read it, but
     Meta's own dashboard and any later message-template work do.
   - **Temporary access token** — valid ~24 hours. Fine for the first day of development; it
     expires silently, and a send that suddenly 401s is almost always this.
   - **Recipient phone numbers** — the test number can only message numbers added here (a small
     fixed limit, currently five). Add your own number and confirm the code WhatsApp sends you,
     or nothing you build will be able to reply to you.

### 2. Get a long-lived access token

The 24-hour token is only for the first day. For anything that should stay working — including
the Render deployment — create a **System User** token instead:

1. [business.facebook.com](https://business.facebook.com) → **Business settings → Users → System
   users → Add**, role Admin.
2. **Add assets** → assign both the app and the WABA to that system user, with full control.
3. **Generate new token** → pick the app, set expiration **Never**, and tick the
   `whatsapp_business_messaging` and `whatsapp_business_management` permissions.
4. Copy it immediately — Meta shows it exactly once. This is `WHATSAPP_ACCESS_TOKEN`.

### 3. Collect the two webhook secrets

- `WHATSAPP_APP_SECRET` — **App settings → Basic → App secret** (click Show). The webhook route
  verifies `X-Hub-Signature-256` against this on every inbound POST; it is the inbound
  caller-identity boundary, the role the bot token plays for Discord.
- `WHATSAPP_VERIFY_TOKEN` — **you invent this one.** Any long random string
  (`python -c "import secrets; print(secrets.token_urlsafe(32))"`). It goes into the backend env
  *and* into Meta's webhook config below; Meta echoes it back on the verification handshake so the
  route can tell a real subscription request from anyone else's.

### 4. Set the environment variables

Locally in `.env` (see `.env.example`'s placeholder block); on Render, service → Environment. All
four are backend-only — none is ever sent to a frontend, and unlike `VAPID_PUBLIC_KEY` there is no
half of this that a browser needs:

```
WHATSAPP_ACCESS_TOKEN=...        # system user token from step 2
WHATSAPP_PHONE_NUMBER_ID=...     # numeric id from step 1.3, not the phone number
WHATSAPP_VERIFY_TOKEN=...        # the string you invented in step 3
WHATSAPP_APP_SECRET=...          # app secret from step 3
```

Any one of them unset means WhatsApp stays off and the rest of the backend behaves exactly as it
does today (`backend/app/config/settings.py`, same convention as `DISCORD_BOT_TOKEN`).

### 5. Point Meta's webhook at the backend

The route (`GET`/`POST /api/whatsapp/webhook`, `backend/app/api/routes/whatsapp_webhook.py`)
is in place. Meta validates the callback URL at the moment you save it, so it has to be deployed
and reachable before you save it here.

1. **WhatsApp → Configuration → Edit** in the app dashboard.
2. **Callback URL**: `https://<backend-host>/api/whatsapp/webhook` — e.g.
   `https://jarvis-api-hi10.onrender.com/api/whatsapp/webhook`. It must be public HTTPS with a
   valid certificate; `localhost` will not work. For local development, expose the dev server
   through a tunnel (`cloudflared tunnel --url http://localhost:8000`, ngrok, or similar) and use
   the tunnel's HTTPS URL — it changes on each restart, so expect to re-save this during dev.
3. **Verify token**: the exact `WHATSAPP_VERIFY_TOKEN` value. Meta immediately GETs the callback
   URL with it and refuses to save unless the route echoes `hub.challenge` back.
4. **Webhook fields** → subscribe to **`messages`**. Without this, verification succeeds and no
   message ever arrives — the most common "it saved fine but nothing happens" cause.
5. Render free-tier note: a spun-down instance can miss the first delivery while it cold-starts.
   Meta retries, but the keep-warm ping in [Known limitations](#known-limitations) matters more
   here than it does for the dashboard.

### 6. Link your phone number to your account

A WhatsApp sender is a phone number, not a username and password, so the backend won't act on
messages from a number it doesn't recognise — and it never creates an account for one, the same
"no public register route" posture `backend/app/auth/service.py` already takes. Pair once:

1. Signed in to the web app (or with a bearer token), `POST /api/whatsapp/link-code`. It returns a
   short pairing code, valid ~15 minutes, single use.
2. Send any WhatsApp message containing that code to the test number, from the phone you want
   linked (it has to be one of the recipient numbers from step 1.3).
3. The webhook matches the code, stores your number on your user row, and every later message from
   that number resolves to you with no code involved. `GET /api/whatsapp/link` shows the current
   state; `DELETE /api/whatsapp/link` unlinks.

An unlinked number always gets the same reply telling it how to link (`UNLINKED_REPLY` in
`backend/app/whatsapp/linking.py`) and nothing else — no tool ever runs for it.

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
specific preview URL allowed). Render redeploys automatically. Reload the Vercel site: it should
land on the login page (`frontend/src/pages/Login.tsx`); sign in with the `AUTH_SEED_*`
credentials and confirm the Chat/Tasks/Routines/Provider Status tabs load real data rather than a
CORS error in the browser console (a CORS error means the origin string doesn't match exactly) or
a stuck login (wrong `AUTH_SEED_*` values, or `VITE_API_BASE_URL` pointed at the wrong backend).

## Troubleshooting

### Gemini says MISCONFIGURED — `model_not_found`

Symptom: chat answers every non-deterministic message with *"I can't reach any reasoning provider
right now, so I can't work through that request. I can still handle direct commands in the
meantime."*, and the Provider Status page shows `Health: MISCONFIGURED — model_not_found:<name>`.

Cause: `GEMINI_MODEL` names a model the deployed `GEMINI_API_KEY` can't reach — a typo, or a model
that has since been retired (Google answers both with HTTP 404). `GeminiProvider` classifies that
as a `PERMANENT_ERROR`, `HealthManager` marks the provider `MISCONFIGURED` on the first one, and
that state is **sticky** — no cooldown clears it, so every later request skips Gemini entirely and
falls through to the message above. Note the failing call is a *config* fault, not a quota one:
it does not consume `GEMINI_DAILY_REQUEST_BUDGET` (see `QuotaManager._UNBILLED_STATUSES`), though
it does still show up in the Provider Status page's request/failure counts, which report every
attempt.

Fix:

1. List what the deployed key can actually reach:
   `python -c "from google import genai; print([m.name for m in genai.Client(api_key='YOUR_KEY').models.list()])"`
   — run this against the key set on **Render**, which is not necessarily the one in your local
   `.env`.
2. Set `GEMINI_MODEL` (Render → `jarvis-api` → Environment) to a name from that list, or update
   the pinned `value:` in `render.yaml`.
3. Let Render redeploy. The restart clears the sticky `MISCONFIGURED` (provider health is
   in-memory only). If you'd rather not wait out a redeploy — or you changed the key/model
   without triggering one — hit **Reset health** on the Gemini card in the Provider Status page
   (`POST /api/diagnostics/providers/gemini/reset`), which clears the same state in place. It only
   clears bookkeeping: if the model name is still wrong, the next request puts it right back.

## Known limitations

- **Free instance spin-down** — Render's free web service spins down after periods of
  inactivity; the first request after an idle period can take 50+ seconds while it cold-starts
  (Render's own dashboard surfaces this warning on the service), and login from the Vercel
  frontend can fail outright with "Failed to fetch" if the request lands mid-spin-up rather than
  waiting it out. Not a bug — the alternative on the free tier is paying for an always-on
  instance. `.github/workflows/keep-backend-warm.yml` pings `/api/health` every 10 minutes so the
  service never sees 15 idle minutes and (in practice) never spins down — a free workaround, not
  a guarantee (GitHub Actions cron can occasionally run late, and this still counts against
  Render's free-tier monthly hour cap). If cold starts still happen, upgrade `jarvis-api` to a
  paid Render plan instead of relying on the ping.
- **SQLite persistence** — Render's free web-service plan has no persistent disk; `jarvis.db`
  lives on the container's ephemeral filesystem and is wiped on every redeploy and on the
  periodic restart free instances get after idling. Fine for demoing the deployed dashboards;
  don't treat it as the durable store for real tasks/routines/memory — that's still the local
  desktop deployment's SQLite file (file 11), which doesn't have this problem. Promoting the
  cloud deployment to a real persistent store (a paid Render disk, or an external Postgres) is
  future work, not done here.
- **Voice and desktop-only tools stay local** — by design (§23, §33): the deployed backend
  always rejects `platform="desktop"` requests (including all of `/api/voice/message`) that
  don't arrive from loopback. This is not a bug to fix in the cloud deployment; it's the
  boundary `app.api.local_only` exists to enforce.

## Redeploying

Both hosts redeploy on push to the connected branch by default (Render: any push touching
`backend/**` given `rootDir: backend`; Vercel: any push touching `frontend/**` given the linked
project root) — no manual redeploy step needed for ordinary changes. Re-run `pytest` locally
before pushing.
