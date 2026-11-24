"""The building map: every building on a terrain, read from the admin's own mission.

A map image is its author's work, so zsm ships none. Every map still needs
something to place spawns on, so the map editor draws the buildings instead
(plans/69).

The positions come from ``<mission>/mapgrouppos.xml``: one ``<group … pos="x y z">``
per loot building. A server cannot run its Central Economy without that file, so
it is on every install zsm manages, and reading it at runtime ships nothing of
the map's author — the same rule plans/65 set for every mod. Nothing is fetched
online.
"""

from __future__ import annotations

import logging
import math
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from app.maps.locations import get_terrain_from_mission

log = logging.getLogger(__name__)

#: Positions are rounded to this many metres and deduplicated. A building is
#: wider than that, so nothing visible is lost, and a 1.8 MB file shrinks a bit.
CELL_METRES = 5

#: One `<group …>` tag: a building. Its `pos="x y z"` holds the map position
#: in x and z, and height in y. A tag ends at the next `<` as well as at its
#: `>`: a mission's file is a third party's, and with `[^>]*` a run of
#: `<group ` with no `>` after it was read again from each one to the end of
#: the file (800 KB took 32 s, with the whole panel waiting).
_GROUP = re.compile(r"<group\b[^<>]*>")
_POS = re.compile(r'\bpos="([^"]*)"')
_NAME = re.compile(r'\bname="([^"]*)"')

#: A mapgrouppos.xml larger than this is not read: stock Chernarus is about
#: 1.8 MB, and a mission's file is a third party's.
MAX_MAPGROUPPOS_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class _Read:
    """One parse of a mapgrouppos.xml: each building's lower-cased group name
    and its cell, in file order, plus the sorted unique cells."""

    named: tuple[tuple[str, tuple[int, int]], ...]
    points: list[tuple[int, int]]


_cache: dict[Path, tuple[int, int, _Read]] = {}
_cache_lock = threading.Lock()


def mission_file(mission_dir: Path, lower_name: str) -> Path | None:
    """``<mission>/<lower_name>``, matched case-insensitively; never a subfolder."""
    try:
        for entry in mission_dir.iterdir():
            if entry.name.lower() == lower_name and entry.is_file():
                return entry
    except OSError:
        return None
    return None


def mapgrouppos_path(mission_dir: Path) -> Path | None:
    """``<mission>/mapgrouppos.xml``, matched case-insensitively; never a subfolder."""
    return mission_file(mission_dir, "mapgrouppos.xml")


def _cell(tag: str) -> tuple[int, int] | None:
    """The `CELL_METRES` cell of one `<group>` tag's position, or None.

    None for a tag with no `pos`, one that is not three numbers, or one that is
    not finite. A downloaded mission's file is a third party's: `1e999` reads
    as infinity, which `round` cannot turn into a cell, and the route answered
    500 for the whole terrain (plans/68 NB-1).
    """
    match = _POS.search(tag)
    parts = match.group(1).split() if match else []
    if len(parts) != 3:
        return None
    try:
        x, z = float(parts[0]), float(parts[2])
    except ValueError:
        return None
    if not (math.isfinite(x) and math.isfinite(z)):
        return None
    return round(x / CELL_METRES) * CELL_METRES, round(z / CELL_METRES) * CELL_METRES


_EMPTY = _Read((), [])


def _read(mission_dir: Path) -> _Read:
    """The one cached parse of *mission_dir*'s mapgrouppos.xml.

    Keyed by the file's ``(st_mtime_ns, st_size)``. Missing, unreadable or
    larger than `MAX_MAPGROUPPOS_BYTES` reads as empty.
    """
    path = mapgrouppos_path(mission_dir)
    if path is None:
        return _EMPTY
    try:
        st = path.stat()
    except OSError:
        return _EMPTY

    with _cache_lock:
        hit = _cache.get(path)
        if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            return hit[2]

    if st.st_size > MAX_MAPGROUPPOS_BYTES:
        log.warning(
            "Not reading %s: %d bytes, over the %d byte limit",
            path, st.st_size, MAX_MAPGROUPPOS_BYTES,
        )
        return _EMPTY
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.warning("Could not read %s: %s", path, exc)
        return _EMPTY

    named: list[tuple[str, tuple[int, int]]] = []
    skipped = 0
    for tag in _GROUP.findall(text):
        point = _cell(tag)
        if point is None:
            skipped += 1
            continue
        name = _NAME.search(tag)
        named.append(((name.group(1) if name else "").lower(), point))
    if skipped:
        log.warning("Skipped %d building(s) in %s with no usable pos", skipped, path)
    result = _Read(tuple(named), sorted({point for _, point in named}))

    with _cache_lock:
        _cache[path] = (st.st_mtime_ns, st.st_size, result)
    return result


def building_points(mission_dir: Path) -> list[tuple[int, int]]:
    """``(x, z)`` of every building in *mission_dir*, in game metres.

    Rounded to `CELL_METRES` and deduplicated, sorted so the answer is stable.
    ``[]`` when the file is missing or unreadable: an empty map is something the
    page explains, not an error.
    """
    return _read(mission_dir).points


def building_groups(mission_dir: Path) -> dict[str, list[tuple[int, int]]]:
    """Lower-cased group name -> the unique cells of every building of it.

    Read from the same cached parse as `building_points`. A group tag with no
    ``name`` is under ``""``. The loot layer (`app.maps.loot_map`) joins this
    to mapgroupproto.xml by name.
    """
    out: dict[str, set[tuple[int, int]]] = {}
    for name, point in _read(mission_dir).named:
        out.setdefault(name, set()).add(point)
    return {name: sorted(cells) for name, cells in out.items()}


def find_mission_for_terrain(
    mpmissions: Path, terrain: str, current: Path | None = None,
) -> Path | None:
    """The installed mission folder for *terrain*.

    The running mission when it is for that terrain, otherwise the first folder
    under *mpmissions* that is, by name, so the answer does not change between
    calls. ``None`` when no installed mission is for *terrain*.
    """
    if current is not None and current.is_dir() and get_terrain_from_mission(current) == terrain:
        return current
    try:
        folders = sorted(p for p in mpmissions.iterdir() if p.is_dir())
    except OSError:
        return None
    for folder in folders:
        if get_terrain_from_mission(folder) == terrain:
            return folder
    return None


def has_buildings(mpmissions: Path, terrain: str, current: Path | None = None) -> bool:
    """Whether the installed mission for *terrain* has a mapgrouppos.xml to draw."""
    mission = find_mission_for_terrain(mpmissions, terrain, current)
    return mission is not None and mapgrouppos_path(mission) is not None
