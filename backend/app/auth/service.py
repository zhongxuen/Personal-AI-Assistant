"""
AuthService -- the DB-backed half of authentication (§34, file 12 prompt 1).

Thin wrapper around `app.database.models.User` and `app/auth/security.py`'s pure
functions, same split as `TaskService`/`MemoryService` (§41 Rule 7): routes and
startup code call into this, never touch `User` rows or hashing/JWT primitives
directly.

Deliberately no public "register" method exposed over HTTP -- §34's brief is a single
personal user today, structured so more users need no architecture change later, not
an open self-service signup surface on a personal assistant. `create_user` exists here
for that later multi-user admin path (or a one-off script/shell), and
`seed_default_user` is what actually provisions the first user today, called once from
`main.py`'s startup if `AUTH_SEED_USERNAME`/`AUTH_SEED_PASSWORD` are configured.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.auth.security import hash_password, verify_password
from app.database.models import User


class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_username(self, username: str) -> User | None:
        return self.db.query(User).filter(User.username == username).first()

    def authenticate(self, username: str, password: str) -> User | None:
        """Returns the matching `User` if `username`/`password` are valid, else None.
        A missing user and a wrong password both just return None -- the login route
        must not let a caller distinguish "no such user" from "wrong password" (that
        would let it enumerate valid usernames).
        """
        user = self.get_by_username(username)
        if user is None or not user.password_hash:
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user

    def create_user(self, username: str, password: str) -> User:
        """Create a new user with a hashed password. Raises ValueError if `username`
        is already taken (mirrors `RoutineRegistry.create_routine`'s convention of
        raising ValueError on a request-shape conflict rather than letting a raw
        IntegrityError escape to the caller).
        """
        if self.get_by_username(username) is not None:
            raise ValueError(f"Username '{username}' is already taken.")

        user = User(username=username, password_hash=hash_password(password))
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def seed_default_user(self, username: str | None, password: str | None) -> User | None:
        """Idempotent startup bootstrap for the single personal user (§34): if
        `username`/`password` are both set and no user exists yet with that username,
        create it. No-op (returns None) if either is unset, or if that username
        already exists -- safe to call on every startup, matches
        `register_default_tools`'s "coding" routine seeding convention in spirit
        (create-if-missing, never overwrite).
        """
        if not username or not password:
            return None

        existing = self.get_by_username(username)
        if existing is not None:
            return None

        return self.create_user(username, password)
