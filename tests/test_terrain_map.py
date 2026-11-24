"""The terrain map (plans/70): heights from a world file, encoded for the browser.

Every world file here is built by `world_pbo`: invented heights in the layouts
measured on DayZ 1.30's ``.wrp`` (OPRW v32), 1.29's (v29) and Deer Isle's
(v28). Never copy a real world file into a test; a community map's is its
author's (plans/65).
"""

from __future__ import annotations

import gzip
import math
import os
import struct
import threading
from pathlib import Path

import pytest
from array import array

from app.maps import lzo, terrain_map
from app.maps.terrain_map import Heights, encode_heights, heights_source, parse_heights, terrain_heights, world_file

# The readers only open a terrain on registry.TERRAIN_LAYER_MAPS; these tests
# build files for invented terrains (tests/conftest.py::INVENTED_TERRAINS).
pytestmark = pytest.mark.usefixtures('invented_terrains_allowed')


# ── builders ───────────────────────────────────────────────────────────


def lzo_literals(data: bytes) -> bytes:
    """An LZO1X stream holding *data* as literals only, plus the end marker."""
    n = len(data)
    if n <= 238:
        assert n >= 4
        head = bytes([17 + n])
    else:
        rest = n - 18
        zeros = (rest - 1) // 255
        head = b"\0" + b"\0" * zeros + bytes([rest - 255 * zeros])
    return head + data + b"\x11\x00\x00"


LEAF_TREE = b"\0" + b"\0\0\0\0"  # a quadtree that is one leaf


def road_section(layer: int, pieces: list[tuple[str, list[tuple[float, float]]]]) -> bytes:
    """What follows the heights, up to the end of the road list.

    One name in each list before the roads; every road piece goes into the
    first of the *layer* x *layer* cells, at an invented height of 7 m.
    """
    parts = b""
    for model, points in pieces:
        parts += struct.pack("<H", len(points))
        parts += b"".join(struct.pack("<3f", x, 7.0, z) for x, z in points)
        parts += bytes(len(points)) + struct.pack("<I", 1) + bytes(4)  # flags, object id, unnamed
        parts += model.encode() + b"\0" + struct.pack("<12f", *[0.0] * 12)
    net = struct.pack("<I", len(pieces)) + parts + struct.pack("<I", 0) * (layer * layer - 1)
    return (
        struct.pack("<I", 1) + b"data\\a.rvmat\0\0"  # surface materials
        + struct.pack("<I", 1) + b"data\\a.p3d\0"  # models
        + struct.pack("<I", 1) + b"Land_A\0data\\a.p3d\0" + bytes(20)  # classed models
        + LEAF_TREE + struct.pack("<I", 0) + LEAF_TREE + struct.pack("<I", 0)
        + lzo_literals(bytes(8)) + lzo_literals(bytes(8))
        + struct.pack("<I", 99) + struct.pack("<I", len(net)) + net
    )


def sized_block(payload: bytes, size: int | None = None) -> bytes:
    """v32's one block before the heights: its decompressed *size*, then the LZO stream."""
    return struct.pack("<I", len(payload) if size is None else size) + lzo_literals(payload)


def wrp_bytes(
    heights: list[list[float]], cell: float = 10.0, version: int = 29, roads=None,
    geography: bytes = LEAF_TREE, clutter: bytes | None = None,
) -> bytes:
    """A world file whose height grid is *heights* (row 0 = south).

    *roads*, ``[(model, [(x, z), …]), …]``, adds the road list after them.
    *geography* and *clutter* replace the first quadtree and LZO block (from
    v32 on, the one `sized_block`).
    """
    side = len(heights)
    leaf_tree = LEAF_TREE
    flat = [v for row in heights for v in row]
    if version >= 32:
        before_heights = clutter if clutter is not None else sized_block(b"\0" * (side * side))
    else:
        before_heights = (
            (clutter if clutter is not None else lzo_literals(b"\0" * (side * side)))
            + lzo_literals(b"\0" * (side * side))  # unnamed bytes
        )
    return (
        b"OPRW" + struct.pack("<I", version) + b"0FNE" + (b"\x01" if version >= 29 else b"")
        + struct.pack("<5I", 0, side, side, side, side) + struct.pack("<f", cell)
        + geography + leaf_tree  # geography, sound map
        + struct.pack("<I", 1) + struct.pack("<3f", 1.0, 2.0, 3.0)  # one peak
        + leaf_tree  # surface index
        + before_heights
        + lzo_literals(struct.pack(f"<{side * side}f", *flat))
        + (road_section(side, roads) if roads is not None else b"")
        + b"rest of the world file"
    )


def pbo_bytes(files: dict[str, bytes]) -> bytes:
    """A PBO with a version entry, *files*, and no signature."""
    head = b"\0" + struct.pack("<5I", 0x56657273, 0, 0, 0, 0) + b"prefix\0worlds\\test\0\0"
    for name, body in files.items():
        head += name.encode() + b"\0" + struct.pack("<5I", 0, 0, 0, 0, len(body))
    head += b"\0" + struct.pack("<5I", 0, 0, 0, 0, 0)
    return head + b"".join(files.values())


def world_pbo(server_dir: Path, terrain: str, heights, **kw) -> Path:
    addons = server_dir / "addons"
    addons.mkdir(parents=True, exist_ok=True)
    pbo = addons / f"worlds_{terrain}.pbo"
    pbo.write_bytes(pbo_bytes({
        "config.bin": b"not a world file",
        f"{terrain}.wrp": wrp_bytes(heights, **kw),
    }))
    return pbo


def coast(side: int = 8) -> list[list[float]]:
    """Sea in the west half, land rising to the east."""
    return [[-10.0 if x < side // 2 else 20.0 * x for x in range(side)] for _ in range(side)]


def decode(path: Path) -> tuple[int, float, float, list[list[int]]]:
    """What useTerrainLayer.js's decode does: side, cell, unit, rows south first."""
    data = gzip.decompress(path.read_bytes())
    assert data[:4] == b"DZTH"
    side, cell, unit = struct.unpack_from("<Iff", data, 4)
    steps = struct.unpack_from(f"<{side * side}h", data, 16)
    rows = []
    for r in range(side):
        h, row = 0, []
        for step in steps[r * side:(r + 1) * side]:
            h += step
            row.append(h)
        rows.append(row)
    return side, cell, unit, rows


@pytest.fixture(autouse=True)
def _maps_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(terrain_map.paths, "maps_dir", lambda: tmp_path / "maps")


# ── LZO ────────────────────────────────────────────────────────────────


def lzo_run(n: int) -> bytes:
    """An LZO1X stream of a little over *n* bytes of ``A``, from about n / 255 bytes.

    One zero byte of input per 255 bytes of output, the run that expands a
    world file's block about 254 times (plans/68 NEW-3).
    """
    zeros = n // 255
    return bytes([18, 0x41, 0x20]) + b"\0" * zeros + bytes([0xFF, 0, 0]) + b"\x11\x00\x00"


def nested_tree(depth: int) -> bytes:
    """A quadtree section whose first child nests *depth* nodes deep."""
    return b"\x01" + struct.pack("<H", 1) * (depth - 1) + struct.pack("<H", 0) + bytes(64) + bytes(60) * (depth - 1)


@pytest.mark.parametrize("n", [4, 200, 238, 239, 300, 70_000])
def test_lzo_reads_a_literal_run_of_any_length(n):
    body = bytes(i % 251 for i in range(n))
    stream = b"junk" + lzo_literals(body) + b"next"
    out, end = lzo.decompress(stream, 4, max_out=n)
    assert out == body
    assert stream[end:] == b"next"


def test_lzo_copies_a_match_and_an_overlapping_one():
    assert lzo.decompress(bytes([21]) + b"abcd" + bytes([108, 0, 17, 0, 0]), max_out=8)[0] == b"abcdabcd"
    assert lzo.decompress(bytes([21]) + b"abcd" + bytes([96, 0, 17, 0, 0]), max_out=8)[0] == b"abcddddd"


@pytest.mark.parametrize("distance", range(1, 8))
def test_lzo_repeats_an_overlapping_match_as_a_byte_by_byte_copy_would(distance):
    # A match of 8 bytes reaching `distance` back into "abcdefgh".
    stream = bytes([25]) + b"abcdefgh" + bytes([224 + ((distance - 1) << 2), 0]) + b"\x11\x00\x00"
    expected = bytearray(b"abcdefgh")
    for _ in range(8):
        expected.append(expected[-distance])
    assert lzo.decompress(stream, max_out=16)[0] == expected


def test_lzo_says_so_when_a_stream_is_cut_short():
    with pytest.raises(lzo.LzoError):
        lzo.decompress(lzo_literals(b"abcdefgh")[:-3], max_out=8)


def test_lzo_refuses_a_stream_that_outgrows_its_array_before_writing_it():
    """plans/68 NEW-3: 40 KB of stream is 10 MB of output; the bound stops it first."""
    import tracemalloc

    stream = lzo_run(10_000_000)
    assert len(lzo.decompress(stream, max_out=11_000_000)[0]) > 10_000_000
    tracemalloc.start()
    try:
        with pytest.raises(lzo.LzoTooLarge) as caught:
            lzo.decompress(stream, max_out=1 << 20)
        _now, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1 << 20
    # Reading more of the file would not help, so it is not a short read.
    assert not isinstance(caught.value, lzo.LzoError)


# ── reading ────────────────────────────────────────────────────────────


def test_heights_come_back_south_row_first():
    grid = [[float(10 * y + x) for x in range(4)] for y in range(4)]
    h = parse_heights(wrp_bytes(grid, cell=25.0))
    assert h.side == 4
    assert h.cell == 25.0
    assert list(h.values) == [v for row in grid for v in row]


def test_a_v28_world_file_has_one_header_byte_fewer():
    """Deer Isle's layout: the same body, the grid sizes one byte earlier."""
    grid = [[float(10 * y + x) for x in range(4)] for y in range(4)]
    h = parse_heights(wrp_bytes(grid, cell=25.0, version=28))
    assert (h.side, h.cell) == (4, 25.0)
    assert list(h.values) == [v for row in grid for v in row]


def test_a_v32_world_file_has_one_sized_block_before_the_heights():
    """DayZ 1.30's layout: v29's header, then one block after its size where v29 has two."""
    grid = [[float(10 * y + x) for x in range(4)] for y in range(4)]
    h = parse_heights(wrp_bytes(grid, cell=25.0, version=32))
    assert (h.side, h.cell) == (4, 25.0)
    assert list(h.values) == [v for row in grid for v in row]


def test_the_v29_and_v32_bodies_are_not_read_for_each_other():
    v29, v32 = wrp_bytes(coast(), version=29), wrp_bytes(coast(), version=32)
    with pytest.raises(ValueError):
        parse_heights(v29[:4] + struct.pack("<I", 32) + v29[8:])
    with pytest.raises(ValueError):
        parse_heights(v32[:4] + struct.pack("<I", 29) + v32[8:])


@pytest.mark.parametrize("size, match", [(65, "64 bytes, its size says 65"), (63, "more than 63 bytes")])
def test_a_v32_block_whose_size_is_wrong_is_refused(size, match):
    with pytest.raises(ValueError, match=match):
        parse_heights(wrp_bytes(coast(), version=32, clutter=sized_block(b"\0" * 64, size)))


def test_a_v32_block_larger_than_its_grid_is_refused_before_it_is_read(monkeypatch):
    """An 8x8 grid's blocks hold at most 256 bytes, so the size alone refuses 257."""
    data = wrp_bytes(coast(), version=32, clutter=sized_block(b"\0" * 64, 257))

    def no_read(*_args, **_kw):
        raise AssertionError("the block was decompressed")

    monkeypatch.setattr(lzo, "decompress", no_read)
    with pytest.raises(ValueError, match="257-byte block, more than a grid of 8 points a side"):
        parse_heights(data)


@pytest.mark.parametrize("version", [27, 30, 31, 33])
def test_another_world_file_version_is_refused(version):
    with pytest.raises(ValueError):
        parse_heights(wrp_bytes(coast(), version=version))


def test_a_block_larger_than_its_grid_gives_no_terrain_after_one_read(tmp_path, monkeypatch, caplog):
    """plans/68 NEW-3: an 8x8 grid's blocks hold at most 4 bytes a cell, so 10 KB is refused."""
    world_pbo(tmp_path, "testmap", coast(), clutter=lzo_run(10_000))
    reads = []
    real = terrain_map.parse_heights

    def counting(data):
        reads.append(len(data))
        return real(data)

    monkeypatch.setattr(terrain_map, "parse_heights", counting)
    assert terrain_heights(tmp_path, "testmap") is None
    assert len(reads) == 1
    assert "No heights for testmap" in caplog.text
    assert "decompresses to more than 256 bytes" in caplog.text


def test_a_block_as_large_as_its_grid_says_is_read(tmp_path):
    world_pbo(tmp_path, "testmap", coast(), clutter=lzo_literals(b"\1" * 256))
    assert terrain_heights(tmp_path, "testmap") is not None


def test_a_quadtree_nested_deeper_than_any_grid_needs_is_refused():
    """plans/68 NB-1: 5,000 levels ran Python out of recursion; 16 is the limit, 7 the deepest real tree."""
    assert parse_heights(wrp_bytes(coast(), geography=nested_tree(16))).side == 8
    with pytest.raises(ValueError, match="nests deeper"):
        parse_heights(wrp_bytes(coast(), geography=nested_tree(17)))
    with pytest.raises(ValueError, match="nests deeper"):
        parse_heights(wrp_bytes(coast(), geography=nested_tree(5000)))


@pytest.mark.parametrize("cell", [math.nan, math.inf, -10.0])
def test_a_layer_cell_that_is_not_a_size_is_refused(cell):
    with pytest.raises(ValueError, match="layer grid"):
        parse_heights(wrp_bytes(coast(), cell=cell))


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan, 20_000.0])
def test_a_height_no_terrain_has_gives_no_terrain(tmp_path, value):
    """plans/68 NB-1: an infinite height raised OverflowError, so the route answered 500."""
    grid = coast()
    grid[3][4] = value  # beside the sea, where a step from -10 m is largest
    world_pbo(tmp_path, "testmap", grid)
    assert terrain_heights(tmp_path, "testmap") is None


def test_the_world_file_is_found_by_name_inside_the_pbo(tmp_path):
    pbo = world_pbo(tmp_path, "testmap", coast())
    wf = world_file(tmp_path, "testmap")
    assert wf.pbo == pbo
    assert wf.name == "testmap.wrp"
    assert pbo.read_bytes()[wf.offset:wf.offset + 4] == b"OPRW"


def test_no_world_file_is_none(tmp_path):
    assert world_file(tmp_path, "testmap") is None
    world_pbo(tmp_path, "testmap", coast(), version=30)
    assert world_file(tmp_path, "testmap") is None
    assert world_file(tmp_path, "../testmap") is None


def test_a_community_maps_world_file_is_found_in_its_mod_folder(tmp_path):
    """Invented mod and terrain names: a map mod ships its own world file."""
    addons = tmp_path / "@Some Map" / "Addons"
    addons.mkdir(parents=True)
    pbo = addons / "somemap_terrain.pbo"
    pbo.write_bytes(pbo_bytes({"somemap\\SomeMap.wrp": wrp_bytes(coast())}))
    (tmp_path / "@Other").mkdir()
    wf = world_file(tmp_path, "somemap")
    assert wf.pbo == pbo
    assert wf.name == "somemap\\SomeMap.wrp"


def test_the_stock_world_file_comes_before_a_mods(tmp_path):
    stock = world_pbo(tmp_path, "testmap", coast())
    addons = tmp_path / "@Copy" / "addons"
    addons.mkdir(parents=True)
    (addons / "copy.pbo").write_bytes(pbo_bytes({"testmap.wrp": wrp_bytes(coast())}))
    assert world_file(tmp_path, "testmap").pbo == stock


def test_a_downloaded_maps_world_file_is_found_in_its_workshop_folder(tmp_path):
    """The Maps page's Download only leaves the map mod in the Workshop cache."""
    server = tmp_path / "server"
    server.mkdir()
    item = tmp_path / "workshop" / "1001"
    (item / "addons").mkdir(parents=True)
    pbo = item / "addons" / "somemap.pbo"
    pbo.write_bytes(pbo_bytes({"somemap.wrp": wrp_bytes(coast())}))
    never_downloaded = tmp_path / "workshop" / "1002"
    assert world_file(server, "somemap") is None
    assert world_file(server, "somemap", [never_downloaded, item]).pbo == pbo


def test_the_servers_copy_of_a_map_mod_comes_before_the_workshops(tmp_path):
    copy = tmp_path / "@Some Map" / "addons"
    copy.mkdir(parents=True)
    (copy / "somemap.pbo").write_bytes(pbo_bytes({"somemap.wrp": wrp_bytes(coast())}))
    item = tmp_path / "workshop" / "1001" / "addons"
    item.mkdir(parents=True)
    (item / "somemap.pbo").write_bytes(pbo_bytes({"somemap.wrp": wrp_bytes(coast())}))
    assert world_file(tmp_path, "somemap", [item.parent]).pbo == copy / "somemap.pbo"


def test_the_map_editor_is_told_why_there_is_no_terrain(tmp_path):
    """Invented files. Sakhal's world file is an encrypted .ebo in its DLC folder."""
    from app.maps.terrain_map import world_file_problem

    assert world_file_problem(tmp_path, "testmap") == "missing"
    dlc = tmp_path / "testdlc" / "addons"
    dlc.mkdir(parents=True)
    (dlc / "Worlds_TestMap.ebo").write_bytes(b"not readable")
    assert world_file_problem(tmp_path, "testmap") == "encrypted"

    world_pbo(tmp_path, "othermap", coast(), version=27)
    assert world_file_problem(tmp_path, "othermap") == "unknown_version"
    world_pbo(tmp_path, "goodmap", coast())
    assert world_file_problem(tmp_path, "goodmap") is None
    # Not a terrain name, so not on the allowlist: refused before any file.
    assert world_file_problem(tmp_path, "../testmap") == "not_available"


def test_a_header_longer_than_the_first_read_is_read_whole(tmp_path):
    files = {f"data\\{'x' * 60}_{i}.paa": b"" for i in range(2000)}  # ~170 KB of header
    files["bigmap.wrp"] = wrp_bytes(coast())
    addons = tmp_path / "@Big" / "addons"
    addons.mkdir(parents=True)
    (addons / "big.pbo").write_bytes(pbo_bytes(files))
    assert world_file(tmp_path, "bigmap") is not None


def test_a_mod_search_reads_each_header_once(tmp_path, monkeypatch):
    addons = tmp_path / "@Some Map" / "addons"
    addons.mkdir(parents=True)
    pbo = addons / "somemap.pbo"
    pbo.write_bytes(pbo_bytes({"somemap.wrp": wrp_bytes(coast())}))
    (addons / "other.pbo").write_bytes(pbo_bytes({"config.bin": b"x"}))
    assert world_file(tmp_path, "somemap") is not None

    real = terrain_map._pbo_entries

    def _no_read(_pbo):
        raise AssertionError("header read again although nothing changed")

    monkeypatch.setattr(terrain_map, "_pbo_entries", _no_read)
    assert world_file(tmp_path, "somemap") is not None
    assert world_file(tmp_path, "nosuchmap") is None

    monkeypatch.setattr(terrain_map, "_pbo_entries", real)
    pbo.write_bytes(pbo_bytes({"config.bin": b"no world here any more"}))
    st = pbo.stat()
    os.utime(pbo, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert world_file(tmp_path, "somemap") is None


# ── the browser's copy ─────────────────────────────────────────────────


def test_heights_reach_the_browser_in_half_metres(tmp_path):
    grid = [[float(10 * y + x) for x in range(4)] for y in range(4)]
    world_pbo(tmp_path, "testmap", grid, cell=25.0)
    side, cell, unit, rows = decode(terrain_heights(tmp_path, "testmap"))
    assert (side, cell, unit) == (4, 25.0, 0.5)
    assert rows == [[round(v / 0.5) for v in row] for row in grid]


def test_a_point_below_sea_level_stays_sea():
    h = Heights(side=2, cell=1.0, values=array("f", [-0.2, 0.2, -0.0001, 0.0]))
    data = encode_heights(h)
    first = struct.unpack_from("<4h", data, 16)
    assert first[0] == -1  # rounds to 0, which would be land
    assert first[0] + first[1] == 0  # 0.2 m is 0.4 units: land
    assert first[2] == -1
    assert first[2] + first[3] == 0


def test_the_sea_floor_is_cut_off():
    h = Heights(side=2, cell=1.0, values=array("f", [-300.0, -300.0, 600.0, 600.0]))
    data = encode_heights(h)
    assert struct.unpack_from("<4h", data, 16) == (-100, 0, 1200, 0)


def test_a_built_file_is_reused_until_the_world_file_changes(tmp_path, monkeypatch):
    pbo = world_pbo(tmp_path, "testmap", coast())
    first = terrain_heights(tmp_path, "testmap")

    def _no_rebuild(_wf):
        raise AssertionError("built again although nothing changed")

    real = terrain_map.read_heights
    monkeypatch.setattr(terrain_map, "read_heights", _no_rebuild)
    assert terrain_heights(tmp_path, "testmap") == first

    monkeypatch.setattr(terrain_map, "read_heights", real)
    world_pbo(tmp_path, "testmap", [[5.0] * 8 for _ in range(8)])
    st = pbo.stat()
    os.utime(pbo, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    _side, _cell, _unit, rows = decode(terrain_heights(tmp_path, "testmap"))
    assert rows[0] == [10] * 8


def test_no_world_file_builds_nothing(tmp_path):
    assert terrain_heights(tmp_path, "testmap") is None
    assert not (tmp_path / "maps").exists()


def _unreadable_pbo(server_dir: Path, size: int = 4000) -> Path:
    """A world file whose peaks run past its end, so every read comes up short."""
    data = bytearray(wrp_bytes(coast()))
    at = 13 + 24 + 2 * len(LEAF_TREE)  # the peaks' count
    struct.pack_into("<I", data, at, 0x0FFFFFFF)
    addons = server_dir / "addons"
    addons.mkdir(parents=True, exist_ok=True)
    pbo = addons / "worlds_testmap.pbo"
    pbo.write_bytes(pbo_bytes({"testmap.wrp": bytes(data).ljust(size, b"\0")}))
    return pbo


def test_a_world_file_that_cannot_be_read_is_not_read_again_until_it_changes(tmp_path, monkeypatch):
    """plans/68 NEW-4: each request read the whole file again, behind the one build lock."""
    pbo = _unreadable_pbo(tmp_path)
    monkeypatch.setattr(terrain_map, "FIRST_READ", 1000)
    reads = []
    real = terrain_map.parse_heights

    def counting(data):
        reads.append(len(data))
        return real(data)

    monkeypatch.setattr(terrain_map, "parse_heights", counting)
    assert terrain_heights(tmp_path, "testmap") is None
    one_build = len(reads)
    assert one_build == 3  # 1000, 2000, then the whole entry
    for _ in range(3):
        assert terrain_heights(tmp_path, "testmap") is None
    assert len(reads) == one_build

    st = pbo.stat()
    os.utime(pbo, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert terrain_heights(tmp_path, "testmap") is None
    assert len(reads) == 2 * one_build

    # And after FAILURE_BACKOFF, the same file once more.
    for failure in terrain_map._failed:
        terrain_map._failed[failure] -= terrain_map.FAILURE_BACKOFF
    assert terrain_heights(tmp_path, "testmap") is None
    assert len(reads) == 3 * one_build


def test_a_built_or_failed_terrain_does_not_wait_for_another_build(tmp_path):
    """Only a build takes the build lock; a cache hit or a remembered failure answers at once."""
    world_pbo(tmp_path / "a", "testmap", coast())
    _unreadable_pbo(tmp_path / "b")
    assert terrain_heights(tmp_path / "a", "testmap") is not None
    assert terrain_heights(tmp_path / "b", "testmap") is None
    answers = []
    with terrain_map._build_lock:  # another terrain being built
        for server in (tmp_path / "a", tmp_path / "b"):
            t = threading.Thread(target=lambda s=server: answers.append(terrain_heights(s, "testmap")))
            t.start()
            t.join(timeout=5)
            assert not t.is_alive()
    assert answers[0] is not None and answers[1] is None


def test_two_server_dirs_with_the_same_map_keep_a_cache_each(tmp_path, monkeypatch):
    """plans/68 NB-3: one file per terrain made two instances rebuild in turn."""
    for name in ("a", "b"):
        world_pbo(tmp_path / name, "testmap", coast())
    builds = []
    real = terrain_map.read_heights

    def counting(wf):
        builds.append(wf.pbo)
        return real(wf)

    monkeypatch.setattr(terrain_map, "read_heights", counting)
    for server in [tmp_path / "a", tmp_path / "b"] * 3:
        assert terrain_heights(server, "testmap") is not None
    assert len(builds) == 2


def test_a_terrain_asked_for_in_another_case_is_the_same_cache_entry(tmp_path, monkeypatch):
    """The world file is matched in any case, so every spelling of the name
    was a build of its own and a pair of cache files nothing removed."""
    world_pbo(tmp_path, "testmap", coast())
    builds = []
    real = terrain_map.read_heights

    def counting(wf):
        builds.append(wf.pbo)
        return real(wf)

    monkeypatch.setattr(terrain_map, "read_heights", counting)
    answers = {terrain_heights(tmp_path, name) for name in ("testmap", "TestMap", "TESTMAP", "testmap")}
    assert len(answers) == 1 and None not in answers
    assert len(builds) == 1
    assert sorted(f.suffix for f in (tmp_path / "maps" / "terrain").iterdir()) == [".gz", ".json"]


def test_an_unreadable_world_file_is_remembered_in_any_case(tmp_path, monkeypatch):
    _unreadable_pbo(tmp_path)
    reads = []
    real = terrain_map.read_heights

    def counting(wf):
        reads.append(wf.pbo)
        return real(wf)

    monkeypatch.setattr(terrain_map, "read_heights", counting)
    for name in ("testmap", "TestMap", "TESTMAP"):
        assert terrain_heights(tmp_path, name) is None
    assert len(reads) == 1


def test_a_cache_whose_world_file_is_gone_and_the_old_names_are_removed(tmp_path):
    folder = tmp_path / "maps" / "terrain"
    folder.mkdir(parents=True)
    old = [folder / "testmap.heights.gz", folder / "testmap.heights.json", folder / "testmap.json"]
    for f in old:
        f.write_bytes(b"from before the cache was named per world file")
    gone = world_pbo(tmp_path / "a", "testmap", coast())
    world_pbo(tmp_path / "b", "testmap", coast())
    gone_gz = terrain_heights(tmp_path / "a", "testmap")
    kept_gz = terrain_heights(tmp_path / "b", "testmap")
    gone.unlink()

    world_pbo(tmp_path / "c", "testmap", coast())  # the next build prunes
    assert terrain_heights(tmp_path / "c", "testmap") is not None
    assert not gone_gz.exists() and not gone_gz.with_suffix(".json").exists()
    assert kept_gz.exists() and kept_gz.with_suffix(".json").exists()
    assert not any(f.exists() for f in old)


def test_a_big_grid_is_sampled_down(monkeypatch):
    monkeypatch.setattr(terrain_map, "MAX_SIDE", 4)
    h = Heights(side=8, cell=10.0, values=array("f", [1.0] * 64))
    data = encode_heights(h)
    side, cell = struct.unpack_from("<If", data, 4)
    assert (side, cell) == (4, 20.0)
    assert len(data) == 16 + 4 * 4 * 2


@pytest.mark.parametrize("terrain", ["namalsk", "deerisle", "takistanplus"])
def test_a_community_map_is_refused_without_a_file_read(tmp_path, monkeypatch, terrain):
    """A terrain off registry.heights_allowed: no search, no read, no cache."""
    from app.maps import navmesh_map, road_map
    from app.maps.navmesh_map import navmesh_file
    from app.maps.road_map import terrain_roads
    from app.maps.terrain_map import heights_source, world_file_problem

    def touched(*_a, **_k):
        raise AssertionError("a file was looked at")

    for module, name in [
        (terrain_map, "_candidates"), (terrain_map, "_wrp_names"), (terrain_map, "_pbo_entries"),
        (terrain_map, "_has_encrypted_world"), (navmesh_map, "_nav_candidates"),
    ]:
        monkeypatch.setattr(module, name, touched)

    def no_source(wf, *_a, **_k):
        assert wf is None, "a build was given a file"

    monkeypatch.setattr(terrain_map, "cached_build", no_source)
    monkeypatch.setattr(road_map, "cached_build", no_source)
    world_pbo(tmp_path, terrain, coast())  # a readable world file is there
    assert world_file(tmp_path, terrain) is None
    assert world_file_problem(tmp_path, terrain) == "not_available"
    source = heights_source(tmp_path, terrain)
    assert (source.kind, source.problem) == (None, "not_available")
    assert terrain_heights(tmp_path, terrain) is None
    assert terrain_roads(tmp_path, terrain) is None
    assert navmesh_file(tmp_path, terrain) is None


@pytest.mark.parametrize("terrain", ["banov", "banovfrost"])
def test_a_cleared_map_gives_world_heights_and_roads_but_never_the_navmesh(tmp_path, monkeypatch, terrain):
    """KubeloLive cleared heights and roads (plans/65); the navmesh is never read."""
    from app.maps import navmesh_map, road_map
    from app.maps.navmesh_map import navmesh_file

    monkeypatch.setattr(navmesh_map, "_nav_candidates", lambda *a, **k: pytest.fail("the navmesh was looked at"))
    built = []
    monkeypatch.setattr(road_map, "cached_build", lambda wf, *a, **k: built.append(wf) or tmp_path / "r.gz")
    world_pbo(tmp_path, terrain, coast())
    assert world_file(tmp_path, terrain) is not None
    assert terrain_heights(tmp_path, terrain) is not None
    assert road_map.terrain_roads(tmp_path, terrain) is not None and built[0] is not None
    assert navmesh_file(tmp_path, terrain) is None


def test_a_map_with_a_maker_grid_never_reads_its_world_file_for_heights(tmp_path, monkeypatch):
    """A `heights_url` makes the maker's grid the source (maker_heights), with no file read."""
    from app.maps import maker_heights, registry

    cleared = registry.ClearedMap(registry._KUBELO, heights_url="https://zedstead.com/maps/banov.asc.gz", roads=True)
    monkeypatch.setitem(registry.CLEARED_MAPS, "banov", cleared)

    monkeypatch.setattr(terrain_map, "_candidates", lambda *a, **k: pytest.fail("a world file was looked at"))
    monkeypatch.setattr(maker_heights, "maker_heights", lambda terrain: tmp_path / f"{terrain}.gz")
    source = heights_source(tmp_path, "banov")
    assert (source.kind, source.file, source.problem) == ("maker", None, None)
    assert terrain_heights(tmp_path, "banov") == tmp_path / "banov.gz"
