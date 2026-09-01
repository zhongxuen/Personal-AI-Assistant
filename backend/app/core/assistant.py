"""
Assistant core orchestrator (§20).

AssistantCore.handle() is the single entrypoint every platform adapter (desktop, web,
discord, ...) calls -- CommandRouter resolves the incoming message deterministically,
ToolExecutor runs any resolved tool call through the full validate/permission/platform/
log pipeline, and the result is wrapped back into an AssistantResponse. Never duplicate
this orchestration logic per platform (§41 Rule 7); adapters only translate in and out.

When CommandRouter can't resolve a message deterministically (classification
LLM_REQUIRED), it's handed to AIRouter (file 06), which walks the configured provider
chain (just GeminiProvider for now) and fails over on any non-SUCCESS result. Any tool
calls the LLM asks for still go through the *same* ToolExecutor as the deterministic
path (§41 Rule 6) -- the LLM never gets a shortcut around validation/permission/platform
checks.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import anyio.to_thread
from sqlalchemy.orm import Session

from app.core.command_router import CommandClassification, CommandRouter
from app.core.context_manager import build_context
from app.core.models import AssistantRequest, AssistantResponse, AssistantStreamEvent
from app.core.permissions import RequesterContext
from app.core.tool_executor import ToolExecutor
from app.llm.ai_router import NO_PROVIDER_AVAILABLE_ERROR_TYPE, AIRouter
from app.llm.base import LLMRequest, LLMResult, ToolCallRequest
from app.llm.health import HealthManager
from app.tools.base import Tool, ToolResult
from app.tools.registry import ToolRegistry
from app.tools.relevance import select_relevant_tools

# Honest, non-crashing explanations for each non-SUCCESS LLMResult.status (§41 Rule 3).
# None of these are retried further here -- each provider already retried its own
# RETRYABLE_ERROR internally before returning, and AIRouter already failed over across
# the whole chain -- these are what's left once every option is exhausted.
_LLM_UNAVAILABLE_MESSAGES = {
    "QUOTA_EXHAUSTED": (
        "I've hit my reasoning quota for now, so I can't work through that request. "
        "Please try again later."
    ),
    "RETRYABLE_ERROR": (
        "I'm having trouble reaching my reasoning service right now. Please try again "
        "in a moment."
    ),
    "PERMANENT_ERROR": (
        "Reasoning isn't available right now (a configuration problem on my end). "
        "I can still handle direct commands in the meantime."
    ),
}
# Chain-level condition from AIRouter (every provider skipped or failed) -- distinct
# from a single provider's PERMANENT_ERROR, which is usually a config problem; this
# one might just as easily be quota/health across the board, so it gets its own,
# more accurate message rather than borrowing PERMANENT_ERROR's.
_NO_PROVIDER_AVAILABLE_MESSAGE = (
    "I can't reach any reasoning provider right now, so I can't work through that "
    "request. I can still handle direct commands in the meantime."
)


def _success_text(tool_name: str, result: ToolResult) -> str:
    if result.data and "message" in result.data:
        return str(result.data["message"])
    return f"Done: '{tool_name}' executed successfully."


def _tool_to_schema(tool: Tool) -> dict:
    """A registered `Tool` as the plain JSON-schema dict `LLMRequest.tools` expects."""
    return {"name": tool.name, "description": tool.description, "parameters": tool.parameters}


class AssistantCore:
    """Routes a request to a deterministic tool call, or -- when nothing matches --
    to AIRouter for a reasoned answer (which may itself request tool calls).
    """

    def __init__(
        self,
        registry: ToolRegistry,
        db: Session | None = None,
        health_manager: HealthManager | None = None,
    ) -> None:
        self.registry = registry
        self.db = db
        self.router = CommandRouter(registry)
        self.executor = ToolExecutor(registry, db=db)
        # `health_manager` defaults to a fresh, per-instance HealthManager (the
        # original behavior) when the caller doesn't supply one -- but every real
        # request should pass the process-wide instance from
        # `app.api.dependencies.get_health_manager` so provider health actually
        # persists across requests instead of resetting every time.
        self.ai_router = AIRouter(db=db, health_manager=health_manager)

    def handle(self, request: AssistantRequest) -> AssistantResponse:
        """Synchronous entrypoint, unchanged in behavior and signature.

        Kept for callers that genuinely have no event loop of their own (the test
        suite, and any future sync adapter). Production adapters should prefer
        `handle_async` -- see its docstring for why that matters for latency.
        """
        context = self._requester_context(request)
        route_result = self.router.route(request.message)

        if route_result.classification == CommandClassification.LLM_REQUIRED:
            # Only the LLM path needs a loop, so the deterministic path below stays
            # entirely synchronous -- a direct command must not pay for event-loop
            # setup it never uses.
            return asyncio.run(self._handle_needs_llm(request, context))

        return self._handle_deterministic(route_result, context)

    async def handle_async(self, request: AssistantRequest) -> AssistantResponse:
        """Async entrypoint -- same contract as `handle()`, same return value.

        This exists for latency, not style. `handle()` reaches the LLM through
        `asyncio.run()`, which builds and tears down a fresh event loop per call, and an
        HTTP client's pooled connections die with the loop that opened them. Every
        message therefore paid a full DNS + TCP + TLS handshake to the provider before
        sending a single token. Awaiting on a loop that outlives the request (uvicorn's,
        or discord.py's) lets `app.llm.clients` keep one warm connection pool per loop,
        so only the first message after startup pays that cost.

        Blocking work -- tool handlers, DB reads, context building -- is pushed to a
        worker thread rather than run inline, because on a shared loop a slow tool would
        otherwise stall every other in-flight request, not just this one.
        """
        context = self._requester_context(request)
        route_result = self.router.route(request.message)

        if route_result.classification == CommandClassification.LLM_REQUIRED:
            return await self._handle_needs_llm(request, context)

        return await anyio.to_thread.run_sync(self._handle_deterministic, route_result, context)

    async def handle_stream(
        self, request: AssistantRequest
    ) -> AsyncIterator[AssistantStreamEvent]:
        """Incremental form of `handle_async()` -- see `AssistantStreamEvent`.

        The turn's *total* duration is unchanged; what changes is when the user first
        sees something. Non-streaming, time-to-first-character equals the full
        generation time, so the chat sits on a typing indicator for seconds. Here, text
        is forwarded the moment the model produces it and each tool call is reported as
        it finishes.

        The terminal `"done"` event carries exactly the `AssistantResponse` that
        `handle_async()` would have returned for the same request, so the two paths can
        never disagree about the final answer -- deltas are a preview of it, and a
        consumer that trusts only `"done"` is always correct.
        """
        context = self._requester_context(request)
        route_result = self.router.route(request.message)

        # A deterministic command resolves locally in microseconds -- there is nothing
        # to stream, so it emits a single terminal event rather than pretending to.
        if route_result.classification != CommandClassification.LLM_REQUIRED:
            response = await anyio.to_thread.run_sync(
                self._handle_deterministic, route_result, context
            )
            yield AssistantStreamEvent(type="done", response=response)
            return

        llm_request = await anyio.to_thread.run_sync(self._build_llm_request, request)

        final: LLMResult | None = None
        async for chunk in self.ai_router.route_stream(llm_request):
            if chunk.final is not None:
                final = chunk.final
                break
            if chunk.delta:
                yield AssistantStreamEvent(type="delta", text=chunk.delta)

        if final is None or final.status != "SUCCESS":
            yield AssistantStreamEvent(
                type="done", response=self._llm_unavailable_response(final)
            )
            return

        # Tool calls are executed exactly as the non-streaming path executes them --
        # same ToolExecutor, same validation/permission/platform checks (§41 Rule 6) --
        # the only addition is emitting each one as it completes.
        tool_calls: list[dict] = []
        result_texts: list[str] = []
        for call in final.tool_calls:
            entry, text = await anyio.to_thread.run_sync(
                self._execute_llm_tool_call, call, context
            )
            tool_calls.append(entry)
            result_texts.append(text)
            yield AssistantStreamEvent(type="tool", tool_call=entry)

        yield AssistantStreamEvent(
            type="done",
            response=self._compose_llm_response(final, tool_calls, result_texts),
        )

    def _requester_context(self, request: AssistantRequest) -> RequesterContext:
        return RequesterContext(
            user_id=request.user_id,
            platform=request.platform,
            confirmed=bool(request.metadata.get("confirmed", False)),
            override=bool(request.metadata.get("override", False)),
        )

    def _handle_deterministic(
        self, route_result, context: RequesterContext
    ) -> AssistantResponse:
        # DETERMINISTIC, LOCAL_PARSE, and CACHED all already resolved to a concrete
        # tool call -- run it through the same executor/permission pipeline regardless
        # of which of those produced it.
        result = self.executor.execute(route_result.tool_name, route_result.params, context)

        text = result.error if not result.success else _success_text(route_result.tool_name, result)

        return AssistantResponse(
            text=text or "",
            tool_calls=[
                {
                    "tool_name": route_result.tool_name,
                    "params": route_result.params,
                    "result": result.model_dump(),
                }
            ],
            used_llm=False,
        )

    def _build_llm_request(self, request: AssistantRequest) -> LLMRequest:
        """The `LLMRequest` for one turn. Synchronous and potentially slow -- context
        building reads conversation history and memory from the DB -- so async callers
        run this on a worker thread.
        """
        # file 08: narrow the full platform-eligible tool set down to whatever's
        # plausibly relevant to this message (see app/tools/relevance.py) instead of
        # handing Gemini everything, as file 05 did.
        platform_tools = self.registry.list(platform=request.platform)
        relevant_tools = select_relevant_tools(request.message, platform_tools)
        return LLMRequest(
            message=request.message,
            # file 08 prompt 3: current message + bounded recent conversation turns +
            # relevant memory -- see app.core.context_manager, replacing the
            # {"user_id", "platform"}-only context file 05 built here directly.
            context=build_context(request, self.db),
            tools=[_tool_to_schema(tool) for tool in relevant_tools],
            conversation_id=request.conversation_id,
        )

    def _llm_unavailable_response(self, result: LLMResult | None) -> AssistantResponse:
        """The honest, non-crashing explanation for a non-SUCCESS chain outcome (§41
        Rule 3). `None` means the stream ended without any result at all, which is the
        same "nothing could answer" situation as an exhausted chain.
        """
        if result is None or result.error_type == NO_PROVIDER_AVAILABLE_ERROR_TYPE:
            return AssistantResponse(text=_NO_PROVIDER_AVAILABLE_MESSAGE, used_llm=False)
        message = _LLM_UNAVAILABLE_MESSAGES.get(
            result.status, "Reasoning is temporarily unavailable. Please try again later."
        )
        return AssistantResponse(text=message, used_llm=False)

    async def _handle_needs_llm(
        self, request: AssistantRequest, context: RequesterContext
    ) -> AssistantResponse:
        llm_request = await anyio.to_thread.run_sync(self._build_llm_request, request)
        result = await self.ai_router.route(llm_request)

        if result.status != "SUCCESS":
            return self._llm_unavailable_response(result)

        return await anyio.to_thread.run_sync(self._handle_llm_success, result, context)

    def _handle_llm_success(
        self, result: LLMResult, context: RequesterContext
    ) -> AssistantResponse:
        # file 08 prompt 3 (call consolidation review): GeminiProvider/OllamaProvider
        # already fold every tool call from a single model response into one
        # `LLMResult.tool_calls` list (see their `_to_result`) -- no re-invocation of
        # the LLM happens per tool call, so there's no round trip to remove there.
        #
        # The round trip that *does* remain, for future revisit: once every tool call
        # below is executed, its `ToolResult` is turned into user-facing text locally
        # (`_success_text`/the error string) and never sent back to the provider for a
        # second, tool-result-aware generation call. A full agentic loop would instead
        # feed `tool_calls` + their results back to the model so it can compose one
        # final natural-language reply grounded in what actually happened. Skipping
        # that second call is deliberate for now (one LLM call per turn keeps quota
        # usage predictable, per §9/file 08's whole goal) but means `result.text` (if
        # the model returned any alongside its tool calls) and the mechanically-joined
        # tool result text below are never reconciled by the model itself.
        tool_calls: list[dict] = []
        result_texts: list[str] = []

        for call in result.tool_calls:
            entry, text = self._execute_llm_tool_call(call, context)
            tool_calls.append(entry)
            result_texts.append(text)

        return self._compose_llm_response(result, tool_calls, result_texts)

    def _execute_llm_tool_call(
        self, call: ToolCallRequest, context: RequesterContext
    ) -> tuple[dict, str]:
        """Run one LLM-requested tool call, returning its `AssistantResponse.tool_calls`
        entry alongside the user-facing text for it.

        Same choke point as the deterministic path -- validation, permission,
        platform-capability checks, and logging all still apply (§41 Rule 6). Split out
        so `handle_stream` can report each call the moment it finishes without
        duplicating any of that.
        """
        tool_result = self.executor.execute(call.tool_name, call.params, context)
        entry = {
            "tool_name": call.tool_name,
            "params": call.params,
            "result": tool_result.model_dump(),
        }
        text = (
            tool_result.error
            if not tool_result.success
            else _success_text(call.tool_name, tool_result)
        )
        return entry, text

    def _compose_llm_response(
        self, result: LLMResult, tool_calls: list[dict], result_texts: list[str]
    ) -> AssistantResponse:
        """The final `AssistantResponse` for a successful LLM turn -- the one place
        both the streaming and non-streaming paths agree on what the reply says, so
        they cannot drift apart.
        """
        text = result.text or "\n".join(t for t in result_texts if t) or "Done."

        return AssistantResponse(
            text=text,
            tool_calls=tool_calls,
            used_llm=True,
            # Whichever provider `AIRouter` actually got this from. This was hardcoded
            # to "gemini", which mislabeled every reply the Ollama fallback served --
            # visible to the user, since the web chat prints "Reasoned (<provider>)".
            provider=result.provider,
        )
