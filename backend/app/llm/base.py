"""
LLM provider interface (§3, §4, §30, file 05).

Provider-agnostic contract every concrete provider (`GeminiProvider` now, `OllamaProvider`
in file 07, any future cloud provider) implements. Nothing in this module may reference a
specific vendor's SDK/response shape -- that translation lives in the provider module
itself (e.g. `app/llm/gemini.py`), which is responsible for mapping its own success/error
responses onto `LLMResult` and its `status` taxonomy below.

`AIRouter` (file 06) is what actually chooses/fails over between providers; this file only
defines the shape they all speak so the router (and, for now, `AssistantCore`'s direct
call in file 05) can treat them interchangeably.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

# §5 error taxonomy. `AssistantCore`/`AIRouter` act on this; providers are only responsible
# for correct *classification* (see status meanings below) -- never for retrying
# QUOTA_EXHAUSTED, that's file 06's job.
#
#   SUCCESS          - request completed, `text` and/or `tool_calls` are populated.
#   RETRYABLE_ERROR  - transient (timeout, network blip, 5xx); safe to retry.
#   QUOTA_EXHAUSTED  - rate limit / daily quota hit; never retry, fail over instead.
#   PERMANENT_ERROR  - bad/missing API key, provider disabled, etc.; never retry.
LLMStatus = Literal["SUCCESS", "RETRYABLE_ERROR", "QUOTA_EXHAUSTED", "PERMANENT_ERROR"]


class ToolCallRequest(BaseModel):
    """A single tool invocation an LLM asked for, in the same (tool_name, params) shape
    `CommandRouter`'s deterministic path already produces (see `RouteResult` in
    `app.core.command_router`) -- so `AssistantCore` can send both through the same
    `ToolExecutor` without special-casing which path a call came from (§41 Rule 6).
    """

    tool_name: str
    params: dict[str, Any] = Field(default_factory=dict)


class LLMRequest(BaseModel):
    """Everything a provider needs to answer one turn. `context` carries whatever
    relevant memory/state is reasonably available (file 09 will refine what "relevant"
    means); `tools` is the JSON-schema tool definitions a provider may call, already
    filtered/shaped by the caller (file 08 narrows this from "every registered tool" to
    a selective subset -- this file has no opinion on that)."""

    message: str
    context: dict[str, Any] = Field(default_factory=dict)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    conversation_id: str | None = None


class LLMResult(BaseModel):
    """A provider's answer to one `LLMRequest`. `tool_calls` is empty unless the model
    asked to call tools -- callers execute those through `ToolExecutor`, never directly.
    `error_type` is a short provider-specific string (e.g. "timeout", "invalid_api_key")
    for logging/`llm_usage`, and is only meaningful when `status != "SUCCESS"`.
    """

    text: str = ""
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    request_tokens: int = 0
    response_tokens: int = 0
    status: LLMStatus
    error_type: str | None = None
    latency_ms: float = 0.0


@runtime_checkable
class LLMProvider(Protocol):
    """Contract every LLM provider satisfies. `is_available()` must be a cheap local
    check (e.g. "is an API key configured") -- never a network call -- so `AIRouter`
    (file 06) can probe providers without incurring latency or quota cost (§41 Rule 3:
    never assume the LLM is available)."""

    name: str

    def is_available(self) -> bool: ...

    async def generate(self, request: LLMRequest, *, fallback_used: bool = False) -> LLMResult:
        """`fallback_used` is set by `AIRouter` (file 06) to True whenever this call
        isn't the first provider attempted in the chain for a given request, so each
        provider's own `llm_usage` logging (see e.g. `GeminiProvider._log_usage`)
        records it correctly. Direct callers (unit tests, a provider exercised in
        isolation) get the default False."""
        ...
