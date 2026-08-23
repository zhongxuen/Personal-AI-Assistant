"""
Command router -- deterministic path only (§2, §11).

Tries to resolve a raw user message straight to a (tool_name, params) pair using exact
name/alias matches and a small "<verb> <remainder>" regex pattern (e.g. "open", "launch",
"start" -> whatever tool is registered under those aliases). No LLM is ever called here:
when nothing matches deterministically, `route()` returns the `NEEDS_LLM` sentinel and
the caller (`AssistantCore`, file 02 prompt 3) decides what happens next -- for now a
placeholder response; file 06 wires the real AI Router into that branch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Final

from app.tools.registry import ToolRegistry


@dataclass(frozen=True)
class RouteResult:
    """A routing outcome: a resolved tool call, or (via the `NEEDS_LLM` sentinel below)
    "nothing matched deterministically". Don't construct an empty RouteResult yourself
    to signal "no match" -- compare against `NEEDS_LLM` with `is` instead.
    """

    tool_name: str | None
    params: dict[str, Any] = field(default_factory=dict)


NEEDS_LLM: Final[RouteResult] = RouteResult(tool_name=None, params={})

_VERB_REMAINDER_RE = re.compile(r"^(\S+)\s+(.+)$", re.DOTALL)


def _first_target_param(parameters: dict[str, Any] | None) -> str | None:
    """Best-effort name of the parameter a trailing phrase (e.g. "vscode" in "open
    vscode") should be assigned to: the schema's first required property, falling back
    to its first declared property. None if the schema declares no properties at all.
    """
    schema = parameters or {}
    required = schema.get("required") or []
    if required:
        return required[0]
    properties = schema.get("properties") or {}
    if properties:
        return next(iter(properties))
    return None


class CommandRouter:
    """Resolves a message to a tool call using only exact/alias/regex matching --
    never an LLM.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def route(self, message: str) -> RouteResult:
        text = message.strip()
        if not text:
            return NEEDS_LLM

        # 1. Exact match: the whole message is a tool name or alias, e.g. "open_application".
        tool = self.registry.get(text) or self.registry.get(text.lower())
        if tool is not None:
            return RouteResult(tool_name=tool.name, params={})

        # 2. Verb-prefix match: "<alias> <remainder>", e.g. "open vscode" -> open_application.
        match = _VERB_REMAINDER_RE.match(text)
        if match:
            verb, remainder = match.group(1), match.group(2).strip()
            tool = self.registry.get(verb) or self.registry.get(verb.lower())
            if tool is not None:
                param_name = _first_target_param(tool.parameters)
                params = {param_name: remainder} if param_name else {}
                return RouteResult(tool_name=tool.name, params=params)

        # Nothing matched deterministically -- the caller decides what to do next.
        return NEEDS_LLM
