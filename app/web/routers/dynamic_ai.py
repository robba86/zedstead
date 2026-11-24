"""GET /api/dynamic-ai, /api/expansion-ai, /api/radzone and /api/toxiczone —
what zsm can tell about a pre-configured mod's install: the findings behind
its Doctor row, plus what each mod's own report adds (Dynamic AI: factions,
loadouts and the mod's account of its last boot, plans/47 C; Expansion AI:
patrol counts and factions, plans/64; RadZone and ToxicZone: their zones,
plans/48 B).

The reports are read-only; `app/ops/dynamic_ai.py`, `expansion_ai.py`,
`zone_mods.py` and `toxic_zone.py` do the work, and the editors that change the files are in
`app/web/editors/`. The two writes here are ToxicZone's card actions, both
confirmed in the UI: replacing the mod author's Steam ID in `AdminUID` (D4),
and recomputing the moving zones' paths (D3).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from fastapi import APIRouter, Body, Depends, HTTPException

from app.ops import dynamic_ai, expansion_ai, toxic_zone, zone_mods
from app.web.deps import get_state, require_session
from app.web.editors import toxiczone as toxiczone_editors
from app.web.state import WebState


def _report_router(prefix: str, build: Callable) -> APIRouter:
    r = APIRouter(prefix=prefix, tags=[prefix.rsplit("/", 1)[-1]], dependencies=[Depends(require_session)])

    @r.get("")
    async def get_report(state: WebState = Depends(get_state)) -> dict:
        """``{installed: false}`` without the mod, else the full report."""
        report = await asyncio.to_thread(build, state.config)
        if report is None:
            return {"installed": False}
        return {"installed": True, **report}

    return r


router = _report_router("/api/dynamic-ai", dynamic_ai.build_report)
radzone_router = _report_router("/api/radzone", zone_mods.build_report)
toxiczone_router = _report_router("/api/toxiczone", toxic_zone.build_report)
expansion_ai_router = _report_router("/api/expansion-ai", expansion_ai.build_report)


@toxiczone_router.post("/admin-uid")
async def replace_author_uid(body: dict = Body(...), state: WebState = Depends(get_state)) -> dict:
    """Put ``steam_id`` where AdminUID lists the mod author's ID (D4)."""
    try:
        uids = await asyncio.to_thread(toxiczone_editors.replace_author_uid, state.config, str(body.get("steam_id", "")))
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await state.hub.broadcast({"type": "configs_changed"})
    return {"admin_uid": uids}


@toxiczone_router.post("/recompute-paths")
async def recompute_paths(state: WebState = Depends(get_state)) -> dict:
    """Recompute every moving zone's derived path from its two ends (D3)."""
    try:
        fixed = await asyncio.to_thread(toxiczone_editors.recompute_paths, state.config)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if fixed:
        await state.hub.broadcast({"type": "configs_changed"})
    return {"fixed": fixed}
