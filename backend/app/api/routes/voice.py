"""
Voice message route (§24, §25, file 10 prompt 2).

`POST /api/voice/message` is voice's one entrypoint, mirroring
`app.api.routes.assistant`'s `POST /api/assistant/message` but starting from audio
instead of a JSON message body. It never re-implements command routing: the resulting
transcript is handed to `app.platforms.desktop.DesktopAdapter.to_request()` -- the same
platform-adapter translation file 02 built for turning platform-native input into an
`AssistantRequest` -- and from there into the exact same `AssistantCore.handle()`
every platform calls (§41 Rule 7). The response text is then optionally spoken back via
`TextToSpeechProvider`.

The route is defined `def`, not `async def`, on purpose: `AssistantCore.handle()` calls
`asyncio.run(...)` internally on the LLM_REQUIRED path (see `app.core.assistant`), which
raises if it's ever invoked from a thread that already has a running event loop. FastAPI
runs sync `def` routes in a worker thread with no event loop of its own (the same reason
`app.api.routes.assistant.post_message` is sync), so `audio.file.read()` (the
`UploadFile`'s underlying `SpooledTemporaryFile`, read synchronously) is used here
instead of `await audio.read()`.

Two-step confirm flow lives behind this one endpoint, not two, driven by `dry_run`:
  1. The frontend records audio and POSTs it here with `dry_run=true` -- only STT runs,
     so the transcript can be shown to the user for review/edit before anything actually
     executes. This matters most before a CONFIRM/RESTRICTED tool (§19) runs off of a
     misheard transcript (file 10 goal item 6).
  2. Once the user accepts (or edits) that transcript, the frontend POSTs again with
     `text=<that transcript>` and `dry_run=false` (no audio re-upload, no repeat STT
     pass) to actually run it through `AssistantCore.handle()` and get a spoken reply.
`audio` and `text` are mutually exclusive entry points into the same pipeline -- exactly
one of them must be provided on any given call.
"""

from __future__ import annotations

import base64
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.dependencies import (
    get_health_manager,
    get_stt_provider,
    get_tool_registry,
    get_tts_provider,
)
from app.api.local_only import enforce_desktop_local_only
from app.core.assistant import AssistantCore
from app.database.database import get_db
from app.llm.health import HealthManager
from app.platforms.desktop import DEFAULT_USER_ID, DesktopAdapter
from app.tools.registry import ToolRegistry
from app.voice.stt import SpeechToTextProvider
from app.voice.tts import TextToSpeechProvider

logger = logging.getLogger("jarvis.voice")

router = APIRouter(tags=["voice"])


class VoiceMessageResponse(BaseModel):
    transcript: str
    text: str | None = None
    tool_calls: list = []
    used_llm: bool = False
    provider: str | None = None
    audio_base64: str | None = None


@router.post("/voice/message", response_model=VoiceMessageResponse)
def post_voice_message(
    http_request: Request,
    audio: UploadFile | None = File(None),
    text: str | None = Form(None),
    dry_run: bool = Form(False),
    speak: bool = Form(True),
    user_id: str = Form(DEFAULT_USER_ID),
    conversation_id: str | None = Form(None),
    confirmed: bool = Form(False),
    override: bool = Form(False),
    db: Session = Depends(get_db),
    registry: ToolRegistry = Depends(get_tool_registry),
    health_manager: HealthManager = Depends(get_health_manager),
    stt: SpeechToTextProvider = Depends(get_stt_provider),
    tts: TextToSpeechProvider = Depends(get_tts_provider),
) -> VoiceMessageResponse:
    # This route only ever builds platform="desktop" requests (via DesktopAdapter
    # below), so the local-only boundary (§23, app.api.local_only) applies
    # unconditionally here, checked up front before any STT work is done.
    enforce_desktop_local_only(http_request, "desktop")

    if audio is None and text is None:
        raise HTTPException(status_code=400, detail="Provide either 'audio' or 'text'.")
    if audio is not None and text is not None:
        raise HTTPException(status_code=400, detail="Provide only one of 'audio' or 'text', not both.")

    if text is not None:
        transcript = text.strip()
    else:
        assert audio is not None  # narrowed by the checks above
        audio_bytes = audio.file.read()
        try:
            transcript = stt.transcribe(audio_bytes).strip()
        except Exception as exc:  # STT errors are raised as plain exceptions (see app.voice.stt)
            logger.exception("STT transcription failed.")
            raise HTTPException(status_code=502, detail=f"Transcription failed: {exc}") from exc

    if not transcript:
        raise HTTPException(status_code=422, detail="Nothing was transcribed from that audio.")

    if dry_run:
        # STT-only preview -- AssistantCore.handle() is never called here, so this
        # branch can't trigger a tool (deterministic or LLM) by itself. See module
        # docstring's two-step confirm flow.
        return VoiceMessageResponse(transcript=transcript)

    # Same platform-adapter translation file 02 built for turning platform-native
    # input into an AssistantRequest (§20-22) -- voice does not get its own
    # command-routing path (§41 Rule 7). confirmed/override/conversation_id have no
    # equivalent raw-string input for DesktopAdapter to carry, so they're folded into
    # the resulting request's metadata afterward; CommandRouter/AssistantCore/
    # ToolExecutor themselves are never touched here.
    base_request = DesktopAdapter().to_request(transcript)
    request = base_request.model_copy(
        update={
            "user_id": user_id,
            "conversation_id": conversation_id,
            "metadata": {"confirmed": confirmed, "override": override},
        }
    )

    core = AssistantCore(registry, db=db, health_manager=health_manager)
    response = core.handle(request)

    audio_base64: str | None = None
    if speak and response.text:
        try:
            if tts.is_available():
                audio_base64 = base64.b64encode(tts.synthesize(response.text)).decode("ascii")
            else:
                logger.info("TTS provider unavailable; returning a text-only voice response.")
        except Exception:
            # Speech is a convenience layered on top of the text response, not a
            # requirement for the response to be useful (§41 Rule 3) -- a synthesis
            # failure must never fail the whole request.
            logger.exception("TTS synthesis failed; returning a text-only voice response.")

    return VoiceMessageResponse(
        transcript=transcript,
        text=response.text,
        tool_calls=response.tool_calls,
        used_llm=response.used_llm,
        provider=response.provider,
        audio_base64=audio_base64,
    )
