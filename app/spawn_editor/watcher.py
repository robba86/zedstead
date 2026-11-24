"""Watch XML/JSON files for external changes, trigger reload and broadcast."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.spawn_editor.events_state import EventsState
    from app.spawn_editor.garage_state import GarageState
    from app.spawn_editor.playerspawn_state import PlayerSpawnState
    from app.spawn_editor.safezone_state import SafeZoneState
    from app.spawn_editor.state import SpawnEditorState
    from app.spawn_editor.traders_state import TradersState

log = logging.getLogger(__name__)

# Files we care about (lowercase for case-insensitive matching)
_SPAWN_FILES = {"cfgeventspawns.xml"}
_EVENT_FILES = {"events.xml"}
_PLAYER_SPAWN_FILES = {"cfgplayerspawnpoints.xml"}
_TRADER_FILES = {"traderplusgeneralconfig.json"}
_SAFEZONE_FILES = {"traderplussafezoneconfig.json"}
_GARAGE_FILES = {"traderplusgarageconfig.json"}
_UNDERGROUND_FILES = {"cfgundergroundtriggers.json"}
# The Hazard zones layer (plans/48 C): the mission's own file, and RadZone's
# two files under the profile, routed by name like the ones above.
_HAZARD_FILES = {
    "cfgeffectarea.json", "radiationconfigv1.json", "cratesystemconfigv1.json",
    "statictoxiczoneconfigv5.json", "movingtoxiczoneconfigv5.json",
}
# Dynamic AI's settings (plans/47 D), under the profile like RadZone's. The
# Configs page's Dynamic AI tables write it too.
_DYNAMIC_AI_FILES = {"spatialsettings.json"}
_ALL_WATCH = (
    _SPAWN_FILES | _EVENT_FILES | _PLAYER_SPAWN_FILES | _UNDERGROUND_FILES
    | _TRADER_FILES | _SAFEZONE_FILES | _GARAGE_FILES
)

# Expansion's map data. Two of its four layers are whole *folders* of files
# whose names the server picks (one per trader zone, one per mission), so these
# route on the parent directory rather than the filename — unlike everything
# above, where the name is fixed.
_EXP_SAFEZONE_FILE = "safezonesettings.json"
_EXP_SPAWN_FILE = "spawnsettings.json"
_EXP_TRADERZONE_DIR = "traderzones"
_EXP_MISSIONS_DIR = "missions"
_EXP_AI_PATROL_FILE = "aipatrolsettings.json"
_EXP_AI_LOCATION_FILE = "ailocationsettings.json"
_EXP_MARKET_FILE = "marketsettings.json"
_EXP_MAP_FILE = "mapsettings.json"
_EXP_BASEBUILDING_FILE = "basebuildingsettings.json"


def expansion_reloads(changed_files: set[Path], states: dict[str, object]) -> list[object]:
    """Which Expansion states a set of changed paths affects.

    Pure, so the routing is testable without a real filesystem watcher —
    `awatch` needs actual events, which is why nothing else in this module is
    covered.
    """
    out: list[object] = []
    names = {p.name.lower() for p in changed_files}
    parents = {p.parent.name.lower() for p in changed_files}

    def _add(key: str, hit: bool) -> None:
        state = states.get(key)
        if hit and state is not None and state not in out:
            out.append(state)

    _add("safezone", _EXP_SAFEZONE_FILE in names)
    _add("spawn", _EXP_SPAWN_FILE in names)
    _add("traderzone", _EXP_TRADERZONE_DIR in parents)
    _add("mission", _EXP_MISSIONS_DIR in parents)
    _add("ai_patrol", _EXP_AI_PATROL_FILE in names)
    _add("ai_location", _EXP_AI_LOCATION_FILE in names)
    _add("market", _EXP_MARKET_FILE in names)
    _add("map_marker", _EXP_MAP_FILE in names)
    _add("build_zone", _EXP_BASEBUILDING_FILE in names)
    return out


async def watch_mission_files(
    spawn_state: SpawnEditorState,
    events_state: EventsState,
    playerspawn_state: PlayerSpawnState,
    underground_state: object | None = None,
    traders_state: TradersState | None = None,
    safezone_state: SafeZoneState | None = None,
    garage_state: GarageState | None = None,
    expansion_states: dict[str, object] | None = None,
    hazard_state: object | None = None,
    dynamic_ai_state: object | None = None,
) -> None:
    """Watch mission dir for XML/JSON changes, reload and broadcast on change.

    Uses watchfiles for efficient filesystem monitoring with 500ms debounce.
    Routes changes to the appropriate state modules.
    """
    from watchfiles import awatch

    mission_dir = spawn_state.mission_dir
    watch_paths: list[Path] = []

    # Mission root (eventspawns, playerspawnpoints, undergroundtriggers)
    if mission_dir.is_dir():
        watch_paths.append(mission_dir)

    # db/ subfolder (events.xml)
    db_dir = mission_dir / "db"
    if db_dir.is_dir() and db_dir not in watch_paths:
        watch_paths.append(db_dir)

    # Expansion's own folders. The settings editors write these files too, so
    # an edit there has to reach an open map editor.
    for sub in (("expansion", "settings"), ("expansion", _EXP_TRADERZONE_DIR),
                ("expansion", _EXP_MISSIONS_DIR)):
        exp_dir = mission_dir.joinpath(*sub)
        if exp_dir.is_dir() and exp_dir not in watch_paths:
            watch_paths.append(exp_dir)

    # TraderPlus config directory (profiles/TraderPlus/TraderPlusConfig/)
    if traders_state is not None:
        from app.traderplus.trader_common import get_traderplus_config_dir
        tp_dir = get_traderplus_config_dir(traders_state.config)
        if tp_dir and tp_dir.is_dir() and tp_dir not in watch_paths:
            watch_paths.append(tp_dir)

    # RadZone's and ToxicZone's config folders, while each mod is installed
    # (plans/48 C). Their Configs editors write these files, and an open map
    # has to follow.
    watched_files = [
        *getattr(hazard_state, "zone_files", []),
        *getattr(dynamic_ai_state, "watch_files", []),
    ]
    for watched in watched_files:
        if watched.parent.is_dir() and watched.parent not in watch_paths:
            watch_paths.append(watched.parent)

    if not watch_paths:
        log.warning("No directories to watch — file watcher disabled")
        return

    log.info("Watching for file changes in: %s", [str(p) for p in watch_paths])

    async for changes in awatch(*watch_paths):
        changed_files = {Path(path) for _, path in changes}
        changed_names = {p.name.lower() for p in changed_files}

        # Check for trader config changes
        trader_changed = bool(changed_names & _TRADER_FILES) and traders_state is not None
        safezone_changed = bool(changed_names & _SAFEZONE_FILES) and safezone_state is not None
        garage_changed = bool(changed_names & _GARAGE_FILES) and garage_state is not None

        expansion_changed = expansion_reloads(changed_files, expansion_states or {})
        hazard_changed = bool(changed_names & _HAZARD_FILES) and hazard_state is not None
        dynamic_ai_changed = (
            bool(changed_names & _DYNAMIC_AI_FILES) and dynamic_ai_state is not None
        )

        relevant = changed_names & (
            _ALL_WATCH - _TRADER_FILES - _SAFEZONE_FILES - _GARAGE_FILES
        )
        if (
            not relevant
            and not trader_changed
            and not safezone_changed
            and not garage_changed
            and not expansion_changed
            and not hazard_changed
            and not dynamic_ai_changed
        ):
            continue

        # Debounce: wait for writes to finish
        await asyncio.sleep(0.5)
        log.info("File change detected (%s), reloading...", changed_names)

        # Route reloads to appropriate state modules
        if relevant & (_SPAWN_FILES | _EVENT_FILES):
            # eventspawns or events changed — reload spawn state (metadata)
            await spawn_state.reload_and_broadcast()

        if relevant & _EVENT_FILES:
            # events.xml changed — also reload events state
            await events_state.reload_and_broadcast()

        if relevant & _PLAYER_SPAWN_FILES:
            # player spawn points changed
            await playerspawn_state.reload_and_broadcast()

        if relevant & _UNDERGROUND_FILES and underground_state is not None:
            # cfgundergroundtriggers.json changed — most often from the Configs
            # page's own JSON editor, which writes the same file.
            await underground_state.reload_and_broadcast()

        if trader_changed:
            # TraderPlusGeneralConfig.json changed
            await traders_state.reload_and_broadcast()

        if safezone_changed:
            # TraderPlusSafeZoneConfig.json changed
            await safezone_state.reload_and_broadcast()

        if garage_changed:
            # TraderPlusGarageConfig.json changed
            await garage_state.reload_and_broadcast()

        if hazard_changed:
            # cfgEffectArea.json or a zone mod's file changed — most often from the
            # Configs page's table editors, which write the same files.
            await hazard_state.reload_and_broadcast()

        if dynamic_ai_changed:
            # SpatialSettings.json changed — most often from the Configs page's
            # Dynamic AI tables, which write the same file.
            await dynamic_ai_state.reload_and_broadcast()

        for state in expansion_changed:
            # An Expansion settings file changed — most often from the Configs
            # page's own editors, which write the same files.
            await state.reload_and_broadcast()
