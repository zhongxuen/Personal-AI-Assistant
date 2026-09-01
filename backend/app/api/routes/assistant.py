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

import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.dependencies import get_health_manager, get_optional_current_user, get_tool_registry
from app.api.local_only import enforce_desktop_local_only
from app.core.assistant import AssistantCore
from app.core.models import AssistantRequest, AssistantResponse, AssistantStreamEvent
from app.database.database import get_db
from app.database.models import User
from app.llm.health import HealthManager
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["assistant"])


def _authorize(
    request: AssistantRequest,
    http_request: Request,
    current_user: User | None,
) -> AssistantRequest:
    """The two trust boundaries described in this module's docstring, applied in order,
    returning the request with its `user_id` pinned to the authenticated identity for
    every non-desktop platform.

    Shared verbatim by both routes below -- the streaming endpoint is the same endpoint
    with a different response encoding, so it must not get a weaker (or merely
    different) boundary than the JSON one.
    """
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

    return request


@router.post("/assistant/message", response_model=AssistantResponse)
async def post_message(
    request: AssistantRequest,
    http_request: Request,
    db: Session = Depends(get_db),
    registry: ToolRegistry = Depends(get_tool_registry),
    health_manager: HealthManager = Depends(get_health_manager),
    current_user: User | None = Depends(get_optional_current_user),
) -> AssistantResponse:
    request = _authorize(request, http_request, current_user)

    # The process-wide HealthManager (not a fresh one per request) so provider
    # cooldowns/consecutive-error state actually persists across requests -- see
    # app.api.dependencies' docstring -- and so app.api.routes.llm_usage's status
    # dashboard reflects what AIRouter is really acting on.
    core = AssistantCore(registry, db=db, health_manager=health_manager)
    # `async def` + `handle_async` deliberately, not the sync `def` + `handle` this
    # route used to be: `handle()` reaches the LLM via `asyncio.run()`, and an HTTP
    # client's pooled connections die with the loop that opened them, so every message
    # re-paid a full TLS handshake to the provider. Awaiting on uvicorn's own long-lived
    # loop lets `app.llm.clients` keep the connection warm across requests. Blocking
    # work inside (tool handlers, DB) is offloaded to threads by AssistantCore itself.
    return await core.handle_async(request)


@router.post("/assistant/stream")
async def post_message_stream(
    request: AssistantRequest,
    http_request: Request,
    db: Session = Depends(get_db),
    registry: ToolRegistry = Depends(get_tool_registry),
    health_manager: HealthManager = Depends(get_health_manager),
    current_user: User | None = Depends(get_optional_current_user),
) -> StreamingResponse:
    """Server-Sent Events form of `/assistant/message` -- same body, same auth, same
    orchestration, same final answer; the reply just arrives in pieces.

    This is the single biggest perceived-latency win available: on the JSON endpoint the
    user sees nothing at all until generation finishes, so time-to-first-character is
    the whole turn. Here the first token lands as soon as the model emits it and the
    rest streams in behind it.

    Deliberately a *second* route rather than a replacement. Discord, WhatsApp, the
    desktop agent and the mobile client all consume a single JSON body and have no use
    for partial output; keeping `/assistant/message` exactly as it was means adding
    streaming for the web chat costs those platforms nothing and breaks no existing
    client (§41 Rule 7 -- one orchestrator, `AssistantCore`, rendered two ways).

    Each event is one `AssistantStreamEvent` as JSON on a `data:` line. Consumers should
    treat the `"done"` event's `response` as authoritative and everything before it as a
    preview -- see `AssistantStreamEvent` for the event taxonomy and why there is no
    separate error event.
    """
    request = _authorize(request, http_request, current_user)
    core = AssistantCore(registry, db=db, health_manager=health_manager)

    async def events() -> AsyncIterator[str]:
        try:
            async for event in core.handle_stream(request):
                yield f"data: {event.model_dump_json()}\n\n"
        except Exception:  # noqa: BLE001 -- a stream must not die with a bare socket close
            # The response status is long since sent by the time anything in here can
            # fail, so an exception can't become a 500 -- without this the client just
            # sees the connection drop and has no idea whether the turn happened. Emit a
            # well-formed terminal event instead, so the UI can report it like any other
            # failed turn (§41 Rule 3).
            logger.exception("Assistant stream failed mid-flight.")
            failure = AssistantStreamEvent(
                type="done",
                response=AssistantResponse(
                    text="Something went wrong while generating that reply. Please try again.",
                    used_llm=False,
                ),
            )
            yield f"data: {failure.model_dump_json()}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Nginx and several PaaS proxies (Render included) buffer responses by
            # default, which would hold every chunk back until the stream closed and
            # silently undo the entire point of this route.
            "X-Accel-Buffering": "no",
        },
    )
