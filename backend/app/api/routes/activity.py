"""
Recent activity feed route.

`GET /api/activity` merges the two audit trails that already exist independently --
`tool_execution_logs` (every tool call ToolExecutor runs, §41 Rule 6, written by
`app.core.tool_executor._log`) and `llm_usage` (every LLM call, written by each
provider's `_to_result`, see `app.llm.gemini`/`app.llm.ollama`) -- into one
timestamp-sorted feed. Neither table changes shape for this; a routine/scheduler/timer
run is just a tool call whose `RequesterContext.scope` was something other than
"default" (see `tool_executor.py`'s `_log`, which now persists `scope` inside the
logged JSON payload precisely so this route can surface it), so "routines called" falls
out of the existing tool-call rows rather than needing its own table.

Read-only, like `app.api.routes.llm_usage`: this route reports what already happened,
it never triggers or replays anything. Same auth boundary as every other web-reachable
route in this app (§34) -- router-level `get_current_user`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.database.database import get_db
from app.database.models import LLMUsage, ToolExecutionLog

router = APIRouter(tags=["activity"], dependencies=[Depends(get_current_user)])

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200

# Scopes RequesterContext sets for anything other than a direct user/LLM-issued call
# (see app/routines/engine.py, app/routines/scheduler.py, app/tasks/scheduler.py,
# app/tools/timers.py) -- surfaced to the frontend as a human label instead of the raw
# scope string.
_SCOPE_LABELS = {
    "routine": "Routine (manual run)",
    "scheduled_routine": "Routine (scheduled)",
    "scheduler": "Task reminder",
    "timer": "Timer",
}


class ActivityItem(BaseModel):
    id: str
    type: Literal["tool_call", "llm_call"]
    timestamp: datetime
    status: Literal["ok", "error"]
    summary: str
    # Populated for type == "tool_call".
    tool_name: str | None = None
    scope_label: str | None = None
    platform: str | None = None
    error: str | None = None
    # Populated for type == "llm_call".
    provider: str | None = None
    model: str | None = None
    request_tokens: int | None = None
    response_tokens: int | None = None
    fallback_used: bool | None = None
    latency: int | None = None


class ActivityResponse(BaseModel):
    generated_at: str
    items: list[ActivityItem]


def _parse_payload(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _tool_item(row: ToolExecutionLog) -> ActivityItem:
    payload = _parse_payload(row.result)
    scope = payload.get("scope")
    return ActivityItem(
        id=f"tool-{row.id}",
        type="tool_call",
        timestamp=row.executed_at,
        status="ok" if row.status == "ok" else "error",
        summary=row.tool_name,
        tool_name=row.tool_name,
        scope_label=_SCOPE_LABELS.get(scope),
        platform=payload.get("platform"),
        error=payload.get("error"),
    )


def _llm_item(row: LLMUsage) -> ActivityItem:
    return ActivityItem(
        id=f"llm-{row.id}",
        type="llm_call",
        timestamp=row.timestamp,
        status="ok" if row.status == "SUCCESS" else "error",
        summary=f"{row.provider} · {row.model}",
        provider=row.provider,
        model=row.model,
        request_tokens=row.request_tokens,
        response_tokens=row.response_tokens,
        fallback_used=row.fallback_used,
        latency=row.latency,
        error=row.error_type,
    )


@router.get("/activity", response_model=ActivityResponse)
def get_activity(
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    db: Session = Depends(get_db),
) -> ActivityResponse:
    # Pull `limit` rows from each source table before merging -- the true most-recent
    # `limit` combined items can never need more than `limit` from either individual
    # table, and this keeps the query itself index-friendly (ORDER BY + LIMIT on each
    # table's own timestamp column) rather than joining/unioning in SQL.
    tool_rows = (
        db.query(ToolExecutionLog).order_by(ToolExecutionLog.executed_at.desc()).limit(limit).all()
    )
    llm_rows = db.query(LLMUsage).order_by(LLMUsage.timestamp.desc()).limit(limit).all()

    items = [_tool_item(row) for row in tool_rows] + [_llm_item(row) for row in llm_rows]
    items.sort(key=lambda item: item.timestamp, reverse=True)

    return ActivityResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        items=items[:limit],
    )
