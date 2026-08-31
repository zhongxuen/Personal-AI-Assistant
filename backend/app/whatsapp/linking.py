"""
WhatsAppLinkService -- phone-number-to-user pairing (§37 Phase 13, file 18 prompt 1).

Thin wrapper around the WhatsApp columns on `app.database.models.User`, same shape and
same split as `AuthService`/`PushSubscriptionService` (§41 Rule 7): routes call into
this, and never touch `User` rows or code generation themselves. Takes an injected
`Session` -- the caller owns its lifecycle -- exactly like every other service here.

The flow, end to end:

  1. A logged-in user calls `POST /api/whatsapp/link-code` (`app/api/routes/whatsapp.py`)
     -> `generate_link_code()` stores a fresh single-use code and its expiry on their
     own row and returns it to them.
  2. They send any WhatsApp message containing that code to the number, once.
  3. The webhook handler (file 18 prompt 2) calls `get_by_phone_number()` first; on a
     miss it calls `consume_link_code()` with the raw message text, which matches the
     code, stores the sender's number on that `User`, and clears the code.
  4. Every later message from that number resolves through `get_by_phone_number()` with
     no code involved.

An unknown number that carries no valid code gets `UNLINKED_REPLY` and nothing else --
no tool runs, no user is created. See this package's `__init__.py` on why.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.database.models import User

# Ambiguous glyphs (I/L/O/0/1) are left out: this code gets read off a screen and typed
# into a phone by hand, and "was that an O or a zero?" is the failure mode that costs a
# retry. 8 characters of this 31-symbol alphabet is ~40 bits -- far beyond guessable
# within the few minutes a code is alive, while still short enough to type.
LINK_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
LINK_CODE_LENGTH = 8

# Short on purpose: the code is a bearer credential for "this WhatsApp number is that
# account", and the legitimate flow (copy it, open WhatsApp, send it) takes well under a
# minute. Not a `Settings` field -- it's a security property of this flow, not
# deployment-varying config like a model name or a quota budget.
LINK_CODE_TTL_MINUTES = 15

# Matched against the *uppercased* message so a user who types their code in lower case
# still links. Bounded by \b so a code pasted mid-sentence ("my code is ABCD2345, thanks")
# still matches, without matching a longer run of characters that merely contains one.
_LINK_CODE_PATTERN = re.compile(rf"\b[{LINK_CODE_ALPHABET}]{{{LINK_CODE_LENGTH}}}\b")

# Everything except digits is stripped from an inbound number before it is stored or
# looked up: Meta's payloads carry a `wa_id` in digits-only form (country code, no "+"),
# but humans and other Meta fields write the same number as "+60 12-345 6789". Both must
# resolve to one row, and the unique constraint on `User.whatsapp_phone_number` can only
# enforce that if there's exactly one spelling in the column.
_NON_DIGITS = re.compile(r"\D")

# The reply an unrecognised number gets. Deliberately says how to link and nothing else:
# no account list, no hint about whether any account exists, and no way to make the
# assistant do anything (`app/core/assistant.py` is never reached for an unlinked
# sender). Kept here rather than in the webhook route so the route, the capability test
# (file 18 prompt 3), and any future channel all assert on one string.
UNLINKED_REPLY = (
    "This number isn't linked to an account yet, so I can't act on messages from it. "
    "Sign in to JARVIS on the web, generate a WhatsApp pairing code from Settings, and "
    "send it here as a message to link this number."
)

# The reply the pairing message itself gets, on the one message that consumes a code.
# That message is a code, not a command -- running "ABCD2345" through
# `app/core/assistant.py` would produce a confused answer to something the user never
# asked, so the webhook (`app/api/routes/whatsapp_webhook.py`) answers it here and stops.
# Lives beside `UNLINKED_REPLY` for the same reason: one string, asserted on by the route
# and by the capability test rather than duplicated in each.
LINKED_REPLY = (
    "This number is now linked to your JARVIS account. Just message me normally from "
    "here -- ask about your tasks, set a reminder, and so on."
)


def normalize_phone_number(phone_number: str) -> str:
    """Digits-only form of `phone_number` -- the one spelling stored and queried."""
    return _NON_DIGITS.sub("", phone_number)


def extract_link_code(message: str) -> str | None:
    """The first thing in `message` shaped like a pairing code, uppercased, or None.

    "Any message *containing* the code" is the promise made to the user, so this scans
    rather than requiring the message to be exactly the code. Shape-matching here is
    only a filter -- `consume_link_code` still has to find that exact string stored,
    unexpired, on a real row before anything is linked.
    """
    match = _LINK_CODE_PATTERN.search(message.upper())
    return match.group(0) if match else None


class WhatsAppLinkService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_phone_number(self, phone_number: str) -> User | None:
        """The `User` this WhatsApp number belongs to, or None if it isn't linked.

        The webhook's first call on every inbound message, and the reason
        `User.whatsapp_phone_number` is unique -- a number resolves to at most one
        account, never to "the first of several matches".
        """
        normalized = normalize_phone_number(phone_number)
        if not normalized:
            return None
        return (
            self.db.query(User).filter(User.whatsapp_phone_number == normalized).first()
        )

    def generate_link_code(self, user_id: int) -> tuple[str, datetime]:
        """Issue a fresh single-use pairing code for `user_id`; returns (code, expiry).

        Replaces any code already outstanding for this user rather than keeping both
        alive -- a user who lost the first code (or waited past its expiry) just asks
        again, and only the newest one works. Raises ValueError for an unknown
        `user_id`, mirroring `AuthService.create_user`'s convention of raising rather
        than letting a database-level error escape.
        """
        user = self.db.get(User, user_id)
        if user is None:
            raise ValueError(f"No user with id {user_id}.")

        code = self._unused_code()
        user.whatsapp_link_code = code
        user.whatsapp_link_code_expires_at = datetime.now() + timedelta(
            minutes=LINK_CODE_TTL_MINUTES
        )
        self.db.commit()
        self.db.refresh(user)
        return code, user.whatsapp_link_code_expires_at

    def consume_link_code(self, message: str, phone_number: str) -> User | None:
        """Link `phone_number` to whoever owns the code inside `message`.

        Returns the now-linked `User`, or None if the message carries no code-shaped
        string, the code matches nobody, or it has expired -- all three are the same
        answer to the caller ("this number is still unlinked"), and the webhook replies
        with `UNLINKED_REPLY` in every case rather than saying which it was. An expired
        code is cleared as it's rejected, so a stale value can't sit on the row.

        Single use: the code is cleared on success too, so replaying the same message
        from a different number links nothing.
        """
        code = extract_link_code(message)
        normalized = normalize_phone_number(phone_number)
        if code is None or not normalized:
            return None

        user = self.db.query(User).filter(User.whatsapp_link_code == code).first()
        if user is None:
            return None

        if (
            user.whatsapp_link_code_expires_at is None
            or user.whatsapp_link_code_expires_at < datetime.now()
        ):
            self._clear_code(user)
            self.db.commit()
            return None

        # A number can only be on one row (unique column), so re-pairing a number that
        # some other account still holds *moves* it rather than failing with an
        # IntegrityError. The other account keeps working over web/desktop; it just
        # stops being reachable over WhatsApp, which is exactly what "I linked this
        # number to my other account" should mean. The old owner's own pairing code, if
        # any, is left alone -- it's theirs to use.
        previous_owner = self.get_by_phone_number(normalized)
        if previous_owner is not None and previous_owner.id != user.id:
            previous_owner.whatsapp_phone_number = None
            self.db.flush()

        user.whatsapp_phone_number = normalized
        self._clear_code(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def unlink(self, user_id: int) -> bool:
        """Drop this user's linked number and any outstanding code; returns whether
        anything was actually cleared. Scoped to one `user_id` -- there is no call
        anywhere that unlinks a number by number, so one user can never unlink
        another's by guessing it (same reasoning as
        `PushSubscriptionService.unsubscribe`'s ownership check).
        """
        user = self.db.get(User, user_id)
        if user is None:
            return False
        if user.whatsapp_phone_number is None and user.whatsapp_link_code is None:
            return False

        user.whatsapp_phone_number = None
        self._clear_code(user)
        self.db.commit()
        return True

    def _unused_code(self) -> str:
        """A code no other row currently holds.

        A collision is vanishingly unlikely at ~40 bits over a handful of live codes,
        but `User.whatsapp_link_code` isn't unique at the schema level (an expired code
        may linger until its owner is next touched), so a duplicate would silently make
        `consume_link_code`'s lookup ambiguous. Cheaper to check than to reason about.
        """
        for _ in range(10):
            code = "".join(
                secrets.choice(LINK_CODE_ALPHABET) for _ in range(LINK_CODE_LENGTH)
            )
            if (
                self.db.query(User).filter(User.whatsapp_link_code == code).first()
                is None
            ):
                return code
        raise RuntimeError("Could not generate an unused WhatsApp pairing code.")

    @staticmethod
    def _clear_code(user: User) -> None:
        user.whatsapp_link_code = None
        user.whatsapp_link_code_expires_at = None
