"""
Discord platform adapter + bot wiring (§20-22, file 13).

`DiscordAdapter` converts an incoming discord.py `Message` into the standard
`AssistantRequest` `AssistantCore` accepts, and renders the `AssistantResponse` it gets
back into text a Discord channel can be sent -- no assistant logic lives here (§41 Rule
7); this is translation only, exactly like `DesktopAdapter` (app/platforms/desktop.py)
and the web route (app/api/routes/assistant.py).

`build_discord_client()` wires a real discord.py `Client` on top of that: `on_message`
recognizes a message addressed to the bot (a leading "Jarvis" or an actual @-mention of
the bot), builds the request via `AssistantCore.handle_async()` -- the same orchestrator
every other platform calls -- and sends the response text back to the channel.

`on_message` awaits `handle_async` directly on discord.py's own event loop. It used to
hop onto a worker thread via `asyncio.to_thread` and call the synchronous
`AssistantCore.handle()`, which was forced by `handle()` reaching the LLM through
`asyncio.run()` (that raises inside an already-running loop). The cost of that detour
was a brand new event loop for every Discord message, and since an HTTP client's pooled
connections belong to the loop that opened them, a full TLS handshake to the LLM
provider on every message too. Awaiting on the bot's long-lived loop lets
`app.llm.clients` keep one warm connection pool for the life of the process; blocking
work inside the turn (tool handlers, DB access) is offloaded to threads by
`AssistantCore` itself, so the loop still isn't held up.

`DiscordBotManager` owns the actual client lifecycle: `main.py`'s lifespan calls
`get_discord_bot_manager().start()` once at process startup (a no-op when
`settings.discord_bot_token` isn't configured, so a dev machine without a Discord app
set up is unaffected -- see settings.py's `discord_bot_token` docstring), but unlike the
old fire-and-forget `run_discord_bot()` this can also be started/stopped again later
without restarting the backend -- see `app.api.routes.discord`, which is what the web
dashboard's Settings tab actually calls.
"""

from __future__ import annotations

import asyncio
import contextlib
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

# Presence text shown under the bot's name in Discord's member list -- purely
# cosmetic (§ branding), no bearing on message handling above.
_PRESENCE_ACTIVITY = discord.Activity(type=discord.ActivityType.listening, name='"Jarvis, ..."')


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
        await client.change_presence(activity=_PRESENCE_ACTIVITY)

    @client.event
    async def on_message(message: discord.Message) -> None:
        if not _is_addressed_to_bot(message, client):
            return

        request = adapter.to_request(message)

        async def _handle() -> AssistantResponse:
            # Own short-lived session, same convention as every other module that
            # isn't handed a request-scoped `db` (AuthService.seed_default_user,
            # the tools/* handlers).
            #
            # Awaited on the bot's own event loop rather than pushed onto a worker
            # thread with `asyncio.to_thread`, which is what this did while
            # `AssistantCore.handle()` was the only entrypoint (it calls `asyncio.run`,
            # which raises on a thread that already has a running loop). That detour
            # meant a throwaway event loop per Discord message, and an HTTP client's
            # pooled connections die with the loop that opened them -- so every message
            # re-paid a full TLS handshake to the LLM provider. `handle_async` runs on
            # discord.py's long-lived loop instead, where `app.llm.clients` can keep the
            # connection warm, and it offloads its own blocking work (tool handlers, DB)
            # to threads internally.
            with SessionLocal() as db:
                core = AssistantCore(
                    get_tool_registry(), db=db, health_manager=get_health_manager()
                )
                return await core.handle_async(request)

        try:
            response = await _handle()
            await message.channel.send(adapter.to_platform_output(response))
        except Exception:
            logger.exception("Failed to handle Discord message from user %s", request.user_id)
            await message.channel.send("Sorry, something went wrong handling that.")

    return client


class DiscordBotManager:
    """Owns the process-wide Discord `discord.Client`'s start/stop lifecycle so it can
    be toggled on demand -- from the web dashboard's Settings tab (`app.api.routes.
    discord`), not just fire-and-forgotten once from main.py's lifespan the way the old
    module-level `run_discord_bot()` was. `main.py` still calls `start()` unconditionally
    at startup (a no-op when no token is configured, same "absence is a valid,
    non-crashing state" convention as before), so nothing changes for a deploy that
    never touches the new routes -- this only adds the ability to stop/restart the bot
    without restarting the whole backend process.

    Every method here is a coroutine meant to be awaited from the same asyncio event
    loop main.py's lifespan runs on; there's no locking beyond that single-loop
    guarantee, matching every other asyncio-background-task piece in this codebase
    (e.g. app.tasks.scheduler.ReminderScheduler).
    """

    def __init__(self) -> None:
        self._client: discord.Client | None = None
        self._task: asyncio.Task[None] | None = None
        self._last_error: str | None = None

    @property
    def configured(self) -> bool:
        return bool(get_settings().discord_bot_token)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict[str, Any]:
        """Snapshot for `GET /api/discord/status` -- computed live off the client/task
        rather than tracked via `on_ready`/`on_disconnect` event handlers, so there's
        exactly one source of truth (discord.py's own `Client.is_ready()`/
        `Client.is_closed()`) instead of a second copy that could drift from it.
        """
        if not self.configured:
            state = "disabled"
        elif self._task is None:
            state = "stopped"
        elif not self._task.done():
            state = "connected" if self._client is not None and self._client.is_ready() else "starting"
        else:
            state = "error" if self._last_error else "stopped"
        return {
            "configured": self.configured,
            "state": state,
            "username": str(self._client.user) if self._client and self._client.is_ready() else None,
            "error": self._last_error,
        }

    async def start(self) -> None:
        """No-op if the bot isn't configured or is already running -- safe to call
        unconditionally, which is exactly what both main.py's lifespan (every startup)
        and `POST /api/discord/start` (possibly while it's already connected) do.
        """
        token = get_settings().discord_bot_token
        if not token or self.running:
            return
        self._last_error = None
        client = build_discord_client()
        self._client = client
        self._task = asyncio.create_task(self._run(client, token))

    async def _run(self, client: discord.Client, token: str) -> None:
        try:
            await client.start(token)
        except Exception as exc:  # noqa: BLE001 -- surfaced via status(), not re-raised: a
            # crashed bot task must not take the rest of the backend down with it.
            self._last_error = str(exc)
            logger.exception("Discord bot task failed")

    async def stop(self) -> None:
        """No-op if the bot isn't currently running. Closes the live client (which ends
        `client.start()` inside `_run` cleanly) and waits for that task to actually
        finish before returning, so a caller that immediately calls `start()` again
        right after doesn't race the old client's teardown.
        """
        if self._client is not None and not self._client.is_closed():
            await self._client.close()
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        self._client = None


# Process-wide singleton, same convention as `app.api.dependencies`' `_registry`/
# `_health_manager` -- not defined there because `app.api.dependencies` is imported by
# this module (`get_health_manager`/`get_tool_registry` above), and importing this
# module back from there would be circular.
_discord_bot_manager = DiscordBotManager()


def get_discord_bot_manager() -> DiscordBotManager:
    return _discord_bot_manager
