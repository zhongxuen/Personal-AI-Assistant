"""
WhatsAppAdapter unit tests (§20-22, file 18 prompt 3).

Pure translation-layer tests, the direct counterpart of
`tests/platforms/test_discord_adapter.py`: no `AssistantCore`, no real tools, no
network. `extract_inbound_message`, `to_request`, and `to_platform_output` are the
adapter's whole surface (§41 Rule 7 -- no assistant logic lives there), so each is fed a
hand-built Meta webhook payload / a plain `AssistantResponse` and checked for exactly
one thing: is the translation right.

The one way this file has to be heavier than the Discord one is the database. Discord's
author id *is* the identity, so `DiscordAdapter` is stateless; a WhatsApp sender is a
phone number, so `WhatsAppAdapter` takes a `Session` and answers "who is this?" out of
`app/whatsapp/linking.py`'s columns (see `app/platforms/whatsapp.py`'s docstring). The
`test_db` fixture therefore backs these tests, and a linked `User` is seeded before any
`to_request` call.

`tests/platforms/test_whatsapp_capability.py` covers the other half -- this adapter
wired to a real `AssistantCore` end to end -- and `tests/api/test_whatsapp_webhook.py`
covers the HTTP boundary in front of it.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.auth.service import AuthService
from app.core.models import AssistantResponse
from app.platforms.whatsapp import (
    _WHATSAPP_MESSAGE_LIMIT,
    NoInboundMessageError,
    UnlinkedSenderError,
    WhatsAppAdapter,
    build_text_payload,
    extract_inbound_message,
)

USERNAME = "zhongxuen"
PASSWORD = "correct horse battery staple"

# Digits-only, the form Meta puts in `wa_id`/`messages[].from` and the form
# `normalize_phone_number` stores.
PHONE = "60123456789"
UNLINKED_PHONE = "60199999999"


def _payload(
    *messages: dict[str, Any], include_messages_key: bool = True
) -> dict[str, Any]:
    """Meta's `entry[] -> changes[] -> value.messages[]` envelope around `messages`.

    Deliberately built out in full rather than trimmed to the three fields the adapter
    reads: the point of these tests is that a *realistic* payload -- extra keys,
    `contacts`, `metadata` and all -- translates correctly, not that a payload shaped
    like the adapter's expectations does.
    """
    value: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "15550001111", "phone_number_id": "111222333"},
        "contacts": [{"profile": {"name": "Zhong Xuen"}, "wa_id": PHONE}],
    }
    if include_messages_key:
        value["messages"] = list(messages)
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "9876543210", "changes": [{"field": "messages", "value": value}]}],
    }


def _text_message(
    body: str, *, from_number: str = PHONE, message_id: str = "wamid.HBgLNjAxMjM0NTY3ODkVAgAS"
) -> dict[str, Any]:
    return {
        "from": from_number,
        "id": message_id,
        "timestamp": "1756339200",
        "type": "text",
        "text": {"body": body},
    }


def _status_callback() -> dict[str, Any]:
    """A delivery-receipt POST: same envelope, `statuses` instead of `messages`. Meta
    sends these through the very same webhook, so "no message here" is a routine
    payload, not a malformed one.
    """
    payload = _payload(include_messages_key=False)
    payload["entry"][0]["changes"][0]["value"]["statuses"] = [
        {"id": "wamid.XYZ", "status": "delivered", "recipient_id": PHONE}
    ]
    return payload


@pytest.fixture()
def linked_db(test_db):
    """A session on the in-memory DB with `PHONE` already linked to a real `User`.

    Linking is done by writing the column directly rather than by driving
    `generate_link_code`/`consume_link_code` -- that flow is
    `tests/whatsapp/test_linking.py`'s subject, and depending on it here would make an
    adapter-translation failure look like a linking failure.
    """
    db = test_db()
    user = AuthService(db).create_user(USERNAME, PASSWORD)
    user.whatsapp_phone_number = PHONE
    db.commit()
    yield db
    db.close()


# --- extract_inbound_message -------------------------------------------------------------


class TestExtractInboundMessage:
    def test_reads_sender_text_and_message_id(self):
        inbound = extract_inbound_message(_payload(_text_message("what time is it")))

        assert inbound is not None
        assert inbound.from_number == PHONE
        assert inbound.text == "what time is it"
        assert inbound.message_id == "wamid.HBgLNjAxMjM0NTY3ODkVAgAS"

    def test_returns_none_for_a_status_callback(self):
        """The routine "nothing to do" case -- see `NoInboundMessageError`'s docstring."""
        assert extract_inbound_message(_status_callback()) is None

    def test_returns_none_for_a_non_text_message(self):
        """Media/interactive messages are out of scope for this phase and must not be
        guessed at -- the webhook acks and stays quiet rather than answering an image
        as if it were text.
        """
        image = {
            "from": PHONE,
            "id": "wamid.IMG",
            "type": "image",
            "image": {"id": "media-id", "mime_type": "image/jpeg"},
        }
        assert extract_inbound_message(_payload(image)) is None

    def test_takes_the_first_text_message_when_several_are_batched(self):
        """One POST can batch several messages (someone typing quickly). 1:1 scope means
        answering the first, not queueing up five separate replies.
        """
        inbound = extract_inbound_message(
            _payload(
                _text_message("first", message_id="wamid.ONE"),
                _text_message("second", message_id="wamid.TWO"),
            )
        )

        assert inbound is not None
        assert inbound.text == "first"
        assert inbound.message_id == "wamid.ONE"

    def test_skips_a_leading_non_text_message_to_reach_a_text_one(self):
        sticker = {"from": PHONE, "id": "wamid.STK", "type": "sticker", "sticker": {}}
        inbound = extract_inbound_message(_payload(sticker, _text_message("hello")))

        assert inbound is not None
        assert inbound.text == "hello"

    def test_message_id_is_none_when_meta_omits_it(self):
        message = _text_message("hello")
        del message["id"]

        inbound = extract_inbound_message(_payload(message))

        assert inbound is not None
        assert inbound.message_id is None

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param(None, id="none"),
            pytest.param("not-a-dict", id="string"),
            pytest.param({}, id="empty-dict"),
            pytest.param({"entry": "not-a-list"}, id="entry-not-a-list"),
            pytest.param({"entry": [{"changes": None}]}, id="changes-null"),
            pytest.param({"entry": [{"changes": [{"value": []}]}]}, id="value-not-a-dict"),
            pytest.param(
                {"entry": [{"changes": [{"value": {"messages": {}}}]}]}, id="messages-not-a-list"
            ),
        ],
    )
    def test_walks_rather_than_indexes_a_malformed_envelope(self, payload):
        """The signature check proves a payload came *from Meta*, not that it is shaped
        the way Meta's docs say. Every level must degrade to None instead of raising.
        """
        assert extract_inbound_message(payload) is None

    def test_returns_none_when_from_or_body_is_missing(self):
        no_body = {"from": PHONE, "id": "wamid.X", "type": "text", "text": {}}
        no_from = {"id": "wamid.X", "type": "text", "text": {"body": "hi"}}

        assert extract_inbound_message(_payload(no_body)) is None
        assert extract_inbound_message(_payload(no_from)) is None


# --- to_request --------------------------------------------------------------------------


class TestToRequest:
    def test_builds_assistant_request_with_platform_whatsapp(self, linked_db):
        request = WhatsAppAdapter(linked_db).to_request(_payload(_text_message("what time is it")))

        assert request.platform == "whatsapp"

    def test_maps_linked_user_to_user_id_as_username(self, linked_db):
        """Not the raw phone number and not Discord's platform-native id: this platform
        resolves to a real `User` row, so it carries the same `username` identity string
        `app/api/routes/assistant.py` puts on every authenticated platform's request.
        """
        request = WhatsAppAdapter(linked_db).to_request(_payload(_text_message("hello")))

        assert request.user_id == USERNAME

    def test_maps_message_body_to_message(self, linked_db):
        request = WhatsAppAdapter(linked_db).to_request(
            _payload(_text_message("remind me to buy milk tomorrow"))
        )

        assert request.message == "remind me to buy milk tomorrow"

    def test_strips_surrounding_whitespace_from_the_body(self, linked_db):
        request = WhatsAppAdapter(linked_db).to_request(
            _payload(_text_message("  what are my tasks?  \n"))
        )

        assert request.message == "what are my tasks?"

    def test_keeps_a_bot_style_prefix_intact(self, linked_db):
        """No counterpart to Discord's `_strip_bot_prefix`: a 1:1 WhatsApp chat has no
        other participants to address, so "Jarvis," is the user's own wording and
        removing it would be the adapter editing the message.
        """
        request = WhatsAppAdapter(linked_db).to_request(_payload(_text_message("Jarvis, hello")))

        assert request.message == "Jarvis, hello"

    def test_maps_sender_number_to_conversation_id(self, linked_db):
        request = WhatsAppAdapter(linked_db).to_request(_payload(_text_message("hello")))

        assert request.conversation_id == PHONE

    def test_normalizes_conversation_id_so_one_person_has_one_history(self, linked_db):
        """"+60 12-345 6789" and "60123456789" are the same person -- if they produced
        two `conversation_id`s, one conversation's history would silently split in two.
        """
        request = WhatsAppAdapter(linked_db).to_request(
            _payload(_text_message("hello", from_number="+60 12-345 6789"))
        )

        assert request.conversation_id == PHONE

    def test_raises_no_inbound_message_error_for_a_status_callback(self, linked_db):
        with pytest.raises(NoInboundMessageError):
            WhatsAppAdapter(linked_db).to_request(_status_callback())

    def test_raises_unlinked_sender_error_for_an_unknown_number(self, linked_db):
        """Raised, not returned as None, so it is impossible to accidentally build an
        `AssistantRequest` carrying a placeholder identity.
        """
        with pytest.raises(UnlinkedSenderError) as exc_info:
            WhatsAppAdapter(linked_db).to_request(
                _payload(_text_message("what are my tasks?", from_number=UNLINKED_PHONE))
            )

        assert exc_info.value.phone_number == UNLINKED_PHONE

    def test_does_not_create_a_user_for_an_unknown_number(self, linked_db, test_db):
        """Nothing is auto-created, ever -- the webhook is a far wider surface than the
        deliberately-absent public register route (`app/auth/service.py`).
        """
        from app.database.models import User

        before = linked_db.query(User).count()
        with pytest.raises(UnlinkedSenderError):
            WhatsAppAdapter(linked_db).to_request(
                _payload(_text_message("hi", from_number=UNLINKED_PHONE))
            )

        assert linked_db.query(User).count() == before


# --- to_platform_output / build_text_payload ---------------------------------------------


class TestToPlatformOutput:
    def test_builds_the_cloud_api_text_message_body(self, linked_db):
        payload = WhatsAppAdapter(linked_db).to_platform_output(
            AssistantResponse(text="It's currently 09:41 AM."), PHONE
        )

        assert payload == {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": PHONE,
            "type": "text",
            "text": {"preview_url": False, "body": "It's currently 09:41 AM."},
        }

    def test_addresses_the_recipient_passed_in(self, linked_db):
        """The deliberate signature widening versus `PlatformAdapter.to_platform_output`:
        the Cloud API carries the recipient inside the JSON, so a payload without `to`
        is not a thing Meta accepts.
        """
        payload = WhatsAppAdapter(linked_db).to_platform_output(
            AssistantResponse(text="ok"), UNLINKED_PHONE
        )

        assert payload["to"] == UNLINKED_PHONE

    def test_normalizes_the_recipient_number(self, linked_db):
        payload = WhatsAppAdapter(linked_db).to_platform_output(
            AssistantResponse(text="ok"), "+60 12-345 6789"
        )

        assert payload["to"] == PHONE

    def test_empty_text_falls_back_to_done(self, linked_db):
        """Same fallback `DiscordAdapter.to_platform_output` uses: Meta rejects an empty
        `text.body`, so the user would get nothing back at all.
        """
        payload = WhatsAppAdapter(linked_db).to_platform_output(AssistantResponse(text=""), PHONE)

        assert payload["text"]["body"] == "Done."

    def test_preview_url_is_false_so_links_do_not_drag_in_a_preview_card(self, linked_db):
        payload = WhatsAppAdapter(linked_db).to_platform_output(
            AssistantResponse(text="see https://example.com"), PHONE
        )

        assert payload["text"]["preview_url"] is False

    def test_tool_calls_and_provider_are_not_rendered(self, linked_db):
        """Translation only -- the outbound body is the human-facing text and nothing
        else, no matter what metadata the response carries.
        """
        response = AssistantResponse(
            text="Done.",
            tool_calls=[{"tool_name": "create_task", "params": {}, "result": {"success": True}}],
            used_llm=True,
            provider="gemini",
        )

        payload = WhatsAppAdapter(linked_db).to_platform_output(response, PHONE)

        assert payload["text"]["body"] == "Done."
        assert "create_task" not in str(payload)


class TestTruncation:
    def test_text_at_exactly_the_limit_is_left_alone(self, linked_db):
        text = "x" * _WHATSAPP_MESSAGE_LIMIT

        payload = WhatsAppAdapter(linked_db).to_platform_output(AssistantResponse(text=text), PHONE)

        assert payload["text"]["body"] == text

    def test_over_long_text_is_truncated_to_the_limit_with_an_ellipsis(self, linked_db):
        """Truncating beats erroring for the same reason it does on Discord: an
        over-long body is rejected outright, so the user would get *nothing* back
        rather than a clipped answer.
        """
        text = "y" * (_WHATSAPP_MESSAGE_LIMIT + 500)

        payload = WhatsAppAdapter(linked_db).to_platform_output(AssistantResponse(text=text), PHONE)
        body = payload["text"]["body"]

        assert len(body) == _WHATSAPP_MESSAGE_LIMIT  # the "..." is inside the cap, not added to it
        assert body.endswith("...")
        assert body[: _WHATSAPP_MESSAGE_LIMIT - 3] == text[: _WHATSAPP_MESSAGE_LIMIT - 3]

    def test_build_text_payload_truncates_the_same_way(self):
        """The webhook route's non-assistant replies (link-your-account, link-confirmed)
        go through `build_text_payload` directly rather than through an
        `AssistantResponse`, so the cap has to live there, not in `to_platform_output`.
        """
        payload = build_text_payload(PHONE, "z" * (_WHATSAPP_MESSAGE_LIMIT + 1))

        assert len(payload["text"]["body"]) == _WHATSAPP_MESSAGE_LIMIT
        assert payload["text"]["body"].endswith("...")
