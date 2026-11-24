"""Tests for cli.events_xml — parse, save, CRUD, round-trip."""

from pathlib import Path

from tests.conftest import write_xml

from app.xml.events_xml import (
    add_event_child,
    create_event,
    parse_events_file,
    remove_event_child,
    save_events_file,
    set_child_values,
    set_event_active,
    set_event_flag,
    set_event_limit,
    set_event_numeric,
    set_event_position,
    set_event_secondary,
)


SAMPLE_EVENTS = """\
<?xml version="1.0" encoding="UTF-8"?>
<events>
    <event name="StaticHeliCrash">
        <nominal>3</nominal>
        <min>1</min>
        <max>5</max>
        <lifetime>1800</lifetime>
        <restock>600</restock>
        <saferadius>500</saferadius>
        <distanceradius>300</distanceradius>
        <cleanupradius>200</cleanupradius>
        <secondary>HeliCrashWreck</secondary>
        <flags deletable="0" init_random="1" remove_damaged="1"/>
        <position>fixed</position>
        <limit>mixed</limit>
        <active>1</active>
        <children>
            <child lootmax="10" lootmin="5" max="3" min="1" type="Loot_HeliCrash"/>
        </children>
    </event>
    <event name="InfectedHorde">
        <nominal>10</nominal>
        <min>5</min>
        <max>15</max>
        <lifetime>300</lifetime>
        <restock>120</restock>
        <saferadius>0</saferadius>
        <distanceradius>0</distanceradius>
        <cleanupradius>100</cleanupradius>
        <flags deletable="1" init_random="0" remove_damaged="0"/>
        <position>player</position>
        <limit>custom</limit>
        <active>1</active>
    </event>
</events>
"""


def test_parse_events_file(tmp_path: Path):
    p = write_xml(tmp_path / "events.xml", SAMPLE_EVENTS)
    ef = parse_events_file(p)
    assert len(ef.entries) == 2

    heli = ef.entries[0]
    assert heli.name == "StaticHeliCrash"
    assert heli.nominal == 3
    assert heli.min == 1
    assert heli.max == 5
    assert heli.lifetime == 1800
    assert heli.saferadius == 500
    assert heli.secondary == "HeliCrashWreck"
    assert heli.flags["init_random"] == 1
    assert heli.position == "fixed"
    assert heli.limit == "mixed"
    assert heli.active is True
    assert len(heli.children) == 1
    assert heli.children[0].type == "Loot_HeliCrash"
    assert heli.children[0].lootmax == 10


def test_set_event_numeric(tmp_path: Path):
    p = write_xml(tmp_path / "events.xml", SAMPLE_EVENTS)
    ef = parse_events_file(p)
    entry = ef.entries[0]
    set_event_numeric(entry, "nominal", 20)
    assert entry.nominal == 20


def test_set_event_flag(tmp_path: Path):
    p = write_xml(tmp_path / "events.xml", SAMPLE_EVENTS)
    ef = parse_events_file(p)
    entry = ef.entries[0]
    set_event_flag(entry, "deletable", 1)
    assert entry.flags["deletable"] == 1


def test_set_event_position_limit(tmp_path: Path):
    p = write_xml(tmp_path / "events.xml", SAMPLE_EVENTS)
    ef = parse_events_file(p)
    entry = ef.entries[0]
    set_event_position(entry, "player")
    assert entry.position == "player"
    set_event_limit(entry, "child")
    assert entry.limit == "child"


def test_set_event_active(tmp_path: Path):
    p = write_xml(tmp_path / "events.xml", SAMPLE_EVENTS)
    ef = parse_events_file(p)
    entry = ef.entries[0]
    set_event_active(entry, False)
    assert entry.active is False


def test_set_event_secondary(tmp_path: Path):
    p = write_xml(tmp_path / "events.xml", SAMPLE_EVENTS)
    ef = parse_events_file(p)
    entry = ef.entries[0]
    set_event_secondary(entry, "NewWreck")
    assert entry.secondary == "NewWreck"


def test_a_new_event_has_no_empty_secondary(tmp_path: Path):
    # Every stock <secondary> names an event (/tmp/fresh events.xml, all three
    # maps); an empty one links to nothing.
    p = write_xml(tmp_path / "events.xml", SAMPLE_EVENTS)
    ef = parse_events_file(p)
    entry = create_event(ef, "NewEvent")
    assert entry.element.find("secondary") is None
    save_events_file(ef)
    assert "<secondary" not in p.read_text().split('name="NewEvent"')[1]


def test_secondary_goes_between_cleanupradius_and_flags(tmp_path: Path):
    p = write_xml(tmp_path / "events.xml", SAMPLE_EVENTS)
    ef = parse_events_file(p)
    entry = ef.entries[1]  # InfectedHorde has no <secondary>
    set_event_secondary(entry, "InfectedArmy")
    tags = [c.tag for c in entry.element]
    assert tags.index("secondary") == tags.index("cleanupradius") + 1
    assert tags.index("flags") == tags.index("secondary") + 1
    set_event_secondary(entry, "")
    assert entry.element.find("secondary") is None and entry.secondary == ""


def test_a_first_child_goes_in_bohemias_order(tmp_path: Path):
    p = write_xml(tmp_path / "events.xml", SAMPLE_EVENTS)
    ef = parse_events_file(p)
    entry = ef.entries[1]
    entry.element.remove(entry.element.find("saferadius"))
    set_event_numeric(entry, "saferadius", 50)
    tags = [c.tag for c in entry.element]
    assert tags.index("saferadius") == tags.index("restock") + 1
    add_event_child(entry, "Zombie", 1, 2, 0, 0)
    assert [c.tag for c in entry.element][-1] == "children"


def test_add_and_remove_child(tmp_path: Path):
    p = write_xml(tmp_path / "events.xml", SAMPLE_EVENTS)
    ef = parse_events_file(p)
    entry = ef.entries[1]  # InfectedHorde has no children
    assert len(entry.children) == 0

    child = add_event_child(entry, "Zombie_Soldier", 2, 5, 0, 3)
    assert len(entry.children) == 1
    assert child.type == "Zombie_Soldier"

    remove_event_child(entry, child)
    assert len(entry.children) == 0


def test_set_child_values(tmp_path: Path):
    p = write_xml(tmp_path / "events.xml", SAMPLE_EVENTS)
    ef = parse_events_file(p)
    child = ef.entries[0].children[0]
    set_child_values(child, 3, 8, 2, 15)
    assert child.min == 3
    assert child.max == 8
    assert child.lootmin == 2
    assert child.lootmax == 15


_ONE_CHILD = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    "<events>\n"
    '    <event name="VehicleTest">\n'
    "        <children>\n"
    '            <child lootmax="x" lootmin="5" max="3" min="1" type="OffroadHatchback"/>\n'
    "        </children>\n"
    "    </event>\n"
    "</events>\n"
)


def test_a_bad_child_attribute_does_not_zero_the_good_ones(tmp_path: Path):
    p = tmp_path / "events.xml"
    p.write_text(_ONE_CHILD, encoding="utf-8")
    child = parse_events_file(p).entries[0].children[0]
    assert (child.min, child.max, child.lootmin, child.lootmax) == (1, 3, 5, 0)


def test_set_child_values_writes_only_what_changed(tmp_path: Path):
    """spawn_test_ops passes lootmin and lootmax back as read: the bad
    ``lootmax`` must stay as the file has it."""
    p = tmp_path / "events.xml"
    p.write_text(_ONE_CHILD, encoding="utf-8")
    ef = parse_events_file(p)
    child = ef.entries[0].children[0]
    set_child_values(child, 0, 4, child.lootmin, child.lootmax)
    save_events_file(ef)
    assert p.read_text(encoding="utf-8") == _ONE_CHILD.replace('max="3" min="1"', 'max="4" min="0"')


def test_round_trip(tmp_path: Path):
    p = write_xml(tmp_path / "events.xml", SAMPLE_EVENTS)
    ef = parse_events_file(p)
    set_event_numeric(ef.entries[0], "nominal", 42)
    set_event_active(ef.entries[1], False)
    save_events_file(ef)

    ef2 = parse_events_file(p)
    assert ef2.entries[0].nominal == 42
    assert ef2.entries[1].active is False


def test_a_table_save_keeps_comments_and_merge_markers(tmp_path: Path):
    from app.web.editors.ce_tables import events_load, events_save

    p = write_xml(
        tmp_path / "events.xml",
        "<events>\n"
        "    <!-- raised for the weekend event -->\n"
        '    <event name="AnimalBear">\n'
        "        <nominal>5</nominal><min>2</min><max>8</max><lifetime>180</lifetime>\n"
        "        <restock>0</restock><saferadius>200</saferadius><distanceradius>0</distanceradius>\n"
        "        <cleanupradius>0</cleanupradius><active>1</active>\n"
        "    </event>\n"
        "    <!-- START MOD: @user:extra -->\n"
        '    <event name="ExtraEvent">\n'
        "        <nominal>1</nominal><min>1</min><max>1</max><lifetime>180</lifetime>\n"
        "        <restock>0</restock><saferadius>0</saferadius><distanceradius>0</distanceradius>\n"
        "        <cleanupradius>0</cleanupradius><active>1</active>\n"
        "    </event>\n"
        "    <!-- END MOD: @user:extra -->\n"
        "</events>\n",
    )
    rows = events_load(p)["rows"]
    rows[0]["values"]["nominal"] = 7
    events_save(p, rows)

    content = p.read_text()
    assert "<nominal>7</nominal>" in content
    for comment in ("<!-- raised for the weekend event -->", "<!-- START MOD: @user:extra -->", "<!-- END MOD: @user:extra -->"):
        assert comment in content
