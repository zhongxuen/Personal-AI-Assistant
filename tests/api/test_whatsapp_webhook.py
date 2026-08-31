"""
WhatsApp webhook route tests (file 18 prompt 3 -- `GET`/`POST /api/whatsapp/webhook`).

This route's trust boundary is not a bearer token -- Meta has none to send -- it is the
`X-Hub-Signature-256` HMAC over the raw body, keyed by the Meta app secret (see
`app/api/routes/whatsapp_webhook.py`'s docstring). `tests/api/test_whatsapp_link.py`
proves the *authenticated* half of WhatsApp refuses unauthenticated callers; this file
proves the unauthenticated half refuses *unsigned* ones, which is the same guarantee
expressed the only way this caller allows.

Three things get asserted, in the order the route decides them:

  * `GET` -- the subscription handshake echoes `hub.challenge` as plain text on a
    matching verify token, and 403s on anything else.
  * `POST` with a valid signature -- 200, immediate ack, and the work happening
    afterwards in the background task rather than inline.
  * `POST` unsigned or badly signed -- 403 before the body is parsed, and *nothing*
    runs: no session opened, no assistant, no outbound send.

`get_settings` is stubbed per test (it is `@lru_cache`d, and the real one reads the
developer's `.env`) the same way `tests/push/test_sender.py` and
`tests/platforms/test_discord_manager.py` stub it. `send_message` is patched at
`app.api.routes.whatsapp_webhook.send_message` throughout: nothing here reaches Meta's
Graph API. `TestClient` runs `BackgroundTasks` jobs before returning from the request
call, so an assertion about a background send is safe to make immediately after.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_health_manager, get_tool_registry
from app.api.routes import whatsapp_webhook as webhook_module
from app.auth.service import AuthService
from app.database.database import get_db
from app.llm.health import HealthManager
from app.tools import register_default_tools
from app.tools.registry import ToolRegistry
from app.whatsapp.linking import UNLINKED_REPLY
from main import app

WEBHOOK = "/api/whatsapp/webhook"

APP_SECRET = "meta-app-secret"
VERIFY_TOKEN = "a-token-we-chose"
PHONE = "60123456789"
USERNAME = "zhongxuen"
PASSWORD = "correct horse battery staple"


def _settings(
    *, app_secret: str | None = APP_SECRET, verify_token: str | None = VERIFY_TOKEN
) -> SimpleNamespace:
    return SimpleNamespace(
        whatsapp_app_secret=app_secret,
        whatsapp_verify_token=verify_token,
        whatsapp_access_token="graph-token",
        whatsapp_phone_number_id="111222333",
    )


def _configure(monkeypatch, **kwargs) -> None:
    monkeypatch.setattr(webhook_module, "get_settings", lambda: _settings(**kwargs))


def _signature(body: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _message_payload(text: str, *, from_number: str = PHONE) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "9876543210",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "111222333"},
                            "contacts": [{"wa_id": from_number}],
                            "messages": [
                                {
                                    "from": from_number,
                                    "id": "wamid.ABC",
                                    "timestamp": "1756339200",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def _status_payload() -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "9876543210",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "statuses": [
                                {"id": "wamid.ABC", "status": "delivered", "recipient_id": PHONE}
                            ],
                        },
                    }
                ],
            }
        ],
    }


def _raw(payload: dict[str, Any]) -> bytes:
    """Serialised once and posted as raw `content`, never re-serialised.

    The HMAC is over the exact bytes Meta signed, so a test that signed a dict and then
    let the client re-encode it would be testing a different digest than the route
    verifies -- exactly the bug the route's "read the raw body first" comment exists to
    prevent.
    """
    return json.dumps(payload).encode("utf-8")


@pytest.fixture()
def send_message(monkeypatch) -> AsyncMock:
    """Nothing in this file talks to Meta. Returns the mock so tests can assert on the
    outbound payload the background task produced.
    """
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr(webhook_module, "send_message", mock)
    return mock


@pytest.fixture()
def client(test_db, send_message):
    """The real route, the real registry, the real `AssistantCore` behind it.

    `get_db` is overridden for the rest of the app, but the webhook's background task
    deliberately opens its own `SessionLocal()` (a request-scoped session is closed the
    moment the ack is sent) -- `tests/conftest.py` patches that module-level
    `SessionLocal` onto the in-memory DB for exactly this reason.
    """
    registry = ToolRegistry()
    register_default_tools(registry)

    def override_get_db():
        db = test_db()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_tool_registry] = lambda: registry
    app.dependency_overrides[get_health_manager] = lambda: HealthManager()

    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def linked_user(test_db):
    """A `User` with `PHONE` already linked, so a signed POST reaches `AssistantCore`
    instead of stopping at the link-your-account branch.
    """
    db = test_db()
    user = AuthService(db).create_user(USERNAME, PASSWORD)
    user.whatsapp_phone_number = PHONE
    db.commit()
    db.close()


# --- GET: the subscription handshake ------------------------------------------------------


def test_get_echoes_the_challenge_when_the_verify_token_matches(client, monkeypatch):
    """Meta refuses to save the callback URL unless this echoes `hub.challenge` back."""
    _configure(monkeypatch)

    response = client.get(
        WEBHOOK,
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "1158201444",
        },
    )

    assert response.status_code == 200
    assert response.text == "1158201444"


def test_get_returns_plain_text_not_json(client, monkeypatch):
    """Meta compares the response body to the challenge string it sent, and a
    JSON-encoded `"1158201444"` (quotes included) is not that string.
    """
    _configure(monkeypatch)

    response = client.get(
        WEBHOOK,
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "1158201444",
        },
    )

    assert response.headers["content-type"].startswith("text/plain")
    assert '"' not in response.text


def test_get_rejects_a_wrong_verify_token(client, monkeypatch):
    _configure(monkeypatch)

    response = client.get(
        WEBHOOK,
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "not-the-token",
            "hub.challenge": "1158201444",
        },
    )

    assert response.status_code == 403


def test_get_rejects_a_wrong_mode(client, monkeypatch):
    _configure(monkeypatch)

    response = client.get(
        WEBHOOK,
        params={
            "hub.mode": "unsubscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "1158201444",
        },
    )

    assert response.status_code == 403


def test_get_rejects_a_request_with_no_query_parameters_at_all(client, monkeypatch):
    _configure(monkeypatch)

    assert client.get(WEBHOOK).status_code == 403


def test_get_rejects_everything_when_the_verify_token_is_unset(client, monkeypatch):
    """Including a caller who sends no token either -- the `not configured_token` guard
    exists so `None == None` can't satisfy the check.
    """
    _configure(monkeypatch, verify_token=None)

    with_token = client.get(
        WEBHOOK,
        params={"hub.mode": "subscribe", "hub.verify_token": "anything", "hub.challenge": "1"},
    )
    without_token = client.get(WEBHOOK, params={"hub.mode": "subscribe", "hub.challenge": "1"})

    assert with_token.status_code == 403
    assert without_token.status_code == 403


# --- POST: a valid signature --------------------------------------------------------------


def test_signed_post_is_acked_and_answered(client, monkeypatch, linked_user, send_message):
    """The happy path end to end: signature verified, 200 acked, and the reply sent from
    the background task rather than inline.
    """
    _configure(monkeypatch)
    body = _raw(_message_payload("what time is it"))

    response = client.post(
        WEBHOOK, content=body, headers={"X-Hub-Signature-256": _signature(body)}
    )

    assert response.status_code == 200
    assert response.json() == {"status": "received"}

    send_message.assert_awaited_once()
    outbound = send_message.await_args.args[0]
    assert outbound["messaging_product"] == "whatsapp"
    assert outbound["to"] == PHONE
    assert outbound["type"] == "text"
    # A real answer, not an empty body and not a §22 capability rejection -- `get_time`
    # includes "whatsapp" in its `platforms` (app/tools/system.py), so a linked sender
    # gets the actual time. `tests/platforms/test_whatsapp_capability.py` covers the
    # allowed-versus-rejected split properly; this is just a guard against a green ack
    # that quietly delivered an error string.
    assert outbound["text"]["body"]
    assert "isn't available on whatsapp" not in outbound["text"]["body"]


def test_signed_post_from_an_unlinked_number_gets_the_link_instructions(
    client, monkeypatch, send_message
):
    """A correct signature proves the *message* came from Meta, not that the *sender* is
    anyone we know -- so an unlinked number still gets `UNLINKED_REPLY` and nothing else.
    """
    _configure(monkeypatch)
    body = _raw(_message_payload("what are my tasks?", from_number="60199999999"))

    response = client.post(
        WEBHOOK, content=body, headers={"X-Hub-Signature-256": _signature(body)}
    )

    assert response.status_code == 200
    send_message.assert_awaited_once()
    assert send_message.await_args.args[0]["text"]["body"] == UNLINKED_REPLY


def test_signed_status_callback_is_acked_with_no_reply(client, monkeypatch, send_message):
    """Delivery/read receipts arrive through the same webhook. They ack -- a non-200
    would just make Meta redeliver something there is nothing to do with -- and send
    nothing back.
    """
    _configure(monkeypatch)
    body = _raw(_status_payload())

    response = client.post(
        WEBHOOK, content=body, headers={"X-Hub-Signature-256": _signature(body)}
    )

    assert response.status_code == 200
    assert response.json() == {"status": "received"}
    send_message.assert_not_awaited()


def test_signed_post_with_a_non_json_body_is_a_400(client, monkeypatch, send_message):
    """Correctly signed but not JSON should be impossible from Meta -- surfaced rather
    than silently acked so it is visible if it ever happens.
    """
    _configure(monkeypatch)
    body = b"not json at all"

    response = client.post(
        WEBHOOK, content=body, headers={"X-Hub-Signature-256": _signature(body)}
    )

    assert response.status_code == 400
    send_message.assert_not_awaited()


# --- POST: unsigned / badly signed --------------------------------------------------------


def test_post_with_no_signature_header_is_rejected(client, monkeypatch, linked_user, send_message):
    _configure(monkeypatch)
    body = _raw(_message_payload("open VS Code"))

    response = client.post(WEBHOOK, content=body)

    assert response.status_code == 403
    send_message.assert_not_awaited()


def test_post_with_a_signature_from_the_wrong_secret_is_rejected(
    client, monkeypatch, linked_user, send_message
):
    """The check is the whole trust boundary: anyone who can reach this URL can send
    these bytes, and only the app secret separates them from Meta.
    """
    _configure(monkeypatch)
    body = _raw(_message_payload("open VS Code"))

    response = client.post(
        WEBHOOK,
        content=body,
        headers={"X-Hub-Signature-256": _signature(body, secret="attacker-guess")},
    )

    assert response.status_code == 403
    send_message.assert_not_awaited()


def test_post_whose_body_was_tampered_with_after_signing_is_rejected(
    client, monkeypatch, linked_user, send_message
):
    """A valid signature over *different* bytes must not carry -- this is the replay/
    substitution case the HMAC exists to stop, not merely a missing-header case.
    """
    _configure(monkeypatch)
    signed_body = _raw(_message_payload("what time is it"))
    delivered_body = _raw(_message_payload("open VS Code"))

    response = client.post(
        WEBHOOK,
        content=delivered_body,
        headers={"X-Hub-Signature-256": _signature(signed_body)},
    )

    assert response.status_code == 403
    send_message.assert_not_awaited()


@pytest.mark.parametrize(
    "header",
    [
        pytest.param("", id="empty"),
        pytest.param("deadbeef", id="no-sha256-prefix"),
        pytest.param("sha1=deadbeef", id="wrong-algorithm-prefix"),
        pytest.param("sha256=", id="prefix-only"),
        pytest.param("sha256=not-hex", id="not-a-digest"),
    ],
)
def test_post_with_a_malformed_signature_header_is_rejected(
    client, monkeypatch, send_message, header
):
    _configure(monkeypatch)
    body = _raw(_message_payload("what time is it"))

    response = client.post(WEBHOOK, content=body, headers={"X-Hub-Signature-256": header})

    assert response.status_code == 403
    send_message.assert_not_awaited()


def test_post_is_rejected_when_the_app_secret_is_unset(client, monkeypatch, send_message):
    """"Absence is a valid, non-crashing state" is about features no-opping. A security
    check that no-ops into open access is the one place that convention must not apply,
    so an unconfigured backend answers 403 to everything on this path -- even to a body
    signed with some other secret.
    """
    _configure(monkeypatch, app_secret=None)
    body = _raw(_message_payload("what time is it"))

    response = client.post(
        WEBHOOK, content=body, headers={"X-Hub-Signature-256": _signature(body)}
    )

    assert response.status_code == 403
    send_message.assert_not_awaited()
