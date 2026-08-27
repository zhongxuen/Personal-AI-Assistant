"""
Assistant message route.

Thin HTTP wrapper around AssistantCore.handle -- all orchestration logic lives there
(§41 Rule 7), this just validates the request body, enforces the right trust boundary
for the request's platform, wires up dependencies, and returns the response.

Two independent, non-overlapping boundaries apply here, matching the two different
kinds of caller this one endpoint accepts (§34, file 12 prompt 1 -- see
docs/security.md):

  - `platform="desktop"` -- gated by `enforce_desktop_local_only` (§23, file 11
    prompt 3): must arrive from a loopback client, full stop. No bearer token is
    required or checked for this platform -- the desktop agent runs unauthenticated
    on the same machine as this backend, same as before this file existed, per this
    task's own instruction to keep that boundary separate rather than fold it into
    the new auth layer.
  - every other `platform` (web, discord, mobile, ...) -- gated by
    `get_optional_current_user`:
    a valid bearer token is required, and the request's `user_id` is overwritten with
    the authenticated user's own identity rather than trusting whatever the client put
    in the request body (a caller must not be able to claim to be a different user
    just by putting a different `user_id` in the JSON).

The dependency used for that second boundary is `get_optional_current_user`, not the
stricter `get_current_user` every other protected route uses -- this route can't just
attach `Depends(get_current_user)` to the route itself, since whether a token is even
*required* depends on the request body's `platform`, and FastAPI resolves dependencies
before deciding anything about the body. So the token is still resolved through the
normal `Depends` machinery (keeping it overridable in tests the same way as every other
dependency here), but the platform-conditional 401 decision is made in the route body
below.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_health_manager, get_optional_current_user, get_tool_registry
from app.api.local_only import enforce_desktop_local_only
from app.core.assistant import AssistantCore
from app.core.models import AssistantRequest, AssistantResponse
from app.database.database import get_db
from app.database.models import User
from app.llm.health import HealthManager
from app.tools.registry import ToolRegistry

router = APIRouter(tags=["assistant"])


@router.post("/assistant/message", response_model=AssistantResponse)
def post_message(
    request: AssistantRequest,
    http_request: Request,
    db: Session = Depends(get_db),
    registry: ToolRegistry = Depends(get_tool_registry),
    health_manager: HealthManager = Depends(get_health_manager),
    current_user: User | None = Depends(get_optional_current_user),
) -> AssistantResponse:
    # §23: this endpoint accepts any `platform`, including "desktop" -- reject a
    # claimed platform="desktop" that didn't actually arrive from this machine before
    # it ever reaches AssistantCore/ToolExecutor (which only re-check tool-level
    # platform *capability*, not request *origin*).
    enforce_desktop_local_only(http_request, request.platform)

    # §34: everything that isn't platform="desktop" is a remote/public caller and
    # must authenticate. `current_user` is already resolved (and any invalid/expired
    # token already rejected with 401) by `get_optional_current_user` above -- None
    # here means specifically "no token was supplied at all", which is only a 401 for
    # this platform branch, not for platform="desktop".
    #
    # This is an inequality check against "desktop", not membership in an allowlist
    # of known platforms -- `AssistantRequest.platform` (app/core/models.py) is a
    # plain `str`, so a not-yet-formalized platform like "mobile" already falls into
    # this branch today with zero route changes: it gets the loopback check skipped
    # (`enforce_desktop_local_only` is equally a `!= "desktop"` no-op) and lands here
    # requiring a bearer token, same as web/discord.
    if request.platform != "desktop":
        if current_user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        request = request.model_copy(update={"user_id": current_user.username})

    # The process-wide HealthManager (not a fresh one per request) so provider
    # cooldowns/consecutive-error state actually persists across requests -- see
    # app.api.dependencies' docstring -- and so app.api.routes.llm_usage's status
    # dashboard reflects what AIRouter is really acting on.
    core = AssistantCore(registry, db=db, health_manager=health_manager)
    return core.handle(request)
