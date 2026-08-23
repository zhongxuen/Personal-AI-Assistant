"""
Tool registry (§10 command registry + §18).

Holds every `Tool` instance the assistant knows about. This is a plain in-memory
lookup table -- no persistence, no LLM awareness. `CommandRouter` (file 02b) reads
aliases from here to resolve deterministic phrasings; `ToolExecutor` (file 02b) reads
tools from here to run them. Neither of those is allowed to mutate the registry.
"""

from __future__ import annotations

from app.tools.base import Tool


class ToolRegistry:
    """In-memory store of registered tools, keyed by canonical name and alias."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}  # canonical name -> tool
        self._aliases: dict[str, str] = {}  # alias -> canonical name

    def register(self, tool: Tool, aliases: list[str] | None = None) -> None:
        """Register a tool under its canonical `tool.name`, plus any extra aliases.

        Raises ValueError on a name/alias collision -- silently overwriting a tool
        would let two different phrasings quietly start meaning different things.
        """
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered.")
        if tool.name in self._aliases:
            raise ValueError(f"'{tool.name}' is already registered as an alias of another tool.")

        self._tools[tool.name] = tool
        for alias in aliases or []:
            if alias in self._tools or alias in self._aliases:
                raise ValueError(f"Alias '{alias}' conflicts with an existing tool or alias.")
            self._aliases[alias] = tool.name

    def get(self, name_or_alias: str) -> Tool | None:
        """Look up a tool by its canonical name or by a registered alias."""
        canonical = self._aliases.get(name_or_alias, name_or_alias)
        return self._tools.get(canonical)

    def list(self, platform: str | None = None, subset: list[str] | None = None) -> list[Tool]:
        """List registered tools, optionally filtered.

        `platform` keeps only tools that declare support for it (§22). `subset` keeps
        only tools whose canonical name appears in the given list -- unused until
        dynamic tool loading (file 08), but the filter hook exists now as requested.
        """
        tools = list(self._tools.values())
        if platform is not None:
            tools = [tool for tool in tools if platform in tool.platforms]
        if subset is not None:
            tools = [tool for tool in tools if tool.name in subset]
        return tools

    def aliases_for(self, name: str) -> list[str]:
        """All aliases registered for a canonical tool name (used by CommandRouter)."""
        return [alias for alias, canonical in self._aliases.items() if canonical == name]
