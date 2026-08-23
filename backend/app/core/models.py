"""
Standard request/response models (§21).

Every platform adapter (desktop, web, discord, ...) converts its native input into an
AssistantRequest, and renders the AssistantResponse it gets back -- these two models
are the only shape AssistantCore ever accepts or returns (§20). No platform-specific
fields belong here; platform quirks live in the adapter, not in this contract.
"""

from __future__ import annotations

from pydantic import BaseModel


class AssistantRequest(BaseModel):
    user_id: str
    platform: str  # "desktop" | "web" | "discord" | ...
    message: str
    conversation_id: str | None = None
    metadata: dict = {}


class AssistantResponse(BaseModel):
    text: str
    tool_calls: list = []
    used_llm: bool = False
    provider: str | None = None
