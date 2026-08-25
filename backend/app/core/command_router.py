"""
Command router -- deterministic path only (§2, §11).

Tries to resolve a raw user message straight to a (tool_name, params) pair using exact
name/alias matches and a "<trigger> <remainder>" prefix match against every registered
alias -- single-word verbs ("open", "launch", "start") and short fixed phrases ("remind
me to", "set a timer for") both work the same way. No LLM is ever called here: when
nothing matches deterministically, `route()` returns the `NEEDS_LLM` sentinel and the
caller (`AssistantCore`, file 02 prompt 3) decides what happens next -- for now a
placeholder response; file 06 wires the real AI Router into that branch.

The "remind me to X [time phrase]" alias is the one special case: instead of dumping
the whole remainder into `create_task`'s `title` param like every other trigger, it
runs `app.tasks.service.split_title_and_due` (§41 Rule 6 doesn't apply here -- this is
still zero-LLM local parsing, same as `dateparser` inside `TaskService` itself) so
"remind me to submit my assignment tomorrow at 8pm" becomes
`title="submit my assignment", due="2026-08-26T20:00:00"` instead of file 03's naive
`title="submit my assignment tomorrow at 8pm"`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Final

from app.tasks.service import split_title_and_due
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

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _first_target_param(parameters: dict[str, Any] | None) -> tuple[str, dict[str, Any]] | None:
    """Best-effort (name, schema) of the parameter a trailing phrase (e.g. "vscode" in
    "open vscode") should be assigned to: the schema's first required property, falling
    back to its first declared property. None if the schema declares no properties.
    """
    schema = parameters or {}
    properties: dict[str, Any] = schema.get("properties") or {}
    required = schema.get("required") or []
    name = required[0] if required else (next(iter(properties)) if properties else None)
    if name is None:
        return None
    return name, (properties.get(name) or {})


def _coerce_remainder(remainder: str, param_schema: dict[str, Any]) -> Any:
    """Coerce a raw text remainder to the target param's declared JSON type.

    Handles only the shapes tool schemas in this project actually declare: plain
    strings pass through untouched; "integer"/"number" params get the first number
    found in the remainder (e.g. "set a timer for 10 minutes" -> remainder "10
    minutes" -> 10). Anything that doesn't contain a number is left as the original
    string so ToolExecutor's validator can surface a clear "wrong type" error instead
    of this silently swallowing a bad match.
    """
    expected = param_schema.get("type")
    if expected not in ("integer", "number"):
        return remainder
    match = _NUMBER_RE.search(remainder)
    if not match:
        return remainder
    text = match.group(0)
    return int(text) if expected == "integer" and "." not in text else float(text)


class CommandRouter:
    """Resolves a message to a tool call using only exact/alias/prefix matching --
    never an LLM.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def route(self, message: str) -> RouteResult:
        text = message.strip()
        if not text:
            return NEEDS_LLM

        # 1. Exact match: the whole message is a tool name or alias, e.g. "open_application"
        #    or "list tasks".
        tool = self.registry.get(text) or self.registry.get(text.lower())
        if tool is not None:
            return RouteResult(tool_name=tool.name, params={})

        # 2. Trigger-prefix match: "<trigger> <remainder>", where <trigger> is any
        #    registered tool name/alias -- a single word ("open vscode") or a short
        #    fixed phrase ("remind me to buy milk"). The longest matching trigger wins
        #    so a specific phrase ("set a timer for") beats a generic single word that
        #    happens to share its first token.
        lowered = text.lower()
        best_trigger: str | None = None
        for trigger in self.registry.all_triggers():
            trigger_lower = trigger.lower()
            if not lowered.startswith(trigger_lower + " "):
                continue
            if best_trigger is None or len(trigger) > len(best_trigger):
                best_trigger = trigger

        if best_trigger is not None:
            tool = self.registry.get(best_trigger)
            if tool is not None:
                remainder = text[len(best_trigger) :].strip()

                if tool.name == "create_task":
                    # "remind me to X [time phrase]" -- split any embedded date/time
                    # phrase out of the remainder with the same local parser
                    # TaskService uses, instead of file 03's naive "whole remainder
                    # becomes the title" behavior.
                    title, due = split_title_and_due(remainder)
                    params = {"title": title}
                    if due is not None:
                        params["due"] = due.isoformat()
                    return RouteResult(tool_name=tool.name, params=params)

                target = _first_target_param(tool.parameters)
                params = {}
                if target is not None:
                    param_name, param_schema = target
                    params = {param_name: _coerce_remainder(remainder, param_schema)}
                return RouteResult(tool_name=tool.name, params=params)

        # Nothing matched deterministically -- the caller decides what to do next.
        return NEEDS_LLM
