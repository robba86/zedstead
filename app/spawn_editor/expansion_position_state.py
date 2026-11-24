"""Map-editor state for three position lists inside Expansion's settings files.

TODO H4: the three position lists the
Configs editors could only show on their Raw tab become map layers, each with
move, resize (where there is a radius) and copy. Deleting stays in the Configs
editor.

- `MarketSettings.json` — `Land/Air/Water/TrainSpawnPositions`, where a vehicle
  bought from a trader appears.
- `MapSettings.json` — `ServerMarkers`, the markers every player sees.
- `BaseBuildingSettings.json` — `Zones`, where the listed items may or may not
  be placed.

What Expansion does with each, read from its own scripts
(github.com/salutesh/DayZ-Expansion-Scripts, 2026 Q3), because each one
decides what a move must do with the height:

- **A vehicle spawn keeps its height.** `ExpansionItemSpawnHelper.SpawnVehicle`
  creates land and air vehicles with `ECE_TRACE`, which drops them onto the
  surface *below* the stored position; a boat is created at it as-is. zsm has
  no terrain heights, so a move cannot pick a correct one — the popup shows the
  height, says which way it must be wrong to fail, and lets it be typed.
  `ExpansionTraderBase.HasVehicleSpawnPosition` only uses a position within
  `MaxVehicleDistanceToTrader` (3D) of the trader, which the popup also says.
- **A marker keeps its height.** `m_Visibility` is a bit set (2 = in the world,
  4 = on the map); a world marker is drawn at `m_Position`, height included.
- **A build zone keeps its height, and it matters.** `PlayerBase.
  GetBuildNoBuildZone` compares `vector.Distance(player, Center)` — 3D — with
  `Radius`. (Its `if (center[1] == 0) pos[1] == 0;` is a comparison, not the
  assignment it looks like, so a height of 0 does not switch the check to 2D.)
  `ActionDeployObject` reads `IsWhitelist`: 1 = only the listed items may be
  placed inside, 0 = the listed items may not; with `ZonesAreNoBuildZones` 0,
  nothing may be placed outside every zone.

Every write patches the document that was read (Expansion's files hold far more
than a layer shows), on a copy of the entry, so a refused edit changes nothing
in memory or on disk. Positions a move sets are rounded like the other
Expansion layers; a typed number that only repeats what was shown keeps the
file's own value (`keep_or_round`).
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any, TYPE_CHECKING

from app.shared.json_utils import load_json_preserving
from app.spawn_editor.converters import game_to_latlng, get_map_size, keep_or_round, latlng_to_game
from app.spawn_editor.expansion_state import SETTINGS_DIR, _ExpansionLayerState, _write_json, _xyz

if TYPE_CHECKING:
    from app.spawn_editor.state import Broadcaster

log = logging.getLogger(__name__)


class _PositionListState(_ExpansionLayerState):
    """One Expansion settings file holding lists of positioned entries.

    Subclasses name the file, the lists (URL name -> key in the file) and the
    key each entry keeps its position under, and add their own fields.
    """

    FILE = ""
    LISTS: dict[str, str] = {}
    POSITION_KEY = ""

    def __init__(self, mission_dir: Path, terrain: str, broadcaster: Broadcaster) -> None:
        super().__init__(mission_dir, terrain, broadcaster)
        self.path: Path = self.file_path(mission_dir)
        self._raw: dict[str, Any] = {}

    @classmethod
    def file_path(cls, mission_dir: Path) -> Path:
        return mission_dir.joinpath(*SETTINGS_DIR, cls.FILE)

    def load(self) -> None:
        if not self.path.is_file():
            self._raw = {}
            return
        try:
            self._raw = load_json_preserving(self.path)
        except Exception as exc:  # a broken file must not take the editor down
            log.warning("Failed to parse %s: %s", self.path.name, exc)
            self._raw = {}

    def _list(self, name: str) -> list | None:
        key = self.LISTS.get(name)
        entries = self._raw.get(key) if key else None
        return entries if isinstance(entries, list) else None

    def _entries(self, name: str) -> list[tuple[int, dict]]:
        return [(i, e) for i, e in enumerate(self._list(name) or []) if isinstance(e, dict)]

    def _place(self, index: int, entry: dict) -> dict:
        """The fields every entry shares: its address and where it is."""
        x, y, z = _xyz(entry.get(self.POSITION_KEY))
        lat, lng = game_to_latlng(x, z, self.terrain)
        return {
            "index": index,
            "lat": round(lat, 6),
            "lng": round(lng, 6),
            "game_x": round(x, 2),
            "game_y": round(y, 2),
            "game_z": round(z, 2),
        }

    # -- writes ---------------------------------------------------------

    def _move(self, entry: dict, data: dict) -> None:
        """Position from a drag or a click (lat/lng) and a typed height (game_y).

        x and z come from the map; the height is kept unless typed, because
        zsm cannot know the ground height at the new spot (module doc)."""
        current = entry.get(self.POSITION_KEY)
        vector = list(current) if isinstance(current, list) and len(current) >= 3 else _xyz(current)
        changed = False
        if "lat" in data and "lng" in data:
            x, z = latlng_to_game(float(data["lat"]), float(data["lng"]), self.terrain)
            vector[0], vector[2] = round(x, 2), round(z, 2)
            changed = True
        if "game_y" in data:
            vector[1] = keep_or_round(vector[1], data["game_y"])
            changed = True
        if changed:
            entry[self.POSITION_KEY] = vector

    def _edit(self, entry: dict, data: dict) -> None:
        """A subclass's own fields. Raises ValueError on a bad value."""

    def _copied(self, source: dict, data: dict, siblings: list) -> dict:
        """The new entry a copy makes. Subclasses fix what must stay unique."""
        return copy.deepcopy(source)

    async def update(self, name: str, index: int, data: dict) -> dict | None:
        """Move and/or edit one entry. None when there is no such entry."""
        async with self._lock:
            entries = self._list(name)
            if entries is None or not 0 <= index < len(entries) or not isinstance(entries[index], dict):
                return None
            edited = copy.deepcopy(entries[index])
            self._move(edited, data)
            self._edit(edited, data)
            entries[index] = edited
            _write_json(self.path, self._raw)
        await self._broadcast()
        return {"list": name, "index": index}

    async def duplicate(self, name: str, index: int, data: dict) -> dict | None:
        """Copy an entry to lat/lng — new entries are only ever copies of one
        that works, the rule every Expansion layer follows."""
        if "lat" not in data or "lng" not in data:
            raise ValueError("A copy needs a position")
        async with self._lock:
            entries = self._list(name)
            if entries is None or not 0 <= index < len(entries) or not isinstance(entries[index], dict):
                return None
            entry = self._copied(entries[index], data, entries)
            self._move(entry, {"lat": data["lat"], "lng": data["lng"]})
            self._edit(entry, {k: v for k, v in data.items() if k not in ("lat", "lng")})
            entries.append(entry)
            _write_json(self.path, self._raw)
        await self._broadcast()
        return {"list": name, "index": len(entries) - 1}


def _text(data: dict, key: str, what: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} can't be empty")
    return value.strip()


# ---------------------------------------------------------------------------
# Market vehicle spawn positions
# ---------------------------------------------------------------------------


class ExpansionMarketSpawnState(_PositionListState):
    """`MarketSettings.json`'s four vehicle spawn lists, addressed by kind + index."""

    broadcast_type = "expansion_market_spawns_changed"
    FILE = "MarketSettings.json"
    LISTS = {
        "land": "LandSpawnPositions",
        "air": "AirSpawnPositions",
        "water": "WaterSpawnPositions",
        "train": "TrainSpawnPositions",
    }
    POSITION_KEY = "Position"

    def get_json(self) -> dict:
        out: dict[str, Any] = {"map_size": get_map_size(self.terrain)}
        for name in self.LISTS:
            out[name] = [
                {**self._place(i, e), "heading": float(_xyz(e.get("Orientation"))[0])}
                for i, e in self._entries(name)
            ]
        for key, field in (
            ("max_distance", "MaxVehicleDistanceToTrader"),
            ("max_large_distance", "MaxLargeVehicleDistanceToTrader"),
        ):
            value = self._raw.get(field)
            out[key] = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
        out["enabled"] = bool(self._raw.get("MarketSystemEnabled", 0))
        return out

    def _edit(self, entry: dict, data: dict) -> None:
        if "orientation" in data:
            current = entry.get("Orientation")
            vector = list(current) if isinstance(current, list) and len(current) >= 3 else _xyz(current)
            vector[0] = keep_or_round(vector[0], data["orientation"], 1)
            entry["Orientation"] = vector


# ---------------------------------------------------------------------------
# Server map markers
# ---------------------------------------------------------------------------

#: Expansion's marker visibility bits (ExpansionMarkerData.c).
VIS_WORLD = 2
VIS_MAP = 4


def _argb_to_hex(value: Any) -> tuple[str, int]:
    """`m_Color` is a signed 32-bit ARGB int; the browser wants #rrggbb."""
    number = int(value) & 0xFFFFFFFF if isinstance(value, (int, float)) and not isinstance(value, bool) else 0xFFFFFFFF
    return f"#{number & 0xFFFFFF:06x}", number >> 24


def _hex_to_argb(color: str, alpha: int) -> int:
    text = color.strip().lstrip("#")
    if len(text) != 6 or any(c not in "0123456789abcdefABCDEF" for c in text):
        raise ValueError("The colour must look like #rrggbb")
    number = (alpha << 24) | int(text, 16)
    return number - 0x100000000 if number >= 0x80000000 else number


class ExpansionMapMarkerState(_PositionListState):
    """`MapSettings.json`'s `ServerMarkers`."""

    broadcast_type = "expansion_map_markers_changed"
    FILE = "MapSettings.json"
    LISTS = {"markers": "ServerMarkers"}
    POSITION_KEY = "m_Position"

    def get_json(self) -> dict:
        markers = []
        for i, entry in self._entries("markers"):
            color, alpha = _argb_to_hex(entry.get("m_Color"))
            visibility = entry.get("m_Visibility")
            visibility = int(visibility) if isinstance(visibility, (int, float)) else VIS_WORLD | VIS_MAP
            markers.append(
                {
                    **self._place(i, entry),
                    "uid": str(entry.get("m_UID", "")),
                    "text": str(entry.get("m_Text", "")),
                    "icon": str(entry.get("m_IconName", "")),
                    "color": color,
                    "alpha": alpha,
                    "on_map": bool(visibility & VIS_MAP),
                    "in_world": bool(visibility & VIS_WORLD),
                    "is_3d": bool(entry.get("m_Is3D", 0)),
                    "locked": bool(entry.get("m_Locked", 0)),
                }
            )
        return {
            "markers": markers,
            "enabled": bool(self._raw.get("EnableServerMarkers", 0)),
            "map_size": get_map_size(self.terrain),
        }

    def _edit(self, entry: dict, data: dict) -> None:
        if "text" in data:
            entry["m_Text"] = _text(data, "text", "The marker's text")
        if "icon" in data:
            entry["m_IconName"] = _text(data, "icon", "The icon name")
        if "color" in data:
            if not isinstance(data["color"], str):
                raise ValueError("The colour must look like #rrggbb")
            current, alpha = _argb_to_hex(entry.get("m_Color"))
            if data["color"].lower() != current:
                entry["m_Color"] = _hex_to_argb(data["color"], alpha)

    def _copied(self, source: dict, data: dict, siblings: list) -> dict:
        # Expansion keys markers by m_UID, so a copy needs its own.
        entry = copy.deepcopy(source)
        taken = {str(s.get("m_UID", "")) for s in siblings if isinstance(s, dict)}
        base = str(source.get("m_UID") or "ServerMarker")
        n = 2
        while f"{base}_{n}" in taken:
            n += 1
        entry["m_UID"] = f"{base}_{n}"
        return entry


# ---------------------------------------------------------------------------
# Base-building zones
# ---------------------------------------------------------------------------


class ExpansionBuildZoneState(_PositionListState):
    """`BaseBuildingSettings.json`'s `Zones`."""

    broadcast_type = "expansion_build_zones_changed"
    FILE = "BaseBuildingSettings.json"
    LISTS = {"zones": "Zones"}
    POSITION_KEY = "Center"

    def get_json(self) -> dict:
        zones = []
        for i, entry in self._entries("zones"):
            items = entry.get("Items")
            zones.append(
                {
                    **self._place(i, entry),
                    "name": str(entry.get("Name") or f"Zone {i + 1}"),
                    "radius": float(entry.get("Radius", 0) or 0),
                    "items": [str(item) for item in items] if isinstance(items, list) else [],
                    "whitelist": bool(entry.get("IsWhitelist", 0)),
                    "message": str(entry.get("CustomMessage") or ""),
                }
            )
        no_build = self._raw.get("ZonesAreNoBuildZones")
        return {
            "zones": zones,
            # None when the file does not say; the popup then leaves it out.
            "no_build_zones": bool(no_build) if isinstance(no_build, (int, float)) else None,
            "map_size": get_map_size(self.terrain),
        }

    def _edit(self, entry: dict, data: dict) -> None:
        if "name" in data:
            entry["Name"] = _text(data, "name", "The zone's name")
        if "radius" in data:
            entry["Radius"] = keep_or_round(entry.get("Radius"), data["radius"], 2)
