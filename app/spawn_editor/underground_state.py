"""Underground triggers — `<mission>/cfgundergroundtriggers.json` (TODO H4).

What the file is, because nothing in zsm read it before 2026 Q3: a list of
boxes that decide how dark it is underground. Each trigger has a world
`Position`, a `Size` in X/Y/Z, an `Orientation` (yaw, pitch, roll) and an
`EyeAccommodation` — how much a player inside it can see without a flashlight,
where 1 is normal vision and 0 is black. `Breadcrumbs` are points along the way
in, each with its own accommodation weight and a radius, so the transition
fades instead of switching.

Three things this module has to get right, all from
`dayz-docs/wiki/pages/underground-areas-configuration.wiki`:

* **The type is derived, never stored.** A trigger with breadcrumbs is
  *transitional*; without them it is *outer* when its accommodation is 1 and
  *inner* otherwise. It is computed here rather than in the browser so the CLI,
  the config editor and the map cannot disagree about it.
* **Unknown keys are preserved.** Sakhal's triggers carry twelve keys and
  Livonia's seven (`Comment`, `CustomSpawn`, `ParentNetworkId` and
  `UseLinePointFade` are Sakhal's alone; `AmbientSoundSet` is on every Livonia
  trigger too, which an earlier version of this note got wrong). Every mutation *patches the entry that
  was read* — a document rebuilt from the payload would drop the rest, which is
  the design decision and the reason the file
  got a raw-text editor first.
* **Y is altitude, and the map is flat.** `game_to_latlng` takes X and Z, so
  the height rides along in the payload as `game_y` for the panel to show and
  is written back untouched unless the caller says otherwise.

The filename is matched case-insensitively for the same reason
`cfgEffectArea.json` is: Bohemia's own capitalisation differs between terrains.
"""

from __future__ import annotations

import asyncio
import logging
import math
from pathlib import Path
from typing import Any, TYPE_CHECKING

from app.shared.json_utils import load_json_preserving
from app.spawn_editor.converters import game_to_latlng, get_map_size, latlng_to_game, same_as_shown
from app.spawn_editor import versioning

if TYPE_CHECKING:
    from app.spawn_editor.state import Broadcaster

log = logging.getLogger(__name__)

UNDERGROUND_FILE = "cfgundergroundtriggers.json"

#: The two spellings Bohemia uses for a trigger's ambient sound, both real and
#: neither a superset of the other. `AmbientSoundSet` is on **all eight**
#: Livonia triggers and sixteen of Sakhal's thirty; `AmbientSoundType` is on
#: fourteen Sakhal triggers and appears on no other terrain. Reading only the
#: Sakhal-only name returned `""` for every Livonia trigger — measured
#: 2026 Q3 against a real mission, and the same mistake as reading `user=`
#: where `cfglimitsdefinitionuser.xml` writes `name=`. Both fixtures had been
#: written from the code rather than from Bohemia's files, so both passed.
#: Order matters: the common spelling wins where a trigger carries both.
_AMBIENT_KEYS = ("AmbientSoundSet", "AmbientSoundType")

#: Derived trigger kinds. Not a field in the file — see the module docstring.
TYPE_OUTER = "outer"
TYPE_INNER = "inner"
TYPE_TRANSITIONAL = "transitional"


def _xyz(value: Any) -> tuple[float, float, float]:
    """A `[x, y, z]` array as three floats, tolerating short or junk entries."""
    if not isinstance(value, (list, tuple)):
        return (0.0, 0.0, 0.0)
    out = []
    for i in range(3):
        try:
            out.append(float(value[i]))
        except (IndexError, TypeError, ValueError):
            out.append(0.0)
    return (out[0], out[1], out[2])


def _breadcrumbs(entry: dict) -> list:
    crumbs = entry.get("Breadcrumbs")
    return crumbs if isinstance(crumbs, list) else []


def _ambient_sound(entry: dict) -> str:
    """The trigger's ambient sound, under whichever name this terrain uses."""
    for key in _AMBIENT_KEYS:
        value = entry.get(key)
        if value:
            return str(value)
    return ""


def trigger_type(entry: dict) -> str:
    """Outer, inner or transitional, per the underground-areas spec.

    Bohemia states it as two rules: any trigger *with* breadcrumbs is
    transitional, and one without is outer when `EyeAccommodation` is 1 and
    inner otherwise.
    """
    if _breadcrumbs(entry):
        return TYPE_TRANSITIONAL
    try:
        accommodation = float(entry.get("EyeAccommodation", 0) or 0)
    except (TypeError, ValueError):
        accommodation = 0.0
    return TYPE_OUTER if accommodation >= 1 else TYPE_INNER


class UndergroundTriggerState:
    """Underground triggers for one mission, addressed by index into the file."""

    broadcast_type = "underground_triggers_changed"

    def __init__(self, mission_dir: Path, terrain: str, broadcaster: Broadcaster) -> None:
        self.mission_dir = mission_dir
        self.terrain = terrain
        self._broadcaster = broadcaster
        self._lock = asyncio.Lock()
        self._raw: dict[str, Any] = {}
        self.path: Path | None = self.file_path(mission_dir)

    @staticmethod
    def file_path(mission_dir: Path) -> Path | None:
        """The file whatever its capitalisation, or None when it is absent."""
        if not mission_dir or not mission_dir.is_dir():
            return None
        wanted = UNDERGROUND_FILE.lower()
        for path in mission_dir.iterdir():
            if path.is_file() and path.name.lower() == wanted:
                return path
        return None

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def load(self) -> None:
        self.path = self.file_path(self.mission_dir)
        if self.path is None:
            self._raw = {}
            return
        try:
            self._raw = load_json_preserving(self.path)
        except Exception as exc:
            log.warning("Failed to parse %s: %s", self.path.name, exc)
            self._raw = {}

    def _triggers(self) -> list:
        entries = self._raw.get("Triggers")
        return entries if isinstance(entries, list) else []

    def get_json(self) -> dict:
        map_size = get_map_size(self.terrain)
        triggers = []
        for index, entry in enumerate(self._triggers()):
            if not isinstance(entry, dict):
                continue
            x, y, z = _xyz(entry.get("Position"))
            lat, lng = game_to_latlng(x, z, self.terrain)
            size_x, size_y, size_z = _xyz(entry.get("Size"))
            yaw, pitch, roll = _xyz(entry.get("Orientation"))
            crumbs = []
            for c_index, crumb in enumerate(_breadcrumbs(entry)):
                if not isinstance(crumb, dict):
                    continue
                cx, cy, cz = _xyz(crumb.get("Position"))
                c_lat, c_lng = game_to_latlng(cx, cz, self.terrain)
                crumbs.append({
                    "index": c_index,
                    "lat": round(c_lat, 6),
                    "lng": round(c_lng, 6),
                    "game_x": round(cx, 2),
                    "game_y": round(cy, 2),
                    "game_z": round(cz, 2),
                    "eye_accommodation": _as_float(crumb.get("EyeAccommodation"), 0.0),
                    # -1 means "use the engine's default", which is not the
                    # same as 0 and must survive the round trip as itself.
                    "radius": _as_float(crumb.get("Radius"), -1.0),
                    "use_raycast": bool(crumb.get("UseRaycast", 0)),
                })
            triggers.append({
                "index": index,
                # `Comment` is Sakhal's own field and the only human label the
                # format has; without it a trigger has nothing but its index.
                "name": str(entry.get("Comment") or "").strip() or f"Trigger {index}",
                "type": trigger_type(entry),
                "lat": round(lat, 6),
                "lng": round(lng, 6),
                "game_x": round(x, 2),
                "game_y": round(y, 2),
                "game_z": round(z, 2),
                "size_x": round(size_x, 2),
                "size_y": round(size_y, 2),
                "size_z": round(size_z, 2),
                "yaw": round(yaw, 2),
                "pitch": round(pitch, 2),
                "roll": round(roll, 2),
                "eye_accommodation": _as_float(entry.get("EyeAccommodation"), 0.0),
                "ambient_sound": _ambient_sound(entry),
                "breadcrumbs": crumbs,
                "map_size": map_size,
            })
        return {"triggers": triggers, "map_size": map_size}

    # ------------------------------------------------------------------
    # Mutations — every one patches the entry that was read
    # ------------------------------------------------------------------

    async def update_trigger(self, index: int, data: dict) -> dict | None:
        """Move, resize, rotate or re-light one trigger.

        Returns the reloaded payload, or None when *index* is not a trigger.
        Raises ValueError, before anything changes, for a value the file
        cannot hold (`check_value`). Only what differs from the file is
        written: a field sent back as the popup showed it (rounded) keeps the
        file's value, and an empty comment adds no `Comment` key.
        """
        async with self._lock:
            triggers = self._triggers()
            if index < 0 or index >= len(triggers):
                return None
            entry = triggers[index]
            if not isinstance(entry, dict):
                return None

            changes: dict[str, Any] = {}
            position = self._new_position(entry, data)
            if position is not None:
                changes["Position"] = position
            for key, source in (("Size", "size"), ("Orientation", "orientation")):
                if source in data:
                    check_value(key, data[source])
                    changes[key] = _keep_vector(entry.get(key), data[source])
            if "eye_accommodation" in data:
                check_value("EyeAccommodation", data["eye_accommodation"])
                changes["EyeAccommodation"] = _keep(entry.get("EyeAccommodation"), data["eye_accommodation"])
            if "comment" in data:
                comment = str(data["comment"])
                if comment or "Comment" in entry:
                    changes["Comment"] = comment

            await self._apply(entry, changes)
            return self.get_json()

    async def update_breadcrumb(self, index: int, crumb_index: int, data: dict) -> dict | None:
        """Move one breadcrumb, or change its weight, radius or raycast flag."""
        async with self._lock:
            triggers = self._triggers()
            if index < 0 or index >= len(triggers):
                return None
            entry = triggers[index]
            if not isinstance(entry, dict):
                return None
            crumbs = _breadcrumbs(entry)
            if crumb_index < 0 or crumb_index >= len(crumbs):
                return None
            crumb = crumbs[crumb_index]
            if not isinstance(crumb, dict):
                return None

            changes: dict[str, Any] = {}
            position = self._new_position(crumb, data)
            if position is not None:
                changes["Position"] = position
            if "eye_accommodation" in data:
                check_value("EyeAccommodation", data["eye_accommodation"])
                changes["EyeAccommodation"] = _keep(crumb.get("EyeAccommodation"), data["eye_accommodation"])
            if "radius" in data:
                check_value("Radius", data["radius"])
                changes["Radius"] = _keep(crumb.get("Radius"), data["radius"])
            if "use_raycast" in data:
                changes["UseRaycast"] = 1 if data["use_raycast"] else 0

            await self._apply(crumb, changes)
            return self.get_json()

    async def _apply(self, node: dict, changes: dict) -> None:
        """Write *changes* into *node* and the file, when any of them differs."""
        changed = {key: value for key, value in changes.items() if node.get(key) != value or key not in node}
        if not changed:
            return
        node.update(changed)
        self._write()
        await self._broadcast()

    def _new_position(self, node: dict, data: dict) -> list | None:
        """`Position` from lat/lng, keeping the height the file had; None
        when the request moves nothing.

        The map has no third axis, so a drag can only say where in X/Z. `game_y`
        is accepted for the panel's own height field and ignored otherwise —
        dropping it to 0 would sink an underground trigger through the floor.
        """
        current = node.get("Position")
        x, y, z = _xyz(current)
        if "game_y" in data:
            check_value("Position", [x, data["game_y"], z])
            y = _keep(y, data["game_y"])
        if data.get("lat") is None or data.get("lng") is None:
            if "game_y" not in data or not isinstance(current, list) or len(current) != 3:
                return None
            return [current[0], y, current[2]]
        new_x, new_z = latlng_to_game(
            _as_float(data["lat"], 0.0), _as_float(data["lng"], 0.0), self.terrain
        )
        position = [round(new_x, 6), y, round(new_z, 6)]
        check_value("Position", position)
        return position

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _write(self) -> None:
        if self.path is None:
            return
        versioning.write_json(self.path, self._raw, inline_scalar_lists=True)

    async def reload_and_broadcast(self) -> None:
        async with self._lock:
            self.load()
            await self._broadcast()

    async def _broadcast(self) -> None:
        await self._broadcaster.broadcast({"type": self.broadcast_type})


def _number(key: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{key} must be a finite number")
    return value


def check_value(key: str, value: Any) -> None:
    """Raise ValueError when *value* cannot be the file's *key*.

    From `underground-areas-configuration.wiki`: `Position`, `Orientation`
    and `Size` are X/Y/Z or yaw/pitch/roll vectors (:30-32), so exactly three
    numbers, and a size is a box, so above 0; `EyeAccommodation` runs from
    0 (complete darkness) to 1 (normal visibility, :13); a breadcrumb's
    `Radius` is metres, or -1 for the engine default (:57). Every number is
    finite: NaN and Infinity are not JSON.
    """
    if key in ("Position", "Orientation", "Size"):
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            raise ValueError(f"{key} must be three numbers")
        for item in value:
            if _number(key, item) <= 0 and key == "Size":
                raise ValueError("Size must be greater than 0 on every axis")
    elif key == "EyeAccommodation":
        if not 0 <= _number(key, value) <= 1:
            raise ValueError("EyeAccommodation must be between 0 and 1")
    elif key == "Radius":
        radius = _number(key, value)
        if radius != -1 and radius <= 0:
            raise ValueError("Radius must be greater than 0, or -1 for the engine default")


def check_trigger(entry: Any) -> None:
    """`check_value` on every key a trigger and its breadcrumbs carry."""
    if not isinstance(entry, dict):
        raise ValueError("A trigger must be an object")
    for key in ("Position", "Orientation", "Size", "EyeAccommodation"):
        if key in entry:
            check_value(key, entry[key])
    crumbs = entry.get("Breadcrumbs", [])
    if not isinstance(crumbs, list):
        raise ValueError("Breadcrumbs must be a list")
    for crumb in crumbs:
        if not isinstance(crumb, dict):
            raise ValueError("A breadcrumb must be an object")
        for key in ("Position", "EyeAccommodation", "Radius"):
            if key in crumb:
                check_value(key, crumb[key])


def _keep(current: Any, new: Any) -> Any:
    """The file's own value when *new* is it or the rounding the popup showed."""
    if isinstance(current, (int, float)) and not isinstance(current, bool) and same_as_shown(current, new):
        return current
    return new


def _keep_vector(current: Any, new: list) -> list:
    if not isinstance(current, list) or len(current) != len(new):
        return list(new)
    return [_keep(old, value) for old, value in zip(current, new)]


def _as_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback
