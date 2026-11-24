"""Tests for effectarea_json — parses BI vanilla cfgEffectArea.json schema.

Schema reference: ContaminatedAreaLoader.c reads `Areas[].{AreaName, Type,
TriggerType, Data{Pos, Radius, PosHeight, NegHeight, InnerPartDist,
OuterOffset, ParticleName}, PlayerData{...}}`. Top-level `SafePositions`
must round-trip verbatim — losing it on save would break the loot
scheduler's no-spawn override list.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import CE_1_29, bohemia_ce, ce_block, ce_lines, dayz_docs, write_json
from app.shared.platform import IS_WINDOWS

from app.web.editors.mission_tables import _effectarea_load, _effectarea_save
from app.parsers.effectarea_json import (
    EffectArea,
    EffectAreaFile,
    add_area,
    get_effectarea_path,
    parse_effectarea_file,
    remove_area,
    save_effectarea_file,
    update_area,
)


# Vanilla-style fixture: 2 zones (Ship-Bow and Pavlovo) plus a
# SafePositions array. Mirrors BI's chernarusplus/cfgEffectArea.json shape.
SAMPLE_DATA = {
    "Areas": [
        {
            "AreaName": "Ship-Bow",
            "Type": "ContaminatedArea_Static",
            "TriggerType": "ContaminatedTrigger",
            "Data": {
                "Pos": [13917.0, 0.0, 11178.0],
                "Radius": 75.0,
                "PosHeight": 20.0,
                "NegHeight": 3.0,
                "InnerPartDist": 80.0,
                "OuterOffset": 30.0,
                "ParticleName": "graphics/particles/contaminated_area_gas_bigass",
            },
            "PlayerData": {
                "AroundPartName": "graphics/particles/contaminated_area_gas_around",
                "TinyPartName": "graphics/particles/contaminated_area_gas_around_tiny",
                "PPERequesterType": "PPERequester_ContaminatedAreaTint",
            },
        },
        {
            "AreaName": "Pavlovo",
            "Type": "ContaminatedArea_Static",
            "TriggerType": "ContaminatedTrigger",
            "Data": {
                "Pos": [3700.0, 0.0, 5800.0],
                "Radius": 300.0,
                "PosHeight": 50.0,
                "NegHeight": 5.0,
                "InnerPartDist": 100.0,
                "OuterOffset": 50.0,
                "ParticleName": "graphics/particles/contaminated_area_gas_bigass",
            },
            "PlayerData": {
                "AroundPartName": "graphics/particles/contaminated_area_gas_around",
                "TinyPartName": "graphics/particles/contaminated_area_gas_around_tiny",
                "PPERequesterType": "PPERequester_ContaminatedAreaTint",
            },
        },
    ],
    "SafePositions": [
        {"Position": [1234.0, 0.0, 5678.0], "Radius": 50.0},
    ],
}


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


class TestGetEffectareaPath:
    def test_finds_root_file(self, tmp_path: Path):
        p = tmp_path / "cfgEffectArea.json"
        p.write_text("{}")
        assert get_effectarea_path(tmp_path) == p

    def test_finds_db_file(self, tmp_path: Path):
        db = tmp_path / "db"
        db.mkdir()
        p = db / "cfgEffectArea.json"
        p.write_text("{}")
        assert get_effectarea_path(tmp_path) == p

    def test_returns_none_when_missing(self, tmp_path: Path):
        assert get_effectarea_path(tmp_path) is None


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------


class TestParse:
    def test_parse_areas(self, tmp_path: Path):
        p = write_json(tmp_path / "cfgEffectArea.json", SAMPLE_DATA)
        eaf = parse_effectarea_file(p)
        assert len(eaf.areas) == 2

        ship = eaf.areas[0]
        assert ship.area_name == "Ship-Bow"
        assert ship.type == "ContaminatedArea_Static"
        assert ship.trigger_type == "ContaminatedTrigger"
        assert ship.pos == [13917.0, 0.0, 11178.0]
        assert ship.radius == 75.0
        assert ship.pos_height == 20.0
        assert ship.neg_height == 3.0
        assert ship.inner_part_dist == 80.0
        assert ship.outer_offset == 30.0
        assert ship.particle_name.endswith("gas_bigass")
        assert ship.around_part_name.endswith("gas_around")

    def test_parse_preserves_safe_positions(self, tmp_path: Path):
        p = write_json(tmp_path / "cfgEffectArea.json", SAMPLE_DATA)
        eaf = parse_effectarea_file(p)
        assert len(eaf.safe_positions) == 1
        assert eaf.safe_positions[0]["Position"] == [1234.0, 0.0, 5678.0]

    def test_parse_empty_file(self, tmp_path: Path):
        p = write_json(tmp_path / "cfgEffectArea.json", {})
        eaf = parse_effectarea_file(p)
        assert eaf.areas == []
        assert eaf.safe_positions == []

    def test_parse_missing_safe_positions(self, tmp_path: Path):
        # An Areas[] without SafePositions is valid (Livonia ships this way).
        data = {"Areas": SAMPLE_DATA["Areas"]}
        p = write_json(tmp_path / "cfgEffectArea.json", data)
        eaf = parse_effectarea_file(p)
        assert len(eaf.areas) == 2
        assert eaf.safe_positions == []


# ---------------------------------------------------------------------------
# Dataclass to_dict / from_dict
# ---------------------------------------------------------------------------


class TestEffectAreaDataclass:
    def test_round_trip_first_entry(self):
        d = SAMPLE_DATA["Areas"][0]
        a = EffectArea.from_dict(d)
        assert a.to_dict() == d

    def test_round_trip_second_entry(self):
        d = SAMPLE_DATA["Areas"][1]
        a = EffectArea.from_dict(d)
        assert a.to_dict() == d

    def test_defaults(self):
        a = EffectArea()
        assert a.type == "ContaminatedArea_Static"
        assert a.trigger_type == "ContaminatedTrigger"
        assert a.radius == 75.0
        assert a.pos_height == 20.0
        assert a.particle_name.endswith("gas_bigass")

    def test_from_dict_tolerates_missing_data_block(self):
        # Rare malformed entries should not crash; defaults take over.
        a = EffectArea.from_dict({"AreaName": "Loose"})
        assert a.area_name == "Loose"
        assert a.radius == 75.0  # default


# ---------------------------------------------------------------------------
# Save + round-trip
# ---------------------------------------------------------------------------


class TestSaveRoundTrip:
    def test_save_and_reload(self, tmp_path: Path):
        p = write_json(tmp_path / "cfgEffectArea.json", SAMPLE_DATA)
        eaf = parse_effectarea_file(p)
        save_effectarea_file(eaf)
        reloaded = parse_effectarea_file(p)
        assert len(reloaded.areas) == 2
        assert reloaded.areas[0].to_dict() == eaf.areas[0].to_dict()
        assert reloaded.areas[1].to_dict() == eaf.areas[1].to_dict()

    def test_save_preserves_safe_positions(self, tmp_path: Path):
        # The bug this whole rewrite addresses — SafePositions must
        # survive a parse/save round trip even though the editor
        # never touches it.
        p = write_json(tmp_path / "cfgEffectArea.json", SAMPLE_DATA)
        eaf = parse_effectarea_file(p)
        save_effectarea_file(eaf)
        on_disk = json.loads(p.read_text())
        assert "SafePositions" in on_disk
        assert on_disk["SafePositions"] == SAMPLE_DATA["SafePositions"]

    def test_save_writes_pascalcase_schema(self, tmp_path: Path):
        # Verify we're producing the BI vanilla shape, not the old
        # camelCase {staticEffectArea: [...]} schema.
        p = write_json(tmp_path / "cfgEffectArea.json", SAMPLE_DATA)
        eaf = parse_effectarea_file(p)
        save_effectarea_file(eaf)
        on_disk = json.loads(p.read_text())
        assert "Areas" in on_disk
        assert "staticEffectArea" not in on_disk
        assert "dynamicEffectArea" not in on_disk
        first = on_disk["Areas"][0]
        assert "AreaName" in first
        assert "Data" in first
        assert "Pos" in first["Data"]
        assert "PosHeight" in first["Data"]
        assert "PlayerData" in first

    def test_save_empty(self, tmp_path: Path):
        p = tmp_path / "cfgEffectArea.json"
        eaf = EffectAreaFile(path=p, areas=[], safe_positions=[])
        save_effectarea_file(eaf)
        reloaded = parse_effectarea_file(p)
        assert reloaded.areas == []
        assert reloaded.safe_positions == []


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestAreaCRUD:
    def test_add_area(self, tmp_path: Path):
        p = write_json(tmp_path / "cfgEffectArea.json", SAMPLE_DATA)
        eaf = parse_effectarea_file(p)
        new = EffectArea(area_name="Tisy", pos=[100.0, 0.0, 200.0], radius=500.0)
        add_area(eaf, new)
        assert len(eaf.areas) == 3
        assert eaf.areas[2].area_name == "Tisy"
        assert eaf.areas[2].radius == 500.0

    def test_remove_area(self, tmp_path: Path):
        p = write_json(tmp_path / "cfgEffectArea.json", SAMPLE_DATA)
        eaf = parse_effectarea_file(p)
        remove_area(eaf, 0)
        assert len(eaf.areas) == 1
        assert eaf.areas[0].area_name == "Pavlovo"

    def test_remove_invalid_index(self, tmp_path: Path):
        p = write_json(tmp_path / "cfgEffectArea.json", SAMPLE_DATA)
        eaf = parse_effectarea_file(p)
        remove_area(eaf, 99)
        assert len(eaf.areas) == 2  # unchanged

    def test_update_area(self, tmp_path: Path):
        p = write_json(tmp_path / "cfgEffectArea.json", SAMPLE_DATA)
        eaf = parse_effectarea_file(p)
        updated = EffectArea(area_name="Renamed", radius=999.0)
        update_area(eaf, 0, updated)
        assert eaf.areas[0].area_name == "Renamed"
        assert eaf.areas[0].radius == 999.0

    def test_update_invalid_index(self, tmp_path: Path):
        p = write_json(tmp_path / "cfgEffectArea.json", SAMPLE_DATA)
        eaf = parse_effectarea_file(p)
        original_radius = eaf.areas[0].radius
        update_area(eaf, 99, EffectArea())
        assert eaf.areas[0].radius == original_radius

    def test_crud_does_not_drop_safe_positions(self, tmp_path: Path):
        # Add/remove/update should leave SafePositions untouched.
        p = write_json(tmp_path / "cfgEffectArea.json", SAMPLE_DATA)
        eaf = parse_effectarea_file(p)
        original_safe = list(eaf.safe_positions)
        add_area(eaf, EffectArea(area_name="X"))
        remove_area(eaf, 0)
        update_area(eaf, 0, EffectArea(area_name="Y"))
        save_effectarea_file(eaf)
        reloaded = parse_effectarea_file(p)
        assert reloaded.safe_positions == original_safe


# ---------------------------------------------------------------------------
# A save keeps what it did not edit
# ---------------------------------------------------------------------------

# Bohemia's dayzOffline.sakhal/cfgeffectarea.json, read at the pinned 1.29
# commit, tabs and trailing spaces included: two of its five geysers, a hot
# spring and a volcano mouth. None is a contaminated area: no PlayerData, no
# particle keys, and its whole numbers are written whole.
SAKHAL_AREAS = ("Cluster1Geyser1", "Cluster5Geyser1", "Cluster1Area1A", "VolcanoMouth4")


def sakhal_excerpt() -> str:
    """SAKHAL_AREAS as the file writes them, under its own head and over its
    own last two lines. VolcanoMouth4 is the file's last area, so it alone has
    no comma after its brace. The file has no final newline; this text has one."""
    lines = ce_lines(bohemia_ce(CE_1_29, "dayzOffline.sakhal/cfgeffectarea.json"))
    areas = [line for name in SAKHAL_AREAS for line in ce_block(lines, f'"AreaName": "{name}"')]
    return "".join(lines[:2] + areas + lines[-2:]) + "\n"


# Invented, in the shape of the wiki's example area
# (contaminated-areas-configuration.wiki): five keys beside the ones the table
# shows, a boolean among them, an empty TriggerType, a mod's own key and block,
# and whole numbers.
WIKI_SHAPED_DATA = {
    "Areas": [
        {
            "AreaName": "TestMarsh-East",
            "Type": "ContaminatedArea_Static",
            "TriggerType": "",
            "Data": {
                "Pos": [7350, 120, 5210],
                "Radius": 180,
                "PosHeight": 30,
                "NegHeight": 15,
                "InnerRingCount": 3,
                "InnerPartDist": 45,
                "OuterRingToggle": True,
                "OuterPartDist": 25,
                "OuterOffset": -10,
                "VerticalLayers": 1,
                "VerticalOffset": 20,
                "ParticleName": "test_marsh_gas",
            },
            "PlayerData": {
                "AroundPartName": "test_marsh_gas_around",
                "TinyPartName": "test_marsh_gas_tiny",
                "PPERequesterType": "PPERequester_TestMarshTint",
                "TestModKey": [3, 4.5],
            },
        },
    ],
    "SafePositions": [],
    "TestModBlock": {"Level": 2},
}


def _typed(text: str) -> str:
    """The JSON re-serialised, so that 2 and 2.0 compare unequal, and so does
    a different key order."""
    return json.dumps(json.loads(text))


def _sakhal(folder: Path) -> Path:
    path = folder / "cfgeffectarea.json"
    path.write_text(sakhal_excerpt(), encoding="utf-8")
    return path


class TestSaveKeepsWhatItDidNotEdit:
    def test_an_unedited_file_saves_back_equal(self, tmp_path: Path):
        p = _sakhal(tmp_path)
        save_effectarea_file(parse_effectarea_file(p))
        assert _typed(p.read_text()) == _typed(sakhal_excerpt())
        # Not 11110.1: the number is written the way the file had it.
        assert "11110.10" in p.read_text()

    def test_documented_and_unknown_keys_survive(self, tmp_path: Path):
        p = write_json(tmp_path / "cfgEffectArea.json", WIKI_SHAPED_DATA)
        save_effectarea_file(parse_effectarea_file(p))
        assert _typed(p.read_text()) == json.dumps(WIKI_SHAPED_DATA)

    def test_an_edit_writes_that_key_and_no_other(self, tmp_path: Path):
        p = _sakhal(tmp_path)
        eaf = parse_effectarea_file(p)
        eaf.areas[0].radius = 3.0
        eaf.areas[2].inner_part_dist = 40.0
        save_effectarea_file(eaf)

        want = json.loads(sakhal_excerpt())
        want["Areas"][0]["Data"]["Radius"] = 3
        # A key the area did not have is written whole (the wiki's integer).
        want["Areas"][2]["Data"]["InnerPartDist"] = 40
        assert _typed(p.read_text()) == json.dumps(want)

    def test_a_fraction_in_a_whole_number_key_is_not_rounded_away(self, tmp_path: Path):
        p = _sakhal(tmp_path)
        eaf = parse_effectarea_file(p)
        eaf.areas[0].radius = 2.5
        eaf.areas[0].pos = [8906.0, 0.0, 10913.5]
        save_effectarea_file(eaf)

        data = json.loads(p.read_text())["Areas"][0]["Data"]
        assert _typed(json.dumps(data["Pos"])) == "[8906, 0, 10913.5]"
        assert data["Radius"] == 2.5

    def test_a_player_data_edit_on_an_area_without_one_adds_only_that_key(self, tmp_path: Path):
        p = _sakhal(tmp_path)
        eaf = parse_effectarea_file(p)
        eaf.areas[3].ppe_requester_type = ""
        save_effectarea_file(eaf)

        want = json.loads(sakhal_excerpt())
        want["Areas"][3]["PlayerData"] = {"PPERequesterType": ""}
        assert _typed(p.read_text()) == json.dumps(want)

    def test_a_new_area_is_a_contaminated_area_as_before(self, tmp_path: Path):
        p = _sakhal(tmp_path)
        eaf = parse_effectarea_file(p)
        add_area(eaf, EffectArea(area_name="Tisy", pos=[100.0, 0.0, 200.0]))
        save_effectarea_file(eaf)

        areas = json.loads(p.read_text())["Areas"]
        assert _typed(json.dumps(areas[:4])) == _typed(json.dumps(json.loads(sakhal_excerpt())["Areas"]))
        assert areas[4] == EffectArea(area_name="Tisy", pos=[100.0, 0.0, 200.0]).to_dict()
        assert areas[4]["Data"]["ParticleName"].endswith("gas_bigass")
        assert areas[4]["PlayerData"]["PPERequesterType"] == "PPERequester_ContaminatedAreaTint"


# ---------------------------------------------------------------------------
# The Effect Areas table (PUT /api/configs/effectarea)
# ---------------------------------------------------------------------------


def _rows(config) -> list[dict]:
    """The table's rows through JSON, as the browser gets and returns them."""
    return json.loads(json.dumps(_effectarea_load(config)["rows"]))


class TestEffectAreasTable:
    def test_saving_the_rows_as_loaded_changes_nothing(self, sample_config, tmp_mission_dir: Path):
        p = _sakhal(tmp_mission_dir)
        _effectarea_save(sample_config, _rows(sample_config))
        assert _typed(p.read_text()) == _typed(sakhal_excerpt())

    def test_the_wiki_s_keys_survive_a_table_save(self, sample_config, tmp_mission_dir: Path):
        p = write_json(tmp_mission_dir / "cfgEffectArea.json", WIKI_SHAPED_DATA)
        _effectarea_save(sample_config, _rows(sample_config))
        assert _typed(p.read_text()) == json.dumps(WIKI_SHAPED_DATA)

    def test_a_renamed_and_resized_row_keeps_the_rest_of_its_area(self, sample_config, tmp_mission_dir: Path):
        p = _sakhal(tmp_mission_dir)
        rows = _rows(sample_config)
        rows[0]["values"]["area_name"] = "OldFaithful"
        rows[0]["values"]["radius"] = 4
        _effectarea_save(sample_config, rows)

        want = json.loads(sakhal_excerpt())
        want["Areas"][0]["AreaName"] = "OldFaithful"
        want["Areas"][0]["Data"]["Radius"] = 4
        assert _typed(p.read_text()) == json.dumps(want)

    def test_a_deleted_row_removes_that_area_only(self, sample_config, tmp_mission_dir: Path):
        p = _sakhal(tmp_mission_dir)
        rows = _rows(sample_config)
        del rows[1]
        _effectarea_save(sample_config, rows)

        want = json.loads(sakhal_excerpt())
        del want["Areas"][1]
        assert _typed(p.read_text()) == json.dumps(want)

    def test_a_duplicated_row_carries_its_source_s_keys(self, sample_config, tmp_mission_dir: Path):
        p = _sakhal(tmp_mission_dir)
        rows = _rows(sample_config)
        copy = {"id": "dup-0-1790000000000", "values": {**rows[0]["values"], "area_name": "Copy"}}
        rows.insert(1, copy)
        _effectarea_save(sample_config, rows)

        want = json.loads(sakhal_excerpt())
        want["Areas"].insert(1, {**want["Areas"][0], "AreaName": "Copy"})
        assert _typed(p.read_text()) == json.dumps(want)

    def test_each_row_carries_its_data_and_player_data(self, sample_config, tmp_mission_dir: Path):
        write_json(tmp_mission_dir / "cfgEffectArea.json", WIKI_SHAPED_DATA)
        nested = _rows(sample_config)[0]["nested"]
        area = WIKI_SHAPED_DATA["Areas"][0]
        assert nested == {"Data": area["Data"], "PlayerData": area["PlayerData"]}
        (tmp_mission_dir / "cfgEffectArea.json").unlink()
        _sakhal(tmp_mission_dir)
        assert set(_rows(sample_config)[0]["nested"]) == {"Data"}  # a geyser has no PlayerData

    def test_a_tree_edit_keeps_the_file_s_types(self, sample_config, tmp_mission_dir: Path):
        p = _sakhal(tmp_mission_dir)
        rows = _rows(sample_config)
        data = {**rows[0]["nested"]["Data"], "EffectInterval": "3600", "Radius": "3"}
        rows[0]["values"]["$nested"] = {"Data": data}
        rows[0]["values"]["$nested_base"] = {"Data": rows[0]["nested"]["Data"]}
        _effectarea_save(sample_config, rows)

        want = json.loads(sakhal_excerpt())
        want["Areas"][0]["Data"]["EffectInterval"] = 3600
        want["Areas"][0]["Data"]["Radius"] = 3
        assert _typed(p.read_text()) == json.dumps(want)

    def test_a_tree_edit_takes_a_decimal_where_the_wiki_says_float(self, sample_config, tmp_mission_dir: Path):
        p = _sakhal(tmp_mission_dir)
        rows = _rows(sample_config)
        base = rows[0]["nested"]["Data"]
        rows[0]["values"]["$nested"] = {"Data": {**base, "Radius": "2.5"}}
        rows[0]["values"]["$nested_base"] = {"Data": base}
        _effectarea_save(sample_config, rows)
        assert json.loads(p.read_text())["Areas"][0]["Data"]["Radius"] == 2.5

        rows = _rows(sample_config)
        base = rows[0]["nested"]["Data"]
        rows[0]["values"]["$nested"] = {"Data": {**base, "EffectInterval": "2.5"}}
        rows[0]["values"]["$nested_base"] = {"Data": base}
        with pytest.raises(ValueError, match="EffectInterval: '2.5' is not a whole number"):
            _effectarea_save(sample_config, rows)

    def test_a_changed_column_wins_over_the_tree_s_copy(self, sample_config, tmp_mission_dir: Path):
        p = _sakhal(tmp_mission_dir)
        rows = _rows(sample_config)
        rows[0]["values"]["radius"] = 6
        data = {**rows[0]["nested"]["Data"], "Radius": 4, "NegHeight": 7}
        rows[0]["values"]["$nested"] = {"Data": data}
        rows[0]["values"]["$nested_base"] = {"Data": rows[0]["nested"]["Data"]}
        _effectarea_save(sample_config, rows)
        saved = json.loads(p.read_text())["Areas"][0]["Data"]
        assert saved["Radius"] == 6 and saved["NegHeight"] == 7

    def test_a_tree_edit_over_a_changed_file_is_refused(self, sample_config, tmp_mission_dir: Path):
        p = _sakhal(tmp_mission_dir)
        rows = _rows(sample_config)
        other = json.loads(sakhal_excerpt())
        other["Areas"][0]["Data"]["EffectDuration"] = 60
        write_json(p, other)
        before = p.read_text()
        rows[0]["values"]["$nested"] = {"Data": {**rows[0]["nested"]["Data"], "Radius": 9}}
        rows[0]["values"]["$nested_base"] = {"Data": rows[0]["nested"]["Data"]}
        with pytest.raises(ValueError, match="Cluster1Geyser1: Data changed in the file"):
            _effectarea_save(sample_config, rows)
        assert p.read_text() == before

    def test_a_duplicated_row_takes_its_edited_tree(self, sample_config, tmp_mission_dir: Path):
        p = _sakhal(tmp_mission_dir)
        rows = _rows(sample_config)
        base = rows[0]["nested"]["Data"]
        copy = {
            "id": "dup-0-1790000000000",
            "values": {**rows[0]["values"], "area_name": "Copy",
                       "$nested": {"Data": {**base, "EffectDuration": 90}}, "$nested_base": {"Data": base}},
        }
        rows.insert(1, copy)
        _effectarea_save(sample_config, rows)

        areas = json.loads(p.read_text())["Areas"]
        assert areas[0]["Data"]["EffectDuration"] == 180
        assert areas[1]["AreaName"] == "Copy" and areas[1]["Data"]["EffectDuration"] == 90

    def test_a_new_row_is_a_contaminated_area_as_before(self, sample_config, tmp_mission_dir: Path):
        p = _sakhal(tmp_mission_dir)
        rows = _rows(sample_config)
        rows.append({"id": "new-4-1790000000000", "values": {"area_name": "Tisy", "pos": "100,0,200", "radius": 50}})
        _effectarea_save(sample_config, rows)

        areas = json.loads(p.read_text())["Areas"]
        assert _typed(json.dumps(areas[:4])) == _typed(json.dumps(json.loads(sakhal_excerpt())["Areas"]))
        assert areas[4] == EffectArea(area_name="Tisy", pos=[100.0, 0.0, 200.0], radius=50.0).to_dict()

    @pytest.mark.parametrize(
        "mission", ["dayzOffline.chernarusplus", "dayzOffline.enoch", "dayzOffline.sakhal"]
    )
    def test_a_no_change_save_of_a_stock_file_is_the_same_json(
        self, mission: str, sample_config, tmp_mission_dir: Path
    ):
        stock = next(
            f for f in dayz_docs("vanilla", mission).iterdir() if f.name.lower() == "cfgeffectarea.json"
        )
        before = stock.read_text(encoding="utf-8-sig")
        p = tmp_mission_dir / stock.name
        p.write_text(before, encoding="utf-8")
        _effectarea_save(sample_config, _rows(sample_config))
        assert p.read_text(encoding="utf-8") == before

    def test_a_no_change_save_keeps_crlf_byte_for_byte(self, sample_config, tmp_mission_dir: Path):
        text = sakhal_excerpt().replace("\n", "\r\n").encode("utf-8")
        p = tmp_mission_dir / "cfgeffectarea.json"
        p.write_bytes(text)
        _effectarea_save(sample_config, _rows(sample_config))
        assert p.read_bytes() == text

    def test_an_edit_changes_its_own_line_and_keeps_crlf(self, sample_config, tmp_mission_dir: Path):
        text = sakhal_excerpt().replace("\n", "\r\n")
        p = tmp_mission_dir / "cfgeffectarea.json"
        p.write_bytes(text.encode("utf-8"))
        rows = _rows(sample_config)
        rows[2]["values"]["radius"] = "13"
        _effectarea_save(sample_config, rows)
        assert p.read_bytes() == text.replace('"Radius": 12,', '"Radius": 13,').encode("utf-8")

    @pytest.mark.parametrize("field_name", ["inner_part_dist", "outer_offset"])
    def test_a_fraction_in_an_integer_key_is_refused(self, field_name, sample_config, tmp_mission_dir: Path):
        # contaminated-areas-configuration.wiki:98, :101 mark both "integer".
        p = write_json(tmp_mission_dir / "cfgEffectArea.json", WIKI_SHAPED_DATA)
        before = p.read_bytes()
        rows = _rows(sample_config)
        rows[0]["values"][field_name] = "35.5"
        with pytest.raises(ValueError, match=f"TestMarsh-East: {field_name} must be a whole number"):
            _effectarea_save(sample_config, rows)
        assert p.read_bytes() == before

    def test_an_integer_key_is_written_whole(self, sample_config, tmp_mission_dir: Path):
        p = write_json(tmp_mission_dir / "cfgEffectArea.json", SAMPLE_DATA)
        rows = _rows(sample_config)
        rows[0]["values"]["inner_part_dist"] = "90.0"
        rows[0]["values"]["outer_offset"] = 25
        _effectarea_save(sample_config, rows)
        data = json.loads(p.read_text())["Areas"][0]["Data"]
        assert _typed(json.dumps([data["InnerPartDist"], data["OuterOffset"]])) == "[90, 25]"

    def test_a_fraction_the_file_already_has_is_kept_untouched(self, sample_config, tmp_mission_dir: Path):
        data = json.loads(json.dumps(WIKI_SHAPED_DATA))
        data["Areas"][0]["Data"]["InnerPartDist"] = 35.5
        p = write_json(tmp_mission_dir / "cfgEffectArea.json", data)
        before = p.read_bytes()
        _effectarea_save(sample_config, _rows(sample_config))
        assert p.read_bytes() == before

    @pytest.mark.parametrize("pos", ["100,200", "1,2,3,4", "1,,3", "nan,0,1", "1,0,inf", "a,b,c"])
    def test_a_position_that_is_not_three_numbers_is_refused(self, pos, sample_config, tmp_mission_dir: Path):
        p = _sakhal(tmp_mission_dir)
        before = p.read_bytes()
        rows = _rows(sample_config)
        rows[1]["values"]["pos"] = pos
        with pytest.raises(ValueError, match="Cluster5Geyser1: Pos"):
            _effectarea_save(sample_config, rows)
        assert p.read_bytes() == before

    def test_a_new_row_writes_whole_numbers_whole(self, sample_config, tmp_mission_dir: Path):
        p = _sakhal(tmp_mission_dir)
        rows = _rows(sample_config)
        rows.append({"id": "new-4-1790000000000",
                     "values": {"area_name": "Radunin", "pos": "7347,0,6410", "radius": "150"}})
        _effectarea_save(sample_config, rows)
        text = p.read_text()
        new = json.loads(text)["Areas"][4]["Data"]
        assert _typed(json.dumps([new["Pos"], new["Radius"], new["InnerPartDist"]])) == "[[7347, 0, 6410], 150, 80]"
        assert "150.0" not in text

    def test_a_new_row_names_itself_in_a_refusal(self, sample_config, tmp_mission_dir: Path):
        _sakhal(tmp_mission_dir)
        rows = _rows(sample_config)
        rows.append({"id": "new-4-1790000000000", "values": {"area_name": "", "pos": "1,2"}})
        with pytest.raises(ValueError, match="Row 5: Pos"):
            _effectarea_save(sample_config, rows)


class TestGetEffectareaPathAnyCase:
    def test_a_third_spelling_is_found(self, tmp_path: Path):
        # Only cfgEffectArea.json and cfgeffectarea.json were looked for.
        p = tmp_path / "CfgEffectArea.json"
        p.write_text("{}")
        assert get_effectarea_path(tmp_path) == p

    @pytest.mark.skipif(IS_WINDOWS, reason="two spellings are one file on Windows")
    def test_a_shipped_spelling_wins_and_the_root_before_db(self, tmp_path: Path):
        (tmp_path / "db").mkdir()
        (tmp_path / "db" / "cfgEffectArea.json").write_text("{}")
        (tmp_path / "CFGEFFECTAREA.json").write_text("{}")
        assert get_effectarea_path(tmp_path) == tmp_path / "CFGEFFECTAREA.json"
        (tmp_path / "cfgeffectarea.json").write_text("{}")
        assert get_effectarea_path(tmp_path) == tmp_path / "cfgeffectarea.json"

    def test_no_mission_dir(self, tmp_path: Path):
        assert get_effectarea_path(tmp_path / "missing") is None
        assert get_effectarea_path(None) is None


def test_the_particle_help_follows_the_wiki():
    """contaminated-areas-configuration.wiki:23-27, :50-51: InnerPartDist is
    the emitter spacing (partSize), OuterOffset the outwards bleed."""
    from app.parsers.effectarea_json import FIELD_DESCRIPTIONS

    assert "partSize" in FIELD_DESCRIPTIONS["inner_part_dist"]
    assert "outwardsBleed" in FIELD_DESCRIPTIONS["outer_offset"]
    assert "ring" not in FIELD_DESCRIPTIONS["inner_part_dist"]
