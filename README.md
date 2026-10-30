# JARVIS — Personal AI Assistant

A modular, quota-efficient personal AI assistant. See [`md-files/development-plan.md`](md-files/development-plan.md)
for the full architecture and phased build order — that document is the source of truth for scope and sequencing.

**Status:** Core assistant loop, tasks/routines/timers, chat attachments, and automatic LLM
failover (OpenAI → local Ollama, with Gemini as an opt-in third) are all in place. The model can
call tools and see their results before answering. The assistant runs on desktop (CLI/local agent), a deployed web client
(Vercel + Render), a Discord bot, and (newly) a mobile-facing API client. See
[`docs/architecture.md`](docs/architecture.md) and [`docs/deployment.md`](docs/deployment.md) for
details, and `md-files/development-plan.md` for what's still ahead.

## Project layout

```
backend/         FastAPI app (Python)
frontend/        React + Vite + TypeScript + Tailwind
tests/           Backend test suite (pytest)
md-files/        Planning docs (gitignored)
docs/            Architecture, security, deployment notes
```

## Prerequisites

- Python 3.12
- Node.js 22+
- An OpenAI API key (`OPENAI_API_KEY` in `.env`) for the primary LLM provider — the owner's
  shared key, used only under `OPENAI-RULES.md` (local); see [`docs/deployment.md`](docs/deployment.md#openai-setup-privacy-and-spend)
- Optional: [Ollama](https://ollama.com) running locally as the LLM fallback

## Setup

```bash
# from repo root
python -m venv .venv
.venv/Scripts/activate        # Windows
pip install -r backend/requirements-dev.txt

cp .env.example .env          # fill in values as needed

cd frontend
npm install
```

## Running

**Everything at once (Windows):** double-click `scripts\start-jarvis.cmd`, or run
`.\scripts\start-jarvis.ps1` from any terminal. It starts the backend and the Vite dev
server in their own console windows, waits for both ports, and opens the dashboard in
your browser. Already-running services are detected by port and reused, so re-running it
is harmless. Useful switches: `-BackendOnly` (API/Discord bot only), `-NoBrowser`,
`-Reload` (uvicorn `--reload`). Stop it by closing the two console windows.

The two commands it wraps, if you'd rather run them by hand:

**Backend** (from `backend/`, with the venv active):

```bash
uvicorn main:app --reload
```

Serves on `http://127.0.0.1:8000`. Health check: `GET /api/health`.

The Discord bot isn't a separate process — it starts automatically inside the backend
(`main.py`'s lifespan) whenever `DISCORD_BOT_TOKEN` is set in `.env`. On Windows, run
`.\scripts\start-discord-bot.ps1` from the repo root instead of the command above for a
one-shot script that activates the venv, checks `DISCORD_BOT_TOKEN` is set, and boots
the backend (pass `-Reload` for the `--reload` dev flow).

Once the backend is running (locally or deployed), the web dashboard's **Settings**
tab has a "Discord bot" panel showing live connection status with Start/Stop controls
(`GET/POST /api/discord/status|start|stop`, backed by `DiscordBotManager` in
`backend/app/platforms/discord.py`) — so toggling the bot on/off no longer requires
restarting the backend process at all, just a configured `DISCORD_BOT_TOKEN`.

**Frontend** (from `frontend/`):

```bash
npm run dev
```

Serves on `http://127.0.0.1:5173` and proxies `/api/*` to the backend (see `vite.config.ts`).

## Testing

```bash
# from repo root, with the venv active
pytest
```

Frontend unit tests (Node's built-in runner, no extra dependencies): `cd frontend && npm test`.

## Environment variables

See `.env.example` for the full list. Never commit `.env` — it's gitignored.

## Privacy and where your data lives

- **Local-only storage.** Conversations, memory and attachments are stored only on the
  device running the JARVIS backend (SQLite + `backend/data/attachments/`, gitignored).
  Nothing is synced to the cloud or kept online.
- **Nothing kept on OpenAI.** Every OpenAI request uses the Responses API with
  `store=False`; history and attachments are sent inline by the backend on each request,
  never uploaded or referenced server-side. (`tests/llm/test_openai_privacy.py` enforces this.)
- **Gemini is opt-in** (`GEMINI_ENABLED=false` by default): its free tier lets Google use
  submitted content, so it's never a silent fallback.

## Known limitations

- **No cross-device sync.** Because data stays on the machine running the backend, using
  JARVIS from another device through the deployed site talks to a **temporary server**: its
  conversations, memory and files are wiped whenever it restarts or redeploys, and they won't
  appear on your desktop install. The web app shows this on the Settings page and, against the
  deployed backend, as a dismissible banner in the chat. More in
  [`docs/deployment.md`](docs/deployment.md#known-limitations).
