"""Location database — searchable town/landmark data for coordinate picking."""

from __future__ import annotations

import json
import logging
import math
import threading
from dataclasses import dataclass
from pathlib import Path

from app.shared.json_utils import atomic_write_text, read_text_to_rewrite
from app.shared.zip_utils import is_bare_name

log = logging.getLogger(__name__)


@dataclass
class Location:
    """A named location on a DayZ map.

    *label* is the in-game label when it says more than *name* does: the
    Cyrillic on Chernarus and Sakhal, "FACTORY" under "Sebjan Factory".
    *own* is True for a name from the admin's own label file, which the map
    editor can change (plans/87), False for one from the world config.
    """
    name: str
    x: float
    z: float
    type: str
    label: str | None = None
    own: bool = False


# Every `type` a label file may use. The world config's own classes are folded
# into these (NameCity -> City, StrongpointArea -> Military, ...; plans/60 C),
# and webui's LOCATION_COLORS has a colour for each.
LOCATION_TYPES: frozenset[str] = frozenset({
    "Capital", "City", "Village", "Local", "Hill", "Marine", "Camp", "Ruin",
    "RailroadStation", "Military", "Airfield",
})


# Terrains that share another terrain's location database (reskins / variants
# with identical world coords but no own locations_*.json).
LOCATION_TERRAIN_ALIASES: dict[str, str] = {
    "banovfrost": "banov",
}


def _user_data_dir() -> Path:
    """Return user-level location data dir (~/.config/zsm/locations/)."""
    from app.shared.paths import locations_dir
    return locations_dir()


class LocationFileError(ValueError):
    """The admin's own label file, or an imported one, cannot be used.

    A write refuses it with 400. `LocationFileUnreadable` is the stored file's own
    case.
    """


class LocationFileUnreadable(LocationFileError):
    """The admin's stored label file cannot be read or checked. Every write
    refuses it (409), so a file the admin edited by hand is never overwritten."""


#: An own name closer than this to a world-config name hides that name, in metres.
SHADOW_DISTANCE = 100.0
#: The longest name or label kept.
MAX_NAME_LENGTH = 80
#: The most own names one map keeps.
MAX_OWN_LOCATIONS = 5000

_own_lock = threading.Lock()


def _terrain_key(terrain: str) -> str | None:
    """*terrain* as the label file's key (lower case, alias applied), or None
    when it would not make the filename one path component (plans/68 PATH-4):
    it comes from a query string."""
    key = (terrain or "").lower()
    key = LOCATION_TERRAIN_ALIASES.get(key, key)
    return key if key and is_bare_name(f"locations_{key}.json") else None


def own_locations_path(terrain: str) -> Path | None:
    """The admin's own label file for *terrain*, or None for a bad terrain name."""
    key = _terrain_key(terrain)
    return _user_data_dir() / f"locations_{key}.json" if key else None


# The unreadable-file warnings `load_locations` already logged.
_WARNED: set[str] = set()


def load_locations(terrain: str, server_dir: Path | str | None = None) -> list[Location]:
    """The admin's own place names for a terrain, then the official map's own
    world-config names on *server_dir* that no own name stands in for.

    Zedstead ships no label file (plans/86): a map's place names are its makers'
    work. The admin's own names live in `locations_<terrain>.json` in the
    user's locations dir (the map editor writes it, plans/87). An official
    map's names come from its world PBO on the admin's own server
    (`world_names.world_names`). An own name hides a world name with the same
    name or within `SHADOW_DISTANCE`. An own file that cannot be read shows
    the world names alone. A bad terrain name reads nothing.
    """
    key = _terrain_key(terrain)
    if key is None:
        return []
    try:
        own = load_own_locations(terrain)
    except LocationFileError as exc:
        # Once per message: every load of the place names comes here, and the
        # same broken file would fill the log.
        if str(exc) not in _WARNED:
            _WARNED.add(str(exc))
            log.warning("%s", exc)
        own = []

    from app.maps.world_names import world_names

    names = {loc.name.casefold() for loc in own}
    # Own places bucketed in SHADOW_DISTANCE squares, so a world name looks at
    # its 3x3 neighbourhood instead of every own place (up to MAX_OWN_LOCATIONS).
    cells: dict[tuple[int, int], list[Location]] = {}
    for loc in own:
        cells.setdefault((int(loc.x // SHADOW_DISTANCE), int(loc.z // SHADOW_DISTANCE)), []).append(loc)

    def shadowed(e: dict) -> bool:
        if e["name"].casefold() in names:
            return True
        cx, cz = int(e["x"] // SHADOW_DISTANCE), int(e["z"] // SHADOW_DISTANCE)
        return any(
            math.hypot(e["x"] - loc.x, e["z"] - loc.z) < SHADOW_DISTANCE
            for dx in (-1, 0, 1) for dz in (-1, 0, 1)
            for loc in cells.get((cx + dx, cz + dz), ())
        )

    world = [
        Location(name=e["name"], x=e["x"], z=e["z"], type=e["type"])
        for e in world_names(server_dir, key)
        if not shadowed(e)
    ]
    return own + world


def load_own_locations(terrain: str) -> list[Location]:
    """The admin's own place names for *terrain*, each with ``own=True``.

    No file is no names. A file that is not JSON or not a list of places
    raises `LocationFileUnreadable`.
    """
    path = own_locations_path(terrain)
    if path is None or not path.is_file():
        return []
    try:
        data = json.loads(read_text_to_rewrite(path))
        return parse_location_entries(data, terrain, source=path.name)
    except (OSError, ValueError) as exc:
        raise LocationFileUnreadable(
            f"{path.name} cannot be read, so Zedstead leaves it as it is. Fix or remove it first: {exc}"
        ) from exc


def parse_location_entries(data, terrain: str, *, source: str = "The file") -> list[Location]:
    """Check a label file's list (``[{name, x, z, type, label?}]``, game
    coordinates) and return its places, each with ``own=True``.

    Raises `LocationFileError` with the first problem: not a list, a place
    with no name, an unknown type, a position that is not a number or is off
    the map. `name_ru` is read as `label` (its name before plans/60 C).
    """
    if not isinstance(data, list):
        raise LocationFileError(f"{source} must hold a list of places.")
    if len(data) > MAX_OWN_LOCATIONS:
        raise LocationFileError(f"{source} holds more than {MAX_OWN_LOCATIONS} places.")
    size = own_map_size(terrain)
    out = []
    for i, e in enumerate(data, start=1):
        if not isinstance(e, dict):
            raise LocationFileError(f"{source}: place {i} is not an object.")
        try:
            out.append(make_location(e, size))
        except LocationFileError as exc:
            raise LocationFileError(f"{source}: place {i}: {exc}") from None
    return out


def own_map_size(terrain: str) -> int | None:
    """The size that bounds an own place on *terrain* (its label-file key, alias
    applied), None for a map with no known size. The one lookup the file check
    and the map editor's add and change routes share."""
    from app.shared.seed_data import MAP_SIZES

    return MAP_SIZES.get(_terrain_key(terrain) or "")


def make_location(e: dict, size: int | None) -> Location:
    """One checked own place from *e* (``name``, ``x``, ``z``, ``type``,
    ``label``). *size* bounds the position (0..size), None for a map with no
    known size. Raises `LocationFileError`."""
    name = e.get("name")
    if not isinstance(name, str) or not name.strip():
        raise LocationFileError("A place needs a name.")
    name = name.strip()
    if len(name) > MAX_NAME_LENGTH:
        raise LocationFileError(f"A name is at most {MAX_NAME_LENGTH} characters.")
    kind = e.get("type", "Local")
    # A list or object is unhashable: `in` on the frozenset would raise TypeError (500).
    if not isinstance(kind, str) or kind not in LOCATION_TYPES:
        raise LocationFileError(f"Unknown type {str(kind)[:40]!r}. Use one of: {', '.join(sorted(LOCATION_TYPES))}.")
    coords = []
    for axis in ("x", "z"):
        v = e.get(axis)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise LocationFileError(f"{axis} must be a number.")
        try:
            v = float(v)  # an int too large for a float raises OverflowError
        except OverflowError:
            raise LocationFileError(f"{axis} must be a number.") from None
        if not math.isfinite(v):
            raise LocationFileError(f"{axis} must be a number.")
        if size is not None and not 0 <= v <= size:
            raise LocationFileError(f"{axis} is outside the map (0-{size}).")
        coords.append(round(v, 2))
    label = e.get("label") or e.get("name_ru") or None
    if label is not None:
        if not isinstance(label, str):
            raise LocationFileError("label must be text.")
        label = label.strip()[:MAX_NAME_LENGTH] or None
    return Location(name=name, x=coords[0], z=coords[1], type=kind, label=label, own=True)


def location_entry(loc: Location) -> dict:
    """*loc* as one label-file entry, the format an export holds."""
    entry = {"name": loc.name, "x": loc.x, "z": loc.z, "type": loc.type}
    if loc.label:
        entry["label"] = loc.label
    return entry


def export_own_locations(terrain: str) -> str:
    """The admin's own label file for *terrain* as JSON text (``[]`` when it
    has none). Raises `LocationFileError` for a file that cannot be read."""
    return _dump(load_own_locations(terrain))


def change_own_locations(terrain: str, change) -> list[Location]:
    """Read the own names, apply ``change(list) -> list`` and write the result.

    One lock covers the read and the write, so two edits never lose one
    another. A file that cannot be read raises `LocationFileError` before
    *change* runs, so it is never overwritten. *change* raises
    `LocationFileError` or `IndexError` to refuse.
    """
    path = own_locations_path(terrain)
    if path is None:
        raise LocationFileError("Unknown map.")
    with _own_lock:
        places = change(load_own_locations(terrain))
        if len(places) > MAX_OWN_LOCATIONS:
            raise LocationFileError(f"A map keeps at most {MAX_OWN_LOCATIONS} own places.")
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, _dump(places))
    return places


def import_own_locations(terrain: str, data, *, replace: bool) -> tuple[int, int]:
    """Add the places of an imported label file to the own names, or put them
    in place of the own names when *replace*. A place with the same name and
    position as one already kept is skipped. Returns (added, skipped).
    Raises `LocationFileError` for a bad file, before anything is written."""
    incoming = parse_location_entries(data, terrain, source="The imported file")
    counts = [0, 0]

    def merge(current: list[Location]) -> list[Location]:
        kept = [] if replace else list(current)
        seen = {(p.name.casefold(), p.x, p.z) for p in kept}
        for loc in incoming:
            k = (loc.name.casefold(), loc.x, loc.z)
            if k in seen:
                counts[1] += 1
                continue
            seen.add(k)
            kept.append(loc)
            counts[0] += 1
        return kept

    change_own_locations(terrain, merge)
    return counts[0], counts[1]


def _dump(places: list[Location]) -> str:
    """*places* as the label file's JSON text (UTF-8 names kept as written)."""
    return json.dumps([location_entry(p) for p in places], ensure_ascii=False, indent=2) + "\n"


def get_terrain_from_mission(mission_dir: Path) -> str:
    """Extract terrain name from a mission directory name.

    e.g. "dayzOffline.chernarusplus" → "chernarusplus"
    """
    parts = mission_dir.name.split(".", 1)
    return parts[-1].lower() if len(parts) > 1 else parts[0].lower()


