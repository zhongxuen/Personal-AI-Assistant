"""
Shared fixtures (§37 Phase 2 / file 03, extended file 04 prompts 1 & 2, file 09 prompt 2).

`app/tools/tasks.py`, `app/tools/routines.py`, `app/tools/applications.py`,
`app/projects/discovery.py`, `app/memory/service.py`, `app/memory/retrieval.py`,
`app/tasks/scheduler.py`, and `app/routines/engine.py` each open their own short-lived
DB session via a module-level
`SessionLocal` imported directly from `app.database.database` -- there's no dependency
injection to swap a session in. `test_db` monkeypatches those module-level references
to a throwaway in-memory SQLite database (shared across the whole fixture via
`StaticPool`, since every tool call opens a brand new `Session`) so tests never touch
the real `jarvis.db` file. `app/routines/registry.py`'s `RoutineRegistry` and
`app/memory/service.py`'s `MemoryService` both take an injected `Session` instead (same
pattern as `TaskService`), so neither needs a patch of its own here -- only the
module-level `SessionLocal` each one's seed function/`applications.py`'s resolver/
`retrieval.py`'s `retrieve_relevant` opens for itself.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import models  # noqa: F401 -- registers tables on Base.metadata
from app.database.database import Base


class RealOpenAICallBlocked(BaseException):
    """Raised when a test lets a request reach OpenAI's real transport. A
    `BaseException`, like `tests.core.test_zero_llm.NetworkBlocked`, so no provider's
    broad `except Exception` can swallow it and turn it into a quiet failover."""


@pytest.fixture(autouse=True)
def _block_real_openai(monkeypatch):
    """No test may ever reach the real OpenAI API.

    `Settings` reads the repo's real `.env`, which holds a real OPENAI_API_KEY, so any
    test that builds a real provider chain and sends an LLM_REQUIRED message would
    otherwise make a real, billed call. The `openai` SDK (3.x) talks through its own
    `httpx2` package, which the `httpx` patches in the `no_network` fixtures don't
    cover -- so it's blocked here, for every test. Tests exercise `OpenAIProvider` by
    injecting a stub client (`provider._client`), never the transport.
    """
    import httpx2

    def _blocked(*_args, **_kwargs):
        raise RealOpenAICallBlocked("A test attempted a real OpenAI network call.")

    async def _blocked_async(*_args, **_kwargs):
        raise RealOpenAICallBlocked("A test attempted a real OpenAI network call.")

    monkeypatch.setattr(httpx2.Client, "send", _blocked)
    monkeypatch.setattr(httpx2.AsyncClient, "send", _blocked_async)


@pytest.fixture()
def test_db(monkeypatch):
    """A throwaway in-memory SQLite DB wired into every tool module that opens its own
    `SessionLocal()` rather than accepting an injected `Session`. Yields the test
    sessionmaker in case a test wants to inspect state directly.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    monkeypatch.setattr("app.tools.tasks.SessionLocal", TestSessionLocal)
    monkeypatch.setattr("app.tools.routines.SessionLocal", TestSessionLocal)
    monkeypatch.setattr("app.tools.applications.SessionLocal", TestSessionLocal)
    monkeypatch.setattr("app.projects.discovery.SessionLocal", TestSessionLocal)
    monkeypatch.setattr("app.tasks.scheduler.SessionLocal", TestSessionLocal)
    monkeypatch.setattr("app.routines.engine.SessionLocal", TestSessionLocal)
    monkeypatch.setattr("app.memory.service.SessionLocal", TestSessionLocal)
    monkeypatch.setattr("app.memory.retrieval.SessionLocal", TestSessionLocal)
    # The WhatsApp webhook's background task opens its own session for the same
    # reason the tool modules above do -- it runs after the response (and so after a
    # request-scoped `Depends(get_db)` session would already be closed).
    monkeypatch.setattr("app.api.routes.whatsapp_webhook.SessionLocal", TestSessionLocal)

    yield TestSessionLocal

    engine.dispose()
