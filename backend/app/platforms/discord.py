"""
Discord platform adapter + bot wiring (§20-22, file 13).

`DiscordAdapter` converts an incoming discord.py `Message` into the standard
`AssistantRequest` `AssistantCore` accepts, and renders the `AssistantResponse` it gets
back into text a Discord channel can be sent -- no assistant logic lives here (§41 Rule
7); this is translation only, exactly like `DesktopAdapter` (app/platforms/desktop.py)
and the web route (app/api/routes/assistant.py).

`build_discord_client()`/`run_discord_bot()` wire a real discord.py `Client` on top of
that: `on_message` recognizes a message addressed to the bot (a leading "Jarvis" or an
actual @-mention of the bot), builds the request via `DiscordAdapter`, calls
`AssistantCore.handle()` -- the same entrypoint every other platform calls -- and sends
the response text back to the channel.

`AssistantCore.handle()` can itself call `asyncio.run()` on the LLM path (see
app/core/assistant.py's `_handle_needs_llm`), which raises if called from inside a
coroutine's already-running event loop. FastAPI's sync routes (app/api/routes/
assistant.py) sidestep this for free by running in FastAPI's threadpool; `on_message`
here is a coroutine, so it has to opt into the same off-loop execution explicitly via
`asyncio.to_thread`.

The bot is optional: `run_discord_bot()` (started from main.py's lifespan) is a no-op
when `settings.discord_bot_token` isn't configured, so a dev machine without a Discord
app configured is unaffected -- see settings.py's `discord_bot_token` docstring.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Protocol

import discord

from app.api.dependencies import get_health_manager, get_tool_registry
from app.config.settings import get_settings
from app.core.assistant import AssistantCore
from app.core.models import AssistantRequest, AssistantResponse
from app.database.database import SessionLocal

logger = logging.getLogger("jarvis.platforms.discord")

# Strips a leading @-mention (<@123>/<@!123>) and/or a leading "Jarvis" name prefix
# (with optional trailing punctuation) before the text reaches AssistantCore -- neither
# is part of the actual command ("Jarvis, what are my tasks?" -> "what are my tasks?").
_MENTION_PREFIX_RE = re.compile(r"^\s*<@!?\d+>\s*[,:]?\s*")
_NAME_PREFIX_RE = re.compile(r"^\s*jarvis\s*[,:]?\s*", re.IGNORECASE)

# Discord's hard per-message character cap.
_DISCORD_MESSAGE_LIMIT = 2000


class DiscordMessage(Protocol):
    """The subset of `discord.Message` this adapter actually reads -- lets tests pass
    a plain mock/stub instead of constructing a real discord.py object."""

    content: str
    author: Any
    channel: Any


def _strip_bot_prefix(content: str) -> str:
    stripped = _MENTION_PREFIX_RE.sub("", content, count=1)
    stripped = _NAME_PREFIX_RE.sub("", stripped, count=1)
    return stripped.strip()


class DiscordAdapter:
    """Translates between discord.py's Message/text-reply shape and AssistantCore's
    platform-agnostic AssistantRequest/AssistantResponse (§20-22)."""

    def to_request(self, discord_message: DiscordMessage) -> AssistantRequest:
        return AssistantRequest(
            user_id=str(discord_message.author.id),
            platform="discord",
            message=_strip_bot_prefix(discord_message.content),
            conversation_id=str(discord_message.channel.id),
        )

    def to_platform_output(self, response: AssistantResponse) -> str:
        text = response.text or "Done."
        if len(text) > _DISCORD_MESSAGE_LIMIT:
            text = text[: _DISCORD_MESSAGE_LIMIT - 3] + "..."
        return text


def _is_addressed_to_bot(message: discord.Message, client: discord.Client) -> bool:
    """A message counts as "addressed to the bot" if it @-mentions the bot, or its
    text leads with "Jarvis" (case-insensitive) -- the same two forms
    `_strip_bot_prefix` above knows how to remove."""
    if message.author.bot:
        return False
    if client.user is not None and client.user in message.mentions:
        return True
    return bool(_NAME_PREFIX_RE.match(message.content))


def build_discord_client() -> discord.Client:
    """Construct a `discord.Client` wired to route addressed messages through
    `AssistantCore.handle()` via `DiscordAdapter`. Doesn't connect -- call
    `client.start(token)`/`client.run(token)` on the result, or use
    `run_discord_bot()` below.
    """
    intents = discord.Intents.default()
    intents.message_content = True  # required to read message.content at all
    client = discord.Client(intents=intents)
    adapter = DiscordAdapter()

    @client.event
    async def on_ready() -> None:
        logger.info("Discord bot connected as %s", client.user)

    @client.event
    async def on_message(message: discord.Message) -> None:
        if not _is_addressed_to_bot(message, client):
            return

        request = adapter.to_request(message)

        def _handle() -> AssistantResponse:
            # Own short-lived session, same convention as every other module that
            # isn't handed a request-scoped `db` (AuthService.seed_default_user,
            # the tools/* handlers) -- and run off the event loop thread (see this
            # module's docstring) since AssistantCore.handle may call asyncio.run().
            with SessionLocal() as db:
                core = AssistantCore(
                    get_tool_registry(), db=db, health_manager=get_health_manager()
                )
                return core.handle(request)

        try:
            response = await asyncio.to_thread(_handle)
            await message.channel.send(adapter.to_platform_output(response))
        except Exception:
            logger.exception("Failed to handle Discord message from user %s", request.user_id)
            await message.channel.send("Sorry, something went wrong handling that.")

    return client


async def run_discord_bot() -> None:
    """Start the Discord bot if `DISCORD_BOT_TOKEN` is configured; a no-op otherwise
    (§41 Rule 5: the token is backend-only and never required for the rest of the app
    to run). Intended to be launched as a background task from main.py's lifespan.
    """
    token = get_settings().discord_bot_token
    if not token:
        logger.info("DISCORD_BOT_TOKEN not set -- Discord bot disabled.")
        return

    client = build_discord_client()
    await client.start(token)
