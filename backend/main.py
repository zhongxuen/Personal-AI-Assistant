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

from app.api.routes.assistant import router as assistant_router
from app.api.routes.health import router as health_router
from app.config.logging import configure_logging
from app.config.settings import get_settings
from app.database import models  # noqa: F401  (import registers models on Base.metadata)
from app.database.database import Base, engine

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("jarvis")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("JARVIS backend starting (env=%s)", settings.app_env)
    # Phase 0: bootstrap tables directly from the ORM models. This should
    # migrate to Alembic migrations before any real data/production use
    # (tracked as tech debt in md-files/01-project-foundation.md).
    Base.metadata.create_all(bind=engine)
    yield


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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=settings.api_host, port=settings.api_port, reload=True)
