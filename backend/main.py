"""
JARVIS backend entrypoint.

App wiring, config, logging, DB connection, the health route, and the
AssistantCore-backed /api/assistant/message route (Phase 1, deterministic
tool routing only -- no LLM layer yet). See md-files/development-plan.md.
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.dependencies import get_tool_registry
from app.api.routes.assistant import router as assistant_router
from app.api.routes.health import router as health_router
from app.api.routes.llm_usage import router as llm_usage_router
from app.api.routes.routines import router as routines_router
from app.api.routes.tasks import router as tasks_router
from app.config.logging import configure_logging
from app.config.settings import get_settings
from app.database import models  # noqa: F401  (import registers models on Base.metadata)
from app.database.database import Base, engine
from app.routines.scheduler import RoutineScheduler
from app.tasks.scheduler import ReminderScheduler
from app.tools import register_default_tools

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("jarvis")

# Phase 3 (file 04 prompt 1): one reminder scheduler for the process, built against the
# same process-wide registry as everything else so it dispatches show_notification
# through the same ToolExecutor path (§41 Rule 6).
reminder_scheduler = ReminderScheduler(get_tool_registry())

# Phase 3 (file 04 prompt 2, optional): scheduled-routine trigger, reusing
# reminder_scheduler's own BackgroundScheduler instance rather than starting a second
# background thread. Nothing is scheduled by default -- no persisted cron config exists
# yet (see app/routines/scheduler.py's docstring) -- this just makes
# `routine_scheduler.schedule(name, cron_expression)` available for whatever wires up
# cron config later (an API route, a settings file, ...). Until something calls it,
# routines remain manual-trigger-only via run_routine, same as before this existed.
routine_scheduler = RoutineScheduler(reminder_scheduler.scheduler, get_tool_registry())


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("JARVIS backend starting (env=%s)", settings.app_env)
    # Phase 0: bootstrap tables directly from the ORM models. This should
    # migrate to Alembic migrations before any real data/production use
    # (tracked as tech debt in md-files/01-project-foundation.md).
    Base.metadata.create_all(bind=engine)
    # Phase 2 (file 03): register the built-in deterministic tools against the
    # process-wide registry so every request routes against the full tool set. Also
    # seeds the "coding" routine as a persisted row the first time this runs (file 04
    # prompt 2) -- see register_default_tools' docstring.
    register_default_tools(get_tool_registry())
    # Phase 3 (file 04): start polling task_reminders for due reminders.
    reminder_scheduler.start()
    yield
    reminder_scheduler.shutdown()


app = FastAPI(title="JARVIS API", version="0.0.1", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router, prefix="/api")
app.include_router(assistant_router, prefix="/api")
app.include_router(tasks_router, prefix="/api")
app.include_router(routines_router, prefix="/api")
app.include_router(llm_usage_router, prefix="/api")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=settings.api_host, port=settings.api_port, reload=True)
