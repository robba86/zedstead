"""The roads on the terrain map: read from a world file, joined, coloured by kind.

Every world file here is built by `tests.test_terrain_map.world_pbo` with
invented road pieces in the layout measured on DayZ 1.29's Chernarus and
Livonia and on Deer Isle. Never copy a real world file into a test; a
community map's is its author's (plans/65).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import random
import signal
import struct
import threading
import time
from collections import defaultdict
from contextlib import contextmanager

import pytest

from app.maps import road_map, terrain_map
from app.maps.road_map import ROAD_CLASSES, encode_roads, join_pieces, parse_roads, road_class, terrain_roads
from tests.test_terrain_map import coast, world_pbo, wrp_bytes

# The readers only open a terrain on registry.TERRAIN_LAYER_MAPS; these tests
# build files for invented terrains (tests/conftest.py::INVENTED_TERRAINS).
pytestmark = pytest.mark.usefixtures('invented_terrains_allowed')


@pytest.fixture(autouse=True)
def _maps_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(terrain_map.paths, "maps_dir", lambda: tmp_path / "maps")


def _decode(path) -> dict:
    """What useRoadLayer.js gets once the browser has inflated the file."""
    return json.loads(gzip.decompress(path.read_bytes()))["roads"]


# ── kinds ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "model, kind",
    [
        ("dz\\structures\\roads\\parts\\asf1_25.p3d", "main"),
        ("dz\\structures\\roads\\parts\\asf1enoch_12.p3d", "main"),
        ("dz\\structures\\roads\\parts\\asf2_6konec.p3d", "secondary"),
        ("dz\\structures\\roads\\parts\\asf3_10 100.p3d", "minor"),
        ("dz\\structures\\roads\\parts\\city_25.p3d", "minor"),
        ("dz\\structures\\roads\\parts\\grav_7 100.p3d", "gravel"),
        ("dz\\structures\\roads\\parts\\mud_1 1000.p3d", "dirt"),
        ("dz\\structures\\roads\\parts\\mudenoch_25.p3d", "dirt"),
        ("dz\\structures\\roads\\parts\\kr_25.p3d", "dirt"),
        ("dz\\structures\\roads\\parts\\quarryenoch_25.p3d", "dirt"),
        ("dz\\structures\\roads\\parts\\path_rock_curve.p3d", "path"),
        ("dz\\structures\\roads\\parts\\pathenoch_dirt_10.p3d", "path"),
        ("dz\\structures\\roads\\parts\\taxiway_25.p3d", "airfield"),
        ("dz\\structures\\roads\\parts\\runway_25.p3d", "airfield"),
        ("dz\\structures\\roads\\bridges\\bridge_wood_25.p3d", "bridge"),
        ("somemap\\roads\\unheard_of_25.p3d", "other"),
        ("dz\\structures\\roads\\decals\\decal_damage_long1.p3d", None),
        ("dz\\structures\\roads\\parts\\centerline_25.p3d", None),
    ],
)
def test_a_road_piece_is_the_kind_its_model_says(model, kind):
    assert road_class(model) == kind


# ── reading ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("version", [28, 29, 32])
def test_roads_are_read_by_kind_and_decals_are_not_roads(version):
    data = wrp_bytes(coast(), version=version, roads=[
        ("dz\\roads\\asf1_25.p3d", [(10.0, 20.0), (35.0, 20.0)]),
        ("dz\\roads\\mud_10 25.p3d", [(1.0, 2.0), (3.0, 4.0), (5.0, 7.0)]),
        ("dz\\roads\\decal_damage_long1.p3d", [(1.0, 1.0), (2.0, 2.0)]),
    ])
    assert parse_roads(data) == {
        "main": [[(10.0, 20.0), (35.0, 20.0)]],
        "dirt": [[(1.0, 2.0), (3.0, 4.0), (5.0, 7.0)]],
    }


def test_a_road_list_that_does_not_end_where_its_size_says_is_refused():
    data = bytearray(wrp_bytes(coast(), roads=[("dz\\roads\\asf1_25.p3d", [(0.0, 0.0), (25.0, 0.0)])]))
    at = bytes(data).index(struct.pack("<I", 99)) + 4  # the road list's size
    (size,) = struct.unpack_from("<I", data, at)
    struct.pack_into("<I", data, at, size + 4)
    with pytest.raises(ValueError, match="road list ended"):
        parse_roads(bytes(data))


@pytest.mark.parametrize("point", [(math.inf, 1.0), (1.0, -math.inf), (math.nan, 1.0), (1e30, 1.0), (241.0, 1.0)])
def test_a_piece_off_the_map_or_not_a_number_is_left_out_and_the_rest_drawn(tmp_path, caplog, point):
    """plans/68 NB-1: an infinite point raised OverflowError, so /roads answered 500 every time.

    The test map is 80 m a side; a road may run up to one map's width off it.
    Such a piece is left out rather than the whole file refused: Livonia's
    real world file has 352 pieces about 207 km out, and refusing it lost every
    road on the map.
    """
    roads = [("dz\\roads\\asf1_25.p3d", [(0.0, 0.0), point]), ("dz\\roads\\asf1_25.p3d", [(0.0, 5.0), (25.0, 5.0)])]
    with caplog.at_level("WARNING", logger="app.maps.road_map"):
        assert parse_roads(wrp_bytes(coast(), roads=roads)) == {"main": [[(0.0, 5.0), (25.0, 5.0)]]}
    assert "1 road piece(s) off the 80 m map" in caplog.text
    world_pbo(tmp_path, "testmap", coast(), roads=roads)
    assert _decode(terrain_roads(tmp_path, "testmap")) == {"main": [[0.0, 5.0, 25.0, 5.0]]}


def test_a_road_that_runs_off_the_edge_is_still_a_road():
    data = wrp_bytes(coast(), roads=[("dz\\roads\\asf1_25.p3d", [(-75.0, 5.0), (155.0, 5.0)])])
    assert parse_roads(data) == {"main": [[(-75.0, 5.0), (155.0, 5.0)]]}


def test_a_point_on_a_piece_that_is_not_drawn_is_not_checked():
    data = wrp_bytes(coast(), roads=[("dz\\roads\\decal_damage_long1.p3d", [(0.0, 0.0), (math.inf, 0.0)])])
    assert parse_roads(data) == {}


@pytest.mark.parametrize("first_read", [40, 300, 700])
def test_a_read_that_stops_short_reads_more(tmp_path, monkeypatch, first_read):
    """Cut in the heights, in the names or in the road list: the read grows."""
    world_pbo(tmp_path, "testmap", coast(), roads=[("dz\\roads\\asf1_25.p3d", [(0.0, 0.0), (25.0, 0.0)])])
    monkeypatch.setattr(terrain_map, "FIRST_READ", first_read)
    assert _decode(terrain_roads(tmp_path, "testmap")) == {"main": [[0.0, 0.0, 25.0, 0.0]]}


# ── joining ────────────────────────────────────────────────────────────


def test_pieces_that_meet_are_joined_whichever_way_they_point():
    a = [(0.0, 0.0), (10.0, 0.0)]
    b = [(20.0, 0.0), (10.2, 0.1)]  # backwards, and 0.2 m off
    c = [(20.0, 0.0), (30.0, 5.0), (40.0, 5.0)]
    assert join_pieces([b, c, a]) in (
        [[(0.0, 0.0), (10.0, 0.0), (20.0, 0.0), (30.0, 5.0), (40.0, 5.0)]],
        [[(40.0, 5.0), (30.0, 5.0), (20.0, 0.0), (10.2, 0.1), (0.0, 0.0)]],
    )


def test_a_junction_keeps_every_piece_once():
    arms = [[(0.0, 0.0), (10.0, 0.0)], [(10.0, 0.0), (20.0, 0.0)], [(10.0, 0.0), (10.0, 10.0)]]
    lines = join_pieces(arms)
    assert len(lines) == 2
    assert sum(len(line) - 1 for line in lines) == 3  # three segments, none twice


def test_ends_further_apart_than_half_a_metre_stay_apart():
    lines = join_pieces([[(0.0, 0.0), (10.0, 0.0)], [(10.7, 0.0), (20.0, 0.0)]])
    assert len(lines) == 2


# ── bridges ────────────────────────────────────────────────────────────


def test_a_bridge_takes_the_kind_of_the_road_that_meets_it():
    pieces = {
        "main": [[(0.0, 0.0), (10.0, 0.0)], [(42.0, 0.0), (60.0, 0.0)]],
        "dirt": [[(10.0, 0.0), (10.0, -30.0)]],
        "bridge": [[(12.0, 0.0), (27.0, 0.0)], [(27.0, 0.0), (40.0, 0.0)]],  # 2 m off each end
    }
    roads = json.loads(encode_roads(pieces))["roads"]
    # Main and dirt both meet the first piece; the higher kind wins. The
    # second piece takes the road from the far end, and the two join.
    assert roads == {
        "main": [[0.0, 0.0, 10.0, 0.0], [42.0, 0.0, 60.0, 0.0], [12.0, 0.0, 27.0, 0.0, 40.0, 0.0]],
        "dirt": [[10.0, 0.0, 10.0, -30.0]],
    }


def test_a_bridge_no_road_meets_is_drawn_as_other():
    roads = json.loads(encode_roads({"bridge": [[(500.0, 500.0), (532.0, 500.0)]]}))["roads"]
    assert roads == {"other": [[500.0, 500.0, 532.0, 500.0]]}


# ── how long it takes (plans/68 NEW-8) ─────────────────────────────────


@contextmanager
def _within(seconds: float):
    """Fail past *seconds*, and where a timer signal can, stop the run there.

    Without the stop, code gone quadratic again holds the suite for minutes
    before the assertion gets to fail it.
    """

    def _stop(_signum, _frame):
        raise TimeoutError(f"still running after {seconds} s")

    timer = hasattr(signal, "setitimer") and threading.current_thread() is threading.main_thread()
    if timer:
        previous = signal.signal(signal.SIGALRM, _stop)
        signal.setitimer(signal.ITIMER_REAL, seconds)
    started = time.perf_counter()
    try:
        yield
    finally:
        if timer:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
    elapsed = time.perf_counter() - started
    assert elapsed < seconds, f"took {elapsed:.2f}s"


def test_a_long_bridge_listed_from_its_far_end_takes_its_road_in_linear_time():
    """Each pass over the bridges used to reach one more piece of this chain.

    2,000 pieces took 4.0 s and 4,000 took 16.2 s; 20,000 now take 0.15 s.
    """
    road = [(-25.0, 0.0), (0.0, 0.0)]
    chain = [[(10.0 * j, 0.0), (10.0 * j + 10.0, 0.0)] for j in range(20_000)]
    with _within(2.0):
        moved = road_map._bridges_carry_their_road({"main": [road], "bridge": chain[::-1]})
    assert moved == {"main": [road, *chain]}  # nearest first: the order the passes reach them in


def test_many_pieces_ending_on_one_point_join_in_linear_time():
    """Every join at the shared point used to step over every piece already used there.

    40,000 pieces took 22.8 s (6.0 s at 20,000); now 0.2 s.
    """
    far = [(float(j % 200) * 7 + 3, float(j // 200) * 7 + 3) for j in range(40_000)]
    with _within(2.0):
        lines = join_pieces([[(0.0, 0.0), pt] for pt in far])
    assert lines == [[far[j + 1], (0.0, 0.0), far[j]] for j in range(0, len(far), 2)]


@pytest.mark.parametrize(
    "model, ends",
    [
        ("dz\\roads\\asf1_25.p3d", [(10.0 + 0.001 * i, 10.0) for i in range(road_map._CROWD + 1)]),
        # 0.4 m apart: a few to each half-metre square, all in one 3 m square
        ("dz\\roads\\bridge_wood_25.p3d", [(29.0 + 0.4 * (i % 6), 29.0 + 0.4 * (i // 6)) for i in range(36)]),
    ],
    ids=["road", "bridge"],
)
def test_road_ends_crowded_closer_than_any_map_puts_them_give_no_roads(tmp_path, model, ends):
    """Every end near a point is measured against it, so a crowd of them is refused."""
    assert len(ends) > road_map._CROWD
    pieces = [(model, [pt, (-70.0 + 4.0 * i, -70.0)]) for i, pt in enumerate(ends)]
    with pytest.raises(ValueError, match="road ends in one"):
        encode_roads(parse_roads(wrp_bytes(coast(), roads=pieces)))
    world_pbo(tmp_path, "testmap", coast(), roads=pieces)
    assert terrain_roads(tmp_path, "testmap") is None


def _random_roads(seed: int) -> dict[str, list[list[tuple[float, float]]]]:
    """10,000 roads between 4,000 points and 500 bridge chains, the bridges shuffled.

    Half the ends are moved up to 0.6 m (a road's) or 2.5 m (a bridge's) on
    each axis, so some still meet and some no longer do. No trigonometry, so
    every platform makes the same numbers.
    """
    rnd = random.Random(seed)
    spots = [(rnd.randrange(200) * 12.5, rnd.randrange(200) * 12.5) for _ in range(4000)]

    def moved(x, z, reach):
        if rnd.random() < 0.5:
            return x + rnd.uniform(-reach, reach), z + rnd.uniform(-reach, reach)
        return x, z

    pieces = defaultdict(list)
    for _ in range(10_000):
        a, b = moved(*rnd.choice(spots), 0.6), moved(*rnd.choice(spots), 0.6)
        cuts = sorted(rnd.random() for _ in range(rnd.randrange(3)))
        pieces[rnd.choice(ROAD_CLASSES)].append([a, *((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f) for f in cuts), b])
    bridges = []
    for _ in range(500):
        at = moved(*rnd.choice(spots), 2.5)
        for _ in range(rnd.randrange(1, 7)):
            nxt = (at[0] + rnd.uniform(-15.0, 15.0), at[1] + rnd.uniform(-15.0, 15.0))
            bridges.append([at, nxt] if rnd.random() < 0.5 else [nxt, at])
            at = moved(*nxt, 2.5)
    rnd.shuffle(bridges)
    pieces["bridge"] = bridges
    return dict(pieces)


def test_a_large_road_set_comes_out_byte_for_byte_as_before_the_index():
    """Digests of what the scanning join and bridge passes made of this set.

    Taken from the code before plans/68 NEW-8: the index changed how long
    the answer takes, not the answer, nor the order of lines and points.
    """
    pieces = _random_roads(68)
    encoded = encode_roads(pieces)
    joined = json.dumps({kind: join_pieces(ps) for kind, ps in sorted(pieces.items())}).encode()
    assert hashlib.sha256(encoded).hexdigest() == "41a9eaab37b8510898c914352055bd9836b4c0488d8122220c5d9f6b7533ce09"
    assert hashlib.sha256(joined).hexdigest() == "f61352958ddc67c1d5b63088c44e864e86557630aca521f3870f49856ca1238d"


# ── the browser's copy ─────────────────────────────────────────────────


def test_roads_reach_the_browser_joined_and_to_a_tenth_of_a_metre(tmp_path):
    world_pbo(tmp_path, "testmap", coast(), cell=100.0, roads=[  # an 800 m map
        ("dz\\roads\\asf2_12.p3d", [(100.04, 200.06), (112.0, 200.0)]),
        ("dz\\roads\\asf2_12.p3d", [(112.0, 200.0), (124.0, 200.0)]),
        ("dz\\roads\\grav_6.p3d", [(1.0, 1.0), (1.01, 1.02), (7.0, 1.0)]),
    ])
    assert _decode(terrain_roads(tmp_path, "testmap")) == {
        "gravel": [[1.0, 1.0, 7.0, 1.0]],  # the second point rounds onto the first
        "secondary": [[100.0, 200.1, 112.0, 200.0, 124.0, 200.0]],
    }


def test_built_roads_are_reused_until_the_world_file_changes(tmp_path, monkeypatch):
    pbo = world_pbo(tmp_path, "testmap", coast(), roads=[("dz\\roads\\asf1_6.p3d", [(0.0, 0.0), (6.0, 0.0)])])
    first = terrain_roads(tmp_path, "testmap")

    def _no_rebuild(*_args):
        raise AssertionError("built again although nothing changed")

    real = road_map.read_prefix
    monkeypatch.setattr(road_map, "read_prefix", _no_rebuild)
    assert terrain_roads(tmp_path, "testmap") == first

    monkeypatch.setattr(road_map, "read_prefix", real)
    world_pbo(tmp_path, "testmap", coast(), roads=[("dz\\roads\\mud_6.p3d", [(0.0, 0.0), (6.0, 0.0)])])
    st = pbo.stat()
    os.utime(pbo, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert _decode(terrain_roads(tmp_path, "testmap")) == {"dirt": [[0.0, 0.0, 6.0, 0.0]]}


def test_no_world_file_or_no_road_list_gives_no_roads(tmp_path):
    assert terrain_roads(tmp_path, "testmap") is None
    world_pbo(tmp_path, "testmap", coast())  # heights, then not a road list
    assert terrain_roads(tmp_path, "testmap") is None
