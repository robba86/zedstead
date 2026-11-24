"""GET /api/update/steps, POST /api/update/run, POST /api/update/rollback,
GET /api/update/conflicts, POST /api/update/conflicts/resolve,
POST /api/update/conflicts/values, GET|DELETE /api/update/triage,
POST /api/update/triage/apply, GET /api/logs/rpt."""

from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.ops import mission_stock, pipeline, post_update
from app.ops.log_analysis import stock_line_checker
from app.server import service_control
from app.web import redact
from app.web.deps import get_state, require_session
from app.web.jobs import Job, JobConflictError, JobFailed, JobRunner, conflict_response, strip_markup
from app.web.rpt import read_rpt_tail
from app.web.schemas import (
    UpdateConflictsResolveRequest, UpdateConflictValuesRequest, UpdateRollbackRequest, UpdateRunRequest,
    UpdateTriageApplyRequest,
)
from app.web.state import WebState

router = APIRouter(prefix="/api", tags=["update"], dependencies=[Depends(require_session)])


def _steps_by_key(config) -> dict[str, pipeline.PipelineStep]:
    return {s.key: s for s in pipeline.build_update_steps(config)}


@router.get("/update/steps")
async def get_update_steps(state: WebState = Depends(get_state)) -> dict:
    # Steps are cheap to build; the Steam-login check is a slow SteamCMD
    # subprocess (up to a 15s timeout) so it is NOT done here — the frontend
    # fetches it separately via GET /api/steam/status so the steps render
    # instantly instead of blocking the whole page on the login probe.
    steps = pipeline.build_update_steps(state.config)
    return {
        "steps": [
            {"key": s.key, "label": s.label, "undoable": s.undo is not None, "needs_steam": s.needs_steam}
            for s in steps
        ],
    }


def _make_context(
    job: Job, config, should_run, *, dry_run: bool = False
) -> pipeline.PipelineContext:
    return pipeline.PipelineContext(
        config=config,
        emit=lambda s: job.log_line_threadsafe(strip_markup(s)),
        runner=JobRunner(job),
        should_run=should_run,
        dry_run=dry_run,
        cancel_event=job.cancel_event,
    )


@router.post("/update/run")
async def run_update(body: UpdateRunRequest, state: WebState = Depends(get_state)):
    # A dry run writes nothing, so a running server does not stop it.
    if not body.force and not body.dry_run and await asyncio.to_thread(
        service_control.is_server_running, state.config
    ):
        raise HTTPException(
            # This string is the whole toast the Guide's Install button shows, so
            # it speaks to the person clicking, not to an API client.
            status_code=409,
            detail="The server is running — stop it before installing or updating.",
        )

    all_steps = _steps_by_key(state.config)
    requested = set(body.steps) if body.steps else set(all_steps.keys())
    unknown = requested - set(all_steps.keys())
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown step key(s): {', '.join(unknown)}")

    async def _job_coro(job: Job) -> dict:
        config = state.config
        ctx = _make_context(
            job, config, lambda key: key in requested, dry_run=body.dry_run
        )
        before = None if body.dry_run else await asyncio.to_thread(post_update.snapshot, config)
        results, _completed_undoable = await pipeline.run_pipeline(list(all_steps.values()), ctx)
        await state.hub.broadcast({"type": "settings_changed"})
        out: dict = {
            "results": [
                {
                    "key": r.key, "label": r.label, "status": r.status,
                    "duration": r.duration, "error": r.error,
                }
                for r in results
            ],
            "game": None,
            "boot": None,
        }
        failed = [r for r in results if r.status == "failed"]
        if before is not None:
            if await asyncio.to_thread(post_update.game_updated, before, config):
                after = await asyncio.to_thread(mission_stock.installed_buildid, config)
                out["game"] = {"before": before.build, "after": after}
            if body.start_after and not failed and not await asyncio.to_thread(
                service_control.is_server_running, config
            ):
                out["boot"] = await post_update.start_and_check(config, before, emit=ctx.emit)
                await state.hub.broadcast({"type": "update_triage_changed"})
        if failed:
            # The job ends failed, so the toast and the Activity drawer say so;
            # the results stay on the job for the Update page (F6).
            raise JobFailed(
                "; ".join(f"{r.label}: {r.error or 'failed'}" for r in failed), result=out,
            )
        return out

    try:
        job = state.jobs.start("Update pipeline", "update", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)

    return {"job_id": job.id}


@router.post("/update/rollback")
async def rollback_update(body: UpdateRollbackRequest, state: WebState = Depends(get_state)):
    all_steps = _steps_by_key(state.config)
    unknown = set(body.steps) - set(all_steps.keys())
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown step key(s): {', '.join(unknown)}")

    to_rollback = [
        s for s in all_steps.values() if s.key in body.steps and s.undo is not None
    ]

    async def _job_coro(job: Job) -> dict:
        ctx = _make_context(job, state.config, lambda key: True)
        failed = await pipeline.rollback_pipeline(to_rollback, ctx)
        await state.hub.broadcast({"type": "settings_changed"})
        if failed:
            # The job shows "failed", never "success", when an undo raised.
            raise RuntimeError(pipeline.rollback_failed_message(failed))
        return None

    try:
        job = state.jobs.start("Update rollback", "update_rollback", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)

    return {"job_id": job.id}


@router.get("/update/triage")
async def get_update_triage(
    mods: list[str] | None = Query(None), state: WebState = Depends(get_state),
) -> dict:
    """The last boot after an update (plans/73), the offer when it failed,
    and the mods set aside after earlier updates. *mods* narrows the offer to
    the suspects ticked, so the dependents shown are the ones that apply."""
    def _read() -> dict:
        config = state.config
        report = post_update.load_report(config)
        failed = post_update.failed(report)
        offerable = post_update.offerable(report) if failed else []
        chosen = None if mods is None else [m for m in mods if m in offerable]
        return {
            "report": report,
            "occasion": post_update.occasion(report) if report else "",
            "offerable": offerable,
            "offer": post_update.offer(config, report, chosen) if failed else None,
            "set_aside": post_update.triage_entries(config),
        }

    return await asyncio.to_thread(_read)


@router.delete("/update/triage")
async def dismiss_update_triage(state: WebState = Depends(get_state)) -> dict:
    """Forget the last boot report. Mods already set aside stay set aside."""
    cleared = await asyncio.to_thread(post_update.clear_report, state.config)
    await state.hub.broadcast({"type": "update_triage_changed"})
    return {"ok": True, "cleared": cleared}


@router.post("/update/triage/apply")
async def apply_update_triage(body: UpdateTriageApplyRequest, state: WebState = Depends(get_state)):
    """Set suspects of the last failed boot aside, restart, check once. Only
    names the report offers are accepted: anything else is the Mods page's."""
    report = await asyncio.to_thread(post_update.load_report, state.config)
    offered = post_update.offerable(report) if post_update.failed(report) else []
    if not offered:
        raise HTTPException(status_code=409, detail="There is no failed boot to act on.")
    unknown = [m for m in body.mods if m not in offered]
    if unknown or not body.mods:
        raise HTTPException(
            status_code=422,
            detail=f"Not a suspect of the last failed boot: {', '.join(unknown)}" if unknown else "No mods given.",
        )

    async def _job_coro(job: Job) -> dict:
        from app.server.service_ops import generate_service_files_async

        async def _regenerate(config) -> None:
            await generate_service_files_async(JobRunner(job), config)

        result = await post_update.set_aside(
            state.config, body.mods,
            mutate=state.mutate_config,
            regenerate=_regenerate,
            emit=lambda s: job.log_line_threadsafe(strip_markup(s)),
            backup_first=body.backup_first,
        )
        await state.hub.broadcast({"type": "mods_changed"})
        await state.hub.broadcast({"type": "update_triage_changed"})
        return {"report": result}

    try:
        job = state.jobs.start("Set mods aside", "update_triage", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}


@router.get("/update/conflicts")
async def list_update_conflicts(state: WebState = Depends(get_state)) -> dict:
    """Stock mission files a DayZ update changed after the admin had (plans/67),
    waiting for a choice. DayZ's new file is live for each one meanwhile."""
    rows = await asyncio.to_thread(mission_stock.list_conflicts, state.config)
    return {"conflicts": rows, "policy": mission_stock.conflict_policy(state.config)}


@router.get("/update/conflicts/diff")
async def diff_update_conflict(file: str, request: Request, state: WebState = Depends(get_state)) -> dict:
    """What separates DayZ's live file from the version the admin had, mod
    blocks left out. 404 when *file* is not waiting for a choice."""
    diff = await asyncio.to_thread(mission_stock.conflict_diff, state.config, file)
    if diff is None:
        raise HTTPException(status_code=404, detail=f"{file} is not waiting for a choice.")
    # Same rule as the Versions diff (app/web/redact.py).
    return {"file": file, "diff": diff if redact.is_admin(request) else redact.redact_diff(diff)}


@router.post("/update/conflicts/resolve")
async def resolve_update_conflicts(body: UpdateConflictsResolveRequest, state: WebState = Depends(get_state)):
    """Keep the admin's whole version ("mine") or DayZ's ("dayz"). A job,
    because putting a file back re-runs the merge."""
    config = state.config

    async def _job_coro(job: Job) -> dict:
        log = job.log_line_threadsafe
        chosen, merged = await asyncio.to_thread(mission_stock.settle, config, body.files, body.keep, log=log)
        if not chosen:
            log("Nothing was waiting for a choice.")
        await state.hub.broadcast({"type": "configs_changed"})
        return {"resolved": chosen, "merged": merged}

    try:
        job = state.jobs.start("Settle update conflicts", "update_conflicts", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}


@router.post("/update/conflicts/values")
async def settle_update_conflict_values(body: UpdateConflictValuesRequest, state: WebState = Depends(get_state)):
    """Settle listed values of a merged file (plans/71). A job, because a
    value written into the file re-runs the merge."""
    config = state.config

    async def _job_coro(job: Job) -> dict:
        log = job.log_line_threadsafe
        settled, merged = await asyncio.to_thread(
            mission_stock.settle_values, config, body.file, body.ids, body.keep, log=log,
        )
        if not settled:
            log("Nothing was listed for those values.")
        await state.hub.broadcast({"type": "configs_changed"})
        return {"settled": settled, "merged": merged}

    try:
        job = state.jobs.start("Settle update values", "update_conflicts", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}


@router.get("/logs/rpt")
async def get_rpt_snapshot(lines: int = 200, state: WebState = Depends(get_state)) -> dict:
    """The newest server log's last lines. ``stock`` flags each line that is
    not the admin's doing, which the Key lines pane mutes (``is_stock_line``).
    ``utc_offset_minutes`` is this machine's offset, the zone of the times the
    lines start with, so the browser can show them on its own clock."""
    content = await asyncio.to_thread(read_rpt_tail, state.config, lines)
    is_stock = await asyncio.to_thread(stock_line_checker, state.config)
    offset = datetime.now().astimezone().utcoffset()
    return {
        "lines": content,
        "stock": [is_stock(line) for line in content],
        "utc_offset_minutes": round(offset.total_seconds() / 60) if offset is not None else None,
    }
