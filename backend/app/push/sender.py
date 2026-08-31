"""
`WebPushSender` -- the delivery half of web push, the piece this package's
`__init__.py` said would land "alongside" the subscription store once something
actually needed to send (§41 Rule 1). `ReminderScheduler` (file 17 task 4) is that
caller.

Split deliberately from `PushSubscriptionService`: that one owns the
`push_subscriptions` *table*, this one owns *delivery* (VAPID signing, payload
encryption, one POST per endpoint) and reads the table only through the service, never
by querying `PushSubscription` itself -- the same service-not-model discipline
`app/api/routes/push.py` follows (§41 Rule 7). It is also the only module in the
project that imports `pywebpush` or reads `settings.vapid_private_key`, so the private
half of the VAPID pair never spreads past this file (docs/security.md).

Two hard rules, because the first caller is a background poll loop that must survive
anything this does (`app/tasks/scheduler.py`):

  * `send_to_user` never raises. Every failure -- unconfigured VAPID, an endpoint that
    404s/410s because the browser unsubscribed, a push service timeout, a malformed
    row -- is logged and skipped, per-subscription, so one dead device can't stop the
    others or the caller's own work.
  * It never blocks a channel that already fired. The caller sends its desktop toast
    first and calls this after; nothing here can un-send that toast.

An endpoint that comes back 404/410 is *gone* permanently (the browser dropped the
subscription), and every future send to it will fail the same way. Pruning that row is
the obvious follow-up, but it is deliberately not done here: it isn't needed for
correct delivery, nothing has asked for it, and a delivery path silently deleting user
rows is a bigger decision than this file should make on its own. Logged distinctly
(`expired`, not `failed`) so the case is visible if it ever matters.
"""

from __future__ import annotations

import json
import logging

from pywebpush import WebPushException, webpush
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.push.service import PushSubscriptionService

logger = logging.getLogger("jarvis.push")

# Push services drop a subscription permanently on these; anything else (429, 5xx, a
# timeout) is transient and the same endpoint may well work on the next reminder.
_GONE_STATUS_CODES = frozenset({404, 410})

# Push services cap an encrypted payload at ~4KB. Reminder text is a task title, so
# this only ever trips on absurd input -- truncating beats the whole send failing.
_MAX_BODY_CHARS = 500

# Seconds a push service should hold the message for a device that's currently
# offline. A reminder is time-relevant, so a few hours rather than the spec's
# "deliver whenever": a task reminder surfacing a day late is noise, not help.
_TTL_SECONDS = 6 * 60 * 60


class WebPushSender:
    """Fans one notification out to every browser a user has subscribed from.

    Takes an injected `Session` whose lifecycle the caller owns, same as
    `PushSubscriptionService`/`AuthService`/`TaskService`.
    """

    def __init__(self, db: Session) -> None:
        self._subscriptions = PushSubscriptionService(db)

    @property
    def configured(self) -> bool:
        """Whether both halves of the VAPID pair are set.

        Unset -> the whole feature no-ops rather than erroring, the same "absence is a
        valid, non-crashing state" convention `settings.py` documents for
        `discord_bot_token`/`gemini_api_key`. A dev machine with no keys configured
        keeps its desktop toasts and simply never pushes.
        """
        settings = get_settings()
        return bool(settings.vapid_private_key and settings.vapid_public_key)

    def send_to_user(self, user_id: int | None, title: str, body: str) -> int:
        """Push `title`/`body` to each of `user_id`'s subscribed browsers.

        Returns how many were accepted by their push service -- for logging and tests,
        not for control flow; callers are expected to carry on regardless, including
        when this returns 0. Never raises.
        """
        if user_id is None:
            # Pre-auth rows (every user-owned table here is nullable `user_id`, see
            # models.py) have nobody to deliver to. Not an error -- the desktop toast
            # already covered them, which is exactly how it worked before push existed.
            return 0

        if not self.configured:
            logger.debug("Web push skipped: VAPID keys not configured.")
            return 0

        try:
            subscriptions = self._subscriptions.list_for_user(user_id)
        except Exception:
            logger.exception("Web push skipped: could not load subscriptions for user %s.", user_id)
            return 0

        if not subscriptions:
            return 0

        payload = json.dumps(
            {
                "title": title,
                "body": body[:_MAX_BODY_CHARS],
                # Where the service worker's notificationclick should land. The app
                # shell is a SPA, so the route is enough.
                "url": "/tasks",
            }
        )

        delivered = 0
        for subscription in subscriptions:
            if self._send_one(subscription, payload):
                delivered += 1

        logger.info(
            "Web push fan-out for user %s: %s/%s subscription(s) delivered.",
            user_id,
            delivered,
            len(subscriptions),
        )
        return delivered

    def _send_one(self, subscription, payload: str) -> bool:
        """One endpoint, one POST. Swallows and logs every failure -- see module
        docstring; a per-subscription failure must not end the fan-out.
        """
        settings = get_settings()
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": {
                        "p256dh": subscription.keys_p256dh,
                        "auth": subscription.keys_auth,
                    },
                },
                data=payload,
                vapid_private_key=settings.vapid_private_key,
                # `aud` and `exp` are filled in by pywebpush from the endpoint/clock;
                # `sub` is the one claim the spec makes us supply.
                vapid_claims={"sub": settings.vapid_subject},
                ttl=_TTL_SECONDS,
            )
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in _GONE_STATUS_CODES:
                logger.info(
                    "Web push subscription %s expired (HTTP %s); skipped.",
                    subscription.id,
                    status,
                )
            else:
                logger.warning(
                    "Web push to subscription %s failed (HTTP %s): %s",
                    subscription.id,
                    status,
                    exc,
                )
            return False
        except Exception:
            # Anything pywebpush didn't wrap -- a socket timeout, a bad key that fails
            # during signing. Same treatment: this endpoint is skipped, the rest go on.
            logger.exception("Web push to subscription %s raised unexpectedly.", subscription.id)
            return False

        return True
