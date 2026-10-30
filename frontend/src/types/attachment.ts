// Mirrors backend/app/api/routes/attachments.py's AttachmentOut.

export interface Attachment {
  id: number
  filename: string
  mime_type: string
  size_bytes: number
  sha256: string
  created_at: string
}
