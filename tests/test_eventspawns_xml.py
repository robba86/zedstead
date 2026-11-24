"""Tests for cli.eventspawns_xml — parse, CRUD, validation, round-trip."""

from pathlib import Path

from tests.conftest import CE_1_29, bohemia_ce, ce_lines, ce_pick, write_xml

from app.xml.eventspawns_xml import (
    add_position,
    auto_shift_position,
    distance,
    duplicate_position,
    find_exact_duplicates,
    find_nearby_positions,
    parse_eventspawns_file,
    remove_position,
    save_eventspawns_file,
    update_position,
)


SAMPLE_EVENTSPAWNS = """\
<?xml version="1.0" encoding="UTF-8"?>
<eventspawns>
    <event name="StaticHeliCrash">
        <pos x="4500.000000" z="9200.000000" a="180.000000"/>
        <pos x="6700.000000" z="3100.000000" a="-1.000000"/>
        <pos x="11000.000000" z="7800.000000" a="90.000000"/>
    </event>
    <event name="StaticContamination">
        <pos x="7500.500000" z="5000.500000" a="0.000000"/>
    </event>
</eventspawns>
"""


def test_parse_eventspawns_file(tmp_path: Path):
    p = write_xml(tmp_path / "cfgeventspawns.xml", SAMPLE_EVENTSPAWNS)
    ef = parse_eventspawns_file(p)
    assert len(ef.entries) == 2

    heli = ef.entries[0]
    assert heli.name == "StaticHeliCrash"
    assert len(heli.positions) == 3
    assert heli.positions[0].x == 4500.0
    assert heli.positions[0].z == 9200.0
    assert heli.positions[0].a == 180.0
    assert heli.positions[1].a == -1.0

    contam = ef.entries[1]
    assert contam.name == "StaticContamination"
    assert len(contam.positions) == 1
    assert contam.positions[0].x == 7500.5


def test_add_position(tmp_path: Path):
    p = write_xml(tmp_path / "cfgeventspawns.xml", SAMPLE_EVENTSPAWNS)
    ef = parse_eventspawns_file(p)
    entry = ef.entries[0]
    assert len(entry.positions) == 3
    new_pos = add_position(entry, 1000.0, 2000.0, 45.0)
    assert len(entry.positions) == 4
    assert new_pos.x == 1000.0
    assert new_pos.z == 2000.0
    assert new_pos.a == 45.0


def test_remove_position(tmp_path: Path):
    p = write_xml(tmp_path / "cfgeventspawns.xml", SAMPLE_EVENTSPAWNS)
    ef = parse_eventspawns_file(p)
    entry = ef.entries[0]
    pos = entry.positions[0]
    remove_position(entry, pos)
    assert len(entry.positions) == 2


def test_update_position(tmp_path: Path):
    p = write_xml(tmp_path / "cfgeventspawns.xml", SAMPLE_EVENTSPAWNS)
    ef = parse_eventspawns_file(p)
    pos = ef.entries[0].positions[0]
    update_position(pos, 9999.0, 8888.0, 270.0)
    assert pos.x == 9999.0
    assert pos.z == 8888.0
    assert pos.a == 270.0
    assert pos.element.get("x") == "9999.000000"


def test_duplicate_position(tmp_path: Path):
    p = write_xml(tmp_path / "cfgeventspawns.xml", SAMPLE_EVENTSPAWNS)
    ef = parse_eventspawns_file(p)
    entry = ef.entries[1]
    orig = entry.positions[0]
    dup = duplicate_position(entry, orig)
    assert len(entry.positions) == 2
    assert dup.x == orig.x
    assert dup.z == orig.z
    assert dup.a == orig.a


def test_distance():
    assert distance(0, 0, 3, 4) == 5.0
    assert distance(1, 1, 1, 1) == 0.0


def test_find_exact_duplicates(tmp_path: Path):
    p = write_xml(tmp_path / "cfgeventspawns.xml", SAMPLE_EVENTSPAWNS)
    ef = parse_eventspawns_file(p)
    entry = ef.entries[1]
    duplicate_position(entry, entry.positions[0])
    pairs = find_exact_duplicates(entry.positions)
    assert len(pairs) == 1
    assert pairs[0] == (0, 1)


def test_find_nearby_positions(tmp_path: Path):
    p = write_xml(tmp_path / "cfgeventspawns.xml", SAMPLE_EVENTSPAWNS)
    ef = parse_eventspawns_file(p)
    entry = ef.entries[0]
    # positions[0] = (4500, 9200), positions[1] = (6700, 3100)
    # They are far apart, so searching near positions[0] should find nothing nearby at 50m
    nearby = find_nearby_positions(
        entry.positions, 4500.0, 9200.0, min_distance=50.0, exclude_index=0,
    )
    assert len(nearby) == 0

    # Add a position close to positions[0]
    add_position(entry, 4510.0, 9210.0, 0.0)
    nearby = find_nearby_positions(
        entry.positions, 4500.0, 9200.0, min_distance=50.0, exclude_index=0,
    )
    assert len(nearby) == 1


def test_auto_shift_position():
    new_x, new_z = auto_shift_position(100.0, 100.0, 100.0, 100.0, 100.0)
    # Coincident points should shift north (dz=1)
    assert new_x == 100.0
    assert new_z == 200.0


def test_round_trip(tmp_path: Path):
    p = write_xml(tmp_path / "cfgeventspawns.xml", SAMPLE_EVENTSPAWNS)
    ef = parse_eventspawns_file(p)
    add_position(ef.entries[0], 1234.0, 5678.0, 90.0)
    update_position(ef.entries[1].positions[0], 100.0, 200.0, 0.0)
    save_eventspawns_file(ef)

    ef2 = parse_eventspawns_file(p)
    assert len(ef2.entries[0].positions) == 4
    assert ef2.entries[0].positions[3].x == 1234.0
    assert ef2.entries[1].positions[0].x == 100.0
    assert ef2.entries[1].positions[0].z == 200.0


def stock_positions() -> str:
    """Lines of Bohemia's Chernarus cfgeventspawns.xml, read at the pinned 1.29
    commit, each as the file writes it: two of StaticSantaCrash's positions
    (with a height, and without) and one of StaticBonfire's (no angle)."""
    lines = ce_lines(bohemia_ce(CE_1_29, "dayzOffline.chernarusplus/cfgeventspawns.xml"))
    return "".join(ce_pick(
        lines, "<?xml", "<eventposdef>",
        'name="StaticSantaCrash"', 'x="2593.47"', 'x="8483.96"', "</event>",
        'name="StaticBonfire"', 'x="8098.73"', "</event>",
        "</eventposdef>",
    ))


def test_parse_reads_height_and_whether_there_is_an_angle(tmp_path: Path):
    p = write_xml(tmp_path / "cfgeventspawns.xml", stock_positions())
    santa, bonfire = parse_eventspawns_file(p).entries
    assert (santa.positions[0].y, santa.positions[1].y) == (201.66, None)
    assert santa.positions[0].has_a and not bonfire.positions[0].has_a
    assert bonfire.positions[0].a == 0.0


def test_update_position_writes_only_what_changed(tmp_path: Path):
    p = write_xml(tmp_path / "cfgeventspawns.xml", stock_positions())
    santa, bonfire = parse_eventspawns_file(p).entries
    pos = bonfire.positions[0]

    assert update_position(pos, pos.x, pos.z, 0.0) is False   # an absent a reads 0
    assert pos.element.attrib == {"x": "8098.73", "z": "13023.365234"}

    assert update_position(pos, 8100.0, pos.z) is True
    assert pos.element.attrib == {"x": "8100.000000", "z": "13023.365234"}

    height = santa.positions[0]
    update_position(height, 2600.0, height.z)                 # y is kept unless given
    assert height.element.get("y") == "201.66"
    update_position(height, height.x, height.z, y=None)
    assert "y" not in height.element.attrib


def test_duplicate_position_copies_the_attributes_as_written(tmp_path: Path):
    p = write_xml(tmp_path / "cfgeventspawns.xml", stock_positions())
    santa = parse_eventspawns_file(p).entries[0]
    dup = duplicate_position(santa, santa.positions[0])
    assert dup.element.attrib == {"x": "2593.47", "z": "5094.11", "a": "0", "y": "201.66"}
    assert dup.y == 201.66
