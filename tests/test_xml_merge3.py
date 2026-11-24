"""Three-way merge of a stock mission XML file (plans/71 Step 2).

Fixtures are short and shaped like Bohemia's files, except stock_groups(),
which reads Bohemia's own at a pinned commit; the real-file runs are in the
plan's "Measured in Step 0" and Step 2 notes.
"""

from __future__ import annotations

import json

import pytest

from app.xml.xml_merge3 import (
    BOTH_CHANGED,
    DAYZ_REMOVED,
    YOU_REMOVED,
    Unmergeable,
    find_element,
    merge_xml3,
    parse,
)
from tests.conftest import CE_1_29, bohemia_ce, ce_block, ce_lines

TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<types>
    <type name="AKM">
        <nominal>10</nominal>
        <lifetime>28800</lifetime>
        <flags count_in_cargo="0" crafted="0"/>
        <category name="weapons"/>
        <usage name="Military"/>
    </type>
    <type name="Apple">
        <nominal>40</nominal>
        <lifetime>3600</lifetime>
    </type>
    <type name="Pear">
        <nominal>20</nominal>
        <lifetime>3600</lifetime>
    </type>
</types>
"""


def _merge(base, mine, theirs, filename="types.xml", **kw):
    return merge_xml3(base, mine, theirs, filename=filename, **kw)


def _types(text):
    return {el.get("name"): el for el in parse(text)}


def _nominal(text, name):
    return _types(text)[name].findtext("nominal")


def test_with_dayz_unchanged_the_result_is_the_admins_file():
    mine = TYPES.replace("<nominal>10</nominal>", "<nominal>3</nominal>")
    result = _merge(TYPES, mine, TYPES)
    assert _nominal(result.text, "AKM") == "3"
    assert result.rows == [] and result.yours == 1 and result.dayz == 0


def test_with_the_admin_unchanged_the_result_is_dayzs_file():
    theirs = TYPES.replace("<lifetime>28800</lifetime>", "<lifetime>7200</lifetime>")
    result = _merge(TYPES, TYPES, theirs)
    assert _types(result.text)["AKM"].findtext("lifetime") == "7200"
    assert result.rows == [] and result.dayz == 1


def test_different_values_of_one_entry_both_come_in():
    mine = TYPES.replace("<nominal>10</nominal>", "<nominal>3</nominal>")
    theirs = TYPES.replace("<lifetime>28800</lifetime>", "<lifetime>7200</lifetime>")
    akm = _types(_merge(TYPES, mine, theirs).text)["AKM"]
    assert (akm.findtext("nominal"), akm.findtext("lifetime")) == ("3", "7200")


def test_a_value_both_changed_keeps_the_admins_and_is_listed():
    mine = TYPES.replace("<nominal>10</nominal>", "<nominal>3</nominal>")
    theirs = TYPES.replace("<nominal>10</nominal>", "<nominal>8</nominal>")
    result = _merge(TYPES, mine, theirs)
    assert _nominal(result.text, "AKM") == "3"
    (row,) = result.rows
    assert (row.kind, row.entry, row.field, row.base, row.mine, row.theirs, row.live) == (
        BOTH_CHANGED, "AKM", "nominal", "10", "3", "8", "mine")


def test_prefer_dayz_keeps_dayzs_value_and_lists_it():
    mine = TYPES.replace("<nominal>10</nominal>", "<nominal>3</nominal>")
    theirs = TYPES.replace("<nominal>10</nominal>", "<nominal>8</nominal>")
    result = _merge(TYPES, mine, theirs, prefer="dayz")
    assert _nominal(result.text, "AKM") == "8"
    assert result.rows[0].live == "dayz"


def test_an_attribute_both_changed_is_one_row():
    mine = TYPES.replace('crafted="0"', 'crafted="1"')
    theirs = TYPES.replace('crafted="0"', 'crafted="2"')
    (row,) = _merge(TYPES, mine, theirs).rows
    assert (row.field, row.target) == ("flags/@crafted", ["attr", "crafted"])


def test_entries_added_on_either_side_are_all_kept_in_place():
    mine = TYPES.replace('    <type name="Pear">', '    <type name="Mine"><nominal>1</nominal></type>\n    <type name="Pear">')
    theirs = TYPES.replace("</types>", '    <type name="Theirs"><nominal>2</nominal></type>\n</types>')
    names = [el.get("name") for el in parse(_merge(TYPES, mine, theirs).text)]
    assert names == ["AKM", "Apple", "Mine", "Pear", "Theirs"]


def test_the_same_entry_added_differently_on_both_sides_is_listed():
    mine = TYPES.replace("</types>", '<type name="New"><nominal>1</nominal></type></types>')
    theirs = TYPES.replace("</types>", '<type name="New"><nominal>2</nominal></type></types>')
    result = _merge(TYPES, mine, theirs)
    assert _nominal(result.text, "New") == "1"
    assert result.rows[0].kind == BOTH_CHANGED and result.rows[0].base is None


def test_an_entry_dayz_removed_goes_even_when_the_admin_changed_it():
    mine = TYPES.replace("<nominal>40</nominal>", "<nominal>4</nominal>")
    theirs = TYPES.replace(TYPES[TYPES.index('    <type name="Apple">'):TYPES.index('    <type name="Pear">')], "")
    result = _merge(TYPES, mine, theirs, prefer="mine")
    assert "Apple" not in _types(result.text)
    (row,) = result.rows
    assert (row.kind, row.live, row.entry, row.target) == (DAYZ_REMOVED, "dayz", "Apple", ["entry"])
    assert "<nominal>4</nominal>" in row.mine


def test_an_entry_dayz_removed_that_nobody_changed_just_goes():
    theirs = TYPES.replace(TYPES[TYPES.index('    <type name="Apple">'):TYPES.index('    <type name="Pear">')], "")
    result = _merge(TYPES, TYPES, theirs)
    assert "Apple" not in _types(result.text) and result.rows == []


def test_an_entry_the_admin_removed_stays_removed_and_is_listed_when_dayz_changed_it():
    mine = TYPES.replace(TYPES[TYPES.index('    <type name="Apple">'):TYPES.index('    <type name="Pear">')], "")
    theirs = TYPES.replace("<nominal>40</nominal>", "<nominal>50</nominal>")
    result = _merge(TYPES, mine, theirs)
    assert "Apple" not in _types(result.text)
    assert result.rows[0].kind == YOU_REMOVED
    assert "Apple" in _types(_merge(TYPES, mine, theirs, prefer="dayz").text)


def test_dayzs_order_wins_unless_only_the_admin_moved_entries():
    moved = _types(TYPES)
    reordered = "<types>" + "".join(
        f'<type name="{n}"><nominal>{moved[n].findtext("nominal")}</nominal><lifetime>{moved[n].findtext("lifetime")}</lifetime></type>'
        for n in ("Pear", "Apple")) + "</types>"
    base = "<types>" + "".join(
        f'<type name="{n}"><nominal>{moved[n].findtext("nominal")}</nominal><lifetime>{moved[n].findtext("lifetime")}</lifetime></type>'
        for n in ("Apple", "Pear")) + "</types>"
    assert [el.get("name") for el in parse(_merge(base, reordered, base).text)] == ["Pear", "Apple"]
    assert [el.get("name") for el in parse(_merge(base, base, reordered).text)] == ["Pear", "Apple"]


def test_mod_blocks_in_the_admins_file_are_left_out():
    mine = TYPES.replace("</types>", (
        "<!-- START MOD: @CF -->\n<type name=\"CF_Thing\"><nominal>1</nominal></type>\n<!-- END MOD: @CF -->\n</types>"))
    result = _merge(TYPES, mine, TYPES)
    assert "CF_Thing" not in result.text and "START MOD" not in result.text


def test_a_disabled_wrapper_in_cfgeconomycore_survives():
    base = '<economycore><classes><rootclass name="DefaultWeapon"/></classes></economycore>'
    wrapper = '<!-- DISABLED @Bikes: <ce folder="custom"><file name="bikes-types.xml" type="types"/></ce> -->'
    mine = base.replace("</economycore>", f'<ce folder="custom"><file name="x.xml" type="types"/></ce>{wrapper}</economycore>')
    theirs = base.replace('<rootclass name="DefaultWeapon"/>', '<rootclass name="DefaultWeapon"/><rootclass name="DefaultMagazine"/>')
    text = _merge(base, mine, theirs, filename="cfgeconomycore.xml").text
    assert "DISABLED @Bikes" in text and "DefaultMagazine" in text and 'folder="custom"' in text


def test_two_ce_folders_are_keyed_by_folder():
    base = '<economycore><ce folder="db"><file name="a.xml" type="types"/></ce></economycore>'
    mine = base.replace("</economycore>", '<ce folder="mine"><file name="b.xml" type="types"/></ce></economycore>')
    text = _merge(base, mine, base, filename="cfgeconomycore.xml").text
    assert 'folder="db"' in text and 'folder="mine"' in text


SPAWNABLE = """<spawnabletypes>
    <type name="Bag">
        <attachments chance="0.5"><item name="A" chance="1"/></attachments>
        <attachments chance="0.5"><item name="B" chance="1"/></attachments>
    </type>
    <type name="Box"><cargo chance="1"><item name="C" chance="1"/></cargo></type>
</spawnabletypes>
"""


def test_a_spawnabletypes_entry_without_named_parts_is_one_value():
    mine = SPAWNABLE.replace('<item name="A" chance="1"/>', '<item name="A" chance="0.2"/>')
    theirs = SPAWNABLE.replace('<item name="B" chance="1"/>', '<item name="B" chance="0.3"/>')
    result = _merge(SPAWNABLE, mine, theirs, filename="cfgspawnabletypes.xml")
    (row,) = result.rows
    assert (row.entry, row.field, row.target) == ("Bag", "(whole entry)", ["entry"])
    assert 'chance="0.2"' in result.text and 'chance="0.3"' not in result.text


def test_a_name_twice_is_one_entry_compared_whole():
    """Livonia's cfgspawnabletypes.xml has ZmbM_usSoldier_Heavy_Woodland twice."""
    twice = SPAWNABLE.replace("</spawnabletypes>", '<type name="Box"><cargo chance="0.5"><item name="D" chance="1"/></cargo></type></spawnabletypes>')
    theirs = twice.replace('<item name="D" chance="1"/>', '<item name="E" chance="1"/>')
    result = _merge(twice, twice, theirs, filename="cfgspawnabletypes.xml")
    assert result.rows == [] and 'name="E"' in result.text and result.text.count('<type name="Box">') == 2


TERRITORIES = """<territory-type>
    <territory color="1">
        <zone name="InfectedVillage" smin="1" smax="2" dmin="3" dmax="6" x="100" z="200" r="50"/>
        <zone name="InfectedVillage" smin="1" smax="2" dmin="3" dmax="6" x="300" z="400" r="50"/>
    </territory>
    <territory color="1">
        <zone name="InfectedCity" smin="2" smax="4" dmin="6" dmax="12" x="500" z="600" r="80"/>
    </territory>
</territory-type>
"""


def test_territory_zones_merge_value_by_value():
    mine = TERRITORIES.replace('dmin="3" dmax="6" x="100"', 'dmin="6" dmax="12" x="100"')
    theirs = TERRITORIES.replace('r="80"', 'r="90"')
    text = _merge(TERRITORIES, mine, theirs, filename="zombie_territories.xml").text
    assert 'dmin="6" dmax="12" x="100"' in text and 'r="90"' in text and text.count("<zone") == 3


def test_entries_with_no_key_at_the_root_cannot_be_merged():
    base = "<root><item><a/></item><item><b/></item></root>"
    with pytest.raises(Unmergeable):
        _merge(base, base.replace("<a/>", "<c/>"), base, filename="cfgthing.xml")


def test_a_version_that_does_not_parse_cannot_be_merged():
    with pytest.raises(Unmergeable, match="does not parse"):
        _merge(TYPES, TYPES, TYPES.replace("</types>", ""))


def test_a_different_root_cannot_be_merged():
    with pytest.raises(Unmergeable, match="root"):
        _merge(TYPES, TYPES, "<events/>")


def test_a_listed_value_can_be_found_again_after_a_json_round_trip():
    mine = TYPES.replace("<nominal>10</nominal>", "<nominal>3</nominal>")
    theirs = TYPES.replace("<nominal>10</nominal>", "<nominal>8</nominal>")
    result = _merge(TYPES, mine, theirs)
    keys = json.loads(json.dumps(result.rows[0].keys))
    (found,) = find_element(parse(result.text), keys, "types.xml")
    assert found.text == "3"
    assert find_element(parse(TYPES.replace('name="AKM"', 'name="Gone"')), keys, "types.xml") == []


# --- a leaf replaced on both sides (the 1.30 convoy wrecks) ------------------

# Bohemia's 1.29 Chernarus cfgeventgroups.xml, read at the pinned commit: the
# Abandoned_Arsenovo convoy group with its first three children.
def stock_groups() -> str:
    lines = ce_lines(bohemia_ce(CE_1_29, "dayzOffline.chernarusplus/cfgeventgroups.xml"))
    group = ce_block(lines, 'name="Abandoned_Arsenovo"')
    return "".join(lines[:2] + group[:4] + group[-1:] + lines[-1:])


def test_a_wreck_dayz_swapped_at_the_same_spot_keeps_the_admins_loot():
    # DayZ 1.30 put a V3S where Chernarus' MTVR convoy wreck was. A group repeats
    # its decals' type, so children used to be keyed by content: the admin's
    # edited MTVR and DayZ's V3S both stayed, two wrecks on one spot, no row.
    mine = stock_groups().replace('lootmax="3"', 'lootmax="9"')
    theirs = stock_groups().replace("StaticObj_Wreck_MTVR_Cargo_DE", "Land_Wreck_V3S_DE")
    result = _merge(stock_groups(), mine, theirs, filename="dayzOffline.chernarusplus/cfgeventgroups.xml")
    children = list(parse(result.text).find("group"))
    assert [c.get("type") for c in children][0] == "Land_Wreck_V3S_DE"
    assert len(children) == 3
    assert children[0].get("lootmax") == "9"
    assert result.rows == []


PRESETS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<randompresets>
    <cargo chance="0.16" name="foodHermit">
        <item name="Apple" chance="0.10"/>
        <item name="Apple" chance="0.10"/>
        <item name="Pear" chance="0.20"/>
    </cargo>
    <cargo chance="0.5" name="tools">
        <item name="Hammer" chance="0.30"/>
    </cargo>
</randompresets>
"""


def test_a_leaf_both_sides_replaced_differently_makes_its_parent_one_value():
    # Items repeat a name here, so they are keyed by content and an edit is a
    # removal plus an addition. Two different replacements of one item cannot
    # be paired up, so the cargo is one value: listed, never both kept.
    mine = PRESETS.replace('name="Pear" chance="0.20"', 'name="Pear" chance="0.05"')
    theirs = PRESETS.replace('name="Pear" chance="0.20"', 'name="Pear" chance="0.40"')
    theirs = theirs.replace('name="Hammer" chance="0.30"', 'name="Hammer" chance="0.35"')
    result = _merge(PRESETS, mine, theirs, filename="cfgrandompresets.xml", prefer="mine")
    cargo = {c.get("name"): c for c in parse(result.text)}
    assert [i.get("chance") for i in cargo["foodHermit"] if i.get("name") == "Pear"] == ["0.05"]
    assert cargo["tools"][0].get("chance") == "0.35"  # the rest still merges
    (row,) = result.rows
    assert (row.kind, row.target, row.entry) == (BOTH_CHANGED, ["entry"], "foodHermit")
    assert 'chance="0.40"' in row.theirs


def test_the_same_replacement_on_both_sides_merges_without_a_row():
    both = PRESETS.replace('name="Pear" chance="0.20"', 'name="Pear" chance="0.05"')
    result = _merge(PRESETS, both, both, filename="cfgrandompresets.xml")
    assert result.rows == []
    assert 'name="Pear" chance="0.05"' in result.text


def test_a_leaf_both_sides_replaced_at_the_root_cannot_be_merged():
    base = '<map>\n<group name="Land_A" pos="1 0 1"/>\n<group name="Land_A" pos="2 0 2"/>\n</map>\n'
    mine = base.replace('pos="1 0 1"', 'pos="1 0 5"')
    theirs = base.replace('pos="1 0 1"', 'pos="1 0 9"')
    with pytest.raises(Unmergeable):
        _merge(base, mine, theirs, filename="mapgrouppos.xml")
