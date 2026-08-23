"""
Assistant core orchestrator (§20).

AssistantCore.handle() is the single entrypoint every platform adapter (desktop, web,
discord, ...) calls -- CommandRouter resolves the incoming message deterministically,
ToolExecutor runs any resolved tool call through the full validate/permission/platform/
log pipeline, and the result is wrapped back into an AssistantResponse. Never duplicate
this orchestration logic per platform (§41 Rule 7); adapters only translate in and out.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.command_router import NEEDS_LLM, CommandRouter
from app.core.models import AssistantRequest, AssistantResponse
from app.core.permissions import RequesterContext
from app.core.tool_executor import ToolExecutor
from app.tools.base import ToolResult
from app.tools.registry import ToolRegistry


def _success_text(tool_name: str, result: ToolResult) -> str:
    if result.data and "message" in result.data:
        return str(result.data["message"])
    return f"Done: '{tool_name}' executed successfully."


class AssistantCore:
    """Routes a request to a deterministic tool call, or explains that reasoning-based
    requests aren't supported yet.
    """

    def __init__(self, registry: ToolRegistry, db: Session | None = None) -> None:
        self.registry = registry
        self.router = CommandRouter(registry)
        self.executor = ToolExecutor(registry, db=db)

    def handle(self, request: AssistantRequest) -> AssistantResponse:
        route_result = self.router.route(request.message)

        if route_result is NEEDS_LLM:
            # TODO(file 06 - AI Router, md-files/06-ai-router-and-quota-manager.md): once
            # the AI Router exists, NEEDS_LLM should be handed off to it instead of
            # returning this placeholder response.
            return AssistantResponse(
                text=(
                    "I don't have a deterministic match for that yet, and "
                    "reasoning-based requests aren't supported until the AI Router "
                    "is wired up."
                ),
                used_llm=False,
            )

        context = RequesterContext(
            user_id=request.user_id,
            platform=request.platform,
            confirmed=bool(request.metadata.get("confirmed", False)),
            override=bool(request.metadata.get("override", False)),
        )
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
