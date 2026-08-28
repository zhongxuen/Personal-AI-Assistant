"""
WhatsApp pairing tests (file 18 prompt 1 -- `app/whatsapp/linking.py`).

Service-level, against the shared in-memory `test_db` fixture, same shape as
tests/push/test_service.py: `WhatsAppLinkService` takes an injected `Session`, so
nothing here needs a monkeypatched module-level `SessionLocal`.

The load-bearing cases are the negative ones -- an unknown number, a wrong code, an
expired code, and a replayed code all have to leave the DB untouched, because the
webhook (file 18 prompt 2) turns "no linked user" into `UNLINKED_REPLY` and an
accidental link is the one failure mode that would hand another phone someone's
account.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.auth.service import AuthService
from app.database.models import User
from app.whatsapp.linking import (
    LINK_CODE_LENGTH,
    WhatsAppLinkService,
    extract_link_code,
    normalize_phone_number,
)

PASSWORD = "correct horse battery staple"
PHONE = "60123456789"
OTHER_PHONE = "60987654321"


@pytest.fixture()
def db(test_db):
    session = test_db()
    yield session
    session.close()


def _user(db, username: str = "zhongxuen") -> User:
    return AuthService(db).create_user(username, PASSWORD)


# --- helpers ---------------------------------------------------------------------------


def test_normalize_phone_number_strips_everything_but_digits():
    # Meta sends a digits-only wa_id, humans type "+60 12-345 6789" -- one column value.
    assert normalize_phone_number("+60 12-345 6789") == "60123456789"


def test_extract_link_code_finds_a_code_mid_sentence_case_insensitively():
    assert extract_link_code("my code is abcd2345, thanks") == "ABCD2345"


def test_extract_link_code_returns_none_when_nothing_is_code_shaped():
    assert extract_link_code("hey, what are my tasks today?") is None


# --- generating ------------------------------------------------------------------------


def test_generate_link_code_stores_a_code_and_expiry_on_the_user(db):
    user = _user(db)

    code, expires_at = WhatsAppLinkService(db).generate_link_code(user.id)

    assert len(code) == LINK_CODE_LENGTH
    assert user.whatsapp_link_code == code
    assert user.whatsapp_link_code_expires_at == expires_at
    assert expires_at > datetime.now()
    # A code is a pairing in flight, not a link.
    assert user.whatsapp_phone_number is None


def test_generate_link_code_replaces_any_previous_code(db):
    user = _user(db)
    service = WhatsAppLinkService(db)

    first, _ = service.generate_link_code(user.id)
    second, _ = service.generate_link_code(user.id)

    assert first != second
    assert service.consume_link_code(first, PHONE) is None
    assert service.consume_link_code(second, PHONE) is not None


def test_generate_link_code_rejects_an_unknown_user(db):
    with pytest.raises(ValueError):
        WhatsAppLinkService(db).generate_link_code(4242)


# --- consuming -------------------------------------------------------------------------


def test_consume_link_code_links_the_sender_and_clears_the_code(db):
    user = _user(db)
    service = WhatsAppLinkService(db)
    code, _ = service.generate_link_code(user.id)

    linked = service.consume_link_code(f"link me: {code}", "+60 12-345 6789")

    assert linked is not None and linked.id == user.id
    assert linked.whatsapp_phone_number == PHONE
    assert linked.whatsapp_link_code is None
    assert linked.whatsapp_link_code_expires_at is None
    assert service.get_by_phone_number(PHONE).id == user.id


def test_consume_link_code_is_single_use(db):
    user = _user(db)
    service = WhatsAppLinkService(db)
    code, _ = service.generate_link_code(user.id)
    service.consume_link_code(code, PHONE)

    # Replaying the same message from a different phone must link nothing.
    assert service.consume_link_code(code, OTHER_PHONE) is None
    assert service.get_by_phone_number(OTHER_PHONE) is None


def test_consume_link_code_ignores_a_message_with_no_code(db):
    user = _user(db)
    service = WhatsAppLinkService(db)
    service.generate_link_code(user.id)

    assert service.consume_link_code("what are my tasks?", PHONE) is None
    assert service.get_by_phone_number(PHONE) is None


def test_consume_link_code_rejects_a_code_nobody_holds(db):
    _user(db)
    service = WhatsAppLinkService(db)

    assert service.consume_link_code("ABCD2345", PHONE) is None
    assert service.get_by_phone_number(PHONE) is None


def test_consume_link_code_rejects_and_clears_an_expired_code(db):
    user = _user(db)
    service = WhatsAppLinkService(db)
    code, _ = service.generate_link_code(user.id)
    user.whatsapp_link_code_expires_at = datetime.now() - timedelta(seconds=1)
    db.commit()

    assert service.consume_link_code(code, PHONE) is None
    assert user.whatsapp_phone_number is None
    assert user.whatsapp_link_code is None


def test_consume_link_code_moves_a_number_already_linked_to_another_account(db):
    first = _user(db, "zhongxuen")
    second = _user(db, "someone-else")
    service = WhatsAppLinkService(db)

    code, _ = service.generate_link_code(first.id)
    service.consume_link_code(code, PHONE)
    code, _ = service.generate_link_code(second.id)
    service.consume_link_code(code, PHONE)

    # The unique column means one number resolves to exactly one account -- re-pairing
    # moves it rather than blowing up on the constraint.
    assert service.get_by_phone_number(PHONE).id == second.id
    assert first.whatsapp_phone_number is None


# --- lookups / unlink ------------------------------------------------------------------


def test_get_by_phone_number_returns_none_for_an_unlinked_number(db):
    _user(db)
    assert WhatsAppLinkService(db).get_by_phone_number(PHONE) is None


def test_get_by_phone_number_normalizes_before_looking_up(db):
    user = _user(db)
    service = WhatsAppLinkService(db)
    code, _ = service.generate_link_code(user.id)
    service.consume_link_code(code, PHONE)

    assert service.get_by_phone_number("+60 12-345 6789").id == user.id


def test_unlink_clears_the_number_and_reports_whether_anything_changed(db):
    user = _user(db)
    service = WhatsAppLinkService(db)
    code, _ = service.generate_link_code(user.id)
    service.consume_link_code(code, PHONE)

    assert service.unlink(user.id) is True
    assert user.whatsapp_phone_number is None
    assert service.unlink(user.id) is False
