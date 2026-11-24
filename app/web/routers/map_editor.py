"""The Visual Map Editor's API, served under ``/api/map-editor``.

Includes the shared ``APIRouter`` from ``app.spawn_editor.routes`` and adds
the routes that need this app: status, reload and the admin's own map image.
Each shared route resolves its state through ``routes.get_map_editor``, which
reads this app's ``WebState.map_editor``.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.maps import map_data
from app.spawn_editor import routes as editor_routes
from app.web.deps import get_state, require_session
from app.web.map_editor_svc import ensure_map_editor_ready, reload_map_editor
from app.web.state import WebState
from app.web.uploads import read_bounded

router = APIRouter(
    prefix="/api/map-editor",
    tags=["map-editor"],
    dependencies=[Depends(require_session)],
)


async def _ready(request: Request, state: WebState = Depends(get_state)) -> None:
    """Lazily build the editor state before any editor route runs.

    A write while no page watches re-reads the files first. The file watcher
    runs only while a page subscribes, so the write saved the copy loaded
    earlier and lost a change made on disk since (browser test 2026-11-17, F9).
    """
    me = await ensure_map_editor_ready(state)
    watching = me.watcher_task is not None and not me.watcher_task.done()
    if request.method not in ("GET", "HEAD") and not watching:
        await me.reload_all(new_session=False)


router.include_router(editor_routes.router, dependencies=[Depends(_ready)])


@router.get("/status")
async def map_editor_status(state: WebState = Depends(get_state)) -> dict:
    me = state.map_editor
    return {
        "loaded": me is not None,
        "watching": me is not None and me.watcher_task is not None,
        "terrain": me.terrain if me is not None else "",
        "traderplus": me is not None and me.traders_state is not None,
    }


@router.post("/reload")
async def map_editor_reload(state: WebState = Depends(get_state)) -> dict:
    await ensure_map_editor_ready(state)
    await reload_map_editor(state)
    return {"loaded": True}


# ---------------------------------------------------------------------------
# The admin's own map image (plans/69 Step 4)
#
# Here rather than on the shared editor router: that router checks every
# write's body as JSON numbers, and this body is an image. GET /map-image
# serves it. Admin-only through
# the permissions default, like every write not in MODERATOR_WRITES.
# ---------------------------------------------------------------------------


def _own_image_terrain(state: WebState, terrain: str) -> str:
    """*terrain*, when it is a map this editor knows; never a path.

    `editor_terrains`, the set GET /maps offers: the registry's maps, plus the
    running mission's terrain for a community map zsm does not list.
    """
    if terrain not in editor_routes.editor_terrains(state.map_editor):
        raise HTTPException(404, f"Unknown map: {terrain!r}")
    return terrain


@router.post("/map-image", dependencies=[Depends(_ready)])
async def upload_own_map_image(
    request: Request,
    terrain: str = Query(...),
    state: WebState = Depends(get_state),
) -> dict:
    """Store the request body as the admin's own image for *terrain*.

    The body is the image itself (see `app.web.uploads`). Its type is read
    from its first bytes; the name it had on the admin's disk never arrives.
    """
    t = _own_image_terrain(state, terrain)
    data = await read_bounded(request, map_data.MAX_OWN_IMAGE_BYTES)
    try:
        path = await asyncio.to_thread(map_data.save_own_image, t, data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"terrain": t, "size": len(data), "type": path.suffix[1:]}


@router.post("/map-image/thumbnail", dependencies=[Depends(_ready)])
async def upload_own_map_thumbnail(
    request: Request,
    terrain: str = Query(...),
    state: WebState = Depends(get_state),
) -> dict:
    """Store the request body as the preview of the admin's own image.

    The browser sends it right after POST /map-image, made from the same file.
    GET /api/maps/thumbnail serves it in place of the full image.
    """
    t = _own_image_terrain(state, terrain)
    data = await read_bounded(request, map_data.MAX_OWN_THUMBNAIL_BYTES)
    try:
        await asyncio.to_thread(map_data.save_own_thumbnail, t, data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"terrain": t, "size": len(data)}


@router.delete("/map-image", dependencies=[Depends(_ready)])
async def remove_own_map_image(terrain: str = Query(...), state: WebState = Depends(get_state)) -> dict:
    """Delete the admin's own image for *terrain*; the map goes back to what it had."""
    t = _own_image_terrain(state, terrain)
    if not await asyncio.to_thread(map_data.remove_own_image, t):
        raise HTTPException(404, "There is no image of your own for this map")
    return {"terrain": t, "removed": True}


# GET /help lives on the shared editor router (app/spawn_editor/routes.py) and
# is reachable here as /api/map-editor/help.
