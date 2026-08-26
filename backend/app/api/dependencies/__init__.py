"""
Shared FastAPI dependencies.

`get_tool_registry` hands out the single process-wide ToolRegistry so every request
routes against the same set of registered tools. It's empty until tools register
themselves against it (starting file 03, md-files/03-basic-deterministic-tools.md) --
until then every message falls through to classification LLM_REQUIRED.

`get_health_manager` hands out a single process-wide `HealthManager` (§6, file 06) the
same way -- `AssistantCore` previously built a brand new one per request (each
`AssistantCore(...)` construction in `app.api.routes.assistant` implicitly created a
fresh `AIRouter`, which defaults to a fresh `HealthManager`), so a provider's tracked
cooldowns/consecutive-error counts never actually survived past the request that
recorded them. Routing every request through this one instance instead (see
`app.api.routes.assistant`) makes that health tracking real across requests, and lets
`app.api.routes.llm_usage` report the same live state `AIRouter` is actually acting on
-- rather than a second, always-fresh-and-therefore-always-AVAILABLE instance.

`get_stt_provider`/`get_tts_provider` (file 10) hand out single process-wide
`SpeechToTextProvider`/`TextToSpeechProvider` instances the same way -- `LocalWhisperSTT`
lazily loads its model on first `transcribe()` (see `app.voice.stt`), so reusing one
instance across requests means that load only ever happens once per process instead of
once per voice message.

`get_current_user`/`get_optional_current_user` (§34, file 12 prompt 1) are different in
kind from the four above -- per-request, not a process-wide singleton -- but live here
too since this module is where every route imports its FastAPI dependencies from.
`get_current_user` is the one auth gate for routes that unconditionally need a valid
bearer token: attach `Depends(get_current_user)` (or, for a whole router,
`APIRouter(dependencies=[Depends(get_current_user)])` as `app.api.routes.tasks` etc. do).
`get_optional_current_user` exists only for `app.api.routes.assistant.post_message`,
whose auth requirement is conditional on the request body's `platform` (decided only
after the body is parsed, so it can't be a required `Depends` on the route itself) --
it resolves the same token the same way but returns None instead of raising when no
token was supplied at all, leaving the platform-conditional 401 decision to the route.
Neither one covers the desktop-only local endpoints from file 11
(`app.api.local_only`'s `enforce_desktop_local_only`) -- those stay on their own
separate loopback-based boundary. See docs/security.md.
"""

from __future__ import annotations

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.auth.security import decode_access_token
from app.config.settings import get_settings
from app.database.database import get_db
from app.database.models import User
from app.llm.health import HealthManager
from app.tools.registry import ToolRegistry
from app.voice.stt import LocalWhisperSTT, SpeechToTextProvider
from app.voice.tts import LocalPyttsx3TTS, TextToSpeechProvider

_registry = ToolRegistry()
_health_manager = HealthManager()
_stt_provider: SpeechToTextProvider = LocalWhisperSTT()
_tts_provider: TextToSpeechProvider = LocalPyttsx3TTS()

# auto_error=False so a missing token falls through to the same
# `_UNAUTHENTICATED` HTTPException every other failure mode below raises, rather than
# OAuth2PasswordBearer's own generic 401 -- one consistent error shape regardless of
# *why* authentication failed (no header, malformed token, expired token, deleted
# user). `tokenUrl` only affects the OpenAPI docs' "Authorize" button, not verification.
_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def get_tool_registry() -> ToolRegistry:
    return _registry


def get_health_manager() -> HealthManager:
    return _health_manager


def get_stt_provider() -> SpeechToTextProvider:
    return _stt_provider


def get_tts_provider() -> TextToSpeechProvider:
    return _tts_provider


def _unauthenticated() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _resolve_user(token: str, db: Session) -> User:
    """Shared token -> `User` resolution for both dependencies below. Never trusts the
    token's `username` claim for lookup -- always re-fetches by `sub` (the user id) so
    a renamed/deleted user is reflected immediately rather than only once the old
    token expires. Raises 401 for any invalid/expired token or a since-deleted user.
    """
    settings = get_settings()
    try:
        payload = decode_access_token(
            token, secret_key=settings.auth_secret_key, algorithm=settings.auth_algorithm
        )
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise _unauthenticated() from exc

    user = db.get(User, user_id)
    if user is None:
        raise _unauthenticated()
    return user


def get_current_user(
    token: str | None = Depends(_oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the bearer token on the request to its `User` row, or raise 401.
    Attach via `Depends` to any route (or `APIRouter(dependencies=[...])`) that
    unconditionally requires authentication.
    """
    if not token:
        raise _unauthenticated()
    return _resolve_user(token, db)


def get_optional_current_user(
    token: str | None = Depends(_oauth2_scheme),
    db: Session = Depends(get_db),
) -> User | None:
    """Same resolution as `get_current_user`, but returns None instead of raising when
    no token was supplied at all -- see this module's docstring on why
    `app.api.routes.assistant.post_message` is the only caller. A token that *is*
    present but invalid/expired/for a deleted user still raises 401 here, same as
    `get_current_user` -- only a genuinely absent token is treated as "not
    authenticated yet" rather than "authentication failed".
    """
    if not token:
        return None
    return _resolve_user(token, db)
