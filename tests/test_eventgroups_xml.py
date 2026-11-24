"""Tests for app.xml.eventgroups_xml — parse, CRUD, attribute fidelity."""

from pathlib import Path

from tests.conftest import CE_1_29, bohemia_ce, ce_lines, ce_pick, write_xml

from app.xml.eventgroups_xml import (
    add_child,
    create_group,
    get_eventgroups_path,
    parse_eventgroups_file,
    remove_child,
    save_eventgroups_file,
    update_child,
)


SAMPLE_EVENTGROUPS = """\
<?xml version="1.0" encoding="UTF-8"?>
<eventgroupdef>
    <group name="StaticHeliCrash">
        <child type="Wreck_UH1Y" deloot="1" lootmax="0" lootmin="0" x="0.0" y="0.0" z="0.0" a="0.0"/>
        <child type="LandMineTrap" deloot="0" lootmax="3" lootmin="1" x="3.0" y="0.5" z="1.5" a="45.0"/>
    </group>
    <!-- START MOD: @Bikes -->
    <group name="BikeWreck">
        <child type="Bike" deloot="0" lootmax="1" lootmin="1" x="1.0" y="0.0" z="2.0" a="90.0"/>
    </group>
    <!-- END MOD: @Bikes -->
</eventgroupdef>
"""


def _parse(tmp_path: Path):
    return parse_eventgroups_file(write_xml(tmp_path / "cfgeventgroups.xml", SAMPLE_EVENTGROUPS))


def test_get_eventgroups_path(tmp_path: Path):
    assert get_eventgroups_path(tmp_path) is None
    p = write_xml(tmp_path / "cfgeventgroups.xml", SAMPLE_EVENTGROUPS)
    assert get_eventgroups_path(tmp_path) == p


def test_parse_groups_and_children(tmp_path: Path):
    gf = _parse(tmp_path)
    assert [e.name for e in gf.entries] == ["StaticHeliCrash", "BikeWreck"]

    first = gf.entries[0].children[0]
    assert (first.type, first.deloot, first.lootmin, first.lootmax) == ("Wreck_UH1Y", True, 0, 0)
    second = gf.entries[0].children[1]
    assert (second.deloot, second.lootmin, second.lootmax) == (False, 1, 3)
    assert (second.x, second.y, second.z, second.a) == (3.0, 0.5, 1.5, 45.0)


def test_malformed_attributes_fall_back_instead_of_raising(tmp_path: Path):
    """A hand-edited or mod-written file is not guaranteed to be numeric — the
    editor has to open it to be the thing that fixes it."""
    p = write_xml(
        tmp_path / "cfgeventgroups.xml",
        '<?xml version="1.0"?><eventgroupdef><group name="G">'
        '<child type="X" lootmin="oops" x="not-a-number"/>'
        "</group></eventgroupdef>",
    )
    child = parse_eventgroups_file(p).entries[0].children[0]
    assert child.lootmin == 0 and child.x == 0.0
    # A missing deloot is off, not a crash.
    assert child.deloot is False


def test_a_number_too_large_or_not_a_number_falls_back(tmp_path: Path):
    """`lootmax="1e999"` raised OverflowError out of the table's load, and
    `x="nan"` reached a JSON answer that cannot hold NaN."""
    p = write_xml(
        tmp_path / "cfgeventgroups.xml",
        '<?xml version="1.0"?><eventgroupdef><group name="G">'
        '<child type="X" lootmin="-inf" lootmax="1e999" x="nan" y="inf" z="1e999" a="-inf"/>'
        "</group></eventgroupdef>",
    )
    child = parse_eventgroups_file(p).entries[0].children[0]
    assert (child.lootmin, child.lootmax) == (0, 0)
    assert (child.x, child.y, child.z, child.a) == (0.0, 0.0, 0.0, 0.0)


def test_lootmin_accepts_a_float_string(tmp_path: Path):
    p = write_xml(
        tmp_path / "cfgeventgroups.xml",
        '<?xml version="1.0"?><eventgroupdef><group name="G">'
        '<child type="X" lootmin="2.0" lootmax="4.0"/>'
        "</group></eventgroupdef>",
    )
    child = parse_eventgroups_file(p).entries[0].children[0]
    assert (child.lootmin, child.lootmax) == (2, 4)


def test_update_child_round_trips(tmp_path: Path):
    gf = _parse(tmp_path)
    update_child(
        gf.entries[0].children[0],
        type="Wreck_Mi8",
        deloot=False,
        lootmin=2,
        lootmax=5,
        x=1.5,
        y=2.5,
        z=3.5,
        a=180.0,
    )
    save_eventgroups_file(gf)

    child = parse_eventgroups_file(gf.path).entries[0].children[0]
    assert child.type == "Wreck_Mi8"
    assert child.deloot is False
    assert (child.lootmin, child.lootmax) == (2, 5)
    assert (child.x, child.y, child.z, child.a) == (1.5, 2.5, 3.5, 180.0)


def test_update_child_keeps_unknown_attributes(tmp_path: Path):
    """Edits set the eight known attributes on the existing element rather than
    rebuilding it, so anything a mod or a newer DayZ puts there survives."""
    p = write_xml(
        tmp_path / "cfgeventgroups.xml",
        '<?xml version="1.0"?><eventgroupdef><group name="G">'
        '<child type="X" deloot="0" spawnsecondary="1"/>'
        "</group></eventgroupdef>",
    )
    gf = parse_eventgroups_file(p)
    update_child(
        gf.entries[0].children[0],
        type="Y", deloot=True, lootmin=0, lootmax=0, x=0.0, y=0.0, z=0.0, a=0.0,
    )
    save_eventgroups_file(gf)
    assert 'spawnsecondary="1"' in p.read_text()


def test_add_and_remove_child(tmp_path: Path):
    gf = _parse(tmp_path)
    entry = gf.entries[0]
    add_child(entry, type="Barrel", lootmin=1, lootmax=2, x=9.0)
    remove_child(entry, entry.children[0])
    save_eventgroups_file(gf)

    reloaded = parse_eventgroups_file(gf.path).entries[0]
    assert [c.type for c in reloaded.children] == ["LandMineTrap", "Barrel"]
    added = reloaded.children[-1]
    assert (added.lootmin, added.lootmax, added.x) == (1, 2, 9.0)


def test_remove_child_is_idempotent(tmp_path: Path):
    gf = _parse(tmp_path)
    entry = gf.entries[0]
    child = entry.children[0]
    remove_child(entry, child)
    remove_child(entry, child)  # already gone — must not raise
    assert len(entry.children) == 1


def test_create_group(tmp_path: Path):
    gf = _parse(tmp_path)
    entry = create_group(gf, "MyGroup")
    add_child(entry, type="Truck")
    save_eventgroups_file(gf)

    reloaded = parse_eventgroups_file(gf.path)
    assert [e.name for e in reloaded.entries] == ["StaticHeliCrash", "BikeWreck", "MyGroup"]
    assert [c.type for c in reloaded.entries[-1].children] == ["Truck"]


def test_save_keeps_mod_merge_comments(tmp_path: Path):
    """xml_merge.remove_mod_xml_blocks matches the markers as text — losing them
    on an editor save would make the merged group unremovable."""
    gf = _parse(tmp_path)
    add_child(gf.entries[0], type="Barrel")
    save_eventgroups_file(gf)

    content = gf.path.read_text()
    assert "<!-- START MOD: @Bikes -->" in content
    assert "<!-- END MOD: @Bikes -->" in content


def test_update_child_writes_only_what_changed(tmp_path: Path):
    """A child of Bohemia's Sakhal cfgeventgroups.xml, its line read at the
    pinned 1.29 commit: no loot attributes, no y."""
    lines = ce_lines(bohemia_ce(CE_1_29, "dayzOffline.sakhal/cfgeventgroups.xml"))
    (child,) = ce_pick(lines, '<child type="ScientificBriefcase"')
    p = write_xml(
        tmp_path / "cfgeventgroups.xml",
        f'<?xml version="1.0"?><eventgroupdef><group name="G">{child.strip()}</group></eventgroupdef>',
    )
    child = parse_eventgroups_file(p).entries[0].children[0]
    assert (child.lootmin, child.lootmax, child.y) == (None, None, None)

    assert update_child(child, type=child.type, deloot=False, lootmin=None, lootmax=None,
                        x=0.7, y=None, z=-1.25, a=180.0) is False
    assert child.element.attrib == {
        "type": "ScientificBriefcase", "spawnsecondary": "false", "x": "0.7", "z": "-1.25", "a": "180",
    }

    assert update_child(child, z=-2.0, y=1.5) is True
    assert child.element.get("z") == "-2.000000" and child.element.get("y") == "1.500000"
    update_child(child, y=None)
    assert "y" not in child.element.attrib
