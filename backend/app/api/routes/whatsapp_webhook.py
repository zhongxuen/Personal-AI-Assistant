"""
WhatsApp Cloud API webhook (§37 Phase 13, file 18 prompt 2).

Meta's two-verb contract on one URL (`/api/whatsapp/webhook`):

  * `GET` -- the subscription handshake. Meta calls it once, at the moment you save the
    callback URL in the app dashboard, with `hub.mode`/`hub.verify_token`/`hub.challenge`
    query parameters, and refuses to save unless the route echoes `hub.challenge` back
    as plain text. Answered only when `hub.verify_token` matches
    `settings.whatsapp_verify_token`.
  * `POST` -- inbound message delivery, and also delivery/read status callbacks (which
    carry no message and are simply acked).

**Why this is its own module, separate from `app.api.routes.whatsapp`.** That router
carries a blanket `Depends(get_current_user)`: every pairing-code route is called by
someone holding a bearer token. This one is called by Meta, which has no token to send.
Its caller identity is the `X-Hub-Signature-256` HMAC instead -- an HMAC-SHA256 of the
exact raw request body, keyed by the Meta *app secret*, which only Meta and this backend
know. That check is this route's entire trust boundary, playing the role Discord's bot
token plays before `DiscordAdapter.to_request()` runs, and it is verified against the raw
bytes *before* the payload is parsed, let alone acted on. Unset `whatsapp_app_secret`
therefore means **reject**, never "skip the check": "absence is a valid, non-crashing
state" (settings.py) is about features no-opping, and a security check that no-ops into
open access is the one place that convention must not apply. A backend with no WhatsApp
configured answers 403 to anything that reaches this path, which is exactly right --
nothing legitimate is pointed at it.

**Why the work happens in a background task.** The handshake, the signature check, and
the payload parse all decide the HTTP status, so they run inline. Everything after that
-- resolving the sender, `AssistantCore.handle()`, sending the reply -- runs in a
FastAPI `BackgroundTasks` job so Meta gets its 200 immediately. That is not tidiness: an
LLM round trip can take longer than Meta waits for a webhook, and a timed-out delivery is
*retried*, which on an inline handler would mean the user's question answered twice.
Acking first turns that failure mode off. It also means the assistant's own failures
never become a non-200 Meta would retry -- there is nothing useful for Meta to redeliver
if our LLM provider was down, so those are logged, not signalled.

The background task opens its own short-lived `SessionLocal()` rather than using a
request-scoped `Depends(get_db)` session, because a request-scoped one is closed the
moment the ack is sent -- the same reason `app/platforms/discord.py`'s `on_message` opens
its own, and the same convention as every module here that isn't handed a `db`.

**No hop through `/api/assistant/message`.** `AssistantCore` is called directly, the
server-side-adapter shape (`docs/architecture.md`'s "Two adapter shapes") Discord's bot
event handler already uses. Routing through the shared HTTP route would mean inventing a
bearer token for a caller that has none, purely to satisfy a boundary this route has
already satisfied a different way.

The background task awaits `AssistantCore.handle_async()` on the running loop. It
previously called the sync `handle()` via `asyncio.to_thread`, because `handle()` reaches
the LLM through `asyncio.run()`, which raises inside a running event loop -- the same
detour `on_message` made, for the same reason, at the same cost: a throwaway event loop
per message, and therefore a fresh TLS handshake to the LLM provider on every message.

Three replies can come back to a WhatsApp sender, and only one of them runs a tool:
an unlinked number gets `UNLINKED_REPLY`; the single message carrying a valid pairing
code gets `LINKED_REPLY` and is not treated as a command; every later message from a
linked number goes to `AssistantCore`. See `app/whatsapp/linking.py`.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from app.api.dependencies import get_health_manager, get_tool_registry
from app.config.settings import get_settings
from app.core.assistant import AssistantCore
from app.core.models import AssistantResponse
from app.database.database import SessionLocal
from app.llm.health import HealthManager
from app.platforms.whatsapp import (
    InboundMessage,
    WhatsAppAdapter,
    build_text_payload,
    extract_inbound_message,
    send_message,
)
from app.tools.registry import ToolRegistry
from app.whatsapp.linking import LINKED_REPLY, UNLINKED_REPLY, WhatsAppLinkService

logger = logging.getLogger("jarvis.api.whatsapp_webhook")

# Meta's own prefix on the signature header value: "sha256=<hex digest>".
_SIGNATURE_PREFIX = "sha256="

# What every POST that gets past the signature check returns, whatever happened after.
# Meta only reads the status code -- the body is for humans reading a curl output.
_ACK = {"status": "received"}

# A separate router from `app.api.routes.whatsapp`'s despite the shared "/whatsapp"
# prefix (FastAPI merges paths across routers fine): that one is authenticated at the
# router level and this one deliberately cannot be. Same tag so both halves group
# together in the OpenAPI docs.
router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])


def _verify_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """Whether `signature_header` is Meta's HMAC of exactly these bytes.

    Constant-time compared (`hmac.compare_digest`) so a wrong signature can't be walked
    one character at a time off response timing -- the same discipline
    `app/auth/security.py`'s `verify_password` applies to password hashes.

    False when `whatsapp_app_secret` is unset: with no key there is no way to *verify*
    anything, and the only safe reading of "can't verify" on a trust boundary is "don't
    trust it". See this module's docstring.
    """
    app_secret = get_settings().whatsapp_app_secret
    if not app_secret:
        logger.warning(
            "WhatsApp webhook POST rejected: WHATSAPP_APP_SECRET is not configured, so "
            "the X-Hub-Signature-256 HMAC cannot be verified."
        )
        return False
    if not signature_header or not signature_header.startswith(_SIGNATURE_PREFIX):
        return False

    expected = hmac.new(
        app_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header[len(_SIGNATURE_PREFIX) :])


@router.get("/webhook", response_class=PlainTextResponse)
def verify_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> str:
    """Meta's subscription handshake. Echoes `hub.challenge` verbatim, or 403s.

    Plain text, not JSON: Meta compares the response body to the challenge string it
    sent, and a JSON-encoded `"1158201444"` (quotes included) is not that string.

    The challenge is only echoed when the verify token matches. An unset
    `whatsapp_verify_token` matches nothing -- the `not configured_token` guard exists
    so a missing setting can't be satisfied by a caller sending no token either, which
    is what a bare `configured_token == hub_verify_token` would allow with both None.
    """
    configured_token = get_settings().whatsapp_verify_token
    if (
        not configured_token
        or hub_verify_token != configured_token
        or hub_mode != "subscribe"
        or not hub_challenge
    ):
        logger.warning("WhatsApp webhook verification rejected (mode=%r).", hub_mode)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="WhatsApp webhook verification failed.",
        )
    logger.info("WhatsApp webhook verification handshake succeeded.")
    return hub_challenge


@router.post("/webhook")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str | None = Header(default=None),
    registry: ToolRegistry = Depends(get_tool_registry),
    health_manager: HealthManager = Depends(get_health_manager),
) -> dict[str, str]:
    """Inbound delivery. Verifies the signature, then acks and handles out of band.

    The raw body is read before anything else -- the HMAC is over the exact bytes Meta
    signed, so re-serialising a parsed body would produce a different digest over the
    same message (key order, whitespace) and reject every legitimate request.
    """
    raw_body = await request.body()
    if not _verify_signature(raw_body, x_hub_signature_256):
        # 403, not 401: there is no authentication scheme to challenge the caller with,
        # and nothing about a retry with the same body would help.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid X-Hub-Signature-256.",
        )

    try:
        payload = json.loads(raw_body)
    except ValueError:
        # Correctly signed but not JSON should be impossible from Meta; 400 rather than
        # a silent ack so it's visible if it ever happens.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook body is not valid JSON.",
        ) from None

    inbound = extract_inbound_message(payload)
    if inbound is None:
        # A status callback, a media message, or an envelope shape this doesn't read.
        # All ack: there is nothing for Meta to usefully redeliver.
        logger.debug("WhatsApp webhook POST carried no plain-text message; acked.")
        return _ACK

    background_tasks.add_task(_process_inbound, payload, inbound, registry, health_manager)
    return _ACK


async def _process_inbound(
    payload: Any,
    inbound: InboundMessage,
    registry: ToolRegistry,
    health_manager: HealthManager,
) -> None:
    """Resolve the sender, produce a reply, send it. Runs after the ack; never raises.

    A background task that raised would surface as an error on a connection already
    closed with a 200, so every failure is caught and logged here instead -- the same
    posture `send_message` and `WebPushSender.send_to_user` take, for the same reason:
    there is no caller left to report it to.
    """
    try:
        outbound = await _build_outbound(payload, inbound, registry, health_manager)
        await send_message(outbound)
    except Exception:
        logger.exception(
            "Failed to handle inbound WhatsApp message %s.", inbound.message_id
        )


async def _build_outbound(
    payload: Any,
    inbound: InboundMessage,
    registry: ToolRegistry,
    health_manager: HealthManager,
) -> dict[str, Any]:
    """The three-way branch from this module's docstring, resolved against its own
    session; returns the outbound Cloud API JSON to send.

    Awaited on the running event loop. This used to be a sync function dispatched via
    `asyncio.to_thread`, forced by `AssistantCore.handle()` reaching the LLM through
    `asyncio.run()` (which raises on a thread that already has a running loop). That
    detour cost a throwaway event loop per inbound message and, with it, a fresh TLS
    handshake to the LLM provider -- pooled connections belong to the loop that opened
    them. `handle_async` runs on uvicorn's long-lived loop instead and offloads its own
    blocking work internally; the linking lookups left inline here are single indexed
    SQLite reads.

    Takes both the whole `payload` (what the adapter translates) and the already-
    extracted `inbound` (the sender and raw text, which the linking branch needs *before*
    any `User` exists) rather than re-deriving one from the other.
    """
    with SessionLocal() as db:
        adapter = WhatsAppAdapter(db)
        links = WhatsAppLinkService(db)

        if links.get_by_phone_number(inbound.from_number) is None:
            # Not linked yet -- the one thing an unknown number can do is present a
            # valid pairing code. Anything else is told how to link, and no tool runs.
            if links.consume_link_code(inbound.text, inbound.from_number) is None:
                logger.info("WhatsApp message from an unlinked number; sent link instructions.")
                return build_text_payload(inbound.from_number, UNLINKED_REPLY)
            logger.info("WhatsApp number linked via pairing code.")
            return build_text_payload(inbound.from_number, LINKED_REPLY)

        # `to_request` repeats the lookup just done above. That is a deliberate duplicate
        # of one indexed query, in exchange for `to_request` keeping the
        # `(webhook_payload) -> AssistantRequest` shape the platform-adapter contract
        # (`app/platforms/base.py`) describes, instead of taking a pre-resolved `User`
        # the caller had to fetch first.
        assistant_request = adapter.to_request(payload)

        # Process-wide registry/HealthManager, passed down from the route's `Depends`
        # rather than built here, so provider cooldowns and quota state are the same ones
        # every other platform's requests move (see `app.api.dependencies`).
        core = AssistantCore(registry, db=db, health_manager=health_manager)
        response: AssistantResponse = await core.handle_async(assistant_request)
        return adapter.to_platform_output(response, inbound.from_number)
