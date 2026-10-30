"""
LLM provider interface (§3, §4, §30, file 05).

Provider-agnostic contract every concrete provider (`OpenAIProvider`, `OllamaProvider`,
`GeminiProvider`, any future one) implements. Nothing in this module may reference a
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
    # The provider's own id for this call, when it has one (OpenAI's `call_id`). Only
    # needed to pair a tool result back up with the call that asked for it on the next
    # tool-loop step; providers without ids leave it None.
    call_id: str | None = None


class LLMAttachment(BaseModel):
    """A user-uploaded file referenced by this turn, already read from local disk by
    the caller. Providers that can take files send it inline for this one request only
    (never uploaded to a provider-side file store); providers that can't ignore it."""

    filename: str
    mime_type: str
    data: bytes


class ToolExchange(BaseModel):
    """One completed tool-loop step: the calls a model asked for and what each one
    returned, fed back on the next request so the model can answer from real results.

    `calls` and `outputs` are parallel lists. `provider`/`provider_items` carry the
    answering provider's own native output items (e.g. OpenAI's encrypted reasoning +
    function_call items) so that provider can replay them verbatim; any other provider
    must ignore them and use the provider-agnostic `calls`/`outputs` instead, since a
    turn may fail over between steps.
    """

    calls: list[ToolCallRequest]
    outputs: list[str]
    provider: str | None = None
    provider_items: list[dict[str, Any]] = Field(default_factory=list)


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
    attachments: list[LLMAttachment] = Field(default_factory=list)
    # Earlier steps of this same turn's tool loop, oldest first (see `ToolExchange`).
    tool_exchanges: list[ToolExchange] = Field(default_factory=list)
    # False on the tool loop's last allowed step: the model must answer in text. A
    # provider that can keep `tools` declared while forbidding calls (OpenAI's
    # tool_choice="none") should, so the cached prompt prefix stays identical.
    allow_tool_calls: bool = True

    def context_with_tool_results(self) -> dict[str, Any]:
        """`context`, plus the results of any earlier tool-loop steps as plain data --
        for providers with no native tool-result message (they serialize context into
        their system prompt), so a turn that fails over mid-loop still sees what the
        tools returned instead of answering blind."""
        if not self.tool_exchanges:
            return self.context
        results = [
            {"tool": call.tool_name, "params": call.params, "result": output}
            for exchange in self.tool_exchanges
            for call, output in zip(exchange.calls, exchange.outputs)
        ]
        return {**self.context, "tool_results": results}

    def tools_for_call(self) -> list[dict[str, Any]]:
        """`tools`, or none at all when this step may not call tools -- for providers
        with no way to declare tools while forbidding their use."""
        return self.tools if self.allow_tool_calls else []


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
    # Also stamped by `AIRouter`: whether the provider that answered can take its tool
    # results back on a follow-up call (its class-level `supports_tool_loop`).
    # `AssistantCore` only continues the tool loop when this is True; otherwise it
    # keeps the original single-shot behavior (execute tools, report their results).
    tool_loop: bool = False
    # Provider-native output items worth replaying on the next tool-loop step (see
    # `ToolExchange.provider_items`). Empty for providers that don't need any.
    provider_items: list[dict[str, Any]] = Field(default_factory=list)
    # Input tokens served from the provider's prompt cache (a subset of
    # `request_tokens`), when the provider reports it. Billed at a lower rate.
    cached_tokens: int = 0


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
    # Optional class attribute, read via `getattr(provider, "supports_tool_loop",
    # False)` -- True only for a provider that understands `LLMRequest.tool_exchanges`
    # natively (OpenAI). See `LLMResult.tool_loop`.

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
