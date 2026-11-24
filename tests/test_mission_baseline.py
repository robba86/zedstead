"""Tests for app/ops/mission_baseline.py and how log_analysis uses it (plans/39).

The fixture missions are hand-built and tiny; the real numbers (5/4/5 pointless
groups, ``VehicleTransitBus`` on Chernarus and Livonia and nothing on Sakhal)
were checked against a boot of each stock map in 2026 Q3 and are recorded
in the module docstring rather than re-measured here.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.ops import mission_baseline
from app.ops.log_analysis import analyze_rpt, build_log_report
from app.ops.mission_baseline import MISSION, baseline_for, derive

GROUPS = """<?xml version="1.0" encoding="UTF-8"?>
<prototype>
  <group name="Land_Has_Points" lootmax="4">
    <usage name="Town"/>
    <container name="lootFloor"><category name="tools"/><point pos="1 2 3" range="0.5" height="1"/></container>
  </group>
  <group name="Land_Empty_A" lootmax="4"><usage name="Town"/></group>
  <group name="Land_Empty_B"><container name="lootFloor"/></group>
</prototype>
"""

TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<types>
  <type name="Apple"/>
  <type name="Stock_Only"/>
</types>
"""

EVENTS = """<?xml version="1.0" encoding="UTF-8"?>
<events>
  <event name="VehicleCivilianSedan"/>
</events>
"""

EVENTSPAWNS = """<?xml version="1.0" encoding="UTF-8"?>
<eventposdef>
  <event name="VehicleCivilianSedan"><pos x="1" z="2"/></event>
  <event name="VehicleTransitBus"><pos x="3" z="4"/></event>
  <!-- START MOD: @Bikes -->
  <event name="VehicleBike"><pos x="5" z="6"/></event>
  <event name="VehicleUnicycle"><pos x="7" z="8"/></event>
  <!-- END MOD: @Bikes -->
</eventposdef>
"""

ECONOMYCORE = """<?xml version="1.0" encoding="UTF-8"?>
<economycore>
  <ce folder="custom">
    <file name="bikes-events.xml" type="events"/>
    <file name="bikes-types.xml" type="types"/>
    <file name="user-mine.xml" type="types"/>
  </ce>
</economycore>
"""


@pytest.fixture()
def mission(tmp_path: Path) -> Path:
    server = tmp_path / "server"
    m = server / "mpmissions" / "dayzOffline.chernarusplus"
    (m / "db").mkdir(parents=True)
    (m / "custom").mkdir()
    (server / "@Bikes").mkdir()
    (m / "mapgroupproto.xml").write_text(GROUPS)
    (m / "db" / "types.xml").write_text(TYPES)
    (m / "db" / "events.xml").write_text(EVENTS)
    (m / "cfgeventspawns.xml").write_text(EVENTSPAWNS)
    (m / "cfgeconomycore.xml").write_text(ECONOMYCORE)
    (m / "custom" / "bikes-events.xml").write_text(
        '<events><event name="VehicleBike"/></events>'
    )
    (m / "custom" / "bikes-types.xml").write_text(
        '<types><type name="Bike_Frame"/><type name="Apple"/></types>'
    )
    (m / "custom" / "user-mine.xml").write_text('<types><type name="My_Hat"/></types>')
    # In custom/ but not registered: the engine never loads it, so neither may we.
    (m / "custom" / "stray-events.xml").write_text(
        '<events><event name="VehicleUnicycle"/></events>'
    )
    return m


# ── The derivations ──────────────────────────────────────────────────────


def test_groups_without_a_point_are_the_pointless_ones(mission: Path):
    assert derive(mission).pointless_groups == ("Land_Empty_A", "Land_Empty_B")


def test_an_orphan_event_outside_any_block_is_the_missions(mission: Path):
    assert derive(mission).orphan_event_spawns == ("VehicleTransitBus",)


def test_an_orphan_event_inside_a_mod_block_is_the_mods(mission: Path):
    # VehicleBike is defined by the mod's registered custom/ events file, so it
    # is no orphan; VehicleUnicycle is defined only in an unregistered file.
    assert derive(mission).mod_orphan_event_spawns == {"@Bikes": ("VehicleUnicycle",)}


def test_type_sources_name_every_file_that_defines_a_class(mission: Path):
    sources = derive(mission).type_sources
    assert sources["Stock_Only"] == (MISSION,)
    assert sources["Bike_Frame"] == ("@Bikes",)
    assert sources["Apple"] == (MISSION, "@Bikes")
    assert sources["My_Hat"] == ("@user:mine",)


def test_a_registration_outside_the_mission_is_not_read(mission: Path):
    """A custom/ name or a folder that leaves the mission is skipped, so a
    cfgeconomycore.xml that came with a map cannot have other files parsed
    (plans/68 CON-8)."""
    server = mission.parent.parent
    (server / "outside-types.xml").write_text('<types><type name="Outside_Class"/></types>')
    (server / "outside-too.xml").write_text('<types><type name="Outside_Too"/></types>')
    (mission / "cfgeconomycore.xml").write_text(
        ECONOMYCORE.replace(
            '    <file name="user-mine.xml" type="types"/>\n',
            '    <file name="user-mine.xml" type="types"/>\n'
            '    <file name="../../../outside-types.xml" type="types"/>\n',
        ).replace(
            "</economycore>",
            '  <ce folder="../.."><file name="outside-too.xml" type="types"/></ce>\n'
            "</economycore>",
        )
    )

    sources = derive(mission).type_sources

    assert "Outside_Class" not in sources
    assert "Outside_Too" not in sources
    assert sources["My_Hat"] == ("@user:mine",)


def test_a_parked_registration_is_not_loaded(mission: Path):
    (mission / "cfgeconomycore.xml").write_text(
        ECONOMYCORE.replace(
            '    <file name="bikes-events.xml" type="events"/>\n', ""
        ).replace(
            "</economycore>",
            '<!-- DISABLED @Bikes: <ce folder="custom"><file name="bikes-events.xml" '
            'type="events"/></ce> -->\n</economycore>',
        )
    )
    assert derive(mission).mod_orphan_event_spawns == {
        "@Bikes": ("VehicleBike", "VehicleUnicycle")
    }


def test_an_unreadable_events_file_derives_nothing_rather_than_everything(mission: Path):
    # Read as "nothing is defined", every event spawn would be an orphan and a
    # healthy server would look badly broken.
    (mission / "db" / "events.xml").write_text("<events><event name=")
    result = derive(mission)
    assert result.orphan_event_spawns is None
    assert "db/events.xml" in result.unreadable


def test_a_missing_mapgroupproto_derives_nothing(mission: Path):
    (mission / "mapgroupproto.xml").unlink()
    result = derive(mission)
    assert result.pointless_groups is None
    assert "mapgroupproto.xml" in result.unreadable


def test_an_unreadable_types_file_leaves_no_type_sources(mission: Path):
    (mission / "db" / "types.xml").write_text("not xml")
    assert derive(mission).type_sources is None


def test_the_cache_follows_the_files(mission: Path):
    first = baseline_for(mission)
    assert baseline_for(mission) is first
    groups = mission / "mapgroupproto.xml"
    groups.write_text(GROUPS.replace('<group name="Land_Empty_B"><container name="lootFloor"/></group>', ""))
    st = groups.stat()
    os.utime(groups, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert baseline_for(mission).pointless_groups == ("Land_Empty_A",)


def test_not_a_mission_dir_has_no_baseline(tmp_path: Path):
    assert baseline_for(tmp_path / "nope") is None


# ── Wired into the log report ────────────────────────────────────────────

HEADER = (
    "20:14:13  ENGINE       : FileSystem: Adding relative directory "
    "'/srv/dayz/mpmissions/dayzOffline.chernarusplus' to RV filesystem under name mission\n"
    "20:14:13  WORLD        : Loading landscape 'chernarusplus'\n"
)


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "run.RPT"
    path.write_text(HEADER + body, encoding="utf-8")
    return path


def _findings(report: dict, rule: str) -> dict[str, dict]:
    return {f["origin"]: f for f in report["findings"] if f["id"] == rule}


def test_the_derived_group_count_beats_the_table(tmp_path: Path, mission: Path):
    # The table says a stock Chernarus prints 5; this mission has 2, so 3 is
    # one more than the mission explains, and the report says so.
    log = _write(tmp_path, "20:14:27 !!! [CE][LoadPrototype] 3 groups have no points...\n")
    report = analyze_rpt(log, baseline_for=lambda _m: derive(mission))
    finding = _findings(report, "ce_groups_no_points")["yours"]
    assert finding["detail"] == (
        "3 reported — 2 are your mission's own, 1 more than its "
        "mapgroupproto.xml explains as it reads now"
    )
    assert "Land_Empty_A, Land_Empty_B" in finding["how"]


def test_a_group_count_the_mission_explains_is_baseline_with_names(
    tmp_path: Path, mission: Path
):
    log = _write(tmp_path, "20:14:27 !!! [CE][LoadPrototype] 2 groups have no points...\n")
    finding = _findings(
        analyze_rpt(log, baseline_for=lambda _m: derive(mission)), "ce_groups_no_points"
    )["vanilla"]
    assert finding["detail"] == "2 reported — all 2 are your mission's own mapgroupproto.xml groups"
    assert "Land_Empty_A, Land_Empty_B" in finding["how"]


def test_a_community_map_gets_a_baseline_from_its_own_mission(tmp_path: Path, mission: Path):
    # No table entry covers an unknown terrain; the derivation does.
    path = tmp_path / "custom.RPT"
    path.write_text(
        "20:14:13  ENGINE       : FileSystem: Adding relative directory "
        "'/srv/dayz/mpmissions/empty.somecustommap' to RV filesystem under name mission\n"
        "20:14:13  WORLD        : Loading landscape 'somecustommap'\n"
        "20:14:29 !!! [CE][DE][SPAWNS] :: [WARNING] :: Skipping entry for non-existing event 'VehicleTransitBus'.\n"
    )
    report = analyze_rpt(path, baseline_for=lambda _m: derive(mission))
    assert list(_findings(report, "ce_missing_event")) == ["vanilla"]


def test_an_orphan_event_in_a_mod_block_stays_the_admins_and_names_the_mod(
    tmp_path: Path, mission: Path
):
    log = _write(
        tmp_path,
        "20:14:29 !!! [CE][DE][SPAWNS] :: [WARNING] :: Skipping entry for non-existing event 'VehicleTransitBus'.\n"
        "20:14:29 !!! [CE][DE][SPAWNS] :: [WARNING] :: Skipping entry for non-existing event 'VehicleUnicycle'.\n",
    )
    halves = _findings(analyze_rpt(log, baseline_for=lambda _m: derive(mission)), "ce_missing_event")
    assert halves["vanilla"]["subjects"] == ["VehicleTransitBus"]
    assert halves["yours"]["subjects"] == ["VehicleUnicycle"]
    assert (
        "In cfgeventspawns.xml, @Bikes added positions for VehicleUnicycle"
        in halves["yours"]["how"]
    )


def test_the_table_is_the_fallback_when_the_mission_cannot_answer(
    tmp_path: Path, mission: Path
):
    (mission / "db" / "events.xml").write_text("<events><event name=")
    log = _write(
        tmp_path,
        "20:14:29 !!! [CE][DE][SPAWNS] :: [WARNING] :: Skipping entry for non-existing event 'VehicleTransitBus'.\n",
    )
    halves = _findings(analyze_rpt(log, baseline_for=lambda _m: derive(mission)), "ce_missing_event")
    # Chernarus' table entry still covers it — "could not tell" is not "yours".
    assert list(halves) == ["vanilla"]
    assert "stock events.xml never defines" in halves["vanilla"]["how"]


def test_a_rejected_type_is_attributed_to_whoever_listed_it(tmp_path: Path, mission: Path):
    log = _write(
        tmp_path,
        "20:14:29 !!! [CE][offlineDB] :: Type 'Bike_Frame' will be ignored. (Type does not exist. (Typo?))\n"
        "20:14:29 !!! [CE][offlineDB] :: Type 'Stock_Only' will be ignored. (Type does not exist. (Typo?))\n"
        "20:14:29 !!! [CE][offlineDB] :: Type 'Gone_Now' will be ignored. (Type does not exist. (Typo?))\n",
    )
    finding = _findings(
        analyze_rpt(log, baseline_for=lambda _m: derive(mission)), "ce_type_missing"
    )["yours"]
    assert "From @Bikes: Bike_Frame" in finding["how"]
    assert "In your mission's db/types.xml: Stock_Only" in finding["how"]
    assert "In no types file now: Gone_Now" in finding["how"]
    assert {"label": "Custom config", "to": "/configs?tab=Custom"} in finding["links"]


def test_a_non_public_type_from_a_manual_file_is_not_blamed_on_a_mod(
    tmp_path: Path, mission: Path
):
    log = _write(
        tmp_path,
        "20:14:29 !!! [CE][offlineDB] :: Type 'My_Hat' will be ignored. (Not spawnable. (Scope is not public?))\n",
    )
    how = _findings(
        analyze_rpt(log, baseline_for=lambda _m: derive(mission)), "ce_type_not_spawnable"
    )["yours"]["how"]
    assert how.startswith("From your manual XML file mine: My_Hat")
    assert "the mod's" not in how


def test_the_stock_half_of_a_type_finding_keeps_the_tables_note(tmp_path: Path, mission: Path):
    log = _write(
        tmp_path,
        "20:14:29 !!! [CE][offlineDB] :: Type 'ChristmasTree' will be ignored. (Not spawnable. (Scope is not public?))\n",
    )
    halves = _findings(
        analyze_rpt(log, baseline_for=lambda _m: derive(mission)), "ce_type_not_spawnable"
    )
    assert list(halves) == ["vanilla"]
    assert "stock Chernarus types.xml" in halves["vanilla"]["how"]


def test_build_log_report_judges_the_mission_the_log_loaded(sample_config, tmp_server_dir: Path):
    # The configured mission is Chernarus; the log ran a different one, and the
    # derivation must come from the one it ran.
    ran = tmp_server_dir / "mpmissions" / "other.enoch"
    (ran / "db").mkdir(parents=True)
    (ran / "mapgroupproto.xml").write_text(GROUPS)
    (ran / "db" / "types.xml").write_text(TYPES)
    (ran / "db" / "events.xml").write_text(EVENTS)
    (ran / "cfgeventspawns.xml").write_text(EVENTSPAWNS)
    (tmp_server_dir / "profiles" / "DayZServer_x.RPT").write_text(
        "20:14:13  ENGINE       : FileSystem: Adding relative directory "
        f"'{ran}' to RV filesystem under name mission\n"
        "20:14:13  WORLD        : Loading landscape 'enoch'\n"
        "20:14:27 !!! [CE][LoadPrototype] 2 groups have no points...\n"
    )
    report = build_log_report(sample_config)
    finding = next(f for f in report["findings"] if f["id"] == "ce_groups_no_points")
    assert finding["origin"] == "vanilla"
    assert "all 2 are your mission's own" in finding["detail"]


def test_a_baseline_that_blows_up_never_takes_the_report_down(
    sample_config, tmp_server_dir: Path, monkeypatch
):
    def boom(_path):
        raise RuntimeError("bad mission")

    monkeypatch.setattr(mission_baseline, "baseline_for", boom)
    (tmp_server_dir / "profiles" / "DayZServer_x.RPT").write_text(
        HEADER + "20:14:27 !!! [CE][LoadPrototype] 5 groups have no points...\n"
    )
    report = build_log_report(sample_config)
    finding = next(f for f in report["findings"] if f["id"] == "ce_groups_no_points")
    assert finding["detail"] == "5 reported — a stock Chernarus server prints 5"
