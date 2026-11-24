"""Forests and gravel and dirt roads, read from the mission's mapgroupcluster*.xml.

A mission's ``mapgroupcluster*.xml`` files list objects the Central Economy
hangs loot on outside buildings: trees, stumps and fallen trees (sticks,
bark, mushrooms), and on Sakhal and Badlands also the gravel and dirt road
pieces. Each is one ``<group name="…" pos="x y z" a="…"/>`` tag. Bohemia
publishes these files for admins to edit, so reading them reads no world
file and opens nothing encrypted (ROADMAP 50). Only the maps in
`registry.TERRAIN_LAYER_MAPS` are read, like every other terrain layer.

What the tags give:

- A road piece's name is its model (``RoadGrav_25``, ``PathD_7100``,
  ``Pathdirt2takistan_6konec``). The part after the family is the piece's
  shape: ``25``, ``12`` and ``6`` are straights of that many metres, and the
  rest are curves, ``7100`` being 7 degrees of a 100 m radius. A piece is drawn
  as its chord, centred on ``pos`` and turned by ``a`` degrees, with the
  direction ``(cos a, sin a)``. Measured on 1.30's Sakhal and Badlands
  missions, 2026 Q4: with that rule 93 % and 80 % of the piece ends lie within
  2 m of another piece's end, and every other reading of ``a`` stays under
  35 %. The ends are then snapped together (`_SNAP_METRES`), so
  `road_map.join_pieces` joins a road into one line.
- Every other tag that is not a hay bale, a straw stack or a manure pile is
  vegetation. Its count per `FOREST_CELL` square is the forest layer. These
  are the trees the economy uses, not every tree on the map, so the layer
  shows where the forests are, not each tree.

A mission is a third party's download: each file is read within a size limit,
a tag with a position that is not a finite number is skipped, and a road
piece of a shape this table does not know is left out and counted.
"""

from __future__ import annotations

import gzip
import json
import logging
import math
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from app.maps.building_map import _GROUP, _NAME, _POS
from app.maps.registry import terrain_layer_allowed
from app.maps.road_map import encode_roads

log = logging.getLogger(__name__)

#: One cluster file larger than this is not read. Stock Badlands' largest is
#: about 4.7 MB.
MAX_CLUSTER_BYTES = 64 * 1024 * 1024
#: More cluster files than this in one mission are not read past the limit.
#: Stock Badlands has six.
MAX_CLUSTER_FILES = 32

#: The forest layer's square, in metres.
FOREST_CELL = 50

#: Piece ends closer than this many metres are one point. A curve drawn as its
#: chord ends up to about a metre off its neighbour's end.
_SNAP_METRES = 2.0

#: A coordinate further out than this is not on any map.
_FAR = 100_000.0

_ANGLE = re.compile(r'\ba="([^"]*)"')

#: A road model's family (lower case, up to the first ``_``) -> the road kind
#: of `road_map.ROAD_CLASSES`. Checked in order, so ``pathdirt`` comes
#: before ``path``.
ROAD_FAMILIES = (
    ("roadgrav", "gravel"),
    ("roadmud", "dirt"),
    ("pathdirt", "dirt"),
    ("path", "path"),
    ("quarry", "dirt"),
)

#: Families in the cluster files that are neither a road nor vegetation.
NOT_VEGETATION = ("haybale", "strawstack", "manurepile", "land")

#: Families of conifers; every other tree is drawn as a broadleaf tree.
CONIFERS = ("picea", "pinus", "abies", "larix", "juniperus", "taxus")

#: A straight piece's shape -> its length in metres.
STRAIGHTS = {"25": 25.0, "12": 12.0, "6": 6.0, "6konec": 6.0}
#: A curved piece's shape -> (degrees, radius in metres).
CURVES = {
    "02000": (0.5, 2000.0),
    "11000": (1.0, 1000.0),
    "7100": (7.0, 100.0),
    "1025": (10.0, 25.0),
    "1050": (10.0, 50.0),
    "1075": (10.0, 75.0),
    "10100": (10.0, 100.0),
    "1575": (15.0, 75.0),
    "2250": (22.5, 50.0),
    "3025": (30.0, 25.0),
    "6010": (60.0, 10.0),
}
#: Short spellings some models use for the same shape.
SHAPE_ALIASES = {"11k": "11000", "02k": "02000", "6ko": "6konec"}


def piece_length(shape: str) -> float | None:
    """The chord length of a road piece of *shape* in metres, None when unknown."""
    shape = SHAPE_ALIASES.get(shape, shape)
    if shape in STRAIGHTS:
        return STRAIGHTS[shape]
    if shape in CURVES:
        degrees, radius = CURVES[shape]
        return 2 * radius * math.sin(math.radians(degrees) / 2)
    return None


def road_kind(name: str) -> str | None:
    """The road kind of a cluster tag's lower-cased *name*, None when not a road."""
    family = name.split("_", 1)[0]
    for prefix, kind in ROAD_FAMILIES:
        if family.startswith(prefix):
            return kind
    return None


def cluster_files(mission_dir: Path) -> list[Path]:
    """``<mission>/mapgroupcluster*.xml``, matched case-insensitively, sorted by name."""
    try:
        found = [
            entry for entry in mission_dir.iterdir()
            if entry.name.lower().startswith("mapgroupcluster")
            and entry.name.lower().endswith(".xml")
            and entry.is_file()
        ]
    except OSError:
        return []
    return sorted(found, key=lambda p: p.name.lower())[:MAX_CLUSTER_FILES]


@dataclass(frozen=True)
class ClusterLayers:
    """One parse of a mission's cluster files, encoded for the map editor."""

    #: `road_map.encode_roads` of the road pieces, or None when there are none.
    roads: bytes | None
    #: The gzipped `encode_forest` JSON, or None when there is no vegetation.
    forest: bytes | None


_EMPTY = ClusterLayers(None, None)
_cache: dict[Path, tuple[tuple, ClusterLayers]] = {}
_cache_lock = threading.Lock()


def _tag_point(tag: str) -> tuple[float, float] | None:
    """A tag's ``(x, z)``, or None when it is missing, malformed or far off any map."""
    match = _POS.search(tag)
    parts = match.group(1).split() if match else []
    if len(parts) != 3:
        return None
    try:
        x, z = float(parts[0]), float(parts[2])
    except ValueError:
        return None
    if not (math.isfinite(x) and math.isfinite(z)) or abs(x) > _FAR or abs(z) > _FAR:
        return None
    return x, z


def _piece(name: str, tag: str, point: tuple[float, float]) -> list[tuple[float, float]] | None:
    """A road piece's two ends: its chord centred on *point*, turned by its ``a``."""
    length = piece_length("".join(name.split("_")[1:]))
    match = _ANGLE.search(tag)
    if length is None or not match:
        return None
    try:
        angle = math.radians(float(match.group(1)))
    except ValueError:
        return None
    if not math.isfinite(angle):
        return None
    dx, dz = math.cos(angle) * length / 2, math.sin(angle) * length / 2
    x, z = point
    return [(x - dx, z - dz), (x + dx, z + dz)]


def snap_ends(pieces: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    """*pieces* with every end moved onto the first end within `_SNAP_METRES` of it.

    The first end seen in a spot stays where it is and later ones move onto
    it, so the result does not depend on anything but the order of *pieces*.
    A piece whose two ends land on the same point is dropped.
    """
    grid: dict[tuple[int, int], list[tuple[float, float]]] = {}

    def place(pt: tuple[float, float]) -> tuple[float, float]:
        gx, gz = int(pt[0] // _SNAP_METRES), int(pt[1] // _SNAP_METRES)
        for ox in (-1, 0, 1):
            for oz in (-1, 0, 1):
                for other in grid.get((gx + ox, gz + oz), ()):
                    if math.dist(pt, other) <= _SNAP_METRES:
                        return other
        grid.setdefault((gx, gz), []).append(pt)
        return pt

    out = []
    for a, b in pieces:
        a, b = place(a), place(b)
        if a != b:
            out.append([a, b])
    return out


def encode_forest(counts: dict[tuple[int, int], int], conifers: dict[tuple[int, int], int]) -> bytes:
    """The forest as the map editor reads it, gzipped JSON.

    ``{"cell": FOREST_CELL, "points": [x0, z0, …], "counts": [n0, …],
    "conifers": [c0, …]}``: each square's centre in game metres, sorted by x
    so a tile finds its first square with a binary search, how many trees
    stand in it, and how many of those are conifers (the map's tree icon).
    """
    points: list[int] = []
    numbers: list[int] = []
    needles: list[int] = []
    for cell, n in sorted(counts.items()):
        cx, cz = cell
        points.extend((cx * FOREST_CELL + FOREST_CELL // 2, cz * FOREST_CELL + FOREST_CELL // 2))
        numbers.append(n)
        needles.append(conifers.get(cell, 0))
    body = {"cell": FOREST_CELL, "points": points, "counts": numbers, "conifers": needles}
    return gzip.compress(json.dumps(body, separators=(",", ":")).encode(), mtime=0)


def _parse(files: list[Path]) -> ClusterLayers:
    """Read *files* into the two layers."""
    pieces: dict[str, list[list[tuple[float, float]]]] = {}
    counts: dict[tuple[int, int], int] = {}
    conifers: dict[tuple[int, int], int] = {}
    skipped = unknown = 0
    for path in files:
        try:
            if path.stat().st_size > MAX_CLUSTER_BYTES:
                log.warning("Not reading %s: over the %d byte limit", path, MAX_CLUSTER_BYTES)
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log.warning("Could not read %s: %s", path, exc)
            continue
        for tag in _GROUP.findall(text):
            point = _tag_point(tag)
            match = _NAME.search(tag)
            if point is None or not match:
                skipped += 1
                continue
            name = match.group(1).lower()
            kind = road_kind(name)
            if kind is not None:
                piece = _piece(name, tag, point)
                if piece is None:
                    unknown += 1
                else:
                    pieces.setdefault(kind, []).append(piece)
            elif not name.split("_", 1)[0].startswith(NOT_VEGETATION):
                cell = (math.floor(point[0] / FOREST_CELL), math.floor(point[1] / FOREST_CELL))
                counts[cell] = counts.get(cell, 0) + 1
                if name.startswith(CONIFERS):
                    conifers[cell] = conifers.get(cell, 0) + 1
    if skipped or unknown:
        log.warning(
            "Cluster files in %s: skipped %d tag(s) with no usable pos or name, "
            "%d road piece(s) of an unknown shape",
            files[0].parent, skipped, unknown,
        )
    snapped = {kind: snap_ends(list_) for kind, list_ in pieces.items()}
    has_roads = any(snapped.values())
    return ClusterLayers(
        roads=gzip.compress(encode_roads(snapped), mtime=0) if has_roads else None,
        forest=encode_forest(counts, conifers) if counts else None,
    )


def cluster_layers(mission_dir: Path) -> ClusterLayers:
    """The one cached parse of *mission_dir*'s cluster files.

    Keyed by every file's name, ``st_mtime_ns`` and size, so an edit or a new
    file reads them again.
    """
    files = cluster_files(mission_dir)
    try:
        key = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in files)
    except OSError:
        return _EMPTY
    if not key:
        return _EMPTY
    with _cache_lock:
        hit = _cache.get(mission_dir)
        if hit and hit[0] == key:
            return hit[1]
    result = _parse(files)
    with _cache_lock:
        _cache[mission_dir] = (key, result)
    return result


def mission_roads(terrain: str, mission_dir: Path | None) -> bytes | None:
    """The gzipped roads JSON from *mission_dir*'s cluster files, or None.

    None for a map off `registry.terrain_layer_allowed`, without a file read.
    """
    if mission_dir is None or not terrain_layer_allowed(terrain):
        return None
    return cluster_layers(mission_dir).roads


def mission_forest(terrain: str, mission_dir: Path | None) -> bytes | None:
    """The gzipped forest JSON from *mission_dir*'s cluster files, or None.

    None for a map off `registry.terrain_layer_allowed`, without a file read.
    """
    if mission_dir is None or not terrain_layer_allowed(terrain):
        return None
    return cluster_layers(mission_dir).forest


def has_cluster_files(terrain: str, mission_dir: Path | None) -> bool:
    """Whether *terrain* may use cluster layers and its mission has the files.

    A directory listing only, for `/map-info`; the parse waits for the layer.
    """
    return mission_dir is not None and terrain_layer_allowed(terrain) and bool(cluster_files(mission_dir))
