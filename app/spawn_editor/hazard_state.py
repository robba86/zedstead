"""The "Hazard zones" layer family (plans/48 C, decision D2).

One state for every kind of zone that hurts players where they stand:

- **Contaminated areas**, vanilla since DayZ 1.23: the static areas in the
  mission's ``cfgEffectArea.json`` (``Areas[].Data.Pos`` + ``Radius``). They had
  a table editor and no map at all.
- **RadZone**, when the mod is installed: its zones from
  ``RadiationConfigV1.json`` (a circle, or with ``IsSquareZone`` a rectangle
  between two corners; ``Y`` is the map's Z) and, read-only, the loot crates
  from ``CrateSystemConfigV1.json``.

- **ToxicZone**, when the mod is installed: its static zones from
  ``StaticToxicZoneConfigV5.json`` (RadZone's shape under other key names),
  their loot crates (read-only), and its **moving zones** from
  ``MovingToxicZoneConfigV5.json``: a path from a start point to an end point.
  Dragging either end writes that point and recomputes the path the mod needs
  (``toxic_zone.apply_derived``: ``Coeff``, ``YtoOrigin``, ``Norme`` and
  ``Speed``'s sign, D3/D7/D8) in the same write.

The dynamic contaminated zones are events (``StaticContaminatedArea`` in
events.xml) and already draw as event spawns.

Every mutation **patches** the entry that was read: a drag writes the centre
(and a resize the radius) and nothing else, so particle names, heights,
messages and keys a mod adds later stay as they were. Going through
``effectarea_json.EffectArea`` would rebuild each area from its known fields
and drop the rest, which is why this reads the raw JSON instead. A number
keeps the type the file gave it: a whole-number ``Radius`` stays whole.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.shared.json_utils import load_json_preserving
from app.spawn_editor.converters import game_to_latlng, get_map_size, latlng_to_game
from app.spawn_editor import versioning

if TYPE_CHECKING:
    from app.shared.config import DayzConfig
    from app.spawn_editor.state import Broadcaster

log = logging.getLogger(__name__)

EFFECT_AREA_FILE = "cfgeffectarea.json"


def _num(value: Any, fallback: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    return float(value)


def _required(data: dict, key: str) -> float:
    """A number the request must carry. Falling back to 0 here would move a
    zone to the map's corner on a malformed request."""
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number")
    return float(value)


def _like(old: Any, value: float) -> int | float:
    """*value* in the type the file already used for this key."""
    if isinstance(old, int) and not isinstance(old, bool):
        return int(round(value))
    return round(float(value), 2)


class HazardState:
    """Contaminated areas, RadZone and ToxicZone zones for one mission."""

    broadcast_type = "hazards_changed"

    def __init__(
        self, config: DayzConfig, mission_dir: Path, terrain: str, broadcaster: Broadcaster
    ) -> None:
        self.config = config
        self.mission_dir = mission_dir
        self.terrain = terrain
        self._broadcaster = broadcaster
        self._lock = asyncio.Lock()
        self.effect_path: Path | None = None
        self.radiation_path: Path | None = None
        self.crates_path: Path | None = None
        self.toxic_static_path: Path | None = None
        self.toxic_moving_path: Path | None = None
        self._effect: dict[str, Any] = {}
        self._radiation: dict[str, Any] = {}
        self._crates: dict[str, Any] = {}
        self._toxic_static: dict[str, Any] = {}
        self._toxic_moving: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Where the files are
    # ------------------------------------------------------------------

    @staticmethod
    def effect_area_path(mission_dir: Path | None) -> Path | None:
        """The mission's cfgEffectArea.json, whatever its capitalisation."""
        from app.parsers.effectarea_json import get_effectarea_path

        return get_effectarea_path(mission_dir)

    @staticmethod
    def _mod_dir(config: DayzConfig, spec) -> Path | None:
        """A zone mod's config folder while the mod is installed, else None.

        Uninstalling keeps ``profiles/<Mod>/`` (live data), so the folder
        alone does not mean the zones are active.
        """
        if spec.installed_name(config) is None:
            return None
        folder = spec.config_dir(config)
        return folder if folder.is_dir() else None

    @classmethod
    def radzone_dir(cls, config: DayzConfig) -> Path | None:
        from app.ops import zone_mods

        return cls._mod_dir(config, zone_mods.RADZONE)

    @classmethod
    def toxiczone_dir(cls, config: DayzConfig) -> Path | None:
        from app.ops import toxic_zone

        return cls._mod_dir(config, toxic_zone.TOXICZONE)

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def load(self) -> None:
        from app.ops import toxic_zone, zone_mods

        self.effect_path = self.effect_area_path(self.mission_dir)
        self._effect = self._read(self.effect_path)

        folder = self.radzone_dir(self.config)
        self.radiation_path = folder / zone_mods.RADIATION if folder else None
        self.crates_path = folder / zone_mods.CRATES if folder else None
        self._radiation = self._read(self.radiation_path)
        self._crates = self._read(self.crates_path)

        folder = self.toxiczone_dir(self.config)
        self.toxic_static_path = folder / toxic_zone.STATIC if folder else None
        self.toxic_moving_path = folder / toxic_zone.MOVING if folder else None
        self._toxic_static = self._read(self.toxic_static_path)
        self._toxic_moving = self._read(self.toxic_moving_path)

    @staticmethod
    def _read(path: Path | None) -> dict[str, Any]:
        if path is None or not path.is_file():
            return {}
        try:
            data = load_json_preserving(path)
        except Exception as exc:
            log.warning("Failed to parse %s: %s", path.name, exc)
            return {}
        return data if isinstance(data, dict) else {}

    @property
    def has_radzone(self) -> bool:
        return self.radiation_path is not None and self.radiation_path.is_file()

    @property
    def has_toxiczone(self) -> bool:
        return any(p is not None and p.is_file() for p in (self.toxic_static_path, self.toxic_moving_path))

    @property
    def zone_files(self) -> list[Path]:
        """The mod files an open map follows, for the watcher."""
        return [p for p in (self.radiation_path, self.crates_path, self.toxic_static_path,
                            self.toxic_moving_path) if p is not None]

    @staticmethod
    def _list(doc: dict, key: str) -> list:
        entries = doc.get(key)
        return entries if isinstance(entries, list) else []

    def _areas(self) -> list:
        return self._list(self._effect, "Areas")

    def _zones(self) -> list:
        return self._list(self._radiation, "RadiationAreaLocation")

    def _toxic_zones(self) -> list:
        return self._list(self._toxic_static, "StaticToxicAreaLocation")

    def _toxic_moving_zones(self) -> list:
        return self._list(self._toxic_moving, "MovingToxicAreaLocation")

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def _latlng(self, x: float, z: float) -> dict:
        lat, lng = game_to_latlng(x, z, self.terrain)
        return {"lat": round(lat, 6), "lng": round(lng, 6)}

    def get_json(self) -> dict:
        contaminated = []
        for index, area in enumerate(self._areas()):
            if not isinstance(area, dict):
                continue
            data = area.get("Data") if isinstance(area.get("Data"), dict) else {}
            pos = data.get("Pos") if isinstance(data.get("Pos"), list) else []
            x = _num(pos[0]) if len(pos) > 0 else 0.0
            z = _num(pos[2]) if len(pos) > 2 else 0.0
            contaminated.append({
                "index": index,
                "name": str(area.get("AreaName", "")),
                "type": str(area.get("Type", "")),
                "game_x": round(x, 2),
                "game_z": round(z, 2),
                "radius": _num(data.get("Radius")),
                **self._latlng(x, z),
            })

        radzone = None
        if self.has_radzone:
            radzone = {
                "zones": [
                    {**self._zone_entry(index, zone, "RadiationZoneStatut"),
                     "radiation": _num(zone.get("NbSickGivenForRadiation"))}
                    for index, zone in enumerate(self._zones())
                    if isinstance(zone, dict)
                ],
                "crates": self._crate_points(),
                "crates_active": _num(self._crates.get("IsCrateSystemActive"), 1.0) != 0,
            }

        return {
            "contaminated": contaminated,
            "radzone": radzone,
            "toxiczone": self._toxiczone_json() if self.has_toxiczone else None,
            "map_size": get_map_size(self.terrain),
        }

    def _zone_entry(self, index: int, zone: dict, name_key: str) -> dict:
        """A RadZone-shaped zone (RadZone's and ToxicZone's static zones): a
        circle round ``X``/``Y``, or with ``IsSquareZone`` the rectangle
        between two corners."""
        x, z = _num(zone.get("X")), _num(zone.get("Y"))
        entry = {
            "index": index,
            "name": str(zone.get(name_key, "")),
            "shape": "square" if _num(zone.get("IsSquareZone")) == 1 else "circle",
            "game_x": round(x, 2),
            "game_z": round(z, 2),
            "radius": _num(zone.get("Radius")),
            **self._latlng(x, z),
        }
        if entry["shape"] == "square":
            x1, z1 = _num(zone.get("X1"), -1.0), _num(zone.get("Y1"), -1.0)
            x2, z2 = _num(zone.get("X2"), -1.0), _num(zone.get("Y2"), -1.0)
            entry["corners_set"] = min(x1, z1, x2, z2) >= 0
            entry["sw"] = self._latlng(x1, z1)
            entry["ne"] = self._latlng(x2, z2)
            entry["centre"] = self._latlng((x1 + x2) / 2, (z1 + z2) / 2)
        return entry

    def _toxiczone_json(self) -> dict:
        from app.ops import toxic_zone

        zones, crates = [], []
        for index, zone in enumerate(self._toxic_zones()):
            if not isinstance(zone, dict):
                continue
            entry = self._zone_entry(index, zone, "ToxicZoneStatut")
            zones.append(entry)
            if _num(zone.get("IsLootActive"), 1.0) == 0:
                continue
            for n, crate in enumerate(self._list(zone, "ToxicCratePosition")):
                pos = crate.get("POS") if isinstance(crate, dict) else None
                if isinstance(pos, list) and len(pos) == 3:
                    crates.append({
                        "area": entry["name"],
                        "index": n,
                        "loot": str(crate.get("Name", "")),
                        **self._latlng(_num(pos[0]), _num(pos[2])),
                    })

        moving = []
        for zone in toxic_zone.read_moving(self._toxic_moving):
            if not zone.points_known:
                continue
            want = zone.expected()
            moving.append({
                "index": zone.index,
                "name": zone.name,
                "radius": zone.radius or 0.0,
                "speed": abs(zone.speed or 0.0),
                "start": self._latlng(*zone.start),
                "end": self._latlng(*zone.end),
                # What the checks would say about this zone, for its tooltip.
                "north_south": want is None,
                "stale": bool(want and toxic_zone.stale_parts(zone, want)),
                "backwards": bool(want and zone.speed and (zone.speed > 0) != (want.direction > 0)),
            })

        return {
            "zones": zones,
            "crates": crates,
            "moving": moving,
            "static_active": _num(self._toxic_static.get("IsToxicZoneActive"), 1.0) != 0,
            "moving_active": _num(self._toxic_moving.get("IsMovingZoneActive"), 1.0) != 0,
        }

    def _crate_points(self) -> list[dict]:
        out = []
        areas = self._crates.get("AreaLocation")
        for area in areas if isinstance(areas, list) else []:
            if not isinstance(area, dict):
                continue
            crates = area.get("RZCrateLocations")
            for index, crate in enumerate(crates if isinstance(crates, list) else []):
                pos = crate.get("POS") if isinstance(crate, dict) else None
                if not isinstance(pos, list) or len(pos) != 3:
                    continue
                x, z = _num(pos[0]), _num(pos[2])
                out.append({
                    "area": str(area.get("RZName", "")),
                    "index": index,
                    "loot": str(crate.get("Name", "")),
                    **self._latlng(x, z),
                })
        return out

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def _target(self, data: dict) -> tuple[float, float] | None:
        if data.get("lat") is None or data.get("lng") is None:
            return None
        return latlng_to_game(_required(data, "lat"), _required(data, "lng"), self.terrain)

    async def update_contaminated(self, index: int, data: dict) -> dict | None:
        """Move (``lat``/``lng``) or resize (``radius``, metres) one area."""
        async with self._lock:
            areas = self._areas()
            if self.effect_path is None or not 0 <= index < len(areas):
                return None
            area = areas[index]
            if not isinstance(area, dict):
                return None
            block = area.setdefault("Data", {})
            target = self._target(data)
            if target is not None:
                pos = block.get("Pos") if isinstance(block.get("Pos"), list) else [0.0, 0.0, 0.0]
                pos = (list(pos) + [0.0, 0.0, 0.0])[:3]
                # The height is the file's: 0 snaps the area to the ground, and a
                # map drag has no third axis to change it with.
                pos[0] = _like(pos[0], target[0])
                pos[2] = _like(pos[2], target[1])
                block["Pos"] = pos
            if "radius" in data:
                block["Radius"] = _like(block.get("Radius"), max(1.0, _required(data, "radius")))
            self._write(self.effect_path, self._effect, inline_vectors=True)
            await self._broadcast()
            return self.get_json()

    def _move_zone(self, zone: dict, data: dict, editor: str) -> None:
        """Move or resize one RadZone-shaped zone, in place.

        A circle moves its ``X``/``Y`` and resizes its ``Radius``. A square
        zone moves as a whole: both corners shift by the same amount, so its
        size is kept (resizing one is the Zones table's job).
        """
        target = self._target(data)
        square = _num(zone.get("IsSquareZone")) == 1
        if target is not None:
            if square:
                x1, z1 = _num(zone.get("X1")), _num(zone.get("Y1"))
                x2, z2 = _num(zone.get("X2")), _num(zone.get("Y2"))
                if min(x1, z1, x2, z2) < 0:
                    raise ValueError(
                        f"This square zone has unset corners; set them on the {editor} "
                        "editor before moving it."
                    )
                dx = target[0] - (x1 + x2) / 2
                dz = target[1] - (z1 + z2) / 2
                for key, value in (("X1", x1 + dx), ("X2", x2 + dx), ("Y1", z1 + dz), ("Y2", z2 + dz)):
                    zone[key] = _like(zone.get(key), value)
            else:
                zone["X"] = _like(zone.get("X"), target[0])
                zone["Y"] = _like(zone.get("Y"), target[1])
        if "radius" in data and not square:
            zone["Radius"] = _like(zone.get("Radius"), max(1.0, _required(data, "radius")))

    async def update_radzone(self, index: int, data: dict) -> dict | None:
        """Move or resize one RadZone zone (``_move_zone``)."""
        async with self._lock:
            zones = self._zones()
            if self.radiation_path is None or not 0 <= index < len(zones):
                return None
            zone = zones[index]
            if not isinstance(zone, dict):
                return None
            self._move_zone(zone, data, "RadZone Zones")
            self._write(self.radiation_path, self._radiation)
            await self._broadcast()
            return self.get_json()

    async def update_toxic_static(self, index: int, data: dict) -> dict | None:
        """Move or resize one ToxicZone static zone. Its particles and crates
        stay where they are: they are positions of their own."""
        async with self._lock:
            zones = self._toxic_zones()
            if self.toxic_static_path is None or not 0 <= index < len(zones):
                return None
            zone = zones[index]
            if not isinstance(zone, dict):
                return None
            self._move_zone(zone, data, "ToxicZone Static Zones")
            self._write(self.toxic_static_path, self._toxic_static)
            await self._broadcast()
            return self.get_json()

    async def update_toxic_moving(self, index: int, data: dict) -> dict | None:
        """Move one end of a ToxicZone moving zone (``point``: ``start`` or
        ``end``, plus ``lat``/``lng``), or resize it (``radius``).

        Moving an end recomputes the path in the same write (D3). The edit is
        made on a copy, so a refused one (a north-south path) leaves the zone
        as it was, in memory as on disk.
        """
        from app.ops import toxic_zone

        async with self._lock:
            zones = self._toxic_moving_zones()
            if self.toxic_moving_path is None or not 0 <= index < len(zones):
                return None
            if not isinstance(zones[index], dict):
                return None
            zone = copy.deepcopy(zones[index])
            target = self._target(data)
            if target is not None:
                point = data.get("point")
                if point not in ("start", "end"):
                    raise ValueError('point must be "start" or "end"')
                prefix = "Start" if point == "start" else "End"
                zone[f"{prefix}X"] = _like(zone.get(f"{prefix}X"), target[0])
                zone[f"{prefix}Y"] = _like(zone.get(f"{prefix}Y"), target[1])
                toxic_zone.apply_derived(zone)
            if "radius" in data:
                zone["Radius"] = _like(zone.get("Radius"), max(1.0, _required(data, "radius")))
            zones[index] = zone
            self._write(self.toxic_moving_path, self._toxic_moving)
            await self._broadcast()
            return self.get_json()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _write(path: Path, data: dict, *, inline_vectors: bool = False) -> None:
        # Atomic, with one version snapshot per editor session like the other
        # JSON layers (versioning.py): a drag writes on every dragend, so one
        # per write would push the pre-session copy out of the 50 kept within
        # one sitting. Every file is patched rather than rewritten
        # (app.shared.json_patch), so it keeps its writer's layout and an edit
        # diffs as the lines it changed: Bohemia hand-writes `"Pos": [ x, y, z ]`
        # with tabs and CRLF, while RadZone's own loader breaks every array out.
        versioning.write_json(path, data, inline_scalar_lists=inline_vectors)

    async def reload_and_broadcast(self) -> None:
        async with self._lock:
            self.load()
            await self._broadcast()

    async def _broadcast(self) -> None:
        await self._broadcaster.broadcast({"type": self.broadcast_type})
