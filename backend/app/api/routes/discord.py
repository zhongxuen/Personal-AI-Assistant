"""
Discord bot control routes (web dashboard follow-up to file 13).

Thin HTTP wrappers around `DiscordBotManager` (`app/platforms/discord.py`) -- same
"route only validates + shapes the response, the real module owns the behavior" split
as `app.api.routes.routines` does over `RoutineRegistry`/`RoutineEngine`. Before this
existed, the only way to get the bot online/offline was `scripts/start-discord-bot.ps1`
(a local-machine-only PowerShell script that boots the whole backend process); these
routes let the same start/stop control happen from the web dashboard's Settings tab
against a backend that's already running (locally or on Render), and let that tab show
whether the bot is actually connected instead of the caller having to guess from
backend logs.

Every route here requires a valid bearer token (`get_current_user`, router-level
dependency), same boundary as `app.api.routes.routines` -- `DISCORD_BOT_TOKEN` itself
never crosses this boundary (`DiscordBotManager.status()` reports whether a token is
*configured*, never the token's value).
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.dependencies import get_current_user
from app.platforms.discord import DiscordBotManager, get_discord_bot_manager

router = APIRouter(tags=["discord"], dependencies=[Depends(get_current_user)])


class DiscordStatusOut(BaseModel):
    configured: bool
    state: Literal["disabled", "stopped", "starting", "connected", "error"]
    username: str | None = None
    error: str | None = None


@router.get("/discord/status", response_model=DiscordStatusOut)
def get_discord_status(manager: DiscordBotManager = Depends(get_discord_bot_manager)) -> dict[str, Any]:
    return manager.status()


@router.post("/discord/start", response_model=DiscordStatusOut)
async def start_discord_bot(manager: DiscordBotManager = Depends(get_discord_bot_manager)) -> dict[str, Any]:
    # No-op (not an error) if it's already running or DISCORD_BOT_TOKEN isn't
    # configured -- see DiscordBotManager.start's docstring. The returned status
    # reflects whichever of "disabled"/"starting"/"connected" is actually true right
    # after this call, so a caller pressing "Start" against an unconfigured bot sees
    # "disabled" come back rather than a misleading 200 that implies it's connecting.
    await manager.start()
    return manager.status()


@router.post("/discord/stop", response_model=DiscordStatusOut)
async def stop_discord_bot(manager: DiscordBotManager = Depends(get_discord_bot_manager)) -> dict[str, Any]:
    await manager.stop()
    return manager.status()
