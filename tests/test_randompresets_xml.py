"""Tests for cli.randompresets_xml — parse, CRUD, round-trip."""

from pathlib import Path

import pytest

from tests.conftest import write_xml

from app.web.editors.mission_tables import _randompresets_load, _randompresets_save
from app.xml.randompresets_xml import (
    add_cargo_preset,
    add_item_to_preset,
    parse_randompresets_file,
    remove_cargo_preset,
    remove_item_from_preset,
    save_randompresets_file,
    set_item_chance,
    set_preset_chance,
)
from app.xml.xml_utils import load_xml, save_xml


SAMPLE_RANDOMPRESETS = """\
<?xml version="1.0" encoding="UTF-8"?>
<randompresets>
    <cargo name="foodSmall" chance="0.30">
        <item name="Apple" chance="0.50"/>
        <item name="Pear" chance="0.30"/>
        <item name="Plum" chance="0.20"/>
    </cargo>
    <cargo name="medicalBasic" chance="0.10">
        <item name="BandageDressing" chance="0.80"/>
    </cargo>
</randompresets>
"""


def test_parse_randompresets_file(tmp_path: Path):
    p = write_xml(tmp_path / "cfgRandomPresets.xml", SAMPLE_RANDOMPRESETS)
    rpf = parse_randompresets_file(p)
    assert len(rpf.presets) == 2

    food = rpf.presets[0]
    assert food.name == "foodSmall"
    assert food.chance == "0.30"
    assert len(food.items) == 3
    assert food.items[0].name == "Apple"
    assert food.items[0].chance == "0.50"

    med = rpf.presets[1]
    assert med.name == "medicalBasic"
    assert len(med.items) == 1


def test_set_preset_chance(tmp_path: Path):
    p = write_xml(tmp_path / "cfgRandomPresets.xml", SAMPLE_RANDOMPRESETS)
    rpf = parse_randompresets_file(p)
    preset = rpf.presets[0]
    set_preset_chance(preset, "0.50")
    assert preset.chance == "0.50"
    assert preset.element.get("chance") == "0.50"


def test_set_item_chance(tmp_path: Path):
    p = write_xml(tmp_path / "cfgRandomPresets.xml", SAMPLE_RANDOMPRESETS)
    rpf = parse_randompresets_file(p)
    item = rpf.presets[0].items[0]
    set_item_chance(item, "0.99")
    assert item.chance == "0.99"
    assert item.element.get("chance") == "0.99"


def test_add_and_remove_cargo_preset(tmp_path: Path):
    p = write_xml(tmp_path / "cfgRandomPresets.xml", SAMPLE_RANDOMPRESETS)
    rpf = parse_randompresets_file(p)
    assert len(rpf.presets) == 2
    new_preset = add_cargo_preset(rpf, "ammoBox", "0.15")
    assert len(rpf.presets) == 3
    assert new_preset.name == "ammoBox"
    assert new_preset.chance == "0.15"
    remove_cargo_preset(rpf, new_preset)
    assert len(rpf.presets) == 2


def test_add_and_remove_item(tmp_path: Path):
    p = write_xml(tmp_path / "cfgRandomPresets.xml", SAMPLE_RANDOMPRESETS)
    rpf = parse_randompresets_file(p)
    preset = rpf.presets[0]
    assert len(preset.items) == 3
    new_item = add_item_to_preset(preset, "Kiwi", "0.10")
    assert len(preset.items) == 4
    assert new_item.name == "Kiwi"
    remove_item_from_preset(preset, new_item)
    assert len(preset.items) == 3


def test_round_trip(tmp_path: Path):
    p = write_xml(tmp_path / "cfgRandomPresets.xml", SAMPLE_RANDOMPRESETS)
    rpf = parse_randompresets_file(p)
    set_preset_chance(rpf.presets[0], "0.99")
    add_item_to_preset(rpf.presets[1], "Morphine", "0.40")
    save_randompresets_file(rpf)

    rpf2 = parse_randompresets_file(p)
    assert rpf2.presets[0].chance == "0.99"
    assert len(rpf2.presets[1].items) == 2
    assert rpf2.presets[1].items[1].name == "Morphine"


# ---------------------------------------------------------------------------
# The Random Presets table (mission_tables._randompresets_*)
# ---------------------------------------------------------------------------

# cfgrandompresets.xml is a direct-merge target: a mod extends a preset by
# shipping a second <cargo> of the same name inside its START/END MOD block.
MERGED_RANDOMPRESETS = """\
<?xml version="1.0" encoding="UTF-8"?>
<randompresets>
    <cargo chance="0.16" name="foodVillage">
        <item name="SodaCan_Cola" chance="0.02" />
        <item name="TunaCan" chance="0.05" />
    </cargo>
    <cargo chance="0.10" name="mixArmy">
        <item name="Rag" chance="0.10" />
        <item name="Rag" chance="0.30" />
    </cargo>
    <cargo chance="0.20" name="emptyOne" />
    <attachments chance="0.15" name="hatsVillage">
        <item name="FlatCap_Black" chance="0.07" />
    </attachments>
    <!-- START MOD: @MyMod -->
    <cargo chance="0.50" name="foodVillage">
        <item name="MyModFood" chance="0.40" />
    </cargo>
    <!-- END MOD: @MyMod -->
</randompresets>
"""


def _merged_file(tmp_mission_dir: Path) -> Path:
    """The fixture, already in save_xml's own formatting, so a save that changes
    nothing must leave the file byte for byte as it is."""
    p = write_xml(tmp_mission_dir / "cfgrandompresets.xml", MERGED_RANDOMPRESETS)
    save_xml(load_xml(p, preserve_comments=True), p)
    return p


def _values(rows: list[dict]) -> list[tuple[str, str, str, str]]:
    return [
        (r["values"]["preset"], r["values"]["preset_chance"], r["values"]["item"], r["values"]["chance"])
        for r in rows
    ]


def test_parse_and_save_keep_the_mod_markers(tmp_path: Path):
    p = write_xml(tmp_path / "cfgrandompresets.xml", MERGED_RANDOMPRESETS)
    save_randompresets_file(parse_randompresets_file(p))
    content = p.read_text()
    assert "<!-- START MOD: @MyMod -->" in content
    assert "<!-- END MOD: @MyMod -->" in content


def test_table_shows_every_cargo_and_item_even_when_names_repeat(sample_config, tmp_mission_dir: Path):
    _merged_file(tmp_mission_dir)
    rows = _randompresets_load(sample_config)["rows"]
    assert _values(rows) == [
        ("foodVillage", "0.16", "SodaCan_Cola", "0.02"),
        ("foodVillage", "0.16", "TunaCan", "0.05"),
        ("mixArmy", "0.10", "Rag", "0.10"),
        ("mixArmy", "0.10", "Rag", "0.30"),
        ("emptyOne", "0.20", "", ""),
        ("hatsVillage", "0.15", "FlatCap_Black", "0.07"),
        ("foodVillage", "0.50", "MyModFood", "0.40"),
    ]
    assert [r["values"]["kind"] for r in rows].count("attachments") == 1
    assert len({r["id"] for r in rows}) == len(rows)


def test_table_save_without_a_change_leaves_the_file_as_it_was(sample_config, tmp_mission_dir: Path):
    p = _merged_file(tmp_mission_dir)
    before = p.read_text()
    _randompresets_save(sample_config, _randompresets_load(sample_config)["rows"])
    assert p.read_text() == before


def test_table_save_adds_no_attribute_to_an_element_that_lacks_one(sample_config, tmp_mission_dir: Path):
    p = write_xml(
        tmp_mission_dir / "cfgrandompresets.xml",
        '<randompresets><cargo name="bare"><item name="Rag" /></cargo></randompresets>',
    )
    save_xml(load_xml(p, preserve_comments=True), p)
    before = p.read_text()
    _randompresets_save(sample_config, _randompresets_load(sample_config)["rows"])
    assert p.read_text() == before


def test_table_edits_the_row_it_was_given_not_its_namesake(sample_config, tmp_mission_dir: Path):
    p = _merged_file(tmp_mission_dir)
    rows = _randompresets_load(sample_config)["rows"]
    rows[3]["values"]["chance"] = "0.99"         # the second Rag
    rows[6]["values"]["preset_chance"] = "0.75"  # the mod's foodVillage
    rows[1]["values"]["item"] = "SardinesCan"    # TunaCan renamed where it stands
    _randompresets_save(sample_config, rows)

    assert _values(_randompresets_load(sample_config)["rows"]) == [
        ("foodVillage", "0.16", "SodaCan_Cola", "0.02"),
        ("foodVillage", "0.16", "SardinesCan", "0.05"),
        ("mixArmy", "0.10", "Rag", "0.10"),
        ("mixArmy", "0.10", "Rag", "0.99"),
        ("emptyOne", "0.20", "", ""),
        ("hatsVillage", "0.15", "FlatCap_Black", "0.07"),
        ("foodVillage", "0.75", "MyModFood", "0.40"),
    ]
    content = p.read_text()
    assert content.index("START MOD: @MyMod") < content.index("MyModFood") < content.index("END MOD: @MyMod")
    assert "hatsVillage" in content


@pytest.mark.parametrize("edited_row", [0, 1])
def test_a_preset_chance_changed_on_one_of_its_rows_is_kept(sample_config, tmp_mission_dir: Path, edited_row):
    """Every row of foodVillage repeats its chance. The row after the edited
    one carried the old value and set it back, with a 200."""
    _merged_file(tmp_mission_dir)
    rows = _randompresets_load(sample_config)["rows"]
    rows[edited_row]["values"]["preset_chance"] = "0.33"
    _randompresets_save(sample_config, rows)

    values = _values(_randompresets_load(sample_config)["rows"])
    assert [v[1] for v in values[:2]] == ["0.33", "0.33"]
    assert values[6][1] == "0.50"  # the mod's foodVillage is another preset


def test_two_new_chances_for_one_preset_are_refused(sample_config, tmp_mission_dir: Path):
    p = _merged_file(tmp_mission_dir)
    before = p.read_text()
    rows = _randompresets_load(sample_config)["rows"]
    rows[0]["values"]["preset_chance"] = "0.50"
    rows[1]["values"]["preset_chance"] = "0.60"
    with pytest.raises(ValueError, match="two rows give it different chances"):
        _randompresets_save(sample_config, rows)
    assert p.read_text() == before


def test_table_adds_deletes_and_moves_rows(sample_config, tmp_mission_dir: Path):
    p = _merged_file(tmp_mission_dir)
    rows = _randompresets_load(sample_config)["rows"]
    del rows[2]                                   # the first Rag
    rows[0]["values"]["preset"] = "foodCity"      # SodaCan_Cola moves to a new preset
    rows[3]["values"]["item"] = "Pear"            # the empty preset's blank row, filled in
    rows.append({"id": "new-1", "values": {"preset": "foodVillage", "preset_chance": "", "item": "Plum", "chance": ""}})
    _randompresets_save(sample_config, rows)

    assert _values(_randompresets_load(sample_config)["rows"]) == [
        ("foodVillage", "0.16", "TunaCan", "0.05"),
        ("foodVillage", "0.16", "Plum", "1.0"),   # joined the admin's preset, not the mod's
        ("mixArmy", "0.10", "Rag", "0.30"),
        ("emptyOne", "0.20", "Pear", "1.0"),
        ("hatsVillage", "0.15", "FlatCap_Black", "0.07"),
        ("foodVillage", "0.50", "MyModFood", "0.40"),
        ("foodCity", "0.16", "SodaCan_Cola", "0.02"),
    ]
    content = p.read_text()
    assert content.index("END MOD: @MyMod") < content.index("foodCity")


def test_table_removes_a_preset_whose_rows_are_all_deleted(sample_config, tmp_mission_dir: Path):
    p = _merged_file(tmp_mission_dir)
    rows = [r for r in _randompresets_load(sample_config)["rows"] if r["values"]["preset"] != "mixArmy"]
    _randompresets_save(sample_config, rows)
    content = p.read_text()
    assert "mixArmy" not in content
    assert content.count('name="foodVillage"') == 2
    assert "hatsVillage" in content


def test_an_attachments_preset_is_a_row_and_a_new_one_is_attachments(sample_config, tmp_mission_dir: Path):
    """Bohemia's files hold 32-35 <attachments> presets ("optics", "hatsFarm")
    the table never showed, and naming one created a <cargo name="optics">,
    which cfgspawnabletypes.xml's <attachments preset="optics"/> does not read."""
    p = _merged_file(tmp_mission_dir)
    rows = _randompresets_load(sample_config)["rows"]
    rows.append({"id": "new-1", "values": {
        "kind": "attachments", "preset": "optics", "preset_chance": "0.10",
        "item": "M4_CarryHandleOptic", "chance": "0.40",
    }})
    _randompresets_save(sample_config, rows)
    content = p.read_text()
    assert '<attachments chance="0.10" name="optics">' in content
    assert '<cargo chance="0.10" name="optics"' not in content
    assert '<item name="M4_CarryHandleOptic" chance="0.40" />' in content


@pytest.mark.parametrize(("key", "value", "words"), [
    ("preset_chance", "-0.1", "0 to 1"), ("preset_chance", "1.5", "0 to 1"),
    ("preset_chance", "50%", "0 to 1"), ("preset_chance", "nan", "0 to 1"),
    # An item's chance weighs it against the preset's other items: Sakhal's
    # stock attachments write 40.00, so only a negative or a non-number is wrong.
    ("chance", "50%", "not a number"), ("chance", "x", "not a number"),
    ("chance", "nan", "0 or more"), ("chance", "-1", "0 or more"),
])
def test_a_preset_chance_is_0_to_1_and_an_item_chance_0_or_more(sample_config, tmp_mission_dir: Path, key, value, words):
    p = _merged_file(tmp_mission_dir)
    before = p.read_text()
    rows = _randompresets_load(sample_config)["rows"]
    rows[0]["values"][key] = value
    with pytest.raises(ValueError, match=words):
        _randompresets_save(sample_config, rows)
    assert p.read_text() == before


def test_an_item_chance_above_1_is_written(sample_config, tmp_mission_dir: Path):
    p = _merged_file(tmp_mission_dir)
    rows = _randompresets_load(sample_config)["rows"]
    rows[0]["values"]["chance"] = "40.00"
    _randompresets_save(sample_config, rows)
    assert 'chance="40.00"' in p.read_text()


def test_a_chance_typed_as_the_same_number_is_not_rewritten(sample_config, tmp_mission_dir: Path):
    p = _merged_file(tmp_mission_dir)
    before = p.read_text()
    rows = _randompresets_load(sample_config)["rows"]
    rows[0]["values"]["chance"] = "0.020"
    rows[0]["values"]["preset_chance"] = "0.160"
    _randompresets_save(sample_config, rows)
    assert p.read_text() == before


def test_a_new_preset_writes_chance_before_name(tmp_path: Path):
    """Bohemia's own order: `<cargo chance="0.15" name="foodHermit">`."""
    p = write_xml(tmp_path / "cfgrandompresets.xml", SAMPLE_RANDOMPRESETS)
    rpf = parse_randompresets_file(p)
    add_cargo_preset(rpf, "foodHermit", "0.15")
    save_randompresets_file(rpf)
    assert '<cargo chance="0.15" name="foodHermit"' in p.read_text()
