# CLAUDE.md

## ⚠️ Temporary project location — read this first

This repo currently lives at:

```
C:\Users\Goh Zhong Xuen\Dev\Personal-AI-Assistant
```

This is **not** the normal location. Development usually happens under the "Coding" folder
(e.g. `C:\Users\Goh Zhong Xuen\Coding\...`), but that folder is synced by OneDrive, and OneDrive
sync activity has been causing VS Code to crash/close the entire window while working here.

As a **temporary workaround**, the project was moved to `Dev` (outside the OneDrive sync scope)
so development can continue without interruption.

**Do not** treat this path as permanent, and don't "fix" it by moving things back on your own.
Once the OneDrive sync issue is resolved, the project will be moved back to the `Coding` folder.
When that happens, this note should be removed/updated.

If you (Claude) are reasoning about absolute paths, repo location, or writing setup docs, use the
current `Dev` path as-is — just don't bake in assumptions that this is the project's forever home.

---

## OpenAI API key rules — VERY CRUCIAL, read before touching anything OpenAI

Whenever OpenAI is used (code, config, docs, or advice about the key), the owner's rules in
`OPENAI-RULES.md` apply. They are final: never weaken, reinterpret or bypass them, and if
something can't be built within them, stop and ask. All OpenAI traffic goes through
`backend/app/llm/openai_guard.py` (the only file that may import `openai`); the build gates
`backend/scripts/check_openai.py` and `frontend/scripts/check-openai.mjs` must keep passing.

@OPENAI-RULES.md

---

## Project overview

See [`README.md`](README.md) and [`md-files/development-plan.md`](md-files/development-plan.md)
(gitignored planning doc) for architecture and phased build order.
