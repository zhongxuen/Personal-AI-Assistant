"""
`app/auth/security.py` tests (§34, file 12 prompt 1).

Pure-function tests, no DB/FastAPI involved -- password hashing/verification and
JWT issuing/decoding in isolation. `AuthService` tests (tests/auth/test_service.py)
cover the DB-backed layer built on top of these.
"""

from __future__ import annotations

import jwt
import pytest

from app.auth.security import create_access_token, decode_access_token, hash_password, verify_password


# --- hash_password / verify_password --------------------------------------------------


def test_verify_password_accepts_the_correct_password():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed) is True


def test_verify_password_rejects_the_wrong_password():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("wrong password", hashed) is False


def test_hash_password_never_stores_plaintext():
    hashed = hash_password("hunter2")
    assert "hunter2" not in hashed


def test_hash_password_is_salted_so_repeats_differ():
    first = hash_password("same password")
    second = hash_password("same password")
    assert first != second
    # ... but both still verify against the original password.
    assert verify_password("same password", first) is True
    assert verify_password("same password", second) is True


@pytest.mark.parametrize(
    "malformed",
    [
        "",
        "not-a-hash-at-all",
        "pbkdf2_sha256$not-an-int$c2FsdA==$aGFzaA==",
        "bcrypt$260000$c2FsdA==$aGFzaA==",  # right shape, wrong algorithm tag
    ],
)
def test_verify_password_fails_closed_on_malformed_hash(malformed):
    assert verify_password("anything", malformed) is False


# --- create_access_token / decode_access_token -----------------------------------------


def test_decode_access_token_round_trips_the_issuing_claims():
    token = create_access_token(
        user_id=7, username="zhongxuen", secret_key="test-secret", algorithm="HS256", expires_minutes=60
    )
    payload = decode_access_token(token, secret_key="test-secret", algorithm="HS256")

    assert payload["sub"] == "7"
    assert payload["username"] == "zhongxuen"


def test_decode_access_token_rejects_wrong_secret():
    token = create_access_token(
        user_id=1, username="u", secret_key="secret-a", algorithm="HS256", expires_minutes=60
    )
    with pytest.raises(jwt.PyJWTError):
        decode_access_token(token, secret_key="secret-b", algorithm="HS256")


def test_decode_access_token_rejects_expired_token():
    # A negative expiry puts `exp` in the past at issue time -- already expired the
    # instant it's created, no need to sleep past a real deadline.
    token = create_access_token(
        user_id=1, username="u", secret_key="test-secret", algorithm="HS256", expires_minutes=-1
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token, secret_key="test-secret", algorithm="HS256")
