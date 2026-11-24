"""Tests for app/ops/log_analysis.py — the .RPT analyzer.

The server logs are read with `server_log(<name>)` from `tests/private/rpt/`,
the private fixtures repository: they are the DayZ server's own output, which
stays out of this repository (plans/86 D2). Without it these tests skip.

sample-1.29 is a trimmed but verbatim excerpt of real DayZ 1.29 Linux server
logs: the header block, one of every finding the analyzer claims to recognise,
and a representative sample of the engine noise that makes up most of a real
file. Keep it verbatim — the value of these tests is that the patterns are
checked against strings the engine actually emits, not against strings written
to make the patterns pass. Most of it is one 2026 Q3 run; the persistence,
material and animation-continuation lines were lifted from a 2026 Q3 run on
build 163709 (a different server), which is also where stock-chernarus-1.29
comes from.

stock-chernarus-1.29 is the other half of the point: a run with no mods at
all, from a clean 1.29.163709 Chernarus install with an empty `-mod=` (2026 Q3),
the run that showed "2 errors, 6 warnings" on a server that had never been
touched. Every complaint in it comes out of Bohemia's own mission folder. It is
what the origin tagging is measured against, so it must stay free of anything
the admin could have caused.

radzone-1.29 is the per-mod version of stock-chernarus-1.29 (plans/48 D6): a
Chernarus boot with @RadZone loaded, whose own types.xml lists three classes
its own Workshop build rejects. Verbatim from the 2026 Q3 boot on 1.29.163709
(plans/48 Step 0): 22 mods, @RadZone last. Its Expansion_M79 line is the
admin's own manual types file, kept in to show that a mod's quirks come off
without taking the admin's findings with them.

toxiczone-1.29 is the first boot after installing @ToxicZone (plans/48 Step 0,
2026 Q3): verbatim lines, the -mod= list trimmed to the mods that matter. One
block per mask class: the "!!!" line, the StreamLoad line and the generic
exception the engine prints for the same item. It is also the fixture for a
morning boot, whose timestamps the engine pads to " 8:41:33".

traderplus-1.29 is the first boot after installing @TraderPlus (plans/48 D,
2026 Q3): verbatim lines, the -mod= list trimmed and three of its 83
lost vehicles kept. The run before it did not load TraderPlus; every run after
it lost nothing.

livonia-1.29 and sakhal-1.29 are the same thing for the other two stock maps
(plans/39 C, 2026 Q3): one boot each on 1.29.163709, no mods, from a byte
copy of the shipped mission folder (hence ``p39check.<terrain>``). Verbatim
and trimmed; the only lines left out besides engine chatter are the server
description warning, which came from the test box's own serverDZ.cfg.

exp-1.30 is a stock boot of DayZ 1.30 Experimental (plans/66 Step 4,
2026 Q4): Chernarus, no mods, a fresh download of app 1042420, verbatim and
trimmed the same way. It ends mid-line because the server hung on shutdown and
was killed, which lost the end of the log. exp-1.30-stop is the last lines of
the second stock 1.30 run's .RPT, and the summary line the first run's journal
kept after its .RPT was cut (2026 Q4).

chernarus-1.30 and badlands-1.30 are stock boots of DayZ 1.30 stable
(1.30.164078, 2026 Q4): a fresh download of app 223350, no mods, an empty
storage_1, verbatim and trimmed the same way. badlands-1.30 is the first stock
boot of the Badlands map (terrain ``nasdara``); badlands-1.30-second-boot is a
line of the second.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from tests.conftest import private_fixture, set_test_flags, write_test_mods

from app.ops.log_analysis import (
    MOD_BASELINE,
    analyze_rpt,
    build_log_report,
    crash_dumps,
    latest_rpt,
    list_logs,
    parse_mods,
    parse_server_mods,
    resolve_log,
    validate_mods,
)


def server_log(name: str) -> str:
    """The server log `tests/private/rpt/<name>.RPT` (the module docstring
    says where each came from)."""
    return private_fixture(f"rpt/{name}.RPT")


def before_traderplus_rpt() -> str:
    """The command line of the run before traderplus-1.29: the same mods, no TraderPlus."""
    return server_log("traderplus-1.29").replace(";@TraderPlus", "").replace(
        "Current time:  2026/09/24 20:04:15", "Current time:  2026/09/24 12:24:47"
    )


@pytest.fixture()
def stock_rpt(tmp_path: Path) -> Path:
    path = tmp_path / "DayZServer_2026-09-18_19-50-03.RPT"
    path.write_text(server_log("stock-chernarus-1.29"), encoding="utf-8")
    return path


@pytest.fixture()
def rpt(tmp_path: Path) -> Path:
    path = tmp_path / "DayZServer_2026-07-16_20-14-08.RPT"
    path.write_text(server_log("sample-1.29"), encoding="utf-8")
    return path


def _by_id(report: dict) -> dict[str, dict]:
    return {f["id"]: f for f in report["findings"]}


# ── Run facts ────────────────────────────────────────────────────────────


def test_header_facts_are_read(rpt: Path):
    run = analyze_rpt(rpt)["run"]
    assert run["version"] == "1.29.163451"
    assert run["started_at"] == "2026/07/16 20:14:08"
    assert run["mission"] == "dayzOffline.chernarusplus"
    assert run["terrain"] == "chernarusplus"
    assert run["port"] == 2302
    assert run["mod_args"] == ["@CF", "@TraderPlus"]
    assert run["addons_loaded"] == 134
    assert run["steam_registered"] is True
    assert run["hive"] == "OFFLINE"


def test_mods_that_actually_loaded_come_from_the_addon_paths(rpt: Path):
    # -mod= says what was asked for; the mounted bank paths say what was found.
    assert analyze_rpt(rpt)["run"]["mod_addons"] == ["@CF"]


def test_time_to_joinable_is_measured(rpt: Path):
    # 20:14:08 (first line) → 20:14:38 ("Player connect enabled").
    assert analyze_rpt(rpt)["run"]["ready_seconds"] == 30


def test_ready_seconds_survives_midnight_rollover(tmp_path: Path):
    path = tmp_path / "roll.RPT"
    path.write_text("23:59:50 Starting\n00:00:20 Player connect enabled\n")
    assert analyze_rpt(path)["run"]["ready_seconds"] == 30


def test_economy_totals_are_read(rpt: Path):
    stats = analyze_rpt(rpt)["stats"]
    assert stats["loot_nominal"] == 20367
    assert stats["loot_in_map"] == 20839
    assert stats["dynamic_events"] == 51
    assert stats["entities"] == 22317
    assert stats["storage_load_seconds"] == 8.87
    assert stats["landscape_load_ms"] == 4303


# ── Outcome ──────────────────────────────────────────────────────────────


def test_clean_shutdown_is_recognised(rpt: Path):
    assert analyze_rpt(rpt)["run"]["outcome"] == "clean_shutdown"


def test_a_log_that_stops_mid_boot_is_not_called_healthy(tmp_path: Path):
    path = tmp_path / "cut.RPT"
    path.write_text("20:14:08 Version 1.29\n20:14:10 WORLD : Loading landscape 'enoch'\n")
    run = analyze_rpt(path)["run"]
    assert run["outcome"] == "startup_incomplete"


def test_a_run_that_never_logged_a_shutdown_is_flagged_as_such(tmp_path: Path):
    path = tmp_path / "live.RPT"
    path.write_text("20:14:08 Version 1.29\n20:14:38 Player connect enabled\n")
    assert analyze_rpt(path)["run"]["outcome"] == "running_or_cut_short"


# ── Findings ─────────────────────────────────────────────────────────────


def test_types_entry_for_a_missing_class_is_an_error(rpt: Path):
    finding = _by_id(analyze_rpt(rpt))["ce_type_missing"]
    assert finding["severity"] == "error"
    assert finding["subjects"] == ["Static_FrozenScientist_DE"]
    assert any(link["to"] == "/configs/types" for link in finding["links"])


def test_a_non_public_type_is_only_a_warning(rpt: Path):
    finding = _by_id(analyze_rpt(rpt))["ce_type_not_spawnable"]
    assert finding["severity"] == "warning"
    assert finding["subjects"] == ["ChristmasTree"]


def test_event_spawns_for_an_undefined_event_are_reported(rpt: Path):
    finding = _by_id(analyze_rpt(rpt))["ce_missing_event"]
    assert finding["subjects"] == ["VehicleTransitBus"]
    assert {link["to"] for link in finding["links"]} == {
        "/configs/events",
        "/configs/eventspawns",
    }


def test_the_count_a_message_states_is_used_not_the_line_count(rpt: Path):
    # One line that says "10 Errors" is ten errors, not one.
    assert _by_id(analyze_rpt(rpt))["ce_xml_parse_errors"]["count"] == 10
    assert _by_id(analyze_rpt(rpt))["ce_groups_no_points"]["count"] == 5


def test_missing_shutdown_marker_is_reported(rpt: Path):
    assert "ce_no_shutdown_message" in _by_id(analyze_rpt(rpt))


def test_over_long_description_is_reported(rpt: Path):
    # Bohemia spells it "exceeeds"; matching their typo is the whole point.
    finding = _by_id(analyze_rpt(rpt))["description_too_long"]
    assert finding["links"] == [{"label": "serverDZ.cfg", "to": "/configs/serverdz"}]


def test_animation_load_failure_names_the_file(rpt: Path):
    assert _by_id(analyze_rpt(rpt))["anim_load_error"]["subjects"] == [
        "sakhal/Anims/cfg/skeletons.anim.xml"
    ]


def test_a_successful_steam_init_is_not_read_as_a_failure(rpt: Path):
    assert "steam_init_failed" not in _by_id(analyze_rpt(rpt))


def test_steam_init_failure_is_an_error(tmp_path: Path):
    path = tmp_path / "steam.RPT"
    path.write_text("20:14:13 Failed to initialize SteamGameServer_Init(0,8766)\n")
    assert _by_id(analyze_rpt(path))["steam_init_failed"]["severity"] == "error"


# ── Noise ────────────────────────────────────────────────────────────────


def test_vanilla_engine_chatter_is_suppressed_but_counted(rpt: Path):
    report = analyze_rpt(rpt)
    assert report["noise"]["suppressed"] >= 12
    assert {c["label"] for c in report["noise"]["categories"]} >= {
        "Model geometry warnings",
        "Missing localisation strings",
        "Config lookup warnings",
    }


def test_vanilla_model_paths_are_not_reported_as_missing_mod_files(rpt: Path):
    # dz\ and dta\ paths, and the bmp.p3d vanilla asks for and never ships,
    # are excused; a mod path is not.
    finding = _by_id(analyze_rpt(rpt))["missing_mod_model"]
    assert finding["subjects"] == ["@MyMod\\structures\\custom_thing.p3d"]


def test_geometry_and_localisation_warnings_produce_no_findings(rpt: Path):
    titles = {f["title"] for f in analyze_rpt(rpt)["findings"]}
    assert not any("components" in t or "ocalization" in t for t in titles)


def test_a_handful_of_off_map_objects_is_not_worth_reporting(tmp_path: Path):
    path = tmp_path / "few.RPT"
    path.write_text("20:14:31 Accessing static object outside landscape (Obj-1 in 256,256)\n" * 3)
    assert "off_map_object" not in _by_id(analyze_rpt(path))


def test_a_pile_of_off_map_objects_is(tmp_path: Path):
    path = tmp_path / "many.RPT"
    path.write_text("20:14:31 Accessing static object outside landscape (Obj-1 in 256,256)\n" * 40)
    assert _by_id(analyze_rpt(path))["off_map_object"]["count"] == 40


def test_an_unrecognised_ce_complaint_is_still_reported(tmp_path: Path):
    # The catch-all is the guard against Bohemia adding a message we have no
    # rule for — it must never vanish silently.
    path = tmp_path / "future.RPT"
    path.write_text("20:14:29 !!! [CE][SomethingNew] :: brand new complaint\n")
    finding = _by_id(analyze_rpt(path))["ce_other"]
    assert finding["samples"][0]["text"].endswith("brand new complaint")


# ── Vanilla baseline (origin tagging) ────────────────────────────────────


def _origins(report: dict, rule_id: str) -> dict[str, dict]:
    """A rule can yield two findings now — the stock half and the admin half."""
    return {f["origin"]: f for f in report["findings"] if f["id"] == rule_id}


def test_a_stock_server_owns_none_of_its_own_findings(stock_rpt: Path):
    # The whole point: a clean install has nothing for its admin to act on.
    report = analyze_rpt(stock_rpt)
    assert report["summary"] == {"error": 0, "warning": 0, "notice": 0, "vanilla": 7, "mod": 0}
    assert [f["origin"] for f in report["findings"]] == ["vanilla"] * 7


def test_a_stock_1_30_server_owns_none_of_its_own_findings(tmp_path: Path):
    # plans/66: the same guarantee on Experimental. 1.30 drops the ten XML
    # parse errors, adds the flare preset lines and logs Nasdara's animations.
    path = tmp_path / "exp130.RPT"
    path.write_text(server_log("exp-1.30"), encoding="utf-8")
    report = analyze_rpt(path)
    assert report["summary"] == {"error": 0, "warning": 0, "notice": 0, "vanilla": 8, "mod": 0}
    assert {f["origin"] for f in report["findings"]} == {"vanilla"}
    assert _by_id(report)["ce_preset_item_ignored"]["count"] == 5
    assert "nasdara/Anims/cfg/skeletons.anim.xml" in _by_id(report)["anim_load_error"]["subjects"]


def _yours(report: dict) -> dict[str, dict]:
    return {f["id"]: f for f in report["findings"] if f["origin"] == "yours"}


def _report(tmp_path: Path, text: str) -> dict:
    path = tmp_path / "stock.RPT"
    path.write_text(text, encoding="utf-8")
    return analyze_rpt(path)


def test_stock_chernarus_on_1_30_stable_owns_nothing(tmp_path: Path):
    # 1.30 stable lists six classes in every map's types.xml that the server
    # does not define. The bare "RESOURCES (E): Failed to open" names its file
    # on the line before; it is Bohemia's own DZ/ file, so it is stock too
    # (night test 2026-11-23: four fresh stock boots).
    report = _report(tmp_path, server_log("chernarus-1.30"))
    assert set(_yours(report)) == set()
    engine = _origins(report, "engine_error")["vanilla"]
    assert any("Failed to open (/DZ/" in s for s in engine["subjects"]), engine
    assert report["summary"]["error"] == report["summary"]["warning"] == 0
    assert "CeramicPot_Black" in _origins(report, "ce_type_missing")["vanilla"]["subjects"]


def test_stock_badlands_owns_nothing(tmp_path: Path):
    report = _report(tmp_path, server_log("badlands-1.30"))
    assert report["run"]["terrain"] == "nasdara"
    assert report["run"]["mission"] == "dayzOffline.nasdara"
    assert set(_yours(report)) == set()
    found = _by_id(report)
    assert found["ce_xml_parse_errors"]["origin"] == "vanilla"
    assert found["ce_xml_parse_errors"]["count"] == 3
    assert found["ce_groups_wrong_points"]["origin"] == "vanilla"
    assert found["ce_vehicles_short"]["origin"] == "vanilla"
    assert found["ce_event_child_missing"]["origin"] == "vanilla"
    assert found["missing_mod_model"]["origin"] == "vanilla"
    assert "Water_Well_N1" in _origins(report, "ce_type_missing")["vanilla"]["subjects"]


def test_no_valid_spawns_is_one_error_per_section(tmp_path: Path):
    # Stock Badlands logs it, yet a new character on a stock Badlands server
    # spawned at one of the file's positions (2026 Q4), so there it is
    # Bohemia's. On another map it is the admin's error.
    spawns = _by_id(_report(tmp_path, server_log("badlands-1.30")))["ce_no_player_spawns"]
    assert spawns["origin"] == "vanilla"
    assert spawns["count"] == 3
    lone = "\n".join(
        line for line in server_log("badlands-1.30").splitlines() if "There are no valid groups" not in line
    ) + "\n"
    assert _by_id(_report(tmp_path, lone))["ce_no_player_spawns"]["count"] == 3
    elsewhere = server_log("badlands-1.30").replace("Loading landscape 'nasdara'", "Loading landscape 'chernarusplus'")
    mine = _yours(_report(tmp_path, elsewhere))["ce_no_player_spawns"]
    assert mine["severity"] == "error"


def test_a_vehicle_shortfall_on_a_later_boot_is_the_same_finding(tmp_path: Path):
    # A line of the second stock Badlands boot (2026 Q4).
    text = server_log("badlands-1.30") + server_log("badlands-1.30-second-boot")
    found = _by_id(_report(tmp_path, text))
    assert "ce_other" not in found
    assert found["ce_vehicles_short"]["origin"] == "vanilla"


def test_the_1_30_stock_types_are_the_admins_on_1_29(tmp_path: Path):
    report = _report(tmp_path, server_log("chernarus-1.30").replace("Version 1.30.164078.38", "Version 1.29.163709"))
    assert "CeramicPot_Black" in _yours(report)["ce_type_missing"]["subjects"]


def test_a_badlands_type_on_another_map_stays_the_admins(tmp_path: Path):
    text = server_log("chernarus-1.30").replace(
        "Type 'Glock_Tan' will be ignored",
        "Type 'Water_Well_N1' will be ignored",
    )
    assert _yours(_report(tmp_path, text))["ce_type_missing"]["subjects"] == ["Water_Well_N1"]


def test_a_1_30_log_still_yields_its_facts(tmp_path: Path):
    # 1.30 says "to filesystem" where 1.29 said "to RV filesystem", and
    # reports its events as "[CE][DynEvent] :: 51 types".
    path = tmp_path / "exp130.RPT"
    path.write_text(server_log("exp-1.30"), encoding="utf-8")
    report = analyze_rpt(path)
    assert report["run"]["version"] == "1.30.164014.27"
    assert report["run"]["mission"] == "dayzOffline.chernarusplus"
    assert report["stats"]["dynamic_events"] == 51


def test_the_1_30_engine_lines_are_counted_as_noise(tmp_path: Path):
    path = tmp_path / "exp130.RPT"
    path.write_text(server_log("exp-1.30"), encoding="utf-8")
    report = analyze_rpt(path)
    assert not any(f["id"].startswith(("engine_", "script_")) or f["id"].endswith("_other")
                   for f in report["findings"])
    labels = {c["label"] for c in report["noise"]["categories"]}
    assert {"Rendering warnings", "Vanilla asset warnings"} <= labels


def test_parse_errors_on_1_30_are_the_admins(tmp_path: Path):
    # A stock 1.30 Chernarus prints no parse-error line, so the 1.29 count of
    # ten must not excuse ten of the admin's own.
    path = tmp_path / "exp.RPT"
    path.write_text(
        "Version 1.30.164014.27\n"
        "20:25:15   WORLD        : Loading landscape 'chernarusplus'\n"
        "20:25:26 !!! [CE][LoadPrototype] 10 Errors during XML parse...\n"
    )
    halves = _origins(analyze_rpt(path), "ce_xml_parse_errors")
    assert list(halves) == ["yours"]
    assert halves["yours"]["count"] == 10


def test_an_ignored_preset_item_bohemia_does_not_ship_is_the_admins(tmp_path: Path):
    path = tmp_path / "exp.RPT"
    path.write_text(
        "Version 1.30.164014.27\n"
        "20:25:15   WORLD        : Loading landscape 'chernarusplus'\n"
        "20:25:26  WORLD     (E): PRESET LOAD ERROR: Spawnable type '(null)' could not load preset item 'Flaregun' because it is ignored.\n"
        "20:25:26  WORLD     (E): PRESET LOAD ERROR: Spawnable type '(null)' could not load preset item 'M4A1' because it is ignored.\n"
    )
    halves = _origins(analyze_rpt(path), "ce_preset_item_ignored")
    assert halves["vanilla"]["subjects"] == ["Flaregun"]
    assert halves["yours"]["subjects"] == ["M4A1"]


def test_a_stock_1_30_shutdown_leak_is_baseline(tmp_path: Path):
    # Both stock 1.30 stops logged it and then hung until systemd killed them.
    path = tmp_path / "stop.RPT"
    path.write_text(server_log("exp-1.30-stop"))
    report = analyze_rpt(path)
    assert list(_origins(report, "script_error")) == ["vanilla"]
    assert report["summary"]["error"] == 0
    assert "90 s" in _origins(report, "script_error")["vanilla"]["how"]


def test_a_leak_of_any_other_class_stays_the_admins(tmp_path: Path):
    path = tmp_path / "stop.RPT"
    path.write_text(
        server_log("exp-1.30-stop").replace("==== Total Leaks (1x)! ====", "==== Total Leaks (2x)! ====")
        + "21:02:17   SCRIPT    (E): Leaked 'MyModManager' script instance (1x)!\n"
    )
    halves = _origins(analyze_rpt(path), "script_error")
    assert halves["vanilla"]["count"] == 1
    assert halves["yours"]["count"] == 2


def test_the_shutdown_leak_baseline_is_1_30_only(tmp_path: Path):
    path = tmp_path / "stop.RPT"
    path.write_text(server_log("exp-1.30-stop").replace("Version 1.30.164014.27", "Version 1.29.163709"))
    assert list(_origins(analyze_rpt(path), "script_error")) == ["yours"]


def test_the_flare_preset_baseline_is_1_30_only(tmp_path: Path):
    # Stock 1.29 does not ignore the flare gun, so there the line is news.
    path = tmp_path / "old.RPT"
    path.write_text(
        "Version 1.29.163709\n"
        "20:25:15   WORLD        : Loading landscape 'chernarusplus'\n"
        "20:25:26  WORLD     (E): PRESET LOAD ERROR: Spawnable type '(null)' could not load preset item 'Flaregun' because it is ignored.\n"
    )
    assert list(_origins(analyze_rpt(path), "ce_preset_item_ignored")) == ["yours"]


@pytest.mark.parametrize(
    "name, expected",
    [("livonia-1.29", 9), ("sakhal-1.29", 8)],
    ids=["livonia", "sakhal"],
)
def test_the_other_stock_maps_own_none_of_their_findings_either(
    tmp_path: Path, name: str, expected: int
):
    # plans/39 C: the same guarantee, measured on a real boot of each map.
    path = tmp_path / "stock.RPT"
    path.write_text(server_log(name), encoding="utf-8")
    report = analyze_rpt(path)
    assert report["summary"] == {
        "error": 0, "warning": 0, "notice": 0, "vanilla": expected, "mod": 0
    }
    assert {f["origin"] for f in report["findings"]} == {"vanilla"}


def test_stock_livonia_is_judged_by_its_own_numbers(tmp_path: Path):
    path = tmp_path / "livonia.RPT"
    path.write_text(server_log("livonia-1.29"), encoding="utf-8")
    report = analyze_rpt(path)
    assert _origins(report, "ce_groups_no_points")["vanilla"]["detail"] == (
        "4 reported — a stock Livonia server prints 4"
    )
    assert _origins(report, "ce_groups_wrong_points")["vanilla"]["count"] == 1
    assert _origins(report, "ce_xml_parse_errors")["vanilla"]["count"] == 10
    assert _origins(report, "ce_type_missing")["vanilla"]["subjects"] == [
        "Static_FrozenScientist_DE",
        "WinterMilitaryCoat_Greay",
    ]


def test_stock_sakhal_prints_no_parse_error_line_and_is_missing_messages(tmp_path: Path):
    path = tmp_path / "sakhal.RPT"
    path.write_text(server_log("sakhal-1.29"), encoding="utf-8")
    found = _by_id(analyze_rpt(path))
    assert "ce_xml_parse_errors" not in found
    # Printed twice by the engine; only the `!!!` copy counts.
    assert found["ce_messages_missing"]["count"] == 1
    assert found["ce_groups_wrong_points"]["count"] == 12
    assert found["ce_point_removed"]["subjects"] == ["Land_Roadblock_WoodenCrate"]
    assert found["ce_type_added_by_event"]["origin"] == "vanilla"


def test_more_wrong_points_than_stock_are_the_admins(tmp_path: Path):
    path = tmp_path / "wrong.RPT"
    path.write_text(
        " 7:00:59  WORLD        : Loading landscape 'enoch'\n"
        " 7:01:09 !!! [CE][LoadPrototype] 3 groups have wrong points...\n"
    )
    finding = _origins(analyze_rpt(path), "ce_groups_wrong_points")["yours"]
    assert "2 came from your XML" in finding["detail"]


def test_a_missing_messages_file_on_chernarus_is_the_admins(tmp_path: Path):
    # Only Sakhal ships without one.
    path = tmp_path / "msg.RPT"
    path.write_text(
        "20:14:13  WORLD        : Loading landscape 'chernarusplus'\n"
        "20:14:29 !!! [CE][offlineDB] :: Failed to read messages file 'db/messages.xml'.\n"
    )
    assert list(_origins(analyze_rpt(path), "ce_messages_missing")) == ["yours"]


def test_dropped_loot_points_on_a_community_map_are_the_admins(tmp_path: Path):
    path = tmp_path / "pts.RPT"
    path.write_text(
        "20:14:13  WORLD        : Loading landscape 'namalsk'\n"
        "20:14:29 !!! [CE][Point] Removing 1.5, -2.25 from Land_Custom_Shed\n"
    )
    finding = _origins(analyze_rpt(path), "ce_point_removed")["yours"]
    assert finding["subjects"] == ["Land_Custom_Shed"]
    assert finding["severity"] == "notice"


def test_the_stock_chernarus_subjects_are_tagged_vanilla(stock_rpt: Path):
    report = analyze_rpt(stock_rpt)
    assert _origins(report, "ce_type_missing")["vanilla"]["subjects"] == [
        "Static_FrozenScientist_DE"
    ]
    assert _origins(report, "ce_type_not_spawnable")["vanilla"]["subjects"] == [
        "ChristmasTree"
    ]
    assert _origins(report, "ce_missing_event")["vanilla"]["subjects"] == [
        "VehicleTransitBus"
    ]


def test_the_baseline_note_replaces_the_rules_own_advice(stock_rpt: Path):
    # "Remove the entry" is wrong for a file Bohemia ships — that advice is
    # what sent admins editing the stock mission.
    how = _origins(analyze_rpt(stock_rpt), "ce_type_not_spawnable")["vanilla"]["how"]
    assert "Remove the entry" not in how
    assert "stock Chernarus types.xml" in how


def test_a_real_bad_entry_splits_out_of_the_stock_one(tmp_path: Path):
    path = tmp_path / "mixed.RPT"
    path.write_text(
        "20:14:13  WORLD        : Loading landscape 'chernarusplus'\n"
        "20:14:29 !!! [CE][offlineDB] :: Type 'Static_FrozenScientist_DE' will be ignored. (Type does not exist. (Typo?))\n"
        "20:14:29 !!! [CE][offlineDB] :: Type 'MyMod_Rifle' will be ignored. (Type does not exist. (Typo?))\n"
        "20:14:29 !!! [CE][offlineDB] :: Type 'MyMod_Rifle' will be ignored. (Type does not exist. (Typo?))\n"
    )
    halves = _origins(analyze_rpt(path), "ce_type_missing")
    assert halves["yours"]["subjects"] == ["MyMod_Rifle"]
    assert halves["yours"]["count"] == 2  # its own lines, not the hit's total
    assert halves["vanilla"]["subjects"] == ["Static_FrozenScientist_DE"]
    assert halves["vanilla"]["count"] == 1
    assert all("MyMod_Rifle" in s["text"] for s in halves["yours"]["samples"])


def test_a_stock_entry_on_a_map_that_does_not_ship_it_stays_yours(tmp_path: Path):
    # On 1.29 ChristmasTree is in Chernarus' stock types.xml and in neither
    # Livonia's nor Sakhal's, so on Sakhal someone put it there. (1.30 ships
    # it in every map's types.xml.)
    path = tmp_path / "sakhal.RPT"
    path.write_text(
        "Version 1.29.163709\n"
        "20:14:13  WORLD        : Loading landscape 'sakhal'\n"
        "20:14:29 !!! [CE][offlineDB] :: Type 'ChristmasTree' will be ignored. (Not spawnable. (Scope is not public?))\n"
    )
    assert list(_origins(analyze_rpt(path), "ce_type_not_spawnable")) == ["yours"]


def test_an_unknown_terrain_gets_no_baseline(tmp_path: Path):
    # A community map's mission is not Bohemia's, so assuming it ships
    # Bohemia's quirks would hide the admin's real ones.
    path = tmp_path / "custom.RPT"
    path.write_text(
        "20:14:13  WORLD        : Loading landscape 'somecustommap'\n"
        "20:14:29 !!! [CE][DE][SPAWNS] :: [WARNING] :: Skipping entry for non-existing event 'VehicleTransitBus'.\n"
    )
    assert list(_origins(analyze_rpt(path), "ce_missing_event")) == ["yours"]


def test_a_stock_parse_error_count_is_baseline(stock_rpt: Path):
    finding = _origins(analyze_rpt(stock_rpt), "ce_xml_parse_errors")["vanilla"]
    assert finding["count"] == 10
    assert finding["detail"] == "10 reported — a stock Chernarus server prints 10"


def test_more_parse_errors_than_stock_are_the_admins(tmp_path: Path):
    path = tmp_path / "extra.RPT"
    path.write_text(
        "20:14:13  WORLD        : Loading landscape 'chernarusplus'\n"
        "20:14:27 !!! [CE][LoadPrototype] 17 Errors during XML parse...\n"
    )
    finding = _origins(analyze_rpt(path), "ce_xml_parse_errors")["yours"]
    assert "7 came from your XML" in finding["detail"]


def test_an_animation_config_for_the_loaded_base_game_map_is_baseline_too(tmp_path: Path):
    # A stock Sakhal boot (2026 Q3) logs this for Sakhal itself — before any
    # mission loads, so it was never about which map is running.
    path = tmp_path / "anim.RPT"
    path.write_text(
        " 7:01:31 ANIMATION (E): Can't load sakhal/Anims/cfg/skeletons.anim.xml\n"
        " 7:01:33 WORLD        : Loading landscape 'sakhal'\n"
    )
    assert list(_origins(analyze_rpt(path), "anim_load_error")) == ["vanilla"]


def test_an_animation_config_for_a_mod_map_is_still_the_admins(tmp_path: Path):
    path = tmp_path / "anim.RPT"
    path.write_text(
        "20:14:10 ANIMATION (E): Can't load namalsk/Anims/cfg/skeletons.anim.xml\n"
        "20:14:13  WORLD        : Loading landscape 'chernarusplus'\n"
    )
    assert list(_origins(analyze_rpt(path), "anim_load_error")) == ["yours"]


_MOD_ANIM_LINES = (
    "10:36:42 ANIMATION (E): Can't load @DayZ-Dynamic-AI-Addon/Anims/cfg/skeletons.anim.xml\n"
    "10:36:42 ANIMATION (E): Can't load @Dabs Framework/Anims/cfg/skeletons.anim.xml\n"
    "10:36:42 ANIMATION (E): Can't load @Shipped/Anims/cfg/skeletons.anim.xml\n"
)


def test_the_engines_per_mod_animation_probe_is_noise_when_the_mod_ships_none(tmp_path: Path):
    # Measured in 2026 Q3: 22 mods, 22 lines, and no mod has an Anims folder.
    # A mod that ships the file and still fails is the one worth a warning.
    path = tmp_path / "anim.RPT"
    path.write_text(_MOD_ANIM_LINES)
    report = analyze_rpt(path, mod_file_exists=lambda rel: rel.startswith("@Shipped/"))
    assert _by_id(report)["anim_load_error"]["subjects"] == [
        "@Shipped/Anims/cfg/skeletons.anim.xml"
    ]
    assert {"label": "Animation config a mod does not ship", "count": 2} in report["noise"]["categories"]


def test_without_the_install_every_mod_animation_probe_stays_a_warning(tmp_path: Path):
    path = tmp_path / "anim.RPT"
    path.write_text(_MOD_ANIM_LINES)
    assert _by_id(analyze_rpt(path))["anim_load_error"]["count"] == 3


def test_the_report_asks_the_install_about_mod_animation_files(sample_config):
    shipped = sample_config.server_dir_path / "@Shipped" / "Anims" / "cfg"
    shipped.mkdir(parents=True)
    (shipped / "skeletons.anim.xml").write_text("<x/>")
    (sample_config.server_dir_path / "profiles" / "run.RPT").write_text(server_log("sample-1.29") + _MOD_ANIM_LINES)
    # server_log("sample-1.29")'s own Sakhal line is the stock half; the mod probes are the rest.
    finding = _origins(build_log_report(sample_config), "anim_load_error")["yours"]
    assert finding["subjects"] == ["@Shipped/Anims/cfg/skeletons.anim.xml"]


def test_an_animation_config_for_another_base_game_map_is_baseline(stock_rpt: Path):
    assert list(_origins(analyze_rpt(stock_rpt), "anim_load_error")) == ["vanilla"]


def test_the_missing_shutdown_marker_is_baseline_on_its_own(stock_rpt: Path):
    # It prints after a clean termination too — this very log ends with
    # "--- Termination successfully completed ---".
    assert list(_origins(analyze_rpt(stock_rpt), "ce_no_shutdown_message")) == ["vanilla"]


# ── Persistence findings ─────────────────────────────────────────────────


def test_damaged_persistence_is_reported(rpt: Path):
    finding = _by_id(analyze_rpt(rpt))["storage_stream_damage"]
    assert finding["count"] == 2
    assert finding["origin"] == "yours"


def test_failed_persistence_items_are_counted_and_zero_is_not(rpt: Path):
    # The fixture has "(2 failed)" and "(0 failed)"; only the first is a finding.
    finding = _by_id(analyze_rpt(rpt))["storage_items_failed"]
    assert finding["count"] == 2
    assert finding["occurrences"] == 1


# ── Engine chatter that used to reach the catch-all ──────────────────────


def test_vanilla_asset_warnings_are_suppressed_and_counted(stock_rpt: Path):
    report = analyze_rpt(stock_rpt)
    assert "engine_error" not in _by_id(report)
    labels = {c["label"]: c["count"] for c in report["noise"]["categories"]}
    assert labels["Vanilla asset warnings"] == 2


def test_a_mods_material_error_still_surfaces(tmp_path: Path):
    path = tmp_path / "modmat.RPT"
    path.write_text(
        "20:14:59   MATERIAL  (E): Object @MyMod/thing.xob - cannot load material M.emat\n"
    )
    assert "engine_error" in _by_id(analyze_rpt(path))


# ── Report shape / robustness ────────────────────────────────────────────


def test_summary_counts_only_the_admins_findings(rpt: Path):
    report = analyze_rpt(rpt)
    mine = [f for f in report["findings"] if f["origin"] == "yours"]
    for severity in ("error", "warning", "notice"):
        assert report["summary"][severity] == sum(
            1 for f in mine if f["severity"] == severity
        )
    assert report["summary"]["vanilla"] == len(report["findings"]) - len(mine)


def test_errors_sort_above_warnings_within_each_origin(rpt: Path):
    order = ["error", "warning", "notice"].index
    for origin in ("yours", "vanilla"):
        severities = [
            f["severity"] for f in analyze_rpt(rpt)["findings"] if f["origin"] == origin
        ]
        assert severities == sorted(severities, key=order)


def test_the_admins_findings_sort_above_the_vanilla_ones(rpt: Path):
    origins = [f["origin"] for f in analyze_rpt(rpt)["findings"]]
    assert origins == sorted(origins, key=["yours", "vanilla"].index)


def test_a_missing_file_yields_a_well_formed_report(tmp_path: Path):
    report = analyze_rpt(tmp_path / "nope.RPT")
    assert report["findings"] == []
    assert report["run"]["outcome"] == "unreadable"


def test_past_the_line_cap_only_the_tail_is_read(tmp_path: Path, monkeypatch):
    """A flood in the middle must not hide the errors at the end of the run."""
    from app.ops import log_analysis

    monkeypatch.setattr(log_analysis, "TAIL_LINES", 5)
    path = tmp_path / "big.RPT"
    path.write_text(
        "20:14:08 filler\n" * 100
        + "20:14:09 SCRIPT    (E): late error\n"
        + "20:14:10 filler\n" * 4
    )
    report = analyze_rpt(path, max_lines=10)
    assert report["log"]["truncated"] is True
    assert report["log"]["skipped_lines"] == 90
    assert report["log"]["lines"] == 105
    finding = _by_id(report)["script_error"]
    assert finding["first_line"] == 101


def test_a_log_within_the_line_cap_skips_nothing(tmp_path: Path):
    path = tmp_path / "small.RPT"
    path.write_text("20:14:08 filler\r\n" * 12)
    report = analyze_rpt(path, max_lines=10)
    assert report["log"]["skipped_lines"] == 0 and report["log"]["truncated"] is False
    assert report["log"]["lines"] == 12


# ── Launch-command validation (moved from app/commands/log_analyzer.py) ──


@pytest.mark.parametrize(
    "command",
    [
        'ExecStart=/srv/DayZServer "-mod=@CF;@TraderPlus" -config=serverDZ.cfg',
        "ExecStart=/srv/DayZServer '-mod=@CF;@TraderPlus' -config=serverDZ.cfg",
        "ExecStart=/srv/DayZServer -mod=@CF;@TraderPlus -config=serverDZ.cfg",
    ],
)
def test_all_three_quoting_styles_parse(command: str):
    assert parse_mods(command) == ["@CF", "@TraderPlus"]


def test_validate_mods_flags_missing_folders(tmp_path: Path):
    (tmp_path / "keys").mkdir()
    (tmp_path / "@CF").mkdir()
    issues = validate_mods(tmp_path, tmp_path / "keys", ["@CF", "@Gone"])
    assert ("error", "Missing mod folder: @Gone") in issues


def test_validate_mods_wants_cf_first(tmp_path: Path):
    (tmp_path / "keys").mkdir()
    for name in ("@CF", "@TraderPlus"):
        (tmp_path / name).mkdir()
    issues = validate_mods(tmp_path, tmp_path / "keys", ["@TraderPlus", "@CF"])
    assert ("warning", "@CF should be FIRST in the -mod list.") in issues


# ── Discovery + the composed report ──────────────────────────────────────


def test_logs_are_found_case_insensitively(sample_config, rpt: Path):
    profiles = sample_config.server_dir_path / "profiles"
    (profiles / "a.RPT").write_text("x")
    (profiles / "b.rpt").write_text("x")
    (profiles / "notes.txt").write_text("x")
    assert {entry["name"] for entry in list_logs(sample_config)} == {"a.RPT", "b.rpt"}


def test_the_newest_log_wins(sample_config):
    import os
    import time

    profiles = sample_config.server_dir_path / "profiles"
    (profiles / "old.RPT").write_text("x")
    (profiles / "new.RPT").write_text("x")
    os.utime(profiles / "old.RPT", (time.time() - 600, time.time() - 600))
    assert latest_rpt(sample_config).name == "new.RPT"


def test_resolve_log_refuses_a_path_outside_the_profiles_dir(sample_config):
    (sample_config.server_dir_path / "profiles" / "a.RPT").write_text("x")
    assert resolve_log(sample_config, "../../etc/passwd") is None
    assert resolve_log(sample_config, "a.RPT").name == "a.RPT"


def test_crash_dumps_are_listed(sample_config):
    (sample_config.server_dir_path / "profiles" / "crash.mdmp").write_text("x")
    assert [d["name"] for d in crash_dumps(sample_config)] == ["crash.mdmp"]


def test_report_keeps_the_keys_the_doctor_card_already_used(sample_config):
    report = build_log_report(sample_config)
    assert {"profiles_found", "latest_log", "errors", "mod_validation", "mods_listed"} <= report.keys()


def test_report_is_well_formed_with_no_logs_at_all(sample_config):
    report = build_log_report(sample_config)
    assert report["latest_log"] is None
    assert report["findings"] == []


def test_a_crash_dump_becomes_an_error_finding(sample_config):
    (sample_config.server_dir_path / "profiles" / "crash.mdmp").write_text("x")
    finding = _by_id(build_log_report(sample_config))["crash_dump"]
    assert finding["severity"] == "error"


def test_a_crash_dump_hands_the_shutdown_warning_back_to_the_admin(sample_config):
    """A dump from the *previous* run: it crashed between those two starts."""
    profiles = sample_config.server_dir_path / "profiles"
    (profiles / "DayZServer_2026-09-24_20-04-15.RPT").write_text(server_log("stock-chernarus-1.29"))
    (profiles / "DayZServer_2026-09-24_20-17-01.RPT").write_text(server_log("stock-chernarus-1.29"))
    assert _by_id(build_log_report(sample_config))["ce_no_shutdown_message"][
        "origin"
    ] == "vanilla"

    (profiles / "DayZServer_2026-09-24_20-10-33.mdmp").write_text("x")
    finding = _by_id(build_log_report(sample_config))["ce_no_shutdown_message"]
    assert finding["origin"] == "yours"
    assert "crash dump beside this log" in finding["how"]


def test_an_old_crash_dump_does_not_blame_the_previous_run(sample_config):
    """Test pass 1, F49: the previous run ended cleanly, and the only dump in
    the folder was 13 runs older. It said the previous run crashed."""
    profiles = sample_config.server_dir_path / "profiles"
    (profiles / "DayZServer_2026-09-23_10-12-39.RPT").write_text(server_log("stock-chernarus-1.29"))
    (profiles / "DayZServer_2026-09-23_10-13-47.mdmp").write_text("x")
    for stamp in ("2026-09-23_10-36-36", "2026-09-24_20-04-15", "2026-09-24_20-17-01"):
        (profiles / f"DayZServer_{stamp}.RPT").write_text(server_log("stock-chernarus-1.29"))

    report = build_log_report(sample_config)
    by_id = _by_id(report)
    assert by_id["ce_no_shutdown_message"]["origin"] == "vanilla"
    dump = by_id["crash_dump"]
    assert dump["severity"] == "notice"
    assert dump["detail"].endswith(", 3 runs ago")
    assert report["summary"]["error"] == 0


def test_a_crash_in_the_latest_run_is_an_error_but_not_about_the_previous_one(sample_config):
    profiles = sample_config.server_dir_path / "profiles"
    (profiles / "DayZServer_2026-09-24_20-04-15.RPT").write_text(server_log("stock-chernarus-1.29"))
    (profiles / "DayZServer_2026-09-24_20-17-01.RPT").write_text(server_log("stock-chernarus-1.29"))
    (profiles / "DayZServer_2026-09-24_20-30-00.mdmp").write_text("x")
    by_id = _by_id(build_log_report(sample_config))
    assert by_id["crash_dump"]["severity"] == "error"
    assert by_id["ce_no_shutdown_message"]["origin"] == "vanilla"


def test_a_first_run_with_a_dump_blames_no_previous_run(sample_config):
    profiles = sample_config.server_dir_path / "profiles"
    (profiles / "DayZServer_2026-09-24_20-17-01.RPT").write_text(server_log("stock-chernarus-1.29"))
    (profiles / "DayZServer_2026-09-24_20-10-00.mdmp").write_text("x")
    assert _by_id(build_log_report(sample_config))["ce_no_shutdown_message"][
        "origin"
    ] == "vanilla"


# --- the description line prints with any description (test pass 1, F50) ----

def _description_report(sample_config, description: str) -> dict:
    cfg = sample_config.server_cfg_path
    text = "\n".join(
        line for line in cfg.read_text().splitlines() if not line.strip().startswith("description")
    )
    cfg.write_text(text + f'\ndescription = "{description}";\n')
    (sample_config.server_dir_path / "profiles" / "run.RPT").write_text(server_log("sample-1.29"))
    return _by_id(build_log_report(sample_config))["description_too_long"]


def test_an_empty_description_is_not_the_admins_to_shorten(sample_config):
    finding = _description_report(sample_config, "")
    assert finding["origin"] == "vanilla"
    assert "0 characters" in finding["how"]


def test_a_description_over_the_limit_stays_the_admins(sample_config):
    finding = _description_report(sample_config, "x" * 300)
    assert finding["origin"] == "yours"
    assert "Shorten" in finding["how"]


def test_running_the_map_that_is_no_longer_configured_is_an_error(sample_config, rpt: Path):
    # serverDZ.cfg in the fixture templates dayzOffline.chernarusplus, so a log
    # that ran enoch means the map was switched after this run.
    profiles = sample_config.server_dir_path / "profiles"
    (profiles / "run.RPT").write_text(
        server_log("sample-1.29").replace("dayzOffline.chernarusplus", "dayzOffline.enoch")
    )
    finding = _by_id(build_log_report(sample_config))["mission_mismatch"]
    assert finding["severity"] == "error"
    assert "dayzOffline.enoch" in finding["detail"]


def test_a_matching_mission_raises_nothing(sample_config, rpt: Path):
    (sample_config.server_dir_path / "profiles" / "run.RPT").write_text(server_log("sample-1.29"))
    assert "mission_mismatch" not in _by_id(build_log_report(sample_config))


def test_a_vanilla_run_with_mods_installed_is_an_error(sample_config):
    write_test_mods(sample_config, ['@CF', '@TraderPlus'])
    (sample_config.server_dir_path / "profiles" / "run.RPT").write_text(
        server_log("sample-1.29").replace("-mod=@CF;@TraderPlus", "-mod=")
    )
    finding = _by_id(build_log_report(sample_config))["run_without_mods"]
    assert finding["severity"] == "error"
    assert "2 mod(s)" in finding["detail"]


def test_a_run_whose_mods_are_all_server_side_did_not_run_vanilla(sample_config):
    # plans/47 A: a server-only mod loads through -serverMod=, so -mod= is empty
    # on purpose. That run loaded its mod; calling it vanilla would be wrong.
    write_test_mods(sample_config, ['@DayZ-Dynamic-AI-Addon'])
    set_test_flags(sample_config, server_only=["@DayZ-Dynamic-AI-Addon"])
    (sample_config.server_dir_path / "profiles" / "run.RPT").write_text(
        server_log("sample-1.29").replace("-mod=@CF;@TraderPlus", "-serverMod=@DayZ-Dynamic-AI-Addon")
    )
    report = build_log_report(sample_config)
    assert "run_without_mods" not in _by_id(report)
    assert report["run"]["server_mod_args"] == ["@DayZ-Dynamic-AI-Addon"]


def test_an_empty_mod_flag_is_still_an_error_when_client_mods_were_meant_for_it(sample_config):
    write_test_mods(sample_config, ['@CF', '@TraderPlus', '@DayZ-Dynamic-AI-Addon'])
    set_test_flags(sample_config, server_only=["@DayZ-Dynamic-AI-Addon"])
    (sample_config.server_dir_path / "profiles" / "run.RPT").write_text(
        server_log("sample-1.29").replace("-mod=@CF;@TraderPlus", "-serverMod=@DayZ-Dynamic-AI-Addon")
    )
    finding = _by_id(build_log_report(sample_config))["run_without_mods"]
    assert finding["severity"] == "error"
    assert "2 mod(s) meant for it" in finding["detail"]


def test_run_facts_keep_mod_names_with_spaces(sample_config):
    # The RPT logs the command line unquoted; "@Dabs Framework" is one mod.
    (sample_config.server_dir_path / "profiles" / "run.RPT").write_text(
        server_log("sample-1.29").replace(
            "-mod=@CF;@TraderPlus",
            "-mod=@CF;@Dabs Framework;@DayZ-Expansion-Core -serverMod=@DayZ-Dynamic-AI-Addon",
        )
    )
    run = build_log_report(sample_config)["run"]
    assert run["mod_args"] == ["@CF", "@Dabs Framework", "@DayZ-Expansion-Core"]
    assert run["server_mod_args"] == ["@DayZ-Dynamic-AI-Addon"]
    assert run["port"] == 2302


@pytest.mark.parametrize(
    "command",
    [
        'ExecStart=/bin/bash -c \'exec ./DayZServer "-mod=@CF" "-serverMod=@Dyn;@X" -port=2302\'',
        "ExecStart=/srv/DayZServer '-serverMod=@Dyn;@X' -port=2302",
        "ExecStart=/srv/DayZServer -servermod=@Dyn;@X -port=2302",
    ],
)
def test_parse_server_mods_reads_every_quoting_style(command: str):
    assert parse_server_mods(command) == ["@Dyn", "@X"]


def test_the_two_mod_flags_never_read_each_other():
    command = 'ExecStart=/srv/DayZServer "-mod=@CF" "-serverMod=@Dyn" -port=2302'
    assert parse_mods(command) == ["@CF"]
    assert parse_mods("ExecStart=/srv/DayZServer -serverMod=@Dyn -port=2302") == []
    assert parse_server_mods('ExecStart=/srv/DayZServer "-mod=@CF" -port=2302') == []


def test_a_vanilla_run_with_no_mods_installed_is_only_a_notice(sample_config):
    (sample_config.server_dir_path / "profiles" / "run.RPT").write_text(
        server_log("sample-1.29").replace("-mod=@CF;@TraderPlus", "-mod=")
    )
    assert _by_id(build_log_report(sample_config))["run_without_mods"]["severity"] == "notice"


@pytest.mark.parametrize(
    "command",
    [
        'ExecStart=/srv/DayZServer "-mod=" -config=serverDZ.cfg',
        "ExecStart=/srv/DayZServer '-mod=' -config=serverDZ.cfg",
        "ExecStart=/srv/DayZServer -mod= -config=serverDZ.cfg",
    ],
)
def test_an_empty_mod_flag_parses_to_no_mods(command: str):
    # Units installed before 8a34696 still write a literal `-mod=`. Reading that
    # as one mod named "" made `server_dir / ""` — the server dir itself — look
    # like a present mod folder, and the Doctor card said "All mod folders
    # exist." for a server running none.
    assert parse_mods(command) == []


def test_an_empty_mod_flag_is_reported_as_no_mods(tmp_path: Path):
    (tmp_path / "keys").mkdir()
    command = 'ExecStart=/srv/DayZServer "-mod=" -config=serverDZ.cfg'
    assert validate_mods(tmp_path, tmp_path / "keys", parse_mods(command)) == [
        ("warning", "No mods found in ExecStart.")
    ]


def test_build_log_report_can_target_an_older_log(sample_config):
    profiles = sample_config.server_dir_path / "profiles"
    (profiles / "new.RPT").write_text(server_log("sample-1.29"))
    (profiles / "old.RPT").write_text(server_log("sample-1.29").replace("1.29.163451", "1.28.999"))
    report = build_log_report(sample_config, profiles / "old.RPT")
    assert report["latest_log"] == "old.RPT"
    assert report["run"]["version"] == "1.28.999"


def test_missing_steam_registration_is_not_reported_for_a_boot_that_never_finished(
    sample_config,
):
    # The run never reached "Player connect enabled", so of course Steam was
    # never registered — outcome already says the boot failed.
    (sample_config.server_dir_path / "profiles" / "cut.RPT").write_text(
        "Version 1.29.163451\n20:14:10 WORLD : Loading landscape 'enoch'\n"
    )
    assert "steam_not_registered" not in _by_id(build_log_report(sample_config))


# ── zsm's own spawn test ────────────────────────────────────────────────

# Verbatim from the container run in 2026 Q3 (plan 37 step A9).
SPAWN_TEST_OK = "21:33:07 SCRIPT       : [zsm spawn test] created 10 of 10 Old_Bike\n"
SPAWN_TEST_NONE = "21:54:34 SCRIPT       : [zsm spawn test] created 0 of 2 Bicycle_Base\n"


def _with_line(tmp_path: Path, extra: str) -> Path:
    path = tmp_path / "DayZServer_2026-09-17_21-30-22.RPT"
    path.write_text(server_log("sample-1.29") + extra, encoding="utf-8")
    return path


def test_spawn_test_result_is_reported_as_a_notice(tmp_path: Path):
    finding = _by_id(analyze_rpt(_with_line(tmp_path, SPAWN_TEST_OK)))["spawn_test_ok"]
    assert finding["severity"] == "notice"
    assert finding["subjects"] == ["Old_Bike"]


def test_a_spawn_test_that_created_nothing_is_a_warning(tmp_path: Path):
    findings = _by_id(analyze_rpt(_with_line(tmp_path, SPAWN_TEST_NONE)))
    assert "spawn_test_ok" not in findings  # the 0-of-N rule wins
    finding = findings["spawn_test_none"]
    assert finding["severity"] == "warning"
    assert finding["subjects"] == ["Bicycle_Base"]


# ── the CLI ──────────────────────────────────────────────────────────────


def test_the_command_ends_after_its_report_without_waiting_for_input(sample_config):
    # It ended with a "← Go Back" menu from the deleted CLI menus, which hung
    # any run without a terminal (a pipe, cron) until someone pressed a key.
    from click.testing import CliRunner

    from app.commands.log_analyzer import log_analyzer

    sample_config.save()
    (sample_config.server_dir_path / "profiles" / "run.RPT").write_text(server_log("sample-1.29"))
    result = CliRunner().invoke(log_analyzer, [])
    assert result.exit_code == 0, result.output
    assert "Validating the installed mod list" in result.output
    assert "Go Back" not in result.output


# ── Mod baseline (plans/48 D6) ───────────────────────────────────────────


@pytest.fixture()
def radzone_rpt(tmp_path: Path) -> Path:
    path = tmp_path / "DayZServer_2026-09-23_19-50-31.RPT"
    path.write_text(server_log("radzone-1.29"), encoding="utf-8")
    return path


def test_radzones_own_rejected_types_are_the_mods_not_the_admins(radzone_rpt: Path):
    report = analyze_rpt(radzone_rpt)
    missing = _origins(report, "ce_type_missing")
    assert missing["mod"]["subjects"] == ["DecontaminationShowerStaticUnlimited"]
    assert missing["mod"]["mod"] == "@RadZone"
    assert missing["vanilla"]["subjects"] == ["Static_FrozenScientist_DE"]
    assert "yours" not in missing

    hidden = _origins(report, "ce_type_not_spawnable")
    assert hidden["mod"]["subjects"] == ["NBCBootsBase", "GP5GasMask_Filter"]
    assert hidden["mod"]["count"] == 2
    assert hidden["vanilla"]["subjects"] == ["ChristmasTree"]
    # The admin's own entry in the same hit stays theirs, with its own count.
    assert hidden["yours"]["subjects"] == ["Expansion_M79"]
    assert hidden["yours"]["count"] == 1
    assert [s["text"] for s in hidden["yours"]["samples"]] == [
        "!!! [CE][offlineDB] :: Type 'Expansion_M79' will be ignored. "
        "(Not spawnable. (Scope is not public?))"
    ]

    # The mod's findings are counted apart, never in the headline.
    assert report["summary"] == {"error": 0, "warning": 1, "notice": 0, "vanilla": 2, "mod": 2}


def test_the_mod_note_replaces_the_rules_own_advice(radzone_rpt: Path):
    how = _origins(analyze_rpt(radzone_rpt), "ce_type_not_spawnable")["mod"]["how"]
    assert "RadZone's own types.xml" in how
    assert "Remove the entry" not in how


def test_without_radzone_loaded_the_same_classes_are_the_admins(tmp_path: Path):
    # Someone copied the entries into their own file and never installed the
    # mod: nothing here comes with a mod, so it is all theirs to fix.
    path = tmp_path / "copied.RPT"
    path.write_text(
        # The log was recorded before the rename, under the old user name.
        server_log("radzone-1.29").replace(";@RadZone", "").replace(
            "19:50:34 /home/dzsm/server/@RadZone/Addons/RadZone.pbo - 25092822\n", ""
        ),
        encoding="utf-8",
    )
    report = analyze_rpt(path)
    assert "mod" not in _origins(report, "ce_type_missing")
    assert _origins(report, "ce_type_missing")["yours"]["subjects"] == [
        "DecontaminationShowerStaticUnlimited"
    ]
    assert report["summary"]["mod"] == 0


def test_a_class_the_admin_lists_as_well_stays_theirs(radzone_rpt: Path):
    from app.ops.mission_baseline import MISSION, MissionBaseline

    sources = {
        "NBCBootsBase": ("@RadZone", MISSION),  # also in their db/types.xml
        "GP5GasMask_Filter": ("@RadZone",),
        "DecontaminationShowerStaticUnlimited": ("@RadZone",),
    }
    report = analyze_rpt(
        radzone_rpt,
        baseline_for=lambda _m: MissionBaseline(None, None, type_sources=sources),
    )
    hidden = _origins(report, "ce_type_not_spawnable")
    assert hidden["mod"]["subjects"] == ["GP5GasMask_Filter"]
    assert "NBCBootsBase" in hidden["yours"]["subjects"]


def _renamed_radzone_rpt(tmp_path: Path) -> Path:
    path = tmp_path / "DayZServer_2026-10-10_12-00-00.RPT"
    path.write_text(server_log("radzone-1.29").replace("@RadZone", "@MyRadZone"), encoding="utf-8")
    return path


def test_a_renamed_copy_of_radzone_keeps_its_baseline(tmp_path: Path):
    # An adopted server loads RadZone as @MyRadZone (plans/78 step 9). By
    # folder name its stock rejects read as the admin's; the alias, from the
    # PBO prefixes, gives them back to the mod, owners included.
    from app.ops.mission_baseline import MissionBaseline

    path = _renamed_radzone_rpt(tmp_path)
    assert "mod" not in _origins(analyze_rpt(path), "ce_type_missing")

    sources = {name: ("@MyRadZone",) for name in (
        "NBCBootsBase", "GP5GasMask_Filter", "DecontaminationShowerStaticUnlimited",
    )}
    report = analyze_rpt(
        path,
        baseline_for=lambda _m: MissionBaseline(None, None, type_sources=sources),
        mod_aliases={"@myradzone": "@RadZone"},
    )
    assert _origins(report, "ce_type_missing")["mod"]["subjects"] == [
        "DecontaminationShowerStaticUnlimited"
    ]
    assert _origins(report, "ce_type_not_spawnable")["mod"]["subjects"] == [
        "NBCBootsBase", "GP5GasMask_Filter",
    ]
    assert report["summary"]["mod"] == 2


def test_mod_aliases_match_a_folder_by_its_pbo_prefixes(sample_config):
    from app.ops.log_analysis import mod_aliases
    from tests.test_post_update import _mod

    _mod(sample_config, "@MyRadZone", "RadZone", "RadZone_Data")
    _mod(sample_config, "@RadZone", "RadZone", "RadZone_Data")  # its own name needs no alias
    _mod(sample_config, "@HalfZone", "RadZone")  # one of the two is not RadZone
    _mod(sample_config, "@CF", "JM/CF/Scripts")
    assert mod_aliases(sample_config) == {"@myradzone": "@RadZone"}


def test_every_mod_baseline_entry_names_its_build_and_its_source():
    # A mod update can fix what an entry describes; the entry must say which
    # build it was measured on so it can be rechecked instead of trusted.
    for entry in MOD_BASELINE:
        assert entry.mod.startswith("@")
        assert entry.subjects or entry.every_subject
        assert "build" in entry.source
        # A claim on every subject, kept on after the first boot, would take
        # every later loss away from the admin for as long as the mod loads.
        assert not entry.every_subject or entry.first_boot


# ── ToxicZone and morning boots (plans/48 Step 0, ToxicZone) ─────────────


@pytest.fixture()
def toxiczone_rpt(tmp_path: Path) -> Path:
    path = tmp_path / "DayZServer_2026-09-24_08-40-44.RPT"
    path.write_text(server_log("toxiczone-1.29"), encoding="utf-8")
    return path


def test_a_morning_boot_is_read_as_joinable(toxiczone_rpt: Path):
    """The engine pads a one-digit hour (" 8:41:33"); the pattern used to want
    two digits, so every boot before 10:00 read as never joinable."""
    report = analyze_rpt(toxiczone_rpt)
    assert report["run"]["ready_seconds"] == 46  # 8:40:47 to 8:41:33
    assert report["run"]["outcome"] != "startup_incomplete"
    engine = _origins(report, "engine_error")["yours"]
    assert engine["subjects"][0].startswith("PHYSICS   (E): Action selection")  # no " 8:41:32"


def test_the_physlayer_lines_of_bohemias_own_assets_are_noise(toxiczone_rpt: Path):
    """547 of them on every boot of the test server, every one naming a `dz\\`
    model. Counted under a label, never dropped silently."""
    report = analyze_rpt(toxiczone_rpt)
    engine = _origins(report, "engine_error")["yours"]
    assert not [s for s in engine["subjects"] if "physLayer" in s]
    counts = {c["label"]: c["count"] for c in report["noise"]["categories"]}
    assert counts["Vanilla asset warnings"] >= 1


def test_masks_toxiczone_cannot_read_are_one_finding_each(toxiczone_rpt: Path):
    report = analyze_rpt(toxiczone_rpt)
    ids = {f["id"] for f in report["findings"]}
    # The exception printed for the same item is part of it, not a script error.
    assert "script_error" not in ids
    lost = _origins(report, "storage_scripted_vars")["mod"]
    assert lost["subjects"] == ["GP5GasMask", "AirborneMask"]
    assert lost["count"] == 2 and lost["mod"] == "@ToxicZone"
    assert "once" in lost["how"]


def test_toxiczones_missing_gasmask_is_the_mods(toxiczone_rpt: Path):
    from app.ops.mission_baseline import MISSION, MissionBaseline

    stock = MissionBaseline(None, None, type_sources={"GasMask": (MISSION,)})
    report = analyze_rpt(toxiczone_rpt, baseline_for=lambda _m: stock)
    missing = _origins(report, "ce_type_missing")
    assert missing["mod"]["subjects"] == ["GasMask"] and missing["mod"]["mod"] == "@ToxicZone"
    assert report["summary"]["error"] == 0

    # The same name in the admin's own manual file is theirs to read.
    theirs = MissionBaseline(None, None, type_sources={"GasMask": (MISSION, "@user:mine")})
    report = analyze_rpt(toxiczone_rpt, baseline_for=lambda _m: theirs)
    assert _origins(report, "ce_type_missing")["yours"]["subjects"] == ["GasMask"]


def test_without_toxiczone_loaded_the_lost_masks_are_the_admins(tmp_path: Path):
    path = tmp_path / "other.RPT"
    path.write_text(
        server_log("toxiczone-1.29").replace(";@ToxicZone", "").replace(
            " 8:40:47 /home/dzsm/server/@ToxicZone/Addons/ToxicZone.pbo - 21090123\n", ""
        ),
        encoding="utf-8",
    )
    report = analyze_rpt(path)
    assert _origins(report, "storage_scripted_vars")["yours"]["count"] == 2
    assert _origins(report, "ce_type_missing")["yours"]["subjects"] == ["GasMask"]



# ── TraderPlus: every vehicle, on its first boot only (plans/48 D) ───────


@pytest.fixture()
def traderplus_rpt(tmp_path: Path) -> Path:
    path = tmp_path / "DayZServer_2026-09-24_20-04-15.RPT"
    path.write_text(server_log("traderplus-1.29"), encoding="utf-8")
    return path


def _mods_of(text: str) -> set[str]:
    from app.ops.log_analysis import launched_mods

    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".RPT", delete=False) as f:
        f.write(text)
    return launched_mods(Path(f.name))


def test_the_first_boot_with_traderplus_owns_the_lost_vehicles(traderplus_rpt: Path):
    report = analyze_rpt(traderplus_rpt, previous_mods=_mods_of(before_traderplus_rpt()))
    lost = _origins(report, "storage_scripted_vars")
    assert set(lost) == {"mod"}
    assert lost["mod"]["mod"] == "@TraderPlus" and lost["mod"]["count"] == 3
    assert lost["mod"]["subjects"] == ["CivilianSedan_Black", "CivilianSedan_Wine", "ExpansionZodiacBoat_Black"]
    assert "first" in lost["mod"]["how"]
    assert report["summary"]["warning"] == 0 and report["summary"]["mod"] == 1


@pytest.mark.parametrize("previous", [None, "same mods"])
def test_otherwise_the_lost_vehicles_stay_the_admins(traderplus_rpt: Path, previous):
    """No previous run to compare with, or one that already loaded TraderPlus:
    a vehicle lost now was lost for some other reason."""
    mods = _mods_of(server_log("traderplus-1.29")) if previous else None
    report = analyze_rpt(traderplus_rpt, previous_mods=mods)
    lost = _origins(report, "storage_scripted_vars")
    assert set(lost) == {"yours"} and lost["yours"]["count"] == 3


def test_a_named_entry_keeps_its_items_on_traderplus_first_boot(tmp_path: Path):
    """ToxicZone's masks on the same boot stay ToxicZone's, not TraderPlus's."""
    mask = server_log("toxiczone-1.29").split("\n 8:41:18 !!! Scripted", 1)[1].split("\n 8:41:20 !!!", 1)[0]
    text = server_log("traderplus-1.29").replace(
        "20:05:07 Player connect enabled", "20:05:07 !!! Scripted" + mask + "\n20:05:07 Player connect enabled"
    )
    path = tmp_path / "both.RPT"
    path.write_text(text, encoding="utf-8")
    report = analyze_rpt(path, previous_mods=_mods_of(before_traderplus_rpt()))
    by_mod = {f["mod"]: f["subjects"] for f in report["findings"] if f.get("origin") == "mod"}
    assert by_mod["@ToxicZone"] == ["GP5GasMask"]
    assert "GP5GasMask" not in by_mod["@TraderPlus"]


def test_every_lost_vehicle_is_counted_past_the_subject_cap(tmp_path: Path):
    """83 vehicles in 38 classes on the real boot; a finding lists only the
    first names, and the count must still be all of them."""
    from app.ops.log_analysis import MAX_SUBJECTS

    block = server_log("traderplus-1.29").split("20:05:05 !!! Scripted", 1)[1].split("20:05:05 !!! Scripted", 1)[0]
    many = "".join(
        ("20:05:05 !!! Scripted" + block).replace("CivilianSedan_Black", f"Car_{n}") for n in range(MAX_SUBJECTS + 5)
    )
    path = tmp_path / "many.RPT"
    path.write_text(server_log("traderplus-1.29").replace("20:05:07 Player connect enabled", many + "20:05:07 Player connect enabled"))
    report = analyze_rpt(path, previous_mods=_mods_of(before_traderplus_rpt()))
    lost = _origins(report, "storage_scripted_vars")
    assert set(lost) == {"mod"} and lost["mod"]["count"] == 3 + MAX_SUBJECTS + 5


def test_launched_mods_reads_both_flags(traderplus_rpt: Path, tmp_path: Path):
    from app.ops.log_analysis import launched_mods

    mods = launched_mods(traderplus_rpt)
    assert {"@traderplus", "@dabs framework", "@dayz-dynamic-ai-addon"} <= mods
    (tmp_path / "cut.RPT").write_text("20:00:00 nothing here\n")
    assert launched_mods(tmp_path / "cut.RPT") is None


def test_the_report_compares_with_the_run_before(sample_config):
    import os
    import time

    profiles = sample_config.server_dir_path / "profiles"
    before = profiles / "DayZServer_2026-09-24_12-24-47.RPT"
    first = profiles / "DayZServer_2026-09-24_20-04-15.RPT"
    before.write_text(before_traderplus_rpt())
    first.write_text(server_log("traderplus-1.29"))
    os.utime(before, (time.time() - 600, time.time() - 600))
    assert _origins(build_log_report(sample_config), "storage_scripted_vars")["mod"]["mod"] == "@TraderPlus"

    # The next boot with the same mods: a vehicle lost there is the admin's.
    after = profiles / "DayZServer_2026-09-24_21-00-00.RPT"
    after.write_text(server_log("traderplus-1.29").replace("2026/09/24 20:04:15", "2026/09/24 21:00:00"))
    os.utime(first, (time.time() - 300, time.time() - 300))
    assert set(_origins(build_log_report(sample_config), "storage_scripted_vars")) == {"yours"}


# An invented line in the shape @Survivor Animations prints when a second
# animation mod loads (tests/CLAUDE.md: no mod output pasted in a test).
_TWO_ANIM_LINE = (
    "19:05:33 SCRIPT       : YOU HAVE TWO ANIMATION MODS, SO UNINSTALL OTHER ANIMS, OR ELSE!\n"
)


def test_two_animation_mods_is_an_error_naming_the_mod_to_remove(tmp_path):
    path = tmp_path / "anim.RPT"
    path.write_text(_TWO_ANIM_LINE * 3)
    finding = _by_id(analyze_rpt(path))["two_animation_mods"]
    assert finding["severity"] == "error"
    assert finding["subjects"] == ["OTHER ANIMS"]
    assert finding["count"] == 3


def test_an_unclaimed_line_repeated_thousands_of_times_is_a_flood(tmp_path):
    from app.ops.log_analysis import FLOOD_MIN_REPEATS

    path = tmp_path / "flood.RPT"
    path.write_text(
        "19:05:33 SCRIPT       : some mod says hello\n" * FLOOD_MIN_REPEATS
        + "19:05:34 SCRIPT       : said once\n" * (FLOOD_MIN_REPEATS - 1)
    )
    finding = _by_id(analyze_rpt(path))["log_flood"]
    assert finding["subjects"] == ["SCRIPT       : some mod says hello"]
    assert finding["count"] == FLOOD_MIN_REPEATS


def test_a_missing_resource_is_stock_only_under_bohemias_dz_folder(tmp_path: Path):
    """The engine names the file on the line before the bare failure. A mod's
    file stays the admin's, in the report and in Key lines (is_stock_line)."""
    from app.ops.log_analysis import is_stock_line

    def log(path: str) -> str:
        return (
            f'10:00:00  RESOURCES    : GetResourceObject @"{{1A2B}}/{path}"\n'
            "10:00:00   RESOURCES (E): Failed to open\n"
        )

    stock = _report(tmp_path, log("DZ/invented/stock.anm"))
    assert set(_yours(stock)) == set()
    modded = _report(tmp_path, log("@InventedMod/data/missing.anm"))
    assert "@InventedMod/data/missing.anm" in " ".join(_yours(modded)["engine_error"]["subjects"])

    bare = "10:00:00   RESOURCES (E): Failed to open"
    assert is_stock_line(bare, previous=log("DZ/invented/stock.anm").splitlines()[0])
    assert not is_stock_line(bare, previous=log("@InventedMod/data/missing.anm").splitlines()[0])
    assert not is_stock_line(bare)
