"""Tests for cli.spawnabletypes_xml — parse, CRUD, round-trip."""

from pathlib import Path

import pytest

from tests.conftest import CE_1_29, CE_1_30, bohemia_ce, ce_block, ce_lines, ce_pick, write_xml

from app.xml.spawnabletypes_xml import (
    add_attachment_group,
    add_attachment_item,
    parse_spawnabletypes_file,
    remove_attachment_group,
    remove_attachment_item,
    save_spawnabletypes_file,
    set_attachment_chance,
    set_damage,
    set_hoarder,
    set_item_chance,
    set_tag,
)


SAMPLE_SPAWNABLETYPES = """\
<?xml version="1.0" encoding="UTF-8"?>
<spawnabletypes>
    <type name="AKM">
        <damage min="0.10" max="0.60"/>
        <tag name="shelves"/>
        <attachments chance="0.30">
            <item name="AK_RailHndgrd" chance="0.50"/>
            <item name="AK_PlasticBttstck" chance="0.40"/>
        </attachments>
        <cargo chance="0.20" preset="foodSmall"/>
    </type>
    <type name="Mosin9130">
        <hoarder/>
        <attachments chance="1.00">
            <item name="PUScopeOptic" chance="0.10"/>
        </attachments>
    </type>
</spawnabletypes>
"""


def test_parse_spawnabletypes_file(tmp_path: Path):
    p = write_xml(tmp_path / "cfgspawnabletypes.xml", SAMPLE_SPAWNABLETYPES)
    sf = parse_spawnabletypes_file(p)
    assert len(sf.entries) == 2

    akm = sf.entries[0]
    assert akm.name == "AKM"
    assert akm.damage_min == 0.10
    assert akm.damage_max == 0.60
    assert akm.tag_name == "shelves"
    assert akm.has_hoarder is False
    assert len(akm.attachment_groups) == 2

    att = akm.attachment_groups[0]
    assert att.tag == "attachments"
    assert att.chance == 0.30
    assert att.preset is None
    assert len(att.items) == 2
    assert att.items[0].name == "AK_RailHndgrd"
    assert att.items[0].chance == 0.50

    cargo = akm.attachment_groups[1]
    assert cargo.tag == "cargo"
    assert cargo.chance == 0.20
    assert cargo.preset == "foodSmall"

    mosin = sf.entries[1]
    assert mosin.has_hoarder is True
    assert mosin.damage_min is None


def test_set_damage(tmp_path: Path):
    p = write_xml(tmp_path / "cfgspawnabletypes.xml", SAMPLE_SPAWNABLETYPES)
    sf = parse_spawnabletypes_file(p)
    entry = sf.entries[0]
    set_damage(entry, 0.20, 0.80)
    assert entry.damage_min == 0.20
    assert entry.damage_max == 0.80


def test_set_damage_remove(tmp_path: Path):
    p = write_xml(tmp_path / "cfgspawnabletypes.xml", SAMPLE_SPAWNABLETYPES)
    sf = parse_spawnabletypes_file(p)
    entry = sf.entries[0]
    set_damage(entry, None, None)
    assert entry.damage_min is None
    assert entry.damage_max is None
    assert entry.element.find("damage") is None


def test_set_hoarder(tmp_path: Path):
    p = write_xml(tmp_path / "cfgspawnabletypes.xml", SAMPLE_SPAWNABLETYPES)
    sf = parse_spawnabletypes_file(p)
    entry = sf.entries[0]
    assert entry.has_hoarder is False
    set_hoarder(entry, True)
    assert entry.has_hoarder is True
    assert entry.element.find("hoarder") is not None
    set_hoarder(entry, False)
    assert entry.has_hoarder is False
    assert entry.element.find("hoarder") is None


def test_set_tag(tmp_path: Path):
    p = write_xml(tmp_path / "cfgspawnabletypes.xml", SAMPLE_SPAWNABLETYPES)
    sf = parse_spawnabletypes_file(p)
    entry = sf.entries[0]
    set_tag(entry, "floor")
    assert entry.tag_name == "floor"
    set_tag(entry, None)
    assert entry.tag_name is None
    assert entry.element.find("tag") is None


def test_set_attachment_chance(tmp_path: Path):
    p = write_xml(tmp_path / "cfgspawnabletypes.xml", SAMPLE_SPAWNABLETYPES)
    sf = parse_spawnabletypes_file(p)
    group = sf.entries[0].attachment_groups[0]
    set_attachment_chance(group, 0.75)
    assert group.chance == 0.75


def test_set_item_chance(tmp_path: Path):
    p = write_xml(tmp_path / "cfgspawnabletypes.xml", SAMPLE_SPAWNABLETYPES)
    sf = parse_spawnabletypes_file(p)
    item = sf.entries[0].attachment_groups[0].items[0]
    set_item_chance(item, 0.90)
    assert item.chance == 0.90


def test_add_and_remove_attachment_item(tmp_path: Path):
    p = write_xml(tmp_path / "cfgspawnabletypes.xml", SAMPLE_SPAWNABLETYPES)
    sf = parse_spawnabletypes_file(p)
    group = sf.entries[0].attachment_groups[0]
    assert len(group.items) == 2
    new_item = add_attachment_item(group, "AK_Bayonet", 0.15)
    assert len(group.items) == 3
    assert new_item.name == "AK_Bayonet"
    remove_attachment_item(group, new_item)
    assert len(group.items) == 2


def test_add_and_remove_attachment_group(tmp_path: Path):
    p = write_xml(tmp_path / "cfgspawnabletypes.xml", SAMPLE_SPAWNABLETYPES)
    sf = parse_spawnabletypes_file(p)
    entry = sf.entries[1]
    assert len(entry.attachment_groups) == 1
    new_group = add_attachment_group(entry, "cargo", 0.50, None)
    assert len(entry.attachment_groups) == 2
    assert new_group.tag == "cargo"
    remove_attachment_group(entry, new_group)
    assert len(entry.attachment_groups) == 1


def test_round_trip(tmp_path: Path):
    p = write_xml(tmp_path / "cfgspawnabletypes.xml", SAMPLE_SPAWNABLETYPES)
    sf = parse_spawnabletypes_file(p)
    set_damage(sf.entries[0], 0.05, 0.95)
    set_hoarder(sf.entries[0], True)
    save_spawnabletypes_file(sf)

    sf2 = parse_spawnabletypes_file(p)
    assert sf2.entries[0].damage_min == 0.05
    assert sf2.entries[0].damage_max == 0.95
    assert sf2.entries[0].has_hoarder is True
    assert sf2.entries[1].has_hoarder is True  # unchanged


# ---------------------------------------------------------------------------
# The Spawnable Types table (ce_tables.spawnabletypes_*)
# ---------------------------------------------------------------------------

# A Mod XML link can splice a block into any mission file, between START/END
# MOD markers that the merge finds as text.
MERGED_SPAWNABLETYPES = """\
<?xml version="1.0" encoding="UTF-8"?>
<spawnabletypes>
    <type name="AKM">
        <damage min="0.555" max="0.8" />
        <attachments chance="0.30">
            <item name="AK_WoodBttstck" chance="1.00" />
        </attachments>
    </type>
    <type name="Barrel_Blue">
        <hoarder />
    </type>
    <!-- START MOD: @MyMod -->
    <type name="ModGun">
        <damage min="0.0" max="0.32" />
    </type>
    <!-- END MOD: @MyMod -->
</spawnabletypes>
"""


def _merged(tmp_path: Path) -> Path:
    from app.xml.xml_utils import load_xml, save_xml

    p = write_xml(tmp_path / "cfgspawnabletypes.xml", MERGED_SPAWNABLETYPES)
    save_xml(load_xml(p, preserve_comments=True), p)
    return p


def test_a_table_save_without_a_change_leaves_the_file_as_it_was(tmp_path: Path):
    from app.web.editors.ce_tables import spawnabletypes_load, spawnabletypes_save

    p = _merged(tmp_path)
    before = p.read_text()
    spawnabletypes_save(p, spawnabletypes_load(p)["rows"])
    assert p.read_text() == before
    assert "<!-- START MOD: @MyMod -->" in before and 'min="0.555"' in before


def test_a_table_save_writes_only_the_value_that_changed(tmp_path: Path):
    from app.web.editors.ce_tables import spawnabletypes_load, spawnabletypes_save

    p = _merged(tmp_path)
    rows = spawnabletypes_load(p)["rows"]
    rows[0]["values"]["damage_max"] = "0.9"
    rows[2]["values"]["damage_min"] = "0.125"
    spawnabletypes_save(p, rows)

    content = p.read_text()
    assert '<damage min="0.555" max="0.90" />' in content
    assert '<damage min="0.125" max="0.32" />' in content
    assert content.index("START MOD: @MyMod") < content.index("ModGun") < content.index("END MOD: @MyMod")


def test_a_chance_that_is_not_a_number_does_not_stop_the_file_loading(tmp_path: Path):
    p = write_xml(
        tmp_path / "cfgspawnabletypes.xml",
        '<spawnabletypes><type name="AKM"><damage min="" max="x" />'
        '<attachments chance=""><item name="Mag" chance="lots" /></attachments></type></spawnabletypes>',
    )
    entry = parse_spawnabletypes_file(p).entries[0]
    assert (entry.damage_min, entry.damage_max) == (None, None)
    group = entry.attachment_groups[0]
    assert (group.chance, group.items[0].chance) == (None, None)


# Entries of Bohemia's stock files, read at the pinned 1.29 commit: Sakhal's
# cfgspawnabletypes.xml lists ScientificBriefcase twice, the second with
# `0.1`; Chernarus' FirstAidKit has items without a chance and its duffel
# bags only preset groups. The first briefcase and the FirstAidKit keep their
# first five lines and their closing tag. The XML declaration is the test's own.
BRIEFCASE = '<type name="ScientificBriefcase">'


def stock_spawnables() -> str:
    sakhal = ce_lines(bohemia_ce(CE_1_29, "dayzOffline.sakhal/cfgspawnabletypes.xml"))
    chernarus = ce_lines(bohemia_ce(CE_1_29, "dayzOffline.chernarusplus/cfgspawnabletypes.xml"))
    first = ce_block(sakhal, BRIEFCASE)
    second = ce_block(sakhal[sakhal.index(first[0]) + 1:], BRIEFCASE)
    kit = ce_block(chernarus, '<type name="FirstAidKit">')
    return "".join(
        ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n']
        + ce_pick(sakhal, "<spawnabletypes>")
        + first[:5] + first[-1:]
        + ce_block(chernarus, '<type name="DuffelBagSmall_Green">')
        + kit[:5] + kit[-1:]
        + second
        + ce_pick(sakhal, "</spawnabletypes>")
    )


def _stock(tmp_path: Path):
    from app.web.editors import ce_tables

    p = tmp_path / "cfgspawnabletypes.xml"
    p.write_text(stock_spawnables())
    return p, ce_tables


def test_a_type_listed_twice_is_two_rows_and_each_keeps_its_own_values(tmp_path: Path):
    """Rows were looked up by name, so the first ScientificBriefcase's edits
    went to the second, and every save rewrote the second's `0.1` as `0.10`."""
    p, ce = _stock(tmp_path)
    rows = ce.spawnabletypes_load(p)["rows"]
    assert [r["values"]["name"] for r in rows].count("ScientificBriefcase") == 2
    assert len({r["id"] for r in rows}) == len(rows)

    ce.spawnabletypes_save(p, rows)
    assert p.read_text() == stock_spawnables()

    rows[0]["values"]["damage_max"] = "0.2"
    ce.spawnabletypes_save(p, rows)
    text = p.read_text()
    assert '<damage min="0.0" max="0.20" />' in text
    assert '<damage min="0.1" max="0.1" />' in text


@pytest.mark.parametrize(("cells", "message"), [
    ({"damage_max": "30"}, "outside 0 to 1"),
    ({"damage_max": "nan"}, "outside 0 to 1"),
    ({"damage_max": "lots"}, "not a number"),
    ({"damage_min": "0.5", "damage_max": "0.2"}, "above damage max"),
    ({"damage_min": ""}, "set both damage min and damage max"),
])
def test_a_damage_outside_0_to_1_or_out_of_order_or_half_cleared_is_refused(tmp_path: Path, cells, message):
    p, ce = _stock(tmp_path)
    rows = ce.spawnabletypes_load(p)["rows"]
    rows[0]["values"].update(cells)
    with pytest.raises(ValueError, match=message):
        ce.spawnabletypes_save(p, rows)
    assert p.read_text() == stock_spawnables()


def test_clearing_both_damage_bounds_removes_the_element(tmp_path: Path):
    p, ce = _stock(tmp_path)
    rows = ce.spawnabletypes_load(p)["rows"]
    rows[0]["values"].update(damage_min="", damage_max="")
    ce.spawnabletypes_save(p, rows)
    briefcase = p.read_text().split("</type>")[0]
    assert "<damage" not in briefcase


def test_set_damage_refuses_what_the_table_refuses(tmp_path: Path):
    p = write_xml(tmp_path / "cfgspawnabletypes.xml", SAMPLE_SPAWNABLETYPES)
    entry = parse_spawnabletypes_file(p).entries[0]
    with pytest.raises(ValueError):
        set_damage(entry, 0.0, 30)
    with pytest.raises(ValueError):
        set_damage(entry, 0.8, 0.2)
    assert (entry.damage_min, entry.damage_max) == (0.10, 0.60)


def _with_groups(row: dict, edit) -> dict:
    import copy

    tree = copy.deepcopy(row["nested"])
    edit(tree["groups"])
    row["values"]["$nested"] = tree
    row["values"]["$nested_base"] = row["nested"]
    return row


def test_an_item_without_a_chance_can_be_added_beside_one(tmp_path: Path):
    """FirstAidKit's items carry no chance; the tree fitted a new item to its
    sibling's keys and refused the template's `chance`, or one without it."""
    p, ce = _stock(tmp_path)
    rows = ce.spawnabletypes_load(p)["rows"]
    _with_groups(rows[2], lambda groups: groups[0]["items"].extend([
        {"name": "Bandage"}, dict(ce.SPAWNABLE_ITEM_TEMPLATE, name="Morphine"),
    ]))
    ce.spawnabletypes_save(p, rows)
    text = p.read_text()
    assert '<item name="Bandage" />' in text
    assert '<item name="Morphine" chance="1.0" />' in text


def test_an_inline_group_can_be_added_to_a_type_with_only_presets(tmp_path: Path):
    p, ce = _stock(tmp_path)
    rows = ce.spawnabletypes_load(p)["rows"]
    group = dict(ce.SPAWNABLE_TEMPLATES["groups"], items=[{"name": "Battery9V", "chance": 0.5}])
    _with_groups(rows[1], lambda groups: groups.append(group))
    ce.spawnabletypes_save(p, rows)
    bag = p.read_text().split('<type name="DuffelBagSmall_Green">')[1].split("</type>")[0]
    assert '<attachments chance="1.0">' in bag  # no preset copied from the neighbour
    assert '<item name="Battery9V" chance="0.5" />' in bag


@pytest.mark.parametrize("chance", [50, 1.5, "50%", "x", -0.1, "nan", None])
def test_a_group_chance_is_a_fraction_from_0_to_1(tmp_path: Path, chance):
    p, ce = _stock(tmp_path)
    rows = ce.spawnabletypes_load(p)["rows"]

    def edit(groups):
        groups[0]["chance"] = chance

    _with_groups(rows[2], edit)
    with pytest.raises(ValueError, match="chance"):
        ce.spawnabletypes_save(p, rows)
    assert p.read_text() == stock_spawnables()


@pytest.mark.parametrize("chance", ["50%", "x", -0.1, "nan", "inf", None])
def test_an_item_chance_is_a_number_of_0_or_more(tmp_path: Path, chance):
    p, ce = _stock(tmp_path)
    rows = ce.spawnabletypes_load(p)["rows"]

    def edit(groups):
        groups[0]["items"][0]["chance"] = chance

    _with_groups(rows[2], edit)
    with pytest.raises(ValueError, match="chance"):
        ce.spawnabletypes_save(p, rows)
    assert p.read_text() == stock_spawnables()


# Sakhal's cfgspawnabletypes.xml read at the pinned 1.30 commit, its first
# three lines, the AKM entry and the last line (the AKM entry first seen in
# 1.30 Experimental, build 25319221, and the same in 1.29): the items of a group
# carry chances that add up to 100, a weight of each item against the others.
def sakhal_akm() -> str:
    lines = ce_lines(bohemia_ce(CE_1_30, "dayzOffline.sakhal/cfgspawnabletypes.xml"))
    return "".join(lines[:3] + ce_block(lines, '<type name="AKM">') + lines[-1:])


def test_sakhal_s_item_chances_above_1_save_through_the_tree(tmp_path: Path):
    """An edit to AKM's tree sends every chance of the group back; Sakhal's
    40.00 and 30.00 were refused as "outside 0 to 1", so the stock entry could
    not be edited at all. The weight an admin types in that style is taken."""
    from app.web.editors import ce_tables as ce

    p = tmp_path / "cfgspawnabletypes.xml"
    p.write_text(sakhal_akm())
    rows = ce.spawnabletypes_load(p)["rows"]

    def edit(groups):
        groups[1]["items"][3]["chance"] = 15

    _with_groups(rows[0], edit)
    ce.spawnabletypes_save(p, rows)
    text = p.read_text()
    assert '<item name="AK_WoodHndgrd" chance="15.00" />' in text
    assert text == sakhal_akm().replace('"AK_WoodHndgrd" chance="10.00"', '"AK_WoodHndgrd" chance="15.00"')


def test_set_item_chance_takes_a_weight_and_refuses_a_negative(tmp_path: Path):
    p = tmp_path / "cfgspawnabletypes.xml"
    p.write_text(sakhal_akm())
    sf = parse_spawnabletypes_file(p)
    item = sf.entries[0].attachment_groups[0].items[0]
    set_item_chance(item, 45.0)
    assert item.element.get("chance") == "45.00"
    with pytest.raises(ValueError, match="chance"):
        set_item_chance(item, -1)
    with pytest.raises(ValueError, match="chance"):
        add_attachment_item(sf.entries[0].attachment_groups[0], "AK_WoodBttstck", float("nan"))
    with pytest.raises(ValueError, match="chance"):
        set_attachment_chance(sf.entries[0].attachment_groups[0], 40.0)


def test_a_new_hoarder_and_tag_go_before_the_groups(tmp_path: Path):
    """Bohemia writes <hoarder/>, <tag> and <damage> ahead of a type's groups."""
    p, ce = _stock(tmp_path)
    rows = ce.spawnabletypes_load(p)["rows"]
    rows[1]["values"].update(hoarder=True, tag="floor", damage_min="0.1", damage_max="0.2")
    ce.spawnabletypes_save(p, rows)
    bag = p.read_text().split('<type name="DuffelBagSmall_Green">')[1].split("</type>")[0]
    tags = [line.strip().split()[0] for line in bag.strip().splitlines()]
    assert tags == ["<hoarder", "<tag", "<damage", "<cargo", "<cargo"]
