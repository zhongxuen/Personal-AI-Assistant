"""
Auth route (§34, file 12 prompt 1).

`POST /api/auth/login` is the one public entrypoint into this subsystem -- it's the
`User -> Authentication` step of §34's diagram, everything after it
(`-> Authorized Assistant API`) is `app.api.dependencies.get_current_user` gating the
routes that need a token. Thin wrapper around `AuthService`/`create_access_token`
(§41 Rule 7): this route validates the request shape and shapes the response, nothing
else.

Uses `OAuth2PasswordRequestForm` (form-encoded `username`/`password`, not JSON) --
the standard FastAPI convention for a login route, which is also what makes the
generated OpenAPI docs' "Authorize" button work out of the box. `python-multipart` is
already a dependency (file 10's audio upload), so this adds no new one.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.security import create_access_token
from app.auth.service import AuthService
from app.config.settings import get_settings
from app.database.database import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    username: str


@router.post("/login", response_model=TokenResponse)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> TokenResponse:
    user = AuthService(db).authenticate(form_data.username, form_data.password)
    if user is None:
        # Same message regardless of whether the username doesn't exist or the
        # password is wrong (see AuthService.authenticate's docstring) -- must not
        # let a caller enumerate valid usernames.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    settings = get_settings()
    token = create_access_token(
        user_id=user.id,
        username=user.username,
        secret_key=settings.auth_secret_key,
        algorithm=settings.auth_algorithm,
        expires_minutes=settings.auth_token_expire_minutes,
    )
    return TokenResponse(access_token=token, user_id=user.id, username=user.username)
