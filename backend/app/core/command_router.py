"""
Command router -- deterministic path only (§2, §11, file 08 prompt 1).

Tries to resolve a raw user message straight to a (tool_name, params) pair using exact
name/alias matches and a "<trigger> <remainder>" prefix match against every registered
alias -- single-word verbs ("open", "launch", "start") and short fixed phrases ("remind
me to", "set a timer for") both work the same way. No LLM is ever called here: when
nothing matches deterministically, `route()` returns a result classified
`CommandClassification.LLM_REQUIRED` and the caller (`AssistantCore`) decides what
happens next -- AIRouter (file 06) is wired into that branch.

Every `RouteResult` carries an explicit `classification` (§9, §41 Rule 4) instead of the
old `NEEDS_LLM` ad-hoc sentinel, so downstream code (and, eventually, the usage
dashboard from file 08) can report on *how* a message was resolved, not just whether a
tool was found:

  - `DETERMINISTIC` -- a plain exact/alias/prefix match, remainder assigned straight to
    the target param (with only type coercion, e.g. "set a timer for 10 minutes" -> 10).
  - `LOCAL_PARSE` -- resolved deterministically, but only after non-trivial local
    parsing of the remainder. The "remind me to X [time phrase]" alias is the one case
    today: instead of dumping the whole remainder into `create_task`'s `title` param
    like every other trigger, it runs `app.tasks.service.split_title_and_due` (§41 Rule
    6 doesn't apply here -- this is still zero-LLM local parsing, same as `dateparser`
    inside `TaskService` itself) so "remind me to submit my assignment tomorrow at 8pm"
    becomes `title="submit my assignment", due="2026-08-26T20:00:00"` instead of file
    03's naive `title="submit my assignment tomorrow at 8pm"`.
  - `CACHED` -- the resolved tool is `cacheable` (see `app.core.cache.ResponseCache`)
    and already has a fresh cached result for these exact params. This router only
    *labels* the route this way (by peeking at `ResponseCache.has()`); it never reads
    or serves the cached value itself -- `ToolExecutor` is what actually returns it
    with zero handler re-execution, deriving the identical answer from the same
    cache. The label exists so callers (and, eventually, the usage dashboard) can
    tell a cache hit apart from a fresh DETERMINISTIC/LOCAL_PARSE run.
  - `LLM_REQUIRED` -- nothing matched deterministically; the caller must consult
    AIRouter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.core.cache import ResponseCache, response_cache
from app.tasks.service import split_title_and_due
from app.tools.registry import ToolRegistry


class CommandClassification(Enum):
    """How a message was (or wasn't) resolved by `CommandRouter.route`. See the module
    docstring for what each member means.
    """

    DETERMINISTIC = "deterministic"
    LOCAL_PARSE = "local_parse"
    CACHED = "cached"
    LLM_REQUIRED = "llm_required"


@dataclass(frozen=True)
class RouteResult:
    """A routing outcome: `classification` says how it was produced; `tool_name` is
    `None` only for `CommandClassification.LLM_REQUIRED` (nothing matched
    deterministically). Compare `classification` against the `CommandClassification`
    members rather than checking `tool_name` for `None` directly.
    """

    tool_name: str | None
    params: dict[str, Any] = field(default_factory=dict)
    classification: CommandClassification = CommandClassification.LLM_REQUIRED


def _needs_llm() -> RouteResult:
    """Fresh "nothing matched deterministically" result -- callers switch on
    `.classification`, so (unlike the old `NEEDS_LLM` sentinel) there's no need for a
    shared singleton instance here.
    """
    return RouteResult(tool_name=None, params={}, classification=CommandClassification.LLM_REQUIRED)


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

    def __init__(self, registry: ToolRegistry, cache: ResponseCache | None = None) -> None:
        self.registry = registry
        # Same process-wide cache ToolExecutor reads/writes for real -- this router
        # only ever calls `.has()` on it (see `_classify` and the CACHED member of
        # `CommandClassification` above).
        self.cache = cache if cache is not None else response_cache

    def _classify(
        self, tool: Any, params: dict[str, Any], base: CommandClassification
    ) -> CommandClassification:
        """`base` (DETERMINISTIC/LOCAL_PARSE) unless `tool` is `cacheable` and the
        cache already holds a fresh result for these exact `params`, in which case
        this route is relabeled CACHED -- see the CACHED member's docstring above.
        """
        if getattr(tool, "cacheable", False) and self.cache.has(tool.name, params):
            return CommandClassification.CACHED
        return base

    def route(self, message: str) -> RouteResult:
        text = message.strip()
        if not text:
            return _needs_llm()

        # 1. Exact match: the whole message is a tool name or alias, e.g. "open_application"
        #    or "list tasks".
        tool = self.registry.get(text) or self.registry.get(text.lower())
        if tool is not None:
            return RouteResult(
                tool_name=tool.name,
                params={},
                classification=self._classify(tool, {}, CommandClassification.DETERMINISTIC),
            )

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
                    return RouteResult(
                        tool_name=tool.name,
                        params=params,
                        classification=self._classify(tool, params, CommandClassification.LOCAL_PARSE),
                    )

                target = _first_target_param(tool.parameters)
                params = {}
                if target is not None:
                    param_name, param_schema = target
                    params = {param_name: _coerce_remainder(remainder, param_schema)}
                return RouteResult(
                    tool_name=tool.name,
                    params=params,
                    classification=self._classify(tool, params, CommandClassification.DETERMINISTIC),
                )

        # Nothing matched deterministically -- the caller decides what to do next.
        return _needs_llm()
