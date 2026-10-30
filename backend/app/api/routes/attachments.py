"""
Attachment routes -- upload, list, delete (`app.attachments.service`).

Files are stored on the local disk of the machine running this backend and are only
ever sent to a provider inline, for the one chat request that references them. On the
deployed Render backend that disk is ephemeral (see `/api/health`'s `storage_mode`), so
attachments there disappear on restart/redeploy, along with everything else.

Every route requires a bearer token (router-level `get_current_user`), and every
lookup is scoped to the caller's own user id -- another user's attachment id answers
404, exactly like one that doesn't exist.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.attachments.service import AttachmentError, AttachmentService
from app.config.settings import Settings, get_settings
from app.database.database import get_db
from app.database.models import Attachment, User

router = APIRouter(tags=["attachments"], dependencies=[Depends(get_current_user)])

_READ_CHUNK_BYTES = 64 * 1024


class AttachmentOut(BaseModel):
    id: int
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    created_at: datetime

    @classmethod
    def from_row(cls, row: Attachment) -> "AttachmentOut":
        return cls(
            id=row.id,
            filename=row.filename,
            mime_type=row.mime_type,
            size_bytes=row.size_bytes,
            sha256=row.sha256,
            created_at=row.created_at,
        )


def get_attachment_settings() -> Settings:
    """Separate dependency so tests can point the store at a temp directory."""
    return get_settings()


def _service(db: Session, settings: Settings) -> AttachmentService:
    return AttachmentService(db, settings=settings)


@router.post("/attachments", response_model=AttachmentOut, status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    file: UploadFile,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_attachment_settings),
    current_user: User = Depends(get_current_user),
) -> AttachmentOut:
    # Read in chunks and stop one byte past the limit, so an oversized upload is
    # rejected without ever holding the whole thing in memory.
    limit = settings.attachment_max_bytes
    chunks: list[bytes] = []
    received = 0
    while True:
        chunk = await file.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        received += len(chunk)
        if received > limit:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File is larger than the {limit} byte limit.",
            )
        chunks.append(chunk)

    try:
        row = _service(db, settings).save(current_user.id, file.filename, b"".join(chunks))
    except AttachmentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return AttachmentOut.from_row(row)


@router.get("/attachments", response_model=list[AttachmentOut])
def list_attachments(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_attachment_settings),
    current_user: User = Depends(get_current_user),
) -> list[AttachmentOut]:
    return [AttachmentOut.from_row(row) for row in _service(db, settings).list(current_user.id)]


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_attachment(
    attachment_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_attachment_settings),
    current_user: User = Depends(get_current_user),
) -> Response:
    if not _service(db, settings).delete(current_user.id, attachment_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
