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
    # Optional (file 08 prompt 4, see `app.core.cache.ResponseCache`'s docstring for
    # exactly what qualifies) -- most tools don't declare this at all, and
    # ToolExecutor/CommandRouter read it via `getattr(tool, "cacheable", False)`, so
    # omitting it is equivalent to `cacheable = False`. Only set `cacheable = True` on
    # a tool whose result is safe/side-effect-free AND deterministic/static enough to
    # replay for a short TTL -- never on anything that mutates state or reads
    # user-specific-and-mutable data.

    def handler(self, **kwargs: Any) -> ToolResult: ...
