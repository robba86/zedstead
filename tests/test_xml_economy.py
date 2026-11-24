"""Tests for xml_economy disable/enable/remove CE entry functions."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.conftest import manual_dir, write_xml

SAMPLE_ECONCORE = """\
<?xml version="1.0" encoding="UTF-8"?>
<economycore>
    <ce folder="db">
        <file name="types.xml" type="types"/>
    </ce>
    <ce folder="custom">
        <file name="cool-mod-types.xml" type="types"/>
    </ce>
    <ce folder="custom">
        <file name="other-mod-types.xml" type="types"/>
    </ce>
</economycore>"""

SAMPLE_ECONCORE_MIXED = """\
<?xml version="1.0" encoding="UTF-8"?>
<economycore>
    <ce folder="db">
        <file name="types.xml" type="types"/>
    </ce>
    <ce folder="custom">
        <file name="cool-mod-types.xml" type="types"/>
    </ce>
    <ce folder="custom">
        <file name="cool-mod-events.xml" type="events"/>
    </ce>
    <ce folder="custom">
        <file name="cool-mod-cfgspawnabletypes.xml" type="spawnabletypes"/>
    </ce>
</economycore>"""


@pytest.fixture()
def econ_xml(tmp_mission_dir: Path) -> Path:
    return write_xml(tmp_mission_dir / "cfgeconomycore.xml", SAMPLE_ECONCORE)


@pytest.fixture()
def econ_xml_mixed(tmp_mission_dir: Path) -> Path:
    return write_xml(tmp_mission_dir / "cfgeconomycore.xml", SAMPLE_ECONCORE_MIXED)


@pytest.fixture()
def server_dir(tmp_mission_dir: Path) -> Path:
    return tmp_mission_dir.parent.parent


# -- registrations that leave the mission (plans/68 CON-8) --


def test_registered_custom_entries_leave_out_a_name_outside_the_mission(
    tmp_mission_dir: Path, server_dir: Path
):
    """Its callers read the file, and the Only X spawn test rewrites it."""
    from app.xml.xml_economy import registered_custom_entries

    write_xml(tmp_mission_dir / "custom" / "mod-events.xml", "<events/>")
    write_xml(server_dir / "outside-events.xml", "<events/>")
    write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?><economycore><ce folder="custom">'
        '<file name="mod-events.xml" type="events"/>'
        '<file name="../../../outside-events.xml" type="events"/>'
        "</ce></economycore>",
    )

    assert registered_custom_entries(tmp_mission_dir) == [("mod-events.xml", "events")]


@pytest.mark.parametrize(
    ("folder", "name", "inside"),
    [
        ("db", "types.xml", True),
        ("db/sub", "types.xml", True),
        ("./db", "types.xml", True),
        ("custom", "sub/x.xml", True),
        ("..", "types.xml", False),
        ("db", "../../x.xml", False),
        ("db\\..\\..", "x.xml", False),
        ("/etc", "passwd", False),
        ("\\\\host\\share", "x.xml", False),
        ("db", "/etc/passwd", False),
    ],
)
def test_ce_file_path_keeps_a_registration_inside_the_mission(
    tmp_mission_dir: Path, folder: str, name: str, inside: bool
):
    from app.xml.xml_economy import ce_file_path

    path = ce_file_path(tmp_mission_dir, folder, name)
    assert (path is not None) is inside
    if inside:
        assert tmp_mission_dir in path.parents


# -- disable --


def test_disable_comments_out_matching_ce(server_dir, econ_xml):
    from app.xml.xml_economy import disable_mod_economy_entries

    count = disable_mod_economy_entries(
        server_dir, "dayzOffline.chernarusplus", "@Cool-Mod",
    )
    assert count == 1
    text = econ_xml.read_text()
    assert "<!-- DISABLED @Cool-Mod:" in text
    assert 'cool-mod-types.xml' in text
    # Other mod untouched
    assert '<ce folder="custom">' in text
    assert 'other-mod-types.xml' in text


def test_disable_is_idempotent_no_nested_comment(server_dir, econ_xml):
    from xml.dom import minidom

    from app.xml.xml_economy import disable_mod_economy_entries

    first = disable_mod_economy_entries(
        server_dir, "dayzOffline.chernarusplus", "@Cool-Mod",
    )
    second = disable_mod_economy_entries(
        server_dir, "dayzOffline.chernarusplus", "@Cool-Mod",
    )
    assert first == 1
    assert second == 0  # already disabled — must not re-wrap

    text = econ_xml.read_text()
    # Exactly one wrapper; never nested. Nested <!-- --> embeds "--", which is
    # invalid XML and would stop the server loading cfgeconomycore.xml.
    assert text.count("<!-- DISABLED @Cool-Mod:") == 1
    assert "<!-- DISABLED @Cool-Mod: <!-- DISABLED" not in text
    minidom.parseString(text)  # raises if the disable corrupted the XML


def test_disable_leaves_other_entries(server_dir, econ_xml):
    from app.xml.xml_economy import disable_mod_economy_entries

    disable_mod_economy_entries(
        server_dir, "dayzOffline.chernarusplus", "@Cool-Mod",
    )
    text = econ_xml.read_text()
    # db entry and other-mod entry still active
    assert '<ce folder="db">' in text
    assert '<file name="other-mod-types.xml"' in text
    assert "DISABLED @Other-Mod" not in text


def test_disable_noop_when_no_match(server_dir, econ_xml):
    from app.xml.xml_economy import disable_mod_economy_entries

    count = disable_mod_economy_entries(
        server_dir, "dayzOffline.chernarusplus", "@Nonexistent",
    )
    assert count == 0
    assert econ_xml.read_text() == SAMPLE_ECONCORE


def test_disable_noop_when_no_file(server_dir):
    from app.xml.xml_economy import disable_mod_economy_entries

    count = disable_mod_economy_entries(
        server_dir, "dayzOffline.chernarusplus", "@Cool-Mod",
    )
    assert count == 0


# -- enable --


def test_enable_uncomments(server_dir, econ_xml):
    from app.xml.xml_economy import disable_mod_economy_entries, enable_mod_economy_entries

    disable_mod_economy_entries(
        server_dir, "dayzOffline.chernarusplus", "@Cool-Mod",
    )
    count = enable_mod_economy_entries(
        server_dir, "dayzOffline.chernarusplus", "@Cool-Mod",
    )
    assert count == 1
    text = econ_xml.read_text()
    assert "DISABLED" not in text
    assert '<file name="cool-mod-types.xml"' in text


def test_enable_noop_when_none_disabled(server_dir, econ_xml):
    from app.xml.xml_economy import enable_mod_economy_entries

    count = enable_mod_economy_entries(
        server_dir, "dayzOffline.chernarusplus", "@Cool-Mod",
    )
    assert count == 0


# -- remove disabled --


def test_remove_disabled_entries(server_dir, econ_xml):
    from app.xml.xml_economy import disable_mod_economy_entries, remove_disabled_economy_entries

    disable_mod_economy_entries(
        server_dir, "dayzOffline.chernarusplus", "@Cool-Mod",
    )
    count = remove_disabled_economy_entries(
        server_dir, "dayzOffline.chernarusplus", "@Cool-Mod",
    )
    assert count == 1
    text = econ_xml.read_text()
    assert "cool-mod" not in text
    assert "DISABLED" not in text
    # Other entries still present
    assert "other-mod-types.xml" in text


def test_remove_disabled_noop_when_active(server_dir, econ_xml):
    from app.xml.xml_economy import remove_disabled_economy_entries

    count = remove_disabled_economy_entries(
        server_dir, "dayzOffline.chernarusplus", "@Cool-Mod",
    )
    assert count == 0


def _parked(mod: str, name: str) -> str:
    return f'    <!-- DISABLED {mod}: <ce folder="custom"><file name="{name}" type="types"/></ce> -->\n'


def test_parked_entries_are_read_and_removed_per_mod(server_dir, tmp_mission_dir):
    """A mod name with a space or a colon reads whole, and removing one mod's
    wrappers takes the indentation before each and leaves the rest alone."""
    from app.xml.xml_economy import disabled_wrapper_mods, remove_disabled_economy_entries

    head = '<?xml version="1.0"?>\n<economycore>\n    <!-- my note -->\n'
    others = _parked("@Dabs Framework", "d.xml") + _parked("@user:x", "u.xml")
    xml = write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        head + _parked("@Bikes", "b1.xml") + others + _parked("@Bikes", "b2.xml") + "</economycore>\n",
    )

    assert disabled_wrapper_mods(tmp_mission_dir) == ["@Bikes", "@Dabs Framework", "@user:x"]
    assert remove_disabled_economy_entries(server_dir, "dayzOffline.chernarusplus", "@Bikes") == 2
    assert xml.read_text(encoding="utf-8") == head + others.rstrip("\n") + "\n</economycore>\n"


def test_a_parked_entry_ends_at_its_own_comment_close(server_dir, tmp_mission_dir):
    """A comment ends at its first `-->`. One that does not end in `</ce>` is
    not a wrapper, so nothing past it is read as parked or removed."""
    from app.xml.xml_economy import disabled_wrapper_mods, remove_disabled_economy_entries

    live = '<ce folder="db"><file name="types.xml" type="types"/></ce>'
    xml = write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?>\n<economycore>\n'
        '    <!-- DISABLED @A: <ce folder="custom"> -->\n'
        f"    {live}\n"
        "    <!-- </ce> -->\n"
        "</economycore>\n",
    )

    assert disabled_wrapper_mods(tmp_mission_dir) == []
    assert remove_disabled_economy_entries(server_dir, "dayzOffline.chernarusplus", "@A") == 0
    assert live in xml.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("call", "junk"),
    [
        ("mods", '\t<!-- DISABLED @Mod: <ce folder="custom">\n' * 600),
        ("remove", '\t<!-- DISABLED @Mod: <ce folder="custom">\n' * 4000),
        ("remove", " " * 100_000 + "x"),
    ],
    ids=["mods-openers-without-close", "remove-openers-without-close", "remove-long-whitespace"],
)
def test_parked_entries_are_read_in_linear_time(server_dir, tmp_mission_dir, call, junk):
    """The lazy regexes this scan replaced re-read the rest of the file from
    every opener with no close: these files took 2-9s, and the time grew with
    the square or the cube of the size. Every merge reads cfgeconomycore.xml
    this way to prune wrappers whose mod is enabled again."""
    import time

    from app.xml.xml_economy import disabled_wrapper_mods, remove_disabled_economy_entries

    real = _parked("@Mod", "m.xml")
    xml = write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?>\n<economycore>\n' + real + junk + "</economycore>\n",
    )

    started = time.perf_counter()
    if call == "mods":
        assert disabled_wrapper_mods(tmp_mission_dir) == ["@Mod"]
    else:
        assert remove_disabled_economy_entries(server_dir, "dayzOffline.chernarusplus", "@Mod") == 1
        assert real not in xml.read_text(encoding="utf-8")
    elapsed = time.perf_counter() - started

    assert elapsed < 1.0, f"took {elapsed:.2f}s"


def test_stale_wrappers_are_pruned_in_one_pass(server_dir, tmp_mission_dir):
    """Every stale mod's wrappers go in one read, one scan and one write. The
    prune used to take one of each per distinct name: 7s at 3200 names, 28s at
    6400, inside a merge step that cannot be cancelled."""
    import time

    from app.xml.xml_economy import _prune_stale_disabled_entries

    head = '<?xml version="1.0"?>\n<economycore>\n'
    kept = _parked("@Off", "off.xml")
    stale = "".join(_parked(f"@Mod{i}", f"m{i}.xml") for i in range(5000))
    xml = write_xml(tmp_mission_dir / "cfgeconomycore.xml", head + stale + kept + "</economycore>\n")
    lines: list[str] = []

    started = time.perf_counter()
    removed = _prune_stale_disabled_entries(
        server_dir, "dayzOffline.chernarusplus", {"@Off"}, lines.append,
    )
    elapsed = time.perf_counter() - started

    assert removed == 5000
    assert xml.read_text(encoding="utf-8") == head + kept + "</economycore>\n"
    assert len(lines) == 5000
    assert lines[0] == "@Mod0 is no longer disabled — removed 1 parked cfgeconomycore entry."
    assert elapsed < 1.0, f"took {elapsed:.2f}s"


# -- round trip --


def _custom_files(path: Path) -> list[tuple[str, str]]:
    """(name, type) of every live <ce folder="custom"> registration."""
    from app.xml.economycore_xml import parse_economycore_file

    return sorted(
        (f.name, f.type)
        for rc in parse_economycore_file(path).rootclasses
        if rc.folder == "custom"
        for f in rc.files
    )


def test_disable_enable_round_trip(server_dir, econ_xml):
    from app.xml.xml_economy import disable_mod_economy_entries, enable_mod_economy_entries

    before = _custom_files(econ_xml)
    disable_mod_economy_entries(
        server_dir, "dayzOffline.chernarusplus", "@Cool-Mod",
    )
    enable_mod_economy_entries(
        server_dir, "dayzOffline.chernarusplus", "@Cool-Mod",
    )
    # Registrations come back; the file itself is left normalized (one grouped
    # <ce folder="custom">), not byte-identical to the legacy per-file layout.
    assert _custom_files(econ_xml) == before
    text = econ_xml.read_text()
    assert "DISABLED" not in text
    assert text.count('<ce folder="custom">') == 1


def test_disable_groups_surviving_entries(server_dir, econ_xml):
    """A legacy file with one <ce> per registration is collapsed on write."""
    from app.xml.xml_economy import disable_mod_economy_entries

    assert econ_xml.read_text().count('<ce folder="custom">') == 2
    disable_mod_economy_entries(
        server_dir, "dayzOffline.chernarusplus", "@Cool-Mod",
    )
    text = econ_xml.read_text()
    # One live block (the DISABLED wrapper carries its own, inside a comment).
    assert text.split("<!--")[0].count('<ce folder="custom">') == 1
    assert _custom_files(econ_xml) == [("other-mod-types.xml", "types")]


# -- uninstall disabled mod (integration) --


def test_uninstall_removes_active_and_disabled(server_dir, econ_xml):
    from app.xml.xml_economy import (
        disable_mod_economy_entries,
        remove_disabled_economy_entries,
        remove_mod_economy_entries,
    )

    # Disable @Cool-Mod (comments it out)
    disable_mod_economy_entries(
        server_dir, "dayzOffline.chernarusplus", "@Cool-Mod",
    )
    # Uninstall removes active entries (none left) + disabled entries
    remove_mod_economy_entries(
        server_dir, "dayzOffline.chernarusplus", "@Cool-Mod",
    )
    remove_disabled_economy_entries(
        server_dir, "dayzOffline.chernarusplus", "@Cool-Mod",
    )
    text = econ_xml.read_text()
    assert "cool-mod" not in text
    assert "DISABLED" not in text
    assert "other-mod-types.xml" in text


# -- CE file configs --


def test_all_merge_configs_are_the_direct_merge_targets():
    """types/events/spawnabletypes go through cfgeconomycore; everything else a
    mod can ship has to be spliced straight into the mission file."""
    from app.xml.xml_configs import ALL_MERGE_CONFIGS

    assert set(ALL_MERGE_CONFIGS.keys()) == {
        "cfgeventgroups",
        "cfgeventspawns",
        "cfgrandompresets",
        "cfglimitsdefinition",
        "cfglimitsdefinitionuser",
    }


def test_ce_file_configs_has_three_types():
    from app.xml.xml_configs import CE_FILE_CONFIGS

    ce_types = [c["ce_type"] for c in CE_FILE_CONFIGS]
    assert ce_types == ["events", "spawnabletypes", "types"]


# -- wrap with root --


def test_wrap_with_root_events(tmp_path):
    from app.xml.xml_economy import _wrap_with_root_if_needed

    f = tmp_path / "events.xml"
    f.write_text('<event name="Test"/>', encoding="utf-8")
    _wrap_with_root_if_needed(f, "events")
    text = f.read_text()
    assert "<events>" in text
    assert "</events>" in text
    assert '<event name="Test"/>' in text


def test_wrap_with_root_spawnabletypes(tmp_path):
    from app.xml.xml_economy import _wrap_with_root_if_needed

    f = tmp_path / "spawnabletypes.xml"
    f.write_text('<type name="Test"/>', encoding="utf-8")
    _wrap_with_root_if_needed(f, "spawnabletypes")
    text = f.read_text()
    assert "<spawnabletypes>" in text
    assert "</spawnabletypes>" in text


def test_wrap_with_root_noop_if_present(tmp_path):
    from app.xml.xml_economy import _wrap_with_root_if_needed

    content = '<?xml version="1.0"?>\n<events><event name="X"/></events>'
    f = tmp_path / "events.xml"
    f.write_text(content, encoding="utf-8")
    _wrap_with_root_if_needed(f, "events")
    assert f.read_text() == content


# -- update cfgeconomycore with mixed types --


def test_update_cfgeconomycore_mixed_types(tmp_mission_dir):
    from app.xml.xml_economy import _update_cfgeconomycore

    write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?>\n<economycore></economycore>',
    )
    _update_cfgeconomycore(tmp_mission_dir, [
        ("mod-types.xml", "types", "@TestMod"),
        ("mod-events.xml", "events", "@TestMod"),
        ("mod-cfgspawnabletypes.xml", "spawnabletypes", "@OtherMod"),
    ])
    text = (tmp_mission_dir / "cfgeconomycore.xml").read_text()
    assert 'type="types"' in text
    assert 'type="events"' in text
    assert 'type="spawnabletypes"' in text


def test_update_cfgeconomycore_groups_files_in_one_ce(tmp_mission_dir):
    """All three files land as <file> children of ONE <ce folder="custom">."""
    from app.xml.xml_economy import _update_cfgeconomycore

    write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?>\n<economycore></economycore>',
    )
    _update_cfgeconomycore(tmp_mission_dir, [
        ("mod-types.xml", "types", "@TestMod"),
        ("mod-events.xml", "events", "@TestMod"),
        ("other-types.xml", "types", "@OtherMod"),
    ])
    path = tmp_mission_dir / "cfgeconomycore.xml"
    assert path.read_text().count('<ce folder="custom">') == 1
    assert _custom_files(path) == [
        ("mod-events.xml", "events"),
        ("mod-types.xml", "types"),
        ("other-types.xml", "types"),
    ]


def test_update_cfgeconomycore_drops_empty_ce(tmp_mission_dir):
    from app.xml.xml_economy import _update_cfgeconomycore

    write_xml(tmp_mission_dir / "cfgeconomycore.xml", SAMPLE_ECONCORE)
    _update_cfgeconomycore(tmp_mission_dir, [])
    text = (tmp_mission_dir / "cfgeconomycore.xml").read_text()
    assert '<ce folder="custom">' not in text
    assert '<ce folder="db">' in text  # other folders untouched


# -- disable/enable with events type --


def test_disable_events_type(server_dir, econ_xml_mixed):
    from app.xml.xml_economy import disable_mod_economy_entries

    count = disable_mod_economy_entries(
        server_dir, "dayzOffline.chernarusplus", "@Cool-Mod",
    )
    assert count == 3  # types + events + spawnabletypes
    text = econ_xml_mixed.read_text()
    assert text.count("DISABLED @Cool-Mod:") == 3


def test_enable_events_type_round_trip(server_dir, econ_xml_mixed):
    from app.xml.xml_economy import disable_mod_economy_entries, enable_mod_economy_entries

    before = _custom_files(econ_xml_mixed)
    disable_mod_economy_entries(
        server_dir, "dayzOffline.chernarusplus", "@Cool-Mod",
    )
    enable_mod_economy_entries(
        server_dir, "dayzOffline.chernarusplus", "@Cool-Mod",
    )
    assert _custom_files(econ_xml_mixed) == before
    assert "DISABLED" not in econ_xml_mixed.read_text()


# -- merge integration --


def test_merge_economy_core_types_events_spawnabletypes(tmp_server_dir, tmp_mission_dir):
    """Full integration: mod with types, events, and spawnabletypes XMLs."""
    # Create cfgeconomycore.xml
    write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?>\n<economycore></economycore>',
    )

    # Create a mod with three XML files
    mod_dir = tmp_server_dir / "@TestMod"
    mod_dir.mkdir()
    (mod_dir / "types.xml").write_text(
        '<?xml version="1.0"?>\n<types><type name="T1"/></types>',
        encoding="utf-8",
    )
    (mod_dir / "events.xml").write_text(
        '<?xml version="1.0"?>\n<events><event name="E1"/></events>',
        encoding="utf-8",
    )
    (mod_dir / "cfgspawnabletypes.xml").write_text(
        '<?xml version="1.0"?>\n<spawnabletypes><type name="S1"/></spawnabletypes>',
        encoding="utf-8",
    )

    from app.xml.xml_economy import merge_economy_core

    merge_economy_core(tmp_server_dir, "dayzOffline.chernarusplus", manual_dir=manual_dir())

    # Check custom/ folder has all three files
    custom = tmp_mission_dir / "custom"
    custom_files = sorted(f.name for f in custom.iterdir())
    assert "testmod-types.xml" in custom_files
    assert "testmod-events.xml" in custom_files
    assert "testmod-cfgspawnabletypes.xml" in custom_files

    # Check cfgeconomycore.xml has correct type= attributes
    text = (tmp_mission_dir / "cfgeconomycore.xml").read_text()
    assert 'name="testmod-types.xml" type="types"' in text
    assert 'name="testmod-events.xml" type="events"' in text
    assert 'name="testmod-cfgspawnabletypes.xml" type="spawnabletypes"' in text


def test_one_unreadable_mod_file_does_not_stop_the_ce_merge(tmp_server_dir, tmp_mission_dir):
    write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?>\n<economycore></economycore>',
    )
    good = tmp_server_dir / "@TestMod"
    good.mkdir()
    (good / "types.xml").write_text('<types><type name="T1"/></types>', encoding="utf-8")
    # Another mod ships a non-UTF-8 fragment and a directory named like an XML file.
    bad = tmp_server_dir / "@Other"
    bad.mkdir()
    (bad / "types.xml").write_bytes('<type name="Café"/>\n'.encode("cp1252"))
    (bad / "docs.xml").mkdir()
    lines: list[str] = []

    from app.xml.xml_economy import merge_economy_core

    linked = merge_economy_core(
        tmp_server_dir, "dayzOffline.chernarusplus", manual_dir=manual_dir(), log=lines.append
    )
    assert [r[0] for r in linked] == ["testmod-types.xml"]
    assert "Skipped @Other/types.xml: not a UTF-8 text file." in lines


def test_merge_does_not_mutate_source_files(tmp_server_dir, tmp_mission_dir):
    """Wrapping happens on the copy in custom/, not on the original mod file."""
    write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?>\n<economycore></economycore>',
    )

    mod_dir = tmp_server_dir / "@WrapMe"
    mod_dir.mkdir()
    # File without root tag — needs wrapping
    raw_content = '<event name="E1"><nominal>5</nominal></event>'
    (mod_dir / "events.xml").write_text(raw_content, encoding="utf-8")

    from app.xml.xml_economy import merge_economy_core

    merge_economy_core(tmp_server_dir, "dayzOffline.chernarusplus", manual_dir=manual_dir())

    # Source must be untouched
    assert (mod_dir / "events.xml").read_text() == raw_content
    # Copy in custom/ should be wrapped
    copy_text = (tmp_mission_dir / "custom" / "wrapme-events.xml").read_text()
    assert "<events>" in copy_text
    assert "</events>" in copy_text


# -- legacy backup cleanup --


def test_disable_is_atomic_on_crash(econ_xml, server_dir, monkeypatch):
    """If os.replace raises mid-write, the original cfgeconomycore.xml is unchanged."""
    import app.shared.json_utils as ju
    from app.xml.xml_economy import disable_mod_economy_entries

    original = econ_xml.read_text(encoding="utf-8")

    def boom(src, dst):
        raise OSError("simulated crash during rename")

    monkeypatch.setattr(ju.os, "replace", boom)
    with pytest.raises(OSError):
        disable_mod_economy_entries(server_dir, "dayzOffline.chernarusplus", "@Cool-Mod")

    assert econ_xml.read_text(encoding="utf-8") == original
    assert not list(econ_xml.parent.glob("*.tmp"))


def test_legacy_backup_restored_and_removed(tmp_server_dir, tmp_mission_dir):
    """Legacy .backup files from old direct-merge are restored then deleted."""
    write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?>\n<economycore></economycore>',
    )

    # Simulate old direct-merge backups
    events_xml = tmp_mission_dir / "db" / "events.xml"
    events_xml.write_text("<events><event name='injected'/></events>", encoding="utf-8")
    events_backup = events_xml.with_suffix(".xml.backup")
    events_backup.write_text("<events/>", encoding="utf-8")

    spawnable = tmp_mission_dir / "cfgspawnabletypes.xml"
    spawnable.write_text("<spawnabletypes><type name='injected'/></spawnabletypes>", encoding="utf-8")
    spawnable_backup = spawnable.with_suffix(".xml.backup")
    spawnable_backup.write_text("<spawnabletypes/>", encoding="utf-8")

    from app.xml.xml_economy import merge_economy_core

    merge_economy_core(tmp_server_dir, "dayzOffline.chernarusplus", manual_dir=manual_dir())

    # Backups should be gone
    assert not events_backup.exists()
    assert not spawnable_backup.exists()
    # Files should be restored to clean state
    assert events_xml.read_text() == "<events/>"
    assert spawnable.read_text() == "<spawnabletypes/>"


# -- content-based CE bucketing (mods name files inconsistently) --


def test_ce_bucket_follows_content_not_filename(tmp_server_dir, tmp_mission_dir):
    """A mod's events payload under an unrelated filename is still linked as
    events — the filename buckets alone dropped it into the types catch-all."""
    write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?>\n<economycore></economycore>',
    )
    mod_dir = tmp_server_dir / "@OddNames"
    write_xml(
        mod_dir / "MyMod_CE.xml",
        '<?xml version="1.0"?><events><event name="E1"><nominal>3</nominal></event></events>',
    )

    from app.xml.xml_economy import merge_economy_core

    merge_economy_core(tmp_server_dir, "dayzOffline.chernarusplus", manual_dir=manual_dir())

    text = (tmp_mission_dir / "cfgeconomycore.xml").read_text()
    assert 'name="oddnames-MyMod_CE.xml" type="events"' in text


def test_non_ce_files_are_not_linked(tmp_server_dir, tmp_mission_dir):
    """Direct-merge and mod-private XMLs must never get a <ce> entry —
    cfgeventspawns positions linked as types break the economy load.

    `mod_config.xml` carries **no XML declaration on purpose**, and that is the
    whole point of the fixture. Until 2026 Q3 an unrecognised root fell
    through to `CE_FILE_CONFIGS`' `filename_contains=""` catch-all and was
    registered as types; the only thing stopping it was that
    `_wrap_with_root_if_needed` put a *second* `<?xml?>` inside the wrapper,
    which failed validation and was silently unlinked. Drop the declaration and
    the old code linked it happily. Don't "tidy" the declaration back in.
    """
    write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?>\n<economycore></economycore>',
    )
    mod_dir = tmp_server_dir / "@Bicycles"
    write_xml(
        mod_dir / "cfgeventspawns_Chernarus.xml",
        '<?xml version="1.0"?><events><event name="E"><pos x="1" y="2" z="3" a="0"/></event></events>',
    )
    write_xml(mod_dir / "mod_settings.xml", '<?xml version="1.0"?><mysettings><foo/></mysettings>')
    write_xml(mod_dir / "mod_config.xml", "<mysettings><foo/></mysettings>")
    write_xml(
        mod_dir / "cfgrandompresets.xml",
        '<?xml version="1.0"?><randompresets><cargo name="c" chance="1"/></randompresets>',
    )

    from app.xml.xml_economy import merge_economy_core

    linked = merge_economy_core(
        tmp_server_dir, "dayzOffline.chernarusplus", manual_dir=manual_dir(),
    )

    assert linked == []
    assert list((tmp_mission_dir / "custom").iterdir()) == []


def test_rootless_fragment_is_still_linked_as_types(tmp_server_dir, tmp_mission_dir):
    """The filename catch-all's real job: a bare `<type>` fragment with no root.

    It carries a declaration, which `_wrap_with_root_if_needed` has to strip —
    leaving it in put a second one inside `<types>` and the copy was discarded.
    """
    write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?>\n<economycore></economycore>',
    )
    write_xml(
        tmp_server_dir / "@Bicycles" / "bike_types.xml",
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<type name="A"><nominal>1</nominal></type>\n<type name="B"/>',
    )

    from app.xml.xml_economy import merge_economy_core

    linked = merge_economy_core(
        tmp_server_dir, "dayzOffline.chernarusplus", manual_dir=manual_dir(),
    )

    assert [(n, t) for n, t, _m in linked] == [("bicycles-bike_types.xml", "types")]
    copy = (tmp_mission_dir / "custom" / "bicycles-bike_types.xml").read_text()
    assert copy.count("<?xml") == 1
    assert "<types>" in copy and '<type name="A">' in copy


def test_unrecognised_root_is_reported_not_linked(tmp_server_dir, tmp_mission_dir):
    write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?>\n<economycore></economycore>',
    )
    write_xml(tmp_server_dir / "@Bicycles" / "settings.xml", "<mysettings><foo/></mysettings>")

    from app.xml.xml_economy import merge_economy_core

    lines: list[str] = []
    assert merge_economy_core(
        tmp_server_dir, "dayzOffline.chernarusplus", log=lines.append, manual_dir=manual_dir(),
    ) == []
    assert any("aren't central-economy files" in line and "<mysettings>" in line for line in lines)


def test_broken_xml_is_skipped_with_a_log(tmp_server_dir, tmp_mission_dir):
    write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?>\n<economycore></economycore>',
    )
    write_xml(tmp_server_dir / "@Bicycles" / "broken.xml", "<types><type name=")

    from app.xml.xml_economy import merge_economy_core

    lines: list[str] = []
    assert merge_economy_core(
        tmp_server_dir, "dayzOffline.chernarusplus", log=lines.append, manual_dir=manual_dir(),
    ) == []
    assert any("not valid XML" in line for line in lines)


def test_globals_is_linked_but_messages_is_not(tmp_server_dir, tmp_mission_dir):
    """Decision of 2026 Q3: five of the six CE kinds auto-link off a mod dir.

    A mod ships example mission files as documentation and the merge rglobs the
    whole mod dir, so linking its messages.xml would broadcast the author's
    sample messages to real players.
    """
    write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?>\n<economycore></economycore>',
    )
    mod_dir = tmp_server_dir / "@Bicycles"
    write_xml(
        mod_dir / "globals.xml",
        '<?xml version="1.0"?><variables><var name="ZombieMaxCount" type="0" value="10"/></variables>',
    )
    write_xml(
        mod_dir / "economy.xml",
        '<?xml version="1.0"?><economy><dynamic init="1" load="0" respawn="1" save="0"/></economy>',
    )
    write_xml(
        mod_dir / "messages.xml",
        '<?xml version="1.0"?><messages><message><text>hi</text></message></messages>',
    )

    from app.xml.xml_economy import merge_economy_core

    lines: list[str] = []
    linked = merge_economy_core(
        tmp_server_dir, "dayzOffline.chernarusplus", log=lines.append, manual_dir=manual_dir(),
    )

    assert {(n, t) for n, t, _m in linked} == {
        ("bicycles-globals.xml", "globals"),
        ("bicycles-economy.xml", "economy"),
    }
    assert not (tmp_mission_dir / "custom" / "bicycles-messages.xml").exists()
    assert any("not linked automatically" in line for line in lines)
    # globals' root tag is <variables>, not its type= name.
    assert "<variables>" in (tmp_mission_dir / "custom" / "bicycles-globals.xml").read_text()


def test_custom_ce_block_keeps_its_position(tmp_server_dir, tmp_mission_dir):
    """DayZ applies the last definition of a classname, so an admin's own <ce>
    placed after custom must stay after it — appending the rebuilt block
    silently demoted their overrides below every mod's files on each merge."""
    write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?>\n<economycore>\n'
        '    <ce folder="custom"><file name="stale.xml" type="types"/></ce>\n'
        '    <ce folder="myoverrides"><file name="mine.xml" type="types"/></ce>\n'
        "</economycore>",
    )
    write_xml(
        tmp_server_dir / "@Bicycles" / "types.xml",
        '<?xml version="1.0"?><types><type name="A"/></types>',
    )

    from app.xml.xml_economy import merge_economy_core

    merge_economy_core(tmp_server_dir, "dayzOffline.chernarusplus", manual_dir=manual_dir())

    text = (tmp_mission_dir / "cfgeconomycore.xml").read_text()
    assert 'folder="myoverrides"' in text  # other folders untouched
    assert text.index('folder="custom"') < text.index('folder="myoverrides"')


def test_custom_ce_block_is_appended_when_there_was_none(tmp_server_dir, tmp_mission_dir):
    write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?>\n<economycore>\n'
        '    <ce folder="db"><file name="types.xml" type="types"/></ce>\n'
        "</economycore>",
    )
    write_xml(
        tmp_server_dir / "@Bicycles" / "types.xml",
        '<?xml version="1.0"?><types><type name="A"/></types>',
    )

    from app.xml.xml_economy import merge_economy_core

    merge_economy_core(tmp_server_dir, "dayzOffline.chernarusplus", manual_dir=manual_dir())

    text = (tmp_mission_dir / "cfgeconomycore.xml").read_text()
    assert text.index('folder="db"') < text.index('folder="custom"')


def test_merge_survives_a_missing_economycore_root(tmp_server_dir, tmp_mission_dir):
    """A file that parses but isn't a cfgeconomycore used to IndexError and take
    the whole merge job down."""
    path = write_xml(
        tmp_mission_dir / "cfgeconomycore.xml", '<?xml version="1.0"?>\n<nonsense/>',
    )
    write_xml(
        tmp_server_dir / "@Bicycles" / "types.xml",
        '<?xml version="1.0"?><types><type name="A"/></types>',
    )

    from app.xml.xml_economy import merge_economy_core

    lines: list[str] = []
    merge_economy_core(
        tmp_server_dir, "dayzOffline.chernarusplus", log=lines.append, manual_dir=manual_dir(),
    )

    assert any("<economycore> root" in line for line in lines)
    assert path.read_text() == '<?xml version="1.0"?>\n<nonsense/>'


def test_other_map_variant_is_not_linked(tmp_server_dir, tmp_mission_dir):
    write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?>\n<economycore></economycore>',
    )
    mod_dir = tmp_server_dir / "@Bicycles"
    write_xml(mod_dir / "types_Chernarus.xml", '<?xml version="1.0"?><types><type name="A"/></types>')
    write_xml(mod_dir / "types_Livonia.xml", '<?xml version="1.0"?><types><type name="B"/></types>')

    from app.xml.xml_economy import merge_economy_core

    names = {n for n, _t, _m in merge_economy_core(
        tmp_server_dir, "dayzOffline.chernarusplus", manual_dir=manual_dir(),
    )}

    assert names == {"bicycles-types_Chernarus.xml"}


# -- the admin's own cfgeconomycore content survives a merge (2026 Q3) -----
#
# merge_economy_core used to restore cfgeconomycore.xml from its .backup before
# rewriting the custom block, which deleted every rootclass and <defaults> edit
# the admin had made since. `_rebuild_custom_ce` already replaces exactly the
# generated block, so the restore only ever destroyed information.


@pytest.fixture()
def merge_mod(tmp_server_dir: Path) -> Path:
    write_xml(
        tmp_server_dir / "@Cool-Mod" / "types.xml",
        '<?xml version="1.0"?><types><type name="T1"/></types>',
    )
    return tmp_server_dir / "@Cool-Mod"


def test_merge_keeps_user_rootclasses_and_defaults(tmp_server_dir, tmp_mission_dir, merge_mod):
    from app.xml.xml_economy import merge_economy_core

    write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?><economycore><defaults/></economycore>',
    )
    merge_economy_core(
        tmp_server_dir, "dayzOffline.chernarusplus", disabled_mods=set(), manual_dir=manual_dir(),
    )

    # What the admin adds afterwards: an override folder of their own, a
    # <defaults> edit, and a stale registration for a file that is now gone.
    path = tmp_mission_dir / "cfgeconomycore.xml"
    path.write_text(
        path.read_text(encoding="utf-8")
        # minidom re-serializes the empty element as "<defaults />".
        .replace("<defaults />", '<defaults><default name="dayzsurvivalmode" value="1"/></defaults>')
        .replace(
            "</economycore>",
            '<ce folder="myoverrides"><file name="mine.xml" type="types"/></ce></economycore>',
        ),
        encoding="utf-8",
    )
    (tmp_mission_dir / "custom" / "cool-mod-types.xml").unlink()

    merge_economy_core(
        tmp_server_dir, "dayzOffline.chernarusplus", disabled_mods=set(), manual_dir=manual_dir(),
    )

    text = path.read_text(encoding="utf-8")
    assert 'folder="myoverrides"' in text
    assert 'name="dayzsurvivalmode"' in text
    # The generated half is still rebuilt from the mods, not carried forward.
    assert text.count('name="cool-mod-types.xml"') == 1
    assert (tmp_mission_dir / "custom" / "cool-mod-types.xml").is_file()


def _disabled_after_merge(server_dir, mission_dir, mod, disabled_mods) -> str:
    from app.xml.xml_economy import disable_mod_economy_entries, merge_economy_core

    merge_economy_core(
        server_dir, "dayzOffline.chernarusplus", disabled_mods=set(), manual_dir=manual_dir(),
    )
    assert disable_mod_economy_entries(server_dir, "dayzOffline.chernarusplus", mod)
    merge_economy_core(
        server_dir, "dayzOffline.chernarusplus", disabled_mods=disabled_mods,
        manual_dir=manual_dir(),
    )
    return (mission_dir / "cfgeconomycore.xml").read_text(encoding="utf-8")


def test_disabled_wrapper_survives_while_its_mod_is_disabled(
    tmp_server_dir, tmp_mission_dir, merge_mod
):
    write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?><economycore><defaults/></economycore>',
    )

    text = _disabled_after_merge(
        tmp_server_dir, tmp_mission_dir, "@Cool-Mod", {"@Cool-Mod"},
    )

    assert "DISABLED @Cool-Mod:" in text


def test_stale_disabled_wrapper_is_pruned(tmp_server_dir, tmp_mission_dir, merge_mod):
    """A wrapper for a mod that is no longer disabled is dead weight — nothing
    will ever unwrap it, and a later disable would park a second copy beside
    it. The .backup restore used to clear these as a side effect."""
    write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?><economycore><defaults/></economycore>',
    )
    lines: list[str] = []

    from app.xml.xml_economy import disable_mod_economy_entries, merge_economy_core

    merge_economy_core(
        tmp_server_dir, "dayzOffline.chernarusplus", disabled_mods=set(), manual_dir=manual_dir(),
    )
    disable_mod_economy_entries(tmp_server_dir, "dayzOffline.chernarusplus", "@Cool-Mod")
    merge_economy_core(
        tmp_server_dir,
        "dayzOffline.chernarusplus",
        disabled_mods=set(),
        log=lines.append,
        manual_dir=manual_dir(),
    )

    text = (tmp_mission_dir / "cfgeconomycore.xml").read_text(encoding="utf-8")
    assert "DISABLED" not in text
    assert any("no longer disabled" in line for line in lines)
    # And the mod is linked normally again, since it isn't disabled.
    assert 'name="cool-mod-types.xml"' in text


def test_pruning_is_skipped_when_the_caller_has_no_disabled_set(
    tmp_server_dir, tmp_mission_dir, merge_mod
):
    """`disabled_mods=None` means "I don't know which mods are off" — guessing
    there would throw away parked entries a caller simply couldn't describe."""
    write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?><economycore><defaults/></economycore>',
    )

    text = _disabled_after_merge(tmp_server_dir, tmp_mission_dir, "@Cool-Mod", None)

    assert "DISABLED @Cool-Mod:" in text


def test_a_comment_with_dashes_is_mended_in_the_copy_and_linked(tmp_server_dir, tmp_mission_dir):
    """User test in 2026 Q4: @FOX WEAPONS 2.0 ships banner comments with "--"
    inside, which XML does not allow, and 9 of its 10 types files were skipped
    as "not valid XML". The copy in custom/ is mended and linked; the mod's own
    file stays as it was. A file broken some other way is still skipped, and
    the line says where."""
    write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?>\n<economycore></economycore>',
    )
    mod = tmp_server_dir / "@FOX"
    mod.mkdir()
    fox = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<types>\n'
        "    <!-- ------------IRON SIGHTS------------ -->\n"
        '    <type name="FOX_VORTEX"><nominal>2</nominal></type>\n'
        "    <!-- trailing dash --->\n"
        "</types>\n"
    )
    (mod / "types_optics.xml").write_text(fox, encoding="utf-8")
    (mod / "types_broken.xml").write_text("<types><type name='A'></types>", encoding="utf-8")
    lines: list[str] = []

    from app.xml.xml_economy import merge_economy_core

    linked = merge_economy_core(
        tmp_server_dir, "dayzOffline.chernarusplus", manual_dir=manual_dir(), log=lines.append
    )
    assert [r[0] for r in linked] == ["fox-types_optics.xml"]
    copy = (tmp_mission_dir / "custom" / "fox-types_optics.xml").read_text(encoding="utf-8")
    assert "<!-- - - - -" in copy and 'name="FOX_VORTEX"' in copy and "--->" not in copy
    assert (mod / "types_optics.xml").read_text(encoding="utf-8") == fox
    assert any(
        line.startswith("Linked @FOX/types_optics.xml as types") and "Rewrote 2 comment(s)" in line
        for line in lines
    )
    assert any(line.startswith("Skipped @FOX/types_broken.xml: not valid XML at line 1 (") for line in lines)


def test_mend_comment_dashes_changes_only_comments():
    from app.xml.xml_text import check_xml_text, mend_comment_dashes

    text = '<a x="--"><!-- ok --><!-- a--b --><!-- c- --></a>'
    mended, changed = mend_comment_dashes(text)
    assert changed == 1
    assert mended == '<a x="--"><!-- ok --><!-- a- -b --><!-- c- --></a>'
    assert check_xml_text(mended) is None


def test_a_crlf_economycore_keeps_its_line_endings(tmp_path):
    from app.xml.xml_economy import register_custom_file

    path = tmp_path / "cfgeconomycore.xml"
    path.write_bytes(
        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        b"<economycore>\r\n\t<classes>\r\n\t\t<rootclass name=\"DefaultWeapon\" />\r\n\t</classes>\r\n</economycore>\r\n"
    )
    assert register_custom_file(tmp_path, "some-mod-types.xml", "types")
    written = path.read_bytes()
    assert b"some-mod-types.xml" in written
    assert b"\n" not in written.replace(b"\r\n", b"")
