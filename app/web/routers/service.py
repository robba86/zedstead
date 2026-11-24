"""POST /api/service/{start,stop,restart,regenerate,remove,boot},
GET /api/service/{info,setup,start-check}."""

from __future__ import annotations

import asyncio
import subprocess

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.server import service_control
from app.server.unit_guard import UnitNotOurs
from app.shared.config import DayzConfig
from app.shared.platform import IS_WINDOWS, has_systemd, sudo_available_noninteractive
from app.shared import systemd_scope
from app.web.deps import get_state, require_session
from app.web.state import WebState

router = APIRouter(
    prefix="/api/service", tags=["service"], dependencies=[Depends(require_session)]
)


def _scope_fields(config: DayzConfig) -> dict:
    """Scope/linger/privileged-ops fields shared by /info and /setup.

    Blocking (subprocess probes) — run under asyncio.to_thread by the caller.
    """
    scope = systemd_scope.get_scope(config)
    ok, reason = systemd_scope.privileged_ops_available(config)
    return {
        "scope": scope.name,
        "linger_enabled": systemd_scope.linger_enabled(),
        "privileged_ops_ok": ok,
        "privileged_ops_reason": reason,
        # Legacy key kept for older frontends; prefer privileged_ops_ok.
        "sudo_available": ok if not scope.user_mode else sudo_available_noninteractive(),
    }


@router.get("/info")
async def service_info(state: WebState = Depends(get_state)) -> dict:
    fields = await asyncio.to_thread(_scope_fields, state.config)
    return {
        "has_systemd": has_systemd(),
        "is_windows": IS_WINDOWS,
        **fields,
    }


@router.get("/setup")
async def service_setup(state: WebState = Depends(get_state)) -> dict:
    setup = await asyncio.to_thread(
        service_control.service_setup_state, state.config
    )
    fields = await asyncio.to_thread(_scope_fields, state.config)
    setup.update(
        {
            "running": await asyncio.to_thread(
                service_control.is_server_running, state.config
            ),
            "has_systemd": has_systemd(),
            "is_windows": IS_WINDOWS,
            **fields,
        }
    )
    return setup


async def _require_privileged_ops(state: WebState) -> None:
    """Privileged systemd ops from the web process must not hit a sudo prompt
    (system scope) or an unreachable user bus (user scope). Scope-aware gate."""
    if IS_WINDOWS or not has_systemd():
        return
    ok, reason = await asyncio.to_thread(
        systemd_scope.privileged_ops_available, state.config
    )
    if not ok:
        raise HTTPException(status_code=409, detail=reason)


@router.post("/regenerate")
async def regenerate_service(state: WebState = Depends(get_state)) -> dict:
    """Stop the server if needed and regenerate + install the service files."""
    await _require_privileged_ops(state)
    config = state.config

    if not IS_WINDOWS and has_systemd():
        # Before the stop: a unit zsm will not replace must not cost a
        # running server its uptime (plans/78 D2).
        from app.commands.service import _render_service
        from app.server import unit_guard

        try:
            rendered = await asyncio.to_thread(_render_service, config)
            await asyncio.to_thread(unit_guard.guard, config, rendered)
        except (service_control.ServiceUserError, UnitNotOurs) as e:
            raise HTTPException(status_code=409, detail=str(e))

    if await asyncio.to_thread(service_control.is_server_running, config):
        result = await asyncio.to_thread(service_control.stop_server, config)
        if not result.ok:
            raise HTTPException(status_code=409, detail=result.message)

    from app.server.service_ops import generate_service_files

    try:
        await asyncio.to_thread(generate_service_files, config)
    except (service_control.ServiceUserError, service_control.ServerNotInstalled, UnitNotOurs) as e:
        # Actionable misconfiguration, or a unit zsm will not replace
        # (plans/78 D2), not a crash — report it as-is.
        raise HTTPException(status_code=409, detail=str(e))
    except (subprocess.CalledProcessError, OSError) as e:
        raise HTTPException(status_code=409, detail=f"Service regenerate failed: {e}")

    await state.hub.broadcast({"type": "service_changed"})
    return {"ok": True}


@router.post("/remove")
async def remove_service(state: WebState = Depends(get_state)) -> dict:
    if IS_WINDOWS:
        raise HTTPException(status_code=400, detail="Not available on Windows.")
    await _require_privileged_ops(state)
    result = await asyncio.to_thread(service_control.remove_service, state.config)
    if not result.ok:
        raise HTTPException(status_code=409, detail=result.message)
    await state.hub.broadcast({"type": "service_changed"})
    return {"ok": True}


class BootRequest(BaseModel):
    enabled: bool


@router.post("/boot")
async def set_boot(body: BootRequest, state: WebState = Depends(get_state)) -> dict:
    if IS_WINDOWS:
        raise HTTPException(status_code=400, detail="Not available on Windows.")
    await _require_privileged_ops(state)
    result = await asyncio.to_thread(
        service_control.set_boot_enabled, body.enabled, state.config
    )
    if not result.ok:
        raise HTTPException(status_code=409, detail=result.message)
    await state.hub.broadcast({"type": "service_changed"})
    return {"ok": True, "enabled_at_boot": body.enabled}


def _notify_start(config: DayzConfig) -> None:
    from app.monitoring.discord_hooks import notify_start

    try:
        notify_start(config)
    except Exception:
        pass


def _notify_stop(config: DayzConfig) -> None:
    from app.monitoring.discord_hooks import notify_stop

    try:
        notify_stop(config)
    except Exception:
        pass


class StartRequest(BaseModel):
    # Start although a runtime the server needs looks missing (the Start
    # dialog's "Start anyway").
    anyway: bool = False


@router.get("/start-check")
async def start_check(state: WebState = Depends(get_state)) -> dict:
    """What a start would be refused for, asked before the Start dialog opens.

    ``refusal``: the start cannot work (no server, no unit), and Start anyway
    does not pass it. ``problem``: a runtime looks missing, which it does."""
    from app.server.windows_runtime import runtime_problem

    refusal = await asyncio.to_thread(service_control.start_refusal, state.config)
    problem = None if refusal else await asyncio.to_thread(runtime_problem, state.config)
    return {"refusal": refusal, "problem": problem}


@router.post("/start")
async def start(
    body: StartRequest | None = None, state: WebState = Depends(get_state)
) -> dict:
    state.jobs.refuse_while_busy()
    config = state.config
    anyway = bool(body and body.anyway)
    refusal = await asyncio.to_thread(service_control.start_refusal, config)
    if refusal:
        raise HTTPException(status_code=409, detail=refusal)
    # start_server takes the start snapshot itself (plans/56 D).
    result = await asyncio.to_thread(service_control.start_server, config, anyway=anyway)
    if not result.ok:
        raise HTTPException(status_code=409, detail=result.message)

    await asyncio.to_thread(_notify_start, config)
    await state.hub.broadcast({"type": "service_changed", "action": "start"})
    return {"ok": True}


@router.post("/stop")
async def stop(state: WebState = Depends(get_state)) -> dict:
    state.jobs.refuse_while_busy()
    config = state.config
    result = await asyncio.to_thread(service_control.stop_server, config)
    if not result.ok:
        raise HTTPException(status_code=409, detail=result.message)

    await asyncio.to_thread(_notify_stop, config)
    await state.hub.broadcast({"type": "service_changed"})
    return {"ok": True}


@router.post("/restart")
async def restart(state: WebState = Depends(get_state)) -> dict:
    state.jobs.refuse_while_busy()
    config = state.config
    result = await asyncio.to_thread(service_control.restart_server, config)
    if not result.ok:
        raise HTTPException(status_code=409, detail=result.message)

    await state.hub.broadcast({"type": "service_changed", "action": "restart"})
    return {"ok": True}
