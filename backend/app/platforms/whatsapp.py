"""
WhatsApp Cloud API platform adapter (§20-22, file 18 prompt 2).

The server-side-adapter shape, not the thin-client one (`docs/architecture.md`'s "Two
adapter shapes"): WhatsApp's native input is a Meta-defined webhook payload delivered
to a URL we control, not JSON the end user's client builds -- exactly the situation
`discord.py`'s `Message` object is in, and the opposite of web/mobile
(`app/platforms/web.py`, `app/platforms/mobile.py`), which POST an already-
`AssistantRequest`-shaped body to the shared `/api/assistant/message` route. So this is
a real `to_request`/`to_platform_output` class, and inbound messages arrive through a
dedicated webhook route (`app/api/routes/whatsapp_webhook.py`) that calls
`AssistantCore.handle()` directly, with no HTTP hop.

Translation only -- no assistant logic lives here (§41 Rule 7), same as `DiscordAdapter`
(app/platforms/discord.py) and `DesktopAdapter` (app/platforms/desktop.py).

Three things are genuinely different from Discord, and each one shows up in a signature
below:

  * **The sender is a phone number, not an account.** `to_request` therefore needs the
    database to answer "who is this?", so this adapter takes a `Session` the way every
    service here does (`WhatsAppLinkService`, `TaskService`, `AuthService`) rather than
    being stateless like `DiscordAdapter`. A number with no linked `User` is not a
    request this adapter can build, so `to_request` raises `UnlinkedSenderError` -- the
    webhook route catches it and replies with `UNLINKED_REPLY`
    (`app/whatsapp/linking.py`); nothing is auto-created, ever.
  * **Outbound messages are addressed in the body, not by the object you already
    hold.** Discord's `to_platform_output` returns bare text because the caller already
    has the `message.channel` to send it on. The Cloud API instead has one endpoint per
    *sending* number and carries the *recipient* inside the JSON, so
    `to_platform_output` takes the recipient as a second argument. That's a deliberate
    widening of `PlatformAdapter`'s signature (`app/platforms/base.py`): a payload
    without `"to"` is not a thing Meta accepts, and quietly returning a half-formed one
    would be worse than the mismatch.
  * **Sending is an outbound HTTP call we make**, not a method on a connected client --
    hence `send()` and this module's `httpx` dependency, moved from
    `backend/requirements-dev.txt` to `backend/requirements.txt` for exactly this
    reason: it is now a runtime call, not just `TestClient`'s transport.

`send()` never raises, on the same reasoning as `WebPushSender.send_to_user`
(app/push/sender.py): its caller is a webhook handler whose whole job is to have
already returned 200 to Meta, and an unreachable Graph API must not become an exception
on a path where there is no longer anyone to report it to. Failures are logged and
reported as a `False` return.

Unset credentials mean the feature no-ops rather than errors, the same "absence is a
valid, non-crashing state" convention `settings.py` documents for `discord_bot_token`
and the VAPID pair -- a machine with no Meta app configured never receives an inbound
payload in the first place, and `send()` on one that somehow does simply logs and
declines.

Only plain text is handled, inbound and outbound. Media/interactive messages are
explicitly out of scope for this phase (md-files/18-whatsapp-adapter.md's Scope), and
`extract_inbound_message` returns None for them rather than guessing at a text
rendering -- the webhook still acks, so Meta doesn't retry.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.core.models import AssistantRequest, AssistantResponse
from app.whatsapp.linking import WhatsAppLinkService, normalize_phone_number

logger = logging.getLogger("jarvis.platforms.whatsapp")

# Meta's cap on a text message's `text.body`, the direct analogue of Discord's 2000
# (`_DISCORD_MESSAGE_LIMIT`). Truncating is what `DiscordAdapter.to_platform_output`
# does and for the same reason: an over-long body is rejected outright, so the user
# would get *nothing* back rather than a clipped answer.
_WHATSAPP_MESSAGE_LIMIT = 4096

# Graph API host + pinned version. Meta versions the API and retires each version a
# couple of years after release, so this is a named constant to bump rather than a
# string buried in an f-string -- and deliberately pinned rather than left off the URL,
# since an unversioned call silently follows Meta's current default version and would
# change behaviour underneath us with no deploy of ours involved.
_GRAPH_API_BASE_URL = "https://graph.facebook.com"
_GRAPH_API_VERSION = "v21.0"

# Outbound send timeout. Short on purpose: this runs after the webhook has already
# acked, so nobody is waiting on it, and a hung connection pinning a worker is a worse
# outcome than a reply that fails and gets logged.
_SEND_TIMEOUT_SECONDS = 10.0


class WhatsAppAdapterError(Exception):
    """Base for the two "this payload can't become an AssistantRequest" cases below, so
    a caller that treats them identically can catch one thing."""


class NoInboundMessageError(WhatsAppAdapterError):
    """The payload carried no plain-text user message.

    Routine, not exceptional: Meta delivers delivery/read *status* callbacks through the
    same webhook and the same `changes` envelope, and those legitimately have no
    `messages` array at all. The webhook acks and does nothing.
    """


class UnlinkedSenderError(WhatsAppAdapterError):
    """The sender's number isn't linked to any `User`.

    The whole point of `app/whatsapp/linking.py`: a WhatsApp sender proves nothing but
    "I control this phone number", so an unrecognised number gets `UNLINKED_REPLY` and
    no tool ever runs for it. Raised rather than returning None so it is impossible to
    accidentally build an `AssistantRequest` carrying a placeholder identity.
    """

    def __init__(self, phone_number: str) -> None:
        super().__init__(f"WhatsApp number {phone_number} is not linked to any account.")
        self.phone_number = phone_number


@dataclass(frozen=True)
class InboundMessage:
    """The three things this adapter reads out of Meta's envelope.

    Split out from `to_request` because the webhook route needs the sender and the raw
    text *before* a `User` exists -- that text may be a pairing code
    (`WhatsAppLinkService.consume_link_code`), and the sender is who the
    link-your-account reply has to go back to.
    """

    from_number: str
    text: str
    message_id: str | None = None


def _as_list(value: Any) -> list[Any]:
    """Meta's arrays, defensively. Anything that isn't a list (absent key, null, a bare
    object) becomes an empty list -- the signature check upstream proves a payload came
    from Meta, not that it is shaped the way Meta's docs say, so every level walks
    rather than indexes.
    """
    return value if isinstance(value, list) else []


def extract_inbound_message(payload: Any) -> InboundMessage | None:
    """The first plain-text user message in a webhook payload, or None.

    Meta's envelope is `entry[] -> changes[] -> value.messages[]`, and one POST can in
    principle batch several messages. Only the first text message is taken: this
    platform is scoped to 1:1 conversational use (md-files/18-whatsapp-adapter.md's
    Scope), and in that shape a batch is one person typing quickly, not a queue to work
    through -- answering each of five rapid-fire messages separately reads worse than
    answering the first.

    None covers every "nothing to do here" case at once -- a status callback, a media or
    interactive message, a malformed envelope -- because the caller's response to all of
    them is identical: ack, don't reply.
    """
    if not isinstance(payload, dict):
        return None

    for entry in _as_list(payload.get("entry")):
        if not isinstance(entry, dict):
            continue
        for change in _as_list(entry.get("changes")):
            if not isinstance(change, dict):
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            for message in _as_list(value.get("messages")):
                if not isinstance(message, dict) or message.get("type") != "text":
                    continue
                from_number = message.get("from")
                text_field = message.get("text")
                body = text_field.get("body") if isinstance(text_field, dict) else None
                if not isinstance(from_number, str) or not isinstance(body, str):
                    continue
                message_id = message.get("id")
                return InboundMessage(
                    from_number=from_number,
                    text=body,
                    message_id=message_id if isinstance(message_id, str) else None,
                )
    return None


def build_text_payload(to: str, text: str) -> dict[str, Any]:
    """A Cloud API "send text message" body addressed to `to`.

    Shared by `WhatsAppAdapter.to_platform_output` and by the webhook route's
    non-assistant replies (link-your-account, link-confirmed), which have text to send
    but no `AssistantResponse` to render -- one place that knows the outbound JSON shape
    and the length cap, rather than two that could drift apart.

    `preview_url` is False so a URL inside an answer doesn't drag an unrelated link
    preview card into the chat.
    """
    if len(text) > _WHATSAPP_MESSAGE_LIMIT:
        text = text[: _WHATSAPP_MESSAGE_LIMIT - 3] + "..."
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": normalize_phone_number(to),
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }


def is_configured() -> bool:
    """Whether outbound sending is possible: a token to authorise with, and a
    `phone_number_id` to send from. Same shape as `WebPushSender.configured`, and the
    same consequence -- unset means `send_message` no-ops instead of raising.
    """
    settings = get_settings()
    return bool(settings.whatsapp_access_token and settings.whatsapp_phone_number_id)


async def send_message(payload: dict[str, Any]) -> bool:
    """POST `payload` to the Graph API's `/{phone_number_id}/messages`; returns whether
    Meta accepted it. Never raises -- see this module's docstring.

    Module-level rather than only a method because sending needs nothing but `Settings`,
    while `WhatsAppAdapter` needs a `Session` for its inbound half: the webhook route
    sends its replies after the session that resolved the sender has closed, and this
    lets it do that without holding a database handle open purely to reach a method.
    `WhatsAppAdapter.send` delegates here, the same split as `build_text_payload` and
    `to_platform_output` above.

    Async (unlike `WebPushSender.send_to_user`, which is called from a sync scheduler
    thread) because its caller is the webhook route's async background task; a blocking
    send there would hold the event loop for the whole round trip.
    """
    settings = get_settings()
    if not is_configured():
        logger.warning(
            "WhatsApp send skipped: WHATSAPP_ACCESS_TOKEN/WHATSAPP_PHONE_NUMBER_ID "
            "not configured."
        )
        return False

    url = (
        f"{_GRAPH_API_BASE_URL}/{_GRAPH_API_VERSION}"
        f"/{settings.whatsapp_phone_number_id}/messages"
    )
    try:
        async with httpx.AsyncClient(timeout=_SEND_TIMEOUT_SECONDS) as client:
            http_response = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {settings.whatsapp_access_token}"},
            )
            http_response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # Meta's error body says *why* (expired token, recipient outside the 24-hour
        # customer service window, number not registered), and that reason is the entire
        # diagnostic value of the failure -- bounded so a huge body can't flood the log.
        logger.warning(
            "WhatsApp send rejected by Graph API (HTTP %s): %s",
            exc.response.status_code,
            exc.response.text[:500],
        )
        return False
    except Exception:
        logger.exception("WhatsApp send failed before Meta answered.")
        return False

    return True


class WhatsAppAdapter:
    """Translates between Meta's webhook/Graph API shapes and `AssistantCore`'s
    platform-agnostic `AssistantRequest`/`AssistantResponse` (§20-22).

    Takes an injected `Session` whose lifecycle the caller owns -- same convention as
    `WhatsAppLinkService`/`TaskService`/`AuthService` -- because resolving a sender to a
    `User` is a database question here, unlike Discord where the author id *is* the
    identity.
    """

    def __init__(self, db: Session) -> None:
        self._links = WhatsAppLinkService(db)

    def to_request(self, webhook_payload: Any) -> AssistantRequest:
        """Meta's webhook payload -> `AssistantRequest`.

        Raises `NoInboundMessageError` if the payload carries no text message, and
        `UnlinkedSenderError` if the sender's number isn't linked -- neither is a request
        `AssistantCore` should ever see.

        `user_id` is the linked user's `username`, matching what
        `app/api/routes/assistant.py` puts there for every authenticated platform
        (`request.model_copy(update={"user_id": current_user.username})`) rather than
        Discord's raw platform-native id -- this platform resolves to a real `User` row,
        so it can and should carry the same identity string web and mobile do.

        `conversation_id` is the sender's number in `normalize_phone_number`'s
        digits-only form -- the natural 1:1 conversation scope, playing the role a
        channel id plays for Discord. Normalised so "+60 12-345 6789" and "60123456789"
        can't split one person's conversation history in two.
        """
        inbound = extract_inbound_message(webhook_payload)
        if inbound is None:
            raise NoInboundMessageError("Webhook payload carried no plain-text message.")

        user = self._links.get_by_phone_number(inbound.from_number)
        if user is None:
            raise UnlinkedSenderError(inbound.from_number)

        return AssistantRequest(
            user_id=user.username,
            platform="whatsapp",
            # No counterpart to Discord's `_strip_bot_prefix`: a 1:1 WhatsApp chat has
            # no other participants to address, so there is no "@bot"/"Jarvis," chrome
            # to remove -- the whole message is the command.
            message=inbound.text.strip(),
            conversation_id=normalize_phone_number(inbound.from_number),
        )

    def to_platform_output(self, response: AssistantResponse, to: str) -> dict[str, Any]:
        """`AssistantResponse` -> the Cloud API's outbound message JSON, addressed to
        `to` (the sender's number, from the inbound message this is answering).

        The `to` parameter is the deliberate signature difference from
        `PlatformAdapter.to_platform_output` -- see this module's docstring.

        Empty text falls back to "Done." and over-long text is truncated, both exactly as
        `DiscordAdapter.to_platform_output` does for its own 2000-character cap.
        """
        return build_text_payload(to, response.text or "Done.")

    @property
    def configured(self) -> bool:
        """Whether outbound sending is possible -- see `is_configured` above."""
        return is_configured()

    async def send(self, payload: dict[str, Any]) -> bool:
        """Deliver an outbound payload to Meta. Delegates to `send_message` above --
        the adapter carries this method because "render, then send" is one thought from
        a caller's point of view, but the sending itself needs no adapter state.
        """
        return await send_message(payload)
