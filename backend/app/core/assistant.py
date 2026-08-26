"""
Assistant core orchestrator (§20).

AssistantCore.handle() is the single entrypoint every platform adapter (desktop, web,
discord, ...) calls -- CommandRouter resolves the incoming message deterministically,
ToolExecutor runs any resolved tool call through the full validate/permission/platform/
log pipeline, and the result is wrapped back into an AssistantResponse. Never duplicate
this orchestration logic per platform (§41 Rule 7); adapters only translate in and out.

When CommandRouter can't resolve a message deterministically (NEEDS_LLM), it's handed to
AIRouter (file 06), which walks the configured provider chain (just GeminiProvider for
now) and fails over on any non-SUCCESS result. Any tool calls the LLM asks for still go
through the *same* ToolExecutor as the deterministic path (§41 Rule 6) -- the LLM never
gets a shortcut around validation/permission/platform checks.
"""

from __future__ import annotations

import asyncio

from sqlalchemy.orm import Session

from app.core.command_router import NEEDS_LLM, CommandRouter
from app.core.models import AssistantRequest, AssistantResponse
from app.core.permissions import RequesterContext
from app.core.tool_executor import ToolExecutor
from app.llm.ai_router import NO_PROVIDER_AVAILABLE_ERROR_TYPE, AIRouter
from app.llm.base import LLMRequest, LLMResult
from app.tools.base import Tool, ToolResult
from app.tools.registry import ToolRegistry

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

    def __init__(self, registry: ToolRegistry, db: Session | None = None) -> None:
        self.registry = registry
        self.router = CommandRouter(registry)
        self.executor = ToolExecutor(registry, db=db)
        self.ai_router = AIRouter(db=db)

    def handle(self, request: AssistantRequest) -> AssistantResponse:
        context = RequesterContext(
            user_id=request.user_id,
            platform=request.platform,
            confirmed=bool(request.metadata.get("confirmed", False)),
            override=bool(request.metadata.get("override", False)),
        )

        route_result = self.router.route(request.message)

        if route_result is NEEDS_LLM:
            return self._handle_needs_llm(request, context)

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
        # NOTE: passing the *entire* registered tool set to Gemini is temporary (§4/
        # file 05 scope) -- file 08 narrows this to a selective subset per request.
        llm_request = LLMRequest(
            message=request.message,
            context={"user_id": request.user_id, "platform": request.platform},
            tools=[_tool_to_schema(tool) for tool in self.registry.list(platform=request.platform)],
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
