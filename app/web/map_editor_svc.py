"""Map-editor lifecycle for the web app: lazy state init + watcher gating.

Two independent lifetimes, deliberately split:

- **State** (``WebState.map_editor``) is initialized lazily on first use and
  then **sticky** — parsing every mission XML plus the TraderPlus JSON is the
  expensive part, and nothing is gained by throwing it away. The one exception
  is a map switch: every state object captures its mission dir and terrain at
  construction, so ``ensure_map_editor_ready`` rebuilds when serverDZ.cfg names
  a different mission than the state was built for — the web's own switch
  reloads explicitly, a CLI switch is only caught this way.
- **The file watcher** only runs while someone is actually watching the editor,
  i.e. while the ``mapeditor`` WS channel has subscribers. Because the watcher
  is what keeps in-memory state honest, regaining the first subscriber
  **reloads everything first** — the mission files are editable from elsewhere
  in this same app (``/configs/*`` writes cfgeventspawns.xml), so resuming a
  stale in-memory copy would silently show the wrong data.
"""

from __future__ import annotations

import asyncio
import logging

from starlette.concurrency import run_in_threadpool

from app.modmgr.mods import get_current_mission
from app.spawn_editor.state import MAPEDITOR_CHANNEL, Broadcaster
from app.spawn_editor.state_container import MapEditorState, build_map_editor_state, present_layers
from app.web.state import WebState

log = logging.getLogger(__name__)


def _built_for_another_mission(state: WebState, me: MapEditorState) -> bool:
    """True when serverDZ.cfg now names a different mission than *me* was built for.

    The web's own map switch rebuilds the state itself; this catches the ones
    it never hears about — ``zsm maps switch`` from a shell, a hand-edited
    cfg — which otherwise left the editor drawing the old map until a restart.
    A cfg that names no mission is not a switch.
    """
    mission = get_current_mission(state.config.server_cfg_path)
    return bool(mission) and mission != me.mission


def _layers_changed(state: WebState, me: MapEditorState) -> bool:
    """True when a mod layer's files came or went since *me* was built: a mod
    install, an Expansion template or a first boot that writes mod files. The
    layer toggles gate on the state existing, so they stayed off until a restart."""
    mission_dir = me.spawn_state.mission_dir if me.spawn_state else None
    return present_layers(state.config, mission_dir) != me.layers


def _stale(state: WebState, me: MapEditorState) -> bool:
    """True when *me* must be rebuilt before it answers."""
    return _built_for_another_mission(state, me) or _layers_changed(state, me)


async def ensure_map_editor_ready(state: WebState) -> MapEditorState:
    """Initialize the editor state once, and rebuild it if the mission or the
    set of mod layers changed."""
    me = state.map_editor
    if me is not None and not await run_in_threadpool(_stale, state, me):
        return me
    rebuilt = False
    async with state.map_editor_lock:
        if state.map_editor is None:
            state.map_editor = await run_in_threadpool(
                build_map_editor_state, state.config, Broadcaster(state.hub)
            )
        elif await run_in_threadpool(_stale, state, state.map_editor):
            log.info("Mission or mod layers changed; rebuilding the map editor")
            await _rebuild_locked(state)
            rebuilt = True
        me = state.map_editor
    if rebuilt:
        await _broadcast_reloaded(state)
    return me


async def reload_map_editor(state: WebState) -> None:
    """Re-read mission files (map switch, external edits) and resync clients.

    Rebuilds from scratch rather than calling ``reload_all``: a map switch
    changes the terrain, which the state objects capture at construction.
    """
    async with state.map_editor_lock:
        if state.map_editor is None:
            return
        await _rebuild_locked(state)
    await _broadcast_reloaded(state)


async def _rebuild_locked(state: WebState) -> None:
    """Replace the editor state, carrying the watcher over. Hold the lock."""
    watching = state.map_editor.watcher_task is not None
    await _stop_watcher(state.map_editor)
    state.map_editor = await run_in_threadpool(
        build_map_editor_state, state.config, Broadcaster(state.hub)
    )
    if watching:
        _start_watcher(state.map_editor)


async def _broadcast_reloaded(state: WebState) -> None:
    await state.hub.broadcast(
        {"type": "mapeditor_reloaded"}, channel=MAPEDITOR_CHANNEL
    )


async def shutdown_map_editor(state: WebState) -> None:
    """Stop the watcher on web-app shutdown."""
    async with state.map_editor_lock:
        if state.map_editor is not None:
            await _stop_watcher(state.map_editor)
        state.map_editor = None


def register_channel_hooks(state: WebState) -> None:
    """Gate the mission-file watcher on ``mapeditor`` channel subscribers."""

    async def on_first() -> None:
        me = await ensure_map_editor_ready(state)
        # The watcher was off while nobody watched; anything edited in the
        # meantime is only visible after a reload.
        await me.reload_all()
        _start_watcher(me)

    async def on_last() -> None:
        if state.map_editor is not None:
            await _stop_watcher(state.map_editor)

    state.hub.on_first_subscriber(MAPEDITOR_CHANNEL, on_first)
    state.hub.on_last_unsubscriber(MAPEDITOR_CHANNEL, on_last)


# ---------------------------------------------------------------------------
# Watcher task
# ---------------------------------------------------------------------------

def _start_watcher(me: MapEditorState) -> None:
    if me.watcher_task is not None and not me.watcher_task.done():
        return
    from app.spawn_editor.watcher import watch_mission_files

    me.watcher_task = asyncio.create_task(
        watch_mission_files(
            me.spawn_state,
            me.events_state,
            me.playerspawn_state,
            # Keyword from here on: the optional states are positional in the
            # signature, so adding one in the middle silently shifts the rest.
            underground_state=me.underground_state,
            traders_state=me.traders_state,
            safezone_state=me.safezone_state,
            garage_state=me.garage_state,
            expansion_states={
                "safezone": me.exp_safezone_state,
                "traderzone": me.exp_traderzone_state,
                "spawn": me.exp_spawn_state,
                "mission": me.exp_mission_state,
                "ai_patrol": me.exp_ai_patrol_state,
                "ai_location": me.exp_ai_location_state,
                "market": me.exp_market_spawn_state,
                "map_marker": me.exp_map_marker_state,
                "build_zone": me.exp_build_zone_state,
            },
            hazard_state=me.hazard_state,
            dynamic_ai_state=me.dynamic_ai_state,
        )
    )


async def _stop_watcher(me: MapEditorState) -> None:
    task = me.watcher_task
    me.watcher_task = None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
