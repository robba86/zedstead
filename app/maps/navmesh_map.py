"""A terrain's heights from its navmesh, for a map whose world file is encrypted (plans/74).

Sakhal's world file is in an encrypted ``worlds_sakhal.ebo``, which zsm does
not read (`app.maps.terrain_map`). Its navmesh, ``worlds_sakhal_nav.pbo``, is
not encrypted, and it covers the whole map, seabed included: the polygons the
game's AI walks on, with their corners in metres. Rasterised onto a height
grid, keeping the lowest surface where polygons overlap (a bridge, a
building's floors), it gives the coastline and contour lines the world file
would. On Chernarus, which has both, the two agree to 0.29 m in the median.
The navmesh has no roads (no area id or flag marks one), so a map drawn from
it has terrain only.

The layout is Recast/Detour's (``dtMeshHeader``, ``dtPoly``) inside a file
header and a tile frame of Enfusion's own, measured on DayZ 1.29's three stock
navmeshes and 1.30's four (Badlands' Nasdara is the fourth). Only the vertices and polygons are read; links, the BV tree and
what Enfusion adds are skipped. Like a world file, a navmesh is untrusted
input, since any mod can ship one: every count is checked before it is used,
and what no real navmesh holds is a `ValueError`. It is read at runtime from
the admin's own install, one tile at a time; nothing of it ships.
"""

from __future__ import annotations

import logging
import math
import struct
from array import array
from collections.abc import Sequence
from pathlib import Path

from app.maps.registry import terrain_layer_allowed
from app.maps.terrain_map import _TERRAIN_NAME, MAX_HEIGHT, MAX_SIDE, Heights, WorldFile, _pbo_entries

log = logging.getLogger(__name__)

#: The navmesh's entry in its PBO, stored uncompressed.
NAV_ENTRY = "navmesh.nm"
#: The most tiles a navmesh may have; Chernarus and Sakhal have 307x307.
MAX_TILES = 1024 * 1024
#: A tile's corners and polygons are read whole, so a tile is capped; the
#: largest stock tile is well under a megabyte.
MAX_TILE_BYTES = 16 << 20
#: The most the rasteriser does for one navmesh, per point of the grid: rows
#: walked, and points inside its triangles' boxes. A polygon may be as large
#: as the grid, so without a limit the build's time followed the polygon
#: count alone: 80 polygons in a 3 KB file took 16 s behind the build lock.
#: The stock navmeshes need 0.8 to 1.1 rows and 2.7 to 3.7 box points per
#: grid point (Livonia, Chernarus and Sakhal at 2048 a side, 2026 Q4).
MAX_ROWS_PER_POINT = 8
MAX_BOX_PER_POINT = 32

# The file header: "TESM", the version, the tile count, tiles along x and
# along z; the rest of its 52 bytes (tile size, the count again) is not used.
_HEADER = struct.Struct("<4s4I")
_HEADER_BYTES = 52
_MAGIC = b"TESM"
# 6 on DayZ 1.29, 7 on 1.30, 9 since the 1.30 update of 2026 Q4 (Sakhal,
# Badlands), with the same header, frames and tiles. A later version is read
# with the same layout: Bohemia raised the number twice without changing it,
# and the tile checks below refuse a layout that did change (ValueError).
_KNOWN_VERSIONS = frozenset({6, 7, 9})
_FIRST_VERSION = min(_KNOWN_VERSIONS)
# Each tile: three uint32 (a counter, the tile's length minus 16, 0), then
# Detour's data, then 4 bytes the last tile leaves out.
_PREFIX = struct.Struct("<3I")
_TRAILER = 4
# Detour's dtMeshHeader from its magic: polyCount and vertCount are the 7th
# and 8th fields. The vertices start at tile offset 116, after the header
# (112) and one uint32 we have no meaning for.
_TILE_HEADER = struct.Struct("<4s7I")
_TILE_MAGIC = b"VAND"  # "DNAV" read little-endian
_VERTS_AT = 116
_VERT = 12  # x (east), height, z (north): float32 each
# dtPoly: firstLink, verts[6], neis[6], flags, vertCount, areaAndType.
_POLY = struct.Struct("<I6H6HHBB")
_MAX_POLY_VERTS = 6
_OFFMESH = 1  # the polygon type (top two bits) of an off-mesh connection


def navmesh_file(server_dir: Path, terrain: str, mod_dirs: Sequence[Path] = ()) -> WorldFile | None:
    """The ``navmesh.nm`` entry of *terrain*'s ``worlds_<terrain>_nav.pbo``, if the server has one.

    Looks in ``<server_dir>/addons``, then one folder down (Sakhal's is in
    ``<server_dir>/sakhal/addons``, a mod's in ``@Mod/addons``), then in
    *mod_dirs*' ``addons``, as `terrain_map.world_file` does. None for a
    terrain off `registry.TERRAIN_LAYER_MAPS`, without a file read.
    """
    if not _TERRAIN_NAME.match(terrain or "") or not terrain_layer_allowed(terrain):
        return None
    want = f"worlds_{terrain}_nav.pbo".lower()
    for pbo in _nav_candidates(Path(server_dir), want, mod_dirs):
        try:
            entries = _pbo_entries(pbo)
        except OSError as exc:
            log.warning("Could not read %s: %s", pbo, exc)
            continue
        for name, offset, size in entries:
            if name.replace("\\", "/").rsplit("/", 1)[-1].lower() == NAV_ENTRY:
                return WorldFile(pbo, name, offset, size)
    return None


def _nav_candidates(server_dir: Path, want: str, mod_dirs: Sequence[Path]):
    try:
        folders = [server_dir, *sorted(d for d in server_dir.iterdir() if d.is_dir())]
    except OSError:
        folders = [server_dir]
    for folder in [*folders, *mod_dirs]:
        try:
            addons = next((d for d in folder.iterdir() if d.name.lower() == "addons" and d.is_dir()), None)
            if addons is not None:
                yield from sorted(p for p in addons.iterdir() if p.name.lower() == want)
        except OSError:
            continue


def read_navmesh_heights(entry: WorldFile, world_size: float, side: int = MAX_SIDE) -> Heights:
    """*side* x *side* heights over a world *world_size* metres a side, from a navmesh.

    Point (row, column) is at ``column * cell`` east and ``row * cell`` north,
    as in a world file's grid. Where polygons overlap the lowest wins, and a
    point no polygon covers takes its neighbours' heights (`_fill_holes`).
    """
    if not (2 <= side <= MAX_SIDE and 0 < world_size < math.inf):
        raise ValueError(f"a {side}-point grid over {world_size} m")
    cell = world_size / side
    grid = array("f", [math.inf]) * (side * side)
    left = [MAX_ROWS_PER_POINT * side * side, MAX_BOX_PER_POINT * side * side]
    skipped = 0
    for tile in _tiles(entry):
        if tile[12:16] != _TILE_MAGIC:
            skipped += 1
            continue
        _rasterise(tile, grid, side, cell, left)
    if skipped:
        log.warning("%s: %d navmesh tiles without a Detour header were left out", entry.pbo, skipped)
    _fill_holes(grid, side)
    return Heights(side=side, cell=cell, values=grid)


def _tiles(entry: WorldFile):
    """Each tile's bytes from its frame's first byte, read one tile at a time."""
    if entry.size < _HEADER_BYTES:
        raise ValueError("the navmesh is shorter than its header")
    with open(entry.pbo, "rb") as f:
        f.seek(entry.offset)
        head = f.read(_HEADER_BYTES)
        if len(head) < _HEADER_BYTES:
            raise ValueError("the navmesh is cut short")
        magic, version, count, along_x, along_z = _HEADER.unpack_from(head)
        if magic != _MAGIC or version < _FIRST_VERSION:
            raise ValueError("not a navmesh this reader knows")
        if version not in _KNOWN_VERSIONS:
            log.info("%s: navmesh version %d is newer than this reader knows; read with version %d's layout",
                     entry.pbo, version, max(_KNOWN_VERSIONS))
        if not (0 < count <= MAX_TILES and count == along_x * along_z):
            raise ValueError(f"{count} tiles in a {along_x}x{along_z} navmesh")
        table_end = _HEADER_BYTES + 4 * count
        if table_end > entry.size:
            raise ValueError("the navmesh's tile table runs past its end")
        table = array("I")
        if table.itemsize != 4:
            raise ValueError("uint32 is not 4 bytes here")
        table.frombytes(f.read(4 * count))
        if len(table) != count:
            raise ValueError("the navmesh is cut short")
        if struct.pack("=I", 1) != struct.pack("<I", 1):
            table.byteswap()
        # Offsets from here on are from the end of the table.
        size = entry.size - table_end
        start = 0
        for i in range(count):
            prefix = f.read(_PREFIX.size)
            if len(prefix) < _PREFIX.size:
                raise ValueError(f"tile {i} of the navmesh is cut short")
            length = _PREFIX.unpack(prefix)[1] + 16
            # The trailer is the only part the last tile may leave out.
            if length > MAX_TILE_BYTES or start + length - _TRAILER > size:
                raise ValueError(f"tile {i} of the navmesh runs past its end")
            # Each entry is where its tile ends; Sakhal's last one is 0.
            if table[i] and table[i] != start + length:
                raise ValueError(f"tile {i}'s length disagrees with the navmesh's tile table")
            body = f.read(length - _PREFIX.size - _TRAILER)
            if len(body) < length - _PREFIX.size - _TRAILER:
                raise ValueError(f"tile {i} of the navmesh is cut short")
            f.read(_TRAILER)
            yield prefix + body
            start += length


def _rasterise(tile: bytes, grid: array, side: int, cell: float, left: list[int]) -> None:
    """Draw a tile's ground polygons into *grid*, keeping the lowest height at each point.

    *left* is what remains of the navmesh's rows and box points
    (`MAX_ROWS_PER_POINT`, `MAX_BOX_PER_POINT`); each triangle drawn takes its own.
    """
    if len(tile) < _VERTS_AT:
        raise ValueError("a navmesh tile shorter than its header")
    _magic, _version, _x, _y, _layer, _user, poly_count, vert_count = _TILE_HEADER.unpack_from(tile, 12)
    polys_at = _VERTS_AT + vert_count * _VERT
    if polys_at + poly_count * _POLY.size > len(tile):
        raise ValueError(f"a navmesh tile's {vert_count} corners and {poly_count} polygons need more than its length")
    coords = array("f")
    coords.frombytes(tile[_VERTS_AT:polys_at])
    if struct.pack("=f", 1.0) != struct.pack("<f", 1.0):
        coords.byteswap()
    xs = [v / cell for v in coords[0::3]]
    hs = coords[1::3].tolist()
    zs = [v / cell for v in coords[2::3]]
    for v in (*xs, *zs):
        if not -math.inf < v < math.inf:  # NaN fails this too
            raise ValueError("a navmesh corner that is not a number")
    for h in hs:
        if not -MAX_HEIGHT <= h <= MAX_HEIGHT:
            raise ValueError(f"a navmesh height of {h} m")
    top = side - 1
    for poly in _POLY.iter_unpack(tile[polys_at:polys_at + poly_count * _POLY.size]):
        n = poly[14]
        if n > _MAX_POLY_VERTS:
            raise ValueError(f"a navmesh polygon with {n} corners")
        corners = poly[1:1 + n]
        if any(c >= vert_count for c in corners):
            raise ValueError("a navmesh polygon names a corner the tile does not have")
        if poly[15] >> 6 == _OFFMESH or n < 3:
            continue
        # A fan from the first corner; each triangle is a plane over its points.
        a = corners[0]
        ax, az, ah = xs[a], zs[a], hs[a]
        for k in range(1, n - 1):
            b, c = corners[k], corners[k + 1]
            bx, bz, cx, cz = xs[b], zs[b], xs[c], zs[c]
            c0 = math.ceil(max(min(ax, bx, cx), 0.0))
            c1 = math.floor(min(max(ax, bx, cx), top))
            if c0 > c1:
                continue
            r0 = math.ceil(max(min(az, bz, cz), 0.0))
            r1 = math.floor(min(max(az, bz, cz), top))
            if r0 > r1:
                continue
            ux, uz, vx, vz = bx - ax, bz - az, cx - ax, cz - az
            area = ux * vz - vx * uz
            if area == 0:
                continue
            rows = r1 - r0 + 1
            left[0] -= rows
            left[1] -= rows * (c1 - c0 + 1)
            if left[0] < 0 or left[1] < 0:
                raise ValueError("a navmesh whose polygons cover far more ground than a map has")
            uh, vh = hs[b] - ah, hs[c] - ah
            # Height at (x, z): ah + dx * (x - ax) + dz * (z - az).
            dx = (uh * vz - vh * uz) / area
            dz = (ux * vh - vx * uh) / area
            # The three edges as a * col + b * row + c >= 0 inside, scaled by
            # the area's sign; a point on an edge counts for both triangles.
            s = 1.0 if area > 0 else -1.0
            edges = (
                (s * -uz, s * ux, s * (uz * ax - ux * az)),
                (s * -(cz - bz), s * (cx - bx), s * ((cz - bz) * bx - (cx - bx) * bz)),
                (s * vz, s * -vx, s * (vx * cz - vz * cx)),
            )
            eps = 1e-9 * abs(area)
            for r in range(r0, r1 + 1):
                lo, hi = c0, c1
                for ea, eb, ec in edges:
                    rest = eb * r + ec
                    if ea > 0:
                        lo = max(lo, math.ceil((-rest - eps) / ea))
                    elif ea < 0:
                        hi = min(hi, math.floor((-rest - eps) / ea))
                    elif rest < -eps:
                        hi = lo - 1
                if lo > hi:
                    continue
                base = r * side
                h0 = ah + dz * (r - az) - dx * ax
                for col in range(lo, hi + 1):
                    h = h0 + dx * col
                    if h < grid[base + col]:
                        grid[base + col] = h


def _fill_holes(grid: array, side: int) -> None:
    """Give each point no polygon covered the mean of its covered neighbours.

    In rings, from the covered points outwards: each ring takes only the
    rings before it, so a hole fills the same whichever way it is walked.
    About 1% of a stock navmesh's points are holes, specks under trees and
    buildings, plus the strip past the last tile.
    """
    inf = math.inf
    holes = [i for i, v in enumerate(grid) if v == inf]
    if len(holes) == len(grid):
        raise ValueError("the navmesh covers no ground")
    known = bytearray(b"\x01") * len(grid)
    for i in holes:
        known[i] = 0

    def neighbours(i: int):
        r, col = divmod(i, side)
        if col > 0:
            yield i - 1
        if col < side - 1:
            yield i + 1
        if r > 0:
            yield i - side
        if r < side - 1:
            yield i + side

    ring = [i for i in holes if any(known[j] for j in neighbours(i))]
    while ring:
        values = []
        for i in ring:
            near = [grid[j] for j in neighbours(i) if known[j]]
            values.append(sum(near) / len(near))
        for i, v in zip(ring, values):
            grid[i] = v
            known[i] = 1
        nxt = {j for i in ring for j in neighbours(i) if not known[j]}
        ring = sorted(nxt)
