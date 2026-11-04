# CLAUDE.md

## Project location

This repo lives at `C:\Users\Goh Zhong Xuen\Coding\Personal-AI-Assistant` (moved back from a
temporary `Dev` folder on 2026-11-05). `Coding` is synced by OneDrive, which earlier made VS Code
crash; if that happens again, tell the owner rather than moving the repo yourself.

---

## OpenAI API key rules — VERY CRUCIAL, read before touching anything OpenAI

Whenever OpenAI is used (code, config, docs, or advice about the key), the owner's rules in
`OPENAI-KEY-RULES.md` (local, gitignored) apply; they win if anything else in this repo
disagrees. This codebase uses the `personal` project's key (data sharing off). All OpenAI
traffic goes through `backend/app/llm/openai_guard.py` (the one client, the only file that
may import `openai`) via `backend/app/llm/openai.py` (tiers, budget check, cost log); the
build gates `backend/scripts/check_openai.py` and `frontend/scripts/check-openai.mjs` must
keep passing.

@OPENAI-KEY-RULES.md

---

## Project overview

See [`README.md`](README.md) and [`md-files/development-plan.md`](md-files/development-plan.md)
(gitignored planning doc) for architecture and phased build order.
