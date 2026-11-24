"""GET /api/schedule, POST/PUT/DELETE /api/schedule/{task_id},
POST /api/schedule/validate-cron, POST /api/schedule/install-timers,
POST /api/schedule/remove-timers."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.server import scheduler
from app.shared import systemd_scope
from app.shared.platform import has_systemd
from app.web.deps import get_state, require_session
from app.web.jobs import Job, JobConflictError, conflict_response
from app.web.schemas import CronValidateRequest, ScheduleTaskCreateRequest, ScheduleTaskUpdateRequest
from app.web.state import WebState

router = APIRouter(prefix="/api/schedule", tags=["scheduler"], dependencies=[Depends(require_session)])


async def _require_privileged_ops(state: WebState) -> None:
    """Scope-aware gate: no sudo prompt (system) / reachable user bus (user)."""
    ok, reason = await asyncio.to_thread(
        systemd_scope.privileged_ops_available, state.config
    )
    if not ok:
        raise HTTPException(status_code=409, detail=reason)


def _cron_description(expr: str) -> str:
    try:
        from cron_descriptor import get_description

        return get_description(expr)
    except Exception:
        return expr


@router.get("")
async def get_schedule(state: WebState = Depends(get_state)) -> dict:
    config = state.config
    tasks = await asyncio.to_thread(scheduler.load_tasks, config)
    # Read from the unit files, never systemctl or sudo: this is a page load.
    # Empty where there is nothing to read (Windows, no systemd) — unknown.
    install_state, orphans = await asyncio.to_thread(scheduler.timer_install_state, config)
    return {
        "tasks": [t.to_dict() | {"description": t.description, "next_run_display": t.next_run_display} for t in tasks],
        "presets": scheduler.SCHEDULE_PRESETS,
        "install_state": install_state,
        "orphans": orphans,
        # Cron times are the server's local wall clock (scheduler.cron_now).
        "time_zone": scheduler.cron_time_zone(),
    }


@router.post("")
async def create_task(body: ScheduleTaskCreateRequest, state: WebState = Depends(get_state)) -> dict:
    task = scheduler.ScheduledTask(
        id=body.id, task_type=body.task_type, cron=body.cron, enabled=body.enabled, options=body.options
    )
    try:
        await state.mutate_config(lambda cfg: scheduler.apply_add_task(cfg, task))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    await state.hub.broadcast({"type": "schedule_changed"})
    return task.to_dict()


@router.put("/{task_id}")
async def update_task(task_id: str, body: ScheduleTaskUpdateRequest, state: WebState = Depends(get_state)) -> dict:
    kwargs = {k: v for k, v in body.model_dump().items() if v is not None}
    updated: scheduler.ScheduledTask | None = None

    def _apply(cfg) -> None:
        nonlocal updated
        updated = scheduler.apply_update_task(cfg, task_id, **kwargs)
        if updated is None:
            # Raised inside mutate_config, so an unknown id saves nothing.
            raise HTTPException(status_code=404, detail=f"No scheduled task with id {task_id!r}")

    try:
        await state.mutate_config(_apply)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    await state.hub.broadcast({"type": "schedule_changed"})
    return updated.to_dict()


@router.delete("/{task_id}")
async def delete_task(task_id: str, state: WebState = Depends(get_state)) -> dict:
    # 404 before mutating so we don't persist a no-op write.
    if await asyncio.to_thread(scheduler.get_task, state.config, task_id) is None:
        raise HTTPException(status_code=404, detail=f"No scheduled task with id {task_id!r}")
    await state.mutate_config(lambda cfg: scheduler.apply_remove_task(cfg, task_id))
    await state.hub.broadcast({"type": "schedule_changed"})
    return {"ok": True}


@router.post("/validate-cron")
async def validate_cron(body: CronValidateRequest) -> dict:
    # `reason` is what a save would be refused with, so the page can say why
    # before the user tries (plans/68 B24).
    reason = scheduler.cron_problem(body.expr)
    valid = reason is None
    return {
        "valid": valid,
        "description": _cron_description(body.expr) if valid else None,
        "reason": reason,
    }


@router.get("/info")
async def schedule_info(state: WebState = Depends(get_state)) -> dict:
    ok, reason = await asyncio.to_thread(
        systemd_scope.privileged_ops_available, state.config
    )
    scope = systemd_scope.get_scope(state.config)
    return {
        "has_systemd": has_systemd(),
        "scope": scope.name,
        "privileged_ops_ok": ok,
        "privileged_ops_reason": reason,
        # Legacy key kept for older frontends; prefer privileged_ops_ok.
        "sudo_available": ok,
    }


@router.post("/install-timers")
async def install_timers(state: WebState = Depends(get_state)):
    await _require_privileged_ops(state)

    config = state.config

    async def _job_coro(job: Job) -> dict:
        installed = await scheduler.install_timers_async(config, log=job.log_line)
        return {"installed": installed}

    try:
        job = state.jobs.start("Install timers", "timers_install", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}


@router.post("/remove-timers")
async def remove_timers(state: WebState = Depends(get_state)):
    await _require_privileged_ops(state)

    config = state.config

    async def _job_coro(job: Job) -> dict:
        removed = await scheduler.remove_timers_async(config)
        return {"removed": removed}

    try:
        job = state.jobs.start("Remove timers", "timers_remove", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}
