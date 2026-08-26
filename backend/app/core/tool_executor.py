"""
Tool executor (§18-19, §41 Rule 6).

`ToolExecutor.execute()` is the single choke point every tool call must pass through:

    1. look up the tool in the registry
    2. validate params against the tool's JSON schema
    3. check PermissionChecker
    4. check the requesting platform is one the tool supports (§22)
    5. call tool.handler(**params) -- or reuse a cached result for a `cacheable`
       tool (file 08 prompt 4, see `app.core.cache`), which skips this call entirely
    6. write a row to tool_execution_logs
    7. return the ToolResult

No other code path is allowed to call `tool.handler(...)` directly, and none of the
above steps may be skipped -- even an early failure (unknown tool, bad params, denied
permission, unsupported platform) still gets logged before returning, so every attempt
leaves an audit trail. Step 5's cache short-circuit is the one deliberate exception to
"the handler runs every time": it only ever applies to a tool that opted in with
`cacheable = True` (see `app.core.cache.ResponseCache`'s docstring for exactly what
qualifies), and even then only after every check above it already passed -- caching
never bypasses validation, permission, or platform checks, only the handler call.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.cache import ResponseCache, response_cache
from app.core.permissions import PermissionChecker, RequesterContext
from app.database.models import ToolExecutionLog
from app.tools.base import ToolResult
from app.tools.registry import ToolRegistry

logger = logging.getLogger("jarvis.tool_executor")


def _matches_json_type(value: Any, json_type: str) -> bool:
    """Whether `value` satisfies a single JSON Schema `type` keyword value."""
    if json_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if json_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if json_type == "boolean":
        return isinstance(value, bool)
    if json_type == "string":
        return isinstance(value, str)
    if json_type == "array":
        return isinstance(value, list)
    if json_type == "object":
        return isinstance(value, dict)
    if json_type == "null":
        return value is None
    return True  # unknown type keyword -- don't block execution over it


def _validate_params(params: dict[str, Any], schema: dict[str, Any] | None) -> str | None:
    """Minimal JSON Schema (subset) validator for tool parameters.

    Supports what tool schemas in this project actually declare: top-level
    "properties", "required", per-property "type", and "additionalProperties". Swap
    for the `jsonschema` package if a later phase needs the full spec ($ref, nested
    schemas, format validators, ...) -- not adding that dependency yet is deliberate
    (§41, no over-engineering).
    """
    if not isinstance(params, dict):
        return "Parameters must be an object."

    schema = schema or {}
    properties: dict[str, Any] = schema.get("properties") or {}
    required: list[str] = schema.get("required") or []

    missing = [name for name in required if name not in params]
    if missing:
        return f"Missing required parameter(s): {', '.join(missing)}."

    if schema.get("additionalProperties") is False:
        unknown = [name for name in params if name not in properties]
        if unknown:
            return f"Unknown parameter(s): {', '.join(unknown)}."

    for name, value in params.items():
        prop_schema = properties.get(name)
        if not prop_schema:
            continue
        expected = prop_schema.get("type")
        if expected is None:
            continue
        expected_types = expected if isinstance(expected, list) else [expected]
        if not any(_matches_json_type(value, t) for t in expected_types):
            return f"Parameter '{name}' must be of type {expected!r}, got {type(value).__name__}."

    return None


class ToolExecutor:
    """Validates, authorizes, and runs a registered tool, logging every attempt."""

    def __init__(
        self,
        registry: ToolRegistry,
        permission_checker: PermissionChecker | None = None,
        db: Session | None = None,
        cache: ResponseCache | None = None,
    ) -> None:
        self.registry = registry
        self.db = db
        # Share the same db session so permission decisions and execution logs land
        # in the same transaction/connection, unless the caller wired up its own checker.
        self.permission_checker = permission_checker or PermissionChecker(db=db)
        # Process-wide by default (same instance CommandRouter peeks at) -- a caller
        # can still inject its own for test isolation.
        self.cache = cache if cache is not None else response_cache

    def execute(
        self, tool_name: str, params: dict[str, Any] | None, context: RequesterContext
    ) -> ToolResult:
        params = params or {}

        # 1. Look up the tool.
        tool = self.registry.get(tool_name)
        if tool is None:
            return self._finish(
                tool_name, params, context, ToolResult(success=False, error=f"Unknown tool: '{tool_name}'.")
            )

        # 2. Validate params against the tool's JSON schema.
        schema_error = _validate_params(params, tool.parameters)
        if schema_error is not None:
            return self._finish(tool.name, params, context, ToolResult(success=False, error=schema_error))

        # 3. Permission check -- the LLM must never be able to bypass this.
        decision = self.permission_checker.check(tool, context)
        if not decision.allowed:
            return self._finish(
                tool.name,
                params,
                context,
                ToolResult(success=False, error=f"Permission denied: {decision.reason}"),
            )

        # 4. Platform capability check (§22).
        if context.platform not in tool.platforms:
            return self._finish(
                tool.name,
                params,
                context,
                ToolResult(success=False, error=f"This action isn't available on {context.platform}."),
            )

        # 5. Execute the tool's handler -- unless it's `cacheable` and a fresh cached
        #    result already exists, in which case reuse that and skip the handler
        #    entirely (file 08 prompt 4: zero re-execution on a cache hit).
        cacheable = bool(getattr(tool, "cacheable", False))
        cached_result = self.cache.get(tool.name, params) if cacheable else None

        if cached_result is not None:
            result = cached_result
        else:
            try:
                result = tool.handler(**params)
            except Exception as exc:  # a misbehaving handler must not crash the executor
                logger.exception("Tool '%s' raised during execution.", tool.name)
                result = ToolResult(success=False, error=f"Tool '{tool.name}' raised an error: {exc}")

            if cacheable and result.success:
                # Only cache a successful result -- a failure should be retried on
                # the very next call, not replayed from cache until it expires.
                self.cache.set(tool.name, params, result)

        # 6 & 7. Log the attempt and return its result.
        return self._finish(tool.name, params, context, result)

    def _finish(
        self,
        tool_name: str,
        params: dict[str, Any],
        context: RequesterContext,
        result: ToolResult,
    ) -> ToolResult:
        self._log(tool_name, params, result, context)
        return result

    def _log(
        self, tool_name: str, params: dict[str, Any], result: ToolResult, context: RequesterContext
    ) -> None:
        if self.db is None:
            return
        payload = {
            "success": result.success,
            "data": result.data,
            "error": result.error,
            "platform": context.platform,
        }
        self.db.add(
            ToolExecutionLog(
                tool_name=tool_name,
                params=json.dumps(params, default=str),
                result=json.dumps(payload, default=str),
                status="ok" if result.success else "error",
            )
        )
        self.db.commit()
