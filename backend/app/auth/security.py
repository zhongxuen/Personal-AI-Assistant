"""
Password hashing and JWT issuing/verification (§34, file 12 prompt 1).

Two independent primitives, both pure functions with no DB/FastAPI dependency, so
`app/auth/service.py` and tests can exercise either without a database session:

  - `hash_password`/`verify_password` -- PBKDF2-HMAC-SHA256 via the stdlib `hashlib`,
    not bcrypt/passlib. Deliberate: this project already avoids adding a dependency
    where the stdlib does the job (see `app/tools/terminal.py`'s docstring on
    minimalism), and PBKDF2-HMAC-SHA256 at a modern iteration count is still a
    currently-recommended choice (it's Django's own default). The output is a single
    self-describing string -- `"pbkdf2_sha256$<iterations>$<salt-b64>$<hash-b64>"` --
    so the iteration count can be raised later for new hashes without breaking
    verification of ones already stored with the old count.
  - `create_access_token`/`decode_access_token` -- thin wrappers around PyJWT
    (HS256, stdlib `hmac`/`hashlib` under the hood -- no extra crypto backend
    dependency). `decode_access_token` re-raises PyJWT's own exceptions
    (`jwt.ExpiredSignatureError`, `jwt.InvalidTokenError`) rather than swallowing
    them -- `app/api/dependencies.get_current_user` is the one place that turns those
    into an HTTP 401, so callers get the honest reason if they need it, and the
    401-mapping logic itself lives in exactly one place (§41 Rule 7).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt

_PBKDF2_ALGORITHM = "sha256"
_PBKDF2_ITERATIONS = 260_000
_PBKDF2_SALT_BYTES = 16


def hash_password(password: str) -> str:
    """Hash `password` for storage in `User.password_hash`. Never returns or logs the
    plaintext; a fresh random salt is generated per call, so hashing the same password
    twice produces two different (both valid) hashes.
    """
    salt = os.urandom(_PBKDF2_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(_PBKDF2_ALGORITHM, password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return (
        f"pbkdf2_sha256${_PBKDF2_ITERATIONS}$"
        f"{base64.b64encode(salt).decode('ascii')}${base64.b64encode(derived).decode('ascii')}"
    )


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time check of `password` against a hash produced by `hash_password`.
    Returns False (never raises) for a malformed/unrecognized hash -- e.g. a user row
    whose `password_hash` is still None -- so a broken/missing hash simply fails to
    authenticate instead of 500ing the login route.
    """
    try:
        algorithm, iterations_str, salt_b64, hash_b64 = password_hash.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_str)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError, AttributeError):
        return False

    derived = hashlib.pbkdf2_hmac(_PBKDF2_ALGORITHM, password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(derived, expected)


def create_access_token(
    *, user_id: int, username: str, secret_key: str, algorithm: str, expires_minutes: int
) -> str:
    """Issue a signed JWT identifying `user_id`/`username`, expiring `expires_minutes`
    from now. `sub` is the user's id (a stable integer, unlike username which a future
    multi-user admin surface might allow renaming) -- `get_current_user` looks the user
    back up by this id on every request, so the token itself never needs to be
    revocable/re-issued just because a username changed.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
    }
    return jwt.encode(payload, secret_key, algorithm=algorithm)


def decode_access_token(token: str, *, secret_key: str, algorithm: str) -> dict:
    """Decode and verify `token`. Raises `jwt.PyJWTError` (or a subclass, e.g.
    `jwt.ExpiredSignatureError`) on any invalid/expired/tampered token -- callers that
    just want a yes/no should catch `jwt.PyJWTError`.
    """
    return jwt.decode(token, secret_key, algorithms=[algorithm])
