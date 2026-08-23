"""
Tool interface (§18).

Every tool the assistant can call satisfies this Protocol. ToolRegistry (file 02b) stores
instances of it; ToolExecutor is the only thing allowed to call `.handler(...)` directly
(§41 Rule 6) -- it validates params against `parameters`, checks `permission` via
PermissionChecker, and checks `platforms` for the requesting platform first.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from app.core.permissions import PermissionLevel


class ToolResult(BaseModel):
    success: bool
    data: dict | None = None
    error: str | None = None


@runtime_checkable
class Tool(Protocol):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema -- used for validation and later LLM tool-calling
    permission: PermissionLevel
    platforms: list[str]  # §22 capability declaration, e.g. ["desktop", "web", "discord"]
    requires_confirmation: bool

    def handler(self, **kwargs: Any) -> ToolResult: ...
