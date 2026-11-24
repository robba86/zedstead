"""``MapEditorState`` — every editor state object as one addressable unit.

The editor's state used to live in module-level globals on
``app.spawn_editor.server`` (``spawn_state``, ``events_state``, …), which meant
"last app to initialize wins" whenever more than one app existed in a process.
Bundling them here lets ``app.web`` hang an instance off ``WebState``, and the
shared ``APIRouter`` resolves it per request (see ``routes.get_map_editor``).

The three TraderPlus states stay ``None`` when the mod's configs are absent —
that is normal, not an error, and ``/map-info``'s ``traderplus`` flag is what
the frontend gates its UI on. The four Expansion states work the same way
(``expansion``), except that their files appear only after the mod's first
boot rather than with the mod itself.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.modmgr.mods import get_current_mission
from app.shared.config import DayzConfig
from app.spawn_editor.dynamic_ai_state import DynamicAiState
from app.spawn_editor.events_state import EventsState
from app.spawn_editor.expansion_position_state import (
    ExpansionBuildZoneState,
    ExpansionMapMarkerState,
    ExpansionMarketSpawnState,
)
from app.spawn_editor.expansion_state import (
    ExpansionAILocationState,
    ExpansionAIPatrolState,
    ExpansionMissionState,
    ExpansionSafeZoneState,
    ExpansionSpawnLocationState,
    ExpansionTraderZoneState,
)
from app.spawn_editor.garage_state import GarageState
from app.spawn_editor.hazard_state import HazardState
from app.spawn_editor.playerspawn_state import PlayerSpawnState
from app.spawn_editor.safezone_state import SafeZoneState
from app.spawn_editor.state import SpawnEditorState
from app.spawn_editor.traders_state import TradersState
from app.spawn_editor.underground_state import UndergroundTriggerState
from app.spawn_editor import versioning

log = logging.getLogger(__name__)


@dataclass
class MapEditorState:
    """Every piece of editor state for one config, plus its watcher task."""

    config: DayzConfig
    broadcaster: Any
    spawn_state: SpawnEditorState | None = None
    events_state: EventsState | None = None
    playerspawn_state: PlayerSpawnState | None = None
    traders_state: TradersState | None = None
    safezone_state: SafeZoneState | None = None
    garage_state: GarageState | None = None
    # Expansion's generated map data. None until the mod has written the
    # file — the `/map-info` `expansion` flag is what the layer toggles gate
    # on, the same way `traderplus` works.
    exp_safezone_state: ExpansionSafeZoneState | None = None
    exp_traderzone_state: ExpansionTraderZoneState | None = None
    exp_spawn_state: ExpansionSpawnLocationState | None = None
    exp_mission_state: ExpansionMissionState | None = None
    # The AI module's two files, written only when that module is installed.
    exp_ai_patrol_state: ExpansionAIPatrolState | None = None
    exp_ai_location_state: ExpansionAILocationState | None = None
    # Position lists inside three settings files (TODO H4, 2026 Q3).
    exp_market_spawn_state: ExpansionMarketSpawnState | None = None
    exp_map_marker_state: ExpansionMapMarkerState | None = None
    exp_build_zone_state: ExpansionBuildZoneState | None = None
    # Vanilla, not a mod: cfgundergroundtriggers.json. Chernarus ships an empty
    # stub and Sakhal thirty triggers, so None here means "no file", not "no
    # mod" — the layer toggle gates on the list being empty either way.
    underground_state: UndergroundTriggerState | None = None
    # The "Hazard zones" family (plans/48 C): the mission's cfgEffectArea.json
    # and, while the mod is installed, RadZone's zones. Built for every mission,
    # even one with neither: this state is kept for the process's lifetime, and
    # RadZone's files appear only after its first boot. Each reload re-checks.
    hazard_state: HazardState | None = None
    # Dynamic AI's points, locations and audio locations (plans/47 D). Built for
    # every mission for the same reason as hazard_state: the mod writes its
    # settings on its first boot, and a reload re-checks.
    dynamic_ai_state: DynamicAiState | None = None
    # The serverDZ.cfg mission this state was built for. Every state object
    # captures its mission dir and terrain at construction, so a switch made
    # outside this process (the CLI, a hand edit) is detected by comparing
    # this against the cfg — see map_editor_svc.ensure_map_editor_ready.
    mission: str = ""
    # The optional layers whose files existed at the build (`present_layers`).
    # A mod install, a template or a first boot adds files later, so
    # map_editor_svc.ensure_map_editor_ready rebuilds when this differs.
    layers: frozenset[str] = frozenset()
    watcher_task: asyncio.Task | None = field(default=None, repr=False)

    # -- accessors ------------------------------------------------------
    #
    # The route bodies used to `assert spawn_state is not None`, which turns
    # an un-initialized editor into a 500 AssertionError. These raise 503 so
    # the client can tell "not ready" from "genuinely broken".

    def require_spawn(self) -> SpawnEditorState:
        if self.spawn_state is None:
            raise HTTPException(503, "Map editor not initialized")
        return self.spawn_state

    def require_events(self) -> EventsState:
        if self.events_state is None:
            raise HTTPException(503, "Map editor not initialized")
        return self.events_state

    def require_player_spawns(self) -> PlayerSpawnState:
        if self.playerspawn_state is None:
            raise HTTPException(404, "Player spawn points not loaded")
        return self.playerspawn_state

    def require_traders(self) -> TradersState:
        if self.traders_state is None:
            raise HTTPException(404, "TraderPlus not configured")
        return self.traders_state

    def require_safezones(self) -> SafeZoneState:
        if self.safezone_state is None:
            raise HTTPException(404, "TraderPlus SafeZone not configured")
        return self.safezone_state

    def require_garage(self) -> GarageState:
        if self.garage_state is None:
            raise HTTPException(404, "TraderPlus Garage not configured")
        return self.garage_state

    # Expansion writes these on its first boot, so "absent" is the normal state
    # of a server that has not started yet — 404 with the reason, not a 503.

    def require_exp_safezones(self) -> ExpansionSafeZoneState:
        if self.exp_safezone_state is None:
            raise HTTPException(404, "Expansion has not written SafeZoneSettings.json yet")
        return self.exp_safezone_state

    def require_exp_traderzones(self) -> ExpansionTraderZoneState:
        if self.exp_traderzone_state is None:
            raise HTTPException(404, "Expansion has not written any trader zones yet")
        return self.exp_traderzone_state

    def require_exp_spawns(self) -> ExpansionSpawnLocationState:
        if self.exp_spawn_state is None:
            raise HTTPException(404, "Expansion has not written SpawnSettings.json yet")
        return self.exp_spawn_state

    def require_exp_missions(self) -> ExpansionMissionState:
        if self.exp_mission_state is None:
            raise HTTPException(404, "Expansion has not written any missions yet")
        return self.exp_mission_state

    def require_exp_ai_patrols(self) -> ExpansionAIPatrolState:
        if self.exp_ai_patrol_state is None:
            raise HTTPException(
                404, "Expansion has not written AIPatrolSettings.json yet (AI module)"
            )
        return self.exp_ai_patrol_state

    def require_underground(self) -> UndergroundTriggerState:
        if self.underground_state is None:
            raise HTTPException(404, "This mission has no cfgundergroundtriggers.json")
        return self.underground_state

    def require_hazards(self) -> HazardState:
        if self.hazard_state is None:
            raise HTTPException(404, "No mission is loaded")
        return self.hazard_state

    def require_dynamic_ai(self) -> DynamicAiState:
        if self.dynamic_ai_state is None or not self.dynamic_ai_state.has_data:
            raise HTTPException(
                404, "Dynamic AI is not installed, or has not written SpatialSettings.json yet"
            )
        return self.dynamic_ai_state

    def require_exp_ai_locations(self) -> ExpansionAILocationState:
        if self.exp_ai_location_state is None:
            raise HTTPException(
                404, "Expansion has not written AILocationSettings.json yet (AI module)"
            )
        return self.exp_ai_location_state

    @property
    def has_expansion(self) -> bool:
        """True when any Expansion map data is loaded — `/map-info`'s flag."""
        return any(
            state is not None
            for state in (
                self.exp_safezone_state,
                self.exp_traderzone_state,
                self.exp_spawn_state,
                self.exp_mission_state,
                self.exp_ai_patrol_state,
                self.exp_ai_location_state,
                self.exp_market_spawn_state,
                self.exp_map_marker_state,
                self.exp_build_zone_state,
            )
        )

    @property
    def terrain(self) -> str:
        """Active terrain, falling back to Chernarus for collision scans that
        run before (or without) a loaded mission."""
        if self.spawn_state is not None and self.spawn_state.terrain:
            return self.spawn_state.terrain
        return "chernarusplus"

    # -- lifecycle ------------------------------------------------------

    async def reload_all(self, *, new_session: bool = True) -> None:
        """Re-read every backing file and broadcast the changes.

        Called when the editor regains its first WebSocket subscriber: the
        file watcher only runs while someone is watching, so anything edited
        through another surface (``/configs/*`` writes cfgeventspawns.xml too)
        would otherwise be invisible until the next mutation.

        Also where an editor session starts: someone opened the editor, so the
        next write of each file takes its one version snapshot. A write with
        no page open passes ``new_session=False`` (`map_editor._ready`, F9).
        """
        if new_session:
            versioning.new_session()
        for state in (
            self.spawn_state,
            self.events_state,
            self.playerspawn_state,
            self.traders_state,
            self.safezone_state,
            self.garage_state,
            self.exp_safezone_state,
            self.exp_traderzone_state,
            self.exp_spawn_state,
            self.exp_mission_state,
            self.exp_ai_patrol_state,
            self.exp_ai_location_state,
            self.exp_market_spawn_state,
            self.exp_map_marker_state,
            self.exp_build_zone_state,
            self.underground_state,
            self.hazard_state,
            self.dynamic_ai_state,
        ):
            if state is None:
                continue
            try:
                await state.reload_and_broadcast()
            except Exception:
                log.warning(
                    "Map editor reload failed for %s", type(state).__name__, exc_info=True
                )


def _has_json(directory: Path) -> bool:
    """True when *directory* holds at least one .json — an empty folder is the
    same as no folder here, and Expansion creates both before it fills them."""
    return directory.is_dir() and any(directory.glob("*.json"))


# The class of each optional layer in `present_layers`. The three TraderPlus
# classes read the profile through the config, the rest the mission dir.
_LAYER_CLASSES: dict[str, type] = {
    "traders_state": TradersState,
    "safezone_state": SafeZoneState,
    "garage_state": GarageState,
    "exp_safezone_state": ExpansionSafeZoneState,
    "exp_traderzone_state": ExpansionTraderZoneState,
    "exp_spawn_state": ExpansionSpawnLocationState,
    "exp_mission_state": ExpansionMissionState,
    "exp_ai_patrol_state": ExpansionAIPatrolState,
    "exp_ai_location_state": ExpansionAILocationState,
    "exp_market_spawn_state": ExpansionMarketSpawnState,
    "exp_map_marker_state": ExpansionMapMarkerState,
    "exp_build_zone_state": ExpansionBuildZoneState,
    "underground_state": UndergroundTriggerState,
}


def present_layers(config: DayzConfig, mission_dir: Path | None) -> frozenset[str]:
    """The `MapEditorState` fields of the optional layers whose files exist now:
    TraderPlus's configs, Expansion's map data and the underground triggers.
    Only stat calls, so a caller can ask it on every request."""
    from app.traderplus.trader_common import (
        has_traderplus_garage_config,
        has_traderplus_general_config,
        has_traderplus_safezone_config,
    )

    found = {
        name
        for name, present in (
            ("traders_state", has_traderplus_general_config),
            ("safezone_state", has_traderplus_safezone_config),
            ("garage_state", has_traderplus_garage_config),
        )
        if present(config)
    }
    if mission_dir and mission_dir.is_dir():
        files = {
            "exp_safezone_state": ExpansionSafeZoneState.file_path(mission_dir).is_file(),
            "exp_traderzone_state": _has_json(ExpansionTraderZoneState.dir_path(mission_dir)),
            "exp_spawn_state": ExpansionSpawnLocationState.file_path(mission_dir).is_file(),
            "exp_mission_state": _has_json(ExpansionMissionState.dir_path(mission_dir)),
            "exp_ai_patrol_state": ExpansionAIPatrolState.file_path(mission_dir).is_file(),
            "exp_ai_location_state": ExpansionAILocationState.file_path(mission_dir).is_file(),
            "exp_market_spawn_state": ExpansionMarketSpawnState.file_path(mission_dir).is_file(),
            "exp_map_marker_state": ExpansionMapMarkerState.file_path(mission_dir).is_file(),
            "exp_build_zone_state": ExpansionBuildZoneState.file_path(mission_dir).is_file(),
            "underground_state": UndergroundTriggerState.file_path(mission_dir) is not None,
        }
        found |= {name for name, present in files.items() if present}
    return frozenset(found)


def build_map_editor_state(config: DayzConfig, broadcaster: Any) -> MapEditorState:
    """Load every editor state object for *config*.

    Blocking: parses the mission XML and the TraderPlus JSON configs. Callers
    on an event loop should run it in a threadpool.
    """
    # A new state (first use, a map switch, a manual reload) is a new session.
    versioning.new_session()
    me = MapEditorState(config=config, broadcaster=broadcaster)

    # Spawn state (eventspawns + event metadata for display). It also resolves
    # the mission dir and terrain that the others are constructed from.
    me.spawn_state = SpawnEditorState(config, broadcaster)
    me.spawn_state.load()
    me.mission = get_current_mission(config.server_cfg_path) or ""

    me.events_state = EventsState(me.spawn_state.mission_dir, broadcaster)
    me.events_state.load()

    me.playerspawn_state = PlayerSpawnState(
        me.spawn_state.mission_dir, me.spawn_state.terrain, broadcaster
    )
    me.playerspawn_state.load()

    terrain = me.spawn_state.terrain

    # The optional layers: absent mod files leave these None. Each layer is
    # independent, so a server with trader zones but no contaminated areas
    # gets the one it has.
    mission_dir = me.spawn_state.mission_dir
    me.layers = present_layers(config, mission_dir)
    by_config = (TradersState, SafeZoneState, GarageState)
    for name in sorted(me.layers):
        cls = _LAYER_CLASSES[name]
        source = config if cls in by_config else mission_dir
        state = cls(source, terrain, broadcaster)
        state.load()
        setattr(me, name, state)
    if mission_dir and mission_dir.is_dir():
        me.hazard_state = HazardState(config, mission_dir, terrain, broadcaster)
        me.hazard_state.load()
        me.dynamic_ai_state = DynamicAiState(config, terrain, broadcaster)
        me.dynamic_ai_state.load()

    return me
