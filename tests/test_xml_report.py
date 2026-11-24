"""Mod XML destination report (app/xml/xml_report.py) — backs /api/modxml."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.xml.xml_economy import merge_economy_core
from app.xml.xml_merge import merge_all_direct_configs
from app.xml.xml_report import build_mod_xml_report, custom_file_path
from tests.conftest import manual_dir, set_test_flags, write_test_ids, write_xml

MISSION = "dayzOffline.chernarusplus"

TYPES = '<?xml version="1.0"?><types><type name="Mine"/></types>'


@pytest.fixture()
def bike_server(tmp_server_dir: Path, tmp_mission_dir: Path, sample_config):
    write_xml(
        tmp_mission_dir / "cfgeventspawns.xml",
        '<?xml version="1.0"?><eventposdef/>',
    )
    write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?><economycore><classes/><defaults/></economycore>',
    )
    mod = tmp_server_dir / "@Bicycles"
    write_xml(mod / "events.xml", '<?xml version="1.0"?><events><event name="E"><nominal>2</nominal></event></events>')
    write_xml(mod / "types.xml", '<?xml version="1.0"?><types><type name="Old_Bike"/></types>')
    write_xml(
        mod / "cfgeventspawns_Chernarus.xml",
        '<?xml version="1.0"?><events><event name="E"><pos x="1" y="2" z="3" a="0"/></event></events>',
    )
    write_xml(
        mod / "cfgeventspawns_Livonia.xml",
        '<?xml version="1.0"?><events><event name="E"><pos x="9" y="9" z="9" a="0"/></event></events>',
    )
    write_xml(mod / "mod_settings.xml", '<?xml version="1.0"?><mysettings><foo/></mysettings>')
    return sample_config


def _rows(report: dict) -> dict[str, dict]:
    return {r["name"]: r for r in report["files"]}


def test_report_shows_each_destination(bike_server):
    config = bike_server
    merge_economy_core(config.server_dir_path, MISSION, manual_dir=manual_dir())
    merge_all_direct_configs(config.server_dir_path, MISSION, manual_dir=manual_dir())

    rows = _rows(build_mod_xml_report(config))

    assert rows["types.xml"]["status"] == "linked"
    assert rows["types.xml"]["custom_file"] == "bicycles-types.xml"
    assert rows["events.xml"]["status"] == "linked"
    assert rows["cfgeventspawns_Chernarus.xml"]["status"] == "merged"
    assert rows["cfgeventspawns_Chernarus.xml"]["target"] == "cfgeventspawns.xml"
    assert rows["cfgeventspawns_Livonia.xml"]["status"] == "other_map"
    assert rows["mod_settings.xml"]["status"] == "ignored"


def test_report_lists_a_non_utf8_file_as_invalid_and_skips_a_directory(bike_server):
    mod = bike_server.server_dir_path / "@Bicycles"
    (mod / "notes.xml").write_bytes('<note>Café</note>\n'.encode("cp1252"))
    (mod / "docs.xml").mkdir()

    rows = _rows(build_mod_xml_report(bike_server))

    assert rows["notes.xml"]["status"] == "invalid"
    assert "docs.xml" not in rows
    assert rows["types.xml"]["status"] == "no_target"


def test_report_flags_unapplied_files(bike_server):
    """Before any merge runs, nothing is linked or merged yet."""
    rows = _rows(build_mod_xml_report(bike_server))

    assert rows["types.xml"]["status"] == "no_target"
    assert rows["cfgeventspawns_Chernarus.xml"]["status"] == "no_target"


def test_report_marks_disabled_mods(bike_server):
    config = bike_server
    merge_economy_core(config.server_dir_path, MISSION, manual_dir=manual_dir())
    set_test_flags(config, disabled=["@Bicycles"])

    rows = _rows(build_mod_xml_report(config))

    assert rows["types.xml"]["status"] == "disabled"


def test_report_lists_generated_custom_files(bike_server):
    config = bike_server
    merge_economy_core(config.server_dir_path, MISSION, manual_dir=manual_dir())

    report = build_mod_xml_report(config)
    names = {c["name"]: c for c in report["custom"]}

    assert names["bicycles-types.xml"]["type"] == "types"
    assert names["bicycles-types.xml"]["linked"] is True
    assert names["bicycles-events.xml"]["type"] == "events"


def test_a_merge_wiping_custom_mid_report_reads_as_missing(bike_server, monkeypatch):
    """GET /api/modxml answered 500 once right after Save and apply.
    A merge wipes and rebuilds custom/ with no lock the
    report takes (`xml_economy._clear_custom_folder`), so a file the report
    listed can be gone when it reads it. Here the merge's own wipe runs right
    after the report lists the folder."""
    from app.xml import xml_economy

    config = bike_server
    merge_economy_core(config.server_dir_path, MISSION, manual_dir=manual_dir())
    mission_dir = config.server_dir_path / "mpmissions" / MISSION
    custom_dir = mission_dir / "custom"
    listed = Path.glob
    wiped = []

    def glob_then_wipe(self, pattern, *args, **kwargs):
        found = list(listed(self, pattern, *args, **kwargs))
        if self == custom_dir and not wiped:
            xml_economy._clear_custom_folder(mission_dir)
            wiped.append(len(found))
        return iter(found)

    monkeypatch.setattr(Path, "glob", glob_then_wipe)
    report = build_mod_xml_report(config)

    assert wiped and wiped[0] >= 2  # the window was really hit
    tiles = {c["name"]: c for c in report["custom"]}
    # Registered, so known, and missing: what custom/ holds at that moment.
    assert tiles["bicycles-types.xml"]["missing"] is True
    assert tiles["bicycles-events.xml"]["missing"] is True
    assert _rows(report)["types.xml"]["status"] == "linked"


def test_group_is_decided_by_kind_not_status(bike_server):
    """The card a file shows in must not change when you turn the file off."""
    config = bike_server
    merge_economy_core(config.server_dir_path, MISSION, manual_dir=manual_dir())
    merge_all_direct_configs(config.server_dir_path, MISSION, manual_dir=manual_dir())

    rows = _rows(build_mod_xml_report(config))
    assert rows["types.xml"]["group"] == "linked"
    assert rows["events.xml"]["group"] == "linked"
    assert rows["cfgeventspawns_Chernarus.xml"]["group"] == "merged"
    # Never applied, and nothing to turn off.
    assert rows["cfgeventspawns_Livonia.xml"]["group"] == "skipped"
    assert rows["mod_settings.xml"]["group"] == "skipped"


@pytest.mark.parametrize("state", ["excluded", "deleted"])
def test_a_turned_off_ce_file_stays_in_the_linked_group(bike_server, state):
    """The bug this replaced: it lost custom_file and resurfaced as a table row."""
    from app.xml import xml_manual

    config = bike_server
    merge_economy_core(config.server_dir_path, MISSION, manual_dir=manual_dir())
    xml_manual.set_custom_state("bicycles-types.xml", state, manual_dir=manual_dir())
    merge_economy_core(config.server_dir_path, MISSION, manual_dir=manual_dir())

    report = build_mod_xml_report(config)
    row = _rows(report)["types.xml"]
    assert row["group"] == "linked"
    assert row["status"] == state
    # custom_name is the join key that survives; custom_file only exists while
    # the file is really registered.
    assert row["custom_name"] == "bicycles-types.xml"
    assert row["custom_file"] is None

    tile = next(c for c in report["custom"] if c["name"] == "bicycles-types.xml")
    assert tile["missing"] is True and tile["excluded"] is True
    assert tile["deleted"] is (state == "deleted")
    # Still knows its origin, so Restore and "Set as manual" have something to
    # work with.
    assert tile["mod"] == "@Bicycles" and tile["source"] == "@Bicycles/types.xml"


def test_a_ce_file_gets_a_tile_before_any_merge_runs(bike_server):
    report = build_mod_xml_report(bike_server)
    tiles = {c["name"]: c for c in report["custom"]}

    assert tiles["bicycles-types.xml"]["missing"] is True
    assert tiles["bicycles-types.xml"]["type"] == "types"
    assert tiles["bicycles-types.xml"]["linked"] is False
    # An other-map variant is skipped outright — it must not grow a tile.
    assert not any("livonia" in name.lower() for name in tiles)


def test_a_disabled_mods_ce_file_keeps_its_tile(bike_server):
    from app.xml.xml_economy import disable_mod_economy_entries

    config = bike_server
    merge_economy_core(config.server_dir_path, MISSION, manual_dir=manual_dir())
    set_test_flags(config, disabled=["@Bicycles"])
    disable_mod_economy_entries(config.server_dir_path, MISSION, "@Bicycles")

    report = build_mod_xml_report(config)
    tile = next(c for c in report["custom"] if c["name"] == "bicycles-types.xml")
    assert tile["disabled"] is True and tile["mod"] == "@Bicycles"
    assert _rows(report)["types.xml"]["group"] == "linked"


def test_deleted_source_reports_its_own_status(bike_server):
    from app.xml import xml_manual

    config = bike_server
    merge_all_direct_configs(config.server_dir_path, MISSION, manual_dir=manual_dir())
    xml_manual.set_source_state(
        "@Bicycles/cfgeventspawns_Chernarus.xml", "deleted", manual_dir=manual_dir(),
    )

    row = _rows(build_mod_xml_report(config))["cfgeventspawns_Chernarus.xml"]
    assert row["status"] == "deleted"
    assert row["deleted"] is True and row["excluded"] is True
    assert row["group"] == "merged"  # it stays in the table it was deleted from


def test_custom_file_path_rejects_traversal(bike_server):
    config = bike_server
    merge_economy_core(config.server_dir_path, MISSION, manual_dir=manual_dir())

    assert custom_file_path(config, "bicycles-types.xml") is not None
    assert custom_file_path(config, "../cfgeconomycore.xml") is None
    assert custom_file_path(config, "/etc/passwd") is None
    assert custom_file_path(config, "nope.xml") is None


def test_manual_file_mod_matches_name_or_workshop_id(bike_server):
    """The field takes either spelling — a user pre-configuring has only one."""
    from app.xml import xml_manual

    config = bike_server
    (config.server_dir_path / "@Bicycles").mkdir(exist_ok=True)
    write_test_ids({'@Bicycles': '1559212036'})

    xml_manual.create_file("by-name", "types", TYPES, mod="@Bicycles", manual_dir=manual_dir())
    xml_manual.create_file("by-bare-name", "types", TYPES, mod="bicycles", manual_dir=manual_dir())
    xml_manual.create_file("by-id", "types", TYPES, mod="1559212036", manual_dir=manual_dir())
    xml_manual.create_file("not-installed", "types", TYPES, mod="@NotHere", manual_dir=manual_dir())

    rows = {r["id"]: r for r in build_mod_xml_report(config)["manual"]}
    for rid in ("by-name", "by-bare-name", "by-id"):
        assert rows[rid]["mod_name"] == "@Bicycles", rid
        assert "orphaned" not in rows[rid], rid
    # Kept and flagged, not hidden: the XML still merges, it just references
    # classnames nothing provides yet.
    assert rows["not-installed"]["mod_name"] == ""
    assert rows["not-installed"]["orphaned"] is True


# ---------------------------------------------------------------------------
# Report and merge must agree — they share `xml_economy.ce_bucket`
# ---------------------------------------------------------------------------


def test_rootless_fragment_is_reported_linked_not_invalid(bike_server):
    """A bare `<type>` fragment has no root to classify, so the report used to
    fail `validate_xml_file` and call it "Invalid" — while the merge linked it
    as types through the filename fallback and it sat live in custom/."""
    config = bike_server
    write_xml(
        config.server_dir_path / "@Bicycles" / "extra_types.xml",
        '<?xml version="1.0"?>\n<type name="A"><nominal>1</nominal></type>\n<type name="B"/>',
    )
    merge_economy_core(config.server_dir_path, MISSION, manual_dir=manual_dir())

    row = _rows(build_mod_xml_report(config))["extra_types.xml"]

    assert row["status"] == "linked"
    assert row["kind"] == "types"
    assert row["custom_file"] == "bicycles-extra_types.xml"


def test_genuinely_broken_xml_is_still_invalid(bike_server):
    config = bike_server
    write_xml(config.server_dir_path / "@Bicycles" / "broken.xml", "<types><type name=")

    assert _rows(build_mod_xml_report(config))["broken.xml"]["status"] == "invalid"


def test_globals_file_gets_a_ce_tile(bike_server):
    config = bike_server
    write_xml(
        config.server_dir_path / "@Bicycles" / "globals.xml",
        '<?xml version="1.0"?><variables><var name="ZombieMaxCount" type="0" value="10"/></variables>',
    )
    merge_economy_core(config.server_dir_path, MISSION, manual_dir=manual_dir())

    report = build_mod_xml_report(config)
    row = _rows(report)["globals.xml"]
    assert (row["group"], row["kind"], row["status"]) == ("linked", "globals", "linked")

    tile = {c["name"]: c for c in report["custom"]}["bicycles-globals.xml"]
    assert tile["type"] == "globals"
    assert tile["linked"] is True


def test_messages_file_reports_not_auto_linked(bike_server):
    """Understood and forkable, but never applied off a mod dir — the admin
    opts in with "Set as manual" rather than having a mod's example broadcasts
    go out to players."""
    config = bike_server
    write_xml(
        config.server_dir_path / "@Bicycles" / "messages.xml",
        '<?xml version="1.0"?><messages><message><text>hi</text></message></messages>',
    )
    merge_economy_core(config.server_dir_path, MISSION, manual_dir=manual_dir())

    row = _rows(build_mod_xml_report(config))["messages.xml"]
    assert row["status"] == "not_auto_linked"
    assert (row["group"], row["kind"]) == ("linked", "messages")
    assert not (
        config.server_dir_path / "mpmissions" / MISSION / "custom" / "bicycles-messages.xml"
    ).exists()


# ---------------------------------------------------------------------------
# CE folders other than custom/
# ---------------------------------------------------------------------------


def test_foreign_ce_folders_are_reported(bike_server):
    """zsm preserves them but wrote nothing about them anywhere, so a file the
    server really loads was invisible in the whole UI."""
    config = bike_server
    mission = config.server_dir_path / "mpmissions" / MISSION
    write_xml(mission / "db" / "types.xml", TYPES)
    write_xml(
        mission / "cfgeconomycore.xml",
        '<?xml version="1.0"?><economycore>'
        '<ce folder="db"><file name="types.xml" type="types"/>'
        '<file name="gone.xml" type="events"/></ce>'
        '<ce folder="mine"><file name="x.xml" type="loot"/></ce>'
        "</economycore>",
    )

    rows = {(r["folder"], r["name"]): r for r in build_mod_xml_report(config)["ce_folders"]}

    assert rows[("db", "types.xml")]["exists"] is True
    assert rows[("db", "types.xml")]["known_type"] is True
    assert rows[("db", "gone.xml")]["exists"] is False
    # `loot` is not one of DayZ's six — it breaks the whole economy load.
    assert rows[("mine", "x.xml")]["known_type"] is False
    # custom/ has its own tiles; it must not be duplicated here.
    assert not any(folder == "custom" for folder, _name in rows)


def test_a_ce_folder_outside_the_mission_is_neither_read_nor_listed(bike_server, caplog):
    """cfgeconomycore.xml can come with a map mission. A registration whose
    folder or name leaves the mission must not tell the page whether some
    other file exists, or how big it is (plans/68 CON-8)."""
    import logging

    config = bike_server
    server = config.server_dir_path
    mission = server / "mpmissions" / MISSION
    (server / "outside.xml").write_text(TYPES, encoding="utf-8")
    write_xml(mission / "db" / "sub" / "types.xml", TYPES)
    write_xml(
        mission / "cfgeconomycore.xml",
        '<?xml version="1.0"?><economycore>'
        '<ce folder="../.."><file name="outside.xml" type="types"/></ce>'
        '<ce folder="db"><file name="../../../outside.xml" type="types"/></ce>'
        f'<ce folder="{server}"><file name="outside.xml" type="types"/></ce>'
        '<ce folder="db\\..\\..\\.."><file name="outside.xml" type="types"/></ce>'
        '<ce folder="db/sub"><file name="types.xml" type="types"/></ce>'
        "</economycore>",
    )

    with caplog.at_level(logging.WARNING, logger="app.xml.xml_economy"):
        rows = build_mod_xml_report(config)["ce_folders"]

    assert [(r["folder"], r["name"], r["exists"]) for r in rows] == [("db/sub", "types.xml", True)]
    outside = [r.getMessage() for r in caplog.records if "outside the mission folder" in r.getMessage()]
    assert len(outside) == 4


_ECONOMYCORE_WITH_ALL_THREE = (
    '<?xml version="1.0"?>\n<economycore>\n'
    '\t<ce folder="custom">\n'
    '\t\t<file name="live-types.xml" type="types"/>\n'
    "\t</ce>\n"
    '\t<!-- DISABLED @Parked: <ce folder="custom"><file name="parked-events.xml" type="events"/></ce> -->\n'
    '\t<ce folder="db">\n'
    '\t\t<file name="types.xml" type="types"/>\n'
    "\t</ce>\n"
    "{junk}"
    "</economycore>\n"
)


@pytest.mark.parametrize(
    "junk",
    ["\t<!-- DISABLED x:\n" * 2500, '\t<ce folder="x">\n' * 8000],
    ids=["disabled-openers-without-close", "ce-openers-without-close"],
)
def test_economycore_openers_without_a_close_are_read_in_linear_time(tmp_mission_dir, junk):
    """The lazy regexes this scan replaced re-read the rest of the file from
    every opener with no close: these two files took 2-4s each, and the time
    grew with the square of the size. A map mission ships its own
    cfgeconomycore.xml, and the report reads it on every Mod XML page load."""
    import time

    from app.xml.xml_report import _ce_entries, _foreign_ce_entries

    write_xml(tmp_mission_dir / "cfgeconomycore.xml", _ECONOMYCORE_WITH_ALL_THREE.format(junk=junk))

    started = time.perf_counter()
    entries = _ce_entries(tmp_mission_dir)
    foreign = _foreign_ce_entries(tmp_mission_dir)
    elapsed = time.perf_counter() - started

    assert entries == {
        "parked-events.xml": {"type": "events", "disabled": True},
        "live-types.xml": {"type": "types", "disabled": False},
    }
    assert [(r["folder"], r["name"]) for r in foreign] == [("db", "types.xml")]
    assert elapsed < 1.0, f"took {elapsed:.2f}s"


def test_a_mod_whose_name_has_a_space_reads_as_merged(bike_server):
    """A Workshop title gives a mod a name with spaces (`title_mod_name`
    keeps them). The report read the markers with `(\\S+)`, so such a mod's
    merged spawn positions showed as not applied."""
    server_dir = bike_server.server_dir_path
    (server_dir / "@Bicycles").rename(server_dir / "@Big Bikes")
    merge_all_direct_configs(server_dir, MISSION, manual_dir=manual_dir())

    report = build_mod_xml_report(bike_server)

    assert report["merged"]["cfgeventspawns.xml"] == ["@Big Bikes"]
    assert _rows(report)["cfgeventspawns_Chernarus.xml"]["status"] == "merged"


def test_a_mod_the_merge_skips_for_its_name_says_why(bike_server):
    """The merge skips a direct file of a mod named with "--", since the name
    cannot sit in a START/END MOD comment. The report called it "no_target",
    which reads as "not applied yet" and hides the fix (rename the folder)."""
    server_dir = bike_server.server_dir_path
    (server_dir / "@Bicycles").rename(server_dir / "@Big--Bikes")
    merge_all_direct_configs(server_dir, MISSION, manual_dir=manual_dir())

    report = build_mod_xml_report(bike_server)

    assert "cfgeventspawns.xml" not in report["merged"]
    assert _rows(report)["cfgeventspawns_Chernarus.xml"]["status"] == "bad_name"


def test_a_mission_file_that_is_not_utf8_does_not_stop_the_report(bike_server, tmp_mission_dir):
    """A map author's Windows-1252 mission file raised UnicodeDecodeError out
    of the whole report."""
    (tmp_mission_dir / "cfgrandompresets.xml").write_bytes(
        '<randompresets><cargo name="Café" chance="1"/></randompresets>\n'.encode("cp1252")
    )
    merge_all_direct_configs(bike_server.server_dir_path, MISSION, manual_dir=manual_dir())

    report = build_mod_xml_report(bike_server)

    assert "cfgrandompresets.xml" not in report["merged"]
    assert _rows(report)["cfgeventspawns_Chernarus.xml"]["status"] == "merged"
