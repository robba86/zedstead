"""The terrain map: the ground's own shape, drawn from the admin's world file.

A map image is its author's work, so zsm ships none. The building map
(plans/69) gave every map something to place spawns on; this gives it a
coastline, hills and contour lines.

The heights come from the terrain's world file, ``<terrain>.wrp`` inside the
server's ``addons/worlds_<terrain>.pbo``: one height per grid point, the same
data the game's terrain is built from. This module only reads them and hands
them to the browser in a compact form (`encode_heights`); the map editor draws
the map on canvas tiles from them (``useTerrainLayer.js``): flat land and sea,
the coastline where the ground crosses 0 m, and contour lines. It is read at
runtime from the admin's own install; nothing of the map's author ships, the
rule plans/65 set for every mod. Nothing is fetched online.

Only the layouts measured on real world files are read: ``OPRW`` version 32
(DayZ 1.30's Chernarus and Livonia), version 29 (1.29's) and version 28 (Deer
Isle). Anything else is reported as no terrain, and the page falls back to the
building map.
Sakhal's world file is in an encrypted ``worlds_sakhal.ebo`` and is not read;
its heights come from its navmesh instead (`app.maps.navmesh_map`, plans/74),
and only for a world file that is encrypted (`heights_source`).

Only a terrain that `registry.world_file_allowed` names is read: an official
map, or a community map whose maker cleared its heights or roads. For any
other, `world_file`, `world_file_problem` and `heights_source` answer at once
without looking at a file (`WORLD_FILE_NOT_AVAILABLE`). A map whose maker gave
Zedstead a height grid (`registry.maker_heights_url`) takes its heights from that
grid instead (`app.maps.maker_heights`), and its world file only for roads.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import math
import os
import re
import struct
import threading
import time
from array import array
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.maps import lzo
from app.maps.registry import maker_heights_url, world_file_allowed, world_heights_allowed
from app.shared import paths
from app.shared.json_utils import atomic_write_bytes, atomic_write_text

log = logging.getLogger(__name__)

#: Bump when the encoding changes, so cached files are rebuilt.
FORMAT_VERSION = 1
#: The world-file layouts this reader knows: OPRW version -> where the grid
#: sizes start. v29 is DayZ 1.29's (Chernarus, Livonia); v28 is Deer Isle's
#: (2026 Q4), the same body with one header byte fewer. v32 is DayZ 1.30's
#: (Chernarus, Livonia): v29's header, then `_SIZED_FROM`'s one block.
_GRID_AT = {28: 12, 29: 13, 32: 13}
#: From this version on, one LZO block stands between the surface index and
#: the heights where v29 has two, after a uint32 that is its decompressed
#: size (1.7 MB on 1.30's Chernarus, 2.1 MB on Livonia).
_SIZED_FROM = 32
#: A bigger height grid is sampled down to this many points a side.
MAX_SIDE = 2048
#: `read_prefix`'s first read; the heights end at about 18 MB on Chernarus.
FIRST_READ = 32 << 20
#: The largest grid read, in cells a side (Deer Isle's heights are 4096).
MAX_GRID = 16384
#: Every LZO block in the layout is an array of one value per cell of the
#: layer grid or of the height grid, the float32 height being the widest, so
#: a block that decompresses past this many bytes a cell is not a world file.
_CELL_BYTES = 4
#: A quadtree node splits its square 4x4, so a tree over `MAX_GRID` (4^7 a
#: side) is at most 7 nodes deep; more than twice that is refused, well
#: before Python's recursion limit.
_MAX_TREE_DEPTH = 16
#: Heights further than this from sea level are not a terrain (Everest is
#: 8.8 km), and inside it `encode_heights`' int16 steps cannot overflow.
MAX_HEIGHT = 10_000.0
#: What reading a world file raises when the file is not one this reader can
#: use; `cached_build` turns each into "no terrain" and remembers it.
UNREADABLE = (OSError, ValueError, ArithmeticError, RecursionError)
#: How long a world file that could not be read answers "no terrain" without
#: being read again. A changed file (mtime or size) is read at once.
FAILURE_BACKOFF = 3600

_PBO_HEADER_BYTES = 1 << 20
_VERSION_ENTRY = 0x56657273  # "sreV", the PBO's version entry

# One build at a time, so at most one world file is in memory. A cache hit or
# a remembered failure does not wait for it.
_build_lock = threading.Lock()
# (cache file, its key) -> time.monotonic() of the failed build, in this process.
_failed: dict[tuple[str, str], float] = {}
# PBO -> ((mtime_ns, size), its .wrp entries): a mod search stats every PBO
# on the server, and reads only the headers that changed.
_wrp_index: dict[Path, tuple[tuple[int, int], list[tuple[str, int, int]]]] = {}
_index_lock = threading.Lock()
# A terrain name becomes a file name in the cache; anything else is refused.
_TERRAIN_NAME = re.compile(r"^[A-Za-z0-9_]+$")


@dataclass(frozen=True)
class WorldFile:
    """Where a terrain's ``.wrp`` sits inside its PBO."""

    pbo: Path
    name: str
    offset: int
    size: int


@dataclass(frozen=True)
class Heights:
    """A square height grid. Row 0 is the south edge, column 0 the west."""

    side: int
    cell: float  # metres between two grid points
    values: array  # array('f'), side * side


# ── finding the world file ─────────────────────────────────────────────


def _parse_header(head: bytes) -> list[tuple[str, int, int]] | None:
    """Entries of a PBO header, or None when *head* stops before the header does."""
    entries: list[tuple[str, int]] = []
    pos = 0
    while True:
        end = head.find(b"\0", pos)
        if end == -1 or end + 21 > len(head):
            return None
        name = head[pos:end].decode("utf-8", errors="replace")
        pos = end + 1
        packing, _orig, _reserved, _stamp, size = struct.unpack_from("<5I", head, pos)
        pos += 20
        if not name:
            if packing == _VERSION_ENTRY:
                # Header extensions: strings up to an empty one.
                while True:
                    nul = head.find(b"\0", pos)
                    if nul == -1:
                        return None
                    if nul == pos:
                        pos += 1
                        break
                    pos = nul + 1
                continue
            break
        entries.append((name, size))
    out = []
    offset = pos
    for name, size in entries:
        out.append((name, offset, size))
        offset += size
    return out


def _pbo_entries(pbo: Path) -> list[tuple[str, int, int]]:
    """``(name, offset, size)`` of every file in *pbo*, read from its header.

    Reads 64 KB first and more only for a header that needs it, since a mod
    search reads the header of every PBO on the server once.
    """
    want = 64 << 10
    with open(pbo, "rb") as f:
        while True:
            f.seek(0)
            head = f.read(want)
            entries = _parse_header(head)
            if entries is not None:
                return entries
            if len(head) < want or want >= _PBO_HEADER_BYTES:
                return []
            want *= 4


def _wrp_names(pbo: Path) -> list[tuple[str, int, int]]:
    """The ``.wrp`` entries of *pbo*, cached until the file changes."""
    try:
        st = pbo.stat()
    except OSError:
        return []
    stamp = (st.st_mtime_ns, st.st_size)
    with _index_lock:
        hit = _wrp_index.get(pbo)
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        found = [e for e in _pbo_entries(pbo) if e[0].lower().endswith(".wrp")]
    except OSError:
        found = []
    with _index_lock:
        _wrp_index[pbo] = (stamp, found)
    return found


def _candidates(server_dir: Path, terrain: str, mod_dirs: Sequence[Path] = ()):
    """PBOs that may hold *terrain*'s world file, the stock one first."""
    want = f"worlds_{terrain}.pbo".lower()
    try:
        yield from (p for p in (server_dir / "addons").iterdir() if p.name.lower() == want)
    except OSError:
        pass
    # A community map ships its world file in its own mod folder: the server's
    # copy, then the Workshop's of a map downloaded but not switched to.
    try:
        mods = sorted(d for d in server_dir.iterdir() if d.name.startswith("@") and d.is_dir())
    except OSError:
        mods = []
    for mod in [*mods, *mod_dirs]:
        try:
            addons = next((d for d in mod.iterdir() if d.name.lower() == "addons" and d.is_dir()), None)
            if addons is not None:
                yield from sorted(p for p in addons.iterdir() if p.suffix.lower() == ".pbo")
        except OSError:
            continue


def world_file(server_dir: Path, terrain: str, mod_dirs: Sequence[Path] = ()) -> WorldFile | None:
    """The terrain's world file, if the server has one this reader knows.

    Looks in ``<server_dir>/addons/worlds_<terrain>.pbo``, then in every
    installed mod's ``addons`` for a ``<terrain>.wrp``, then in *mod_dirs*'
    (`map_ops.downloaded_map_mod_dirs`). A world file that is compressed
    inside its PBO, or in an encrypted ``.ebo`` (Sakhal's), is not read.
    None for a terrain off `registry.world_file_allowed`, without a file read.
    """
    if not _TERRAIN_NAME.match(terrain or "") or not world_file_allowed(terrain):
        return None
    for pbo in _candidates(Path(server_dir), terrain, mod_dirs):
        for name, offset, size in _wrp_names(pbo):
            if not _is_world_of(name, terrain):
                continue
            try:
                with open(pbo, "rb") as f:
                    f.seek(offset)
                    head = f.read(8)
            except OSError as exc:
                log.warning("Could not read %s: %s", pbo, exc)
                return None
            version = _version(head)
            if version in _GRID_AT:
                return WorldFile(pbo, name, offset, size)
            log.info("%s in %s is OPRW version %s, a layout this reader does not know", name, pbo, version)
            return None
    return None


def _is_world_of(entry: str, terrain: str) -> bool:
    """Whether a PBO entry is ``<terrain>.wrp``, in any folder and any case."""
    return entry.replace("\\", "/").rsplit("/", 1)[-1].lower() == f"{terrain.lower()}.wrp"


#: Why a terrain has no world file this reader can use (`world_file_problem`).
WORLD_FILE_MISSING = "missing"
WORLD_FILE_ENCRYPTED = "encrypted"
WORLD_FILE_UNKNOWN_VERSION = "unknown_version"
#: A terrain off `registry.world_file_allowed`: its files are never read.
WORLD_FILE_NOT_AVAILABLE = "not_available"
#: An encrypted world file whose navmesh failed to build lately (`heights_source`).
NAVMESH_UNREADABLE = "navmesh_unreadable"


def world_file_problem(server_dir: Path, terrain: str, mod_dirs: Sequence[Path] = ()) -> str | None:
    """Why `world_file` finds nothing for *terrain*, for the map editor to say; None when it finds one.

    ``unknown_version``: a ``<terrain>.wrp`` is there, in a layout this reader
    has not measured. ``encrypted``: the server has ``worlds_<terrain>.ebo``
    and no readable copy; Sakhal's is in ``<server_dir>/sakhal/addons``.
    ``missing``: neither. ``not_available``: a terrain off
    `registry.world_file_allowed`, answered without a file read.
    """
    if not world_file_allowed(terrain):
        return WORLD_FILE_NOT_AVAILABLE
    if world_file(server_dir, terrain, mod_dirs) is not None:
        return None
    return _no_world_file(Path(server_dir), terrain, mod_dirs)


def _no_world_file(server_dir: Path, terrain: str, mod_dirs: Sequence[Path]) -> str:
    """`world_file_problem` once `world_file` has found nothing."""
    if not _TERRAIN_NAME.match(terrain or ""):
        return WORLD_FILE_MISSING
    for pbo in _candidates(server_dir, terrain, mod_dirs):
        if any(_is_world_of(name, terrain) for name, _, _ in _wrp_names(pbo)):
            return WORLD_FILE_UNKNOWN_VERSION
    if _has_encrypted_world(server_dir, terrain):
        return WORLD_FILE_ENCRYPTED
    return WORLD_FILE_MISSING


def world_is_encrypted(server_dir: Path, terrain: str) -> bool:
    """Whether the server ships *terrain*'s world file encrypted (Sakhal), for the Maps page.

    A folder scan, no PBO read. A terrain off `registry.world_file_allowed` answers False unread.
    """
    return world_file_allowed(terrain) and _has_encrypted_world(Path(server_dir), terrain)


def _has_encrypted_world(server_dir: Path, terrain: str) -> bool:
    """A ``worlds_<terrain>.ebo`` in the server's ``addons`` or one folder down (a DLC's or a mod's)."""
    want = f"worlds_{terrain}.ebo".lower()
    try:
        folders = [server_dir, *(d for d in server_dir.iterdir() if d.is_dir())]
    except OSError:
        return False
    for folder in folders:
        try:
            addons = next((d for d in folder.iterdir() if d.name.lower() == "addons" and d.is_dir()), None)
            if addons is not None and any(p.name.lower() == want for p in addons.iterdir()):
                return True
        except OSError:
            continue
    return False


#: Where a terrain's heights come from (`heights_source`).
SOURCE_WORLD = "world"
SOURCE_NAVMESH = "navmesh"
#: The height grid the map's maker gave Zedstead (`app.maps.maker_heights`).
SOURCE_MAKER = "maker"


@dataclass(frozen=True)
class HeightsSource:
    """Where a terrain's heights come from, and why not from its world file."""

    kind: str | None  # SOURCE_WORLD, SOURCE_NAVMESH, or None: no terrain
    file: WorldFile | None  # the world file, or the navmesh's entry
    problem: str | None  # `world_file_problem`'s answer


def heights_source(server_dir: Path, terrain: str, mod_dirs: Sequence[Path] = ()) -> HeightsSource:
    """The world file, or the navmesh when the world file is encrypted (plans/74 D1).

    A community map whose world file is a version this reader has not
    measured stays without terrain, even with a navmesh. A terrain off
    `registry.world_heights_allowed` has none, and no file is looked at. A
    source whose heights failed to build within `FAILURE_BACKOFF` is no
    source (`unknown_version`, `navmesh_unreadable`), so /map-info turns
    the Terrain switch off with the reason instead of offering a 404.
    A map with its maker's grid answers `SOURCE_MAKER` without a file read.
    """
    if maker_heights_url(terrain):
        return HeightsSource(SOURCE_MAKER, None, None)
    if not world_heights_allowed(terrain):
        return HeightsSource(None, None, WORLD_FILE_NOT_AVAILABLE)
    wf = world_file(server_dir, terrain, mod_dirs)
    if wf is not None:
        if _heights_failed_lately(wf, terrain):
            return HeightsSource(None, None, WORLD_FILE_UNKNOWN_VERSION)
        return HeightsSource(SOURCE_WORLD, wf, None)
    problem = _no_world_file(Path(server_dir), terrain, mod_dirs)
    if problem == WORLD_FILE_ENCRYPTED:
        from app.maps.navmesh_map import navmesh_file  # it reads this module's types

        nav = navmesh_file(server_dir, terrain, mod_dirs)
        if nav is not None:
            if _heights_failed_lately(nav, terrain):
                return HeightsSource(None, None, NAVMESH_UNREADABLE)
            return HeightsSource(SOURCE_NAVMESH, nav, problem)
    return HeightsSource(None, None, problem)


def _heights_failed_lately(wf: WorldFile, terrain: str) -> bool:
    """Whether `terrain_heights`' build from *wf* failed within `FAILURE_BACKOFF`."""
    keys = _build_keys(wf, terrain, "heights", FORMAT_VERSION)
    return keys is not None and _failed_lately(keys[3])


# ── reading the heights ────────────────────────────────────────────────


def _version(head: bytes) -> int | None:
    """The ``OPRW`` version of a world file, None when it is not one."""
    if len(head) < 8 or head[:4] != b"OPRW":
        return None
    return struct.unpack_from("<I", head, 4)[0]


def _skip_quadtree(data: bytes, p: int) -> int:
    """Skip a quadtree section: a flag byte, then a node or one 4-byte leaf."""
    flag = data[p]
    p += 1
    return _skip_node(data, p, 1) if flag else p + 4


def _skip_node(data: bytes, p: int, depth: int) -> int:
    # 16 children; a set bit is another node, a clear one a 4-byte leaf.
    if depth > _MAX_TREE_DEPTH:
        raise ValueError(f"a quadtree nests deeper than {_MAX_TREE_DEPTH} levels")
    mask = struct.unpack_from("<H", data, p)[0]
    p += 2
    for i in range(16):
        p = _skip_node(data, p, depth + 1) if mask & (1 << i) else p + 4
    return p


@dataclass(frozen=True)
class Grid:
    """A world file's two grids: the layer cells and the height points."""

    layer_x: int
    layer_y: int
    side: int  # height points a side
    world: float  # metres a side

    @property
    def block_limit(self) -> int:
        """The most an LZO block of this file can decompress to (`_CELL_BYTES`)."""
        return _CELL_BYTES * max(self.layer_x * self.layer_y, self.side * self.side)


def _skip_sized_block(data: bytes, p: int, grid: Grid) -> int:
    """Skip a uint32 size and the LZO block that decompresses to exactly that many bytes."""
    (size,) = struct.unpack_from("<I", data, p)
    if size > grid.block_limit:
        raise ValueError(f"a {size}-byte block, more than a grid of {grid.side} points a side holds")
    raw, end = lzo.decompress(data, p + 4, max_out=size)
    if len(raw) != size:
        raise ValueError(f"a block is {len(raw)} bytes, its size says {size}")
    return end


def heights_block(data: bytes) -> tuple[Grid, bytes, int]:
    """Walk an ``OPRW`` v28, v29 or v32 world file to the end of its heights.

    Returns the grids, the raw float32 heights and the offset just past them,
    where the rest of the file (`app.maps.road_map` reads on) starts. Raises
    `IndexError` or `lzo.LzoError` when *data* is too short, `ValueError`
    when it is not a layout in `_GRID_AT` or holds what no world file does
    (`lzo.LzoTooLarge` is one).
    """
    version = _version(data)
    grid_at = _GRID_AT.get(version)
    if grid_at is None:
        raise ValueError("not an OPRW world file this reader knows")
    # "0FNE" (from v29 on a byte we have no name for), a 0, then the grid sizes.
    _unknown, layer_x, layer_y, side_x, side_y = struct.unpack_from("<5I", data, grid_at)
    layer_cell = struct.unpack_from("<f", data, grid_at + 20)[0]
    world = layer_x * layer_cell
    if side_x != side_y or not (2 <= side_x <= MAX_GRID):
        raise ValueError(f"unexpected grid {side_x}x{side_y}")
    if not (1 <= layer_x <= MAX_GRID and 1 <= layer_y <= MAX_GRID and 0 < world < math.inf):
        raise ValueError(f"unexpected layer grid {layer_x}x{layer_y} of {layer_cell} m")
    grid = Grid(layer_x, layer_y, side_x, world)
    p = grid_at + 24
    p = _skip_quadtree(data, p)  # geography
    p = _skip_quadtree(data, p)  # sound map
    (peaks,) = struct.unpack_from("<I", data, p)
    p += 4 + 12 * peaks  # x, y, z each
    p = _skip_quadtree(data, p)  # surface (rvmat) index
    if version >= _SIZED_FROM:
        p = _skip_sized_block(data, p, grid)
    else:
        p = lzo.decompress(data, p, max_out=grid.block_limit)[1]  # clutter
        p = lzo.decompress(data, p, max_out=grid.block_limit)[1]  # unnamed bytes
    want = side_x * side_x * 4  # float32 heights, exactly
    raw, end = lzo.decompress(data, p, max_out=want)
    if len(raw) != want:
        raise ValueError(f"heights are {len(raw)} bytes, expected {want}")
    return grid, raw, end


def parse_heights(data: bytes) -> Heights:
    """The height grid from the start of an ``OPRW`` v28, v29 or v32 world file.

    *data* needs to run only as far as the heights; `read_heights` grows it
    until it does.
    """
    grid, raw, _end = heights_block(data)
    values = array("f")
    values.frombytes(raw)
    return Heights(side=grid.side, cell=grid.world / grid.side, values=values)


def read_prefix(wf: WorldFile, parse, what: str):
    """``parse(data)`` over as little of *wf* as it needs.

    Starts with `FIRST_READ` bytes and doubles while *parse* runs off the end
    of what was read (`IndexError`, `struct.error`, `lzo.LzoError`).
    """
    want = min(wf.size, FIRST_READ)
    with open(wf.pbo, "rb") as f:
        while True:
            f.seek(wf.offset)
            data = f.read(want)
            try:
                return parse(data)
            except (IndexError, struct.error, lzo.LzoError):
                if want >= wf.size:
                    raise ValueError(f"{wf.name}: the {what} could not be read") from None
                want = min(wf.size, want * 2)


def read_heights(wf: WorldFile) -> Heights:
    """Read just enough of *wf* to get its heights (about 18 MB on Chernarus)."""
    return read_prefix(wf, parse_heights, "heights")


# ── the browser's copy ─────────────────────────────────────────────────

#: Heights travel in half metres: plenty for a contour line, and with each
#: value stored as the step from its west neighbour, Chernarus' 2048x2048 grid
#: is 1.4 MB gzipped.
UNIT = 0.5
#: The sea floor is not drawn, so depths stop here (it also compresses).
SEA_FLOOR = -50.0
_MAGIC = b"DZTH"


def _sampled(h: Heights) -> Heights:
    if h.side <= MAX_SIDE:
        return h
    k = math.ceil(h.side / MAX_SIDE)
    side = h.side // k
    vals = array("f", (h.values[(y * k) * h.side + x * k] for y in range(side) for x in range(side)))
    return Heights(side=side, cell=h.cell * k, values=vals)


def _quantised(v: float) -> int:
    if not -MAX_HEIGHT <= v <= MAX_HEIGHT:  # NaN fails this too
        raise ValueError(f"a height of {v} m")
    q = round(max(v, SEA_FLOOR) / UNIT)
    # Rounding must not move a point across the coastline: below 0 m stays sea.
    if v < 0 and q >= 0:
        return -1
    return min(q, 32767)


def encode_heights(h: Heights) -> bytes:
    """The heights as the map editor reads them.

    ``DZTH``, then side (uint32), cell size in metres (float32) and the unit in
    metres (float32), all little-endian; then side x side int16, row 0 south.
    Each row's first value is a height in units, every other value the step
    from its west neighbour.
    """
    h = _sampled(h)
    n = h.side
    out = array("h")
    for r in range(n):
        row = [_quantised(v) for v in h.values[r * n:(r + 1) * n]]
        out.append(row[0])
        out.extend(b - a for a, b in zip(row, row[1:]))
    if out.itemsize != 2:
        raise ValueError("int16 is not 2 bytes here")
    if struct.pack("=h", 1) != struct.pack("<h", 1):
        out.byteswap()
    return _MAGIC + struct.pack("<Iff", n, h.cell, UNIT) + out.tobytes()


# ── the cached file ────────────────────────────────────────────────────


def _cache_files(terrain: str, kind: str, pbo: Path) -> tuple[Path, Path]:
    """The cached file and its key for *terrain*'s world file in *pbo*.

    Named after the PBO as well as the terrain: ``maps_dir()`` is shared by
    every instance, and two server dirs with the same map would otherwise
    overwrite each other's file and rebuild in turn.
    """
    digest = hashlib.sha256(os.fsencode(pbo)).hexdigest()[:12]
    stem = f"{terrain}.{digest}.{kind}"
    folder = paths.maps_dir() / "terrain"
    return folder / f"{stem}.gz", folder / f"{stem}.json"


def _is_cached(gz: Path, meta: Path, key: dict) -> bool:
    try:
        return gz.is_file() and json.loads(meta.read_text(encoding="utf-8")) == key
    except (OSError, ValueError):
        return False


def _failed_lately(failure: tuple[str, str]) -> bool:
    at = _failed.get(failure)
    return at is not None and time.monotonic() - at < FAILURE_BACKOFF


def _remember_failure(failure: tuple[str, str]) -> None:
    now = time.monotonic()
    for old in [k for k, at in _failed.items() if now - at >= FAILURE_BACKOFF]:
        _failed.pop(old, None)
    _failed[failure] = now


def _prune(terrain: str, kind: str, keep: Path) -> None:
    """Drop *terrain*'s cached *kind* files whose PBO is gone, and the old names."""
    folder = keep.parent
    # Until 2026 Q4 the cache was named after the terrain alone.
    stale = [folder / f"{terrain}.{kind}.gz", folder / f"{terrain}.{kind}.json", folder / f"{terrain}.json"]
    for meta in folder.glob(f"{terrain}.*.{kind}.json"):
        if meta == keep:
            continue
        try:
            pbo = json.loads(meta.read_text(encoding="utf-8")).get("pbo")
        except (OSError, ValueError, AttributeError):
            pbo = None
        if not (isinstance(pbo, str) and Path(pbo).is_file()):
            stale += [meta, meta.with_suffix(".gz")]
    for f in stale:
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass


def _build_keys(wf: WorldFile, terrain: str, kind: str, version: int):
    """`cached_build`'s cached file, its JSON key file, the key, and the failure
    memory's key for *wf*; None when the PBO cannot be stat'ed."""
    try:
        st = wf.pbo.stat()
    except OSError:
        return None
    # A world file is found whatever the case of the name asked for, so the
    # cache and the failure memory use one spelling: each other spelling was
    # a build of its own and a file that nothing pruned.
    gz, meta = _cache_files(terrain.lower(), kind, wf.pbo)
    key = {"pbo": str(wf.pbo), "mtime_ns": st.st_mtime_ns, "size": st.st_size, "format": version}
    return gz, meta, key, (str(gz), json.dumps(key, sort_keys=True))


def cached_build(wf: WorldFile | None, terrain: str, kind: str, version: int, build) -> Path | None:
    """``build(wf)``'s bytes for *terrain*, gzipped and kept until *wf*'s PBO changes.

    *wf* is the source: the world file (`world_file`), or the navmesh's entry
    (`heights_source`). Stored as
    ``<maps dir>/terrain/<terrain>.<pbo digest>.<kind>.gz`` beside a JSON key
    (the PBO's path, mtime and size, and *version*). ``None`` when *wf* is
    None, or *build* raises one of `UNREADABLE`: logged once, then answered
    from memory for `FAILURE_BACKOFF` while the PBO stays the same.
    """
    keys = _build_keys(wf, terrain, kind, version) if wf is not None else None
    if keys is None:
        return None
    gz, meta, key, failure = keys
    terrain = terrain.lower()
    if _is_cached(gz, meta, key):
        return gz
    if _failed_lately(failure):
        return None
    with _build_lock:
        # Built, or found unreadable, while this request waited.
        if _is_cached(gz, meta, key):
            return gz
        if _failed_lately(failure):
            return None
        try:
            body = build(wf)
        except UNREADABLE as exc:
            _remember_failure(failure)
            log.warning(
                "No %s for %s from %s: %s (not read again for %d min unless the file changes)",
                kind, terrain, wf.pbo, exc, FAILURE_BACKOFF // 60,
            )
            return None
        _failed.pop(failure, None)
        # The key goes last, so a key that matches always has its file.
        atomic_write_bytes(gz, gzip.compress(body, 6, mtime=0))
        atomic_write_text(meta, json.dumps(key))
        _prune(terrain, kind, meta)
        return gz


def terrain_heights(server_dir: Path, terrain: str, mod_dirs: Sequence[Path] = ()) -> Path | None:
    """The gzipped `encode_heights` file for *terrain*, built once per source.

    The source is `heights_source`'s: the world file, or the navmesh for a
    world file that is encrypted. ``None`` when there is neither. The first
    call takes a few seconds (about 10 from a navmesh); later ones return the
    cached file until the source or the encoding changes.
    """
    source = heights_source(server_dir, terrain, mod_dirs)
    if source.kind == SOURCE_MAKER:
        from app.maps.maker_heights import maker_heights

        return maker_heights(terrain)
    if source.kind != SOURCE_NAVMESH:
        return cached_build(
            source.file, terrain, "heights", FORMAT_VERSION, lambda wf: encode_heights(read_heights(wf))
        )
    from app.maps.navmesh_map import read_navmesh_heights
    from app.maps.registry import map_size

    # The browser lays the terrain over the world's bounds, so the grid spans
    # map_size (15360 on Sakhal), not tiles x 50 m (15350).
    world = map_size(terrain)
    return cached_build(
        source.file, terrain, "heights", FORMAT_VERSION,
        lambda nav: encode_heights(read_navmesh_heights(nav, world, MAX_SIDE)),
    )


# ── one height, for a write that needs Y ───────────────────────────────


def _decoded_row(data: bytes, side: int, row: int) -> list[int]:
    """Row *row* of an `encode_heights` body in units: a running sum of its steps."""
    start = 16 + row * side * 2
    steps = struct.unpack_from(f"<{side}h", data, start)
    out, total = [], 0
    for i, step in enumerate(steps):
        total = step if i == 0 else total + step
        out.append(total)
    return out


def height_at(gz: Path, x: float, z: float) -> float | None:
    """The ground's height in metres at world *x*, *z*, from an `encode_heights` file.

    Bilinear between the four grid points around the point, so it is as good as
    the grid: a few metres apart, half a metre a step, and the sea floor cut at
    `SEA_FLOOR`. Only the two rows it needs are decoded. ``None`` when the file
    cannot be read or the point is off the grid. The Map Editor's Object
    Spawner writes (`app/spawn_editor/object_spawner.py`) use it, because an
    object placed at Y 0 would sit under the ground.
    """
    try:
        data = gzip.decompress(gz.read_bytes())
    except (OSError, EOFError, gzip.BadGzipFile):
        return None
    if len(data) < 16 or data[:4] != _MAGIC:
        return None
    side, cell, unit = struct.unpack_from("<Iff", data, 4)
    if side < 2 or cell <= 0 or len(data) < 16 + side * side * 2:
        return None
    gx, gz_ = x / cell, z / cell
    if not (0 <= gx <= side - 1 and 0 <= gz_ <= side - 1):
        return None
    c0, r0 = min(int(gx), side - 2), min(int(gz_), side - 2)
    fx, fz = gx - c0, gz_ - r0
    south = _decoded_row(data, side, r0)
    north = _decoded_row(data, side, r0 + 1)
    low = south[c0] * (1 - fx) + south[c0 + 1] * fx
    high = north[c0] * (1 - fx) + north[c0 + 1] * fx
    return round((low * (1 - fz) + high * fz) * unit, 2)
