"""
Attachment routes + storage (`app.api.routes.attachments`, `app.attachments.service`).

Covers the hardening the service docstring promises: the type allow-list is enforced
on content (not on the filename or the client's Content-Type), the size cap holds,
user-supplied filenames never reach a filesystem path (traversal attempts included),
and every lookup is scoped to the owner. Files go to a per-test temp directory via the
`get_attachment_settings` dependency override -- never the real `backend/data/`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user, get_optional_current_user
from app.api.routes.attachments import get_attachment_settings
from app.attachments.service import AttachmentService
from app.config.settings import Settings
from app.database.database import get_db
from app.database.models import Attachment, User
from main import app

PDF = b"%PDF-1.4\n%test pdf\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@pytest.fixture()
def store(tmp_path) -> Path:
    return tmp_path / "attachments"


@pytest.fixture()
def settings(store) -> Settings:
    return Settings(_env_file=None, attachments_dir=str(store), attachment_max_bytes=1024)


@pytest.fixture()
def users(test_db):
    session = test_db()
    alice = User(username="alice", password_hash="x")
    bob = User(username="bob", password_hash="x")
    session.add_all([alice, bob])
    session.commit()
    ids = {"alice": alice.id, "bob": bob.id}
    session.close()
    return ids


@pytest.fixture()
def as_user(test_db, settings, users):
    """`as_user("alice")` -> a TestClient authenticated as alice."""

    def override_get_db():
        db = test_db()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_attachment_settings] = lambda: settings

    def _client(username: str) -> TestClient:
        session = test_db()
        user = session.query(User).filter_by(username=username).one()
        session.expunge(user)
        session.close()
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_optional_current_user] = lambda: user
        return TestClient(app)

    yield _client
    app.dependency_overrides.clear()


def _upload(client: TestClient, filename: str, data: bytes, content_type: str = "application/octet-stream"):
    return client.post("/api/attachments", files={"file": (filename, data, content_type)})


def test_upload_stores_file_under_random_name_with_metadata_row(as_user, store, test_db):
    response = _upload(as_user("alice"), "Quarterly Report.pdf", PDF)

    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "Quarterly Report.pdf"
    assert body["mime_type"] == "application/pdf"
    assert body["size_bytes"] == len(PDF)
    assert len(body["sha256"]) == 64

    row = test_db().query(Attachment).one()
    assert row.path != "Quarterly Report.pdf"
    assert row.path.endswith(".pdf")
    stored = store / row.path
    assert stored.read_bytes() == PDF
    assert list(store.iterdir()) == [stored]


@pytest.mark.parametrize(
    ("filename", "data"),
    [
        ("notes.txt", b"plain text notes"),
        ("README.md", b"# Title\nbody"),
        ("photo.png", PNG),
        ("photo.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 8),
        ("anim.gif", b"GIF89a" + b"\x00" * 8),
        ("pic.webp", b"RIFF\x00\x00\x00\x00WEBPVP8 "),
    ],
)
def test_allowed_types_are_accepted(as_user, filename, data):
    assert _upload(as_user("alice"), filename, data).status_code == 201


@pytest.mark.parametrize(
    ("filename", "data", "content_type"),
    [
        ("malware.exe", b"MZ\x90\x00", "application/octet-stream"),
        ("script.js", b"alert(1)", "text/javascript"),
        ("page.html", b"<html></html>", "text/html"),
        ("noext", b"hello", "text/plain"),
        # Allowed extension, wrong contents: the bytes decide, not the name...
        ("fake.pdf", b"MZ\x90\x00 not a pdf", "application/pdf"),
        ("fake.png", PDF, "image/png"),
        # ...and not the client's declared Content-Type either.
        ("evil.exe", PDF, "application/pdf"),
        # Text must be text.
        ("binary.txt", b"abc\x00def", "text/plain"),
        ("latin1.txt", "caf\xe9".encode("latin-1"), "text/plain"),
    ],
)
def test_disallowed_or_mismatched_types_are_rejected(as_user, store, filename, data, content_type):
    response = _upload(as_user("alice"), filename, data, content_type)

    assert response.status_code == 415
    assert not store.exists() or list(store.iterdir()) == []


def test_oversized_upload_is_rejected_with_413(as_user, store):
    response = _upload(as_user("alice"), "big.txt", b"a" * 1025)

    assert response.status_code == 413
    assert not store.exists() or list(store.iterdir()) == []


def test_upload_at_exactly_the_limit_is_accepted(as_user):
    assert _upload(as_user("alice"), "edge.txt", b"a" * 1024).status_code == 201


def test_empty_upload_is_rejected(as_user):
    assert _upload(as_user("alice"), "empty.txt", b"").status_code == 422


@pytest.mark.parametrize(
    "filename",
    [
        "../../../../etc/passwd.txt",
        "..\\..\\windows\\system32\\evil.txt",
        "/absolute/path/notes.txt",
        "C:\\Users\\victim\\notes.txt",
        "sub/dir/notes.txt",
        "..",
    ],
)
def test_traversal_filenames_never_reach_the_filesystem(as_user, store, tmp_path, test_db, filename):
    response = _upload(as_user("alice"), filename, b"harmless")

    if response.status_code == 201:
        # Stored, but only ever inside the store, under a server-chosen name; the
        # display name is reduced to its last component.
        row = test_db().query(Attachment).one()
        assert "/" not in row.filename and "\\" not in row.filename and ".." not in row.path
        assert (store / row.path).resolve().parent == store.resolve()
    else:
        assert response.status_code == 415  # e.g. ".." has no allowed extension
    # Nothing was written anywhere outside the store.
    outside = [p for p in tmp_path.rglob("*") if p.is_file() and store not in p.parents]
    assert outside == []


def test_list_and_delete_are_scoped_to_the_owner(as_user, store):
    alice_id = _upload(as_user("alice"), "a.txt", b"alice's").json()["id"]
    bob_id = _upload(as_user("bob"), "b.txt", b"bob's").json()["id"]

    bob = as_user("bob")
    assert [a["id"] for a in bob.get("/api/attachments").json()] == [bob_id]
    # Someone else's attachment is indistinguishable from a missing one.
    assert bob.delete(f"/api/attachments/{alice_id}").status_code == 404

    alice = as_user("alice")
    assert alice.delete(f"/api/attachments/{alice_id}").status_code == 204
    assert alice.get("/api/attachments").json() == []
    assert alice.delete(f"/api/attachments/{alice_id}").status_code == 404
    assert len(list(store.iterdir())) == 1  # only bob's file is left


def test_delete_with_a_tampered_path_never_touches_files_outside_the_store(test_db, settings, users, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    session = test_db()
    row = Attachment(
        user_id=users["alice"], filename="x.txt", mime_type="text/plain", size_bytes=1, sha256="0" * 64,
        path="../victim.txt",
    )
    session.add(row)
    session.commit()

    assert AttachmentService(session, settings=settings).delete(users["alice"], row.id) is True
    assert victim.read_text() == "keep me"


def test_routes_require_authentication(test_db):
    app.dependency_overrides.clear()
    client = TestClient(app)
    assert client.get("/api/attachments").status_code == 401
    assert client.post("/api/attachments", files={"file": ("a.txt", b"x", "text/plain")}).status_code == 401
    assert client.delete("/api/attachments/1").status_code == 401


def test_load_for_llm_reads_only_the_owners_files(as_user, test_db, settings, users):
    alice_id = _upload(as_user("alice"), "a.md", b"# alice").json()["id"]
    bob_id = _upload(as_user("bob"), "b.md", b"# bob").json()["id"]

    loaded = AttachmentService(test_db(), settings=settings).load_for_llm("alice", [alice_id, bob_id, 9999])

    assert [(a.filename, a.mime_type, a.data) for a in loaded] == [("a.md", "text/markdown", b"# alice")]


def test_chat_rejects_attachment_ids_the_user_does_not_own(as_user):
    bob_id = _upload(as_user("bob"), "b.txt", b"bob's").json()["id"]
    alice = as_user("alice")

    response = alice.post(
        "/api/assistant/message",
        json={"user_id": "x", "platform": "web", "message": "summarize", "attachment_ids": [bob_id]},
    )

    assert response.status_code == 404
    assert str(bob_id) in response.json()["detail"]
