"""Tests for app/ops/dynamic_ai.py — Dynamic AI's settings against the mod's
own load rules (plans/47 B). Fixture documents are built here from the key
names; nothing from the mod's own files is copied."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ops import dynamic_ai
from app.ops.dynamic_ai import build_report, doctor_row, read_last_boot
from tests.conftest import mods_lines, set_test_flags, write_test_ids, write_test_mods

MOD = "@DayZ-Dynamic-AI-Addon"
FACTIONS_PBO = b"\x00junk eAIRegisterFaction(eAIFactionEast); eAIRegisterFaction(eAIFactionWest) \x00"


def _group(name="Guard", loadout="EastLoadout.json", faction="East", lo=1, hi=3):
    return {
        "Spatial_MinCount": lo, "Spatial_MaxCount": hi, "Spatial_Weight": 1.0,
        "Spatial_Loadout": loadout, "Spatial_Faction": faction, "Spatial_Name": name,
        "Spatial_Lootable": 1, "Spatial_Chance": 0.5, "Spatial_MinAccuracy": 0.25,
        "Spatial_MaxAccuracy": 0.66, "Spatial_UnlimitedReload": 0,
    }


def _point(name="Zone", loadouts=("EastLoadout.json",), faction="East", **over):
    point = {
        "Spatial_Name": name, "Spatial_Safe": 0, "Spatial_Radius": 100.0,
        "Spatial_ZoneLoadout": list(loadouts), "Spatial_MinCount": 1, "Spatial_MaxCount": 2,
        "Spatial_HuntMode": 1, "Spatial_Faction": faction, "Spatial_Lootable": 1,
        "Spatial_Chance": 1.0, "Spatial_MinAccuracy": 0.25, "Spatial_MaxAccuracy": 0.66,
        "Spatial_UnlimitedReload": 0, "Spatial_Position": [0.0, 1.0, 0.0],
    }
    point.update(over)
    return point


def _location(name="Tower", loadout="EastLoadout.json", faction="East"):
    return {
        "Spatial_Name": name, "Spatial_TriggerRadius": 50.0, "Spatial_ZoneLoadout": loadout,
        "Spatial_MinCount": 1, "Spatial_MaxCount": 2, "Spatial_HuntMode": 1,
        "Spatial_Faction": faction, "Spatial_Lootable": 1, "Spatial_Chance": 1.0,
        "Spatial_MinAccuracy": 0.25, "Spatial_Timer": 5.0, "Spatial_SpawnMode": 0,
        "Spatial_UnlimitedReload": 0, "Spatial_TriggerPosition": [0.0, 1.0, 0.0],
        "Spatial_SpawnPosition": [[0.0, 1.0, 0.0]],
    }


def _settings(**over) -> dict:
    doc = {
        "Version": 21, "Spatial_MinTimer": 20.0, "Spatial_MaxTimer": 40.0,
        "MinDistance": 140, "MaxDistance": 220, "HuntMode": 1, "Points_Enabled": 0,
        "Locations_Enabled": 0, "Audio_Enabled": 0, "EngageTimer": 5.0, "CleanupTimer": 6.0,
        "PlayerChecks": -5, "MaxAI": 20, "GroupDifficulty": 0, "MinimumPlayerDistance": 0,
        "MaxSoloPlayers": 0, "MinimumAge": 0, "ActiveHoursEnabled": 0, "ActiveStartTime": 0.0,
        "ActiveStopTime": 24.0, "TargetBone": "Neck", "MessageType": 1,
        "MessageTitle": "Spatial AI", "MessageText": "AI nearby", "LootWhitelist": [],
        "Group": [_group()], "Point": [], "Location": [], "Audio": [],
    }
    doc.update(over)
    return doc


@pytest.fixture()
def dyn(sample_config):
    """An installed, server-side Dynamic AI with a valid settings file, a
    loadout folder and one Expansion AI PBO registering East and West."""
    cfg = sample_config
    write_test_mods(cfg, mods_lines(f"@DayZ-Expansion-AI\n{MOD}\n"))
    set_test_flags(cfg, server_only=[MOD])
    cfg.save()
    addons = cfg.server_dir_path / "@DayZ-Expansion-AI" / "addons"
    addons.mkdir(parents=True)
    (addons / "ai_scripts.pbo").write_bytes(FACTIONS_PBO)
    (cfg.server_dir_path / MOD / "addons").mkdir(parents=True)
    loadouts = dynamic_ai.loadouts_dir(cfg)
    loadouts.mkdir(parents=True)
    for name in ("EastLoadout.json", "WestLoadout.json"):
        (loadouts / name).write_text("{}", encoding="utf-8")
    folder = dynamic_ai.settings_dir(cfg)
    folder.mkdir(parents=True)
    _write(cfg, _settings())
    return cfg


def _write(cfg, doc, name="SpatialSettings.json", raw: str | None = None) -> Path:
    path = dynamic_ai.settings_dir(cfg) / name
    path.write_text(raw if raw is not None else json.dumps(doc), encoding="utf-8")
    return path


def _ids(cfg) -> dict[str, dict]:
    return {f["id"]: f for f in build_report(cfg)["findings"]}


# ── when it applies ───────────────────────────────────────────────────


def test_nothing_when_the_mod_is_not_installed(sample_config):
    write_test_mods(sample_config, ['@CF'])
    assert build_report(sample_config) is None
    assert doctor_row(sample_config) is None


def test_nothing_when_the_mod_is_disabled(dyn):
    set_test_flags(dyn, disabled=[MOD])
    assert build_report(dyn) is None


def test_found_under_another_folder_name_by_workshop_id(dyn):
    write_test_mods(dyn, ['@DayZ-Expansion-AI', '@DynAI'])
    write_test_ids({'@DynAI': '2874589934'})
    set_test_flags(dyn, server_only=["@DynAI"])
    assert build_report(dyn)["mod"] == "@DynAI"


def test_a_valid_install_has_no_findings_and_a_green_row(dyn):
    report = build_report(dyn)
    assert report["findings"] == []
    assert report["factions"] == ["East", "West"]
    assert report["format"] == "v21"
    assert doctor_row(dyn) == ("Dynamic AI", True, "Setup OK")


def test_the_report_never_writes(dyn):
    # The author's terms: no editing. The player database is the mod's own.
    players = dynamic_ai.settings_dir(dyn) / "SpatialPlayerSettings.json"
    players.write_text('{"Version": 1}', encoding="utf-8")
    before = {p: p.read_bytes() for p in dyn.server_dir_path.rglob("*") if p.is_file()}
    build_report(dyn)
    after = {p: p.read_bytes() for p in dyn.server_dir_path.rglob("*") if p.is_file()}
    # The one new file is the faction scan's own cache, beside the addon cache.
    assert {p.name for p in set(after) - set(before)} == {".pbo_faction_cache.json"}
    assert all(after[p] == before[p] for p in before)


# ── the install ───────────────────────────────────────────────────────


def test_loading_through_mod_is_a_warning_with_a_mods_link(dyn):
    set_test_flags(dyn, server_only=[])
    finding = _ids(dyn)["dynamic_ai_client_side"]
    assert finding["severity"] == "warning"
    assert finding["links"][0]["to"].startswith("/mods")
    ok, detail = doctor_row(dyn)[1:]
    assert not ok and "-mod=" in detail


def test_missing_folder_says_the_first_boot_creates_it(dyn):
    import shutil

    shutil.rmtree(dynamic_ai.settings_dir(dyn))
    assert _ids(dyn)["dynamic_ai_folder_missing"]["severity"] == "warning"


def test_missing_settings_file_is_only_a_notice(dyn):
    (dynamic_ai.settings_dir(dyn) / "SpatialSettings.json").unlink()
    assert _ids(dyn)["dynamic_ai_settings_missing"]["severity"] == "notice"


def test_unreadable_json_names_the_position(dyn):
    _write(dyn, None, raw='{"Version": 21,\n  "MaxAI": }')
    finding = _ids(dyn)["dynamic_ai_settings_unreadable"]
    assert finding["severity"] == "error"
    assert "line 2" in finding["detail"]


# ── what the mod does with a bad value ──────────────────────────────


def test_a_decimal_in_a_whole_number_field_is_an_error(dyn):
    # The author's diagnosis (2023-08-09): 4.0 in MaxCount stopped a server.
    _write(dyn, _settings(MaxAI=20.0, Group=[_group(hi=4.0)]))
    finding = _ids(dyn)["dynamic_ai_decimal_in_whole_number"]
    assert finding["severity"] == "error"
    assert "MaxAI = 20.0" in finding["detail"]
    assert "Group[0].Spatial_MaxCount = 4.0" in finding["detail"]


def test_decimals_in_decimal_fields_are_fine(dyn):
    _write(dyn, _settings(Spatial_MinTimer=20, EngageTimer=5))
    assert "dynamic_ai_decimal_in_whole_number" not in _ids(dyn)


@pytest.mark.parametrize("version", [18, 22])
def test_an_unsupported_version_switches_the_mod_off(dyn, version):
    _write(dyn, _settings(Version=version))
    ids = _ids(dyn)
    assert ids["dynamic_ai_version_off"]["severity"] == "error"
    # Everything else is moot once the mod is off.
    assert set(ids) == {"dynamic_ai_version_off"}


@pytest.mark.parametrize("version", [19, 20])
def test_an_upgradable_version_is_a_notice(dyn, version):
    _write(dyn, _settings(Version=version))
    assert _ids(dyn)["dynamic_ai_version_upgrade"]["severity"] == "notice"


def test_a_missing_version_loads_as_20_and_is_upgraded(dyn):
    doc = _settings()
    del doc["Version"]
    _write(dyn, doc)
    assert "dynamic_ai_version_upgrade" in _ids(dyn)


def test_the_next_format_in_this_folder_is_named(dyn):
    doc = _settings()
    del doc["Version"]
    doc["m_Version"] = 1
    _write(dyn, doc)
    assert _ids(dyn)["dynamic_ai_unknown_format"]["severity"] == "warning"


def test_max_ai_under_one_switches_the_mod_off(dyn):
    _write(dyn, _settings(MaxAI=0))
    assert _ids(dyn)["dynamic_ai_max_ai_off"]["severity"] == "error"


def test_a_negative_min_timer_is_debug_mode(dyn):
    _write(dyn, _settings(Spatial_MinTimer=-2.0, Spatial_MaxTimer=1.0))
    ids = _ids(dyn)
    assert ids["dynamic_ai_debug_mode"]["severity"] == "warning"
    # Debug uses the absolute value, so 1 < 2 is raised to it.
    assert "minimum 2" in ids["dynamic_ai_max_timer_raised"]["detail"]


def test_a_short_min_timer_is_raised_to_five(dyn):
    _write(dyn, _settings(Spatial_MinTimer=2.0, Spatial_MaxTimer=4.0))
    ids = _ids(dyn)
    assert "dynamic_ai_timer_raised" in ids
    assert "minimum 5" in ids["dynamic_ai_max_timer_raised"]["detail"]


def test_distances_hunt_mode_zones_and_timers(dyn):
    _write(dyn, _settings(
        MinDistance=50, MaxDistance=100, HuntMode=9, Points_Enabled=3,
        Locations_Enabled=-1, EngageTimer=2.0, CleanupTimer=4.0,
    ))
    ids = _ids(dyn)
    assert {
        "dynamic_ai_min_distance_raised", "dynamic_ai_max_distance_raised",
        "dynamic_ai_hunt_mode_reset", "dynamic_ai_points_off", "dynamic_ai_locations_off",
        "dynamic_ai_engage_raised", "dynamic_ai_cleanup_raised",
    } <= set(ids)
    # MaxDistance is compared with the raised minimum, not the written one.
    assert "MinDistance 120" in ids["dynamic_ai_max_distance_raised"]["detail"]


def test_hunt_mode_zero_is_a_notice(dyn):
    _write(dyn, _settings(HuntMode=0))
    assert _ids(dyn)["dynamic_ai_hunt_mode_zero"]["severity"] == "notice"


def test_leaders_only_rides_along_on_a_green_row(dyn):
    _write(dyn, _settings(PlayerChecks=0))
    assert _ids(dyn)["dynamic_ai_leaders_only"]["severity"] == "notice"
    label, ok, detail = doctor_row(dyn)
    assert ok and "group leaders" in detail


# ── groups, factions, loadouts ─────────────────────────────────────


def test_groups_the_mod_skips_are_named_with_the_reason(dyn):
    _write(dyn, _settings(Group=[
        _group("Ok"),
        _group("Neg", lo=-1),
        _group("Inverted", lo=4, hi=2),
        _group("Empty", lo=0, hi=0),
        _group("NoFile", loadout="Missing.json"),
        _group("Case", loadout="eastloadout.json"),
    ]))
    finding = _ids(dyn)["dynamic_ai_group_skipped"]
    assert finding["count"] == 5
    for name in ("Neg", "Inverted", "Empty", "NoFile"):
        assert f"Group '{name}'" in finding["detail"]
    assert "only in case" in finding["detail"]
    assert "Group 'Ok'" not in finding["detail"]


def test_no_usable_group_is_an_error_unless_points_do_the_spawning(dyn):
    _write(dyn, _settings(Group=[_group(loadout="Missing.json")]))
    assert _ids(dyn)["dynamic_ai_no_usable_group"]["severity"] == "error"
    _write(dyn, _settings(Group=[_group(loadout="Missing.json")], Points_Enabled=2))
    assert "dynamic_ai_no_usable_group" not in _ids(dyn)


def test_unknown_factions_are_checked_against_the_install(dyn):
    _write(dyn, _settings(
        Group=[_group(faction="Mercenarys")],
        Location=[_location(faction="Raiders")],
    ))
    finding = _ids(dyn)["dynamic_ai_unknown_faction"]
    assert "Group 'Guard': Mercenarys" in finding["detail"]
    assert "Location 'Tower': Raiders" in finding["detail"]
    assert "East, West" in finding["why"]


def test_random_is_a_faction(dyn):
    """Expansion's faction lookup, which the mod goes through, picks a
    registered faction for RANDOM (plans/64 D2)."""
    _write(dyn, _settings(Group=[_group(faction="RANDOM")], Location=[_location(faction="RANDOM")]))
    assert "dynamic_ai_unknown_faction" not in _ids(dyn)


def test_no_faction_check_when_the_scan_found_none(dyn):
    # Nothing readable is "unknown", never "every faction is wrong".
    (dyn.server_dir_path / "@DayZ-Expansion-AI" / "addons" / "ai_scripts.pbo").write_bytes(b"x")
    _write(dyn, _settings(Group=[_group(faction="Anything")]))
    assert "dynamic_ai_unknown_faction" not in _ids(dyn)


def test_zone_loadouts_are_checked_only_where_the_feature_is_on(dyn):
    doc = _settings(
        Point=[_point(loadouts=("EastLoadout.json", "Gone.json"))],
        Location=[_location(loadout="AlsoGone.json")],
    )
    _write(dyn, doc)
    assert "dynamic_ai_zone_loadout_missing" not in _ids(dyn)
    doc.update(Points_Enabled=1, Locations_Enabled=2)
    _write(dyn, doc)
    detail = _ids(dyn)["dynamic_ai_zone_loadout_missing"]["detail"]
    assert "Point 'Zone': Gone.json" in detail
    assert "Location 'Tower': AlsoGone.json" in detail


def test_point_values_the_mod_replaces(dyn):
    _write(dyn, _settings(Points_Enabled=1, Point=[
        _point(Spatial_Radius=-5.0, Spatial_Safe=2, Spatial_HuntMode=12),
    ]))
    detail = _ids(dyn)["dynamic_ai_point_reset"]["detail"]
    assert "becomes 100" in detail and "no-spawn zone" in detail and "becomes 3" in detail


# ── notifications ───────────────────────────────────────────────────


def _notes(*names):
    return {"Version": 2, "notification": [
        {"Spatial_Name": n, "StartTime": 0.0, "StopTime": 24.0, "AgeTime": 0.0,
         "MessageType": 1, "MessageTitle": "t", "MessageText": ["m"]} for n in names
    ]}


def test_groups_no_notification_names_are_never_picked_in_modes_2_and_3(dyn):
    _write(dyn, _settings(ActiveHoursEnabled=2, Group=[_group("A"), _group("B")]))
    _write(dyn, _notes("A"), name="Notificationsettings.json")
    finding = _ids(dyn)["dynamic_ai_group_not_linked"]
    assert "Group 'B'" in finding["detail"] and "Group 'A'" not in finding["detail"]
    _write(dyn, _notes("Z"), name="Notificationsettings.json")
    assert _ids(dyn)["dynamic_ai_no_group_linked"]["severity"] == "error"


def test_notifications_are_ignored_in_modes_0_and_1(dyn):
    _write(dyn, _settings(ActiveHoursEnabled=1))
    _write(dyn, _notes("Z"), name="Notificationsettings.json")
    assert _ids(dyn) == {}


def test_unreadable_notifications_are_an_error(dyn):
    _write(dyn, None, name="Notificationsettings.json", raw="{nope")
    assert _ids(dyn)["dynamic_ai_notifications_unreadable"]["severity"] == "error"


# ── the Expansion log ───────────────────────────────────────────────


def _log(cfg, text: str, name="ExpLog_2026-09-23_10-36-57.log") -> None:
    logs = dynamic_ai.profiles_dir(cfg) / "ExpansionMod" / "Logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / name).write_text(text, encoding="utf-8")


def test_the_last_boot_reader_keeps_only_spatial_lines(dyn):
    _log(dyn, (
        "10:13:01.100 [Spatial Settings] WARNING: Couldn't find config file !\n"
        "10:13:01.101 [Spatial Settings]  delete the Spatial_MaxAccuracy lines from the Audio arrays if there.\n"
        "10:13:01.200 [AI General] something else\n"
        "10:13:02.000 [Spatial AI] Spatial AI Enabled\n"
    ))
    boot = read_last_boot(dyn)
    assert boot["enabled"] is True and boot["first_boot"] is True
    assert [ln["tag"] for ln in boot["lines"]] == ["Spatial Settings", "Spatial Settings", "Spatial AI"]
    assert [ln["expected"] for ln in boot["lines"]] == [False, True, False]
    assert doctor_row(dyn)[2] == "Setup OK · last boot: enabled (ExpLog_2026-09-23_10-36-57.log)"


def test_the_newest_log_wins(dyn):
    import os

    _log(dyn, "10:00:00.000 [Spatial AI] Spatial AI Enabled\n", name="ExpLog_a.log")
    _log(dyn, "11:00:00.000 [Spatial Settings] MaxAI set under 1, disabling Spatial AI.\n", name="ExpLog_b.log")
    logs = dynamic_ai.profiles_dir(dyn) / "ExpansionMod" / "Logs"
    os.utime(logs / "ExpLog_a.log", (1, 1))
    boot = read_last_boot(dyn)
    assert boot["file"] == "ExpLog_b.log" and boot["enabled"] is False
    assert _ids(dyn)["dynamic_ai_disabled_last_boot"]["severity"] == "error"


def test_no_log_is_none(dyn):
    assert read_last_boot(dyn) is None


def test_ai_logging_off_is_a_notice(dyn):
    settings = dynamic_ai.profiles_dir(dyn) / "ExpansionMod" / "Settings"
    settings.mkdir(parents=True)
    (settings / "LogsSettings.json").write_text('{"AIGeneral": 0}', encoding="utf-8")
    assert _ids(dyn)["dynamic_ai_log_off"]["severity"] == "notice"


# ── the Doctor row ──────────────────────────────────────────────────


def test_a_failing_row_lists_three_problems_and_counts_the_rest(dyn):
    set_test_flags(dyn, server_only=[])
    _write(dyn, _settings(MinDistance=50, HuntMode=9, EngageTimer=2.0, Points_Enabled=5))
    label, ok, detail = doctor_row(dyn)
    assert not ok
    assert detail.count(" — ") == 3 and detail.endswith("more")


def test_doctor_runs_the_row_only_with_the_mod(dyn, monkeypatch):
    from app.commands import doctor

    assert doctor._check_dynamic_ai(dyn)[0] == "Dynamic AI"
    write_test_mods(dyn, ['@CF'])
    assert doctor._check_dynamic_ai(dyn) is None


def test_a_crashing_check_does_not_sink_the_doctor(dyn, monkeypatch):
    from app.commands import doctor

    def _boom(config):
        raise RuntimeError("bad")

    monkeypatch.setattr(dynamic_ai, "doctor_row", _boom)
    assert doctor._check_dynamic_ai(dyn) == ("Dynamic AI", False, "Check failed: bad")


def test_the_web_row_links_to_mods_when_it_loads_through_mod(dyn):
    from app.ops.doctor_report import _links_for

    links, _ = _links_for("Dynamic AI", False, "Loads through -mod=, so players …")
    assert links == [{"label": "Mods", "to": "/mods?tab=Installed"}]
    assert _links_for("Dynamic AI", False, "MaxAI is under 1")[0] == []
