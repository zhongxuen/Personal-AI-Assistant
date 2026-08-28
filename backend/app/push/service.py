"""
PushSubscriptionService -- the DB-backed half of web push (browser notifications).

Thin wrapper around `app.database.models.PushSubscription`, same shape and same split
as `AuthService` (§41 Rule 7): `app/api/routes/push.py` calls into this, and never
touches `PushSubscription` rows itself. Takes an injected `Session` -- the caller owns
its lifecycle -- exactly like `AuthService`/`TaskService`/`MemoryService`.

Nothing here sends a notification or imports `pywebpush`; this is the subscription
*store* only. See this package's `__init__.py` on why delivery is deliberately a
separate piece.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.database.models import PushSubscription


class PushSubscriptionService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_endpoint(self, endpoint: str) -> PushSubscription | None:
        return (
            self.db.query(PushSubscription)
            .filter(PushSubscription.endpoint == endpoint)
            .first()
        )

    def list_for_user(self, user_id: int) -> list[PushSubscription]:
        """Every browser this user has subscribed from -- the list a delivery path
        fans out over (one row per browser, see `PushSubscription`'s docstring).
        """
        return (
            self.db.query(PushSubscription)
            .filter(PushSubscription.user_id == user_id)
            .order_by(PushSubscription.created_at.desc())
            .all()
        )

    def subscribe(
        self, user_id: int, endpoint: str, keys_p256dh: str, keys_auth: str
    ) -> PushSubscription:
        """Create the subscription, or update the existing row for this `endpoint`.

        Upsert rather than insert because a browser re-subscribing hands back the same
        endpoint with (possibly) rotated keys, and the frontend has no way to know
        whether it has subscribed before -- it just calls this on every page load.
        Inserting blindly would either violate the unique constraint or accumulate
        stale rows that all push to the same browser. The `user_id` is overwritten too,
        so a shared browser follows whoever is currently logged in rather than keeping
        delivering to the previous user.
        """
        existing = self.get_by_endpoint(endpoint)
        if existing is not None:
            existing.user_id = user_id
            existing.keys_p256dh = keys_p256dh
            existing.keys_auth = keys_auth
            self.db.commit()
            self.db.refresh(existing)
            return existing

        subscription = PushSubscription(
            user_id=user_id,
            endpoint=endpoint,
            keys_p256dh=keys_p256dh,
            keys_auth=keys_auth,
        )
        self.db.add(subscription)
        self.db.commit()
        self.db.refresh(subscription)
        return subscription

    def unsubscribe(self, endpoint: str, user_id: int) -> bool:
        """Delete this user's subscription for `endpoint`; returns whether a row was
        actually deleted. Scoped to `user_id` so one authenticated user can't
        unsubscribe another's browser by guessing an endpoint -- an endpoint belonging
        to someone else is reported the same as one that doesn't exist (False), never
        deleted.
        """
        subscription = self.get_by_endpoint(endpoint)
        if subscription is None or subscription.user_id != user_id:
            return False

        self.db.delete(subscription)
        self.db.commit()
        return True
