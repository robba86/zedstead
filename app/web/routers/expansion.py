"""GET /api/expansion/templates, POST /api/expansion/templates/download,
GET /api/expansion/mission, POST /api/expansion/mission/apply,
POST /api/expansion/mission/remove — Expansion's mission files.
GET/POST /api/expansion/market/zone-offers — a zone mod's items as a Market
category (plans/48 D; the rules are in `app/ops/zone_trader.py`).

The catalog half of the feature (which modules exist, what each needs) is
`GET /api/mods/expansion` in `routers/mods.py`; this router is only about the
template repo and what it writes into the mission. See
`app/ops/expansion_ops.py` for why those writes go in as manual XML files.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.ops import expansion_ops
from app.web.deps import get_state, require_session
from app.web.jobs import Job, JobConflictError, conflict_response
from app.web.schemas import ExpansionMissionRequest, ZoneMarketRequest
from app.web.state import WebState

router = APIRouter(prefix="/api/expansion", tags=["expansion"], dependencies=[Depends(require_session)])


def _mission_terrain(config) -> tuple[str, str]:
    from app.maps.registry import terrain_from_mission
    from app.modmgr import mods

    mission = mods.get_current_mission(config.server_cfg_path) or ""
    return mission, terrain_from_mission(mission) if mission else ""


def _resolve_template(config, name: str):
    """The named template, or the one matching the mission's terrain.

    404 rather than a silent fallback: applying the wrong map's template writes
    another map's spawn positions into the mission.
    """
    templates = expansion_ops.list_templates(config)
    if name:
        found = next((t for t in templates if t.name == name), None)
        if found is None:
            raise HTTPException(404, f"No template named {name!r}. Download the templates first.")
        return found
    _mission, terrain = _mission_terrain(config)
    found = expansion_ops.find_template(config, terrain)
    if found is None:
        raise HTTPException(
            404,
            "No template matches this mission's map. Pick one explicitly, or download the "
            "templates if you have not yet.",
        )
    return found


@router.get("/templates")
async def list_templates(state: WebState = Depends(get_state)) -> dict:
    """The unpacked map templates, and which one fits the current mission."""
    config = state.config

    def _load() -> dict:
        from app.maps.registry import display_name

        mission, terrain = _mission_terrain(config)
        templates = expansion_ops.list_templates(config)
        match = expansion_ops.find_template(config, terrain)
        return {
            "dir": str(expansion_ops.template_root(config)),
            "configured_dir": config.expansion_template_dir,
            "repo_url": expansion_ops.TEMPLATE_REPO_URL,
            "zip_url": expansion_ops.TEMPLATE_ZIP_URL,
            "downloaded": bool(templates),
            "mission": mission,
            "terrain": terrain,
            "map_name": display_name(terrain) if terrain else "",
            "match": match.name if match else "",
            "templates": [
                {
                    "name": t.name,
                    "terrain": t.terrain,
                    "map_name": display_name(t.terrain) if t.terrain else "",
                    "complete": t.complete,
                    "files": list(t.files),
                    "map_folders": t.has_map_folders,
                }
                for t in templates
            ],
            "applied": expansion_ops.applied_files(config),
        }

    return await asyncio.to_thread(_load)


@router.post("/templates/download")
async def download_templates(state: WebState = Depends(get_state)):
    """Fetch the missions repo and unpack its Template/ folder (a job).

    409 before the job when the folder is not one zsm may empty; the job
    re-checks, since it runs later.
    """
    config = state.config
    refusal = await asyncio.to_thread(expansion_ops.template_dir_refusal, config)
    if refusal:
        raise HTTPException(409, refusal)

    async def _job_coro(job: Job) -> dict:
        job.log_line(f"Downloading {expansion_ops.TEMPLATE_ZIP_URL}")
        count = await asyncio.to_thread(
            expansion_ops.download_templates, config, job.log_line_threadsafe
        )
        templates = await asyncio.to_thread(expansion_ops.list_templates, config)
        await state.hub.broadcast({"type": "expansion_changed"})
        return {"files": count, "templates": [t.name for t in templates]}

    try:
        job = state.jobs.start("Download Expansion templates", "expansion_templates", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}


@router.get("/mission")
async def preview_mission(template: str = "", state: WebState = Depends(get_state)) -> dict:
    """What applying a template would write, and every entry it would drop."""
    config = state.config
    tpl = await asyncio.to_thread(_resolve_template, config, template)
    plan = await asyncio.to_thread(expansion_ops.build_plan, config, tpl)
    return plan.summary()


@router.post("/mission/apply")
async def apply_mission(body: ExpansionMissionRequest, state: WebState = Depends(get_state)):
    """Filter the template and put it into the mission (a job — it writes)."""
    config = state.config
    tpl = await asyncio.to_thread(_resolve_template, config, body.template)

    async def _job_coro(job: Job) -> dict:
        plan = await asyncio.to_thread(expansion_ops.build_plan, config, tpl)
        job.log_line(
            f"{tpl.name} template, filtered for {len(plan.modules)} installed module(s): "
            + ", ".join(plan.modules)
        )
        for kind, total in plan.total.items():
            job.log_line(f"  {kind}: {plan.kept.get(kind, 0)} of {total} kept")
        for d in plan.dropped:
            job.log_line(f"  - {d.kind}/{d.element} {d.name} ({d.reason})")
        result = await asyncio.to_thread(
            expansion_ops.apply_plan, config, plan, log=job.log_line_threadsafe
        )
        await state.hub.broadcast({"type": "expansion_changed"})
        await state.hub.broadcast({"type": "configs_changed"})
        return {**result, "plan": plan.summary()}

    try:
        job = state.jobs.start("Apply Expansion mission files", "expansion_mission", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}


@router.post("/mission/remove")
async def remove_mission(state: WebState = Depends(get_state)) -> dict:
    """Delete the generated records and undo what they put in the mission."""
    config = state.config
    removed = await asyncio.to_thread(expansion_ops.remove_applied, config)
    if removed:
        await state.hub.broadcast({"type": "expansion_changed"})
        await state.hub.broadcast({"type": "configs_changed"})
    return {"removed": removed}


@router.get("/market/zone-offers")
async def market_zone_offers(state: WebState = Depends(get_state)) -> dict:
    """The installed zone mods' items a Market category could hold."""
    from app.ops import zone_trader

    return await asyncio.to_thread(zone_trader.market_offers, state.config)


@router.post("/market/zone-offers")
async def add_market_zone_offer(body: ZoneMarketRequest, state: WebState = Depends(get_state)) -> dict:
    """Write one zone mod's priced items as a Market category and list it at traders."""
    from app.ops import zone_trader

    try:
        result = await asyncio.to_thread(
            zone_trader.add_to_market, state.config, body.mod, body.category, body.trader_ids,
            body.mode, [row.model_dump() for row in body.items],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await state.hub.broadcast({"type": "configs_changed"})
    return result
