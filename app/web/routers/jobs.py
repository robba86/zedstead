"""GET /api/jobs, GET /api/jobs/{id}, POST /api/jobs/{id}/cancel,
POST /api/jobs/{id}/kill."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.web.deps import get_panel_state, require_session
from app.web.state import WebState

router = APIRouter(prefix="/api/jobs", tags=["jobs"], dependencies=[Depends(require_session)])


@router.get("")
async def list_jobs(state: WebState = Depends(get_panel_state)) -> list[dict]:
    return [job.to_dict() for job in state.jobs.list_jobs()]


@router.get("/{job_id}")
async def get_job(
    job_id: str, log_after: int = Query(0), state: WebState = Depends(get_panel_state)
) -> dict:
    job = state.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    data = job.to_dict()
    data["log"] = [{"seq": seq, "line": line} for seq, line in job.logs_after(log_after)]
    return data


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, state: WebState = Depends(get_panel_state)) -> dict:
    if not state.jobs.cancel(job_id):
        raise HTTPException(status_code=404, detail="Job not found or already finished")
    job = state.jobs.get(job_id)
    if job is not None:
        # Cancellation only takes effect at the job's next checkpoint, so say so
        # in the log and push the cancel_requested flag to every open tab —
        # otherwise the request looks like it did nothing for several seconds.
        job.log_line("Cancelling — waiting for the current step to finish...")
        await job.broadcast_status()
    return {"ok": True}


@router.post("/{job_id}/kill")
async def kill_job(job_id: str, state: WebState = Depends(get_panel_state)) -> dict:
    """Force-stop a running job whose cancel is stuck — the UI offers it once a
    cancel has been requested, so nobody has to restart zsm or the container."""
    job = state.jobs.get(job_id)
    if job is None or not state.jobs.kill(job_id):
        raise HTTPException(status_code=404, detail="Job not found or not running")
    job.log_line(
        "Killed — the job stopped part-way through its current step. If that step "
        "was a file copy or XML merge, it finishes in the background. Re-run the "
        "job to complete it."
    )
    await job.broadcast_status()
    return {"ok": True}
