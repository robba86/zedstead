"""GET /api/maps, POST /api/maps/switch and /download (exclusive jobs — mirrors MapsPanel), POST /mission-check."""

from __future__ import annotations

import asyncio
import shutil
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from app.web.deps import get_state, require_session
from app.web.jobs import Job, JobConflictError, JobRunner, conflict_response, strip_markup
from app.web.map_editor_svc import reload_map_editor
from app.web.schemas import MapDownloadRequest, MapSwitchRequest
from app.web.state import WebState

router = APIRouter(prefix="/api/maps", tags=["maps"], dependencies=[Depends(require_session)])


def _catalog_terrain(mission_template: str | None) -> str | None:
    """Terrain behind a mission template, but only if we can show its image.

    ``empty.banovfrost`` -> ``banovfrost``, the same derivation the thumbnail
    route uses on the *installed* mission. Returns None when no image exists for
    it, because the terrain's only job in the payload is to drive the card's
    preview and its Map Editor deep link — emitting one we can't render would
    give the card a broken image.
    """
    from app.maps.map_data import own_image

    terrain = _template_terrain(mission_template)
    return terrain if terrain and own_image(terrain) else None


def _template_terrain(mission_template: str | None) -> str | None:
    """Terrain behind a mission template, image or not: the card's Map editor link."""
    from app.maps.locations import get_terrain_from_mission

    if not mission_template:
        return None
    return get_terrain_from_mission(Path(mission_template)) or None


def _editor_features(config, mission_template: str | None) -> dict | None:
    """What the map editor may draw for the map, None for a map with no mission yet."""
    from app.maps.registry import editor_features
    from app.maps.terrain_map import world_is_encrypted

    terrain = _template_terrain(mission_template)
    if not terrain:
        return None
    return editor_features(terrain, world_is_encrypted(config.server_dir_path, terrain))


def _cfg_backup(config, map_id: str) -> dict | None:
    """The saved serverDZ.cfg a switch to *map_id* would restore, for the
    switch dialog's choice: when it was saved, from a stat. Never reads it."""
    from app.maps.map_ops import get_map_cfg_backup

    path = get_map_cfg_backup(config, map_id)
    if path is None:
        return None
    try:
        return {"modified": path.stat().st_mtime}
    except OSError:
        return None


def _mission_commit(config, mission: dict) -> str | None:
    """The upstream commit an installed community mission came from, short, or None."""
    from app.maps.map_ops import read_mission_source

    if mission.get("source") != "zip" or not mission.get("template"):
        return None
    record = read_mission_source(config.server_dir_path / "mpmissions" / mission["template"])
    commit = (record or {}).get("commit")
    return commit[:7] if isinstance(commit, str) else None


def _catalog(config) -> dict:
    from app.maps.map_ops import (
        get_active_map_name,
        is_map_downloaded,
        map_available,
        vanilla_mission,
    )
    from app.shared.seed_data import (
        MAP_DESCRIPTIONS,
        VANILLA_MAPS,
        WORKSHOP_MAPS,
        map_is_released,
    )

    active = get_active_map_name(config)
    maps = []
    for name, data in WORKSHOP_MAPS.items():
        mission = data.get("mission", {})
        maps.append(
            {
                "id": name,
                "type": "workshop",
                "active": name == active,
                "available": map_is_released(name),
                "description": MAP_DESCRIPTIONS.get(name, ""),
                "mods": data.get("mods", {}),
                "deps": data.get("deps", {}),
                "mission_template": mission.get("template"),
                "terrain": _catalog_terrain(mission.get("template")),
                "editor_terrain": _template_terrain(mission.get("template")),
                "editor_features": _editor_features(config, mission.get("template")),
                # Its mods and mission are on this machine: the active map, or
                # one fetched with "Download only" (POST /download).
                "downloaded": is_map_downloaded(config, data),
                "mission_source": mission.get("source"),
                "mission_zip_url": mission.get("zip_url"),
                "mission_commit": _mission_commit(config, mission),
                "recommended_cfg": bool(mission.get("zip_url") and mission.get("server_cfg_path")),
                "cfg_backup": _cfg_backup(config, name),
            }
        )
    for name, data in VANILLA_MAPS.items():
        # An announced-but-unreleased official map is a display-only card: no
        # mission, no image, and `available` False greys its button. The route
        # refuses it independently — see switch_map. Once a server update has
        # put its mission in mpmissions/ it is a normal card.
        available = map_available(config, name)
        mission_template = vanilla_mission(config, name)
        maps.append(
            {
                "id": name,
                "type": "vanilla",
                "active": name == active,
                "available": available,
                "release": data.get("release"),
                "url": data.get("url"),
                "description": MAP_DESCRIPTIONS.get(name, ""),
                "dlc": data.get("dlc", ""),
                "dlc_note": data.get("dlc_note", ""),
                "mods": {},
                "deps": {},
                "mission_template": mission_template,
                "terrain": _catalog_terrain(mission_template),
                "editor_terrain": _template_terrain(mission_template),
                "editor_features": _editor_features(config, mission_template),
                # An official map comes with the server.
                "downloaded": available,
                "mission_source": None,
                "mission_zip_url": None,
                "mission_commit": None,
                "recommended_cfg": False,
                "cfg_backup": _cfg_backup(config, name),
            }
        )
    return {"active": active, "maps": maps}


@router.get("")
async def get_maps(state: WebState = Depends(get_state)) -> dict:
    return await asyncio.to_thread(_catalog, state.config)


# Workshop sizes for the Download only buttons. A Maps page view must not ask
# Steam every time. Kept per Workshop id, so every instance the panel serves
# shares one answer: a size for _SIZES_TTL, and "no size" (Steam left the id
# out, or the request failed) for _SIZES_RETRY, so an offline panel does not
# wait on Steam at every view and a removed item is not asked for at each one.
_SIZES_TTL = 6 * 3600
_SIZES_RETRY = 5 * 60
_sizes_cache: dict[str, tuple[int | None, float]] = {}  # id -> (bytes or None, keep until)
# Held across the request, so page views that arrive together share one.
_sizes_lock = threading.Lock()


def _workshop_sizes(ids: set[str]) -> dict[str, int]:
    """``{mod_id: bytes}`` for the *ids* Steam gave a size for; asks only for
    ids not cached or past their time."""
    from app.modmgr.workshop_api import get_public_file_sizes

    with _sizes_lock:
        now = time.monotonic()
        due = sorted(i for i in ids if i not in _sizes_cache or _sizes_cache[i][1] <= now)
        if due:
            answer = get_public_file_sizes(due)
            now = time.monotonic()
            for mod_id in due:
                size = answer.get(mod_id)
                _sizes_cache[mod_id] = (
                    size, now + (_SIZES_TTL if size is not None else _SIZES_RETRY),
                )
        sizes = {mod_id: _sizes_cache[mod_id][0] for mod_id in ids}
    return {mod_id: size for mod_id, size in sizes.items() if size is not None}


def _download_sizes(config) -> dict[str, int]:
    """Bytes each community map's Download only would still fetch.

    The Workshop size of every mod and dep not on this machine yet, so a map
    sharing a dep with one already downloaded shows less. A map with nothing
    left to fetch, or one whose sizes Steam did not give, is left out: no
    size is better than a wrong one. The mission ZIP is not counted.
    """
    from app.maps.map_ops import missing_map_mods
    from app.shared.seed_data import WORKSHOP_MAPS

    missing = {name: missing_map_mods(config, data) for name, data in WORKSHOP_MAPS.items()}
    ids = {mod_id for mods in missing.values() for mod_id in mods.values()}
    if not ids:
        return {}
    sizes = _workshop_sizes(ids)
    return {
        name: sum(sizes[mod_id] for mod_id in mods.values())
        for name, mods in missing.items()
        if mods and all(mod_id in sizes for mod_id in mods.values())
    }


@router.get("/download-sizes")
async def get_download_sizes(state: WebState = Depends(get_state)) -> dict:
    """``{"sizes": {map_id: bytes}}`` for the Download only buttons.

    Apart from ``GET /api/maps`` because it may ask Steam (the key-free
    `workshop_api.get_public_file_sizes`), and the catalog stays fast offline.
    """
    return {"sizes": await asyncio.to_thread(_download_sizes, state.config)}


def _active_terrain(config) -> str | None:
    """Terrain of the installed mission, straight from serverDZ.cfg.

    Deliberately does NOT go through the map editor state — a thumbnail must
    not pay for (or trigger) parsing every mission XML.
    """
    from app.maps.locations import get_terrain_from_mission
    from app.modmgr.mods import get_current_mission

    mission = get_current_mission(config.server_cfg_path)
    if not mission:
        return None
    return get_terrain_from_mission(Path(mission))


def _thumbnail_path(config, terrain: str | None) -> Path:
    """The preview the browser made at upload, else the full image."""
    from app.maps.map_data import own_image, own_thumbnail

    t = terrain or _active_terrain(config)
    if not t:
        raise HTTPException(status_code=404, detail="No active map")
    path = own_thumbnail(t) or own_image(t)
    if not path:
        raise HTTPException(status_code=404, detail=f"No map image for {t!r}")
    return path


@router.get("/thumbnail")
async def get_map_thumbnail(
    request: Request,
    terrain: str | None = Query(None),
    state: WebState = Depends(get_state),
):
    """The admin's own image of the active (or requested) map, for previews.

    It can be replaced or removed under the same URL, so the browser asks
    every time and gets a 304 while the file is unchanged.
    """
    path = await asyncio.to_thread(_thumbnail_path, state.config, terrain)
    st = path.stat()
    etag = f'"own-{st.st_mtime_ns:x}-{st.st_size:x}"'
    headers = {"Cache-Control": "private, no-cache", "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return FileResponse(str(path), headers=headers)


def _check_map(config, map_id: str) -> None:
    """404 for an unknown map, 400 for one announced but not released."""
    from app.maps.map_ops import map_available
    from app.shared.seed_data import VANILLA_MAPS, WORKSHOP_MAPS

    vanilla_data = VANILLA_MAPS.get(map_id)
    if map_id not in WORKSHOP_MAPS and vanilla_data is None:
        raise HTTPException(status_code=404, detail=f"Unknown map: {map_id!r}")
    # Known, but announced-only — there is no mission to install. 400, not 404:
    # the name is real, the request just can't be carried out yet.
    if not map_available(config, map_id):
        raise HTTPException(
            status_code=400,
            detail=f"{map_id} has not been released yet — nothing to install."
            + (
                " Once it is out, update the server and its mission will be found."
                if vanilla_data and vanilla_data.get("terrain") else ""
            ),
        )


def _switch_plan(config, map_id: str) -> dict:
    from app.maps.map_ops import switch_plan

    _check_map(config, map_id)
    return switch_plan(config, map_id)


@router.get("/switch-plan")
async def get_switch_plan(map_id: str = Query(...), state: WebState = Depends(get_state)) -> dict:
    """What a switch to *map_id* takes along and what the admin may leave
    behind: the switch dialog's content (`map_ops.switch_plan`). Writes nothing."""
    return await asyncio.to_thread(_switch_plan, state.config, map_id)


@router.post("/switch")
async def switch_map(body: MapSwitchRequest, state: WebState = Depends(get_state)):
    from app.maps.map_ops import SwitchChoices, check_switch_choices
    from app.server.service_control import is_server_running

    map_id = body.map_id
    choices = SwitchChoices(
        restore_previous=body.restore_previous,
        apply_recommended=body.apply_recommended,
        apply_mod_xml=body.apply_mod_xml,
        disable_mods=list(body.disable_mods),
        leave_manual_files=list(body.leave_manual_files),
    )

    if await asyncio.to_thread(is_server_running, state.config):
        raise HTTPException(status_code=409, detail="Stop the server before switching maps.")

    config = state.config

    from app.maps.map_ops import vanilla_mission
    from app.shared.seed_data import VANILLA_MAPS, WORKSHOP_MAPS

    workshop_data = WORKSHOP_MAPS.get(map_id)
    vanilla_data = VANILLA_MAPS.get(map_id)
    plan = await asyncio.to_thread(_switch_plan, config, map_id)
    problem = check_switch_choices(plan, choices)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    vanilla_template = (
        await asyncio.to_thread(vanilla_mission, config, map_id) if vanilla_data else None
    )

    async def _job_coro(job: Job) -> dict:
        loop = asyncio.get_running_loop()

        def _mark_enabled(name: str) -> None:
            # mark_enabled also takes the mod out of config.json's update
            # triage, so it is saved the one way a router may save config.
            from app.modmgr.mods import mark_enabled

            asyncio.run_coroutine_threadsafe(
                state.mutate_config(lambda cfg: mark_enabled(cfg, name)), loop,
            ).result()

        if workshop_data is not None:
            result = await _switch_workshop_map(
                job, config, map_id, workshop_data, choices=choices, mark_enabled=_mark_enabled,
            )
        else:
            result = await _switch_vanilla_map(
                job, config, map_id, vanilla_template, choices=choices, mark_enabled=_mark_enabled,
            )
        from app.maps.map_ops import MOD_XML_SKIPPED, apply_mod_xml_to_new_mission

        if choices.apply_mod_xml:
            result["mod_xml_applied"] = await asyncio.to_thread(
                apply_mod_xml_to_new_mission, config,
                lambda line: job.log_line_threadsafe(strip_markup(line)),
            )
        else:
            job.log_line(MOD_XML_SKIPPED)
            result["mod_xml_applied"] = False
        await state.hub.broadcast({"type": "settings_changed"})
        await state.hub.broadcast({"type": "configs_changed"})
        await state.hub.broadcast({"type": "mods_changed"})
        # The editor's state objects capture terrain + mission dir at
        # construction, so a map switch leaves them pointing at the old map.
        await reload_map_editor(state)
        return result

    try:
        job = state.jobs.start(f"Switch map to {map_id}", "map_switch", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}


async def _leave_behind(job: Job, config, map_id: str, choices, mark_enabled) -> dict:
    """The dialog's choices, at the top of the mod-list step (`map_ops.leave_behind`)."""
    from app.maps.map_ops import leave_behind

    return await asyncio.to_thread(
        leave_behind, config, map_id, choices, job.log_line_threadsafe,
        mark_enabled=mark_enabled,
    )


async def _switch_workshop_map(
    job: Job, config, map_id: str, map_data: dict, *, choices=None, mark_enabled=None,
) -> dict:
    from app.maps.map_ops import (
        SwitchChoices,
        backup_map_cfg,
        ensure_map_ids_cached,
        map_mod_items,
        mission_installed,
        partition_old_map_mods,
        remove_old_map_mods_from_server,
        switch_map_cfg,
        update_mods_list_for_map,
    )
    from app.ops import mod_rollback
    from app.ops.fileops import copy_mod_to_server
    from app.parsers.servercfg import set_mission_template
    from app.server.service_ops import generate_service_files_async

    choices = choices or SwitchChoices()
    mission = map_data["mission"]
    template = mission["template"]
    all_mod_items = map_mod_items(map_data)

    mission_dest = config.server_dir_path / "mpmissions" / template
    skip_mission_install = await asyncio.to_thread(mission_installed, mission_dest)
    if skip_mission_install:
        job.log_line(f"Mission folder {template!r} already exists — using it as-is.")

    job.log_line("Step 1/5: Downloading map mods")
    await _download_map_mods(job, config, all_mod_items)

    job.log_line("Step 2/5: Copying map mods to server")
    cached_ids = await asyncio.to_thread(ensure_map_ids_cached, config, map_data)
    generation = mod_rollback.Generation(config, log=job.log_line_threadsafe)
    for mod_name in all_mod_items:
        job.log_line(f"  Copying {mod_name}...")
        await asyncio.to_thread(
            copy_mod_to_server, config, mod_name, cached_ids,
            log=job.log_line_threadsafe, generation=generation,
        )

    job.log_line("Step 3/5: Mission files")
    if not skip_mission_install:
        await _install_mission(job, config, mission, mission_dest, cached_ids)

    job.log_line("Step 4/5: Updating the mod list")
    left = await _leave_behind(job, config, map_id, choices, mark_enabled)
    old_map_mods, kept_deps = await asyncio.to_thread(
        partition_old_map_mods, config, map_data
    )
    await asyncio.to_thread(update_mods_list_for_map, config, map_data)
    if old_map_mods:
        removed = await asyncio.to_thread(remove_old_map_mods_from_server, config, old_map_mods)
        if removed:
            job.log_line(f"  Removed: {', '.join(removed)}")
    _log_kept_deps(job, kept_deps)

    job.log_line("Step 5/5: Setting mission template")
    await asyncio.to_thread(backup_map_cfg, config)
    await asyncio.to_thread(set_mission_template, config.server_cfg_path, template)
    await asyncio.to_thread(
        switch_map_cfg, config, map_id, template, mission,
        restore_previous=choices.restore_previous,
        apply_recommended=choices.apply_recommended,
        log=job.log_line_threadsafe,
    )

    ok = await generate_service_files_async(JobRunner(job), config)
    if not ok:
        job.log_line("Warning: service file install failed — run `zsm service install`.")
    job.log_line(f"Map changed to {map_id}.")
    return _switch_result(map_id, left)


def _switch_result(map_id: str, left: dict) -> dict:
    return {
        "map_id": map_id,
        "left_mods": left["left_mods"],
        "left_manual_files": left["left_manual_files"],
        "turned_on": left["turned_on"],
    }


async def _download_map_mods(job: Job, config, mod_items: dict[str, str]) -> None:
    """A workshop map's mods and deps into the Workshop cache, skipping those already there."""
    from app.shared.steam import download_mod_async, is_mod_downloaded

    for mod_name, mod_id in mod_items.items():
        if await asyncio.to_thread(is_mod_downloaded, config, mod_id):
            job.log_line(f"  {mod_name} already downloaded.")
            continue
        job.log_line(f"  Downloading {mod_name} ({mod_id})...")
        ok = await download_mod_async(JobRunner(job), config, mod_id, mod_name)
        if not ok:
            raise RuntimeError(f"Failed to download {mod_name}")


async def _install_mission(
    job: Job, config, mission: dict, mission_dest: Path, mod_ids: dict[str, str],
) -> None:
    """A workshop map's mission folder, from its ZIP or out of its mod's Workshop copy."""
    from app.maps.map_ops import copy_mission_from_mod, download_and_extract_mission

    source_type = mission.get("source")
    if source_type == "zip" and mission.get("zip_url") and mission.get("mission_path"):
        if mission_dest.exists():
            await asyncio.to_thread(shutil.rmtree, mission_dest)
        await asyncio.to_thread(
            download_and_extract_mission,
            mission["zip_url"], mission["mission_path"], mission_dest,
            log=job.log_line_threadsafe, record_source=True,
        )
    elif source_type == "mod" and mission.get("source_mod") and mission.get("mission_path"):
        if mission_dest.exists():
            await asyncio.to_thread(shutil.rmtree, mission_dest)
        await asyncio.to_thread(
            copy_mission_from_mod, config, mission["source_mod"], mission["mission_path"], mission_dest, mod_ids,
        )
    else:
        job.log_line("  No mission source configured — install manually.")


@router.post("/download")
async def download_map(body: MapDownloadRequest, state: WebState = Depends(get_state)):
    """A workshop map's mods and mission, without switching to it.

    Steps 1 and 3 of a switch, so the Map editor can show the map
    (`map_ops.downloaded_map_mod_dirs`, `find_mission_for_terrain`) and a later
    switch skips the download. The mods stay in the Workshop cache (see
    map_ops' "Download only" note), and the mod list and serverDZ.cfg are not
    touched, so the server may be running.
    """
    from app.shared.seed_data import VANILLA_MAPS, WORKSHOP_MAPS, map_is_released

    map_id = body.map_id
    map_data = WORKSHOP_MAPS.get(map_id)
    if map_data is None:
        if map_id in VANILLA_MAPS:
            raise HTTPException(
                status_code=400, detail=f"{map_id} comes with the server — there is nothing to download.",
            )
        raise HTTPException(status_code=404, detail=f"Unknown map: {map_id!r}")
    if not map_is_released(map_id):
        raise HTTPException(
            status_code=400, detail=f"{map_id} has not been released yet — nothing to download.",
        )
    config = state.config

    async def _job_coro(job: Job) -> dict:
        from app.maps.map_ops import map_mod_items, mission_installed

        mission = map_data["mission"]
        mission_dest = config.server_dir_path / "mpmissions" / mission["template"]
        job.log_line("Step 1/2: Downloading map mods")
        await _download_map_mods(job, config, map_mod_items(map_data))
        job.log_line("Step 2/2: Mission files")
        if await asyncio.to_thread(mission_installed, mission_dest):
            job.log_line(f"  Mission folder {mission['template']!r} already exists — using it as-is.")
        else:
            await _install_mission(job, config, mission, mission_dest, map_mod_items(map_data))
        job.log_line(
            f"Downloaded {map_id}. Your active map, mod list and serverDZ.cfg are unchanged."
        )
        return {"map_id": map_id}

    try:
        job = state.jobs.start(f"Download {map_id}", "map_download", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}


@router.post("/remove")
async def remove_map(body: MapDownloadRequest, state: WebState = Depends(get_state)):
    """A downloaded workshop map's mission folder and the mods only it uses.

    The Maps page's Remove, the undo of Download only, also for a map used
    earlier: its mission goes with its player storage. `map_ops.map_removal_plan`
    keeps a mod the mod list names or another downloaded map lists. The map in
    use is refused, so the server may be running.
    """
    from app.maps.map_ops import get_active_map_name, remove_downloaded_map
    from app.shared.seed_data import VANILLA_MAPS, WORKSHOP_MAPS

    map_id = body.map_id
    if map_id not in WORKSHOP_MAPS:
        if map_id in VANILLA_MAPS:
            raise HTTPException(status_code=400, detail=f"{map_id} comes with the server and cannot be removed.")
        raise HTTPException(status_code=404, detail=f"Unknown map: {map_id!r}")
    config = state.config

    def _refuse_active() -> None:
        if get_active_map_name(config) == map_id:
            raise HTTPException(
                status_code=409, detail=f"{map_id} is the map in use. Switch to another map first.",
            )

    await asyncio.to_thread(_refuse_active)

    async def _job_coro(job: Job) -> dict:
        await asyncio.to_thread(_refuse_active)  # a switch can have run since
        job.log_line(f"Removing {map_id}")
        await asyncio.to_thread(remove_downloaded_map, config, map_id, job.log_line_threadsafe)
        job.log_line(f"Removed {map_id}. Your active map, mod list and serverDZ.cfg are unchanged.")
        return {"map_id": map_id}

    try:
        job = state.jobs.start(f"Remove {map_id}", "map_remove", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}


@router.post("/mission-check")
async def check_mission(body: MapDownloadRequest, state: WebState = Depends(get_state)):
    """Whether upstream changed a community map's mission after Zedstead downloaded it.

    One request to GitHub (`map_ops.mission_update_status`), on the admin's
    click only: a POST, since GitHub allows 60 of them an hour per address.
    The URL comes from the catalog, never from the body.
    """
    from app.maps.map_ops import MissionCheckError, mission_installed, mission_update_status
    from app.shared.seed_data import WORKSHOP_MAPS

    map_data = WORKSHOP_MAPS.get(body.map_id)
    if map_data is None:
        raise HTTPException(status_code=404, detail=f"Unknown community map: {body.map_id!r}")
    mission = map_data["mission"]
    if mission.get("source") != "zip" or not mission.get("zip_url"):
        raise HTTPException(
            status_code=400, detail=f"{body.map_id}'s mission comes with its mod, not from GitHub.",
        )
    mission_dir = state.config.server_dir_path / "mpmissions" / mission["template"]
    if not await asyncio.to_thread(mission_installed, mission_dir):
        raise HTTPException(status_code=400, detail=f"{body.map_id}'s mission is not installed.")
    try:
        result = await asyncio.to_thread(mission_update_status, mission_dir, mission["zip_url"])
    except MissionCheckError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {
        "map_id": body.map_id,
        "newer": result.newer,
        "detail": result.detail,
        "changed": result.changed,
    }


def _log_kept_deps(job: Job, kept_deps: list[str]) -> None:
    """The CLI's `_report_kept_deps`, in a job log. Markup-free, like every
    other line a job writes."""
    if not kept_deps:
        return
    job.log_line(
        f"  Kept: {', '.join(kept_deps)} — other mods are still installed and"
        " may need them."
    )


async def _switch_vanilla_map(
    job: Job, config, map_id: str, template: str, *, choices=None, mark_enabled=None,
) -> dict:
    from app.maps.map_ops import (
        SwitchChoices,
        backup_map_cfg,
        partition_old_map_mods,
        remove_old_map_mods_from_server,
        switch_map_cfg,
        update_mods_list_for_map,
    )
    from app.parsers.servercfg import set_mission_template
    from app.server.service_ops import generate_service_files_async

    choices = choices or SwitchChoices()
    job.log_line("Step 1/3: Updating the mod list")
    left = await _leave_behind(job, config, map_id, choices, mark_enabled)
    old_map_mods, kept_deps = await asyncio.to_thread(
        partition_old_map_mods, config, None
    )
    await asyncio.to_thread(update_mods_list_for_map, config, None)
    if old_map_mods:
        removed = await asyncio.to_thread(remove_old_map_mods_from_server, config, old_map_mods)
        if removed:
            job.log_line(f"  Removed: {', '.join(removed)}")
    _log_kept_deps(job, kept_deps)

    job.log_line("Step 2/3: Setting mission template")
    await asyncio.to_thread(backup_map_cfg, config)
    await asyncio.to_thread(set_mission_template, config.server_cfg_path, template)

    job.log_line("Step 3/3: Config restore")
    await asyncio.to_thread(
        switch_map_cfg, config, map_id, template,
        restore_previous=choices.restore_previous, log=job.log_line_threadsafe,
    )

    ok = await generate_service_files_async(JobRunner(job), config)
    if not ok:
        job.log_line("Warning: service file install failed — run `zsm service install`.")
    job.log_line(f"Map changed to {map_id}.")
    return _switch_result(map_id, left)
