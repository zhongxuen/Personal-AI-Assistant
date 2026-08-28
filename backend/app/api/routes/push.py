"""
Web push subscription routes (browser notifications).

Thin HTTP wrappers around `PushSubscriptionService` (`app/push/service.py`) -- same
split as `app.api.routes.tasks` over `TaskService` (§41 Rule 7): these routes only
(1) validate the request shape, (2) call into the service, and (3) shape the response.
The upsert-vs-insert decision, the ownership check on delete, and everything else
about the `push_subscriptions` table lives in the service, not here.

Both routes are "non-desktop-local" in §34's sense -- the frontend that subscribes is
reachable over the web (file 12), not gated by `app.api.local_only`'s loopback check --
so both require a valid bearer token via the router-level `get_current_user`
dependency, the same boundary every other web-reachable router here uses. A
subscription is stored *against the authenticated user*, so there is no meaningful
unauthenticated version of either call: the endpoint identifies a browser, only the
token identifies who to deliver to. See docs/security.md.

Only the subscription *store* is here. Sending a notification to the stored endpoints
is a separate piece (see `app/push/__init__.py`), so nothing on this path touches the
VAPID private key -- `settings.vapid_public_key` reaching the browser is what makes a
subscription possible in the first place, and that half is meant to be public (see
`app/config/settings.py`).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.database.database import get_db
from app.database.models import User
from app.push.service import PushSubscriptionService

router = APIRouter(prefix="/push", tags=["push"], dependencies=[Depends(get_current_user)])


class SubscriptionKeys(BaseModel):
    """The nested `keys` object exactly as `PushSubscription.toJSON()` produces it in
    the browser, so the frontend can post its subscription through unchanged rather
    than flattening it first.
    """

    p256dh: str
    auth: str


class SubscriptionIn(BaseModel):
    endpoint: str
    keys: SubscriptionKeys
    # `toJSON()` also includes `expirationTime`; ignored rather than rejected, since
    # pydantic drops unknown fields by default and nothing here uses it (it is null in
    # every browser that ships push today).


class UnsubscribeIn(BaseModel):
    """DELETE with a request body rather than a query parameter: an endpoint is a long
    push-service URL, and this keeps the unsubscribe payload the same shape the
    frontend already holds from `subscribe`. `fetch()` allows a body on DELETE.
    """

    endpoint: str = Field(min_length=1)


class SubscriptionOut(BaseModel):
    id: int
    endpoint: str


@router.post("/subscribe", response_model=SubscriptionOut, status_code=201)
def subscribe(
    payload: SubscriptionIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SubscriptionOut:
    # Idempotent by endpoint (see PushSubscriptionService.subscribe's docstring) -- the
    # frontend calls this on every page load without tracking whether it has subscribed
    # before, so a repeat call updates the existing row instead of erroring or
    # duplicating. 201 either way: the caller's subscription exists and is current,
    # which is the only thing it needs to know.
    subscription = PushSubscriptionService(db).subscribe(
        user_id=current_user.id,
        endpoint=payload.endpoint,
        keys_p256dh=payload.keys.p256dh,
        keys_auth=payload.keys.auth,
    )
    return SubscriptionOut(id=subscription.id, endpoint=subscription.endpoint)


@router.delete("/subscribe", status_code=204, response_model=None)
def unsubscribe(
    payload: UnsubscribeIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    # 404 covers both "no such subscription" and "that endpoint belongs to another
    # user" -- the service scopes the delete to `current_user`, and this route doesn't
    # distinguish the two cases, so a caller can't probe for other users' endpoints
    # (same reasoning as the login route's single error message).
    if not PushSubscriptionService(db).unsubscribe(payload.endpoint, current_user.id):
        raise HTTPException(status_code=404, detail="No such push subscription.")
