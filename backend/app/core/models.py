"""
Standard request/response models (§21).

Every platform adapter (desktop, web, discord, ...) converts its native input into an
AssistantRequest, and renders the AssistantResponse it gets back -- these two models
are the only shape AssistantCore ever accepts or returns (§20). No platform-specific
fields belong here; platform quirks live in the adapter, not in this contract.
"""

from __future__ import annotations

from typing import Literal

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


class AssistantStreamEvent(BaseModel):
    """One event from `AssistantCore.handle_stream()` -- the incremental form of
    `AssistantResponse`, for platforms that can render a reply as it arrives (the web
    chat's SSE endpoint today, see `app.api.routes.assistant`).

    `type` says which fields matter:

      - `"delta"`  -- `text` holds more reply text to append. Incremental, never
                      cumulative.
      - `"tool"`   -- `tool_call` holds one finished tool call (same
                      `{tool_name, params, result}` shape as `AssistantResponse.
                      tool_calls`), emitted as soon as that call completes so the UI can
                      show progress instead of waiting for the whole turn.
      - `"done"`   -- `response` holds the complete `AssistantResponse`, identical to
                      what the non-streaming path would have returned for the same
                      request. Always exactly one, always last.

    There is deliberately no `"error"` type: a failure is still a `"done"` carrying an
    `AssistantResponse` whose `text` is the honest explanation (§41 Rule 3), so a
    consumer has exactly one terminal case to handle and streaming can't produce a
    user-visible outcome the non-streaming path wouldn't.
    """

    type: Literal["delta", "tool", "done"]
    text: str = ""
    tool_call: dict | None = None
    response: AssistantResponse | None = None
