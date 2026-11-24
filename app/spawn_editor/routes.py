"""Map editor REST routes as one ``APIRouter``.

Paths are declared **without** an ``/api`` prefix; ``app.web.routers.map_editor``
includes the router under ``/api/map-editor``. Adding ``/api`` here would yield
``/api/map-editor/api/spawns``. (The prefix is a parameter because a standalone
editor app used to include the same router at ``/api`` — that app is gone, but
the split still keeps mount-root knowledge out of the route declarations.)

Every route resolves its state through ``Depends(get_map_editor)``, i.e. the
``MapEditorState`` hanging off the requesting app's ``WebState``.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from app.maps.locations import load_locations
from app.maps.registry import display_name, known_terrains
from app.spawn_editor.converters import (
    MAP_CALIBRATION,
    game_to_latlng,
    get_map_bounds,
    get_map_size,
    latlng_to_game,
)
from app.spawn_editor import trader_systems
from app.spawn_editor.expansion_state import ObjectPatrolWaypoints
from app.spawn_editor.state import ANY_OWNER, OwnedPositionError
from app.spawn_editor.state_container import MapEditorState
from app.xml.events_xml import custom_event_names

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Write bodies (plans/51 B)
#
# Body keys the editor's writes read as numbers: positions, angles, sizes.
# Whichever of them a write carries must be a finite number, or a list of them
# (a trigger's size and orientation are vectors). Checking them once, before
# any route or state class runs, is what keeps a bad value from reaching a file
# or half-applying in memory for the next save to persist. Test pass 2 got a
# bare 500 from `"lat": "abc"` (N21), and a radius of -50 silently became 1 m
# (N22). An emptied popup field arrives as null: `JSON.stringify(NaN)` is
# `null`, and SafeZonePopup's countdown is not guarded.
# NaN/Infinity never get this far: `app.web.json_guard` refuses them app-wide.
# ---------------------------------------------------------------------------

_NUMBER_KEYS = frozenset({
    "lat", "lng", "x", "y", "z",
    "game_x", "game_y", "game_z",
    "park_lat", "park_lng", "park_game_x", "park_game_y", "park_game_z",
    "angle", "orientation", "park_orientation", "yaw", "pitch", "roll",
    "radius", "height", "size", "countdown", "eye_accommodation",
    "buy_percent", "sell_percent",
})
# Also > 0: a circle or a box with no size. Except a breadcrumb's radius, where
# -1 means "the engine default" — Bohemia's own Enoch and Sakhal files use it.
_POSITIVE_KEYS = frozenset({"radius", "size"})
# Far beyond any map (the largest is ~20 km) and any angle; a value past it is
# a unit mix-up or garbage, not a place.
_NUMBER_LIMIT = 1e7


# Horizontal positions, bounded by the map (test pass 1, F9: X = 99999 was
# written to cfgeventspawns.xml). A write names them directly as game
# coordinates or as the marker's lat/lng, which every state converts with
# `latlng_to_game` and the mission's terrain — so converting here checks
# exactly what would be written. The bound is 0..map_size: every position in
# Bohemia's three stock missions is inside it (cfgeventspawns.xml,
# cfgplayerspawnpoints.xml, cfgundergroundtriggers.json, cfgEffectArea.json;
# measured in 2026 Q3, nearest to an edge 147.9 on Chernarus). Height is not
# bounded.
_GAME_XZ_KEYS = ("x", "z", "game_x", "game_z", "park_game_x", "park_game_z")
_LATLNG_PAIRS = (("lat", "lng"), ("park_lat", "park_lng"))


def _mission_terrain(request: Request) -> str | None:
    """The terrain writes are converted with, or None before one is known.

    Not ``MapEditorState.terrain``: that falls back to Chernarus, and a
    Chernarus bound on a larger map would refuse valid positions."""
    me = getattr(_request_state(request), "map_editor", None)
    spawn = getattr(me, "spawn_state", None)
    return getattr(spawn, "terrain", None) or None


def _off_map_problem(body: dict, terrain: str | None) -> str | None:
    from app.shared.seed_data import MAP_SIZES
    from app.spawn_editor.converters import latlng_to_game

    # A community map with no size entry gets no bound: guessing would refuse
    # valid positions on a larger map.
    size = MAP_SIZES.get(terrain or "")
    if size is None:
        return None
    for key in _GAME_XZ_KEYS:
        value = body.get(key)
        if isinstance(value, (int, float)) and not 0 <= value <= size:
            return f"{key} is outside the map (0–{size})"
    for lat_key, lng_key in _LATLNG_PAIRS:
        lat, lng = body.get(lat_key), body.get(lng_key)
        if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
            x, z = latlng_to_game(lat, lng, terrain)
            if not (0 <= x <= size and 0 <= z <= size):
                return f"The position is outside the map (0–{size})"
    return None


def _number_problem(key: str, value, positive: bool) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return f"{key} must be a number"
    if not math.isfinite(value) or abs(value) > _NUMBER_LIMIT:
        return f"{key} is out of range"
    if positive and value <= 0:
        return f"{key} must be greater than 0"
    return None


async def _check_write_body(request: Request) -> None:
    """Refuse a write whose numeric fields aren't numbers (router dependency).

    Reads the body through ``request.json()``, which Starlette caches on the
    request, so the route's own ``await request.json()`` gets the same dict.
    """
    if request.method not in ("POST", "PUT", "PATCH"):
        return
    raw = await request.body()
    if not raw.strip():
        return
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "Expected a JSON body") from None
    if not isinstance(body, dict):
        return
    breadcrumb = "/breadcrumbs/" in request.url.path
    for key in _NUMBER_KEYS & body.keys():
        value = body[key]
        positive = key in _POSITIVE_KEYS and not (breadcrumb and key == "radius")
        for item in value if isinstance(value, list) else [value]:
            problem = _number_problem(key, item, positive)
            if problem:
                raise HTTPException(400, problem)
    problem = _off_map_problem(body, _mission_terrain(request))
    if problem:
        raise HTTPException(400, problem)


def _need(body: dict, *keys: str) -> list[float]:
    """The numbers a route reads directly; a missing one was a KeyError (500),
    and one that is not a number a ValueError (500): both answer 400."""
    missing = [key for key in keys if body.get(key) is None]
    if missing:
        raise HTTPException(400, f"{' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} required")
    try:
        return [float(body[key]) for key in keys]
    except (TypeError, ValueError):
        raise HTTPException(400, f"{' and '.join(keys)} must be numbers") from None


router = APIRouter(dependencies=[Depends(_check_write_body)])

# ---------------------------------------------------------------------------
# State resolution
# ---------------------------------------------------------------------------

def _request_state(request: Request):
    """The request's instance bundle (its ``/i/<id>/`` prefix, else the
    startup one), or None in an app built without a panel."""
    panel = getattr(request.app.state, "panel", None)
    return panel.bundle_for(request) if panel is not None else None


def get_map_editor(request: Request) -> MapEditorState:
    me = getattr(_request_state(request), "map_editor", None)
    if me is None:
        raise HTTPException(503, "Map editor not initialized")
    return me


# ---------------------------------------------------------------------------
# Guide
# ---------------------------------------------------------------------------

@router.get("/help")
async def get_help() -> dict:
    """The editor guide (``app/data/map_editor_help.json``).

    Rendered by the browser's ? dialog (``HelpDialog.jsx``).
    """
    from app.spawn_editor.help_content import load_raw

    return load_raw()


# ---------------------------------------------------------------------------
# Map info
# ---------------------------------------------------------------------------

def editor_terrains(me: MapEditorState | None) -> list[str]:
    """Every terrain the editor offers: the registry's, plus the loaded one.

    The list is `app.maps.registry`'s — the same map catalog the Maps page
    installs from — so a map added there shows up here without a second edit.
    A community map zsm doesn't ship is added when it is the one loaded: the
    editor renders it off fallback calibration, so it belongs in the picker.
    `GET /maps` and the own-image routes (`app.web.routers.map_editor`) both
    ask this, so a map that can be picked is a map that can get an image.
    """
    terrains = list(known_terrains())
    running = me.spawn_state.terrain if me is not None and me.spawn_state is not None else ""
    if running and running not in terrains:
        terrains.append(running)
    return terrains


@router.get("/maps")
async def get_maps(me: MapEditorState = Depends(get_map_editor)):
    """List available maps (`editor_terrains`) with current map flagged."""
    spawn_state = me.require_spawn()

    maps = []
    for terrain in sorted(editor_terrains(me)):
        maps.append({
            "terrain": terrain,
            "name": display_name(terrain),
            "current": terrain == spawn_state.terrain,
        })
    return maps


@router.get("/map-info")
async def get_map_info(
    terrain: str | None = Query(None),
    me: MapEditorState = Depends(get_map_editor),
):
    """Map metadata: terrain, size, world bounds, calibration scale."""
    spawn_state = me.require_spawn()
    t = terrain or spawn_state.terrain
    cal = MAP_CALIBRATION.get(t, MAP_CALIBRATION.get("chernarusplus", {}))
    from starlette.concurrency import run_in_threadpool
    from app.maps.building_map import find_mission_for_terrain, has_buildings
    from app.maps.cluster_map import has_cluster_files
    from app.maps.map_data import own_image
    from app.maps.registry import cleared_map, roads_allowed
    from app.maps.terrain_map import SOURCE_MAKER, SOURCE_WORLD, heights_source, world_file

    # Header reads, not the drawing; a mod search stats every PBO, so it runs
    # off the event loop.
    def sources():
        search = _world_search(spawn_state, t)
        source = heights_source(*search)
        # Roads come from the world file. A map with its maker's height grid
        # has not looked for one yet.
        roads = roads_allowed(t) and (
            source.kind == SOURCE_WORLD
            or (source.kind == SOURCE_MAKER and world_file(*search) is not None)
        )
        # Without them, an official map's mission can still give its gravel
        # and dirt roads and its forests (app.maps.cluster_map).
        clusters = has_cluster_files(t, find_mission_for_terrain(*_mission_search(spawn_state, t)))
        roads_source = "world" if roads else "mission" if clusters else None
        return source, roads_source, clusters

    source, roads_source, has_forest = await run_in_threadpool(sources)
    own = own_image(t)
    cleared = cleared_map(t)
    try:
        own_stat = own.stat() if own else None
    except OSError:
        own, own_stat = None, None
    return {
        "terrain": t,
        "map_size": get_map_size(t),
        "bounds": get_map_bounds(t),
        "scale": cal.get("scale", 65.0),
        "traderplus": me.traders_state is not None,
        # Expansion's map data is written on the mod's first boot, so this is
        # False both when the mod is absent and when it has never run — the
        # layer toggles say which in their tooltip.
        "expansion": me.has_expansion,
        # RadZone's zones join the Hazard zones family only while the mod is
        # installed and has written its files (plans/48 C).
        "radzone": bool(me.hazard_state and me.hazard_state.has_radzone),
        "toxiczone": bool(me.hazard_state and me.hazard_state.has_toxiczone),
        # Dynamic AI's points and locations (plans/47 D), same rule.
        "dynamic_ai": bool(me.dynamic_ai_state and me.dynamic_ai_state.has_data),
        "has_image": own is not None or bool(cleared and cleared.image_url),
        # A community map whose maker cleared its image (registry.CLEARED_MAPS):
        # /map-image downloads it from zedstead.com on first use. Their credit is
        # shown wherever their image or heights are drawn.
        "image_cleared": bool(cleared and cleared.image_url),
        # A point in each lake above sea level, for the terrain map to fill.
        "lakes": [list(p) for p in cleared.lakes] if cleared else [],
        "credit": (
            {"text": cleared.credit.text, "link_label": cleared.credit.link_label, "url": cleared.credit.url}
            if cleared else None
        ),
        # The image the admin uploaded for this map (plans/69 Step 4). It is
        # the default ground, and `version` changes with each upload so the
        # browser asks for the new one.
        "image_own": (
            {"size": own_stat.st_size, "version": own_stat.st_mtime_ns, "type": own.suffix[1:]}
            if own else None
        ),
        # Whether the server has a world file to draw the terrain map from
        # (plans/70), and when not, why: `terrain_map.world_file_problem`'s
        # missing, encrypted (Sakhal), unknown_version or not_available (a map
        # off registry.heights_allowed), for the Terrain hint. `maker`: the
        # height grid the map's maker gave Zedstead (app.maps.maker_heights).
        # An encrypted world file's terrain comes from the navmesh instead
        # (plans/74), which has no roads.
        "has_terrain": source.kind is not None,
        "terrain_source": source.kind,
        "terrain_problem": source.problem,
        # Off for a map whose roads are not cleared, and a map with its
        # maker's grid whose world file is not on the server. `roads_source`
        # "mission": the world file has none to give (Sakhal's is encrypted),
        # so the gravel and dirt roads come from the mission's cluster files.
        "has_roads": roads_source is not None,
        "roads_source": roads_source,
        # The trees of the mission's cluster files, official maps only.
        "has_forest": has_forest,
        # Whether an installed mission for this map has buildings to draw, so
        # the Buildings toggle can say why it is off before it is ticked.
        "has_buildings": await run_in_threadpool(has_buildings, *_mission_search(spawn_state, t)),
    }


@router.get("/map-image")
async def get_map_image(
    terrain: str | None = Query(None),
    me: MapEditorState = Depends(get_map_editor),
):
    """Serve the admin's own map image for the terrain, else its cleared one.

    A cleared image (`registry.CLEARED_MAPS`) is downloaded from zedstead.com on
    the first request and served from the cache after that.
    """
    spawn_state = me.require_spawn()
    from starlette.concurrency import run_in_threadpool
    from app.maps.map_data import cleared_image, own_image

    t = terrain or spawn_state.terrain
    img_path = own_image(t) or await run_in_threadpool(cleared_image, t)
    if not img_path:
        raise HTTPException(404, "Map image not found")
    return FileResponse(str(img_path), headers={"Cache-Control": "no-store"})


# The 404 of /terrain-heights by `terrain_map.heights_source`'s problem; the
# browser shows it in the toast.
_NO_HEIGHTS = {
    None: "No world file or navmesh for this map on the server",
    "unknown_version": "This map's world file is a version Zedstead cannot read yet",
    "navmesh_unreadable": "This map's navmesh is a version Zedstead cannot read yet",
}


@router.get("/terrain-heights")
async def get_terrain_heights(terrain: str | None = Query(None), me: MapEditorState = Depends(get_map_editor)):
    """The terrain's heights for the canvas terrain map (plans/70), gzipped.

    From the world file, or from the navmesh when the world file is encrypted
    (Sakhal, plans/74): `terrain_map.heights_source`.

    Served as stored, with ``Content-Encoding: gzip``: the browser inflates it
    and the bytes never pass through Python again.
    """
    spawn_state = me.require_spawn()
    from starlette.concurrency import run_in_threadpool
    from app.maps.terrain_map import heights_source, terrain_heights
    t = terrain or spawn_state.terrain
    _require_heights(t)
    search = await run_in_threadpool(_world_search, spawn_state, t)
    gz = await run_in_threadpool(terrain_heights, *search)
    if gz is None:
        # A failed build is remembered, so this asks no file read again.
        problem = (await run_in_threadpool(heights_source, *search)).problem
        raise HTTPException(404, _NO_HEIGHTS.get(problem, _NO_HEIGHTS[None]))
    return FileResponse(str(gz), media_type="application/octet-stream", headers={"Content-Encoding": "gzip"})


@router.get("/roads")
async def get_roads(terrain: str | None = Query(None), me: MapEditorState = Depends(get_map_editor)):
    """The terrain's roads by kind, from the same world file as its heights, gzipped JSON.

    ``{"roads": {kind: [[x0, z0, x1, z1, …], …]}}`` in game metres
    (`app.maps.road_map.encode_roads`). Served as stored, like the heights.
    When the world file gives none (Sakhal's is encrypted), the gravel and
    dirt roads of the mission's cluster files (`app.maps.cluster_map`).
    """
    spawn_state = me.require_spawn()
    from starlette.concurrency import run_in_threadpool
    from app.maps.building_map import find_mission_for_terrain
    from app.maps.cluster_map import mission_roads
    from app.maps.road_map import terrain_roads
    t = terrain or spawn_state.terrain
    from app.maps.registry import roads_allowed
    if not roads_allowed(t):
        raise HTTPException(404, "Roads are not available for this map.")
    gz = await run_in_threadpool(lambda: terrain_roads(*_world_search(spawn_state, t)))
    if gz is not None:
        return FileResponse(str(gz), media_type="application/json", headers={"Content-Encoding": "gzip"})
    body = await run_in_threadpool(
        lambda: mission_roads(t, find_mission_for_terrain(*_mission_search(spawn_state, t)))
    )
    if body is None:
        raise HTTPException(404, "No roads for this map: the server has no world file or mission Zedstead can read them from")
    return Response(body, media_type="application/json", headers={"Content-Encoding": "gzip"})


@router.get("/forest")
async def get_forest(terrain: str | None = Query(None), me: MapEditorState = Depends(get_map_editor)):
    """Where the trees stand, from the mission's cluster files, gzipped JSON.

    ``{"cell": metres, "points": [x0, z0, …], "counts": [n0, …]}``
    (`app.maps.cluster_map.encode_forest`). Official maps only.
    """
    spawn_state = me.require_spawn()
    from starlette.concurrency import run_in_threadpool
    from app.maps.building_map import find_mission_for_terrain
    from app.maps.cluster_map import mission_forest
    t = terrain or spawn_state.terrain
    body = await run_in_threadpool(
        lambda: mission_forest(t, find_mission_for_terrain(*_mission_search(spawn_state, t)))
    )
    if body is None:
        raise HTTPException(404, "No forest for this map: no installed official mission has cluster files")
    return Response(body, media_type="application/json", headers={"Content-Encoding": "gzip"})


def _require_heights(terrain: str | None) -> None:
    """404 for a terrain whose heights may not be read, before any file is looked at."""
    from app.maps.registry import heights_allowed

    if not heights_allowed(terrain):
        raise HTTPException(404, "The terrain layer is not available for this map.")


def _world_search(spawn_state, terrain: str) -> tuple[Path, str, list[Path]]:
    """`terrain_map.world_file`'s arguments: the server dir, the terrain, and the
    Workshop copies of a map downloaded but not switched to (Maps page,
    "Download only"). Stats the Workshop dir, so call it off the event loop."""
    from app.maps.map_ops import downloaded_map_mod_dirs

    config = spawn_state.config
    return config.server_dir_path, terrain, downloaded_map_mod_dirs(config, terrain)


def _mission_search(spawn_state, terrain: str) -> tuple[Path, str, Path | None]:
    """`find_mission_for_terrain`'s arguments: mpmissions, the terrain, the running mission."""
    current = spawn_state.mission_dir if spawn_state.terrain else None
    return spawn_state.config.server_dir_path / "mpmissions", terrain, current


@router.get("/buildings")
async def get_buildings(
    terrain: str | None = Query(None),
    me: MapEditorState = Depends(get_map_editor),
):
    """Every building on *terrain*, from the admin's own installed mission (plans/69).

    ``points`` is flat, ``[x0, z0, x1, z1, …]`` in game metres, converted by the
    page like every other layer. No installed mission for the terrain is an
    answer, not an error: ``mission`` is ``None`` and the page says why the map
    is empty.
    """
    spawn_state = me.require_spawn()
    from starlette.concurrency import run_in_threadpool

    from app.maps.building_map import building_points, find_mission_for_terrain

    t = terrain or spawn_state.terrain

    def _read() -> tuple[str | None, list[tuple[int, int]]]:
        mission = find_mission_for_terrain(*_mission_search(spawn_state, t))
        if mission is None:
            return None, []
        return mission.name, building_points(mission)

    mission, points = await run_in_threadpool(_read)
    return {
        "terrain": t,
        "mission": mission,
        "count": len(points),
        "points": [v for point in points for v in point],
    }


#: A mod's item list in a `/loot` answer is cut at this many rows.
MAX_LOOT_ITEMS = 2000


def _loot_index(spawn_state, terrain: str):
    """``(mission folder name, LootIndex)`` for *terrain*, or ``(None, None)``."""
    from app.maps.building_map import find_mission_for_terrain
    from app.maps.loot_map import index_for

    mission = find_mission_for_terrain(*_mission_search(spawn_state, terrain))
    if mission is None:
        return None, None
    return mission.name, index_for(mission)


def _building_fields(layout: dict) -> dict:
    """`loot_map.building_kinds` as the catalog's three building fields."""
    return {
        "buildings": layout["kinds"],
        "building_points": layout["points"],
        "building_point_kinds": layout["point_kinds"],
    }


@router.get("/loot/catalog")
async def get_loot_catalog(
    terrain: str | None = Query(None),
    me: MapEditorState = Depends(get_map_editor),
):
    """Every type the economy of *terrain*'s installed mission loads, and its owners (plans/82).

    ``items`` is `loot_map.catalog`'s rows, and ``buildings``,
    ``building_points`` and ``building_point_kinds`` are
    `loot_map.building_kinds`'s. No installed mission is an answer
    (``problem: "no_mission"``), as on `/buildings`.
    """
    spawn_state = me.require_spawn()
    from starlette.concurrency import run_in_threadpool

    from app.maps.loot_map import building_kinds, catalog

    t = terrain or spawn_state.terrain

    def _read() -> dict:
        mission, index = _loot_index(spawn_state, t)
        if index is None:
            return {"terrain": t, "mission": None, "problem": "no_mission", "owners": [],
                    "categories": [], "usages": [], "items": [], "buildings": [], "building_points": [],
                    "building_point_kinds": [], "unreadable": []}
        return {
            "terrain": t,
            "mission": mission,
            "problem": index.problem,
            **catalog(index),
            **_building_fields(building_kinds(index)),
            "unreadable": index.unreadable,
        }

    return await run_in_threadpool(_read)


@router.get("/loot/at")
async def get_loot_at(
    lat: float = Query(...),
    lng: float = Query(...),
    radius: float = Query(30, ge=1, le=500),
    terrain: str | None = Query(None),
    me: MapEditorState = Depends(get_map_editor),
):
    """The building nearest to a map point, within *radius* metres, and every
    item that can spawn in it (`loot_map.building_report`).

    ``building`` is None when no building is that close. The point is display
    lat/lng, projected here like every write's.
    """
    spawn_state = me.require_spawn()
    from starlette.concurrency import run_in_threadpool

    from app.maps.loot_map import building_at, building_report
    from app.spawn_editor.converters import game_to_latlng, latlng_to_game

    t = terrain or spawn_state.terrain

    def _read() -> dict:
        mission, index = _loot_index(spawn_state, t)
        if index is None:
            raise HTTPException(404, f"No installed mission is for the map {t}.")
        x, z = latlng_to_game(lat, lng, t)
        hit = building_at(index, x, z, radius)
        building = None
        if hit is not None:
            building = building_report(index, *hit)
            b_lat, b_lng = game_to_latlng(*hit[1], t)
            building["lat"], building["lng"] = b_lat, b_lng
        return {"terrain": t, "mission": mission, "building": building}

    return await run_in_threadpool(_read)


@router.get("/loot")
async def get_loot(
    terrain: str | None = Query(None),
    item: str | None = Query(None, max_length=200),
    mod: str | None = Query(None, max_length=200),
    me: MapEditorState = Depends(get_map_editor),
):
    """Every building where one item, or every item of one owner, can spawn (plans/82).

    Exactly one of ``item`` (a classname) and ``mod`` (an owner key from
    `/loot/catalog`), matched without regard to case. The answer is
    `loot_map.match`'s: an upper bound by usage, category and tag, with
    ``tiers_unchecked`` when an item's value tiers could not be checked.
    Neither value is ever joined onto a path.
    """
    spawn_state = me.require_spawn()
    from starlette.concurrency import run_in_threadpool

    from app.maps.loot_map import label_of, match, owner_from_key, owner_key

    if (item is None) == (mod is None):
        raise HTTPException(400, "Name either an item or a mod, not both and not neither.")
    t = terrain or spawn_state.terrain

    def _read() -> dict:
        mission, index = _loot_index(spawn_state, t)
        if index is None:
            raise HTTPException(404, f"No installed mission is for the map {t}.")
        if item is not None:
            key = item.strip().lower()
            found = index.types.get(key)
            if found is None:
                raise HTTPException(404, f"No type named {item!r} is loaded by {mission}.")
            names = [key]
            selection = {"kind": "item", "key": found.name, "label": found.name}
        else:
            owner = owner_from_key(mod, index)
            if owner is None:
                raise HTTPException(404, f"Nothing loaded by {mission} comes from {mod!r}.")
            names = sorted(
                (k for k, info in index.types.items() if info.owner == owner),
                key=lambda k: index.types[k].name.lower(),
            )
            selection = {"kind": "mod", "key": owner_key(owner), "label": label_of(owner)}
        result = match(index, names)
        truncated = len(result["items"]) > MAX_LOOT_ITEMS
        if truncated:
            result["items"] = result["items"][:MAX_LOOT_ITEMS]
        return {
            "terrain": t,
            "mission": mission,
            "selection": selection,
            "unreadable": index.unreadable,
            "problem": index.problem,
            "truncated": truncated,
            **result,
        }

    return await run_in_threadpool(_read)


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

def locations_for_terrain(terrain: str, server_dir=None) -> list[dict]:
    """Location database for *terrain*, converted to display lat/lng.

    Pure helper (no request, no editor state) so both the route and tests can
    call it directly. *server_dir* lets an official map without a label file
    take its names from its own world config (`load_locations`).
    """
    converted = []
    # The own names come first (`load_locations`), so an own entry's `index`
    # is its place in the label file, the index the own-name routes take.
    for i, loc in enumerate(load_locations(terrain, server_dir)):
        lat, lng = game_to_latlng(loc.x, loc.z, terrain)
        entry = {"name": loc.name, "lat": round(lat, 6), "lng": round(lng, 6), "type": loc.type}
        if loc.label:
            entry["label"] = loc.label
        if loc.own:
            entry["own"] = True
            entry["index"] = i
        converted.append(entry)
    return converted


@router.get("/locations")
async def get_locations(
    terrain: str | None = Query(None),
    me: MapEditorState = Depends(get_map_editor),
):
    """Location database (game coords converted to display lat/lng)."""
    spawn_state = me.require_spawn()
    t = terrain or spawn_state.terrain
    config = getattr(spawn_state, "config", None)
    server_dir = config.server_dir_path if config is not None else None
    from starlette.concurrency import run_in_threadpool

    return JSONResponse(await run_in_threadpool(locations_for_terrain, t, server_dir))


# The admin's own place names (plans/87): added, changed and removed on the
# mission's own terrain, as every map editor write is. Export and import take
# any terrain the registry knows, so a file moves between servers. A label
# file that cannot be read refuses every write (409) and stays as it is.

def _location_file_http(exc) -> HTTPException:
    """409 for a stored label file that cannot be read, 400 for a refused value."""
    from app.maps.locations import LocationFileUnreadable

    return HTTPException(409 if isinstance(exc, LocationFileUnreadable) else 400, str(exc))


def _own_place_fields(body: dict, terrain: str, base: dict | None = None) -> dict:
    """The label-file entry a POST or PUT body asks for: *base* (the place as
    it is) with the body's ``name``, ``type``, ``label`` and ``lat``/``lng``."""
    from app.spawn_editor.converters import latlng_to_game

    entry = dict(base or {"type": "Local"})
    for key in ("name", "type", "label"):
        if key in body:
            entry[key] = body[key]
    if "lat" in body or "lng" in body:
        entry["x"], entry["z"] = latlng_to_game(*_need(body, "lat", "lng"), terrain)
    return entry


class _StalePlace(Exception):
    """The place at an index is no longer the one the caller's list showed."""


def _own_place_at(places: list, index: int, expect: str | None):
    """The own place at *index*. A write addresses a place by its position,
    so *expect* (the name the caller's list showed there) guards it: another
    tab's removal shifts every later index, and a stale list would otherwise
    rename or remove the wrong place."""
    if not 0 <= index < len(places):
        raise IndexError(index)
    if expect is not None and places[index].name != expect:
        raise _StalePlace(index)
    return places[index]


async def _change_own_places(terrain: str, change) -> None:
    """Run `locations.change_own_locations` off the event loop and turn its
    refusals into HTTP: a missing index is 404, a bad file or value 409/400."""
    from starlette.concurrency import run_in_threadpool
    from app.maps import locations

    try:
        await run_in_threadpool(locations.change_own_locations, terrain, change)
    except IndexError:
        raise HTTPException(404, "That place name is not in your label file") from None
    except _StalePlace:
        raise HTTPException(409, "The place names changed since this list loaded. Look at the list again.") from None
    except locations.LocationFileError as exc:
        raise _location_file_http(exc) from exc


def _mission_terrain_or_400(me: MapEditorState) -> str:
    """The mission's own terrain, the only one an own-name write may change; 400 without one."""
    terrain = getattr(me.require_spawn(), "terrain", None)
    if not terrain:
        raise HTTPException(400, "No mission map is loaded")
    return terrain


@router.post("/locations/own")
async def add_own_location(request: Request, me: MapEditorState = Depends(get_map_editor)):
    """Add one of the admin's own place names at ``lat``/``lng``."""
    from app.maps import locations

    terrain = _mission_terrain_or_400(me)
    body = await request.json()
    _need(body, "lat", "lng")

    def add(places):
        entry = _own_place_fields(body, terrain)
        return places + [locations.make_location(entry, locations.own_map_size(terrain))]

    await _change_own_places(terrain, add)
    return {"ok": True}


@router.put("/locations/own/{index}")
async def update_own_location(
    index: int, request: Request, expect: str | None = Query(None), me: MapEditorState = Depends(get_map_editor)
):
    """Rename, retype, relabel or move one of the admin's own place names."""
    from app.maps import locations

    terrain = _mission_terrain_or_400(me)
    body = await request.json()

    def update(places):
        entry = _own_place_fields(body, terrain, locations.location_entry(_own_place_at(places, index, expect)))
        places[index] = locations.make_location(entry, locations.own_map_size(terrain))
        return places

    await _change_own_places(terrain, update)
    return {"ok": True}


@router.delete("/locations/own/{index}")
async def delete_own_location(
    index: int, expect: str | None = Query(None), me: MapEditorState = Depends(get_map_editor)
):
    """Remove one of the admin's own place names."""
    terrain = _mission_terrain_or_400(me)

    def remove(places):
        _own_place_at(places, index, expect)
        return places[:index] + places[index + 1:]

    await _change_own_places(terrain, remove)
    return {"ok": True}


def _known_terrain_or_400(terrain: str | None, me: MapEditorState) -> str:
    """*terrain* (else the mission's) when it is the mission's own map or one
    the registry knows."""
    mission = (getattr(me.spawn_state, "terrain", None) or "").lower()
    t = (terrain or mission).lower()
    if not t or (t != mission and t not in {k.lower() for k in known_terrains()}):
        raise HTTPException(400, "Unknown map")
    return t


@router.get("/locations/export")
async def export_own_locations(terrain: str | None = Query(None), me: MapEditorState = Depends(get_map_editor)):
    """The admin's own label file for *terrain*, as a download that another
    Zedstead imports (game coordinates, so it fits the same map anywhere)."""
    from fastapi.responses import Response
    from app.maps import locations

    t = _known_terrain_or_400(terrain, me)
    try:
        text = locations.export_own_locations(t)
    except locations.LocationFileError as exc:
        raise _location_file_http(exc) from exc
    return Response(
        text,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="locations_{t}.json"'},
    )


@router.post("/locations/import")
async def import_own_locations(
    request: Request, terrain: str | None = Query(None), me: MapEditorState = Depends(get_map_editor)
):
    """Add the places of an exported label file (``places``) to the own names
    of *terrain*, or put them in place of them (``replace``)."""
    from starlette.concurrency import run_in_threadpool
    from app.maps import locations

    t = _known_terrain_or_400(terrain, me)
    body = await request.json()
    if not isinstance(body, dict) or "places" not in body:
        raise HTTPException(400, "Expected {places: [...], replace: true|false}")
    try:
        added, skipped = await run_in_threadpool(
            lambda: locations.import_own_locations(t, body["places"], replace=bool(body.get("replace")))
        )
    except locations.LocationFileError as exc:
        raise _location_file_http(exc) from exc
    return {"added": added, "skipped": skipped}


# ---------------------------------------------------------------------------
# Spawns CRUD
# ---------------------------------------------------------------------------

@router.get("/spawns")
async def get_spawns(me: MapEditorState = Depends(get_map_editor)):
    """All eventspawn entries with positions (converted to lat/lng)."""
    return me.require_spawn().get_spawns_json()


def _spawn_owner(owner: str | None):
    """The ``owner`` query of a spawn write: absent means "the first entry of
    that name" (the old behaviour), empty means the mission's own entry, any
    other value a mod's or a manual file's (``@user:<id>``).

    A mod's position is written only with ``copy``: the mod's file (or, empty,
    the one the merge record names) is copied into a manual XML file first and
    the change goes into the copy. Without it the write is a 409."""
    if owner is None:
        return ANY_OWNER
    return owner or None


async def _owned_write(call):
    """Run a spawn write; a position the editor will not write where it is
    (a mod's, or a manual file that cannot be written back) is a 409 whose
    detail says where to change it."""
    try:
        return await call
    except OwnedPositionError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/spawns-copy-source")
async def spawn_copy_source(mod: str, me: MapEditorState = Depends(get_map_editor)):
    """Which of *mod*'s cfgeventspawns files "Copy into a manual file" copies:
    ``{"source", "sources"}``, from the merge record. ``source`` is null when
    the record does not name exactly one; the page lists ``sources`` then."""
    return me.require_spawn().copy_source(mod)


@router.post("/spawns/{event_name}")
async def add_spawn(
    event_name: str,
    request: Request,
    owner: str | None = Query(None),
    entry: int = Query(0),
    copy: str | None = Query(None),
    me: MapEditorState = Depends(get_map_editor),
):
    """Add a new spawn position."""
    body = await request.json()
    lat, lng = _need(body, "lat", "lng")
    result = await _owned_write(me.require_spawn().add_spawn_position(
        event_name, lat, lng, body.get("angle", -1), _spawn_owner(owner), entry, copy,
    ))
    if result is None:
        raise HTTPException(404, f"Event '{event_name}' not found")
    return result


@router.put("/spawns/{event_name}/{pos_index}")
async def update_spawn(
    event_name: str,
    pos_index: int,
    request: Request,
    owner: str | None = Query(None),
    entry: int = Query(0),
    copy: str | None = Query(None),
    me: MapEditorState = Depends(get_map_editor),
):
    """Update a spawn position."""
    body = await request.json()
    lat, lng = _need(body, "lat", "lng")
    # No angle keeps the current one; -1 used to overwrite it with "random".
    result = await _owned_write(me.require_spawn().update_spawn_position(
        event_name, pos_index, lat, lng, body.get("angle"), _spawn_owner(owner), entry, copy,
    ))
    if result is None:
        raise HTTPException(404, "Position not found")
    return result


@router.put("/spawns/{event_name}/{pos_index}/game")
async def update_spawn_game(
    event_name: str,
    pos_index: int,
    request: Request,
    owner: str | None = Query(None),
    entry: int = Query(0),
    copy: str | None = Query(None),
    me: MapEditorState = Depends(get_map_editor),
):
    """Update a spawn position using game coordinates directly."""
    body = await request.json()
    x, z = _need(body, "x", "z")
    result = await _owned_write(me.require_spawn().update_spawn_position_game(
        event_name, pos_index, x, z, body.get("angle"), _spawn_owner(owner), entry, copy,
    ))
    if result is None:
        raise HTTPException(404, "Position not found")
    return result


@router.delete("/spawns/{event_name}/{pos_index}")
async def delete_spawn(
    event_name: str,
    pos_index: int,
    owner: str | None = Query(None),
    entry: int = Query(0),
    copy: str | None = Query(None),
    me: MapEditorState = Depends(get_map_editor),
):
    """Delete a spawn position."""
    ok = await _owned_write(me.require_spawn().delete_spawn_position(
        event_name, pos_index, _spawn_owner(owner), entry, copy,
    ))
    if not ok:
        raise HTTPException(404, "Position not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

@router.get("/events")
async def get_events(me: MapEditorState = Depends(get_map_editor)):
    """All events with metadata, flags, radii, and spawn counts."""
    events_state = me.require_events()
    counts = me.spawn_state.get_spawn_counts() if me.spawn_state else {}
    return events_state.get_all_events(counts)


@router.get("/events-for-class/{classname}")
async def get_events_for_class(classname: str, me: MapEditorState = Depends(get_map_editor)):
    """The events.xml events that spawn *classname*, each with its position count.

    The Items tab offers to add positions to one of these before it offers a
    new event. A mod's events in custom/ are not listed: their children are
    the mod's to change.
    """
    events_state = me.require_events()
    counts = me.spawn_state.get_spawn_counts() if me.spawn_state else {}
    return [
        {"name": name, "spawn_count": counts.get(name, 0)}
        for name in events_state.events_with_child(classname)
    ]


@router.post("/events")
async def create_event(request: Request, me: MapEditorState = Depends(get_map_editor)):
    """Create a new event in both events.xml and cfgeventspawns.xml."""
    events_state = me.require_events()
    spawn_state = me.require_spawn()
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(400, "Event name is required")
    # The Items tab's lasting placement: a new event that spawns this class.
    # Only for a new event, so an existing one never gains a child here.
    child = body.get("child")
    if child is not None:
        from app.spawn_editor.object_spawner import ObjectSpawnerError, check_classname

        try:
            child = check_classname(child)
        except ObjectSpawnerError as e:
            raise HTTPException(e.status, e.message)
        if events_state.get_entry(name) is not None or spawn_state.has_event(name) or name in custom_event_names(
            spawn_state.mission_dir
        ):
            raise HTTPException(409, f"Event '{name}' already exists. Pick another name, or add positions to it.")

    # An event defined by a mod's CE-linked custom/ file is already live on the
    # server, so writing a second definition into db/events.xml would duplicate
    # it. Only its cfgeventspawns.xml half can be missing.
    if events_state.get_entry(name) is None and name in custom_event_names(
        spawn_state.mission_dir
    ):
        if spawn_state.has_event(name):
            raise HTTPException(409, f"Event '{name}' already exists")
        await spawn_state.create_event_spawn_entry(name)
        return {"name": name, "spawn_count": 0, "defined_in": "custom"}

    # Create in events.xml
    result = await events_state.create_new_event(name, child=child)
    if result is None:
        # Already in events.xml. That is only a conflict when cfgeventspawns.xml
        # has it too: an events.xml-only event (mods ship plenty, and the map
        # editor lists none of them because it reads positions) otherwise had no
        # way to ever get positions — the "Event X is not in cfgeventspawns.xml"
        # dead end behind every "View <event> on map" link.
        entry = events_state.get_entry(name)
        if entry is None or spawn_state.has_event(name):
            raise HTTPException(409, f"Event '{name}' already exists")
        result = events_state.get_event_detail(name)

    # Create empty entry in cfgeventspawns.xml and register event metadata
    event_entry = events_state.get_entry(name)
    await spawn_state.create_event_spawn_entry(name, event_entry)

    return result


@router.get("/events/{event_name}")
async def get_event_detail(
    event_name: str, me: MapEditorState = Depends(get_map_editor)
):
    """Full detail for a single event."""
    events_state = me.require_events()
    counts = me.spawn_state.get_spawn_counts() if me.spawn_state else {}
    result = events_state.get_event_detail(event_name, counts.get(event_name, 0))
    if result is None:
        # A mod's event lives in custom/<mod>-events.xml, which the merge
        # regenerates — editable on the Mod XML page, never here.
        if me.spawn_state and event_name in custom_event_names(me.spawn_state.mission_dir):
            raise HTTPException(
                404,
                f"'{event_name}' is defined by a mod's custom/ economy file, not "
                "db/events.xml. Its positions are editable here; its nominal, "
                "lifetime and flags on the Custom config tab.",
            )
        raise HTTPException(404, f"Event '{event_name}' not found")
    return result


@router.get("/type-names")
async def get_type_names(me: MapEditorState = Depends(get_map_editor)):
    """Classname suggestions from types.xml, mod XMLs, and PBO classes."""
    spawn_state = me.require_spawn()
    events_state = me.events_state
    config = me.config
    from app.spawn_editor.classname_sources import (
        get_mod_xml_names,
        get_pbo_class_names,
        get_types_xml_names,
    )

    # Exclusion is keyed on cfgeventspawns.xml, NOT on events.xml: an event that
    # exists only in events.xml (no <pos> entries) is exactly the one a user
    # needs to find here, since the map lists nothing without positions.
    existing = set()
    if spawn_state._eventspawns:
        for entry in spawn_state._eventspawns.entries:
            existing.add(entry.name.lower())

    sources: list[dict] = []
    seen: set[str] = set()

    # Source 0: events that are already defined — in db/events.xml or in a mod's
    # CE-linked custom/ file — but have no positions yet. First, so the dedupe
    # below keeps them under this label rather than under types.xml.
    defined = {}
    if events_state and events_state._event_map:
        defined = {name: "in events.xml, no positions yet" for name in events_state._event_map}
    for name in custom_event_names(spawn_state.mission_dir):
        defined.setdefault(name, "in a mod's custom/ economy file, no positions yet")
    no_pos = [
        {"name": name, "detail": detail}
        for name, detail in defined.items()
        if name.lower() not in existing
    ]
    if no_pos:
        sources.append({"label": "events.xml (no map positions)", "names": no_pos})
        seen.update(n["name"].lower() for n in no_pos)

    # Source 1: types.xml
    types_src = get_types_xml_names(spawn_state.mission_dir)
    types_src["names"] = [
        n for n in types_src["names"]
        if n["name"].lower() not in existing and n["name"].lower() not in seen
    ]
    seen.update(n["name"].lower() for n in types_src["names"])
    if types_src["names"]:
        sources.append(types_src)

    # Source 2: mod XML files
    mod_src = get_mod_xml_names(config.server_dir_path)
    mod_src["names"] = [
        n for n in mod_src["names"]
        if n["name"].lower() not in existing and n["name"].lower() not in seen
    ]
    seen.update(n["name"].lower() for n in mod_src["names"])
    if mod_src["names"]:
        sources.append(mod_src)

    # Source 3: PBO CfgVehicles classes
    try:
        pbo_src = get_pbo_class_names(config)
        pbo_src["names"] = [
            n for n in pbo_src["names"]
            if n["name"].lower() not in existing and n["name"].lower() not in seen
        ]
        seen.update(n["name"].lower() for n in pbo_src["names"])
        if pbo_src["names"]:
            sources.append(pbo_src)
    except Exception:
        log.debug("PBO classname scan failed", exc_info=True)

    return {"sources": sources, "total": sum(len(s["names"]) for s in sources)}


@router.put("/events/{event_name}")
async def update_event(
    event_name: str, request: Request, me: MapEditorState = Depends(get_map_editor)
):
    """Update event fields (partial — only fields in body are changed)."""
    from app.xml.events_xml import EVENT_FLAG_NAMES, NUMERIC_FIELDS

    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "Expected a JSON object")
    # update_event reads these with int(): "abc" or null was a ValueError or
    # TypeError, a bare 500, after the fields before it had been applied.
    for key in (*NUMERIC_FIELDS, *EVENT_FLAG_NAMES):
        if key in body:
            try:
                int(body[key])
            except (TypeError, ValueError):
                raise HTTPException(400, f"{key} must be a whole number") from None
    result = await me.require_events().update_event(event_name, body)
    if result is None:
        raise HTTPException(404, f"Event '{event_name}' not found")
    return result


@router.put("/events/{event_name}/active")
async def toggle_event_active(
    event_name: str, me: MapEditorState = Depends(get_map_editor)
):
    """Toggle event active/inactive."""
    result = await me.require_events().toggle_active(event_name)
    if result is None:
        raise HTTPException(404, f"Event '{event_name}' not found")
    return result


# ---------------------------------------------------------------------------
# Collision detection
# ---------------------------------------------------------------------------

_EMPTY_COLLISIONS = {"collisions": [], "scanned_events": 0, "scanned_positions": 0, "skipped": []}


@router.get("/collisions/eventspawn-names")
async def get_eventspawn_names(me: MapEditorState = Depends(get_map_editor)):
    """List event names available in cfgeventspawns.xml for filtering."""
    if not me.spawn_state or not me.spawn_state._eventspawns:
        return {"names": []}
    return {"names": sorted(e.name for e in me.spawn_state._eventspawns.entries)}


@router.get("/collisions/eventspawns")
async def get_eventspawn_collisions(
    events: str | None = Query(None),
    check_radius: bool = Query(False),
    check_overlap: bool = Query(False),
    me: MapEditorState = Depends(get_map_editor),
):
    """Scan eventspawns for coordinate collisions."""
    spawn_state = me.require_spawn()
    from app.ops.collision_ops import scan_eventspawn_collisions

    event_names = set(events.split(",")) if events else None
    report = scan_eventspawn_collisions(
        spawn_state._eventspawns,
        me.events_state._events if me.events_state else None,
        event_names=event_names,
        check_radius=check_radius,
        check_overlap=check_overlap,
    )
    return _collision_report_to_json(report, spawn_state.terrain)


@router.get("/collisions/territory-file-names")
async def get_territory_file_names(me: MapEditorState = Depends(get_map_editor)):
    """List territory XML filenames in env/ for filtering."""
    from app.xml.xml_utils import get_mission_dir

    mission_dir = get_mission_dir(me.config)
    if not mission_dir:
        return {"names": []}
    env_dir = mission_dir / "env"
    if not env_dir.is_dir():
        return {"names": []}
    return {"names": sorted(p.name for p in env_dir.glob("*territories*.xml"))}


@router.get("/collisions/territories")
async def get_territory_collisions(
    files: str | None = Query(None),
    me: MapEditorState = Depends(get_map_editor),
):
    """Scan territory zones for circle overlaps."""
    from app.ops.collision_ops import scan_territory_collisions_from_dir
    from app.xml.xml_utils import get_mission_dir

    mission_dir = get_mission_dir(me.config)
    if not mission_dir:
        return dict(_EMPTY_COLLISIONS)

    report = scan_territory_collisions_from_dir(mission_dir, set(files.split(",")) if files else None)
    return _collision_report_to_json(report, me.terrain)


@router.get("/collisions/playerspawn-section-names")
async def get_playerspawn_section_names(me: MapEditorState = Depends(get_map_editor)):
    """List section names available in cfgPlayerSpawnPoints.xml."""
    ps = me.playerspawn_state
    if not ps or not ps._spawnpoints:
        return {"names": []}
    return {"names": list(ps._spawnpoints.sections.keys())}


@router.get("/collisions/playerspawns")
async def get_playerspawn_collisions(
    sections: str | None = Query(None),
    me: MapEditorState = Depends(get_map_editor),
):
    """Scan player spawn points for coordinate collisions."""
    ps = me.playerspawn_state
    if not ps or not ps._spawnpoints:
        return dict(_EMPTY_COLLISIONS)

    from copy import copy

    from app.ops.collision_ops import scan_playerspawn_collisions

    spf = ps._spawnpoints
    if sections:
        requested = set(sections.split(","))
        spf = copy(spf)
        spf.sections = {k: v for k, v in spf.sections.items() if k in requested}

    report = scan_playerspawn_collisions(spf)
    return _collision_report_to_json(report, ps.terrain)


def _collision_report_to_json(report, terrain: str) -> dict:
    """Convert a CollisionReport to JSON with lat/lng coords."""
    items = []
    for c in report.collisions:
        lat_a, lng_a = game_to_latlng(c.pos_a[0], c.pos_a[1], terrain)
        lat_b, lng_b = game_to_latlng(c.pos_b[0], c.pos_b[1], terrain)
        items.append({
            "kind": c.kind,
            "severity": c.severity,
            "event_a": c.event_a,
            "pos_a_index": c.pos_a_index,
            "pos_a": {"lat": lat_a, "lng": lng_a, "game_x": c.pos_a[0], "game_z": c.pos_a[1]},
            "event_b": c.event_b,
            "pos_b_index": c.pos_b_index,
            "pos_b": {"lat": lat_b, "lng": lng_b, "game_x": c.pos_b[0], "game_z": c.pos_b[1]},
            "distance": c.distance,
            "radius": c.radius,
            "description": c.description,
        })
    return {
        "collisions": items,
        "scanned_events": report.scanned_events,
        "scanned_positions": report.scanned_positions,
        "skipped": report.skipped,
    }


@router.get("/collisions/effectareas")
async def get_effectarea_collisions(me: MapEditorState = Depends(get_map_editor)):
    """Scan effect areas for circle overlaps."""
    from app.ops.collision_ops import scan_effectarea_collisions_from_path
    from app.parsers.effectarea_json import get_effectarea_path
    from app.xml.xml_utils import get_mission_dir

    mission_dir = get_mission_dir(me.config)
    if not mission_dir:
        return dict(_EMPTY_COLLISIONS)

    ea_path = get_effectarea_path(mission_dir)
    if not ea_path:
        return dict(_EMPTY_COLLISIONS)

    report = scan_effectarea_collisions_from_path(ea_path)
    return _collision_report_to_json(report, me.terrain)


@router.get("/collisions/traderplus")
async def get_traderplus_collisions(me: MapEditorState = Depends(get_map_editor)):
    """Scan TraderPlus configs for coordinate collisions."""
    from app.ops.collision_ops import read_traderplus_configs, scan_traderplus_collisions

    sz_cfg, gen_cfg, gar_cfg, unread = read_traderplus_configs(me.config)
    report = scan_traderplus_collisions(sz_cfg, gen_cfg, gar_cfg)
    report.skipped = unread + report.skipped
    return _collision_report_to_json(report, me.terrain)


@router.get("/collisions/all")
async def get_all_collisions(
    events: str | None = Query(None),
    check_radius: bool = Query(False),
    check_overlap: bool = Query(False),
    me: MapEditorState = Depends(get_map_editor),
):
    """Run all collision scans including cross-file checks."""
    from app.ops.collision_ops import scan_all_collisions
    from app.xml.xml_utils import get_mission_dir

    mission_dir = get_mission_dir(me.config)
    if not mission_dir:
        return dict(_EMPTY_COLLISIONS)

    event_names = set(events.split(",")) if events else None
    report = scan_all_collisions(
        mission_dir, me.config, event_names=event_names,
        check_radius=check_radius, check_overlap=check_overlap,
    )
    return _collision_report_to_json(report, me.terrain)


# ---------------------------------------------------------------------------
# Player spawn points
# ---------------------------------------------------------------------------

@router.get("/player-spawns")
async def get_player_spawns(me: MapEditorState = Depends(get_map_editor)):
    """Player spawn points with full section/group/position data."""
    if not me.playerspawn_state:
        return []
    return me.playerspawn_state.get_json()


@router.get("/player-spawns/param-metadata")
async def get_player_spawn_param_metadata(me: MapEditorState = Depends(get_map_editor)):
    """Field lists and descriptions for section parameter forms."""
    if not me.playerspawn_state:
        return {}
    return me.playerspawn_state.get_param_metadata()


@router.post("/player-spawns/{section}/groups")
async def add_player_spawn_group(
    section: str, request: Request, me: MapEditorState = Depends(get_map_editor)
):
    """Add a new group to a section."""
    ps = me.require_player_spawns()
    body = await request.json()
    result = await ps.add_group(section, body.get("name", "NewGroup"))
    if result is None:
        raise HTTPException(404, f"Section '{section}' not found")
    return result


@router.delete("/player-spawns/{section}/groups/{group_idx}")
async def delete_player_spawn_group(
    section: str, group_idx: int, me: MapEditorState = Depends(get_map_editor)
):
    """Remove a group from a section."""
    ok = await me.require_player_spawns().remove_group(section, group_idx)
    if not ok:
        raise HTTPException(404, "Group not found")
    return {"ok": True}


@router.post("/player-spawns/{section}/groups/{group_idx}/positions")
async def add_player_spawn_position(
    section: str,
    group_idx: int,
    request: Request,
    me: MapEditorState = Depends(get_map_editor),
):
    """Add a position to a group.

    Accepts either game coordinates (``x``/``z``) or display coordinates
    (``lat``/``lng``). The lat/lng form exists so map clients never re-derive
    the projection themselves — the old JS frontend did, with the Z axis
    mirrored about the map centre, so every clicked player spawn landed on the
    wrong side of the map.
    """
    ps = me.require_player_spawns()
    body = await request.json()
    if "x" in body and "z" in body:
        x, z = _need(body, "x", "z")
    elif "lat" in body and "lng" in body:
        from app.spawn_editor.converters import latlng_to_game

        x, z = latlng_to_game(*_need(body, "lat", "lng"), ps.terrain)
    else:
        raise HTTPException(400, "Provide either x/z or lat/lng")

    result = await ps.add_position(section, group_idx, x, z)
    if result is None:
        raise HTTPException(404, "Section or group not found")
    return result


@router.put("/player-spawns/{section}/groups/{group_idx}/positions/{pos_idx}/game")
async def update_player_spawn_position(
    section: str,
    group_idx: int,
    pos_idx: int,
    request: Request,
    me: MapEditorState = Depends(get_map_editor),
):
    """Update a position's game coordinates."""
    body = await request.json()
    result = await me.require_player_spawns().update_position(
        section, group_idx, pos_idx, *_need(body, "x", "z"),
    )
    if result is None:
        raise HTTPException(404, "Position not found")
    return result


@router.put("/player-spawns/{section}/groups/{group_idx}/positions/{pos_idx}")
async def update_player_spawn_position_latlng(
    section: str,
    group_idx: int,
    pos_idx: int,
    request: Request,
    me: MapEditorState = Depends(get_map_editor),
):
    """Update a position using display lat/lng (from drag)."""
    body = await request.json()
    result = await me.require_player_spawns().update_position_latlng(
        section, group_idx, pos_idx, *_need(body, "lat", "lng"),
    )
    if result is None:
        raise HTTPException(404, "Position not found")
    return result


@router.delete("/player-spawns/{section}/groups/{group_idx}/positions/{pos_idx}")
async def delete_player_spawn_position(
    section: str,
    group_idx: int,
    pos_idx: int,
    me: MapEditorState = Depends(get_map_editor),
):
    """Delete a position from a group."""
    ok = await me.require_player_spawns().delete_position(section, group_idx, pos_idx)
    if not ok:
        raise HTTPException(404, "Position not found")
    return {"ok": True}


@router.put("/player-spawns/{section}/params/{param_type}")
async def update_player_spawn_params(
    section: str,
    param_type: str,
    request: Request,
    me: MapEditorState = Depends(get_map_editor),
):
    """Update section parameters (spawn/generator/group)."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "Send the parameters as an object.")
    try:
        result = await me.require_player_spawns().update_params(section, param_type, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    if result is None:
        raise HTTPException(
            404, f"Section '{section}' or param type '{param_type}' not found"
        )
    return result


# ---------------------------------------------------------------------------
# Traders (TraderPlus NPCs & objects)
# ---------------------------------------------------------------------------

@router.get("/trader-ids")
async def get_trader_ids(me: MapEditorState = Depends(get_map_editor)):
    """All trader IDs with their categories from TraderPlusIDsConfig.json."""
    from app.traderplus.traderplus_ids_json import get_ids_config_path, parse_ids_config

    path = get_ids_config_path(me.config)
    if not path:
        return {"ids": []}
    try:
        cfg = parse_ids_config(path)
        return {"ids": [
            {"id": e.id, "categories": e.categories}
            for e in cfg.entries
        ]}
    except (OSError, ValueError, TypeError, AttributeError):
        # Not JSON, not UTF-8 (a ValueError), or `"IDs": null`: no ids to offer.
        return {"ids": []}


@router.get("/traders")
async def get_traders(me: MapEditorState = Depends(get_map_editor)):
    """All trader NPCs and objects with positions (converted to lat/lng)."""
    if not me.traders_state:
        return {"traders": [], "trader_objects": []}
    return me.traders_state.get_traders_json()


@router.post("/traders")
async def add_trader(request: Request, me: MapEditorState = Depends(get_map_editor)):
    """Add a new trader NPC."""
    body = await request.json()
    try:
        result = await me.require_traders().add_trader(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if result is None:
        raise HTTPException(500, "Failed to add trader")
    return result


@router.put("/traders/{index}")
async def update_trader(
    index: int, request: Request, me: MapEditorState = Depends(get_map_editor)
):
    """Update trader fields by array index (partial)."""
    body = await request.json()
    result = await me.require_traders().update_trader(index, body)
    if result is None:
        raise HTTPException(404, f"Trader at index {index} not found")
    return result


@router.put("/traders/{index}/position/game")
async def update_trader_position_game(
    index: int, request: Request, me: MapEditorState = Depends(get_map_editor)
):
    """Update trader position using game coordinates."""
    body = await request.json()
    ok = await me.require_traders().update_trader_position_game(
        index, *_need(body, "x", "y", "z"), body.get("orientation"),
    )
    if not ok:
        raise HTTPException(404, f"Trader at index {index} not found")
    return {"ok": True}


@router.delete("/traders/{index}")
async def delete_trader(index: int, me: MapEditorState = Depends(get_map_editor)):
    """Delete a trader NPC by array index."""
    ok = await me.require_traders().delete_trader(index)
    if not ok:
        raise HTTPException(404, f"Trader at index {index} not found")
    return {"ok": True}


@router.post("/trader-objects")
async def add_trader_object(
    request: Request, me: MapEditorState = Depends(get_map_editor)
):
    """Add a new trader object."""
    body = await request.json()
    try:
        result = await me.require_traders().add_trader_object(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if result is None:
        raise HTTPException(500, "Failed to add trader object")
    return result


@router.put("/trader-objects/{index}")
async def update_trader_object(
    index: int, request: Request, me: MapEditorState = Depends(get_map_editor)
):
    """Update trader object fields."""
    body = await request.json()
    result = await me.require_traders().update_trader_object(index, body)
    if result is None:
        raise HTTPException(404, f"Trader object at index {index} not found")
    return result


@router.put("/trader-objects/{index}/position/game")
async def update_trader_object_position_game(
    index: int, request: Request, me: MapEditorState = Depends(get_map_editor)
):
    """Update trader object position using game coordinates."""
    body = await request.json()
    ok = await me.require_traders().update_trader_object_position_game(
        index, *_need(body, "x", "y", "z"), body.get("orientation"),
    )
    if not ok:
        raise HTTPException(404, f"Trader object at index {index} not found")
    return {"ok": True}


@router.delete("/trader-objects/{index}")
async def delete_trader_object(
    index: int, me: MapEditorState = Depends(get_map_editor)
):
    """Delete a trader object."""
    ok = await me.require_traders().delete_trader_object(index)
    if not ok:
        raise HTTPException(404, f"Trader object at index {index} not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Safe Zones
# ---------------------------------------------------------------------------

@router.get("/safezones")
async def get_safezones(me: MapEditorState = Depends(get_map_editor)):
    """All safe zones with positions (converted to lat/lng) and radius."""
    if not me.safezone_state:
        return {"safezones": []}
    return me.safezone_state.get_safezones_json()


@router.post("/safezones")
async def add_safezone(request: Request, me: MapEditorState = Depends(get_map_editor)):
    """Add a new safe zone."""
    body = await request.json()
    try:
        result = await me.require_safezones().add_safezone(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if result is None:
        raise HTTPException(500, "Failed to add safe zone")
    return result


@router.put("/safezones/{index}")
async def update_safezone(
    index: int, request: Request, me: MapEditorState = Depends(get_map_editor)
):
    """Update safe zone fields (partial)."""
    body = await request.json()
    try:
        result = await me.require_safezones().update_safezone(index, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if result is None:
        raise HTTPException(404, f"Safe zone at index {index} not found")
    return result


@router.delete("/safezones/{index}")
async def delete_safezone(index: int, me: MapEditorState = Depends(get_map_editor)):
    """Delete a safe zone."""
    ok = await me.require_safezones().delete_safezone(index)
    if not ok:
        raise HTTPException(404, f"Safe zone at index {index} not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Garage NPCs
# ---------------------------------------------------------------------------

@router.get("/garage")
async def get_garage(me: MapEditorState = Depends(get_map_editor)):
    """All garage NPCs with NPC and parking positions."""
    if not me.garage_state:
        return {"garage_npcs": []}
    return me.garage_state.get_garage_json()


@router.post("/garage")
async def add_garage_npc(request: Request, me: MapEditorState = Depends(get_map_editor)):
    """Add a new garage NPC."""
    body = await request.json()
    try:
        result = await me.require_garage().add_garage_npc(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if result is None:
        raise HTTPException(500, "Failed to add garage NPC")
    return result


@router.put("/garage/{index}")
async def update_garage_npc(
    index: int, request: Request, me: MapEditorState = Depends(get_map_editor)
):
    """Update garage NPC fields (partial)."""
    body = await request.json()
    result = await me.require_garage().update_garage_npc(index, body)
    if result is None:
        raise HTTPException(404, f"Garage NPC at index {index} not found")
    return result


@router.delete("/garage/{index}")
async def delete_garage_npc(index: int, me: MapEditorState = Depends(get_map_editor)):
    """Delete a garage NPC."""
    ok = await me.require_garage().delete_garage_npc(index)
    if not ok:
        raise HTTPException(404, f"Garage NPC at index {index} not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Expansion map data (plans/36 D)
#
# Four layers over the JSON Expansion writes on its first boot. A GET answers
# with an empty list when the file isn't there — an unticked layer must not toast
# an error on a server that has never started — while a PUT goes through
# `require_exp_*`, which 404s with what is missing.
# ---------------------------------------------------------------------------

@router.get("/expansion/safezones")
async def get_expansion_safezones(me: MapEditorState = Depends(get_map_editor)):
    """Expansion safe zones: circles, cylinders and polygons."""
    if not me.exp_safezone_state:
        return {"circles": [], "cylinders": [], "polygons": [], "enabled": False}
    return me.exp_safezone_state.get_json()


@router.put("/expansion/safezones/{kind}/{index}")
async def update_expansion_safezone(
    kind: str, index: int, request: Request, me: MapEditorState = Depends(get_map_editor)
):
    """Move or resize one zone. A polygon takes `point` — the vertex index."""
    body = await request.json()
    result = await me.require_exp_safezones().update_zone(kind, index, body)
    if result is None:
        raise HTTPException(404, f"No {kind} safe zone at index {index}")
    return result


@router.get("/expansion/trader-zones")
async def get_expansion_trader_zones(me: MapEditorState = Depends(get_map_editor)):
    """One entry per trader zone file. `Stock` is deliberately not included."""
    if not me.exp_traderzone_state:
        return {"trader_zones": []}
    return me.exp_traderzone_state.get_json()


@router.put("/expansion/trader-zones/{zone_id}")
async def update_expansion_trader_zone(
    zone_id: str, request: Request, me: MapEditorState = Depends(get_map_editor)
):
    """Move/resize a zone or change its two price percentages."""
    body = await request.json()
    result = await me.require_exp_traderzones().update_zone(zone_id, body)
    if result is None:
        raise HTTPException(404, f"No trader zone named {zone_id!r}")
    return result


@router.get("/expansion/spawn-points")
async def get_expansion_spawn_points(me: MapEditorState = Depends(get_map_editor)):
    """Player spawn positions from SpawnSettings.json, grouped by location."""
    if not me.exp_spawn_state:
        return {"spawn_locations": [], "enabled": False}
    return me.exp_spawn_state.get_json()


@router.put("/expansion/spawn-points/{loc_index}/{pos_index}")
async def update_expansion_spawn_point(
    loc_index: int,
    pos_index: int,
    request: Request,
    me: MapEditorState = Depends(get_map_editor),
):
    """Move one spawn position."""
    body = await request.json()
    result = await me.require_exp_spawns().update_position(loc_index, pos_index, body)
    if result is None:
        raise HTTPException(404, f"No spawn position {loc_index}/{pos_index}")
    return result


@router.get("/expansion/missions")
async def get_expansion_missions(me: MapEditorState = Depends(get_map_editor)):
    """Airdrop and contaminated-area positions. Read-only in this cut."""
    if not me.exp_mission_state:
        return {"missions": []}
    return me.exp_mission_state.get_json()


# ---------------------------------------------------------------------------
# Expansion AI — patrol routes and roaming locations
#
# Both files belong to the AI module, so they are absent on a server that does
# not run it: same contract as the four layers above, a GET answers empty and a
# write 404s with the file that is missing.
# ---------------------------------------------------------------------------

_OBJECT_PATROL = (
    "This is an object patrol: its waypoints are metres from each building of its "
    "class, not positions on the map. Edit them on the Raw tab of Expansion AI Patrols."
)


@router.get("/expansion/ai/patrols")
async def get_expansion_ai_patrols(me: MapEditorState = Depends(get_map_editor)):
    """Patrol routes from AIPatrolSettings.json, one entry per patrol."""
    if not me.exp_ai_patrol_state:
        return {"patrols": [], "unplaceable_patrols": 0, "object_patrols": 0}
    return me.exp_ai_patrol_state.get_json()


@router.put("/expansion/ai/patrols/{patrol_index}/waypoints/{wp_index}")
async def update_expansion_ai_waypoint(
    patrol_index: int,
    wp_index: int,
    request: Request,
    me: MapEditorState = Depends(get_map_editor),
):
    """Move one waypoint. The patrol's other 40-odd fields are untouched."""
    body = await request.json()
    try:
        result = await me.require_exp_ai_patrols().update_waypoint(patrol_index, wp_index, body)
    except ObjectPatrolWaypoints:
        raise HTTPException(409, _OBJECT_PATROL)
    if result is None:
        raise HTTPException(404, f"No waypoint {patrol_index}/{wp_index}")
    return result


@router.post("/expansion/ai/patrols/{patrol_index}/waypoints")
async def add_expansion_ai_waypoint(
    patrol_index: int, request: Request, me: MapEditorState = Depends(get_map_editor)
):
    """Append a waypoint to a patrol — the order is the route."""
    body = await request.json()
    try:
        result = await me.require_exp_ai_patrols().add_waypoint(patrol_index, body)
    except ObjectPatrolWaypoints:
        raise HTTPException(409, _OBJECT_PATROL)
    if result is None:
        raise HTTPException(404, f"No patrol {patrol_index}, or no position given")
    return result


@router.delete("/expansion/ai/patrols/{patrol_index}/waypoints/{wp_index}")
async def delete_expansion_ai_waypoint(
    patrol_index: int, wp_index: int, me: MapEditorState = Depends(get_map_editor)
):
    """Remove a waypoint. The last one cannot go — see the state class."""
    try:
        error = await me.require_exp_ai_patrols().delete_waypoint(patrol_index, wp_index)
    except ObjectPatrolWaypoints:
        raise HTTPException(409, _OBJECT_PATROL)
    if error == "last waypoint":
        raise HTTPException(
            409,
            "A patrol needs at least one waypoint — Expansion skips a patrol with none. "
            "Delete the patrol itself if that is what you want.",
        )
    if error:
        raise HTTPException(404, f"No waypoint {patrol_index}/{wp_index}")
    return {"ok": True}


@router.delete("/expansion/ai/patrols/{patrol_index}")
async def delete_expansion_ai_patrol(
    patrol_index: int, me: MapEditorState = Depends(get_map_editor)
):
    """Remove a whole patrol from the file."""
    if not await me.require_exp_ai_patrols().delete_patrol(patrol_index):
        raise HTTPException(404, f"No patrol {patrol_index}")
    return {"ok": True}


@router.get("/expansion/ai/locations")
async def get_expansion_ai_locations(me: MapEditorState = Depends(get_map_editor)):
    """Roaming locations and no-go areas from AILocationSettings.json."""
    if not me.exp_ai_location_state:
        return {"roaming_locations": [], "no_go_areas": [], "skipped_no_go_areas": 0}
    return me.exp_ai_location_state.get_json()


@router.put("/expansion/ai/locations/{index}")
async def update_expansion_ai_location(
    index: int, request: Request, me: MapEditorState = Depends(get_map_editor)
):
    """Move, resize, rename, retype or enable/disable one roaming location."""
    body = await request.json()
    result = await me.require_exp_ai_locations().update_location(index, body)
    if result is None:
        raise HTTPException(404, f"No roaming location {index}")
    return result


@router.post("/expansion/ai/locations/{index}/duplicate")
async def duplicate_expansion_ai_location(
    index: int, request: Request, me: MapEditorState = Depends(get_map_editor)
):
    """Copy a roaming location to a new position — never built from defaults."""
    body = await request.json()
    result = await me.require_exp_ai_locations().duplicate_location(index, body)
    if result is None:
        raise HTTPException(404, f"No roaming location {index}, or no position given")
    return result


@router.delete("/expansion/ai/locations/{index}")
async def delete_expansion_ai_location(
    index: int, me: MapEditorState = Depends(get_map_editor)
):
    """Remove one roaming location."""
    if not await me.require_exp_ai_locations().delete_location(index):
        raise HTTPException(404, f"No roaming location {index}")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Expansion position lists in settings files (TODO H4, 2026 Q3): market
# vehicle spawns, server map markers, base-building zones. Move, resize, copy;
# deleting stays in the Configs editor.
# ---------------------------------------------------------------------------


async def _exp_position_write(
    state: Any, file: str, name: str, index: int, request: Request, *, copy: bool
) -> dict:
    if state is None:
        raise HTTPException(404, f"Expansion has not written {file} yet")
    body = await request.json()
    try:
        if copy:
            result = await state.duplicate(name, index, body)
        else:
            result = await state.update(name, index, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if result is None:
        raise HTTPException(404, f"No entry {name} {index} in {file}")
    return result


@router.get("/expansion/market-spawns")
async def get_expansion_market_spawns(me: MapEditorState = Depends(get_map_editor)):
    """Where bought vehicles appear, from MarketSettings.json."""
    if not me.exp_market_spawn_state:
        return {"land": [], "air": [], "water": [], "train": [], "max_distance": None,
                "max_large_distance": None, "enabled": False}
    return me.exp_market_spawn_state.get_json()


@router.put("/expansion/market-spawns/{kind}/{index}")
async def update_expansion_market_spawn(
    kind: str, index: int, request: Request, me: MapEditorState = Depends(get_map_editor)
):
    """Move (lat/lng), raise or lower (game_y) or turn (orientation) one position."""
    return await _exp_position_write(
        me.exp_market_spawn_state, "MarketSettings.json", kind, index, request, copy=False
    )


@router.post("/expansion/market-spawns/{kind}/{index}/duplicate")
async def duplicate_expansion_market_spawn(
    kind: str, index: int, request: Request, me: MapEditorState = Depends(get_map_editor)
):
    return await _exp_position_write(
        me.exp_market_spawn_state, "MarketSettings.json", kind, index, request, copy=True
    )


@router.get("/expansion/map-markers")
async def get_expansion_map_markers(me: MapEditorState = Depends(get_map_editor)):
    """The server markers every player sees, from MapSettings.json."""
    if not me.exp_map_marker_state:
        return {"markers": [], "enabled": False}
    return me.exp_map_marker_state.get_json()


@router.put("/expansion/map-markers/{index}")
async def update_expansion_map_marker(
    index: int, request: Request, me: MapEditorState = Depends(get_map_editor)
):
    """Move one marker, or change its text, icon, colour or height."""
    return await _exp_position_write(
        me.exp_map_marker_state, "MapSettings.json", "markers", index, request, copy=False
    )


@router.post("/expansion/map-markers/{index}/duplicate")
async def duplicate_expansion_map_marker(
    index: int, request: Request, me: MapEditorState = Depends(get_map_editor)
):
    return await _exp_position_write(
        me.exp_map_marker_state, "MapSettings.json", "markers", index, request, copy=True
    )


@router.get("/expansion/build-zones")
async def get_expansion_build_zones(me: MapEditorState = Depends(get_map_editor)):
    """Base-building zones from BaseBuildingSettings.json."""
    if not me.exp_build_zone_state:
        return {"zones": [], "no_build_zones": None}
    return me.exp_build_zone_state.get_json()


@router.put("/expansion/build-zones/{index}")
async def update_expansion_build_zone(
    index: int, request: Request, me: MapEditorState = Depends(get_map_editor)
):
    """Move, resize, rename or raise one zone."""
    return await _exp_position_write(
        me.exp_build_zone_state, "BaseBuildingSettings.json", "zones", index, request, copy=False
    )


@router.post("/expansion/build-zones/{index}/duplicate")
async def duplicate_expansion_build_zone(
    index: int, request: Request, me: MapEditorState = Depends(get_map_editor)
):
    return await _exp_position_write(
        me.exp_build_zone_state, "BaseBuildingSettings.json", "zones", index, request, copy=True
    )


# ---------------------------------------------------------------------------
# Object Spawner (the Items tab's lasting objects): app/spawn_editor/object_spawner.py
# ---------------------------------------------------------------------------


def _object_spawner_error(e) -> HTTPException:
    """The HTTP answer for an `ObjectSpawnerError`."""
    return HTTPException(e.status, e.message)


@router.get("/object-spawner")
async def get_object_spawner(me: MapEditorState = Depends(get_map_editor)):
    """Zedstead's Object Spawner objects, and whether DayZ reads the file."""
    from starlette.concurrency import run_in_threadpool
    from app.spawn_editor.object_spawner import read_objects

    spawn_state = me.require_spawn()
    return await run_in_threadpool(
        read_objects, spawn_state.mission_dir, spawn_state.terrain, spawn_state.config.server_cfg_path
    )


async def _ground_y(spawn_state, x: float, z: float) -> float | None:
    """The ground's height at game *x*, *z*, from the Terrain switch's heights; None without them."""
    from starlette.concurrency import run_in_threadpool
    from app.maps.terrain_map import height_at, terrain_heights

    gz = await run_in_threadpool(lambda: terrain_heights(*_world_search(spawn_state, spawn_state.terrain)))
    return await run_in_threadpool(height_at, gz, x, z) if gz else None


@router.get("/object-spawner/ground")
async def object_spawner_ground(
    x: float | None = Query(None),
    z: float | None = Query(None),
    lat: float | None = Query(None),
    lng: float | None = Query(None),
    me: MapEditorState = Depends(get_map_editor),
):
    """The ground under a point, for the Edit form.

    With game *x*, *z* (Y to ground): ``{y}``, or 404 when Zedstead has no terrain
    height there. With *lat*, *lng* (a marker dragged on the map): ``{x, z, y}``,
    the point in game metres as the server converts it, and ``y`` null without
    a height, so the form never projects a position itself.
    """
    spawn_state = me.require_spawn()
    if lat is not None and lng is not None:
        gx, gz = latlng_to_game(lat, lng, spawn_state.terrain)
        return {"x": round(gx, 3), "z": round(gz, 3), "y": await _ground_y(spawn_state, gx, gz)}
    if x is None or z is None:
        raise HTTPException(400, "Send x and z, or lat and lng.")
    y = await _ground_y(spawn_state, x, z)
    if y is None:
        raise HTTPException(404, "Zedstead has no terrain height for this point. Type Y yourself.")
    return {"y": y}


@router.post("/object-spawner")
async def add_object_spawner(request: Request, me: MapEditorState = Depends(get_map_editor)):
    """Place one object for good: ``{lat, lng, name}``, optional ``y`` and ``yaw``.

    Without ``y`` the ground's height at the point is used, from the same
    terrain heights the Terrain switch draws; a map without them asks for Y.
    """
    from starlette.concurrency import run_in_threadpool
    from app.spawn_editor.object_spawner import ObjectSpawnerError, add_object, read_objects

    spawn_state = me.require_spawn()
    body = await request.json()
    lat, lng = _need(body, "lat", "lng")
    x, z = latlng_to_game(lat, lng, spawn_state.terrain)
    y = body.get("y")
    if y is None:
        y = await _ground_y(spawn_state, x, z)
        if y is None:
            raise HTTPException(
                400,
                "Zedstead has no terrain heights for this map, so it cannot tell the ground's height here. "
                "Type the height (Y) in the bar.",
            )
    try:
        row = await add_object(
            spawn_state.mission_dir, spawn_state.terrain, body.get("name"), x, float(y), z,
            float(body.get("yaw") or 0),
        )
    except ObjectSpawnerError as e:
        raise _object_spawner_error(e)
    state = await run_in_threadpool(
        read_objects, spawn_state.mission_dir, spawn_state.terrain, spawn_state.config.server_cfg_path
    )
    return {**row, "listed": state["listed"], "gameplay_on": state["gameplay_on"]}


@router.put("/object-spawner/gameplay-file")
async def set_gameplay_file(request: Request, me: MapEditorState = Depends(get_map_editor)):
    """Turn serverDZ.cfg's ``enableCfgGameplayFile`` on or off: ``{on: bool}``.

    It goes through the Server form's own save (`save_serverdz`), so it takes
    the same snapshot, keeps the file's bool style and refuses a missing file
    or a container-locked key. DayZ reads the value at the next start.
    """
    from starlette.concurrency import run_in_threadpool
    from app.spawn_editor.object_spawner import gameplay_file_on
    from app.web.editors.server_forms import save_serverdz

    spawn_state = me.require_spawn()
    body = await request.json()
    on = body.get("on") if isinstance(body, dict) else None
    if not isinstance(on, bool):
        raise HTTPException(400, "Send on: true or false.")
    try:
        await run_in_threadpool(save_serverdz, spawn_state.config, {"enableCfgGameplayFile": on})
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"gameplay_on": gameplay_file_on(spawn_state.config.server_cfg_path)}


@router.delete("/object-spawner/{index}")
async def remove_object_spawner(
    index: int,
    expect: str | None = Query(None),
    disabled: bool = Query(False),
    me: MapEditorState = Depends(get_map_editor),
):
    """Remove one object; *expect* is the name the editor showed (409 when it changed).

    *disabled* names the disabled objects' file instead of the live one.
    """
    from app.spawn_editor.object_spawner import ObjectSpawnerError, remove_object

    spawn_state = me.require_spawn()
    try:
        await remove_object(spawn_state.mission_dir, index, expect, disabled)
    except ObjectSpawnerError as e:
        raise _object_spawner_error(e)
    return {"ok": True}


@router.put("/object-spawner/{index}")
async def edit_object_spawner(
    index: int,
    request: Request,
    expect: str | None = Query(None),
    disabled: bool = Query(False),
    me: MapEditorState = Depends(get_map_editor),
):
    """Change one object's position and rotation: ``{x, y, z, yaw, pitch, roll}``, in game metres and degrees.

    The admin types the game's own numbers, so nothing is projected. *disabled*
    names the disabled objects' file. Answers the object's new row.
    """
    from app.spawn_editor.object_spawner import ObjectSpawnerError, edit_object

    spawn_state = me.require_spawn()
    body = await request.json()
    x, y, z, yaw, pitch, roll = _need(body, "x", "y", "z", "yaw", "pitch", "roll")
    try:
        return await edit_object(
            spawn_state.mission_dir, spawn_state.terrain, index, expect, disabled, (x, y, z), (yaw, pitch, roll)
        )
    except ObjectSpawnerError as e:
        raise _object_spawner_error(e)


@router.post("/object-spawner/{index}/toggle")
async def toggle_object_spawner(
    index: int,
    expect: str | None = Query(None),
    disabled: bool = Query(False),
    me: MapEditorState = Depends(get_map_editor),
):
    """Disable a live object, or enable a disabled one (*disabled* names its file now).

    Answers the object's new row, with its new index and ``disabled``.
    """
    from app.spawn_editor.object_spawner import ObjectSpawnerError, move_object

    spawn_state = me.require_spawn()
    try:
        return await move_object(spawn_state.mission_dir, spawn_state.terrain, index, expect, disabled)
    except ObjectSpawnerError as e:
        raise _object_spawner_error(e)


@router.get("/underground-triggers")
async def get_underground_triggers(me: MapEditorState = Depends(get_map_editor)):
    """Trigger boxes and their breadcrumbs from cfgundergroundtriggers.json.

    An empty list for a mission with no file, rather than a 404: Chernarus
    ships an empty stub, so "none here" is the normal answer on the map most
    people run and not something to show an error for.
    """
    if not me.underground_state:
        return {"triggers": [], "map_size": 0}
    return me.underground_state.get_json()


@router.put("/underground-triggers/{index}")
async def update_underground_trigger(
    index: int, request: Request, me: MapEditorState = Depends(get_map_editor)
):
    """Move, resize, rotate or re-light one trigger."""
    body = await request.json()
    try:
        result = await me.require_underground().update_trigger(index, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if result is None:
        raise HTTPException(404, f"No underground trigger {index}")
    return result


@router.put("/underground-triggers/{index}/breadcrumbs/{crumb_index}")
async def update_underground_breadcrumb(
    index: int,
    crumb_index: int,
    request: Request,
    me: MapEditorState = Depends(get_map_editor),
):
    """Move one breadcrumb, or change its weight, radius or raycast flag."""
    body = await request.json()
    try:
        result = await me.require_underground().update_breadcrumb(index, crumb_index, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if result is None:
        raise HTTPException(404, f"No breadcrumb {crumb_index} on trigger {index}")
    return result


@router.get("/trader-systems")
async def get_trader_systems(me: MapEditorState = Depends(get_map_editor)):
    """TraderPlus and Expansion Market side by side, and where they overlap.

    Answers for both systems at once rather than 404ing when one is absent —
    "only one trader system is configured" is the answer in the common case,
    and the panel that asks needs to know that to stay quiet.
    """
    traders = (
        me.traders_state.get_traders_json()["traders"] if me.traders_state else []
    )
    safezones = (
        me.safezone_state.get_safezones_json()["safezones"] if me.safezone_state else []
    )
    exp_zones = (
        me.exp_traderzone_state.get_json()["trader_zones"]
        if me.exp_traderzone_state
        else []
    )
    return trader_systems.analyze(traders, safezones, exp_zones)


# ---------------------------------------------------------------------------
# Hazard zones (plans/48 C): contaminated areas, RadZone and ToxicZone
# ---------------------------------------------------------------------------


async def _hazard_body(request: Request) -> dict:
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "Expected a JSON object")
    return body


@router.get("/hazards")
async def get_hazards(me: MapEditorState = Depends(get_map_editor)):
    """Contaminated areas from cfgEffectArea.json and, with each mod installed,
    RadZone's and ToxicZone's zones and crates. Empty rather than a 404 when
    none exists."""
    if not me.hazard_state:
        return {"contaminated": [], "radzone": None, "toxiczone": None, "map_size": 0}
    return me.hazard_state.get_json()


@router.put("/hazards/contaminated/{index}")
async def update_contaminated_area(
    index: int, request: Request, me: MapEditorState = Depends(get_map_editor)
):
    """Move (lat/lng) or resize (radius, metres) one contaminated area."""
    body = await _hazard_body(request)
    try:
        result = await me.require_hazards().update_contaminated(index, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if result is None:
        raise HTTPException(404, f"No contaminated area {index}")
    return result


@router.put("/hazards/radzone/{index}")
async def update_radzone_zone(
    index: int, request: Request, me: MapEditorState = Depends(get_map_editor)
):
    """Move (lat/lng) or resize (radius, metres) one RadZone zone."""
    body = await _hazard_body(request)
    try:
        result = await me.require_hazards().update_radzone(index, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if result is None:
        raise HTTPException(404, f"No RadZone zone {index}")
    return result


@router.put("/hazards/toxiczone/{index}")
async def update_toxiczone_zone(
    index: int, request: Request, me: MapEditorState = Depends(get_map_editor)
):
    """Move (lat/lng) or resize (radius, metres) one ToxicZone static zone."""
    body = await _hazard_body(request)
    try:
        result = await me.require_hazards().update_toxic_static(index, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if result is None:
        raise HTTPException(404, f"No ToxicZone static zone {index}")
    return result


@router.put("/hazards/toxiczone-moving/{index}")
async def update_toxiczone_moving(
    index: int, request: Request, me: MapEditorState = Depends(get_map_editor)
):
    """Move one end (point + lat/lng) or resize (radius) one ToxicZone moving
    zone. Moving an end recomputes the zone's path in the same write."""
    body = await _hazard_body(request)
    try:
        result = await me.require_hazards().update_toxic_moving(index, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if result is None:
        raise HTTPException(404, f"No ToxicZone moving zone {index}")
    return result



# ---------------------------------------------------------------------------
# Dynamic AI (plans/47 D): points, locations and audio locations
# ---------------------------------------------------------------------------


def _dynamic_ai_section(section: str) -> str:
    from app.spawn_editor.dynamic_ai_state import SECTIONS

    if section not in SECTIONS:
        raise HTTPException(404, f"No Dynamic AI list {section!r}")
    return section


@router.get("/dynamic-ai")
async def get_dynamic_ai(me: MapEditorState = Depends(get_map_editor)):
    """Dynamic AI's points, locations and audio locations. Empty lists rather
    than a 404 when the mod is not installed or has not booted yet."""
    if not me.dynamic_ai_state or not me.dynamic_ai_state.has_data:
        return {"points": [], "locations": [], "audio": [], "modes": {}, "map_size": 0}
    return me.dynamic_ai_state.get_json()


@router.put("/dynamic-ai/{section}/{index}")
async def update_dynamic_ai_entry(
    section: str, index: int, request: Request, me: MapEditorState = Depends(get_map_editor)
):
    """Move (lat/lng) or resize (radius, metres) one entry. On a location or
    audio entry, ``spawn`` moves one spawn position instead, and
    ``carry_spawns`` moves the spawn positions along with the trigger."""
    section = _dynamic_ai_section(section)
    body = await _hazard_body(request)
    try:
        result = await me.require_dynamic_ai().update(section, index, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if result is None:
        raise HTTPException(404, f"No Dynamic AI {section} entry {index}")
    return result


@router.post("/dynamic-ai/{section}/{index}/duplicate")
async def duplicate_dynamic_ai_entry(
    section: str, index: int, request: Request, me: MapEditorState = Depends(get_map_editor)
):
    """Copy one entry to lat/lng (D3: new entries only by duplicating one)."""
    section = _dynamic_ai_section(section)
    body = await _hazard_body(request)
    try:
        result = await me.require_dynamic_ai().duplicate(section, index, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if result is None:
        raise HTTPException(404, f"No Dynamic AI {section} entry {index}")
    return result
