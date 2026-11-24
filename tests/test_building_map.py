"""The building map's reader (plans/69): positions from the admin's own mapgrouppos.xml.

Every position here is invented. Never paste a real mapgrouppos.xml into a test:
a community map's mission files are its author's (plans/65).
"""

from __future__ import annotations

import os
from pathlib import Path

from app.maps import building_map
from app.maps.building_map import (
    building_points,
    find_mission_for_terrain,
    mapgrouppos_path,
)


def _write(mission: Path, *positions: tuple[float, float], name: str = "mapgrouppos.xml") -> Path:
    mission.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(
        f'    <group name="Land_Test_House" pos="{x} 120.5 {z}" rpy="0 0 0" a="0" />'
        for x, z in positions
    )
    path = mission / name
    path.write_text(f'<?xml version="1.0" encoding="UTF-8"?>\n<map>\n{rows}\n</map>\n', encoding="utf-8")
    return path


def test_positions_are_x_and_z_rounded_to_the_cell(tmp_path):
    mission = tmp_path / "empty.testmap"
    _write(mission, (1001.2, 2002.6), (3497.4, 12.0))
    assert building_points(mission) == [(1000, 2005), (3495, 10)]


def test_buildings_in_one_cell_are_one_point(tmp_path):
    mission = tmp_path / "empty.testmap"
    _write(mission, (500.0, 500.0), (501.0, 499.0), (502.4, 502.4), (800.0, 800.0))
    assert building_points(mission) == [(500, 500), (800, 800)]


def test_a_mission_without_the_file_has_no_buildings(tmp_path):
    mission = tmp_path / "empty.testmap"
    mission.mkdir()
    assert building_points(mission) == []
    assert building_points(tmp_path / "missing") == []


def test_the_file_name_is_matched_case_insensitively_but_never_in_a_subfolder(tmp_path):
    upper = tmp_path / "a.testmap"
    _write(upper, (10.0, 10.0), name="MapGroupPos.xml")
    assert mapgrouppos_path(upper) is not None
    assert building_points(upper) == [(10, 10)]

    nested = tmp_path / "b.testmap"
    _write(nested / "db", (10.0, 10.0))
    assert mapgrouppos_path(nested) is None


def test_an_edited_file_is_read_again(tmp_path):
    mission = tmp_path / "empty.testmap"
    path = _write(mission, (100.0, 100.0))
    assert building_points(mission) == [(100, 100)]

    _write(mission, (100.0, 100.0), (900.0, 900.0))
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert building_points(mission) == [(100, 100), (900, 900)]


def test_an_unchanged_file_is_parsed_once(tmp_path, monkeypatch):
    mission = tmp_path / "empty.testmap"
    _write(mission, (100.0, 100.0))
    building_points(mission)

    def _no_second_read(*args, **kwargs):
        raise AssertionError("parsed again although the file did not change")

    monkeypatch.setattr(Path, "read_text", _no_second_read)
    assert building_points(mission) == [(100, 100)]


def test_garbage_positions_are_skipped(tmp_path):
    mission = tmp_path / "empty.testmap"
    mission.mkdir()
    (mission / "mapgrouppos.xml").write_text(
        '<map>\n<group name="A" pos="1e 2 3e" />\n<group name="B" pos="50 1 60" />\n</map>\n',
        encoding="utf-8",
    )
    assert building_points(mission) == [(50, 60)]


def test_the_running_mission_wins_when_it_is_for_the_terrain(tmp_path):
    mpmissions = tmp_path / "mpmissions"
    first = mpmissions / "aaa.banov"
    running = mpmissions / "dayzOffline.banov"
    first.mkdir(parents=True)
    running.mkdir()
    assert find_mission_for_terrain(mpmissions, "banov", running) == running
    assert find_mission_for_terrain(mpmissions, "banov", None) == first


def test_another_terrain_is_found_by_its_folder_name(tmp_path):
    mpmissions = tmp_path / "mpmissions"
    running = mpmissions / "dayzOffline.chernarusplus"
    running.mkdir(parents=True)
    (mpmissions / "empty.deerisle").mkdir()
    assert find_mission_for_terrain(mpmissions, "deerisle", running) == mpmissions / "empty.deerisle"
    assert find_mission_for_terrain(mpmissions, "namalsk", running) is None
    assert find_mission_for_terrain(tmp_path / "nowhere", "namalsk", None) is None


def test_no_map_image_ships():
    """A map's picture is its author's work, so zsm ships none."""
    from pathlib import Path

    assert not list((Path(__file__).resolve().parent.parent / "app" / "data").glob("**/*.jpg"))


def teardown_function(_fn):
    building_map._cache.clear()


def test_a_position_that_is_not_a_finite_number_is_skipped_and_counted(tmp_path, caplog):
    """NB-1: `1e999` reads as infinity, and `/buildings` answered 500 for the
    whole terrain. Bad entries are skipped, the rest still draws."""
    import logging

    mission = tmp_path / "empty.testmap"
    mission.mkdir()
    rows = [
        '<group name="Land_A" pos="1e999 0 5" />',
        '<group name="Land_B" pos="-1e999 0 5" />',
        '<group name="Land_C" pos="nan 0 5" />',
        '<group name="Land_D" pos="5 0 inf" />',
        '<group name="Land_E" rpy="0 0 0" />',  # no pos at all
        '<group name="Land_F" pos="5 0" />',
        '<group name="Land_G" pos="five 0 5" />',
        '<group name="Land_H" pos="10 0 10" />',
    ]
    (mission / "mapgrouppos.xml").write_text("<map>\n" + "\n".join(rows) + "\n</map>\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="app.maps.building_map"):
        assert building_points(mission) == [(10, 10)]
    assert "Skipped 7 building(s)" in caplog.text


def test_a_file_of_tags_that_never_close_is_read_in_one_pass(tmp_path):
    """Each `<group ` used to be scanned to the end of the file: 560 KB took
    about 15 s, and no other request was answered meanwhile."""
    import time

    mission = tmp_path / "dayzOffline.testmap"
    mission.mkdir()
    (mission / "mapgrouppos.xml").write_text(
        "<map>" + "<group " * 80_000 + '<group pos="10 0 20" />', encoding="utf-8",
    )
    start = time.monotonic()
    assert building_points(mission) == [(10, 20)]
    assert time.monotonic() - start < 3


def test_building_groups_keep_each_buildings_name(tmp_path):
    """The loot layer (plans/82) joins a building to its mapgroupproto group by name."""
    from app.maps.building_map import building_groups

    mission = tmp_path / "empty.testmap"
    mission.mkdir()
    (mission / "mapgrouppos.xml").write_text(
        "<map>\n"
        '<group name="Land_Shed" pos="100 1 100" />\n'
        '<group name="Land_Shed" pos="101 1 99" />\n'
        '<group name="LAND_Barn" pos="500 1 600" />\n'
        '<group name="Land_Shed" pos="900 1 900" />\n'
        "</map>\n",
        encoding="utf-8",
    )
    assert building_groups(mission) == {
        "land_shed": [(100, 100), (900, 900)],
        "land_barn": [(500, 600)],
    }


def test_building_groups_and_points_come_from_one_read(tmp_path, monkeypatch):
    from app.maps.building_map import building_groups

    mission = tmp_path / "empty.testmap"
    _write(mission, (100.0, 100.0), (300.0, 300.0))
    assert building_points(mission) == [(100, 100), (300, 300)]

    def _no_second_read(*args, **kwargs):
        raise AssertionError("parsed again although the file did not change")

    monkeypatch.setattr(Path, "read_text", _no_second_read)
    assert building_groups(mission) == {"land_test_house": [(100, 100), (300, 300)]}


def test_a_mapgrouppos_over_the_size_limit_is_not_read(tmp_path, monkeypatch, caplog):
    import logging

    mission = tmp_path / "empty.testmap"
    _write(mission, (100.0, 100.0))
    monkeypatch.setattr(building_map, "MAX_MAPGROUPPOS_BYTES", 10)
    with caplog.at_level(logging.WARNING, logger="app.maps.building_map"):
        assert building_points(mission) == []
        assert building_map.building_groups(mission) == {}
    assert "over the 10 byte limit" in caplog.text
