"""Map-editor state for DayZ-Expansion's generated map data.

Six layers over the JSON Expansion writes on its first boot, each mirroring
`SafeZoneState`/`TradersState`: parse on load, CRUD under a lock, write the file
atomically, broadcast. Nothing here creates a file — Expansion owns these, and a
state whose file is absent stays `None` on `MapEditorState`, which is what the
`/map-info` `expansion` flag reports and what the layer toggles gate on. (Four
shipped with plans/36 D; the two AI ones followed — the AI module writes its
files only when it is one of the installed modules.)

The rules that keep these edits safe:

- **Every write patches the raw JSON that was read.** Expansion's files carry far
  more than the editor shows — a trader zone's `Stock` alone is up to 1932
  entries — so a save re-serialises the *loaded* document with the edited field
  replaced, never a document built from the payload.
- **A trader zone's `Stock` never reaches the API payload.** Six zones x ~2000
  entries is 200 kB of JSON per poll for data the map cannot draw. It stays in
  `_raw` and is written back untouched.
- **Height stays untouched, except on a spawn point.** Expansion stores
  positions as `[x, y, z]` with `y` the terrain height; the editor is a top-down
  view and converts `x`/`z` only, so a drag preserves whatever height the entry
  had (`converters.game_to_latlng` takes x and z, same as every other layer
  here). **A moved spawn point gets `y = 0`** (2026 Q3): Expansion's
  respawn handler puts a height of 0 on the ground (`SurfaceY`) and uses any
  other height as it is, so a coastal point dragged to NWAF kept its 4.3 m and
  spawned players ~335 m underground.
- **Missions are read-only in this cut** (plans/36 D4). An airdrop's
  `DropLocation` and a contaminated area's `Data` are drawn so their positions can
  be *seen* next to traders and spawns; moving them is a settings edit with
  several coupled fields (heights, ring counts) and belongs to the settings
  editor, not a drag.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, TYPE_CHECKING

from app.ops.expansion_ai import is_object_patrol
from app.shared.json_utils import load_json_preserving
from app.spawn_editor.converters import game_to_latlng, get_map_size, keep_or_round, latlng_to_game
from app.spawn_editor import versioning

if TYPE_CHECKING:
    from app.spawn_editor.state import Broadcaster

log = logging.getLogger(__name__)

SETTINGS_DIR = ("expansion", "settings")
TRADERZONES_DIR = ("expansion", "traderzones")
MISSIONS_DIR = ("expansion", "missions")

SAFEZONE_FILE = "SafeZoneSettings.json"
SPAWN_FILE = "SpawnSettings.json"
AI_PATROL_FILE = "AIPatrolSettings.json"
AI_LOCATION_FILE = "AILocationSettings.json"


def _write_json(path: Path, data: Any) -> None:
    """Atomic write over the file's own text (``versioning.write_json``).

    Atomic for the same reason `SafeZoneState._save` is: the editor writes on
    every drag, and a truncated write would lose the file.
    """
    versioning.write_json(path, data)


def _xyz(value: Any, fallback: float = 0.0) -> list[float]:
    """A `[x, y, z]` list from whatever the file had, padded to three."""
    out = [fallback, fallback, fallback]
    if isinstance(value, (list, tuple)):
        for i, item in enumerate(value[:3]):
            try:
                out[i] = float(item)
            except (TypeError, ValueError):
                pass
    return out


class _ExpansionLayerState:
    """Shared plumbing: a mission dir, a lock, and one broadcast type."""

    #: Editor-side message name; `state._WEB_MESSAGE_TYPES` maps it to the
    #: namespaced type the browser sees.
    broadcast_type = ""

    def __init__(self, mission_dir: Path, terrain: str, broadcaster: Broadcaster) -> None:
        self.mission_dir = mission_dir
        self.terrain = terrain
        self._broadcaster = broadcaster
        self._lock = asyncio.Lock()

    def load(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    async def reload_and_broadcast(self) -> None:
        async with self._lock:
            self.load()
            await self._broadcast()

    async def _broadcast(self) -> None:
        await self._broadcaster.broadcast({"type": self.broadcast_type})


# ---------------------------------------------------------------------------
# Safe zones — CircleZones / CylinderZones / PolygonZones
# ---------------------------------------------------------------------------


class ExpansionSafeZoneState(_ExpansionLayerState):
    """`<mission>/expansion/settings/SafeZoneSettings.json`.

    Three shapes in one file, so a zone is addressed by ``kind`` + index rather
    than a flat index: reindexing across three lists would make a stale browser
    tab move the wrong zone.
    """

    broadcast_type = "expansion_safezones_changed"

    KINDS = ("circle", "cylinder", "polygon")
    _LIST_FOR = {
        "circle": "CircleZones",
        "cylinder": "CylinderZones",
        "polygon": "PolygonZones",
    }

    def __init__(self, mission_dir: Path, terrain: str, broadcaster: Broadcaster) -> None:
        super().__init__(mission_dir, terrain, broadcaster)
        self._raw: dict[str, Any] = {}
        self.path: Path = mission_dir.joinpath(*SETTINGS_DIR, SAFEZONE_FILE)

    @staticmethod
    def file_path(mission_dir: Path) -> Path:
        return mission_dir.joinpath(*SETTINGS_DIR, SAFEZONE_FILE)

    def load(self) -> None:
        if not self.path.is_file():
            self._raw = {}
            return
        try:
            self._raw = load_json_preserving(self.path)
        except Exception as exc:  # a broken file must not take the editor down
            log.warning("Failed to parse %s: %s", self.path.name, exc)
            self._raw = {}

    def _zones(self, kind: str) -> list:
        entries = self._raw.get(self._LIST_FOR[kind])
        return entries if isinstance(entries, list) else []

    def get_json(self) -> dict:
        map_size = get_map_size(self.terrain)
        out: dict[str, list[dict]] = {"circles": [], "cylinders": [], "polygons": []}

        for kind, key in (("circle", "circles"), ("cylinder", "cylinders")):
            for index, zone in enumerate(self._zones(kind)):
                x, y, z = _xyz(zone.get("Center"))
                lat, lng = game_to_latlng(x, z, self.terrain)
                entry = {
                    "kind": kind,
                    "index": index,
                    "lat": round(lat, 6),
                    "lng": round(lng, 6),
                    "game_x": round(x, 2),
                    "game_y": round(y, 2),
                    "game_z": round(z, 2),
                    "radius": float(zone.get("Radius", 0) or 0),
                    "map_size": map_size,
                }
                if kind == "cylinder":
                    entry["height"] = float(zone.get("Height", 0) or 0)
                out[key].append(entry)

        for index, zone in enumerate(self._zones("polygon")):
            points = []
            for position in zone.get("Positions", []) or []:
                x, y, z = _xyz(position)
                lat, lng = game_to_latlng(x, z, self.terrain)
                points.append(
                    {
                        "lat": round(lat, 6),
                        "lng": round(lng, 6),
                        "game_x": round(x, 2),
                        "game_y": round(y, 2),
                        "game_z": round(z, 2),
                    }
                )
            out["polygons"].append(
                {"kind": "polygon", "index": index, "points": points, "map_size": map_size}
            )

        out["enabled"] = bool(self._raw.get("Enabled", 0))
        return out

    async def update_zone(self, kind: str, index: int, data: dict) -> dict | None:
        """Move/resize one circle or cylinder, or move one polygon vertex."""
        if kind not in self._LIST_FOR:
            return None
        async with self._lock:
            zones = self._zones(kind)
            if index < 0 or index >= len(zones):
                return None
            zone = zones[index]

            if kind == "polygon":
                point = data.get("point")
                if point is None:
                    return None
                positions = zone.get("Positions")
                if not isinstance(positions, list) or not 0 <= int(point) < len(positions):
                    return None
                current = _xyz(positions[int(point)])
                x, z = self._resolve_xz(data, current)
                # y is the terrain height Expansion wrote; a top-down drag has
                # no opinion about it.
                positions[int(point)] = [round(x, 2), current[1], round(z, 2)]
            else:
                current = _xyz(zone.get("Center"))
                x, z = self._resolve_xz(data, current)
                zone["Center"] = [round(x, 2), current[1], round(z, 2)]
                if "radius" in data:
                    zone["Radius"] = float(data["radius"])
                if kind == "cylinder" and "height" in data:
                    zone["Height"] = float(data["height"])

            _write_json(self.path, self._raw)

        await self._broadcast()
        return {"kind": kind, "index": index}

    def _resolve_xz(self, data: dict, current: list[float]) -> tuple[float, float]:
        """Position from a drag (lat/lng) or from typed game coords."""
        if "lat" in data and "lng" in data:
            return latlng_to_game(float(data["lat"]), float(data["lng"]), self.terrain)
        return (
            float(data.get("game_x", current[0])),
            float(data.get("game_z", current[2])),
        )


# ---------------------------------------------------------------------------
# Trader zones — one file per zone
# ---------------------------------------------------------------------------


class ExpansionTraderZoneState(_ExpansionLayerState):
    """`<mission>/expansion/traderzones/*.json`, one file per zone.

    Addressed by **file stem**, not by index: the files are discovered with a
    glob, so an index would shift the moment a zone is added or removed.
    """

    broadcast_type = "expansion_traderzones_changed"

    def __init__(self, mission_dir: Path, terrain: str, broadcaster: Broadcaster) -> None:
        super().__init__(mission_dir, terrain, broadcaster)
        self.dir: Path = mission_dir.joinpath(*TRADERZONES_DIR)
        self._zones: dict[str, dict] = {}

    @staticmethod
    def dir_path(mission_dir: Path) -> Path:
        return mission_dir.joinpath(*TRADERZONES_DIR)

    def load(self) -> None:
        self._zones = {}
        if not self.dir.is_dir():
            return
        for path in sorted(self.dir.glob("*.json")):
            try:
                data = load_json_preserving(path)
            except Exception as exc:
                log.warning("Failed to parse trader zone %s: %s", path.name, exc)
                continue
            if isinstance(data, dict):
                self._zones[path.stem] = data

    def _path_for(self, zone_id: str) -> Path | None:
        """The file for *zone_id*, or None when it isn't a loaded zone.

        The id is matched against what `load` found rather than joined onto the
        directory, so nothing a request sends can address a path.
        """
        if zone_id not in self._zones:
            return None
        return self.dir / f"{zone_id}.json"

    def get_json(self) -> dict:
        map_size = get_map_size(self.terrain)
        zones = []
        for zone_id, data in self._zones.items():
            x, y, z = _xyz(data.get("Position"))
            lat, lng = game_to_latlng(x, z, self.terrain)
            zones.append(
                {
                    "id": zone_id,
                    "name": data.get("m_DisplayName", zone_id),
                    "lat": round(lat, 6),
                    "lng": round(lng, 6),
                    "game_x": round(x, 2),
                    "game_y": round(y, 2),
                    "game_z": round(z, 2),
                    "radius": float(data.get("Radius", 0) or 0),
                    "buy_percent": float(data.get("BuyPricePercent", -1) or -1),
                    "sell_percent": float(data.get("SellPricePercent", -1) or -1),
                    # Count only: the stock table is up to 1932 entries per zone
                    # and the map has nothing to draw with it.
                    "stock_items": len(data.get("Stock") or {}),
                    "map_size": map_size,
                }
            )
        return {"trader_zones": zones}

    async def update_zone(self, zone_id: str, data: dict) -> dict | None:
        async with self._lock:
            path = self._path_for(zone_id)
            if path is None:
                return None
            zone = self._zones[zone_id]
            current = _xyz(zone.get("Position"))

            if "lat" in data and "lng" in data:
                x, z = latlng_to_game(float(data["lat"]), float(data["lng"]), self.terrain)
                zone["Position"] = [round(x, 2), current[1], round(z, 2)]
            elif "game_x" in data or "game_z" in data:
                zone["Position"] = [
                    keep_or_round(current[0], data.get("game_x", current[0])),
                    current[1],
                    keep_or_round(current[2], data.get("game_z", current[2])),
                ]

            if "radius" in data:
                zone["Radius"] = float(data["radius"])
            if "buy_percent" in data:
                zone["BuyPricePercent"] = float(data["buy_percent"])
            if "sell_percent" in data:
                zone["SellPricePercent"] = float(data["sell_percent"])
            if "name" in data:
                zone["m_DisplayName"] = str(data["name"])

            # `zone` is the document that was read, Stock included — this writes
            # it back whole with only the edited fields changed.
            _write_json(path, zone)

        await self._broadcast()
        return {"id": zone_id}


# ---------------------------------------------------------------------------
# Spawn locations — SpawnSettings.SpawnLocations[].Positions[]
# ---------------------------------------------------------------------------


class ExpansionSpawnLocationState(_ExpansionLayerState):
    """`<mission>/expansion/settings/SpawnSettings.json`.

    One marker per position, grouped by location name. A position is addressed
    by ``(location index, position index)``.
    """

    broadcast_type = "expansion_spawnpoints_changed"

    def __init__(self, mission_dir: Path, terrain: str, broadcaster: Broadcaster) -> None:
        super().__init__(mission_dir, terrain, broadcaster)
        self.path: Path = mission_dir.joinpath(*SETTINGS_DIR, SPAWN_FILE)
        self._raw: dict[str, Any] = {}

    @staticmethod
    def file_path(mission_dir: Path) -> Path:
        return mission_dir.joinpath(*SETTINGS_DIR, SPAWN_FILE)

    def load(self) -> None:
        if not self.path.is_file():
            self._raw = {}
            return
        try:
            self._raw = load_json_preserving(self.path)
        except Exception as exc:
            log.warning("Failed to parse %s: %s", self.path.name, exc)
            self._raw = {}

    def _locations(self) -> list:
        entries = self._raw.get("SpawnLocations")
        return entries if isinstance(entries, list) else []

    def get_json(self) -> dict:
        map_size = get_map_size(self.terrain)
        locations = []
        for loc_index, location in enumerate(self._locations()):
            points = []
            for pos_index, position in enumerate(location.get("Positions", []) or []):
                x, y, z = _xyz(position)
                lat, lng = game_to_latlng(x, z, self.terrain)
                points.append(
                    {
                        "index": pos_index,
                        "lat": round(lat, 6),
                        "lng": round(lng, 6),
                        "game_x": round(x, 2),
                        "game_y": round(y, 2),
                        "game_z": round(z, 2),
                    }
                )
            locations.append(
                {
                    "index": loc_index,
                    "name": location.get("Name", f"Location {loc_index}"),
                    "use_cooldown": bool(location.get("UseCooldown", 0)),
                    "positions": points,
                    "map_size": map_size,
                }
            )
        return {"spawn_locations": locations, "enabled": bool(self._raw.get("EnableSpawnSelection", 0))}

    async def update_position(self, loc_index: int, pos_index: int, data: dict) -> dict | None:
        async with self._lock:
            locations = self._locations()
            if loc_index < 0 or loc_index >= len(locations):
                return None
            positions = locations[loc_index].get("Positions")
            if not isinstance(positions, list) or pos_index < 0 or pos_index >= len(positions):
                return None

            current = _xyz(positions[pos_index])
            if "lat" in data and "lng" in data:
                x, z = latlng_to_game(float(data["lat"]), float(data["lng"]), self.terrain)
            else:
                x = float(data.get("game_x", current[0]))
                z = float(data.get("game_z", current[2]))
            x, z = round(x, 2), round(z, 2)
            # Moved: height 0, which Expansion puts on the ground where the point
            # lands. The old height belongs to the old spot (see the module doc).
            y = current[1] if (x, z) == (round(current[0], 2), round(current[2], 2)) else 0.0
            positions[pos_index] = [x, y, z]

            _write_json(self.path, self._raw)

        await self._broadcast()
        return {"location": loc_index, "index": pos_index}


# ---------------------------------------------------------------------------
# Missions — airdrops and contaminated areas (read-only)
# ---------------------------------------------------------------------------


class ExpansionMissionState(_ExpansionLayerState):
    """`<mission>/expansion/missions/*.json`, read-only.

    Two shapes share the folder: an airdrop carries ``DropLocation`` (`x`/`z`,
    no height) and a contaminated area carries ``Data.Pos`` (`[x, y, z]`). Both
    have a radius, which is what makes them worth seeing beside the trader zones
    and spawn points.
    """

    broadcast_type = "expansion_missions_changed"

    def __init__(self, mission_dir: Path, terrain: str, broadcaster: Broadcaster) -> None:
        super().__init__(mission_dir, terrain, broadcaster)
        self.dir: Path = mission_dir.joinpath(*MISSIONS_DIR)
        self._missions: list[dict] = []

    @staticmethod
    def dir_path(mission_dir: Path) -> Path:
        return mission_dir.joinpath(*MISSIONS_DIR)

    def load(self) -> None:
        self._missions = []
        if not self.dir.is_dir():
            return
        for path in sorted(self.dir.glob("*.json")):
            try:
                data = load_json_preserving(path)
            except Exception as exc:
                log.warning("Failed to parse mission %s: %s", path.name, exc)
                continue
            if not isinstance(data, dict):
                continue
            entry = self._entry(path.stem, data)
            if entry is not None:
                self._missions.append(entry)

    def _entry(self, stem: str, data: dict) -> dict | None:
        drop = data.get("DropLocation")
        area = data.get("Data")
        if isinstance(drop, dict):
            x = float(drop.get("x", 0) or 0)
            z = float(drop.get("z", 0) or 0)
            return {
                "id": stem,
                "kind": "airdrop",
                "name": drop.get("Name") or data.get("MissionName", stem),
                "game_x": round(x, 2),
                "game_z": round(z, 2),
                "radius": float(drop.get("Radius", 0) or 0),
                "enabled": bool(data.get("Enabled", 0)),
                "weight": float(data.get("Weight", 0) or 0),
            }
        if isinstance(area, dict):
            x, _y, z = _xyz(area.get("Pos"))
            return {
                "id": stem,
                "kind": "contaminated",
                "name": data.get("MissionName", stem),
                "game_x": round(x, 2),
                "game_z": round(z, 2),
                "radius": float(area.get("Radius", 0) or 0),
                "enabled": bool(data.get("Enabled", 0)),
                "weight": float(data.get("Weight", 0) or 0),
            }
        # A mission type zsm doesn't know how to place is skipped rather than
        # guessed at — Expansion adds mission kinds, and a marker at 0,0 would
        # be worse than none.
        return None

    def get_json(self) -> dict:
        map_size = get_map_size(self.terrain)
        out = []
        for entry in self._missions:
            lat, lng = game_to_latlng(entry["game_x"], entry["game_z"], self.terrain)
            out.append({**entry, "lat": round(lat, 6), "lng": round(lng, 6), "map_size": map_size})
        return {"missions": out}


# ---------------------------------------------------------------------------
# AI patrols — AIPatrolSettings.json
# ---------------------------------------------------------------------------
#
# A patrol's `Waypoints` is Expansion's `TVectorArray`: a list of `[x, y, z]`,
# walked in order. Everything else about a patrol (faction, loadout, formation,
# accuracy, respawn timing — 47 fields on the current class) is Expansion's
# business and is carried through untouched; the map owns the path and nothing
# else.
#
# A patrol with an `ObjectClassName` and no waypoints spawns wherever objects of
# that class stand, so it has no place on the map. Those are counted and
# reported rather than dropped silently — "the file has 30 patrols and the map
# draws 12" needs an explanation.


class ObjectPatrolWaypoints(Exception):
    """A waypoint edit on an object patrol, whose waypoints are not map positions."""


class ExpansionAIPatrolState(_ExpansionLayerState):
    """`<mission>/expansion/settings/AIPatrolSettings.json`.

    Waypoints are addressed by ``(patrol index, waypoint index)``. Patrol order
    is the file's own and is never re-sorted — an index that moved under a
    browser would edit the wrong patrol.

    A patrol with an ``ObjectClassName`` runs at every building of that class,
    and its waypoints are metres from the building (plans/64 B4), so it is not
    drawn and its waypoints are not edited here (``ObjectPatrolWaypoints``).
    """

    broadcast_type = "expansion_ai_patrols_changed"

    def __init__(self, mission_dir: Path, terrain: str, broadcaster: Broadcaster) -> None:
        super().__init__(mission_dir, terrain, broadcaster)
        self.path: Path = mission_dir.joinpath(*SETTINGS_DIR, AI_PATROL_FILE)
        self._raw: dict[str, Any] = {}

    @staticmethod
    def file_path(mission_dir: Path) -> Path:
        return mission_dir.joinpath(*SETTINGS_DIR, AI_PATROL_FILE)

    def load(self) -> None:
        if not self.path.is_file():
            self._raw = {}
            return
        try:
            self._raw = load_json_preserving(self.path)
        except Exception as exc:
            log.warning("Failed to parse %s: %s", self.path.name, exc)
            self._raw = {}

    def _patrols(self) -> list:
        entries = self._raw.get("Patrols")
        return entries if isinstance(entries, list) else []

    def _waypoints(self, patrol_index: int) -> list | None:
        """The patrol's waypoint list, or None. Raises ``ObjectPatrolWaypoints``."""
        patrols = self._patrols()
        if patrol_index < 0 or patrol_index >= len(patrols):
            return None
        patrol = patrols[patrol_index]
        if not isinstance(patrol, dict):
            return None
        if is_object_patrol(patrol):
            raise ObjectPatrolWaypoints(patrol_index)
        points = patrol.get("Waypoints")
        return points if isinstance(points, list) else None

    def get_json(self) -> dict:
        map_size = get_map_size(self.terrain)
        out = []
        unplaceable = 0
        on_objects = 0
        for index, patrol in enumerate(self._patrols()):
            if not isinstance(patrol, dict):
                continue
            if is_object_patrol(patrol):
                # One patrol per building of its class, wherever those are;
                # its waypoints are metres from the building, not map points.
                unplaceable += 1
                on_objects += 1
                continue
            points = []
            for wp_index, waypoint in enumerate(patrol.get("Waypoints") or []):
                x, y, z = _xyz(waypoint)
                lat, lng = game_to_latlng(x, z, self.terrain)
                points.append(
                    {
                        "index": wp_index,
                        "lat": round(lat, 6),
                        "lng": round(lng, 6),
                        "game_x": round(x, 2),
                        "game_y": round(y, 2),
                        "game_z": round(z, 2),
                    }
                )
            if not points:
                # Expansion skips a patrol with no waypoints (plans/64 B6).
                # Counted, not hidden.
                unplaceable += 1
                continue
            out.append(
                {
                    "index": index,
                    # Expansion's own fallback for an unnamed patrol is the
                    # empty string, which would render as a blank tooltip.
                    "name": patrol.get("Name") or f"Patrol {index}",
                    "faction": patrol.get("Faction", ""),
                    "behaviour": patrol.get("Behaviour", ""),
                    "speed": patrol.get("Speed", ""),
                    "formation": patrol.get("Formation", ""),
                    "loadout": patrol.get("Loadout", ""),
                    "number_of_ai": patrol.get("NumberOfAI", 0),
                    "chance": patrol.get("Chance", 1.0),
                    "object_class": patrol.get("ObjectClassName", ""),
                    "waypoints": points,
                    "map_size": map_size,
                }
            )
        return {"patrols": out, "unplaceable_patrols": unplaceable, "object_patrols": on_objects}

    async def update_waypoint(self, patrol_index: int, wp_index: int, data: dict) -> dict | None:
        """Move one waypoint. Height is kept — this is a top-down view."""
        async with self._lock:
            points = self._waypoints(patrol_index)
            if points is None or wp_index < 0 or wp_index >= len(points):
                return None
            current = _xyz(points[wp_index])
            if "lat" in data and "lng" in data:
                x, z = latlng_to_game(float(data["lat"]), float(data["lng"]), self.terrain)
            else:
                x = float(data.get("game_x", current[0]))
                z = float(data.get("game_z", current[2]))
            points[wp_index] = [round(x, 2), current[1], round(z, 2)]
            _write_json(self.path, self._raw)

        await self._broadcast()
        return {"patrol": patrol_index, "index": wp_index}

    async def add_waypoint(self, patrol_index: int, data: dict) -> dict | None:
        """Append a waypoint to an existing patrol.

        Only ever appended: the order *is* the route, and inserting into the
        middle of someone's path from a map click is a guess about which leg
        they meant.
        """
        async with self._lock:
            points = self._waypoints(patrol_index)
            if points is None:
                return None
            if "lat" not in data or "lng" not in data:
                return None
            x, z = latlng_to_game(float(data["lat"]), float(data["lng"]), self.terrain)
            # The new point inherits the last one's height: Expansion snaps
            # patrol waypoints to the surface, and 0 is below ground on most of
            # the map, which reads as an error in the file.
            y = _xyz(points[-1])[1] if points else 0.0
            points.append([round(x, 2), y, round(z, 2)])
            _write_json(self.path, self._raw)

        await self._broadcast()
        return {"patrol": patrol_index, "index": len(points) - 1}

    async def delete_waypoint(self, patrol_index: int, wp_index: int) -> str | None:
        """Remove one waypoint. Returns an error string, or None on success.

        Emptying the list is refused: Expansion skips a patrol with no
        waypoints and logs it (plans/64 B6). Delete the patrol instead.
        """
        async with self._lock:
            points = self._waypoints(patrol_index)
            if points is None or wp_index < 0 or wp_index >= len(points):
                return "not found"
            if len(points) == 1:
                return "last waypoint"
            del points[wp_index]
            _write_json(self.path, self._raw)

        await self._broadcast()
        return None

    async def delete_patrol(self, patrol_index: int) -> bool:
        async with self._lock:
            patrols = self._patrols()
            if patrol_index < 0 or patrol_index >= len(patrols):
                return False
            del patrols[patrol_index]
            _write_json(self.path, self._raw)

        await self._broadcast()
        return True


# ---------------------------------------------------------------------------
# AI roaming locations — AILocationSettings.json
# ---------------------------------------------------------------------------
#
# `RoamingLocations` is where Expansion's roaming AI may spawn: a circle with a
# name, a type and an on/off flag (`{Name, Position, Radius, Type, Enabled}` —
# `ExpansionAIRoamingLocation`, 111 of them in a stock Chernarus file). Beside
# it, `NoGoAreas` are `{Name, Position, Radius, Height}` cylinders the AI must
# stay out of; a stock file ships none, so they are read defensively and an
# entry without a position and a radius is skipped rather than placed at 0,0.


class ExpansionAILocationState(_ExpansionLayerState):
    """`<mission>/expansion/settings/AILocationSettings.json`.

    Both lists are addressed by index into the file's own order.
    """

    broadcast_type = "expansion_ai_locations_changed"

    #: What a new location copies from the one it was duplicated from. The
    #: editor never invents a `Type`: it is one of Expansion's own location
    #: kinds and a made-up value is a roaming area that never triggers.
    _COPIED_KEYS = ("Type", "Radius", "Enabled")

    def __init__(self, mission_dir: Path, terrain: str, broadcaster: Broadcaster) -> None:
        super().__init__(mission_dir, terrain, broadcaster)
        self.path: Path = mission_dir.joinpath(*SETTINGS_DIR, AI_LOCATION_FILE)
        self._raw: dict[str, Any] = {}

    @staticmethod
    def file_path(mission_dir: Path) -> Path:
        return mission_dir.joinpath(*SETTINGS_DIR, AI_LOCATION_FILE)

    def load(self) -> None:
        if not self.path.is_file():
            self._raw = {}
            return
        try:
            self._raw = load_json_preserving(self.path)
        except Exception as exc:
            log.warning("Failed to parse %s: %s", self.path.name, exc)
            self._raw = {}

    def _list(self, key: str) -> list:
        entries = self._raw.get(key)
        return entries if isinstance(entries, list) else []

    def _locations(self) -> list:
        return self._list("RoamingLocations")

    def get_json(self) -> dict:
        map_size = get_map_size(self.terrain)

        def circle(index: int, entry: dict, extra: dict) -> dict:
            x, y, z = _xyz(entry.get("Position"))
            lat, lng = game_to_latlng(x, z, self.terrain)
            return {
                "index": index,
                "name": entry.get("Name") or f"Location {index}",
                "lat": round(lat, 6),
                "lng": round(lng, 6),
                "game_x": round(x, 2),
                "game_y": round(y, 2),
                "game_z": round(z, 2),
                "radius": float(entry.get("Radius", 0) or 0),
                "map_size": map_size,
                **extra,
            }

        locations = [
            circle(
                index,
                entry,
                {"type": entry.get("Type", ""), "enabled": bool(entry.get("Enabled", 0))},
            )
            for index, entry in enumerate(self._locations())
            if isinstance(entry, dict)
        ]

        no_go = []
        skipped = 0
        for index, entry in enumerate(self._list("NoGoAreas")):
            if not isinstance(entry, dict) or "Position" not in entry or "Radius" not in entry:
                skipped += 1
                continue
            no_go.append(circle(index, entry, {"height": float(entry.get("Height", 0) or 0)}))

        return {
            "roaming_locations": locations,
            "no_go_areas": no_go,
            "skipped_no_go_areas": skipped,
        }

    async def update_location(self, index: int, data: dict) -> dict | None:
        """Move, resize, rename, retype or enable/disable one roaming location.

        Patches the entry that was read: Expansion may add fields to it, and a
        document rebuilt from the payload would drop them.
        """
        async with self._lock:
            locations = self._locations()
            if index < 0 or index >= len(locations):
                return None
            entry = locations[index]
            if not isinstance(entry, dict):
                return None

            current = _xyz(entry.get("Position"))
            if "lat" in data and "lng" in data:
                x, z = latlng_to_game(float(data["lat"]), float(data["lng"]), self.terrain)
                entry["Position"] = [round(x, 2), current[1], round(z, 2)]
            elif "game_x" in data or "game_z" in data:
                x = float(data.get("game_x", current[0]))
                z = float(data.get("game_z", current[2]))
                entry["Position"] = [round(x, 2), current[1], round(z, 2)]
            if "radius" in data:
                entry["Radius"] = float(data["radius"])
            if "name" in data:
                entry["Name"] = str(data["name"])
            if "type" in data:
                entry["Type"] = str(data["type"])
            if "enabled" in data:
                # Expansion writes its flags as 0/1, so keep the file's form.
                entry["Enabled"] = 1 if data["enabled"] else 0

            _write_json(self.path, self._raw)

        await self._broadcast()
        return {"index": index}

    async def duplicate_location(self, index: int, data: dict) -> dict | None:
        """Copy an existing roaming location to a new position.

        Copying rather than creating from defaults is deliberate: `Type` is one
        of Expansion's own location kinds and nothing in zsm knows the set, so
        a new location is only ever made from one that already works.
        """
        async with self._lock:
            locations = self._locations()
            if index < 0 or index >= len(locations):
                return None
            source = locations[index]
            if not isinstance(source, dict):
                return None
            if "lat" not in data or "lng" not in data:
                return None

            x, z = latlng_to_game(float(data["lat"]), float(data["lng"]), self.terrain)
            entry: dict[str, Any] = {"Name": str(data.get("name") or source.get("Name", ""))}
            entry["Position"] = [round(x, 2), _xyz(source.get("Position"))[1], round(z, 2)]
            for key in self._COPIED_KEYS:
                if key in source:
                    entry[key] = source[key]
            locations.append(entry)
            _write_json(self.path, self._raw)

        await self._broadcast()
        return {"index": len(locations) - 1}

    async def delete_location(self, index: int) -> bool:
        async with self._lock:
            locations = self._locations()
            if index < 0 or index >= len(locations):
                return False
            del locations[index]
            _write_json(self.path, self._raw)

        await self._broadcast()
        return True
