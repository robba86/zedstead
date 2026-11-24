"""The roads on the terrain map, read from the same world file as its heights.

A terrain's world file (`app.maps.terrain_map`) lists every road piece the
game places: a short line of two to four points in metres, and the model it
is built from. The model's name says what kind of road it is (``asf1_25.p3d``
is a 25 m piece of main road), so the map can colour them by kind. Pieces of
one kind that meet end to end are joined into one line, which keeps a dashed
dirt road's dashes running along it and the file small.

Measured on DayZ 1.29's Chernarus (51,959 pieces), Livonia (36,263) and Deer
Isle (26,988), 2026 Q4; on all three the road list ended exactly where its
stored size said, and so it did on 1.30's Chernarus, Livonia and Namalsk
(``OPRW`` v32). Livonia's models carry ``enoch`` in their names
(``asf1enoch``, ``mudenoch``); Deer Isle uses both sets. Like the heights, it
is read from the admin's own install and nothing of a map's author ships.
"""

from __future__ import annotations

import heapq
import json
import logging
import math
import struct
from collections import defaultdict, deque

from app.maps import lzo
from app.maps.registry import roads_allowed
from app.maps.terrain_map import _skip_quadtree, cached_build, heights_block, read_prefix, world_file

log = logging.getLogger(__name__)

#: Bump when the encoding changes, so cached files are rebuilt.
FORMAT_VERSION = 1

#: The kinds of road the map draws, in drawing order (the last on top).
ROAD_CLASSES = ("path", "dirt", "gravel", "airfield", "other", "minor", "secondary", "main")

#: A model's family (its name up to the first ``_`` or space, without
#: ``enoch``) -> the kind of road. A bridge takes the kind of the road that
#: meets it (`_bridges_carry_their_road`). Anything else in the road list is
#: drawn as "other": the list holds only road pieces, so it is still a road.
FAMILIES = {
    "asf1": "main",
    "asf2": "secondary",
    "asf3": "minor",
    "city": "minor",
    "grav": "gravel",
    "mud": "dirt",
    "kr": "dirt",
    "quarry": "dirt",
    "path": "path",
    "taxiway": "airfield",
    "runway": "airfield",
    "bridge": "bridge",
}
#: In the road list but not a road: damage decals and painted centre lines.
NOT_ROADS = frozenset({"decal", "centerline"})

#: How far past the terrain's edge a road point may lie, in terrain widths. A
#: road may run off the map (a pier, a road into the outer terrain); a piece
#: with a point a whole map away, or not a number, is not a road and is left
#: out, and the rest is drawn. Livonia's 1.29 world file has 352 such pieces
#: (secondary roads, every point between x 206 and 208.6 km on a 12.8 km
#: map), so refusing the file lost all its roads.
_OFF_MAP = 1.0

#: Piece ends closer than this many metres are the same point.
_SNAP = 0.5
#: A bridge's ends sit a little further from its road's: on Chernarus 10 of
#: the 25 bridges no road meets within `_SNAP` have one within 3 m. Most of
#: the rest (Deer Isle's piers and footbridges) have none within 50 m.
_BRIDGE_SNAP = 3.0
#: More distinct piece ends than this in one square of `_Ends` (a snap
#: distance a side) is a road list no map has, and is refused: finding what
#: meets an end measures the distance to every end in the nine squares around
#: it, so ends crowded a hair apart would make the joins quadratic again
#: (plans/68 NEW-8). A junction puts a handful in a square, and ends on
#: exactly the same point count once however many pieces share it. With
#: every square full, 40,000 bridge pieces take 3.6 s.
_CROWD = 32


def road_class(model: str) -> str | None:
    """The kind of road a road piece's model is, None when it is not a road."""
    name = model.replace("\\", "/").rsplit("/", 1)[-1].lower()
    family = name.split(".", 1)[0].split("_", 1)[0].split(" ", 1)[0].replace("enoch", "")
    if family in NOT_ROADS:
        return None
    return FAMILIES.get(family, "other")


def _cstr(data: bytes, p: int) -> int:
    """The offset just past the zero-terminated string at *p*."""
    end = data.find(b"\0", p)
    if end < 0:  # cut off by a short read: `read_prefix` reads more
        raise IndexError("a name runs past the end of what was read")
    return end + 1


def _count(data: bytes, p: int) -> tuple[int, int]:
    """A list's length at *p*, and the offset after it.

    Every entry takes at least a byte, so a length past the end of the file
    is a layout this reader does not know, not a short read.
    """
    (n,) = struct.unpack_from("<I", data, p)
    if n > len(data) - p:
        raise ValueError(f"a list of {n} at {p} is longer than the file")
    return n, p + 4


def parse_roads(data: bytes) -> dict[str, list[list[tuple[float, float]]]]:
    """Every road piece in a world file: kind -> pieces, each a list of (x, z).

    *data* needs to run past the road list; `read_prefix` grows it until it
    does. Raises `ValueError` when the list does not end where its stored
    size says, which is how a layout this reader does not know shows up. A
    piece with a point further off the map than `_OFF_MAP`, or not a number,
    is left out and counted in a warning.
    """
    grid, _raw, p = heights_block(data)
    count, p = _count(data, p)  # surface materials: two names each
    for _ in range(count):
        p = _cstr(data, _cstr(data, p))
    count, p = _count(data, p)  # model names
    for _ in range(count):
        p = _cstr(data, p)
    count, p = _count(data, p)  # class name, model, position, ids
    for _ in range(count):
        p = _cstr(data, _cstr(data, p)) + 20
    p = _skip_quadtree(data, p)
    p += 4  # the objects' size
    p = _skip_quadtree(data, p)
    p += 4  # the map info's size
    p = lzo.decompress(data, p, max_out=grid.block_limit)[1]
    p = lzo.decompress(data, p, max_out=grid.block_limit)[1]
    p += 4  # the highest object id
    (size,) = struct.unpack_from("<I", data, p)
    p += 4
    end = p + size
    low, high = -_OFF_MAP * grid.world, (1 + _OFF_MAP) * grid.world
    pieces: dict[str, list[list[tuple[float, float]]]] = defaultdict(list)
    dropped = 0
    for _cell in range(grid.layer_x * grid.layer_y):
        (parts,) = struct.unpack_from("<I", data, p)
        p += 4
        for _ in range(parts):
            (n,) = struct.unpack_from("<H", data, p)
            p += 2
            xyz = struct.unpack_from(f"<{3 * n}f", data, p)
            p += 13 * n + 8  # x, y, z and a flag byte per point; object id, 4 unnamed bytes
            model_at = p
            p = _cstr(data, p) + 48  # model, then its transform
            kind = road_class(data[model_at:p - 49].decode("latin-1"))
            if kind is not None and n >= 2:
                piece = [(xyz[i], xyz[i + 2]) for i in range(0, 3 * n, 3)]
                # NaN fails the comparison too.
                if all(low <= x <= high and low <= z <= high for x, z in piece):
                    pieces[kind].append(piece)
                else:
                    dropped += 1
    if p != end:
        raise ValueError(f"the road list ended at {p}, its size says {end}")
    if dropped:
        log.warning("%d road piece(s) off the %g m map or not a number left out", dropped, grid.world)
    return dict(pieces)


class _Ends:
    """Piece ends by position, found again within *snap* metres.

    A point is kept once however many pieces end on it, and the caller keeps
    what ends there in a dict keyed by the point `place` returns, so asking
    what meets a point is a lookup of the few points near it, never a pass
    over the pieces (plans/68 NEW-8). Points share a square when they round
    to the same multiple of *snap*; more than `_CROWD` in one is a
    `ValueError`.
    """

    def __init__(self, snap: float = _SNAP):
        self.snap = snap
        self._cells: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
        self._known: set[tuple[float, float]] = set()

    def square(self, pt: tuple[float, float]) -> tuple[int, int]:
        return round(pt[0] / self.snap), round(pt[1] / self.snap)

    def place(self, pt: tuple[float, float]) -> tuple[float, float]:
        """Remember *pt*; the point as a key for the caller's own dicts."""
        at = (pt[0], pt[1])
        if at not in self._known:
            cell = self._cells[self.square(at)]
            if len(cell) >= _CROWD:
                raise ValueError(f"more than {_CROWD} road ends in one {self.snap:g} m square at {at}")
            cell.append(at)
            self._known.add(at)
        return at

    def squares_around(self) -> set[tuple[int, int]]:
        """The squares a point must be in for `near` to find it from a point placed so far."""
        return {(kx + dx, kz + dz) for kx, kz in self._cells for dx in (-1, 0, 1) for dz in (-1, 0, 1)}

    def near(self, pt: tuple[float, float]):
        """The placed points within *snap* of *pt*, a list per square around it.

        Always the same nine squares in the same order, each list in the
        order its points were placed: `join_pieces` relies on that order.
        """
        kx, kz = self.square(pt)
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                cell = self._cells.get((kx + dx, kz + dz))
                if cell:
                    yield [q for q in cell if math.dist(q, pt) <= self.snap]


def _bridges_carry_their_road(pieces: dict[str, list]) -> dict[str, list]:
    """Each bridge piece moved to the kind of road that meets one of its ends.

    Within `_BRIDGE_SNAP` metres; the highest kind wins where two meet it. A
    bridge built of several pieces takes its road from the ends inwards; one
    that touches no road stays "other".

    The result is that of going over the bridges in list order, pass after
    pass until one moves nothing, each piece taking what meets it when it is
    reached, so a later piece of the same pass already sees it. The kinds and
    the order the pieces join them in follow from those passes and are kept,
    without making them: a piece is queued for the pass and place it would be
    reached at as soon as something meeting it has a kind (plans/68 NEW-8).
    """
    pieces = {kind: list(ps) for kind, ps in pieces.items()}
    bridges = pieces.pop("bridge", [])
    rank = {kind: i for i, kind in enumerate(ROAD_CLASSES)}
    ends = _Ends(_BRIDGE_SNAP)
    waiting: dict[tuple[float, float], list[int]] = defaultdict(list)  # a point -> bridges ending there
    for i, piece in enumerate(bridges):
        waiting[ends.place(piece[0])].append(i)
        waiting[ends.place(piece[-1])].append(i)
    best: dict[tuple[float, float], int] = {}  # a point -> the rank of the highest kind ending there
    reach = ends.squares_around()  # a road end anywhere else meets no bridge
    for kind, ps in pieces.items():
        if kind == "other":  # never carried onto a bridge
            continue
        for piece in ps:
            for pt in (piece[0], piece[-1]):
                if ends.square(pt) in reach:
                    at = ends.place(pt)
                    best[at] = max(best.get(at, -1), rank[kind])

    queue: list[tuple[int, int]] = []  # (pass, bridge), the order the passes reach them in
    queued = [False] * len(bridges)

    def meets(points, turn: int, after: int) -> None:
        """Queue the bridges ending at *points*, met by something given a kind in pass *turn*."""
        for q in points:
            for j in waiting.pop(q, ()):
                if not queued[j]:  # after it in this pass, or in the next one
                    queued[j] = True
                    heapq.heappush(queue, (turn if j > after else turn + 1, j))

    for at in best:  # the roads, there before the first pass
        for points in ends.near(at):
            meets(points, 1, -1)
    while queue:
        turn, i = heapq.heappop(queue)
        piece = bridges[i]
        near = [q for pt in (piece[0], piece[-1]) for points in ends.near(pt) for q in points]
        top = max(best.get(q, -1) for q in near)
        pieces.setdefault(ROAD_CLASSES[top], []).append(piece)
        for pt in (piece[0], piece[-1]):
            at = (pt[0], pt[1])
            best[at] = max(best.get(at, -1), top)
        meets(near, turn, i)
    left = [piece for i, piece in enumerate(bridges) if not queued[i]]
    if left:
        pieces.setdefault("other", []).extend(left)
    return pieces


def join_pieces(pieces: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    """Pieces that meet end to end, joined into longer lines.

    Where several meet (a junction), one carries on and the others start lines
    of their own; nothing is dropped and no piece is used twice. The one that
    carries on is the first unused end near the line's end, in `_Ends.near`'s
    order of squares and, within a square, of pieces.
    """
    ends = _Ends()
    at: dict[tuple[float, float], list[tuple[int, int]]] = defaultdict(list)  # a point -> (piece, end) there
    for i, piece in enumerate(pieces):
        at[ends.place(piece[0])].append((i, 0))
        at[ends.place(piece[-1])].append((i, 1))
    behind = dict.fromkeys(at, 0)  # a point -> how many of its ends are known to be used
    used = [False] * len(pieces)

    def take(pt):
        for points in ends.near(pt):
            first = None
            for q in points:
                there, k = at[q], behind[q]
                while k < len(there) and used[there[k][0]]:
                    k += 1
                behind[q] = k
                if k < len(there) and (first is None or there[k] < first):
                    first = there[k]
            if first is not None:
                i, end = first
                used[i] = True
                return pieces[i], end
        return None

    lines = []
    for i, piece in enumerate(pieces):
        if used[i]:
            continue
        used[i] = True
        line = deque(piece)
        while (hit := take(line[-1])) is not None:
            other, end = hit
            line.extend((other[::-1] if end else other)[1:])
        while (hit := take(line[0])) is not None:
            other, end = hit
            line.extendleft(reversed((other if end else other[::-1])[:-1]))
        lines.append(list(line))
    return lines


def encode_roads(pieces: dict[str, list[list[tuple[float, float]]]]) -> bytes:
    """The roads as the map editor reads them: JSON, ``{"roads": {kind: lines}}``.

    Each line is flat, ``[x0, z0, x1, z1, …]`` in metres to one decimal, with
    a point that rounds onto the one before it dropped.
    """
    pieces = _bridges_carry_their_road(pieces)
    out = {}
    for kind in ROAD_CLASSES:
        lines = []
        for line in join_pieces(pieces.get(kind, [])):
            flat: list[float] = []
            for x, z in line:
                pt = (round(x, 1), round(z, 1))
                if flat[-2:] != list(pt):
                    flat.extend(pt)
            if len(flat) >= 4:
                lines.append(flat)
        if lines:
            out[kind] = lines
    return json.dumps({"roads": out}, separators=(",", ":")).encode()


def terrain_roads(server_dir, terrain: str, mod_dirs=()):
    """The gzipped `encode_roads` file for *terrain*, built once per world file.

    ``None`` when the server has no world file this reader knows for it, or
    its road list cannot be read. *mod_dirs* as in `terrain_map.world_file`.
    ``None`` too for a map whose roads are not cleared
    (`registry.roads_allowed`), without a file read.
    """
    if not roads_allowed(terrain):
        return None
    return cached_build(
        world_file(server_dir, terrain, mod_dirs), terrain, "roads", FORMAT_VERSION,
        lambda wf: encode_roads(read_prefix(wf, parse_roads, "roads")),
    )
