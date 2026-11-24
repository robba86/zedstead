"""The forest and the gravel and dirt roads from a mission's cluster files (ROADMAP 50).

Every file here is invented in the shape of a ``mapgroupcluster*.xml``; no
line comes from Bohemia's missions.
"""

from __future__ import annotations

import gzip
import json
import math

import pytest
from fastapi.testclient import TestClient

from app.maps import cluster_map
from app.maps.cluster_map import (
    FOREST_CELL,
    cluster_layers,
    mission_forest,
    mission_roads,
    piece_length,
    road_kind,
    snap_ends,
)
from tests.test_web_map_editor import _login, web_app  # noqa: F401  (fixture)


def _cluster(folder, name, *tags):
    """Write an invented cluster file of *tags* into *folder*."""
    folder.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"    {t}" for t in tags)
    (folder / name).write_text(f'<?xml version="1.0"?>\n<map>\n{body}\n</map>\n', encoding="utf-8")


def _group(name, x, z, a=0.0):
    return f'<group name="{name}" pos="{x} 10.0 {z}" a="{a}" />'


def _gz_json(body):
    return json.loads(gzip.decompress(body))


@pytest.fixture(autouse=True)
def _fresh_cache():
    cluster_map._cache.clear()
    yield
    cluster_map._cache.clear()


def test_a_piece_length_comes_from_its_shape():
    assert piece_length("25") == 25.0
    assert piece_length("6ko") == 6.0
    # 10 degrees of a 25 m radius, as its chord.
    assert piece_length("1025") == pytest.approx(2 * 25 * math.sin(math.radians(5)))
    assert piece_length("11k") == piece_length("11000")
    assert piece_length("999") is None


def test_a_road_kind_comes_from_the_family():
    assert road_kind("roadgrav_25") == "gravel"
    assert road_kind("roadmud_7100") == "dirt"
    assert road_kind("pathdirt2xx_6") == "dirt"
    assert road_kind("pathd_12") == "path"
    assert road_kind("quarryxx_25") == "dirt"
    assert road_kind("piceaabies_stump") is None


def test_ends_within_the_snap_distance_become_one_point():
    pieces = [[(0.0, 0.0), (10.0, 0.0)], [(11.0, 0.0), (20.0, 0.0)], [(30.0, 0.0), (30.5, 0.0)]]
    snapped = snap_ends(pieces)
    assert snapped[0][1] == snapped[1][0] == (10.0, 0.0)
    # A piece shorter than the snap distance collapses and is dropped.
    assert len(snapped) == 2


def test_two_pieces_in_a_row_become_one_road_and_trees_become_forest(tmp_path):
    mission = tmp_path / "dayzOffline.sakhal"
    _cluster(
        mission, "mapgroupcluster.xml",
        # Two 25 m gravel pieces along x, centred 25 m apart: one line, 0..50 m.
        _group("RoadGrav_25", 12.5, 100.0),
        _group("RoadGrav_25", 37.5, 100.0),
        _group("PiceaAbies_3d", 10.0, 10.0),
        _group("PiceaAbies_stump", 20.0, 20.0),
        _group("BetulaPendula_fallen", 60.0, 10.0),
        _group("HaybaleDecayed", 70.0, 10.0),
    )
    layers = cluster_layers(mission)
    roads = _gz_json(layers.roads)["roads"]
    assert roads == {"gravel": [[0.0, 100.0, 25.0, 100.0, 50.0, 100.0]]}
    forest = _gz_json(layers.forest)
    assert forest["cell"] == FOREST_CELL
    half = FOREST_CELL // 2
    # Two trees in the first square, one in the next; the hay bale is no tree.
    assert forest["points"] == [half, half, FOREST_CELL + half, half]
    assert forest["counts"] == [2, 1]
    # The fir and the stump are conifers (the map's icon), the birch is not.
    assert forest["conifers"] == [2, 0]


def test_the_angle_turns_a_piece(tmp_path):
    mission = tmp_path / "dayzOffline.sakhal"
    _cluster(mission, "mapgroupcluster01.xml", _group("PathD_12", 100.0, 100.0, a=90))
    line = _gz_json(cluster_layers(mission).roads)["roads"]["path"][0]
    assert line == pytest.approx([100.0, 94.0, 100.0, 106.0])


def test_bad_tags_are_skipped_not_fatal(tmp_path):
    mission = tmp_path / "dayzOffline.sakhal"
    _cluster(
        mission, "mapgroupcluster.xml",
        '<group name="RoadGrav_25" pos="1e999 0 5" a="0" />',
        '<group name="RoadGrav_25" pos="nope" a="0" />',
        '<group name="RoadGrav_25" pos="5 0 5" a="inf" />',
        _group("RoadGrav_777", 5.0, 5.0),
        '<group pos="5 0 5" a="0" />',
        _group("PiceaAbies_3d", 5.0, 5.0),
    )
    layers = cluster_layers(mission)
    assert layers.roads is None
    assert _gz_json(layers.forest)["counts"] == [1]


def test_only_official_maps_are_read(tmp_path):
    mission = tmp_path / "custom.deerisle"
    _cluster(mission, "mapgroupcluster.xml", _group("RoadGrav_25", 12.5, 0.0), _group("PiceaAbies_3d", 5.0, 5.0))
    assert mission_roads("deerisle", mission) is None
    assert mission_forest("deerisle", mission) is None
    assert cluster_map._cache == {}


def test_an_edited_file_is_read_again(tmp_path):
    mission = tmp_path / "dayzOffline.sakhal"
    _cluster(mission, "mapgroupcluster.xml", _group("PiceaAbies_3d", 5.0, 5.0))
    assert _gz_json(mission_forest("sakhal", mission))["counts"] == [1]
    _cluster(mission, "mapgroupcluster.xml", _group("PiceaAbies_3d", 5.0, 5.0), _group("PiceaAbies_3d", 6.0, 6.0))
    assert _gz_json(mission_forest("sakhal", mission))["counts"] == [2]


def test_sakhal_gets_its_roads_and_forest_from_the_mission(web_app, tmp_mission_dir):  # noqa: F811
    """Sakhal's world file gives no roads, so /roads falls back to the mission."""
    mission = tmp_mission_dir.parent / "dayzOffline.sakhal"
    _cluster(
        mission, "mapgroupcluster.xml",
        _group("RoadMud_25", 12.5, 100.0),
        _group("PiceaAbies_3d", 10.0, 10.0),
    )
    with TestClient(web_app) as client:
        _login(client)
        info = client.get("/api/map-editor/map-info?terrain=sakhal").json()
        roads = client.get("/api/map-editor/roads?terrain=sakhal")
        forest = client.get("/api/map-editor/forest?terrain=sakhal")
        cherno_forest = client.get("/api/map-editor/forest").status_code
    assert (info["has_roads"], info["roads_source"], info["has_forest"]) == (True, "mission", True)
    assert roads.status_code == 200
    assert roads.json() == {"roads": {"dirt": [[0.0, 100.0, 25.0, 100.0]]}}
    assert forest.status_code == 200
    assert forest.json()["counts"] == [1]
    # The running Chernarus mission in the fixture has no cluster files.
    assert cherno_forest == 404
