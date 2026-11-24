"""The nested part of the XML tables (plans/76 step 3, `editors/_xml_nested.py`).

types.xml, events.xml and cfgspawnabletypes.xml rows carry their children as
a tree. A save writes the edit into the elements in place: every comment (the
START/END MOD markers `xml_merge` matches as text, a parked mod) and every
element outside the edited one stays byte for byte as the file had it.
"""

from __future__ import annotations

import copy
import difflib
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from app.web.editors import ce_tables
from app.xml.xml_utils import load_xml, save_xml
from tests.conftest import CE_1_29, bohemia_ce, ce_block, ce_lines, ce_pick

# The parked mod is a comment holding XML, the way a disabled mod's entries sit.
TYPES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<types>
    <!-- Stock items -->
    <type name="SparkPlug">
        <nominal>60</nominal>
        <lifetime>14400</lifetime>
        <restock>0</restock>
        <min>40</min>
        <quantmin>-1</quantmin>
        <quantmax>-1</quantmax>
        <cost>100</cost>
        <flags count_in_cargo="0" count_in_hoarder="0" count_in_map="1" count_in_player="0" crafted="0" deloot="0" modflag="7"/>
        <category name="tools"/>
        <tag name="shelves"/>
        <usage name="Industrial" note="kept"/>
        <usage user="TownVillage"/>
        <usage name="Farm"/>
        <value name="Tier1"/>
        <extra kept="yes"/>
    </type>
    <!-- START MOD: @Bikes -->
    <type name="Bike">
        <nominal>5</nominal>
        <usage name="Town"/>
    </type>
    <!-- END MOD: @Bikes -->
    <!-- DISABLED @Cars: <type name="Car"><nominal>1</nominal></type> -->
    <type name="Apple">
        <nominal>10</nominal>
        <lifetime>7200</lifetime>
        <restock>0</restock>
        <min>5</min>
        <quantmin>-1</quantmin>
        <quantmax>-1</quantmax>
        <cost>100</cost>
        <category name="food"/>
    </type>
</types>
"""

EVENTS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<events>
    <event name="StaticHeliCrash">
        <nominal>3</nominal>
        <min>1</min>
        <max>3</max>
        <lifetime>2100</lifetime>
        <restock>0</restock>
        <saferadius>1000</saferadius>
        <distanceradius>1000</distanceradius>
        <cleanupradius>1000</cleanupradius>
        <secondary>InfectedArmyHard</secondary>
        <flags deletable="1" init_random="0" remove_damaged="0"/>
        <position>fixed</position>
        <limit>child</limit>
        <active>1</active>
        <children>
            <child lootmax="15" lootmin="10" max="3" min="1" type="Wreck_UH1Y" modded="1"/>
            <!-- the Mi-8 -->
            <child lootmax="15" lootmin="10" max="1" min="0" type="Wreck_Mi8_Crashed"/>
        </children>
    </event>
    <!-- START MOD: @Wrecks -->
    <event name="ModWreck">
        <nominal>1</nominal>
        <active>1</active>
    </event>
    <!-- END MOD: @Wrecks -->
    <!-- DISABLED @Old: <event name="OldWreck"><nominal>1</nominal></event> -->
    <event name="AnimalBear">
        <nominal>0</nominal>
        <min>2</min>
        <max>2</max>
        <lifetime>180</lifetime>
        <restock>0</restock>
        <active>1</active>
    </event>
</events>
"""

SPAWNABLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<spawnabletypes>
    <type name="AKM">
        <damage min="0.10" max="0.60"/>
        <tag name="shelves"/>
        <attachments chance="0.30" modattr="x">
            <item name="AK_RailHndgrd" chance="0.50"/>
            <!-- a handguard -->
            <item name="AK_PlasticBttstck" chance="1"/>
        </attachments>
        <cargo chance="0.20" preset="foodSmall"/>
    </type>
    <!-- START MOD: @Guns -->
    <type name="ModGun">
        <attachments chance="1.00">
            <item name="ModScope" chance="0.10"/>
        </attachments>
    </type>
    <!-- END MOD: @Guns -->
    <!-- DISABLED @Old: <type name="OldGun"/> -->
    <type name="Barrel_Blue">
        <hoarder/>
    </type>
</spawnabletypes>
"""


def _normalised(path: Path, text: str) -> str:
    """Write *text* as it is and return it. `save_xml` keeps a file's own
    layout, so "unchanged" is measured against the original bytes; the name
    is from when it had to put the file through the writer first."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path.read_text()


def _without(text: str, tag: str, name: str) -> str:
    """*text* with the one ``<tag name="name">…</tag>`` block cut out."""
    pattern = re.compile(rf'\n[ \t]+<{tag} name="{re.escape(name)}">.*?\n[ \t]+</{tag}>', re.S)
    cut, n = pattern.subn("", text)
    assert n == 1
    return cut


def _rows_with(load: dict, row_id: str, edit) -> list[dict]:
    """Every row of *load*, the row *row_id* (an id, or the name of a
    spawnable type, whose rows are addressed by position) carrying
    `edit(tree)` as its `$nested`, against the tree as loaded."""
    rows = []
    for row in load["rows"]:
        values = dict(row["values"])
        if row["id"] == row_id or (row["id"].startswith("#") and values.get("name") == row_id):
            tree = copy.deepcopy(row["nested"])
            edit(tree)
            values["$nested"] = tree
            values["$nested_base"] = row["nested"]
        rows.append({"id": row["id"], "values": values})
    return rows


@pytest.fixture()
def versions(monkeypatch):
    """The files a save took a version of."""
    taken: list[Path] = []
    monkeypatch.setattr(ce_tables, "snapshot", lambda path, trigger="": taken.append(path))
    return taken


# ---------------------------------------------------------------------------
# types.xml (the custom/ table and the Types page share the shape)
# ---------------------------------------------------------------------------


def test_a_type_row_carries_its_lists_and_flags(tmp_path):
    path = tmp_path / "custom" / "types.xml"
    _normalised(path, TYPES_XML)
    row = ce_tables.types_load(path)["rows"][0]
    assert row["nested"] == {
        "usage": ["Industrial", "Farm"],
        "value": ["Tier1"],
        "flags": {
            "count_in_cargo": 0, "count_in_hoarder": 0, "count_in_map": 1,
            "count_in_player": 0, "crafted": 0, "deloot": 0, "modflag": 7,
        },
    }


def test_a_types_tree_edit_keeps_comments_order_and_what_it_does_not_know(tmp_path, versions):
    path = tmp_path / "custom" / "types.xml"
    before = _normalised(path, TYPES_XML)

    def edit(tree):
        tree["usage"] = ["Farm", "Industrial", "Coast"]  # reordered, one added
        tree["value"] = []
        tree["flags"]["crafted"] = "1"

    ce_tables.types_save(path, _rows_with(ce_tables.types_load(path), "SparkPlug", edit))
    after = path.read_text()

    assert _without(after, "type", "SparkPlug") == _without(before, "type", "SparkPlug")
    spark = re.search(r'<type name="SparkPlug">.*?</type>', after, re.S).group()
    # The usage slots take the new order; the user= group and <extra> stay.
    assert re.findall(r"<usage [^>]*>", spark) == [
        '<usage name="Farm"/>',
        '<usage user="TownVillage"/>',
        '<usage name="Industrial" note="kept"/>',
        '<usage name="Coast"/>',
    ]
    assert "<value" not in spark
    assert 'crafted="1" deloot="0" modflag="7"' in spark
    assert '<extra kept="yes"/>' in spark
    assert versions == [path]


def test_a_first_flag_and_usage_go_where_bohemia_puts_them(tmp_path, versions):
    path = tmp_path / "custom" / "types.xml"
    before = _normalised(path, TYPES_XML)

    def edit(tree):
        tree["usage"] = ["Village"]
        tree["flags"]["count_in_map"] = 1

    ce_tables.types_save(path, _rows_with(ce_tables.types_load(path), "Apple", edit))
    after = path.read_text()
    apple = re.search(r'<type name="Apple">.*?</type>', after, re.S).group()
    tags = re.findall(r"<(\w+)[ >/]", apple)
    assert tags[tags.index("cost") + 1:] == ["flags", "category", "usage"]
    assert '<flags count_in_cargo="0" count_in_hoarder="0" count_in_map="1"' in apple
    assert _without(after, "type", "Apple") == _without(before, "type", "Apple")


def test_an_untouched_types_tree_writes_nothing(tmp_path, versions):
    path = tmp_path / "custom" / "types.xml"
    before = _normalised(path, TYPES_XML)
    ce_tables.types_save(path, _rows_with(ce_tables.types_load(path), "Apple", lambda tree: None))
    assert path.read_text() == before


def test_a_stale_types_tree_is_refused_and_the_file_untouched(tmp_path, versions):
    path = tmp_path / "custom" / "types.xml"
    _normalised(path, TYPES_XML)
    rows = _rows_with(ce_tables.types_load(path), "SparkPlug", lambda tree: tree["value"].append("Tier4"))
    # Someone else changes the same list meanwhile.
    path.write_text(path.read_text().replace('<value name="Tier1"/>', '<value name="Tier2"/>'))
    on_disk = path.read_bytes()
    with pytest.raises(ValueError, match="SparkPlug: value changed in the file"):
        ce_tables.types_save(path, rows)
    assert path.read_bytes() == on_disk
    assert versions == []


def test_a_custom_types_row_naming_no_type_is_refused(tmp_path, versions):
    path = tmp_path / "custom" / "types.xml"
    _normalised(path, TYPES_XML)
    on_disk = path.read_bytes()
    rows = ce_tables.types_load(path)["rows"] + [{"id": "new-1-1", "values": {"name": "Brand_New"}}]
    with pytest.raises(ValueError, match="names no type in this file"):
        ce_tables.types_save(path, rows)
    assert path.read_bytes() == on_disk
    assert versions == []


def test_an_empty_name_is_refused(tmp_path, versions):
    path = tmp_path / "custom" / "types.xml"
    _normalised(path, TYPES_XML)
    rows = _rows_with(ce_tables.types_load(path), "SparkPlug", lambda tree: tree["usage"].append(" "))
    with pytest.raises(ValueError, match="usage: a name can't be left empty"):
        ce_tables.types_save(path, rows)
    assert versions == []


# ---------------------------------------------------------------------------
# events.xml
# ---------------------------------------------------------------------------


def test_an_event_row_carries_its_settings_flags_and_children(tmp_path):
    path = tmp_path / "db" / "events.xml"
    _normalised(path, EVENTS_XML)
    data = ce_tables.events_load(path)
    assert [c["value"] for c in data["nested_choices"]["position"]] == ["fixed", "player", "uniform"]
    # The tree draws an event's 0/1 flags as switches.
    assert data["nested_switches"] == ["flags"]
    nested = data["rows"][0]["nested"]
    assert nested["settings"] == {
        "saferadius": 1000, "distanceradius": 1000, "cleanupradius": 1000,
        "secondary": "InfectedArmyHard", "position": "fixed", "limit": "child",
    }
    assert nested["flags"] == {"deletable": 1, "init_random": 0, "remove_damaged": 0}
    assert nested["children"][0] == {
        "lootmax": 15, "lootmin": 10, "max": 3, "min": 1, "type": "Wreck_UH1Y", "modded": 1,
    }


def test_an_events_tree_edit_keeps_comments_order_and_what_it_does_not_know(tmp_path, versions):
    path = tmp_path / "db" / "events.xml"
    before = _normalised(path, EVENTS_XML)

    def edit(tree):
        uh1y, mi8 = tree["children"]
        tree["children"] = [mi8, dict(uh1y, max="5"), dict(uh1y, type="Wreck_Copy")]
        tree["flags"]["init_random"] = 1
        tree["settings"]["position"] = "player"

    ce_tables.events_save(path, _rows_with(ce_tables.events_load(path), "StaticHeliCrash", edit))
    after = path.read_text()

    assert _without(after, "event", "StaticHeliCrash") == _without(before, "event", "StaticHeliCrash")
    crash = re.search(r'<event name="StaticHeliCrash">.*?</event>', after, re.S).group()
    assert re.findall(r"<child [^>]*>|<!--[^>]*-->", crash) == [
        '<child lootmax="15" lootmin="10" max="1" min="0" type="Wreck_Mi8_Crashed"/>',
        "<!-- the Mi-8 -->",
        '<child lootmax="15" lootmin="10" max="5" min="1" type="Wreck_UH1Y" modded="1"/>',
        '<child lootmax="15" lootmin="10" max="3" min="1" type="Wreck_Copy" modded="1"/>',
    ]
    assert '<flags deletable="1" init_random="1" remove_damaged="0"/>' in crash
    assert "<position>player</position>" in crash
    assert versions == [path]


def test_an_events_tree_refuses_a_fraction_for_a_count(tmp_path, versions):
    path = tmp_path / "db" / "events.xml"
    _normalised(path, EVENTS_XML)

    def edit(tree):
        tree["children"][0]["max"] = "2.5"

    rows = _rows_with(ce_tables.events_load(path), "StaticHeliCrash", edit)
    with pytest.raises(ValueError, match=r"children\[1\]\.max"):
        ce_tables.events_save(path, rows)
    assert versions == []


def test_an_untouched_events_tree_writes_nothing(tmp_path, versions):
    path = tmp_path / "db" / "events.xml"
    before = _normalised(path, EVENTS_XML)
    ce_tables.events_save(path, _rows_with(ce_tables.events_load(path), "StaticHeliCrash", lambda tree: None))
    assert path.read_text() == before


def test_a_stale_events_tree_is_refused_over_the_api(client, sample_config, versions):
    db = sample_config.server_dir_path / "mpmissions" / "dayzOffline.chernarusplus" / "db"
    path = db / "events.xml"
    _normalised(path, EVENTS_XML)
    data = client.get("/api/configs/events").json()
    rows = _rows_with(data, "StaticHeliCrash", lambda tree: tree["children"].pop())
    path.write_text(path.read_text().replace('max="1" min="0"', 'max="2" min="0"'))
    on_disk = path.read_bytes()

    resp = client.put("/api/configs/events", json={"rows": rows})
    assert resp.status_code == 400
    assert "children changed in the file" in resp.json()["detail"]
    assert path.read_bytes() == on_disk
    assert versions == []


@pytest.fixture()
def client(sample_config):
    from fastapi.testclient import TestClient

    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password("testpass123")
    tclient = TestClient(server.create_app(sample_config))
    tclient.post("/api/auth/login", json={"password": "testpass123"})
    return tclient


# ---------------------------------------------------------------------------
# cfgspawnabletypes.xml
# ---------------------------------------------------------------------------


def test_a_spawnable_row_carries_its_groups(tmp_path):
    path = tmp_path / "cfgspawnabletypes.xml"
    _normalised(path, SPAWNABLE_XML)
    row = ce_tables.spawnabletypes_load(path)["rows"][0]
    assert row["values"]["groups"] == "attachments[2], cargo[foodSmall]"
    assert row["nested"]["groups"] == [
        {"kind": "attachments", "chance": 0.3, "modattr": "x", "items": [
            {"name": "AK_RailHndgrd", "chance": 0.5},
            {"name": "AK_PlasticBttstck", "chance": 1.0},
        ]},
        {"kind": "cargo", "chance": 0.2, "preset": "foodSmall", "items": []},
    ]


def test_a_spawnable_tree_edit_keeps_comments_order_and_what_it_does_not_know(tmp_path, versions):
    path = tmp_path / "cfgspawnabletypes.xml"
    before = _normalised(path, SPAWNABLE_XML)

    def edit(tree):
        attachments, cargo = tree["groups"]
        rail, stock = attachments["items"]
        # A chance the file wrote whole takes a fraction; a copied item.
        attachments["items"] = [rail, dict(stock, chance="0.25"), dict(rail, name="AK_WoodHndgrd")]
        cargo["kind"] = "attachments"
        tree["groups"] = [attachments, cargo, copy.deepcopy(attachments)]

    ce_tables.spawnabletypes_save(path, _rows_with(ce_tables.spawnabletypes_load(path), "AKM", edit))
    after = path.read_text()

    assert _without(after, "type", "AKM") == _without(before, "type", "AKM")
    akm = re.search(r'<type name="AKM">.*?</type>', after, re.S).group()
    lines = [line.strip() for line in akm.splitlines()]
    assert lines == [
        '<type name="AKM">',
        '<damage min="0.10" max="0.60"/>',
        '<tag name="shelves"/>',
        '<attachments chance="0.30" modattr="x">',
        '<item name="AK_RailHndgrd" chance="0.50"/>',
        "<!-- a handguard -->",
        '<item name="AK_PlasticBttstck" chance="0.25"/>',
        '<item name="AK_WoodHndgrd" chance="0.50"/>',
        "</attachments>",
        '<attachments chance="0.20" preset="foodSmall"/>',
        '<attachments chance="0.30" modattr="x">',
        '<item name="AK_RailHndgrd" chance="0.50"/>',
        "<!-- a handguard -->",
        '<item name="AK_PlasticBttstck" chance="0.25"/>',
        '<item name="AK_WoodHndgrd" chance="0.50"/>',
        "</attachments>",
        "</type>",
    ]
    assert versions == [path]


def test_an_untouched_spawnable_tree_writes_nothing(tmp_path, versions):
    path = tmp_path / "cfgspawnabletypes.xml"
    before = _normalised(path, SPAWNABLE_XML)
    ce_tables.spawnabletypes_save(path, _rows_with(ce_tables.spawnabletypes_load(path), "AKM", lambda tree: None))
    assert path.read_text() == before


def test_a_stale_spawnable_tree_is_refused_and_the_file_untouched(tmp_path, versions):
    path = tmp_path / "cfgspawnabletypes.xml"
    _normalised(path, SPAWNABLE_XML)
    rows = _rows_with(
        ce_tables.spawnabletypes_load(path), "AKM", lambda tree: tree["groups"].pop(),
    )
    path.write_text(path.read_text().replace('preset="foodSmall"', 'preset="foodBig"'))
    on_disk = path.read_bytes()
    with pytest.raises(ValueError, match="AKM: groups changed in the file"):
        ce_tables.spawnabletypes_save(path, rows)
    assert path.read_bytes() == on_disk
    assert versions == []


def test_a_group_is_attachments_or_cargo(tmp_path, versions):
    path = tmp_path / "cfgspawnabletypes.xml"
    _normalised(path, SPAWNABLE_XML)

    def edit(tree):
        tree["groups"][1]["kind"] = "script"

    rows = _rows_with(ce_tables.spawnabletypes_load(path), "AKM", edit)
    with pytest.raises(ValueError, match="attachments or cargo"):
        ce_tables.spawnabletypes_save(path, rows)
    assert versions == []


def test_an_empty_list_grows_from_its_template(tmp_path, versions):
    """An event with no <children> and a type with no groups had no way to get
    one in the browser: the template is what "Add" puts there."""
    path = tmp_path / "db" / "events.xml"
    _normalised(path, EVENTS_XML)
    load = ce_tables.events_load(path)
    assert load["nested_templates"]["children"]["type"] == ""

    def add_child(tree):
        tree["children"].append({**ce_tables.EVENT_CHILD_TEMPLATE, "type": "Animal_UrsusArctos", "max": "2"})

    ce_tables.events_save(path, _rows_with(load, "AnimalBear", add_child))
    bear = load_xml(path, preserve_comments=True).getroot().find("event[@name='AnimalBear']")
    (child,) = bear.find("children").findall("child")
    assert child.get("type") == "Animal_UrsusArctos" and child.get("max") == "2"

    spath = tmp_path / "cfgspawnabletypes.xml"
    _normalised(spath, SPAWNABLE_XML)
    sload = ce_tables.spawnabletypes_load(spath)

    def add_group(tree):
        group = copy.deepcopy(sload["nested_templates"]["groups"])
        group["items"][0]["name"] = "Battery9V"
        tree["groups"].append(group)

    ce_tables.spawnabletypes_save(spath, _rows_with(sload, "Barrel_Blue", add_group))
    barrel = load_xml(spath, preserve_comments=True).getroot().find("type[@name='Barrel_Blue']")
    assert [i.get("name") for i in barrel.find("attachments").findall("item")] == ["Battery9V"]


def test_a_new_entry_left_without_a_class_name_is_refused(tmp_path, versions):
    path = tmp_path / "db" / "events.xml"
    _normalised(path, EVENTS_XML)

    def add_blank(tree):
        tree["children"].append(dict(ce_tables.EVENT_CHILD_TEMPLATE))

    rows = _rows_with(ce_tables.events_load(path), "AnimalBear", add_blank)
    with pytest.raises(ValueError, match="child 1 needs a class name"):
        ce_tables.events_save(path, rows)
    assert versions == []


# ---------------------------------------------------------------------------
# A save keeps the file's own layout (measured against the original bytes)
# ---------------------------------------------------------------------------


def _changed_lines(before: str, after: str) -> list[str]:
    """The lines `difflib` marks as removed or added, with their sign."""
    return [
        line for line in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0)
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    ]


def _edit_events(tree):
    tree["flags"]["init_random"] = 1
    tree["children"][0]["max"] = "5"


def _edit_types(tree):
    tree["flags"]["crafted"] = 1
    tree["usage"] = ["Industrial", "Coast"]


def _edit_spawnable(tree):
    tree["groups"][0]["chance"] = "0.40"
    tree["groups"][0]["items"][0]["chance"] = "0.25"


@pytest.mark.parametrize(("rel", "text", "load", "save", "row", "edit", "expected"), [
    ("db/events.xml", EVENTS_XML, ce_tables.events_load, ce_tables.events_save, "StaticHeliCrash",
     _edit_events, [
         '-        <flags deletable="1" init_random="0" remove_damaged="0"/>',
         '+        <flags deletable="1" init_random="1" remove_damaged="0"/>',
         '-            <child lootmax="15" lootmin="10" max="3" min="1" type="Wreck_UH1Y" modded="1"/>',
         '+            <child lootmax="15" lootmin="10" max="5" min="1" type="Wreck_UH1Y" modded="1"/>',
     ]),
    ("custom/types.xml", TYPES_XML, ce_tables.types_load, ce_tables.types_save, "SparkPlug",
     _edit_types, [
         '-        <flags count_in_cargo="0" count_in_hoarder="0" count_in_map="1" count_in_player="0" crafted="0" deloot="0" modflag="7"/>',
         '+        <flags count_in_cargo="0" count_in_hoarder="0" count_in_map="1" count_in_player="0" crafted="1" deloot="0" modflag="7"/>',
         '-        <usage name="Farm"/>',
         '+        <usage name="Coast"/>',
     ]),
    ("cfgspawnabletypes.xml", SPAWNABLE_XML, ce_tables.spawnabletypes_load, ce_tables.spawnabletypes_save, "AKM",
     _edit_spawnable, [
         '-        <attachments chance="0.30" modattr="x">',
         '-            <item name="AK_RailHndgrd" chance="0.50"/>',
         '+        <attachments chance="0.40" modattr="x">',
         '+            <item name="AK_RailHndgrd" chance="0.25"/>',
     ]),
], ids=["events", "types", "spawnabletypes"])
def test_a_save_changes_only_the_lines_it_edited(tmp_path, versions, rel, text, load, save, row, edit, expected):
    """Bohemia's files are indented with 4 spaces, declare `standalone="yes"`
    and close empty tags with `"/>`. A save used to rewrite all of it (tabs,
    ElementTree's declaration, ` />`): 2,423 changed lines for two values in
    the stock Chernarus events.xml."""
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode())
    save(path, _rows_with(load(path), row, edit))
    after = path.read_text()
    assert after.splitlines()[0] == text.splitlines()[0]
    assert _changed_lines(text, after) == expected


@pytest.mark.parametrize(("rel", "text", "load", "save", "row"), [
    ("db/events.xml", EVENTS_XML, ce_tables.events_load, ce_tables.events_save, "StaticHeliCrash"),
    ("custom/types.xml", TYPES_XML, ce_tables.types_load, ce_tables.types_save, "SparkPlug"),
    ("cfgspawnabletypes.xml", SPAWNABLE_XML, ce_tables.spawnabletypes_load,
     ce_tables.spawnabletypes_save, "AKM"),
], ids=["events", "types", "spawnabletypes"])
def test_a_save_of_an_unedited_row_leaves_the_original_bytes(tmp_path, versions, rel, text, load, save, row):
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode())
    save(path, _rows_with(load(path), row, lambda tree: None))
    assert path.read_bytes() == text.encode()


def test_the_writer_keeps_a_block_comment_where_the_file_started_it(tmp_path):
    """Bohemia's cfgeconomycore.xml opens its rootclass note at column 0 inside
    <classes>; a save re-indented that line. A one-line comment, as a START/END
    MOD marker is, still takes the indent."""
    # The file read at the pinned 1.29 commit: its first nine lines (the note
    # and the first rootclass) and the closing tags, with CRLF line ends.
    lines = ce_lines(bohemia_ce(CE_1_29, "dayzOffline.chernarusplus/cfgeconomycore.xml"))
    classes = ce_block(lines, "<classes>")
    text = ("".join(lines[:2] + classes[:7] + classes[-1:] + lines[-1:]) + "\n").replace("\n", "\r\n")
    path = tmp_path / "cfgeconomycore.xml"
    path.write_bytes(text.encode())
    tree = load_xml(path, preserve_comments=True)
    save_xml(tree, path)
    assert path.read_bytes() == text.encode()

    tree.getroot().find("classes").append(ET.Comment(" START MOD: @X "))
    save_xml(tree, path)
    assert "\t\t<!-- START MOD: @X -->" in path.read_bytes().decode()


def test_the_writer_keeps_tabs_crlf_blank_lines_and_a_header_comment(tmp_path):
    original = (
        '﻿<?xml version="1.0" encoding="UTF-8"?>\r\n'
        "<!-- written by hand -->\r\n"
        "<events>\r\n"
        '\t<event name="A">\r\n'
        "\t\t<nominal>1</nominal>\r\n"
        "\t</event>\r\n"
        "\r\n"
        '\t<event name="B">\r\n'
        "\t\t<nominal>2</nominal>\r\n"
        "\t</event>\r\n"
        "</events>"
    )
    path = tmp_path / "events.xml"
    path.write_bytes(original.encode())
    tree = load_xml(path, preserve_comments=True)
    save_xml(tree, path, snapshot=False)
    # The byte order mark is the one thing a save drops, as it always has.
    assert path.read_bytes() == original.lstrip("﻿").encode()

    tree.getroot().find("event[@name='B']/nominal").text = "3"
    save_xml(tree, path, snapshot=False)
    assert path.read_bytes() == original.lstrip("﻿").replace("<nominal>2<", "<nominal>3<").encode()


def test_the_writer_keeps_each_tag_s_own_empty_form_and_a_comment_on_its_line(tmp_path):
    """Shapes from Bohemia's own files: cfgeconomycore.xml mixes `/>` and ` />`
    and comments a `<rootclass>` on its own line; cfgignorelist.xml writes
    `<type …></type>`."""
    # Lines of Bohemia's Chernarus cfgeconomycore.xml, read at the pinned 1.29
    # commit. The declaration and the dyn_radius line are the test's own: the
    # file writes ` ?>` and `value="30" />`.
    lines = ce_lines(bohemia_ce(CE_1_29, "dayzOffline.chernarusplus/cfgeconomycore.xml"))
    economy = "".join(
        ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n']
        + ce_pick(lines, "<economycore>", "<classes>", 'name="DefaultWeapon"', 'name="Inventory_Base"',
                  "</classes>", "<defaults>")
        + ['\t\t<default name="dyn_radius" value="30"/>\n']
        + ce_pick(lines, 'name="log_ce_loop"', "</defaults>", "</economycore>")
    ) + "\n"
    ignore = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<ignore>\n"
        '\t<type name="Bandage"></type>\n'
        '\t<type name="Spear"></type>\n'
        "</ignore>\n"
    )
    for name, text, edit, edited in [
        ("cfgeconomycore.xml", economy, ("defaults/default[@name='dyn_radius']", "value", "40"),
         ('value="30"/>', 'value="40"/>')),
        ("cfgignorelist.xml", ignore, ("type[@name='Spear']", "name", "Spear2"),
         ('"Spear"', '"Spear2"')),
    ]:
        path = tmp_path / name
        path.write_text(text)
        tree = load_xml(path, preserve_comments=True)
        save_xml(tree, path, snapshot=False)
        assert path.read_text() == text
        where, attr, value = edit
        tree.getroot().find(where).set(attr, value)
        save_xml(tree, path, snapshot=False)
        assert path.read_text() == text.replace(*edited)


def test_a_new_element_takes_the_file_s_indent(tmp_path):
    path = tmp_path / "events.xml"
    path.write_text(EVENTS_XML)
    tree = load_xml(path, preserve_comments=True)
    bear = tree.getroot().find("event[@name='AnimalBear']")
    ET.SubElement(bear, "saferadius").text = "10"
    save_xml(tree, path, snapshot=False)
    assert _changed_lines(EVENTS_XML, path.read_text()) == ["+        <saferadius>10</saferadius>"]


def test_a_new_file_is_written_as_before(tmp_path):
    path = tmp_path / "new.xml"
    root = ET.Element("types")
    ET.SubElement(ET.SubElement(root, "type", name="A"), "flags", crafted="0")
    save_xml(ET.ElementTree(root), path, snapshot=False)
    assert path.read_text() == (
        "<?xml version='1.0' encoding='utf-8'?>\n"
        "<types>\n"
        '\t<type name="A">\n'
        '\t\t<flags crafted="0" />\n'
        "\t</type>\n"
        "</types>\n"
    )
