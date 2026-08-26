"""
`AuthService` tests (§34, file 12 prompt 1).

Exercises `AuthService` directly against a throwaway in-memory SQLite session (same
pattern as tests/tasks/test_task_service.py), not through HTTP -- the HTTP contract
(`POST /api/auth/login`, protected routes rejecting/accepting) is covered separately
by tests/api/test_auth.py.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.security import verify_password
from app.auth.service import AuthService
from app.database import models  # noqa: F401 -- registers tables on Base.metadata
from app.database.database import Base


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = session_factory()
    yield session
    session.close()
    engine.dispose()


# --- create_user -------------------------------------------------------------------


def test_create_user_hashes_the_password(db):
    user = AuthService(db).create_user("zhongxuen", "hunter2")

    assert user.id is not None
    assert user.username == "zhongxuen"
    assert user.password_hash != "hunter2"
    assert verify_password("hunter2", user.password_hash) is True


def test_create_user_rejects_duplicate_username(db):
    AuthService(db).create_user("zhongxuen", "hunter2")

    with pytest.raises(ValueError):
        AuthService(db).create_user("zhongxuen", "a different password")


# --- authenticate --------------------------------------------------------------------


def test_authenticate_returns_the_user_for_correct_credentials(db):
    created = AuthService(db).create_user("zhongxuen", "hunter2")

    authenticated = AuthService(db).authenticate("zhongxuen", "hunter2")

    assert authenticated is not None
    assert authenticated.id == created.id


def test_authenticate_returns_none_for_wrong_password(db):
    AuthService(db).create_user("zhongxuen", "hunter2")

    assert AuthService(db).authenticate("zhongxuen", "wrong password") is None


def test_authenticate_returns_none_for_unknown_username(db):
    assert AuthService(db).authenticate("nobody", "anything") is None


# --- seed_default_user ----------------------------------------------------------------


def test_seed_default_user_creates_when_both_configured_and_absent(db):
    user = AuthService(db).seed_default_user("zhongxuen", "hunter2")

    assert user is not None
    assert user.username == "zhongxuen"
    assert AuthService(db).authenticate("zhongxuen", "hunter2") is not None


def test_seed_default_user_is_a_noop_when_username_already_exists(db):
    original = AuthService(db).create_user("zhongxuen", "original-password")

    result = AuthService(db).seed_default_user("zhongxuen", "a-different-password")

    assert result is None
    # The original password still works -- seeding never overwrites an existing user.
    assert AuthService(db).authenticate("zhongxuen", "original-password") is not None
    assert AuthService(db).authenticate("zhongxuen", "a-different-password") is None
    assert original.id == AuthService(db).get_by_username("zhongxuen").id


@pytest.mark.parametrize(
    "username,password",
    [(None, "hunter2"), ("zhongxuen", None), (None, None), ("", "hunter2"), ("zhongxuen", "")],
)
def test_seed_default_user_is_a_noop_when_either_is_unset(db, username, password):
    assert AuthService(db).seed_default_user(username, password) is None
    assert AuthService(db).get_by_username("zhongxuen") is None
