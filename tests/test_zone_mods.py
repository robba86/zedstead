"""Tests for app/ops/zone_mods.py — RadZone's zone and crate files (plans/48 A).

The fixture documents are built from the key names and types plans/48's Step 0
recorded; nothing from the mod's own files is copied. The two Chernarus
default centres are the one exception, and they are map coordinates, not text.
"""

from __future__ import annotations

import json

import pytest

from app.ops import zone_mods
from app.ops.zone_mods import CHERNARUS_DEFAULTS, build_report, doctor_row
from tests.conftest import mods_lines, write_test_ids, write_test_mods

MOD = "@RadZone"


def _zone(name="North", x=5000.0, y=6000.0, radius=300.0, square=0, corners=(-1.0,) * 4):
    x1, y1, x2, y2 = corners
    return {
        "RadiationZoneStatut": name, "X": x, "Y": y, "Radius": radius,
        "IsSquareZone": square, "X1": x1, "Y1": y1, "X2": x2, "Y2": y2,
        "IsMsgActive": 1, "MsgEnterZone": "in", "MsgExitZone": "out",
        "NbSickGivenForRadiation": 50,
    }


def _radiation(*zones, active=1) -> dict:
    return {
        "IsRadiationZoneActive": active, "RadChanceVomit": 0.1, "RadChanceBleeding": 0.1,
        "RadBloodLossDamage": 50.0, "RadHealthDamage": 1.0, "RadHighMultiplier": 2.0,
        "QtyRadReducedPerMin": 25.0, "NaturalRadDecayPerMin": 1.0, "SuitsDamage": 5,
        "RequiredProtection": 6, "CriticalProtection": 70,
        "ClothesQuantityMultiplier": 0.1, "AmountGivenAfterCriticalProtection": 0.1,
        "UseChatMessagesInsteadOfNotifications": 0,
        "RadiationAreaLocation": list(zones) if zones else [_zone()],
    }


def _area(name="North", *positions, loot="Army"):
    return {
        "RZName": name, "RZNbCrate": 1, "CreaturesLists": [],
        "RZCrateLocations": [
            {"RZContainer": "Box", "RZMaxLoot": 5, "Name": loot, "POS": list(p),
             "ORI": [0.0, 0.0, 0.0]}
            for p in (positions or [(5000.0, 100.0, 6000.0)])
        ],
    }


def _crates(*areas, active=1) -> dict:
    return {
        "ListLoots": [{"Name": "Army", "Loots": []}],
        "AreaLocation": list(areas) if areas else [_area()],
        "TimerLoot": 2700000, "IsCrateSystemActive": active,
    }


def _write(cfg, name: str, doc) -> None:
    folder = zone_mods.config_dir(cfg)
    folder.mkdir(parents=True, exist_ok=True)
    text = doc if isinstance(doc, str) else json.dumps(doc)
    (folder / name).write_text(text, encoding="utf-8")


@pytest.fixture()
def rz(sample_config):
    """An installed RadZone with valid files: one zone and one crate inside it."""
    cfg = sample_config
    write_test_mods(cfg, mods_lines(f"@CF\n{MOD}\n"))
    (cfg.server_dir_path / MOD).mkdir()
    _write(cfg, zone_mods.RADIATION, _radiation())
    _write(cfg, zone_mods.CRATES, _crates())
    _write(cfg, zone_mods.CLOTHES, {"RZListSlotProtection": []})
    return cfg


def _ids(cfg) -> dict[str, dict]:
    return {f["id"]: f for f in build_report(cfg)["findings"]}


def _use_map(cfg, mission: str) -> None:
    cfg.server_cfg_path.write_text(
        f'class Missions {{ class DayZ {{ template = "{mission}"; }}; }};\n', encoding="utf-8"
    )


# ── the install ───────────────────────────────────────────────────────


def test_a_clean_install_passes_and_counts_its_zones(rz):
    assert _ids(rz) == {}
    assert doctor_row(rz) == ("RadZone", True, "Setup OK · 1 zone")


def test_no_report_and_no_doctor_row_without_the_mod(rz):
    write_test_mods(rz, ['@CF'])
    assert build_report(rz) is None
    assert doctor_row(rz) is None


def test_a_renamed_folder_is_still_found_by_workshop_id(rz):
    write_test_mods(rz, ['@CF', '@RadZoneRenamed'])
    write_test_ids({'@RadZoneRenamed': '2294323732'})
    assert build_report(rz)["mod"] == "@RadZoneRenamed"


def test_the_report_never_writes(rz):
    before = {p: p.read_bytes() for p in rz.server_dir_path.rglob("*") if p.is_file()}
    build_report(rz)
    after = {p: p.read_bytes() for p in rz.server_dir_path.rglob("*") if p.is_file()}
    assert after == before


def test_a_missing_folder_says_the_first_boot_writes_it(rz):
    import shutil

    shutil.rmtree(zone_mods.config_dir(rz))
    found = _ids(rz)
    assert list(found) == ["radzone_folder_missing"]
    assert found["radzone_folder_missing"]["severity"] == "notice"
    ok, detail = doctor_row(rz)[1:]
    assert ok and "config folder does not exist yet" in detail


def test_a_broken_file_is_an_error_naming_the_position(rz):
    _write(rz, zone_mods.RADIATION, '{"RadiationAreaLocation": [}')
    finding = _ids(rz)["radzone_file_unreadable"]
    assert finding["severity"] == "error"
    assert "RadiationConfigV1.json" in finding["title"]
    assert "line 1" in finding["detail"]
    assert doctor_row(rz)[1] is False


def test_a_missing_file_is_a_notice(rz):
    (zone_mods.config_dir(rz) / zone_mods.CLOTHES).unlink()
    assert _ids(rz)["radzone_file_missing"]["title"].startswith(zone_mods.CLOTHES)


def test_zones_switched_off_is_a_notice_on_a_passing_row(rz):
    _write(rz, zone_mods.RADIATION, _radiation(active=0))
    assert _ids(rz)["radzone_zones_off"]["severity"] == "notice"
    ok, detail = doctor_row(rz)[1:]
    assert ok and "switched off" in detail


# ── zones ─────────────────────────────────────────────────────────────


def test_two_zones_with_one_name(rz):
    _write(rz, zone_mods.RADIATION, _radiation(_zone("A"), _zone("A", x=100.0), _zone("B")))
    assert _ids(rz)["radzone_duplicate_zone"]["detail"] == "A"


def test_a_square_zone_with_a_corner_left_unset(rz):
    _write(rz, zone_mods.RADIATION, _radiation(_zone(square=1, corners=(10.0, 10.0, -1.0, 500.0))))
    found = _ids(rz)
    assert found["radzone_square_without_corners"]["detail"] == "'North'"
    # Its unset corner is not also reported as off the map.
    assert "radzone_zone_off_map" not in found


def test_a_square_zone_with_its_corners_swapped(rz):
    _write(rz, zone_mods.RADIATION, _radiation(_zone(square=1, corners=(900.0, 10.0, 100.0, 500.0))))
    assert "probably never" in _ids(rz)["radzone_square_flipped"]["why"]


def test_a_good_square_zone_passes(rz):
    _write(rz, zone_mods.RADIATION, _radiation(_zone(square=1, corners=(4800.0, 5800.0, 5200.0, 6200.0))))
    assert _ids(rz) == {}


def test_a_zone_beyond_the_maps_edge(rz):
    _use_map(rz, "dayzOffline.enoch")  # 12800 m across
    _write(rz, zone_mods.RADIATION, _radiation(_zone("East", x=13000.0), _zone("Fine")))
    _write(rz, zone_mods.CRATES, _crates(_area("Fine")))
    finding = _ids(rz)["radzone_zone_off_map"]
    assert finding["detail"] == "'East'"
    assert "Livonia" in finding["why"]


def test_an_unknown_map_gets_no_bounds_check(rz):
    # No size to judge by: a community map not in the catalog.
    _use_map(rz, "dayzOffline.someisland")
    _write(rz, zone_mods.RADIATION, _radiation(_zone(x=99999.0)))
    _write(rz, zone_mods.CRATES, _crates(_area("North", (99999.0, 1.0, 6000.0))))
    assert _ids(rz) == {}


def test_the_chernarus_defaults_on_another_map(rz):
    _use_map(rz, "dayzOffline.enoch")
    zones = [_zone(name, x=x, y=y) for name, (x, y) in CHERNARUS_DEFAULTS.items()]
    _write(rz, zone_mods.RADIATION, _radiation(*zones))
    _write(rz, zone_mods.CRATES, _crates(active=0))
    finding = _ids(rz)["radzone_chernarus_defaults"]
    assert finding["detail"] == "NWAF, NEAF"
    assert "Livonia" in finding["why"]


def test_the_chernarus_defaults_on_chernarus_are_fine(rz):
    zones = [_zone(name, x=x, y=y) for name, (x, y) in CHERNARUS_DEFAULTS.items()]
    _write(rz, zone_mods.RADIATION, _radiation(*zones))
    _write(rz, zone_mods.CRATES, _crates(active=0))
    assert _ids(rz) == {}


def test_a_moved_zone_that_kept_its_default_name_is_not_a_default(rz):
    _use_map(rz, "dayzOffline.enoch")
    _write(rz, zone_mods.RADIATION, _radiation(_zone("NWAF", x=3000.0, y=3000.0)))
    _write(rz, zone_mods.CRATES, _crates(active=0))
    assert "radzone_chernarus_defaults" not in _ids(rz)


# ── crates ────────────────────────────────────────────────────────────


def test_a_crate_area_naming_no_zone(rz):
    _write(rz, zone_mods.CRATES, _crates(_area("North"), _area("Nowhere")))
    finding = _ids(rz)["radzone_crate_area_unmatched"]
    assert finding["detail"] == "'Nowhere'"
    assert "probably" in finding["why"]


def test_a_crate_outside_its_zone_reads_x_and_z_not_height(rz):
    # POS is x, height, z: a crate at height 6000 is not the point being judged.
    _write(rz, zone_mods.CRATES, _crates(_area("North", (5000.0, 6000.0, 6000.0), (9000.0, 5.0, 6000.0))))
    finding = _ids(rz)["radzone_crate_outside_zone"]
    assert finding["detail"] == "North: crate 2 at 9000, 6000"


def test_a_crate_inside_a_square_zone(rz):
    _write(rz, zone_mods.RADIATION, _radiation(_zone(square=1, corners=(0.0, 0.0, 100.0, 100.0))))
    _write(rz, zone_mods.CRATES, _crates(_area("North", (50.0, 1.0, 50.0), (150.0, 1.0, 50.0))))
    assert _ids(rz)["radzone_crate_outside_zone"]["detail"] == "North: crate 2 at 150, 50"


def test_crates_are_not_judged_while_the_crate_system_is_off(rz):
    _write(rz, zone_mods.CRATES, _crates(_area("Nowhere"), active=0))
    assert _ids(rz) == {}


def test_the_doctor_runs_the_row_only_with_the_mod(rz, monkeypatch):
    from app.commands import doctor

    assert doctor._check_radzone(rz)[0] == "RadZone"
    write_test_mods(rz, ['@CF'])
    assert doctor._check_radzone(rz) is None

    def _boom(config):
        raise RuntimeError("bad")

    write_test_mods(rz, mods_lines(f"{MOD}\n"))
    monkeypatch.setattr(zone_mods, "doctor_row", _boom)
    assert doctor._check_radzone(rz) == ("RadZone", False, "Check failed: bad")


def test_the_catalog_installs_radzone_by_name_with_cf():
    from app.shared.seed_data import MOD_DEPS, WORKSHOP_MOD_IDS

    assert "@RadZone=2294323732" in WORKSHOP_MOD_IDS.splitlines()
    assert MOD_DEPS["2294323732"] == {"name": "@RadZone", "deps": {"1559212036": "@CF"}}


def test_a_crate_naming_no_loot_list(rz):
    _write(rz, zone_mods.CRATES, _crates(_area("North", loot="Army"), _area("North", loot="Gone")))
    finding = _ids(rz)["radzone_crate_loot_unknown"]
    assert finding["detail"] == "North: crate 1 (Gone)"


def test_a_decimal_in_a_whole_number_field(rz):
    doc = _radiation(_zone())
    doc["RequiredProtection"] = 6.0
    doc["RadiationAreaLocation"][0]["NbSickGivenForRadiation"] = 50.5
    _write(rz, zone_mods.RADIATION, doc)
    finding = _ids(rz)["radzone_decimal_in_whole_number"]
    assert finding["detail"] == (
        "RequiredProtection = 6.0, RadiationAreaLocation[0].NbSickGivenForRadiation = 50.5"
    )
    # Float fields are floats: a whole value there is fine.
    assert zone_mods.decimals_in_whole_numbers({"X": 5000.0, "Radius": 300}) == []

