"""Dynamic AI's positions on the map (plans/47 D, decision D3).

Three lists in the mod's ``SpatialSettings.json`` carry map positions:

- **Points**: a circle (``Spatial_Position`` + ``Spatial_Radius``). Inside it the
  point's own spawns replace the group spawns, or, with ``Spatial_Safe``, nothing
  spawns at all.
- **Locations**: a trigger circle (``Spatial_TriggerPosition`` +
  ``Spatial_TriggerRadius``) and the positions the AI spawn at when a player
  enters it (``Spatial_SpawnPosition``, a list).
- **Audio**: a location shaped the same way that a player's noise triggers.

What the map may do is D3's answer: **move and resize, and a new entry only as a
copy of an existing one.** Nothing is created from a click, because every other
key of an entry (faction, loadouts, counts) has to come from somewhere, and an
entry that already works is the only source zsm can stand behind.

The rules shared with the other JSON layers:

- **Every write patches the entry that was read.** The Configs page's Dynamic
  AI tables edit the same file and keep positions as they are; this module
  edits positions and nothing else. Keys the next mod release adds survive both.
- **Height is kept.** ``y`` is the second number of every vector, and this is a
  top-down view. With ``Locations_Enabled`` / ``Audio_Enabled`` at 2 the mod
  spawns at that height exactly, so a moved spawn position keeps the height it
  had; the layer's hint says so.
- **A number keeps the type the file gave it** (``_like``): the mod's file has
  float vectors and float radii, and a whole number stays whole.
- **``SpatialPlayerSettings.json`` is never read or written** (the mod's own
  player database), and nothing inside the mod folder is touched.

A default install puts every entry at ``[0, 1, 0]``: the mod's placeholder.
Those are drawn where the file says, in the map's corner, flagged
``unplaced`` so the tooltip can say it; a location's **Move here** puts the
trigger and its spawn positions somewhere real in one write.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.shared.json_utils import load_json_preserving
from app.shared.seed_data import MAP_SIZES
from app.spawn_editor import versioning
from app.spawn_editor.converters import game_to_latlng, get_map_size, latlng_to_game
from app.spawn_editor.hazard_state import _like, _num, _required

if TYPE_CHECKING:
    from app.shared.config import DayzConfig
    from app.spawn_editor.state import Broadcaster

log = logging.getLogger(__name__)

#: URL section -> the list in the file.
SECTIONS = {"points": "Point", "locations": "Location", "audio": "Audio"}
#: Which of the file's switches turns each list on (0 off, 1 or 2 on).
_MODE_KEYS = {"points": "Points_Enabled", "locations": "Locations_Enabled", "audio": "Audio_Enabled"}


def _xyz(value: Any) -> list:
    """The file's ``[x, y, z]``, padded to three; each number as it was."""
    out: list = [0.0, 0.0, 0.0]
    if isinstance(value, list):
        for i, item in enumerate(value[:3]):
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                out[i] = item
    return out


def _unplaced(vector: list) -> bool:
    """The mod's placeholder: every entry of a fresh install sits at x = z = 0."""
    return float(vector[0]) == 0.0 and float(vector[2]) == 0.0


class DynamicAiState:
    """Dynamic AI's points, locations and audio locations for one mission."""

    broadcast_type = "dynamic_ai_changed"

    def __init__(self, config: DayzConfig, terrain: str, broadcaster: Broadcaster) -> None:
        self.config = config
        self.terrain = terrain
        self._broadcaster = broadcaster
        self._lock = asyncio.Lock()
        self.path: Path | None = None
        self._doc: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    @staticmethod
    def settings_path(config: DayzConfig) -> Path | None:
        """``SpatialSettings.json`` while the mod is installed and enabled.

        Found through ``dynamic_ai``, the module the Doctor and the Configs
        editors use, so all three agree on where the file is and when it counts.
        """
        from app.ops import dynamic_ai

        if dynamic_ai.installed_name(config) is None:
            return None
        return dynamic_ai.settings_dir(config) / dynamic_ai.V21.settings

    def load(self) -> None:
        self.path = self.settings_path(self.config)
        self._doc = {}
        if self.path is None or not self.path.is_file():
            return
        try:
            data = load_json_preserving(self.path)
        except Exception as exc:  # a broken file must not take the editor down
            log.warning("Failed to parse %s: %s", self.path.name, exc)
            return
        if isinstance(data, dict):
            self._doc = data

    @property
    def has_data(self) -> bool:
        """True once the mod is installed and has written its settings: ``/map-info``'s flag."""
        return bool(self._doc)

    @property
    def watch_files(self) -> list[Path]:
        """The file an open map follows, for the watcher (the Configs tables write it too)."""
        return [self.path] if self.path is not None else []

    def _entries(self, section: str) -> list:
        items = self._doc.get(SECTIONS[section])
        return items if isinstance(items, list) else []

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def _point(self, vector: list) -> dict:
        lat, lng = game_to_latlng(float(vector[0]), float(vector[2]), self.terrain)
        return {
            "lat": round(lat, 6),
            "lng": round(lng, 6),
            "game_x": round(float(vector[0]), 2),
            "game_y": round(float(vector[1]), 2),
            "game_z": round(float(vector[2]), 2),
        }

    @staticmethod
    def _common(index: int, entry: dict) -> dict:
        return {
            "index": index,
            "name": str(entry.get("Spatial_Name") or f"#{index + 1}"),
            "faction": str(entry.get("Spatial_Faction", "")),
            "min_count": _num(entry.get("Spatial_MinCount")),
            "max_count": _num(entry.get("Spatial_MaxCount")),
        }

    def get_json(self) -> dict:
        points = []
        for index, entry in enumerate(self._entries("points")):
            if not isinstance(entry, dict):
                continue
            where = _xyz(entry.get("Spatial_Position"))
            points.append({
                **self._common(index, entry),
                **self._point(where),
                "radius": _num(entry.get("Spatial_Radius")),
                "safe": _num(entry.get("Spatial_Safe")) == 1,
                "unplaced": _unplaced(where),
            })

        def location(index: int, entry: dict) -> dict:
            trigger = _xyz(entry.get("Spatial_TriggerPosition"))
            raw = entry.get("Spatial_SpawnPosition")
            spawns = [
                {"index": n, **self._point(_xyz(vector))}
                for n, vector in enumerate(raw if isinstance(raw, list) else [])
            ]
            return {
                **self._common(index, entry),
                "trigger": self._point(trigger),
                "radius": _num(entry.get("Spatial_TriggerRadius")),
                "spawns": spawns,
                "unplaced": _unplaced(trigger),
            }

        return {
            "points": points,
            "locations": [
                location(i, e) for i, e in enumerate(self._entries("locations")) if isinstance(e, dict)
            ],
            "audio": [
                location(i, e) for i, e in enumerate(self._entries("audio")) if isinstance(e, dict)
            ],
            # 0 = the list is off; the layer still draws it, and says so.
            "modes": {key: _num(self._doc.get(field)) for key, field in _MODE_KEYS.items()},
            "map_size": get_map_size(self.terrain),
        }

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def _target(self, data: dict) -> tuple[float, float] | None:
        if data.get("lat") is None or data.get("lng") is None:
            return None
        return latlng_to_game(_required(data, "lat"), _required(data, "lng"), self.terrain)

    @staticmethod
    def _moved(vector: Any, x: float, z: float) -> list:
        """*vector* at (x, z), its height and number types kept."""
        old = _xyz(vector)
        return [_like(old[0], x), old[1], _like(old[2], z)]

    def _entry(self, section: str, index: int) -> dict | None:
        if section not in SECTIONS or self.path is None:
            return None
        entries = self._entries(section)
        if not 0 <= index < len(entries) or not isinstance(entries[index], dict):
            return None
        return entries[index]

    def _shift(self, entry: dict, dx: float, dz: float) -> None:
        """Move a location's spawn positions by (dx, dz), in place.

        Raises ValueError when one would land off the map. The route's guard
        checks only the clicked point, and a spawn position carried along keeps
        its offset from the trigger — after a trigger-only move that offset can
        be the width of the map (measured in the browser, 2026 Q3: x -2804).
        """
        spawns = entry.get("Spatial_SpawnPosition")
        if not isinstance(spawns, list):
            return
        # No size entry (a community map): no bound, as in routes._off_map_problem.
        size = MAP_SIZES.get(self.terrain)
        for n, vector in enumerate(spawns):
            old = _xyz(vector)
            x, z = float(old[0]) + dx, float(old[2]) + dz
            if size is not None and not (0 <= x <= size and 0 <= z <= size):
                raise ValueError(f"Spawn position {n + 1} would land outside the map (0–{size})")
            spawns[n] = self._moved(vector, x, z)

    def _apply(self, section: str, entry: dict, data: dict) -> None:
        """One edit on *entry*, in place. Raises ValueError on a bad request."""
        target = self._target(data)
        if section == "points":
            if "spawn" in data:
                raise ValueError("A point has no spawn positions")
            if target is not None:
                entry["Spatial_Position"] = self._moved(entry.get("Spatial_Position"), *target)
            if "radius" in data:
                entry["Spatial_Radius"] = _like(entry.get("Spatial_Radius"), max(1.0, _required(data, "radius")))
            return

        if "spawn" in data:
            spawns = entry.get("Spatial_SpawnPosition")
            n = data.get("spawn")
            if (
                target is None or isinstance(n, bool) or not isinstance(n, int)
                or not isinstance(spawns, list) or not 0 <= n < len(spawns)
            ):
                raise ValueError("spawn must name an existing spawn position, with lat and lng")
            spawns[n] = self._moved(spawns[n], *target)
            return

        if target is not None:
            old = _xyz(entry.get("Spatial_TriggerPosition"))
            entry["Spatial_TriggerPosition"] = self._moved(entry.get("Spatial_TriggerPosition"), *target)
            if data.get("carry_spawns"):
                # "Move here" with its box ticked: the whole location, its spawn
                # positions keeping their layout around the trigger.
                self._shift(entry, target[0] - float(old[0]), target[1] - float(old[2]))
        if "radius" in data:
            entry["Spatial_TriggerRadius"] = _like(
                entry.get("Spatial_TriggerRadius"), max(1.0, _required(data, "radius"))
            )

    async def update(self, section: str, index: int, data: dict) -> dict | None:
        """Move (``lat``/``lng``) or resize (``radius``) one entry.

        For a location or audio entry, ``spawn: n`` moves spawn position *n*
        instead of the trigger, and ``carry_spawns`` moves the spawn positions
        with the trigger. The edit is made on a copy, so a refused one leaves
        the entry as it was, in memory as on disk.
        """
        async with self._lock:
            entry = self._entry(section, index)
            if entry is None:
                return None
            edited = copy.deepcopy(entry)
            self._apply(section, edited, data)
            self._entries(section)[index] = edited
            self._write()
        await self._broadcast()
        return {"section": section, "index": index}

    async def duplicate(self, section: str, index: int, data: dict) -> dict | None:
        """Copy an entry to a new place (D3: new entries only by duplicating).

        The copy is the whole entry, name included, like the Configs table's
        Duplicate; it lands at ``lat``/``lng``, and a location's spawn positions
        keep their layout around the trigger.
        """
        async with self._lock:
            source = self._entry(section, index)
            if source is None:
                return None
            target = self._target(data)
            if target is None:
                raise ValueError("lat and lng are required")
            entry = copy.deepcopy(source)
            name = data.get("name")
            if isinstance(name, str) and name.strip():
                entry["Spatial_Name"] = name.strip()
            self._apply(section, entry, {"lat": data["lat"], "lng": data["lng"], "carry_spawns": True})
            entries = self._entries(section)
            entries.append(entry)
            self._write()
        await self._broadcast()
        return {"section": section, "index": len(entries) - 1}

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _write(self) -> None:
        # Atomic, one version snapshot per editor session, and every number
        # the edit did not touch written the way the file had it.
        assert self.path is not None
        versioning.write_json(self.path, self._doc)

    async def reload_and_broadcast(self) -> None:
        async with self._lock:
            self.load()
            await self._broadcast()

    async def _broadcast(self) -> None:
        await self._broadcaster.broadcast({"type": self.broadcast_type})
