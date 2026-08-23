"""
Permission system (§19).

PermissionChecker.check() is the single gate every tool execution must pass through
before ToolExecutor calls tool.handler(...) (§41 Rule 6, wired up properly in file 02b's
ToolExecutor). The policy here is deliberately simple for this phase:

  - SAFE        -> always allowed.
  - CONFIRM     -> allowed only if the caller set requester_context.confirmed = True.
  - RESTRICTED  -> denied unless requester_context.override = True.
  - BLOCKED     -> denied unless requester_context.override = True.

`confirmed` and `override` must be set by a human-facing confirmation step (UI prompt,
explicit config), never derived from LLM output -- otherwise the model could talk its way
past a permission gate, which is exactly what this system exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from app.database.models import Permission


class PermissionLevel(str, Enum):
    SAFE = "safe"
    CONFIRM = "confirm"
    RESTRICTED = "restricted"
    BLOCKED = "blocked"


@dataclass
class RequesterContext:
    """Everything PermissionChecker needs to know about the caller of a tool."""

    user_id: str | None = None
    platform: str | None = None
    confirmed: bool = False
    override: bool = False
    scope: str = "default"


@dataclass
class PermissionDecision:
    allowed: bool
    level: PermissionLevel
    reason: str


class PermissionChecker:
    """Evaluates a tool's PermissionLevel against a RequesterContext.

    Pass a `db` Session to have every decision persisted as a row in the existing
    `permissions` table (tool_name, scope, allowed); omit it (e.g. in unit tests) to
    check policy without touching the database.
    """

    def __init__(self, db: Session | None = None) -> None:
        self.db = db

    def check(self, tool: Any, requester_context: RequesterContext) -> PermissionDecision:
        level = tool.permission

        if level == PermissionLevel.SAFE:
            decision = PermissionDecision(True, level, "SAFE tools run unconditionally.")
        elif level == PermissionLevel.CONFIRM:
            if requester_context.confirmed:
                decision = PermissionDecision(
                    True, level, "CONFIRM tool executed with confirmed=True."
                )
            else:
                decision = PermissionDecision(
                    False, level, "CONFIRM tool requires confirmed=True."
                )
        elif level in (PermissionLevel.RESTRICTED, PermissionLevel.BLOCKED):
            if requester_context.override:
                decision = PermissionDecision(
                    True, level, f"{level.value} tool allowed via explicit override."
                )
            else:
                decision = PermissionDecision(
                    False, level, f"{level.value} tools are denied by default."
                )
        else:  # pragma: no cover - defensive, PermissionLevel is exhaustive
            decision = PermissionDecision(False, level, f"Unknown permission level: {level!r}")

        self._persist(tool_name=getattr(tool, "name", "unknown"), context=requester_context, decision=decision)
        return decision

    def _persist(self, *, tool_name: str, context: RequesterContext, decision: PermissionDecision) -> None:
        if self.db is None:
            return
        self.db.add(Permission(tool_name=tool_name, scope=context.scope, allowed=decision.allowed))
        self.db.commit()
