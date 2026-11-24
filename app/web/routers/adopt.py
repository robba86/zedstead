"""/api/adopt — take over a DayZ server zsm did not install (plans/78 D4).

`GET` reads the server and runs the checks against it (`adopt.findings`), and
writes nothing. `POST /apply` writes zsm's mod list and launch fields through
`mutate_config`, so the path lock refuses it before anything is written. The
fixes are their own routes and run only on a click; Apply runs none of them.

Every route is admin: `GET` is listed in `permissions.NON_VIEWER_READS`
because it shows the server's own start script and unit lines, which zsm did
not write and cannot vouch for.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.modmgr import modlist
from app.ops import adopt
from app.web.deps import get_state, require_session
from app.web.state import ConfigLocked, WebState

router = APIRouter(prefix="/api/adopt", tags=["adopt"], dependencies=[Depends(require_session)])


class PastedRequest(BaseModel):
    mod: str
    keep: Literal["mine", "mod"]


class UnitRequest(BaseModel):
    name: str


def _read(config) -> dict:
    report = adopt.scan(config)
    adopted = bool(modlist.installed(config))
    return {
        "report": report.to_dict(),
        "adopted": adopted,
        "findings": adopt.findings(config, report),
        "offered_units": [u.name for u in adopt.offered_units(report)],
    }


@router.get("")
async def get_adopt(state: WebState = Depends(get_state)) -> dict:
    try:
        return await asyncio.to_thread(_read, state.config)
    except modlist.ModListError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def _lines(state: WebState, fn) -> dict:
    try:
        lines = await asyncio.to_thread(fn)
    except adopt.AdoptError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await state.hub.broadcast({"type": "mods_changed"})
    return {"lines": lines}


@router.post("/apply")
async def apply_adopt(state: WebState = Depends(get_state)) -> dict:
    report = await asyncio.to_thread(adopt.scan, state.config)
    out: dict = {}

    def change(cfg):
        write, out["lines"] = adopt.apply_to(cfg, report)
        return write

    try:
        await state.mutate_config(change)
    except (adopt.AdoptError, modlist.ModListError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConfigLocked as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    await state.hub.broadcast({"type": "mods_changed"})
    return {"lines": out["lines"]}


def _pasted(report: adopt.Report, name: str) -> adopt.PastedTypes:
    entry = next((p for p in report.pasted if p.mod == name), None)
    if entry is None:
        raise adopt.AdoptError(f"{name} has no types pasted into the mission.")
    return entry


@router.post("/pasted")
async def settle_pasted(body: PastedRequest, state: WebState = Depends(get_state)) -> dict:
    config = state.config

    def run() -> list[str]:
        report = adopt.scan(config)
        entry = _pasted(report, body.mod)
        if body.keep == "mine":
            return adopt.keep_pasted(config, entry)
        return adopt.remove_pasted(config, report.mission, entry)

    return await _lines(state, run)


@router.post("/copy-keys")
async def copy_keys(state: WebState = Depends(get_state)) -> dict:
    config = state.config
    return await _lines(state, lambda: adopt.copy_missing_keys(config, adopt.scan(config)))


@router.post("/disable-unit")
async def disable_unit(body: UnitRequest, state: WebState = Depends(get_state)) -> dict:
    config = state.config
    return await _lines(
        state, lambda: adopt.disable_unit(adopt.scan(config), body.name, can_prompt=False)
    )
