"""GET /api/self-update, POST /api/self-update — update zsm itself.

Both are the panel's, not one instance's: there is one zsm per machine. The
GET runs ``git fetch``, so it is admin-only and listed in
``permissions.SIDE_EFFECTING_READS``; the POST is an exclusive job, so it never
runs beside a SteamCMD job or another update.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request

from app.ops import self_update
from app.web.deps import get_state, require_session
from app.web.jobs import Job, JobConflictError, conflict_response
from app.web.state import WebState

router = APIRouter(prefix="/api", tags=["self-update"], dependencies=[Depends(require_session)])

# Long enough for the job's last line and its finished event to reach the page.
RESTART_DELAY = 2.0


@router.get("/self-update")
async def check_self_update() -> dict:
    from app import __version__

    info = await asyncio.to_thread(self_update.check)
    return {"version": __version__, **info.to_dict()}


@router.post("/self-update")
async def run_self_update(request: Request, state: WebState = Depends(get_state)):
    restart = getattr(request.app.state, "request_restart", None)

    async def _job_coro(job: Job) -> dict:
        result = await asyncio.to_thread(self_update.apply, job.log_line_threadsafe)
        result["restarting"] = False
        if not result["changed"]:
            return result
        if restart is None:
            job.log_line("Restart zsm web to run the new version.")
        else:
            job.log_line("Restarting the app. Sign in again when the page comes back.")
            asyncio.get_running_loop().call_later(RESTART_DELAY, restart)
            result["restarting"] = True
        return result

    try:
        job = state.jobs.start("Update Zedstead", "self_update", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}
