"""
System diagnostics routes (Status tab's "Run system test" button).

Thin HTTP wrapper around `DiagnosticsService` (`app/diagnostics/service.py`) -- same
"route only validates + shapes the response, the real module owns the behavior" split
as `app.api.routes.routines` over `RoutineRegistry`/`RoutineEngine`. `GET /checks`
lists the component catalog so the dashboard can render a checkbox per component before
anything has run; `POST /run` actually runs the battery (all components, or just the
`checks` named in the request body).

Every route here requires a valid bearer token (`get_current_user`, router-level
dependency), same boundary as `app.api.routes.routines`/`app.api.routes.discord` --
diagnostic detail (which LLM provider is misconfigured, whether the DB is reachable)
is exactly the kind of thing that shouldn't be readable by an unauthenticated caller.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.dependencies import (
    get_current_user,
    get_health_manager,
    get_stt_provider,
    get_tool_registry,
    get_tts_provider,
)
from app.diagnostics.service import CheckResult, DiagnosticsService
from app.llm.health import HealthManager
from app.platforms.discord import DiscordBotManager, get_discord_bot_manager
from app.tools.registry import ToolRegistry
from app.voice.stt import SpeechToTextProvider
from app.voice.tts import TextToSpeechProvider

router = APIRouter(tags=["diagnostics"], dependencies=[Depends(get_current_user)])


class CheckOut(BaseModel):
    name: str
    label: str


class CheckResultOut(BaseModel):
    name: str
    label: str
    ok: bool
    message: str
    duration_ms: float


class DiagnosticsRunIn(BaseModel):
    # None (the default, and what an empty/omitted body means) runs every component --
    # a specific list customizes the run down to just the named ones (§ the routine
    # dashboard's "which command isn't working" use case).
    checks: list[str] | None = None


class DiagnosticsRunOut(BaseModel):
    ok: bool
    results: list[CheckResultOut]


def _build_service(
    tool_registry: ToolRegistry,
    discord_manager: DiscordBotManager,
    stt_provider: SpeechToTextProvider,
    tts_provider: TextToSpeechProvider,
    health_manager: HealthManager,
) -> DiagnosticsService:
    return DiagnosticsService(
        tool_registry=tool_registry,
        discord_manager=discord_manager,
        stt_provider=stt_provider,
        tts_provider=tts_provider,
        health_manager=health_manager,
    )


def _serialize(result: CheckResult) -> dict[str, Any]:
    return {
        "name": result.name,
        "label": result.label,
        "ok": result.ok,
        "message": result.message,
        "duration_ms": result.duration_ms,
    }


@router.get("/diagnostics/checks", response_model=list[CheckOut])
def list_diagnostic_checks(
    tool_registry: ToolRegistry = Depends(get_tool_registry),
    discord_manager: DiscordBotManager = Depends(get_discord_bot_manager),
    stt_provider: SpeechToTextProvider = Depends(get_stt_provider),
    tts_provider: TextToSpeechProvider = Depends(get_tts_provider),
    health_manager: HealthManager = Depends(get_health_manager),
) -> list[dict[str, str]]:
    service = _build_service(tool_registry, discord_manager, stt_provider, tts_provider, health_manager)
    return service.list_checks()


@router.post("/diagnostics/run", response_model=DiagnosticsRunOut)
def run_diagnostics(
    payload: DiagnosticsRunIn,
    tool_registry: ToolRegistry = Depends(get_tool_registry),
    discord_manager: DiscordBotManager = Depends(get_discord_bot_manager),
    stt_provider: SpeechToTextProvider = Depends(get_stt_provider),
    tts_provider: TextToSpeechProvider = Depends(get_tts_provider),
    health_manager: HealthManager = Depends(get_health_manager),
) -> dict[str, Any]:
    service = _build_service(tool_registry, discord_manager, stt_provider, tts_provider, health_manager)

    if payload.checks is not None:
        known = {c["name"] for c in service.list_checks()}
        unknown = [name for name in payload.checks if name not in known]
        if unknown:
            raise HTTPException(status_code=422, detail=f"Unknown check(s): {', '.join(unknown)}.")

    results = service.run(only=payload.checks)
    return {"ok": all(r.ok for r in results), "results": [_serialize(r) for r in results]}
