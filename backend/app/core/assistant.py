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

from sqlalchemy.orm import Session

from app.core.command_router import CommandClassification, CommandRouter
from app.core.context_manager import build_context
from app.core.models import AssistantRequest, AssistantResponse
from app.core.permissions import RequesterContext
from app.core.tool_executor import ToolExecutor
from app.llm.ai_router import NO_PROVIDER_AVAILABLE_ERROR_TYPE, AIRouter
from app.llm.base import LLMRequest, LLMResult
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
        context = RequesterContext(
            user_id=request.user_id,
            platform=request.platform,
            confirmed=bool(request.metadata.get("confirmed", False)),
            override=bool(request.metadata.get("override", False)),
        )

        route_result = self.router.route(request.message)

        if route_result.classification == CommandClassification.LLM_REQUIRED:
            return self._handle_needs_llm(request, context)

        # DETERMINISTIC, LOCAL_PARSE, and (once file 08 prompt 4 lands) CACHED all
        # already resolved to a concrete tool call -- run it through the same
        # executor/permission pipeline regardless of which of those produced it.

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

    def _handle_needs_llm(
        self, request: AssistantRequest, context: RequesterContext
    ) -> AssistantResponse:
        # file 08: narrow the full platform-eligible tool set down to whatever's
        # plausibly relevant to this message (see app/tools/relevance.py) instead of
        # handing Gemini everything, as file 05 did.
        platform_tools = self.registry.list(platform=request.platform)
        relevant_tools = select_relevant_tools(request.message, platform_tools)
        llm_request = LLMRequest(
            message=request.message,
            # file 08 prompt 3: current message + bounded recent conversation turns +
            # relevant memory (guarded no-op until file 09 exists) -- see
            # app.core.context_manager, replacing the {"user_id", "platform"}-only
            # context file 05 built here directly.
            context=build_context(request, self.db),
            tools=[_tool_to_schema(tool) for tool in relevant_tools],
            conversation_id=request.conversation_id,
        )
        result = asyncio.run(self.ai_router.route(llm_request))

        if result.status != "SUCCESS":
            if result.error_type == NO_PROVIDER_AVAILABLE_ERROR_TYPE:
                message = _NO_PROVIDER_AVAILABLE_MESSAGE
            else:
                message = _LLM_UNAVAILABLE_MESSAGES.get(
                    result.status, "Reasoning is temporarily unavailable. Please try again later."
                )
            return AssistantResponse(text=message, used_llm=False)

        return self._handle_llm_success(result, context)

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
            # Same choke point as the deterministic path -- validation, permission,
            # platform-capability checks, and logging all still apply (§41 Rule 6).
            tool_result = self.executor.execute(call.tool_name, call.params, context)
            tool_calls.append(
                {
                    "tool_name": call.tool_name,
                    "params": call.params,
                    "result": tool_result.model_dump(),
                }
            )
            result_texts.append(
                tool_result.error
                if not tool_result.success
                else _success_text(call.tool_name, tool_result)
            )

        text = result.text or "\n".join(t for t in result_texts if t) or "Done."

        return AssistantResponse(
            text=text,
            tool_calls=tool_calls,
            used_llm=True,
            provider="gemini",
        )
