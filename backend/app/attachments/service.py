"""
Attachment storage -- local disk + one `attachments` row per file.

Files a user attaches to a chat message are written under `settings.attachments_path`
(default `backend/data/attachments/`, gitignored) on the machine running this backend,
and nowhere else: nothing is uploaded to a cloud store or to a provider's file API.
When a turn references an attachment, `AssistantCore` reads it back through
`load_for_llm` and the provider sends it inline for that one request
(`app.llm.openai._attachment_part`).

Hardening, since this is the one place the backend writes user-supplied bytes to disk:

  - **Type allow-list by content, not by claim.** The extension picks the expected
    type (pdf, txt, md, png, jpeg, gif, webp); the bytes must then actually match it
    (magic-number check for binaries, valid UTF-8 without NULs for text). The client's
    declared Content-Type is ignored.
  - **Size cap** (`attachment_max_bytes`), enforced on the bytes actually received.
  - **No user input in paths.** Files are stored as `<random hex><ext>`; the original
    filename is display-only metadata in the DB. Every read/write/delete still goes
    through `app.tools.path_safety.resolve_within`, so even a tampered `path` column
    can't escape the attachments directory.
  - **Ownership.** Every lookup is scoped to the requesting user's id.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from pathlib import Path, PurePath

from sqlalchemy.orm import Session

from app.config.settings import Settings, get_settings
from app.database.models import Attachment, User
from app.llm.base import LLMAttachment
from app.tools.path_safety import PathSafetyError, resolve_within

logger = logging.getLogger(__name__)

# Extension -> the MIME type we store and send. The allow-list itself.
ALLOWED_EXTENSIONS: dict[str, str] = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF-",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
}

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class AttachmentError(ValueError):
    """An upload was rejected. `status_code` is what the HTTP route should answer."""

    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


def _display_name(raw: str | None) -> str:
    """The user's filename reduced to a safe display string: last path component only
    (either separator), control characters stripped, length capped. Never used to build
    a path -- this is belt and braces for anything that later renders it."""
    name = PurePath((raw or "").replace("\\", "/")).name
    name = _CONTROL_CHARS.sub("", name).strip()
    return name[:255] or "attachment"


def _content_matches(mime_type: str, data: bytes) -> bool:
    if mime_type in _SIGNATURES:
        return data.startswith(_SIGNATURES[mime_type])
    if mime_type == "image/webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if mime_type.startswith("text/"):
        if b"\x00" in data:
            return False
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return False
        return True
    return False


class AttachmentService:
    def __init__(self, db: Session, settings: Settings | None = None) -> None:
        self._db = db
        self._settings = settings or get_settings()

    @property
    def root(self) -> Path:
        return self._settings.attachments_path

    def _file_path(self, attachment: Attachment) -> Path:
        return resolve_within(self.root, attachment.path)

    def save(self, user_id: int, filename: str | None, data: bytes) -> Attachment:
        """Validate and store one upload. Raises `AttachmentError` on any rejection,
        before anything is written."""
        display_name = _display_name(filename)
        extension = PurePath(display_name).suffix.lower()
        mime_type = ALLOWED_EXTENSIONS.get(extension)
        if mime_type is None:
            allowed = ", ".join(sorted({ext.lstrip(".") for ext in ALLOWED_EXTENSIONS}))
            raise AttachmentError(f"Unsupported file type. Allowed: {allowed}.", status_code=415)
        if not data:
            raise AttachmentError("The file is empty.")
        if len(data) > self._settings.attachment_max_bytes:
            raise AttachmentError(
                f"File is larger than the {self._settings.attachment_max_bytes} byte limit.",
                status_code=413,
            )
        if not _content_matches(mime_type, data):
            raise AttachmentError(
                f"The file's contents don't look like a {extension.lstrip('.')} file.",
                status_code=415,
            )

        stored_name = f"{uuid.uuid4().hex}{extension}"
        try:
            target = resolve_within(self.root, stored_name)
        except PathSafetyError as exc:  # pragma: no cover -- name is server-generated
            raise AttachmentError(str(exc)) from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        # "xb": never overwrite -- a collision on a random 128-bit name means something
        # is very wrong, and failing loudly beats clobbering another user's file.
        with open(target, "xb") as handle:
            handle.write(data)

        attachment = Attachment(
            user_id=user_id,
            filename=display_name,
            mime_type=mime_type,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            path=stored_name,
        )
        self._db.add(attachment)
        try:
            self._db.commit()
        except Exception:
            target.unlink(missing_ok=True)
            raise
        self._db.refresh(attachment)
        return attachment

    def list(self, user_id: int) -> list[Attachment]:
        return (
            self._db.query(Attachment)
            .filter(Attachment.user_id == user_id)
            .order_by(Attachment.id.desc())
            .all()
        )

    def get(self, user_id: int, attachment_id: int) -> Attachment | None:
        return (
            self._db.query(Attachment)
            .filter(Attachment.id == attachment_id, Attachment.user_id == user_id)
            .first()
        )

    def delete(self, user_id: int, attachment_id: int) -> bool:
        attachment = self.get(user_id, attachment_id)
        if attachment is None:
            return False
        try:
            self._file_path(attachment).unlink(missing_ok=True)
        except PathSafetyError:
            # A row whose path points outside the store was never written by `save` --
            # drop the row, touch nothing on disk.
            logger.warning("Attachment %s has an unsafe path; deleting the row only.", attachment.id)
        self._db.delete(attachment)
        self._db.commit()
        return True

    def missing_ids(self, user_id: int, attachment_ids: list[int]) -> list[int]:
        """Which of `attachment_ids` don't exist or aren't this user's."""
        if not attachment_ids:
            return []
        owned = {
            row.id
            for row in self._db.query(Attachment.id)
            .filter(Attachment.user_id == user_id, Attachment.id.in_(attachment_ids))
            .all()
        }
        return [i for i in attachment_ids if i not in owned]

    def load_for_llm(self, username: str, attachment_ids: list[int]) -> list[LLMAttachment]:
        """The bytes of `username`'s attachments among `attachment_ids`, ready to send
        inline. Ids that aren't theirs, or whose file is gone, are skipped (the HTTP
        route has already rejected unknown ids; this is the defensive second check)."""
        if not attachment_ids:
            return []
        user = self._db.query(User).filter(User.username == username).first()
        if user is None:
            return []
        loaded: list[LLMAttachment] = []
        for attachment_id in attachment_ids[: self._settings.attachment_max_per_message]:
            attachment = self.get(user.id, attachment_id)
            if attachment is None:
                continue
            try:
                data = self._file_path(attachment).read_bytes()
            except (OSError, PathSafetyError):
                logger.warning("Attachment %s could not be read; skipping it.", attachment.id)
                continue
            loaded.append(
                LLMAttachment(filename=attachment.filename, mime_type=attachment.mime_type, data=data)
            )
        return loaded
