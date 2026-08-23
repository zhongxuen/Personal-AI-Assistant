"""
Shared FastAPI dependencies.

`get_tool_registry` hands out the single process-wide ToolRegistry so every request
routes against the same set of registered tools. It's empty until tools register
themselves against it (starting file 03, md-files/03-basic-deterministic-tools.md) --
until then every message falls through to NEEDS_LLM.
"""

from __future__ import annotations

from app.tools.registry import ToolRegistry

_registry = ToolRegistry()


def get_tool_registry() -> ToolRegistry:
    return _registry
