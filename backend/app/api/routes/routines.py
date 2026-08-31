"""
Routine routes (Routine Dashboard backend).

Thin HTTP wrappers around `RoutineRegistry` (CRUD on a routine's persisted steps,
`app/routines/registry.py`) and `RoutineEngine` (running one, `app/routines/engine.py`)
-- same split as `app/api/routes/assistant.py` and `app/api/routes/tasks.py` (§41 Rule
7): every route here only validates the request, calls into the registry/engine, and
shapes the response. Step-order persistence, tool-name/params storage, and the actual
step-by-step `ToolExecutor` dispatch (§41 Rule 6) all stay inside those two modules;
nothing here re-implements them.

`GET /tools` is included here (not its own route module) because its only consumer is
the Routine Dashboard's step editor, which needs to know what tools exist to build a
step against -- it's a read-only projection of `ToolRegistry.list()`, not a new concept.

Every route here requires a valid bearer token (§34, file 12 prompt 1, router-level
`get_current_user` dependency) -- the Routine Dashboard is reachable over the web, not
gated by `app.api.local_only`'s loopback check. This also closes the gap
docs/security.md flagged in file 11: `POST /routines/{name}/run` previously had no
boundary at all (`RoutineEngine.run()` defaults to `platform="desktop"` when the route
supplies none), so an unauthenticated remote caller could run a routine's full desktop
tool chain. It's authenticated now like every other route in this file, and (file 12
prompt 2) `run_routine` no longer relies on that `platform="desktop"` default at all --
it builds a real per-request `RequesterContext` whose `platform` is inferred from
`app.api.local_only.is_local_client`, so a routine step that's desktop-only
(`platforms=["desktop"]`) is actually rejected by `ToolExecutor` with the §22-style
explanation when "Run now" is clicked from a non-local browser, instead of either
silently failing or being attempted for real against this process's host.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_tool_registry
from app.api.local_only import is_local_client
from app.core.permissions import RequesterContext
from app.database.database import get_db
from app.database.models import User
from app.routines.engine import RoutineEngine
from app.routines.registry import RoutineData, RoutineRegistry
from app.tools.registry import ToolRegistry

router = APIRouter(tags=["routines"], dependencies=[Depends(get_current_user)])


class RoutineStepIn(BaseModel):
    tool_name: str
    params: dict[str, Any] = {}


class RoutineStepOut(BaseModel):
    tool_name: str
    params: dict[str, Any]


class RoutineOut(BaseModel):
    id: int
    name: str
    trigger_type: str
    enabled: bool
    steps: list[RoutineStepOut]


class RoutineCreate(BaseModel):
    name: str
    steps: list[RoutineStepIn] = []


class RoutineStepsUpdate(BaseModel):
    """Step editor writes are whole-list replacements (add/remove/reorder are all just
    "here is the new ordered list"), matching `RoutineRegistry.update_routine`.
    """

    steps: list[RoutineStepIn]


class RoutineUpdate(BaseModel):
    """Partial update for a routine's identity/status -- both fields optional so the
    "Stop"/"Start" toggle (`enabled` only) and the name editor (`name` only, or both
    at once) share the same `PATCH /routines/{name}` route instead of two competing
    endpoints for what's really one "update this routine" concept.
    """

    name: str | None = None
    enabled: bool | None = None


class RoutineRunResult(BaseModel):
    success: bool
    message: str | None = None
    error: str | None = None
    steps: list[dict[str, Any]] = []


class ToolOut(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]
    platforms: list[str]


def _serialize_routine(routine: RoutineData) -> dict[str, Any]:
    return {
        "id": routine.id,
        "name": routine.name,
        "trigger_type": routine.trigger_type,
        "enabled": routine.enabled,
        "steps": [{"tool_name": s.tool_name, "params": s.params} for s in routine.steps],
    }


def _as_step_tuples(steps: list[RoutineStepIn], registry: ToolRegistry) -> list[tuple[str, dict[str, Any]]]:
    """Reject steps referencing a tool the registry doesn't know about -- this is
    request-shape validation (does the referenced tool exist at all), not a re-check of
    anything `ToolExecutor` already does (permission/platform/param-schema checks still
    happen there, at run time, same as every other caller).
    """
    unknown = [s.tool_name for s in steps if registry.get(s.tool_name) is None]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown tool(s): {', '.join(unknown)}.")
    return [(s.tool_name, s.params) for s in steps]


@router.get("/tools", response_model=list[ToolOut])
def list_tools(registry: ToolRegistry = Depends(get_tool_registry)) -> list[dict[str, Any]]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
            "platforms": tool.platforms,
        }
        for tool in registry.list()
    ]


@router.get("/routines", response_model=list[RoutineOut])
def list_routines(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [_serialize_routine(r) for r in RoutineRegistry(db).list_routines()]


@router.get("/routines/{name}", response_model=RoutineOut)
def get_routine(name: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    routine = RoutineRegistry(db).get_routine(name)
    if routine is None:
        raise HTTPException(status_code=404, detail=f"No routine named '{name}'.")
    return _serialize_routine(routine)


@router.post("/routines", response_model=RoutineOut, status_code=201)
def create_routine(
    payload: RoutineCreate,
    db: Session = Depends(get_db),
    tool_registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    steps = _as_step_tuples(payload.steps, tool_registry)
    try:
        routine = RoutineRegistry(db).create_routine(payload.name, steps)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _serialize_routine(routine)


@router.put("/routines/{name}/steps", response_model=RoutineOut)
def update_routine_steps(
    name: str,
    payload: RoutineStepsUpdate,
    db: Session = Depends(get_db),
    tool_registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    steps = _as_step_tuples(payload.steps, tool_registry)
    routine = RoutineRegistry(db).update_routine(name, steps)
    if routine is None:
        raise HTTPException(status_code=404, detail=f"No routine named '{name}'.")
    return _serialize_routine(routine)


@router.patch("/routines/{name}", response_model=RoutineOut)
def update_routine(
    name: str,
    payload: RoutineUpdate,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """The Routine Dashboard's "Stop"/"Start" toggle (`enabled`) and its name editor
    (`name`) both land here -- either field, or both in one call. `RoutineEngine.run()`
    already refuses to run a disabled routine (both from this file's `/run` route and
    from `RunRoutineTool`/`RoutineScheduler`), so `enabled` is the missing piece that
    lets a routine actually be taken offline from the web instead of only ever being
    deleted outright; `name` is the equivalent for the "Rename" affordance, going
    through `RoutineRegistry.rename_routine`'s same duplicate-name check
    `create_routine` enforces.
    """
    registry = RoutineRegistry(db)
    current_name = name
    if payload.name is not None:
        try:
            routine = registry.rename_routine(name, payload.name)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if routine is None:
            raise HTTPException(status_code=404, detail=f"No routine named '{name}'.")
        current_name = routine.name
    if payload.enabled is not None:
        routine = registry.set_enabled(current_name, payload.enabled)
        if routine is None:
            raise HTTPException(status_code=404, detail=f"No routine named '{current_name}'.")
    elif payload.name is None:
        routine = registry.get_routine(current_name)
        if routine is None:
            raise HTTPException(status_code=404, detail=f"No routine named '{name}'.")
    return _serialize_routine(routine)


@router.delete("/routines/{name}", status_code=204, response_model=None)
def delete_routine(name: str, db: Session = Depends(get_db)) -> None:
    if not RoutineRegistry(db).delete_routine(name):
        raise HTTPException(status_code=404, detail=f"No routine named '{name}'.")


@router.post("/routines/{name}/run", response_model=RoutineRunResult)
def run_routine(
    name: str,
    http_request: Request,
    tool_registry: ToolRegistry = Depends(get_tool_registry),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    # RoutineEngine.run() opens its own short-lived session internally (see its
    # docstring) rather than reusing the request's `db` -- same as every other caller
    # (RunRoutineTool, RoutineScheduler).
    #
    # This route's request body carries no explicit `platform` the way
    # /assistant/message's does, so the effective platform is inferred from where the
    # request actually came from (§22/§23, file 12 prompt 2) rather than
    # RoutineEngine.run()'s old unconditional `platform="desktop"` default -- a routine
    # containing a desktop-only step (e.g. open_application) must actually be blocked
    # by ToolExecutor's platform check when "Run now" is clicked from a *remote*
    # browser, not silently attempted against whatever machine this backend happens to
    # be running on. A same-machine (loopback) caller -- the Routine Dashboard running
    # against a local desktop deployment -- keeps today's full desktop capability.
    context = RequesterContext(
        user_id=current_user.username,
        platform="desktop" if is_local_client(http_request) else "web",
        scope="routine",
    )
    result = RoutineEngine(tool_registry).run(name, context=context)
    data = result.data or {}
    return {
        "success": result.success,
        "message": data.get("message"),
        "error": result.error,
        "steps": data.get("steps", []),
    }
