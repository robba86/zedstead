"""Per-mod cfgeconomycore re-link (app/xml/xml_economy.py::merge_mod_economy_entries).

The scoped counterpart to `merge_economy_core`, which wipes custom/ wholesale.
The invariant under test: re-linking one mod must leave every other mod's
custom/ files and `<ce>` registrations exactly where they were.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.xml.xml_economy import merge_economy_core, merge_mod_economy_entries
from tests.conftest import manual_dir, write_test_mods, write_xml

MISSION = "dayzOffline.chernarusplus"


@pytest.fixture()
def two_mod_server(tmp_server_dir: Path, tmp_mission_dir: Path) -> Path:
    write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?><economycore><classes/><defaults/></economycore>',
    )
    write_xml(
        tmp_server_dir / "@Bicycles" / "types.xml",
        '<?xml version="1.0"?><types><type name="Old_Bike"/></types>',
    )
    write_xml(
        tmp_server_dir / "@Bicycles" / "events.xml",
        '<?xml version="1.0"?><events><event name="BikeEvent"><nominal>2</nominal></event></events>',
    )
    write_xml(
        tmp_server_dir / "@Boats" / "types.xml",
        '<?xml version="1.0"?><types><type name="Zodiac"/></types>',
    )
    return tmp_mission_dir


def _ce_text(mission_dir: Path) -> str:
    return (mission_dir / "cfgeconomycore.xml").read_text(encoding="utf-8")


def _custom_names(mission_dir: Path) -> set[str]:
    return {p.name for p in (mission_dir / "custom").glob("*.xml")}


def test_per_mod_link_preserves_other_mods(tmp_server_dir, two_mod_server):
    mission_dir = two_mod_server
    merge_economy_core(tmp_server_dir, MISSION, manual_dir=manual_dir())
    assert "boats-types.xml" in _custom_names(mission_dir)
    boats_file = mission_dir / "custom" / "boats-types.xml"
    boats_before = boats_file.read_text(encoding="utf-8")

    copied = merge_mod_economy_entries(
        tmp_server_dir, MISSION, "@Bicycles", manual_dir=manual_dir(),
    )

    assert {name for name, _t, _m in copied} == {"bicycles-types.xml", "bicycles-events.xml"}
    # Other mod's file untouched on disk and still registered.
    assert boats_file.read_text(encoding="utf-8") == boats_before
    text = _ce_text(mission_dir)
    assert text.count('name="boats-types.xml"') == 1
    assert text.count('name="bicycles-types.xml"') == 1
    assert text.count('name="bicycles-events.xml"') == 1


def test_per_mod_link_is_idempotent(tmp_server_dir, two_mod_server):
    mission_dir = two_mod_server
    merge_mod_economy_entries(tmp_server_dir, MISSION, "@Bicycles", manual_dir=manual_dir())
    merge_mod_economy_entries(tmp_server_dir, MISSION, "@Bicycles", manual_dir=manual_dir())

    text = _ce_text(mission_dir)
    assert text.count('name="bicycles-types.xml"') == 1
    assert _custom_names(mission_dir) == {"bicycles-types.xml", "bicycles-events.xml"}


def test_per_mod_link_keeps_types_first_ordering(tmp_server_dir, two_mod_server):
    """types must stay ahead of events so event payloads resolve against an
    already-declared classname set — appending one mod's entries at the end
    would break that."""
    mission_dir = two_mod_server
    merge_economy_core(tmp_server_dir, MISSION, manual_dir=manual_dir())
    merge_mod_economy_entries(tmp_server_dir, MISSION, "@Bicycles", manual_dir=manual_dir())

    text = _ce_text(mission_dir)
    assert text.index('type="types"') < text.index('type="events"')


def test_per_mod_link_drops_stale_files_for_that_mod(tmp_server_dir, two_mod_server):
    """A custom/ file the mod no longer ships must not survive a re-link."""
    mission_dir = two_mod_server
    merge_economy_core(tmp_server_dir, MISSION, manual_dir=manual_dir())
    (tmp_server_dir / "@Bicycles" / "events.xml").unlink()

    merge_mod_economy_entries(tmp_server_dir, MISSION, "@Bicycles", manual_dir=manual_dir())

    assert "bicycles-events.xml" not in _custom_names(mission_dir)
    assert 'name="bicycles-events.xml"' not in _ce_text(mission_dir)
    assert "boats-types.xml" in _custom_names(mission_dir)


def test_per_mod_link_drops_stale_registrations(tmp_server_dir, two_mod_server):
    """A registration whose custom/ file vanished isn't carried forward."""
    mission_dir = two_mod_server
    merge_economy_core(tmp_server_dir, MISSION, manual_dir=manual_dir())
    (mission_dir / "custom" / "boats-types.xml").unlink()

    merge_mod_economy_entries(tmp_server_dir, MISSION, "@Bicycles", manual_dir=manual_dir())

    assert 'name="boats-types.xml"' not in _ce_text(mission_dir)


def test_per_mod_link_backs_up_without_restoring(tmp_server_dir, two_mod_server):
    """The backup must be created but never restored — restoring would drop
    every other mod's registration."""
    mission_dir = two_mod_server
    merge_economy_core(tmp_server_dir, MISSION, manual_dir=manual_dir())
    backup = mission_dir / "cfgeconomycore.xml.backup"
    assert backup.exists()
    assert "boats-types.xml" not in backup.read_text(encoding="utf-8")

    merge_mod_economy_entries(tmp_server_dir, MISSION, "@Bicycles", manual_dir=manual_dir())

    assert 'name="boats-types.xml"' in _ce_text(mission_dir)


# --- enable/disable round trips (mods.apply_mod_state_to_mission_xml) -------


def _entries(mission_dir: Path) -> set[str]:
    """Registered custom/ filenames whose file is actually there."""
    import re

    text = _ce_text(mission_dir)
    live = text.split("<!--")[0]  # ignore DISABLED wrappers
    return {
        name
        for name in re.findall(r'<file name="([^"]+)"', live)
        if (mission_dir / "custom" / name).is_file()
    }


def test_enable_after_a_full_merge_restores_the_mods_custom_files(
    sample_config, tmp_mission_dir, two_mod_server
):
    """disable -> full merge -> enable used to leave cfgeconomycore.xml
    registering files the merge had wiped: the wrappers come back as live
    entries, but nothing re-copied the files they name. A missing CE file can
    fail the whole economy load."""
    from app.modmgr.mods import set_mod_disabled, set_mod_enabled

    server_dir = sample_config.server_dir_path
    merge_economy_core(server_dir, MISSION, disabled_mods=set(), manual_dir=manual_dir())
    assert "bicycles-types.xml" in _entries(tmp_mission_dir)

    set_mod_disabled(sample_config, "@Bicycles")
    # The merge that does the damage: custom/ is wiped and the disabled mod is
    # skipped, so its files are gone while its wrappers wait.
    merge_economy_core(server_dir, MISSION, disabled_mods={"@Bicycles"}, manual_dir=manual_dir())
    assert "DISABLED @Bicycles:" in _ce_text(tmp_mission_dir)
    assert not (tmp_mission_dir / "custom" / "bicycles-types.xml").exists()

    set_mod_enabled(sample_config, "@Bicycles")

    # Every registration names a file that exists, and the other mod is intact.
    assert "bicycles-types.xml" in _entries(tmp_mission_dir)
    assert "boats-types.xml" in _entries(tmp_mission_dir)
    assert "DISABLED" not in _ce_text(tmp_mission_dir)


def test_plain_disable_enable_round_trip_still_works(
    sample_config, tmp_mission_dir, two_mod_server
):
    """No merge in between — the wrappers are unwrapped and the files were never
    removed, so nothing should change."""
    from app.modmgr.mods import set_mod_disabled, set_mod_enabled

    merge_economy_core(
        sample_config.server_dir_path, MISSION, disabled_mods=set(), manual_dir=manual_dir(),
    )
    before = _entries(tmp_mission_dir)

    set_mod_disabled(sample_config, "@Bicycles")
    assert "bicycles-types.xml" not in _entries(tmp_mission_dir)

    set_mod_enabled(sample_config, "@Bicycles")

    assert _entries(tmp_mission_dir) == before
    assert "DISABLED" not in _ce_text(tmp_mission_dir)


def test_a_mod_whose_name_prefixes_another_leaves_the_other_alone(tmp_server_dir, tmp_mission_dir):
    """`@Trader` and `@Trader_Plus` give custom/ files `trader-…` and
    `trader-plus-…`. Re-linking, removing or disabling @Trader matched on
    the bare `trader-` prefix, and took @Trader_Plus's files with it."""
    from app.xml.xml_economy import (
        disable_mod_economy_entries,
        registered_custom_entries,
        remove_mod_economy_entries,
    )

    write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?><economycore><classes/><defaults/></economycore>',
    )
    write_xml(tmp_server_dir / "@Trader" / "types.xml", '<?xml version="1.0"?><types><type name="A"/></types>')
    write_xml(tmp_server_dir / "@Trader_Plus" / "types.xml", '<?xml version="1.0"?><types><type name="B"/></types>')
    merge_economy_core(tmp_server_dir, MISSION, manual_dir=manual_dir())
    both = {"trader-types.xml", "trader-plus-types.xml"}
    assert {name for name, _t in registered_custom_entries(tmp_mission_dir)} == both

    merge_mod_economy_entries(tmp_server_dir, MISSION, "@Trader", manual_dir=manual_dir())
    assert {name for name, _t in registered_custom_entries(tmp_mission_dir)} == both

    assert disable_mod_economy_entries(tmp_server_dir, MISSION, "@Trader") == 1
    assert {name for name, _t in registered_custom_entries(tmp_mission_dir)} == {"trader-plus-types.xml"}

    remove_mod_economy_entries(tmp_server_dir, MISSION, "@Trader")
    assert (tmp_mission_dir / "custom" / "trader-plus-types.xml").is_file()
    assert {name for name, _t in registered_custom_entries(tmp_mission_dir)} == {"trader-plus-types.xml"}


def test_a_mod_file_named_with_dashes_can_be_disabled(tmp_server_dir, two_mod_server):
    """types--old.xml went into the DISABLED comment as it was, and minidom
    refused "--" in a comment: the disable raised and the mod stayed linked."""
    from app.xml.xml_economy import (
        disable_mod_economy_entries,
        enable_mod_economy_entries,
        registered_custom_entries,
    )
    from app.xml.xml_manual import mod_file_for_custom
    from app.xml.xml_utils import load_xml

    mission_dir = two_mod_server
    source = write_xml(
        tmp_server_dir / "@Boats" / "types--old.xml",
        '<?xml version="1.0"?><types><type name="Raft"/></types>',
    )
    merge_economy_core(tmp_server_dir, MISSION, manual_dir=manual_dir())
    assert "boats-types-old.xml" in _custom_names(mission_dir)
    assert mod_file_for_custom(tmp_server_dir, "boats-types-old.xml", "@Boats") == source

    assert disable_mod_economy_entries(tmp_server_dir, MISSION, "@Boats") == 2
    load_xml(mission_dir / "cfgeconomycore.xml", preserve_comments=True)  # still well-formed
    enable_mod_economy_entries(tmp_server_dir, MISSION, "@Boats")
    names = {name for name, *_ in registered_custom_entries(mission_dir)}
    assert {"boats-types.xml", "boats-types-old.xml"} <= names



# --- a manual file follows its mod's on/off state (browser test 2026-11-17, Q1) ---


def _bike_manual_files(mission_dir: Path, config=None) -> None:
    from app.xml import xml_manual

    if config is not None:
        write_test_mods(config, ["@Bicycles", "@Boats"])

    write_xml(mission_dir / "cfgeventspawns.xml", '<?xml version="1.0"?><eventposdef></eventposdef>')
    xml_manual.create_file(
        "bike-types", "types", '<types><type name="My_Bike"/></types>',
        manual_dir=manual_dir(), mod="@Bicycles",
    )
    xml_manual.create_file(
        "bike-spawns", "cfgeventspawns",
        '<eventposdef><event name="BikeSpot"><pos x="1" z="2" a="0"/></event></eventposdef>',
        manual_dir=manual_dir(), mod="@Bicycles",
    )


def _full_merge(server_dir: Path, disabled: set[str]) -> None:
    from app.xml.xml_merge import merge_all_direct_configs

    merge_economy_core(server_dir, MISSION, disabled_mods=disabled, manual_dir=manual_dir())
    merge_all_direct_configs(server_dir, MISSION, manual_dir=manual_dir(), disabled_mods=disabled)


def test_a_manual_file_goes_off_and_on_with_its_mod(sample_config, tmp_mission_dir, two_mod_server):
    """With @TraderPlus off, its manual types file stayed registered and DayZ
    logged every class in it as unknown."""
    from app.modmgr.mods import set_mod_disabled, set_mod_enabled

    _bike_manual_files(tmp_mission_dir, sample_config)
    _full_merge(sample_config.server_dir_path, set())
    assert "user-bike-types.xml" in _entries(tmp_mission_dir)
    assert "@user:bike-spawns" in (tmp_mission_dir / "cfgeventspawns.xml").read_text()

    set_mod_disabled(sample_config, "@Bicycles")
    assert "user-bike-types.xml" not in _ce_text(tmp_mission_dir)
    assert not (tmp_mission_dir / "custom" / "user-bike-types.xml").exists()
    assert "@user:bike-spawns" not in (tmp_mission_dir / "cfgeventspawns.xml").read_text()
    assert "boats-types.xml" in _entries(tmp_mission_dir)

    set_mod_enabled(sample_config, "@Bicycles")
    assert "user-bike-types.xml" in _entries(tmp_mission_dir)
    assert "@user:bike-spawns" in (tmp_mission_dir / "cfgeventspawns.xml").read_text()


def test_a_full_merge_leaves_out_a_disabled_mods_manual_files(sample_config, tmp_mission_dir, two_mod_server):
    _bike_manual_files(tmp_mission_dir)
    _full_merge(sample_config.server_dir_path, {"@Bicycles"})
    assert "user-bike-types.xml" not in _ce_text(tmp_mission_dir)
    assert "@user:bike-spawns" not in (tmp_mission_dir / "cfgeventspawns.xml").read_text()


def test_a_manual_file_for_a_mod_not_installed_still_applies(sample_config, tmp_mission_dir, two_mod_server):
    from app.xml import xml_manual

    xml_manual.create_file(
        "later-types", "types", '<types><type name="Later"/></types>',
        manual_dir=manual_dir(), mod="@NotInstalledYet",
    )
    _full_merge(sample_config.server_dir_path, {"@Bicycles"})
    assert "user-later-types.xml" in _entries(tmp_mission_dir)


def test_a_workshop_id_names_a_disabled_mod(monkeypatch):
    from app.modmgr import modlist
    from app.xml.xml_manual import mod_is_off

    monkeypatch.setattr(modlist, "known_ids", lambda: {"@TraderPlus": "2458896948"})
    assert mod_is_off("2458896948", {"@TraderPlus"})
    assert mod_is_off("traderplus", {"@TraderPlus"})
    assert not mod_is_off("2458896948", {"@CF"})
    assert not mod_is_off("", {"@TraderPlus"})
