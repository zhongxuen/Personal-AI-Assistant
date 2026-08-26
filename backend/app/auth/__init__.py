"""
Authentication subsystem (§34, file 12 prompt 1).

`security.py` holds the two independent primitives (password hashing, JWT
issuing/verification) with no DB/FastAPI dependency of their own -- pure functions,
easy to unit test in isolation. `service.py`'s `AuthService` is the thin DB-backed
layer on top (look up a user, verify a password, seed the one bootstrap user) that
`app/api/routes/auth.py` and `main.py`'s startup seeding call into. Same split as
every other subsystem package here (`app/tasks`, `app/memory`, `app/routines`): a
service module wrapping `Session`, never a route importing SQLAlchemy models
directly (§41 Rule 7).
"""
