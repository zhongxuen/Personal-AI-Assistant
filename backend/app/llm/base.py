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

from collections.abc import AsyncIterator
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
    # Which provider actually produced this result. Set by `AIRouter` once it knows
    # which chain entry answered -- a provider doesn't set it on itself, since it has no
    # way to know it was the one whose result got used. `AssistantCore` reports it as
    # `AssistantResponse.provider`, which before this field existed was hardcoded to
    # "gemini" and so mislabeled every reply the Ollama fallback actually served.
    provider: str | None = None


class LLMStreamChunk(BaseModel):
    """One event from a *streaming* provider call (`LLMProvider.generate_stream`).

    Exactly one of the two fields is meaningful per chunk:

      - `delta` -- more generated text, to append to whatever arrived before it. Deltas
        are incremental, never cumulative, so a consumer concatenates rather than
        replaces.
      - `final` -- the terminal, complete `LLMResult` for the whole call, including any
        `tool_calls` and the token/latency accounting. Exactly one chunk carries this,
        it is always the last one yielded, and it is yielded on failure too (with the
        matching non-SUCCESS status) rather than the stream raising.

    Streaming is deliberately expressed as "the same `LLMResult`, plus early partial
    text" rather than a separate parallel result type: a caller that doesn't care about
    incremental output can ignore every `delta` and use only `final`, and get exactly
    what non-streaming `generate()` would have returned. That keeps `AssistantCore`'s
    tool-execution path identical for both, instead of forking into a second
    orchestration path (§41 Rule 7).
    """

    delta: str = ""
    final: LLMResult | None = None


@runtime_checkable
class LLMProvider(Protocol):
    """Contract every LLM provider satisfies. For a cloud provider like `GeminiProvider`,
    `is_available()` should be a cheap local check (e.g. "is an API key configured") --
    never a network call -- so `AIRouter` (file 06) can probe it without incurring
    latency or quota cost. A provider fronting a local service the app cannot assume
    is installed or running (`OllamaProvider`, file 07) is the deliberate exception:
    it performs a short, bounded network probe instead, since "is this configured" and
    "is this actually running right now" are different questions for a local service.
    Either way, `is_available()` must never raise and never assume availability (§41
    Rule 3) -- a connection error, timeout, or missing config all just mean False."""

    name: str

    def is_available(self) -> bool: ...

    async def generate(self, request: LLMRequest, *, fallback_used: bool = False) -> LLMResult:
        """`fallback_used` is set by `AIRouter` (file 06) to True whenever this call
        isn't the first provider attempted in the chain for a given request, so each
        provider's own `llm_usage` logging (see e.g. `GeminiProvider._log_usage`)
        records it correctly. Direct callers (unit tests, a provider exercised in
        isolation) get the default False."""
        ...

    def generate_stream(
        self, request: LLMRequest, *, fallback_used: bool = False
    ) -> AsyncIterator[LLMStreamChunk]:
        """Same call as `generate()`, but yielding text incrementally as the model
        produces it -- see `LLMStreamChunk`. Must yield exactly one chunk with `final`
        set, last, on success *and* on failure (a stream must not raise where
        `generate()` would have returned a classified non-SUCCESS `LLMResult`), and must
        log to `llm_usage` exactly once, same as `generate()`.

        Optional in practice: `AIRouter.route_stream` checks for this attribute and
        falls back to buffering `generate()` into a single terminal chunk for any
        provider that doesn't implement it, so a provider whose SDK has no streaming
        endpoint stays usable and simply doesn't produce early text.
        """
        ...
