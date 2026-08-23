"""
Assistant message route.

Thin HTTP wrapper around AssistantCore.handle -- all orchestration logic lives there
(§41 Rule 7), this just validates the request body, wires up dependencies, and returns
the response.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_tool_registry
from app.core.assistant import AssistantCore
from app.core.models import AssistantRequest, AssistantResponse
from app.database.database import get_db
from app.tools.registry import ToolRegistry

router = APIRouter(tags=["assistant"])


@router.post("/assistant/message", response_model=AssistantResponse)
def post_message(
    request: AssistantRequest,
    db: Session = Depends(get_db),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> AssistantResponse:
    core = AssistantCore(registry, db=db)
    return core.handle(request)
