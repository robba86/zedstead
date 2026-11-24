"""Tests for app/ops/toxic_zone.py — ToxicZone's zone files (plans/48, ToxicZone).

The documents are built from the key names and types plans/48's Step 0
recorded, with numbers of our own. The one exception is the default moving
zone's path and its wrong ``YtoOrigin``: they are the measurement the stale
check exists for.
"""

from __future__ import annotations

import json

import pytest

from app.ops import toxic_zone as tz
from app.ops.toxic_zone import build_report, derived, doctor_row
from tests.conftest import mods_lines, write_test_mods

MOD = "@ToxicZone"


def _static_zone(name="Plant", x=5000.0, y=6000.0, radius=300.0, square=0,
                 corners=(-1.0,) * 4, crates=((5000.0, 10.0, 6000.0),), loot="Kit"):
    x1, y1, x2, y2 = corners
    return {
        "ToxicZoneStatut": name, "X": x, "Y": y, "Radius": radius,
        "X1": x1, "Y1": y1, "X2": x2, "Y2": y2, "IsSquareZone": square,
        "IsMsgActive": 1, "MsgEnterZone": "in", "MsgExitZone": "out",
        "IsOnlyGasMask": 0, "IsLootActive": 1, "ContainerName": "Box",
        "NumbCratePerTime": 1, "MaxLoot": 4,
        "ParticlesPosition": [{"NName": "Smoke", "PPos": [x, 0.0, y]}],
        "ToxicCratePosition": [
            {"Name": loot, "POS": list(p), "ORI": [0.0, 0.0, 0.0]} for p in crates
        ],
        "CreatureList": [],
    }


def _moving_zone(name="Line", start=(1000.0, 2000.0), end=(3000.0, 3000.0), speed=20, **over):
    want = derived(*start, *end)
    zone = {
        "MovingToxicZoneStatut": name, "IsMsgActive": 1, "MsgEnterZone": "in",
        "MsgExitZone": "out", "StartX": start[0], "StartY": start[1],
        "EndX": end[0], "EndY": end[1], "Radius": 150.0,
        "Coeff": want.coeff if want else 0.0,
        "YtoOrigin": want.y_to_origin if want else 0.0,
        "Chance": 0.5, "Speed": speed, "Norme": want.norme if want else 0,
        "NbSickGiven": 40, "IsOnlyGasMask": 0, "ParticleName": "Smoke",
    }
    zone.update(over)
    return zone


def _moving(*zones, admins=("76561190000000001",), active=1) -> dict:
    return {
        "IsMovingZoneActive": active,
        "MovingToxicAreaLocation": list(zones) if zones else [_moving_zone()],
        "AdminUID": list(admins),
        "StaticToxicAreaLocation": [],
    }


def _static(*zones, active=1) -> dict:
    return {"IsToxicZoneActive": active, "StaticToxicAreaLocation": list(zones) if zones else [_static_zone()]}


def _write(cfg, name: str, doc) -> None:
    folder = tz.config_dir(cfg)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")


@pytest.fixture()
def tzc(sample_config):
    """An installed ToxicZone with valid files of our own."""
    cfg = sample_config
    write_test_mods(cfg, mods_lines(f"@CF\n{MOD}\n"))
    (cfg.server_dir_path / MOD).mkdir()
    _write(cfg, tz.GENERAL, {"ToxicBloodLoss": 10, "NbSickGiven": 5, "SuitsDamage": 2,
                             "GP5DamageGrenade": 3, "NbSickGivenforToxicSmoke": 4})
    _write(cfg, tz.CLOTHES, {"RequiredProtection": 5, "TZListSlotProtection": []})
    _write(cfg, tz.LOOT, {"TimerLoot": 600000, "IsActive": 1,
                          "ListLoots": [{"Name": "Kit", "Loots": []}]})
    _write(cfg, tz.STATIC, _static())
    _write(cfg, tz.MOVING, _moving())
    return cfg


def _ids(cfg) -> dict[str, dict]:
    return {f["id"]: f for f in build_report(cfg)["findings"]}


def _use_map(cfg, mission: str) -> None:
    cfg.server_cfg_path.write_text(
        f'class Missions {{ class DayZ {{ template = "{mission}"; }}; }};\n', encoding="utf-8"
    )


# ── the install ───────────────────────────────────────────────────────


def test_not_installed_means_no_report(sample_config):
    assert build_report(sample_config) is None
    assert doctor_row(sample_config) is None


def test_a_clean_install_passes(tzc):
    assert _ids(tzc) == {}
    label, ok, detail = doctor_row(tzc)
    assert label == "ToxicZone" and ok
    assert "1 zone" in detail and "1 moving" in detail


def test_before_the_first_boot_the_folder_is_a_notice(sample_config):
    write_test_mods(sample_config, mods_lines(f"{MOD}\n"))
    ids = _ids(sample_config)
    assert ids["toxiczone_folder_missing"]["severity"] == "notice"
    assert "5 files" in ids["toxiczone_folder_missing"]["why"]


def test_a_broken_file_is_an_error_naming_it(tzc):
    _write(tzc, tz.STATIC, "{not json")
    finding = _ids(tzc)["toxiczone_file_unreadable"]
    assert finding["severity"] == "error" and tz.STATIC in finding["title"]


def test_a_file_saved_in_ansi_is_the_same_error_not_a_crash(tzc):
    """plans/68 CON-11: a Windows-1252 file raised UnicodeDecodeError out of
    the report, so the mod's page answered 500."""
    (tz.config_dir(tzc) / tz.GENERAL).write_bytes('{"Name": "Café"}'.encode("cp1252"))
    finding = _ids(tzc)["toxiczone_file_unreadable"]
    assert tz.GENERAL in finding["title"] and "not UTF-8" in finding["detail"]


def test_a_decimal_in_a_whole_number_is_flagged(tzc):
    _write(tzc, tz.MOVING, _moving(_moving_zone(Speed=20.5)))
    assert "Speed = 20.5" in _ids(tzc)["toxiczone_decimal_in_whole_number"]["detail"]


def test_the_switches_off_are_notices(tzc):
    _write(tzc, tz.STATIC, _static(active=0))
    _write(tzc, tz.MOVING, _moving(active=0))
    ids = _ids(tzc)
    assert ids["toxiczone_static_off"]["severity"] == "notice"
    assert ids["toxiczone_moving_off"]["severity"] == "notice"


# ── the derived values (D3, D7, D8) ───────────────────────────────────


def test_derived_values_are_the_line_through_both_points():
    d = derived(2000.0, 5000.0, 7442.0, 8330.0)
    assert d.coeff == pytest.approx(0.611907387)
    # The intercept, not StartY: the mod's own default stores 5000 here.
    assert d.y_to_origin == pytest.approx(3776.185, abs=0.01)
    # D7: the east-west span, where the zone resets, not the 6380 m path.
    assert d.norme == 5442
    assert d.direction == 1
    assert derived(3000.0, 0.0, 1000.0, 0.0).direction == -1
    assert derived(1000.0, 0.0, 1000.0, 500.0) is None  # north-south


def test_apply_derived_writes_the_line_and_speeds_sign():
    entry = {"StartX": 3000.0, "StartY": 1000.0, "EndX": 1000.0, "EndY": 2000.0,
             "Coeff": 9.0, "YtoOrigin": 9.0, "Norme": 9, "Speed": 25, "Other": "kept"}
    tz.apply_derived(entry)
    assert entry["Coeff"] == pytest.approx(-0.5)
    assert entry["YtoOrigin"] == pytest.approx(2500.0)
    assert entry["Norme"] == 2000 and isinstance(entry["Norme"], int)
    assert entry["Speed"] == -25 and isinstance(entry["Speed"], int)
    assert entry["Other"] == "kept"
    with pytest.raises(ValueError, match="StartX and EndX are the same"):
        tz.apply_derived({"StartX": 5.0, "StartY": 0.0, "EndX": 5.0, "EndY": 9.0, "Speed": 1})
    with pytest.raises(ValueError, match="needs StartX"):
        tz.apply_derived({"StartX": 5.0})


def test_the_mods_own_default_moving_zone_is_stale(tzc):
    # Measured on a fresh install: the true slope, StartY as the intercept, and
    # the straight-line length as Norme.
    zone = _moving_zone(
        "ZelenToNovy", start=(2000.0, 5000.0), end=(7442.0, 8330.0), speed=30,
        Coeff=0.6119073629379272, YtoOrigin=5000.0, Norme=6379,
    )
    _write(tzc, tz.MOVING, _moving(zone))
    finding = _ids(tzc)["toxiczone_moving_stale"]
    assert finding["detail"] == "'ZelenToNovy': YtoOrigin, Norme"
    assert "Coeff" not in finding["detail"]  # float32 of the right slope is right
    ok, detail = doctor_row(tzc)[1:]
    assert not ok and "derived values" in detail


def test_a_zone_moving_the_wrong_way_is_an_error(tzc):
    east_to_west = _moving_zone(start=(3000.0, 1000.0), end=(1000.0, 2000.0), speed=20)
    _write(tzc, tz.MOVING, _moving(east_to_west))
    assert _ids(tzc)["toxiczone_moving_backwards"]["severity"] == "error"
    # D8: a negative Speed is how the mod moves west.
    _write(tzc, tz.MOVING, _moving({**east_to_west, "Speed": -20}))
    assert "toxiczone_moving_backwards" not in _ids(tzc)


def test_a_north_south_path_and_a_still_zone(tzc):
    vertical = _moving_zone("Up", start=(1000.0, 1000.0), end=(1000.0, 3000.0))
    still = _moving_zone("Still", speed=0)
    _write(tzc, tz.MOVING, _moving(vertical, still))
    ids = _ids(tzc)
    assert ids["toxiczone_moving_north_south"]["detail"] == "'Up'"
    assert ids["toxiczone_moving_still"]["detail"] == "'Still'"


def test_the_authors_admin_uid_is_a_notice(tzc):
    _write(tzc, tz.MOVING, _moving(admins=(tz.AUTHOR_UID,)))
    finding = _ids(tzc)["toxiczone_admin_uid_author"]
    assert finding["severity"] == "notice" and finding["title"].endswith("only")
    _write(tzc, tz.MOVING, _moving(admins=(tz.AUTHOR_UID, "76561190000000001")))
    assert not _ids(tzc)["toxiczone_admin_uid_author"]["title"].endswith("only")


# ── static zones, through zone_mods' shared checks ────────────────────


def test_static_zones_share_radzones_checks(tzc):
    _write(tzc, tz.STATIC, _static(_static_zone("A"), _static_zone("A", x=5100.0),
                                   _static_zone("Sq", square=1)))
    ids = _ids(tzc)
    assert ids["toxiczone_duplicate_zone"]["detail"] == "A"
    assert "ToxicZoneStatut" in ids["toxiczone_duplicate_zone"]["how"]
    assert ids["toxiczone_square_without_corners"]["detail"] == "'Sq'"


def test_chernarus_defaults_on_another_map(tzc):
    _use_map(tzc, "dayzOffline.enoch")
    _write(tzc, tz.STATIC, _static(_static_zone("TZKamensk", x=7890.0, y=14697.0, crates=())))
    _write(tzc, tz.MOVING, _moving(_moving_zone("ZelenToNovy", start=(2000.0, 5000.0), end=(7442.0, 8330.0))))
    ids = _ids(tzc)
    assert "Kamensk" in ids["toxiczone_chernarus_defaults"]["why"]
    assert ids["toxiczone_chernarus_defaults"]["detail"] == "TZKamensk"
    assert ids["toxiczone_zone_off_map"]["detail"] == "'TZKamensk'"  # 14697 > Livonia's edge
    assert ids["toxiczone_moving_chernarus_default"]["detail"] == "'ZelenToNovy'"


def test_crates_outside_their_zone_or_naming_no_loot(tzc):
    zone = _static_zone(crates=((5000.0, 10.0, 6000.0), (9000.0, 10.0, 9000.0)), loot="Nope")
    _write(tzc, tz.STATIC, _static(zone))
    ids = _ids(tzc)
    assert ids["toxiczone_crate_outside_zone"]["detail"] == "Plant: crate 2 at 9000, 9000"
    assert "Plant: crate 1 (Nope)" in ids["toxiczone_crate_loot_unknown"]["detail"]
    # Loot switched off for the zone: its crates are not checked.
    _write(tzc, tz.STATIC, _static({**zone, "IsLootActive": 0}))
    assert "toxiczone_crate_outside_zone" not in _ids(tzc)


# ── load order ────────────────────────────────────────────────────────


def test_after_expansions_spawn_selection_is_an_error(tzc, monkeypatch):
    from app.modmgr import mods

    picker = "@DayZ-Expansion-Spawn-Selection"
    write_test_mods(tzc, mods_lines(f"@CF\n{picker}\n{MOD}\n"))
    monkeypatch.setattr(mods, "get_resolved_mod_order", lambda c: ["@CF", picker, MOD])
    finding = _ids(tzc)["toxiczone_after_spawn_selection"]
    assert finding["severity"] == "error" and "Load Order" in finding["how"]
    monkeypatch.setattr(mods, "get_resolved_mod_order", lambda c: ["@CF", MOD, picker])
    assert "toxiczone_after_spawn_selection" not in _ids(tzc)


def test_the_catalog_loads_toxiczone_before_expansion():
    from app.server.service_control import _parse_workshop_registry
    from app.shared.seed_data import MOD_DEPS, WORKSHOP_MOD_IDS

    order = _parse_workshop_registry(WORKSHOP_MOD_IDS)
    assert order.index(MOD) < min(i for i, m in enumerate(order) if m.startswith("@DayZ-Expansion"))
    assert MOD_DEPS["1752669393"] == {"name": MOD, "deps": {"1559212036": "@CF"}}
