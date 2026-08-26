# JARVIS — Personal AI Assistant

A modular, quota-efficient personal AI assistant. See [`md-files/development-plan.md`](md-files/development-plan.md)
for the full architecture and phased build order — that document is the source of truth for scope and sequencing.

**New user?** Start with [`USER_GUIDE.txt`](USER_GUIDE.txt) for a plain-language rundown of what
JARVIS can do, its limits, and how to use it on desktop, web, Discord, and mobile.

**Status:** Core assistant loop, tasks/routines/timers, and automatic Gemini→Ollama LLM failover
are all in place. The assistant runs on desktop (CLI/local agent), a deployed web client
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
USER_GUIDE.txt   Plain-language guide for new users
```

## Prerequisites

- Python 3.12
- Node.js 22+
- (Later phases) [Ollama](https://ollama.com) running locally for LLM fallback

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

**Backend** (from `backend/`, with the venv active):

```bash
uvicorn main:app --reload
```

Serves on `http://127.0.0.1:8000`. Health check: `GET /api/health`.

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

## Environment variables

See `.env.example` for the full list. Never commit `.env` — it's gitignored.
