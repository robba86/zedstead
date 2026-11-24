"""Sakhal's terrain from its navmesh (plans/74): heights from a Detour navmesh.

Every navmesh here is built by `navmesh_bytes`: invented tiles in the layout
measured on DayZ 1.29's three stock navmeshes. Never copy a real navmesh, or
anything made from one, into a test.
"""

from __future__ import annotations

import logging
import math
import struct
from pathlib import Path

import pytest

from app.maps import navmesh_map, registry, terrain_map
from app.maps.navmesh_map import navmesh_file, read_navmesh_heights
from app.maps.terrain_map import heights_source, terrain_heights
from tests.test_terrain_map import coast, decode, pbo_bytes, world_pbo

# The readers only open a terrain on registry.TERRAIN_LAYER_MAPS; these tests
# build files for invented terrains (tests/conftest.py::INVENTED_TERRAINS).
pytestmark = pytest.mark.usefixtures('invented_terrains_allowed')

GROUND = 1  # area 1, type 0: plain ground
OFFMESH = (1 << 6) | 48  # type 1: an off-mesh connection


# ── builders ───────────────────────────────────────────────────────────


def square(x0: float, z0: float, size: float, height) -> list[tuple[float, float, float]]:
    """A square's corners, counterclockwise from the south-west; *height(x, z)* gives each corner's."""
    return [(x, height(x, z), z) for x, z in ((x0, z0), (x0 + size, z0), (x0 + size, z0 + size), (x0, z0 + size))]


def tile_bytes(tx: int, tz: int, polys, *, magic: bytes = b"VAND", vert_count=None, poly_count=None) -> bytes:
    """Detour's data of one tile: *polys* is ``[(corners, areaAndType), …]``, each corner ``(x, h, z)``.

    A corner index of ``99`` in a polygon's corners names a vertex the tile does not have.
    """
    verts, index, rows = [], {}, []
    for corners, area in polys:
        ids = []
        for corner in corners:
            if corner == 99:
                ids.append(99)
                continue
            if corner not in index:
                index[corner] = len(verts)
                verts.append(corner)
            ids.append(index[corner])
        rows.append((ids, area))
    out = magic + struct.pack(
        "<14I", 11, tx, tz, 0, 0,
        len(rows) if poly_count is None else poly_count,
        len(verts) if vert_count is None else vert_count,
        0, 0, 0, 0, 0, 0, 0,
    )
    out += struct.pack("<10f", 2.0, 0.6, 0.9, tx * 50.0, -10.0, tz * 50.0, tx * 50.0 + 50, 100.0, tz * 50.0 + 50, 1.0)
    out += struct.pack("<I", 12)  # the uint32 with no known meaning
    out += b"".join(struct.pack("<3f", *v) for v in verts)
    for ids, area in rows:
        padded = (ids + [0] * 6)[:6]
        out += struct.pack("<I6H6HHBB", 0, *padded, *[0xFFFF] * 6, 1, len(ids), area)
    return out + bytes(24)  # links and the BV tree, which are not read


def navmesh_bytes(tiles: list[bytes], along_x: int, along_z: int, *, magic=b"TESM", version=6,
                  count=None, table=None, last_trailer=False, lengths=None) -> bytes:
    """A ``navmesh.nm``: the header, the tile table, then each tile in its frame.

    Like Sakhal's, the table's last entry is 0 and the last tile has no trailer.
    """
    count = len(tiles) if count is None else count
    framed, ends, pos = [], [], 0
    for i, body in enumerate(tiles):
        length = 12 + len(body) + 4
        stated = length if lengths is None or lengths[i] is None else lengths[i]
        frame = struct.pack("<3I", 0x10000, stated - 16, 0) + body
        if i < len(tiles) - 1 or last_trailer:
            frame += struct.pack("<I", (i + 1) << 20)
        framed.append(frame)
        pos += length
        ends.append(pos)
    ends[-1] = 0
    head = magic + struct.pack("<7I", version, count, along_x, along_z, 0, 0, 0)
    head += struct.pack("<2f", 50.0, 50.0) + struct.pack("<3I", count, 0x100000, 0)
    return head + struct.pack(f"<{len(ends)}I", *(table or ends)) + b"".join(framed)


def nav_pbo(folder: Path, terrain: str, data: bytes) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    pbo = folder / f"worlds_{terrain}_nav.pbo"
    pbo.write_bytes(pbo_bytes({"config.bin": b"not a navmesh", "navmesh.nm": data}))
    return pbo


def slope(x: float, z: float) -> float:
    """Sea in the south-west corner, land rising to the north-east."""
    return 0.5 * x + 0.25 * z - 10.0


def ground_tiles(height=slope, extra=None) -> list[bytes]:
    """2x2 tiles of 50 m, each one square of ground; *extra* maps a tile to more polygons."""
    extra = extra or {}
    return [
        tile_bytes(tx, tz, [*extra.get((tx, tz), []), (square(tx * 50.0, tz * 50.0, 50.0, height), GROUND)])
        for tz in range(2) for tx in range(2)
    ]


def entry_of(tmp_path: Path, data: bytes):
    nav_pbo(tmp_path / "addons", "testmap", data)
    return navmesh_file(tmp_path, "testmap")


def heights(tmp_path: Path, data: bytes, world: float = 100.0, side: int = 10):
    """A 10 m grid over the 100 m of 2x2 tiles: points at 0, 10, … 90 m."""
    return read_navmesh_heights(entry_of(tmp_path, data), world, side)


def at(h, x: float, z: float) -> float:
    return h.values[round(z / h.cell) * h.side + round(x / h.cell)]


@pytest.fixture(autouse=True)
def _maps_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(terrain_map.paths, "maps_dir", lambda: tmp_path / "maps")


# ── the heights ────────────────────────────────────────────────────────


@pytest.mark.parametrize("version", [6, 7, 9, 8, 10, 42])
def test_the_heights_follow_the_ground_row_0_south(tmp_path, version):
    """Version 6 is DayZ 1.29's layout, 7 is 1.30's, 9 its 2026 Q4 update's: the same header,
    frames and tiles. A version this reader has not seen is read with the same layout."""
    h = heights(tmp_path, navmesh_bytes(ground_tiles(), 2, 2, version=version))
    assert (h.side, h.cell) == (10, 10.0)
    for z in range(0, 100, 10):
        for x in range(0, 100, 10):
            assert at(h, x, z) == pytest.approx(slope(x, z), abs=1e-4)
    assert at(h, 0, 0) < 0 < at(h, 90, 90)  # the sea corner


def test_where_polygons_overlap_the_lowest_is_the_ground(tmp_path):
    """A bridge 40 m up, listed before the ground it spans, and a building's upper floor after it."""
    bridge = (square(10.0, 10.0, 20.0, lambda x, z: 40.0), GROUND)
    floor = (square(60.0, 60.0, 20.0, lambda x, z: 80.0), GROUND)
    tiles = ground_tiles(extra={(0, 0): [bridge]})
    tiles[3] = tile_bytes(1, 1, [(square(50.0, 50.0, 50.0, slope), GROUND), floor])
    h = heights(tmp_path, navmesh_bytes(tiles, 2, 2))
    assert at(h, 20, 20) == pytest.approx(slope(20, 20), abs=1e-4)
    assert at(h, 70, 70) == pytest.approx(slope(70, 70), abs=1e-4)


def test_an_off_mesh_connection_is_not_ground(tmp_path):
    jump = (square(10.0, 10.0, 20.0, lambda x, z: -100.0), OFFMESH)
    h = heights(tmp_path, navmesh_bytes(ground_tiles(extra={(0, 0): [jump]}), 2, 2))
    assert at(h, 20, 20) == pytest.approx(slope(20, 20), abs=1e-4)


def test_a_point_no_polygon_covers_takes_its_neighbours_height(tmp_path):
    """A 50 m tile with a hole, and the strip past the last tile (100 m of tiles, 120 m of world)."""
    holed = [
        (square(50.0, 0.0, 50.0, slope)[:2] + [(100.0, slope(100, 20), 20.0), (50.0, slope(50, 20), 20.0)], GROUND),
        (square(50.0, 40.0, 50.0, slope)[:2] + [(100.0, slope(100, 50), 50.0), (50.0, slope(50, 50), 50.0)], GROUND),
    ]
    tiles = ground_tiles()
    tiles[1] = tile_bytes(1, 0, holed)  # nothing between 20 m and 40 m north
    h = heights(tmp_path, navmesh_bytes(tiles, 2, 2), world=120.0, side=12)
    assert all(math.isfinite(v) for v in h.values)
    # The hole's two rows fill from the rows beside them, the strip from the edge.
    assert at(h, 70, 30) == pytest.approx(slope(70, 30), abs=3.0)
    assert at(h, 110, 110) == pytest.approx(at(h, 100, 100), abs=10.0)
    assert at(h, 70, 20) == pytest.approx(slope(70, 20), abs=1e-4)


def test_a_tile_without_a_detour_header_is_left_out_and_counted(tmp_path, caplog):
    tiles = ground_tiles()
    tiles[3] = tile_bytes(1, 1, [], magic=b"XXXX")
    with caplog.at_level(logging.WARNING):
        h = heights(tmp_path, navmesh_bytes(tiles, 2, 2))
    assert "1 navmesh tiles without a Detour header" in caplog.text
    assert all(math.isfinite(v) for v in h.values)


def test_a_last_tile_with_its_trailer_and_a_full_table_are_read(tmp_path):
    tiles = ground_tiles()
    ends, pos = [], 0
    for body in tiles:
        pos += 12 + len(body) + 4
        ends.append(pos)
    h = heights(tmp_path, navmesh_bytes(tiles, 2, 2, table=ends, last_trailer=True))
    assert at(h, 90, 90) == pytest.approx(slope(90, 90), abs=1e-4)


def test_corners_outside_the_world_are_clipped(tmp_path):
    far = (square(-500.0, -500.0, 2000.0, lambda x, z: -5.0), GROUND)
    h = heights(tmp_path, navmesh_bytes([tile_bytes(0, 0, [far])], 1, 1))
    assert set(h.values) == {-5.0}


# ── what a navmesh may not hold ────────────────────────────────────────


def _refused(tmp_path, data: bytes, match: str):
    with pytest.raises(ValueError, match=match):
        heights(tmp_path, data)


@pytest.mark.parametrize("kw", [{"magic": b"MSET"}, {"version": 5}, {"version": 0}])
def test_another_format_is_refused(tmp_path, kw):
    _refused(tmp_path, navmesh_bytes(ground_tiles(), 2, 2, **kw), "not a navmesh this reader knows")


@pytest.mark.parametrize("count, along", [(1025 * 1024, (1025, 1024)), (5, (2, 2)), (4, (4, 4))])
def test_a_tile_count_no_navmesh_has_is_refused(tmp_path, count, along):
    _refused(tmp_path, navmesh_bytes(ground_tiles(), *along, count=count), "tiles in a")


def test_a_tile_that_runs_past_the_navmesh_is_refused(tmp_path):
    data = navmesh_bytes(ground_tiles(), 2, 2, lengths=[None, None, None, 1 << 20])
    _refused(tmp_path, data, "tile 3 of the navmesh runs past its end")


def test_a_tile_that_disagrees_with_the_table_is_refused(tmp_path):
    data = bytearray(navmesh_bytes(ground_tiles(), 2, 2))
    struct.pack_into("<I", data, 52, struct.unpack_from("<I", data, 52)[0] + 4)
    _refused(tmp_path, bytes(data), "tile 0's length disagrees")


@pytest.mark.parametrize("kw", [{"vert_count": 10_000}, {"poly_count": 10_000}])
def test_counts_that_need_more_than_the_tile_are_refused(tmp_path, kw):
    tiles = ground_tiles()
    tiles[0] = tile_bytes(0, 0, [(square(0.0, 0.0, 50.0, slope), GROUND)], **kw)
    _refused(tmp_path, navmesh_bytes(tiles, 2, 2), "need more than its length")


def test_a_polygon_naming_a_corner_the_tile_lacks_is_refused(tmp_path):
    tiles = ground_tiles()
    tiles[0] = tile_bytes(0, 0, [(square(0.0, 0.0, 50.0, slope)[:3] + [99], GROUND)])
    _refused(tmp_path, navmesh_bytes(tiles, 2, 2), "names a corner")


def test_a_polygon_with_more_than_six_corners_is_refused(tmp_path):
    tiles = ground_tiles()
    body = bytearray(tiles[0])
    polys_at = navmesh_map._VERTS_AT - 12 + 4 * navmesh_map._VERT
    body[polys_at + 30] = 7  # the polygon's vertCount
    tiles[0] = bytes(body)
    _refused(tmp_path, navmesh_bytes(tiles, 2, 2), "with 7 corners")


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, 20_000.0])
def test_a_height_no_terrain_has_is_refused(tmp_path, value):
    tiles = ground_tiles()
    tiles[0] = tile_bytes(0, 0, [(square(0.0, 0.0, 50.0, lambda x, z: value), GROUND)])
    _refused(tmp_path, navmesh_bytes(tiles, 2, 2), "height of")


@pytest.mark.parametrize("value", [math.nan, math.inf])
def test_a_corner_that_is_not_a_number_is_refused(tmp_path, value):
    tiles = ground_tiles()
    tiles[0] = tile_bytes(0, 0, [([(value, 1.0, 0.0), (50.0, 1.0, 0.0), (50.0, 1.0, 50.0)], GROUND)])
    _refused(tmp_path, navmesh_bytes(tiles, 2, 2), "not a number")


def test_polygons_that_cover_the_map_over_and_over_are_refused(tmp_path):
    """A polygon may be as large as the grid, so the build's time followed the
    polygon count: 80 of them in a 3 KB file took 16 s behind the build lock."""
    whole = (square(-500.0, -500.0, 2000.0, lambda x, z: 1.0), GROUND)
    assert set(heights(tmp_path, navmesh_bytes([tile_bytes(0, 0, [whole] * 4)], 1, 1)).values) == {1.0}
    _refused(tmp_path, navmesh_bytes([tile_bytes(0, 0, [whole] * 20)], 1, 1), "far more ground than a map has")


def test_slivers_that_cross_every_row_are_refused(tmp_path):
    """A triangle thinner than a cell draws next to nothing and still walks every row it crosses."""
    sliver = ([(19.999, 1.0, 0.0), (20.001, 1.0, 0.0), (20.0, 1.0, 95.0)], GROUND)
    ground = (square(0.0, 0.0, 100.0, lambda x, z: 2.0), GROUND)
    h = heights(tmp_path, navmesh_bytes([tile_bytes(0, 0, [ground] + [sliver] * 10)], 1, 1))
    assert at(h, 50, 50) == 2.0
    _refused(
        tmp_path, navmesh_bytes([tile_bytes(0, 0, [ground] + [sliver] * 100)], 1, 1), "far more ground than a map has",
    )


def test_a_navmesh_with_no_ground_is_refused(tmp_path):
    only_jumps = [tile_bytes(0, 0, [(square(0.0, 0.0, 50.0, slope), OFFMESH)])]
    _refused(tmp_path, navmesh_bytes(only_jumps, 1, 1), "covers no ground")


def test_a_navmesh_cut_short_is_refused(tmp_path):
    data = navmesh_bytes(ground_tiles(), 2, 2)
    _refused(tmp_path, data[:-200], "runs past its end")
    _refused(tmp_path, data[:40], "shorter than its header")


# ── finding it ─────────────────────────────────────────────────────────


def test_the_navmesh_is_found_in_addons_a_dlc_folder_or_a_downloaded_mod(tmp_path):
    data = navmesh_bytes(ground_tiles(), 2, 2)
    assert navmesh_file(tmp_path, "testmap") is None
    dlc = nav_pbo(tmp_path / "testdlc" / "Addons", "TestMap", data)
    found = navmesh_file(tmp_path, "testmap")
    assert (found.pbo, found.name) == (dlc, "navmesh.nm")
    assert dlc.read_bytes()[found.offset:found.offset + 4] == b"TESM"
    stock = nav_pbo(tmp_path / "addons", "testmap", data)
    assert navmesh_file(tmp_path, "testmap").pbo == stock

    item = tmp_path / "workshop" / "123"
    pbo = nav_pbo(item / "addons", "othermap", data)
    assert navmesh_file(tmp_path, "othermap") is None
    assert navmesh_file(tmp_path, "othermap", [item]).pbo == pbo
    assert navmesh_file(tmp_path, "../testmap") is None


# ── the terrain map's fallback (D1) ────────────────────────────────────


def _encrypted(server_dir: Path, terrain: str = "testmap") -> None:
    dlc = server_dir / "testdlc" / "addons"
    dlc.mkdir(parents=True, exist_ok=True)
    (dlc / f"worlds_{terrain}.ebo").write_bytes(b"invented, and not read")


@pytest.fixture
def small_world(monkeypatch):
    """`terrain_heights` grids the navmesh over map_size at MAX_SIDE; 120 m at 12 a side here."""
    monkeypatch.setitem(registry.MAP_SIZES, "testmap", 120)
    monkeypatch.setattr(terrain_map, "MAX_SIDE", 12)


def test_an_encrypted_world_file_with_a_navmesh_serves_its_heights(tmp_path, small_world):
    _encrypted(tmp_path)
    assert heights_source(tmp_path, "testmap").kind is None
    nav_pbo(tmp_path / "testdlc" / "addons", "testmap", navmesh_bytes(ground_tiles(), 2, 2))
    source = heights_source(tmp_path, "testmap")
    assert (source.kind, source.problem) == ("navmesh", "encrypted")
    side, cell, unit, rows = decode(terrain_heights(tmp_path, "testmap"))
    assert (side, cell, unit) == (12, 10.0, 0.5)
    assert rows[2][3] == round(slope(30, 20) / unit)


def test_a_readable_world_file_wins_over_a_navmesh(tmp_path, small_world):
    nav_pbo(tmp_path / "addons", "testmap", navmesh_bytes(ground_tiles(), 2, 2))
    assert heights_source(tmp_path, "testmap").kind is None  # no world file at all: missing
    world_pbo(tmp_path, "testmap", coast())
    _encrypted(tmp_path)
    source = heights_source(tmp_path, "testmap")
    assert (source.kind, source.problem) == ("world", None)
    side, _cell, _unit, _rows = decode(terrain_heights(tmp_path, "testmap"))
    assert side == 8  # coast()'s grid, not the navmesh's 12


def test_a_world_file_of_an_unknown_version_does_not_fall_back(tmp_path, small_world):
    world_pbo(tmp_path, "testmap", coast(), version=27)
    nav_pbo(tmp_path / "addons", "testmap", navmesh_bytes(ground_tiles(), 2, 2))
    _encrypted(tmp_path)
    source = heights_source(tmp_path, "testmap")
    assert (source.kind, source.problem) == (None, "unknown_version")
    assert terrain_heights(tmp_path, "testmap") is None


def test_a_navmesh_build_is_cached_and_a_bad_one_remembered(tmp_path, small_world, monkeypatch):
    _encrypted(tmp_path)
    nav_pbo(tmp_path / "testdlc" / "addons", "testmap", navmesh_bytes(ground_tiles(), 2, 2))
    builds = []
    real = navmesh_map.read_navmesh_heights

    def counting(*args):
        builds.append(args)
        return real(*args)

    monkeypatch.setattr(navmesh_map, "read_navmesh_heights", counting)
    first = terrain_heights(tmp_path, "testmap")
    assert terrain_heights(tmp_path, "testmap") == first
    assert len(builds) == 1
    assert first.name.startswith("testmap.") and first.name.endswith(".heights.gz")

    other = tmp_path / "other"
    _encrypted(other)
    nav_pbo(other / "testdlc" / "addons", "testmap", navmesh_bytes(ground_tiles(), 2, 2, version=5))
    assert heights_source(other, "testmap").kind == "navmesh"  # not read yet
    assert terrain_heights(other, "testmap") is None
    assert terrain_heights(other, "testmap") is None
    assert len(builds) == 2  # the bad one was read once
    # Once it failed, /map-info's source says so and offers no terrain.
    assert heights_source(other, "testmap") == terrain_map.HeightsSource(None, None, "navmesh_unreadable")
