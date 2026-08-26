"""
Shared FastAPI dependencies.

`get_tool_registry` hands out the single process-wide ToolRegistry so every request
routes against the same set of registered tools. It's empty until tools register
themselves against it (starting file 03, md-files/03-basic-deterministic-tools.md) --
until then every message falls through to classification LLM_REQUIRED.

`get_health_manager` hands out a single process-wide `HealthManager` (§6, file 06) the
same way -- `AssistantCore` previously built a brand new one per request (each
`AssistantCore(...)` construction in `app.api.routes.assistant` implicitly created a
fresh `AIRouter`, which defaults to a fresh `HealthManager`), so a provider's tracked
cooldowns/consecutive-error counts never actually survived past the request that
recorded them. Routing every request through this one instance instead (see
`app.api.routes.assistant`) makes that health tracking real across requests, and lets
`app.api.routes.llm_usage` report the same live state `AIRouter` is actually acting on
-- rather than a second, always-fresh-and-therefore-always-AVAILABLE instance.
"""

from __future__ import annotations

from app.llm.health import HealthManager
from app.tools.registry import ToolRegistry

_registry = ToolRegistry()
_health_manager = HealthManager()


def get_tool_registry() -> ToolRegistry:
    return _registry


def get_health_manager() -> HealthManager:
    return _health_manager
