"""GET /api/status, /api/doctor — Dashboard read endpoints."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from app.web.deps import get_panel_state, get_state, require_session
from app.web.state import WebState
from app.web.status_poller import collect_status

router = APIRouter(prefix="/api", tags=["status"], dependencies=[Depends(require_session)])


@router.get("/status")
async def get_status(state: WebState = Depends(get_state)) -> dict:
    config = state.config
    data = await asyncio.to_thread(collect_status, config)
    # The cached answer only (plans/51 D): a probe here ran SteamCMD on every
    # page load. `null` until the startup check has answered.
    data["steam_ok"] = state.steam_known()
    return data


@router.get("/doctor")
async def get_doctor(state: WebState = Depends(get_state)) -> dict:
    """Full structured Doctor report (checks + conflicts + server/disc/log/service).

    ``busy`` tells the page a server-mutating job is running, so the findings
    describe a half-applied server — it pauses its auto-refresh on that.
    """
    from app.ops.doctor_report import build_doctor_report

    report = await asyncio.to_thread(build_doctor_report, state.config)
    report["busy"] = state.jobs.busy
    return report


@router.get("/version-check")
async def version_check(panel_state: WebState = Depends(get_panel_state)) -> dict:
    """Newer zsm release on GitHub, if any (24h-cached; None when current).

    ``version_check_enabled`` is the panel's setting, read from the instance
    `zsm web` started with: off, no request is made (plans/68 NET-13).
    """
    from app import __version__
    from app.shared.version_check import check_for_update

    config = panel_state.config
    latest = await asyncio.to_thread(check_for_update, __version__, config=config)
    return {
        "current": __version__,
        "latest": latest,
        "enabled": bool(config.version_check_enabled),
    }
