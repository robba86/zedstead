"""Manual XML files (app/xml/xml_manual.py).

What they are for since 2026 Q3, when merges stopped restoring direct-merge
targets from their .backup: content that has to outlive the *mission folder* (a
map switch, a server reinstall, and the custom/ wipe every merge does), and
content that has to be applied after every mod so it wins DayZ's
last-definition rule.
"""

from __future__ import annotations

import pytest

from app.xml import xml_manual
from app.xml.xml_economy import merge_economy_core
from app.xml.xml_merge import merge_all_direct_configs
from tests.conftest import manual_dir, write_xml

MISSION = "dayzOffline.chernarusplus"

EVENTSPAWNS = (
    '<?xml version="1.0"?><eventposdef><event name="StaticHeliCrash">'
    '<pos x="1000" z="2000" a="0"/></event></eventposdef>'
)
TYPES = '<?xml version="1.0"?><types><type name="MyGun"><nominal>5</nominal></type></types>'


@pytest.fixture()
def mission(sample_config, tmp_mission_dir):
    """A mission with the files the merges write into."""
    write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?><economycore><classes/><defaults/></economycore>',
    )
    write_xml(
        tmp_mission_dir / "cfgeventspawns.xml",
        '<?xml version="1.0"?><eventposdef><event name="Vanilla">'
        '<pos x="1" z="2" a="0"/></event></eventposdef>',
    )
    # Not OPTIONAL in ALL_MERGE_CONFIGS: without it every merge reports a
    # failure of its own and the assertions below couldn't see the links'.
    write_xml(tmp_mission_dir / "cfgeventgroups.xml", '<?xml version="1.0"?><eventgroupdef/>')
    return tmp_mission_dir


def _merge_all(config):
    merge_economy_core(config.server_dir_path, MISSION, manual_dir=manual_dir())
    return merge_all_direct_configs(config.server_dir_path, MISSION, manual_dir=manual_dir())


# --- storage ----------------------------------------------------------------


def test_link_ids_are_validated(sample_config):
    for bad in ("", "../escape", "a/b", "Upper", "x" * 65, ".hidden"):
        assert not xml_manual.is_safe_file_id(bad), bad
    assert xml_manual.is_safe_file_id("livonia-heli_spawns")

    with pytest.raises(xml_manual.ManualFileError):
        xml_manual.file_content_path("../escape", manual_dir=manual_dir())


def test_the_store_is_the_directory_passed_in(sample_config, mission, tmp_path):
    """plans/44 step 3b: each instance hands in its own custom_xml_dir, so one
    instance's files and switches never reach another's merge."""
    ours, theirs = tmp_path / "ours", tmp_path / "theirs"
    xml_manual.create_file("heli", "cfgeventspawns", EVENTSPAWNS, manual_dir=theirs)
    xml_manual.set_excluded("bikes-types.xml", True, manual_dir=theirs)

    assert xml_manual.load_files(manual_dir=ours) == []
    assert xml_manual.load_exclusions(manual_dir=ours) == set()
    assert not ours.exists()

    merge_all_direct_configs(sample_config.server_dir_path, MISSION, manual_dir=ours)
    assert "@user:heli" not in (mission / "cfgeventspawns.xml").read_text()

    merge_all_direct_configs(sample_config.server_dir_path, MISSION, manual_dir=theirs)
    assert "@user:heli" in (mission / "cfgeventspawns.xml").read_text()


def test_create_rejects_bad_kind_terrain_and_xml(sample_config):
    with pytest.raises(xml_manual.ManualFileError):
        xml_manual.create_file("a", "nonsense", TYPES, manual_dir=manual_dir())
    with pytest.raises(xml_manual.ManualFileError):
        xml_manual.create_file("b", "types", "<types>", manual_dir=manual_dir())
    with pytest.raises(xml_manual.ManualFileError):
        xml_manual.create_file("c", "types", TYPES, terrain="atlantis", manual_dir=manual_dir())


def test_create_accepts_a_bare_fragment(sample_config):
    # Mods ship rootless fragments; the CE copy wraps them, so links may too.
    link = xml_manual.create_file(
        "frag", "types", '<type name="A"/><type name="B"/>', manual_dir=manual_dir(),
    )
    assert link["enabled"] is True
    assert xml_manual.file_content_path("frag", manual_dir=manual_dir()).is_file()


def test_create_is_unique_and_update_round_trips(sample_config):
    xml_manual.create_file("dup", "types", TYPES, manual_dir=manual_dir())
    with pytest.raises(xml_manual.ManualFileError):
        xml_manual.create_file("dup", "types", TYPES, manual_dir=manual_dir())

    xml_manual.update_file(
        "dup", content=TYPES.replace("MyGun", "OtherGun"), note="hi", manual_dir=manual_dir(),
    )
    assert "OtherGun" in xml_manual.file_content_path("dup", manual_dir=manual_dir()).read_text()
    assert xml_manual.get_file("dup", manual_dir=manual_dir())["note"] == "hi"


# --- applying ---------------------------------------------------------------


def test_ce_link_is_copied_and_registered(sample_config, mission):
    xml_manual.create_file("my-guns", "types", TYPES, manual_dir=manual_dir())
    _merge_all(sample_config)

    custom = mission / "custom" / "user-my-guns.xml"
    assert "MyGun" in custom.read_text()
    core = (mission / "cfgeconomycore.xml").read_text()
    assert 'name="user-my-guns.xml"' in core and 'type="types"' in core


def test_globals_manual_file_is_wrapped_in_variables(sample_config, mission):
    """The one CE kind whose `type=` is not its root tag: globals.xml's root is
    `<variables>`. `root_tag_for` returned the kind string for every CE kind,
    which would have written an unloadable `<globals>` file."""
    xml_manual.create_file(
        "zeds",
        "globals",
        '<var name="ZombieMaxCount" type="0" value="42"/>',
        manual_dir=manual_dir(),
    )
    _merge_all(sample_config)

    custom = (mission / "custom" / "user-zeds.xml").read_text()
    assert "<variables>" in custom and "<globals>" not in custom
    core = (mission / "cfgeconomycore.xml").read_text()
    assert 'name="user-zeds.xml"' in core and 'type="globals"' in core


def test_messages_manual_file_is_applied(sample_config, mission):
    """A mod's messages.xml is never auto-linked, but the admin's own is — that
    opt-in is the whole reason `AUTO_LINK_KINDS` is smaller than `CE_KINDS`."""
    xml_manual.create_file(
        "motd", "messages", "<message><text>Hello</text></message>", manual_dir=manual_dir(),
    )
    _merge_all(sample_config)

    assert "Hello" in (mission / "custom" / "user-motd.xml").read_text()
    assert 'type="messages"' in (mission / "cfgeconomycore.xml").read_text()


def test_direct_link_is_merged_with_its_own_markers(sample_config, mission):
    xml_manual.create_file("heli", "cfgeventspawns", EVENTSPAWNS, manual_dir=manual_dir())
    assert _merge_all(sample_config) == []

    text = (mission / "cfgeventspawns.xml").read_text()
    assert "START MOD: @user:heli" in text
    assert "StaticHeliCrash" in text
    assert "Vanilla" in text  # the mission's own content is untouched


def test_links_survive_repeated_merges_without_duplicating(sample_config, mission):
    """The whole point: a merge restores from .backup and wipes custom/."""
    xml_manual.create_file("heli", "cfgeventspawns", EVENTSPAWNS, manual_dir=manual_dir())
    xml_manual.create_file("my-guns", "types", TYPES, manual_dir=manual_dir())

    for _ in range(3):
        _merge_all(sample_config)

    text = (mission / "cfgeventspawns.xml").read_text()
    assert text.count("START MOD: @user:heli") == 1
    assert text.count("StaticHeliCrash") == 1
    assert (mission / "custom" / "user-my-guns.xml").is_file()
    assert (mission / "cfgeconomycore.xml").read_text().count("user-my-guns.xml") == 1


def test_disabled_link_is_not_applied(sample_config, mission):
    xml_manual.create_file("heli", "cfgeventspawns", EVENTSPAWNS, manual_dir=manual_dir())
    _merge_all(sample_config)
    xml_manual.update_file("heli", enabled=False, manual_dir=manual_dir())
    _merge_all(sample_config)

    assert "@user:heli" not in (mission / "cfgeventspawns.xml").read_text()


def test_turning_a_file_off_removes_it_on_apply(sample_config, mission):
    """Apply alone (the page's save/toggle path) must drop an off file's block.

    It used to strip only the active files, so an Off file stayed in the
    mission — and a free-form merge's block survived even a full merge.
    """
    write_xml(
        mission / "db" / "globals.xml",
        '<?xml version="1.0"?><variables><var name="A" type="0" value="1"/></variables>',
    )
    xml_manual.create_file("heli", "cfgeventspawns", EVENTSPAWNS, manual_dir=manual_dir())
    xml_manual.create_file(
        "vars",
        xml_manual.MERGE_KIND,
        '<variables><var name="B" type="0" value="2"/></variables>',
        target="db/globals.xml",
        manual_dir=manual_dir(),
    )
    _merge_all(sample_config)
    assert "@user:heli" in (mission / "cfgeventspawns.xml").read_text()
    assert "@user:vars" in (mission / "db" / "globals.xml").read_text()

    xml_manual.update_file("heli", enabled=False, manual_dir=manual_dir())
    xml_manual.update_file("vars", enabled=False, manual_dir=manual_dir())
    xml_manual.apply_direct_files(sample_config.server_dir_path, MISSION, manual_dir=manual_dir())

    assert "@user:heli" not in (mission / "cfgeventspawns.xml").read_text()
    globals_text = (mission / "db" / "globals.xml").read_text()
    assert "@user:vars" not in globals_text
    assert 'name="A"' in globals_text


def _versions(path):
    """What Analysis > Change history lists for *path*, newest first."""
    from app.shared import config_versions

    return config_versions.load_index(config_versions.store_for(path)).get_versions(
        path.name, source_path=str(path)
    )


def _apply(config):
    return xml_manual.apply_direct_files(config.server_dir_path, MISSION, manual_dir=manual_dir())


# Laid out the way Bohemia ships it. The fixture's one-line file is not: its
# first block lands on a line of its own, and every later apply moves the START
# marker to column 0 (`xml_utils._layout_before`), one write once.
EVENTSPAWNS_MISSION = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    "<eventposdef>\n"
    '\t<event name="Vanilla">\n'
    '\t\t<pos x="1" z="2" a="0" />\n'
    "\t</event>\n"
    "</eventposdef>\n"
)


def test_an_apply_keeps_one_version_and_a_no_op_apply_none(sample_config, mission):
    """Save and apply, Re-apply all.

    An apply stripped the file's block and saved, then spliced it back and
    saved again: two writes, and a version of the stripped copy, a file nobody
    had. Twenty-five applies pushed every real edit out of the 50 versions.
    """
    import hashlib

    target = write_xml(mission / "cfgeventspawns.xml", EVENTSPAWNS_MISSION)
    xml_manual.create_file("heli", "cfgeventspawns", EVENTSPAWNS, manual_dir=manual_dir())
    before = len(_versions(target))

    assert _apply(sample_config) == []
    assert len(_versions(target)) == before + 1
    applied = target.read_bytes()
    inode = target.stat().st_ino

    # Nothing changed: the file is not written (a write is a rename, a new
    # inode) and keeps no version.
    for _ in range(2):
        assert _apply(sample_config) == []
        assert target.stat().st_ino == inode
        assert target.read_bytes() == applied
        assert len(_versions(target)) == before + 1

    # A changed file: one version, and it is the file as it was, block and all.
    xml_manual.update_file(
        "heli", content=EVENTSPAWNS.replace('x="1000"', 'x="1001"'), manual_dir=manual_dir(),
    )
    assert _apply(sample_config) == []
    versions = _versions(target)
    assert len(versions) == before + 2
    assert versions[0].sha256 == hashlib.sha256(applied).hexdigest()
    assert 'x="1001"' in target.read_text()
    assert target.read_text().count("START MOD: @user:heli") == 1


def test_a_no_op_apply_of_two_files_in_one_mission_file_writes_nothing(sample_config, mission):
    """Two blocks in one file come back in the order they were in, so the file
    ends where it started. Applied one at a time, the first one moved to the
    end and the second one moved it back: two writes and a version."""
    xml_manual.create_file("heli", "cfgeventspawns", EVENTSPAWNS, manual_dir=manual_dir())
    xml_manual.create_file(
        "heli-two", "cfgeventspawns", EVENTSPAWNS.replace("StaticHeliCrash", "Other"),
        manual_dir=manual_dir(),
    )
    target = write_xml(mission / "cfgeventspawns.xml", EVENTSPAWNS_MISSION)
    assert _apply(sample_config) == []
    text = target.read_text()
    assert text.index("START MOD: @user:heli ") < text.index("START MOD: @user:heli-two ")
    inode, count = target.stat().st_ino, len(_versions(target))

    assert _apply(sample_config) == []
    assert target.stat().st_ino == inode
    assert len(_versions(target)) == count


def test_an_apply_takes_a_block_out_of_a_file_it_no_longer_targets(sample_config, mission):
    """A free-form merge moved to another file: its old block goes, in the
    same apply that writes the new one."""
    for name in ("globals.xml", "other.xml"):
        write_xml(mission / "db" / name, '<?xml version="1.0"?><variables/>')
    xml_manual.create_file(
        "vars", xml_manual.MERGE_KIND, '<variables><var name="B" type="0" value="2"/></variables>',
        target="db/globals.xml", manual_dir=manual_dir(),
    )
    assert _apply(sample_config) == []
    assert "@user:vars" in (mission / "db" / "globals.xml").read_text()

    xml_manual.update_file("vars", target="db/other.xml", manual_dir=manual_dir())
    assert _apply(sample_config) == []
    assert "@user:vars" not in (mission / "db" / "globals.xml").read_text()
    assert (mission / "db" / "other.xml").read_text().count("START MOD: @user:vars") == 1


def test_terrain_scope_skips_other_maps(sample_config, mission):
    # The user's scenario: positions authored for Livonia, mission is Chernarus.
    xml_manual.create_file(
        "livonia-heli", "cfgeventspawns", EVENTSPAWNS, terrain="enoch", manual_dir=manual_dir(),
    )
    xml_manual.create_file(
        "cherno-heli", "cfgeventspawns", EVENTSPAWNS, terrain="chernarus", manual_dir=manual_dir(),
    )
    _merge_all(sample_config)

    text = (mission / "cfgeventspawns.xml").read_text()
    assert "@user:cherno-heli" in text
    assert "@user:livonia-heli" not in text


def test_link_applies_after_a_mods_block(sample_config, mission):
    """Last definition wins in DayZ, so a link must land after the mods."""
    write_xml(
        sample_config.server_dir_path / "@Heli" / "cfgeventspawns.xml",
        '<?xml version="1.0"?><eventposdef><event name="ModEvent">'
        '<pos x="5" z="5" a="0"/></event></eventposdef>',
    )
    xml_manual.create_file("heli", "cfgeventspawns", EVENTSPAWNS, manual_dir=manual_dir())
    _merge_all(sample_config)

    text = (mission / "cfgeventspawns.xml").read_text()
    assert text.index("START MOD: @Heli") < text.index("START MOD: @user:heli")


def test_delete_removes_the_applied_artifacts(sample_config, mission):
    xml_manual.create_file("heli", "cfgeventspawns", EVENTSPAWNS, manual_dir=manual_dir())
    xml_manual.create_file("my-guns", "types", TYPES, manual_dir=manual_dir())
    _merge_all(sample_config)

    xml_manual.delete_file(
        "heli", server_dir=sample_config.server_dir_path, mission=MISSION, manual_dir=manual_dir(),
    )
    xml_manual.delete_file(
        "my-guns",
        server_dir=sample_config.server_dir_path,
        mission=MISSION,
        manual_dir=manual_dir(),
    )

    assert "@user:heli" not in (mission / "cfgeventspawns.xml").read_text()
    assert not (mission / "custom" / "user-my-guns.xml").exists()
    assert "user-my-guns.xml" not in (mission / "cfgeconomycore.xml").read_text()
    assert xml_manual.load_files(manual_dir=manual_dir()) == []
    assert not xml_manual.file_content_path("heli", manual_dir=manual_dir()).exists()


def test_missing_optional_target_is_not_a_failure(sample_config, mission):
    # cfgrandompresets is OPTIONAL — a mission without it isn't a merge error.
    xml_manual.create_file(
        "presets", "cfgrandompresets", '<?xml version="1.0"?><randompresets/>',
        manual_dir=manual_dir(),
    )
    assert _merge_all(sample_config) == []


def test_per_mod_reconfigure_reapplies_only_its_own_links(sample_config, mission):
    from app.modmgr.mods import reconfigure_mod_xml

    write_xml(
        sample_config.server_dir_path / "@Heli" / "cfgeventspawns.xml",
        '<?xml version="1.0"?><eventposdef><event name="ModEvent">'
        '<pos x="5" z="5" a="0"/></event></eventposdef>',
    )
    xml_manual.create_file(
        "owned", "cfgeventspawns", EVENTSPAWNS, mod="@Heli", manual_dir=manual_dir(),
    )
    xml_manual.create_file("free", "cfgeventspawns", EVENTSPAWNS, manual_dir=manual_dir())
    _merge_all(sample_config)

    reconfigure_mod_xml(sample_config, "@Heli")

    text = (mission / "cfgeventspawns.xml").read_text()
    assert text.count("START MOD: @user:owned") == 1
    assert text.count("START MOD: @user:free") == 1
    # The owning mod's block was rebuilt, so its link has to end up after it.
    assert text.index("START MOD: @Heli") < text.index("START MOD: @user:owned")


# --- capturing map-editor positions ----------------------------------------


def test_capture_event_positions_makes_them_permanent(sample_config, mission):
    from app.xml.xml_manual import capture_event_positions

    # What the map editor leaves behind: positions in the mission file, owned by
    # nobody, that the next merge's .backup restore would delete.
    write_xml(
        mission / "cfgeventspawns.xml",
        '<?xml version="1.0"?><eventposdef><event name="StaticHeliCrash">'
        '<pos x="7" z="8" a="0"/><pos x="9" z="10" a="-1"/>'
        "</event></eventposdef>",
    )

    link = capture_event_positions(
        sample_config.server_dir_path, MISSION, "StaticHeliCrash",
        manual_dir=manual_dir(),
    )
    assert link["kind"] == "cfgeventspawns"
    assert link["terrain"] == "chernarusplus"

    _merge_all(sample_config)
    text = (mission / "cfgeventspawns.xml").read_text()
    assert f"START MOD: @user:{link['id']}" in text
    assert 'x="7"' in text and 'x="9"' in text


def test_capture_moves_the_positions_out_of_the_mission_file(sample_config, mission):
    """A move, not a copy. While a merge restored cfgeventspawns.xml from its
    .backup, leaving the originals behind was harmless — the next merge deleted
    them. It no longer does, so a copy would spawn the event at each captured
    point twice: once from the mission's own content, once from the @user block."""
    from app.xml.xml_manual import capture_event_positions

    write_xml(
        mission / "cfgeventspawns.xml",
        '<?xml version="1.0"?><eventposdef>'
        '<event name="StaticHeliCrash"><pos x="7" z="8" a="0"/><pos x="9" z="10" a="-1"/></event>'
        '<event name="Keep"><pos x="1" z="1" a="0"/></event>'
        "</eventposdef>",
    )

    link = capture_event_positions(
        sample_config.server_dir_path, MISSION, "StaticHeliCrash",
        manual_dir=manual_dir(),
    )

    # Applied straight away, so the positions never leave the map.
    path = mission / "cfgeventspawns.xml"
    text = path.read_text()
    assert text.count(f"START MOD: @user:{link['id']}") == 1
    assert text.count('x="7"') == 1 and text.count('x="9"') == 1
    # The emptied <event> is gone rather than left as a second, position-less
    # row with the same name — the map editor resolves a name to its first match.
    assert text.count('name="StaticHeliCrash"') == 1
    assert 'name="Keep"' in text

    _merge_all(sample_config)

    text = path.read_text()
    assert text.count('x="7"') == 1 and text.count('x="9"') == 1
    assert text.count(f"START MOD: @user:{link['id']}") == 1
    # Everything the capture took now lives inside the block, nothing outside it.
    outside = text.split(f"<!-- START MOD: @user:{link['id']} -->")[0]
    assert 'x="7"' not in outside and 'x="9"' not in outside


def test_capture_refuses_an_event_that_only_a_mod_defines(sample_config, mission):
    """The merge appends a mod's whole <event> at the ROOT inside markers, so an
    event that exists only because a mod merged it has no markers inside it and
    read as entirely hand-placed. Capturing it moved the mod's positions into a
    manual file; the next merge re-splices the mod's block too, and every
    position spawns twice."""
    from app.xml.xml_manual import ManualFileError, capture_event_positions

    write_xml(
        sample_config.server_dir_path / "@Heli" / "cfgeventspawns.xml",
        '<?xml version="1.0"?><eventposdef><event name="ModHeli">'
        '<pos x="5" z="5" a="0"/></event></eventposdef>',
    )
    _merge_all(sample_config)
    before = (mission / "cfgeventspawns.xml").read_text()

    with pytest.raises(ManualFileError, match="comes from a mod"):
        capture_event_positions(
            sample_config.server_dir_path, MISSION, "ModHeli", manual_dir=manual_dir(),
        )

    assert (mission / "cfgeventspawns.xml").read_text() == before


def test_capture_takes_the_missions_own_entry_when_a_mod_shares_the_name(
    sample_config, mission
):
    """Same name, two <event> elements: the mission's own and the mod's merged
    block. Only the first is the admin's."""
    from app.xml.xml_manual import capture_event_positions

    write_xml(
        mission / "cfgeventspawns.xml",
        '<?xml version="1.0"?><eventposdef><event name="Shared">'
        '<pos x="7" z="8" a="0"/></event></eventposdef>',
    )
    write_xml(
        sample_config.server_dir_path / "@Heli" / "cfgeventspawns.xml",
        '<?xml version="1.0"?><eventposdef><event name="Shared">'
        '<pos x="5" z="5" a="0"/></event></eventposdef>',
    )
    _merge_all(sample_config)

    capture_event_positions(
        sample_config.server_dir_path, MISSION, "Shared", manual_dir=manual_dir(),
    )

    text = (mission / "cfgeventspawns.xml").read_text()
    assert text.count('x="7"') == 1 and text.count('x="5"') == 1
    assert "START MOD: @Heli" in text


def test_capture_keeps_an_event_a_mod_also_contributes_to(sample_config, mission):
    """Only the admin's own positions move; the mod's block stays where it is,
    so the <event> element must not be dropped with them."""
    from app.xml.xml_manual import capture_event_positions

    write_xml(
        mission / "cfgeventspawns.xml",
        '<?xml version="1.0"?><eventposdef><event name="Shared">'
        '<pos x="7" z="8" a="0"/>'
        '<!-- START MOD: @Heli --><pos x="3" z="3" a="0"/><!-- END MOD: @Heli -->'
        "</event></eventposdef>",
    )

    capture_event_positions(
        sample_config.server_dir_path, MISSION, "Shared", manual_dir=manual_dir(),
    )

    text = (mission / "cfgeventspawns.xml").read_text()
    assert "START MOD: @Heli" in text
    assert text.count('x="3"') == 1
    assert text.count('x="7"') == 1


def test_capture_skips_positions_a_mod_already_owns(sample_config, mission):
    from app.xml.xml_manual import ManualFileError, capture_event_positions

    write_xml(
        mission / "cfgeventspawns.xml",
        '<?xml version="1.0"?><eventposdef><event name="ModOnly">'
        "<!-- START MOD: @Heli --><pos x=\"1\" z=\"1\" a=\"0\"/><!-- END MOD: @Heli -->"
        "</event></eventposdef>",
    )

    with pytest.raises(ManualFileError):
        capture_event_positions(
            sample_config.server_dir_path, MISSION, "ModOnly", manual_dir=manual_dir(),
        )


def test_capture_unknown_event_raises(sample_config, mission):
    from app.xml.xml_manual import ManualFileError, capture_event_positions

    with pytest.raises(ManualFileError):
        capture_event_positions(
            sample_config.server_dir_path, MISSION, "NoSuchEvent", manual_dir=manual_dir(),
        )


# --- moving a whole mission file's own entries out --------------------------


def test_capture_unowned_moves_only_what_no_mod_owns(sample_config, mission):
    from app.xml.xml_manual import capture_unowned

    write_xml(
        sample_config.server_dir_path / "@Heli" / "cfgeventgroups.xml",
        '<?xml version="1.0"?><eventgroupdef><group name="ModGroup">'
        '<child type="Heli"/></group></eventgroupdef>',
    )
    write_xml(
        mission / "cfgeventgroups.xml",
        '<?xml version="1.0"?><eventgroupdef><group name="Mine">'
        '<child type="Barrel"/></group></eventgroupdef>',
    )
    _merge_all(sample_config)

    link = capture_unowned(
        sample_config.server_dir_path, MISSION, "cfgeventgroups.xml", manual_dir=manual_dir(),
    )
    assert link["kind"] == "cfgeventgroups" and link["enabled"] is True

    text = (mission / "cfgeventgroups.xml").read_text()
    # Applied in place: the admin's group is back, inside its own block now.
    assert text.count('name="Mine"') == 1
    assert f"START MOD: @user:{link['id']}" in text
    assert text.index("START MOD: @Heli") < text.index(f"START MOD: @user:{link['id']}")
    # The mod's block is untouched, and nothing is doubled after a re-merge.
    assert text.count('name="ModGroup"') == 1
    _merge_all(sample_config)
    text = (mission / "cfgeventgroups.xml").read_text()
    assert text.count('name="Mine"') == 1 and text.count('name="ModGroup"') == 1


def test_capture_unowned_leaves_bohemias_own_entries(sample_config, mission, tmp_path):
    """On a stock file the move took every entry as
    "your own", and deleting that manual file later emptied the mission file.
    An entry identical to the stock copy stays, whatever its indentation."""
    from app.xml.xml_manual import ManualFileError, capture_unowned

    stock_text = ('<?xml version="1.0"?><eventgroupdef><group name="Stock">'
                  '<child type="Barrel"/></group></eventgroupdef>')
    stock = tmp_path / "stock-cfgeventgroups.xml"
    stock.write_text(stock_text)
    write_xml(mission / "cfgeventgroups.xml", stock_text.replace("<child", "\n\t\t<child"))
    with pytest.raises(ManualFileError, match="the game's own"):
        capture_unowned(
            sample_config.server_dir_path, MISSION, "cfgeventgroups.xml",
            manual_dir=manual_dir(), stock=stock,
        )

    write_xml(mission / "cfgeventgroups.xml", stock_text.replace(
        "</eventgroupdef>", '<group name="Mine"><child type="Heli"/></group></eventgroupdef>'))
    capture_unowned(
        sample_config.server_dir_path, MISSION, "cfgeventgroups.xml",
        manual_dir=manual_dir(), stock=stock,
    )
    text = (mission / "cfgeventgroups.xml").read_text()
    assert text.count('name="Stock"') == 1 and text.count('name="Mine"') == 1
    assert text.index('name="Stock"') < text.index("START MOD: @user:")  # stayed in place


def test_capture_unowned_refuses_a_file_that_is_all_mod(sample_config, mission):
    from app.xml.xml_manual import ManualFileError, capture_unowned

    write_xml(
        sample_config.server_dir_path / "@Heli" / "cfgeventgroups.xml",
        '<?xml version="1.0"?><eventgroupdef><group name="ModGroup">'
        '<child type="Heli"/></group></eventgroupdef>',
    )
    write_xml(mission / "cfgeventgroups.xml", '<?xml version="1.0"?><eventgroupdef/>')
    _merge_all(sample_config)
    before = (mission / "cfgeventgroups.xml").read_text()

    with pytest.raises(ManualFileError, match="comes from a mod"):
        capture_unowned(
            sample_config.server_dir_path, MISSION, "cfgeventgroups.xml", manual_dir=manual_dir(),
        )

    assert (mission / "cfgeventgroups.xml").read_text() == before


def test_capture_unowned_handles_section_mode(sample_config, mission):
    """cfglimitsdefinition* put the markers one level down, inside each section.
    Taking the root's unowned children there would move the whole
    <usageflags> — mod entries and all — and leave the mod's block dangling."""
    from app.xml.xml_manual import capture_unowned

    write_xml(
        sample_config.server_dir_path / "@Heli" / "cfglimitsdefinitionuser.xml",
        '<?xml version="1.0"?><lists><usageflags>'
        '<user name="modflags"><usage name="Town"/></user></usageflags></lists>',
    )
    write_xml(
        mission / "cfglimitsdefinitionuser.xml",
        '<?xml version="1.0"?><lists>'
        '<usageflags><user name="mine"><usage name="Farm"/></user></usageflags>'
        '<valueflags><user name="tiers"><value name="Tier1"/></user></valueflags>'
        "</lists>",
    )
    _merge_all(sample_config)

    link = capture_unowned(
        sample_config.server_dir_path, MISSION, "cfglimitsdefinitionuser.xml",
        manual_dir=manual_dir(),
    )
    assert link["kind"] == "cfglimitsdefinitionuser"

    text = (mission / "cfglimitsdefinitionuser.xml").read_text()
    usage = text.split("<usageflags>")[1].split("</usageflags>")[0]
    assert 'name="modflags"' in usage and "START MOD: @Heli" in usage
    assert text.count('name="mine"') == 1 and text.count('name="tiers"') == 1
    # The sections themselves stayed put — only their own children moved.
    assert text.count("<usageflags>") == 1 and text.count("<valueflags>") == 1

    _merge_all(sample_config)
    text = (mission / "cfglimitsdefinitionuser.xml").read_text()
    assert text.count('name="mine"') == 1 and text.count('name="modflags"') == 1


@pytest.mark.parametrize("capture", ["file", "event"])
def test_a_move_into_a_manual_file_keeps_one_version(sample_config, mission, capture):
    """Save as manual XML file writes the mission file twice, the entries out
    and then the file's block in. The version of the copy in between was a file
    nobody had: cfglimitsdefinition.xml with its four lists empty."""
    import hashlib

    target = write_xml(
        mission / "cfgeventspawns.xml",
        EVENTSPAWNS_MISSION.replace('x="1" z="2"', 'x="7" z="8"'),
    )
    before = target.read_bytes()
    count = len(_versions(target))
    if capture == "file":
        xml_manual.capture_unowned(
            sample_config.server_dir_path, MISSION, "cfgeventspawns.xml", manual_dir=manual_dir(),
        )
    else:
        xml_manual.capture_event_positions(
            sample_config.server_dir_path, MISSION, "Vanilla", manual_dir=manual_dir(),
        )

    assert 'x="7"' in target.read_text()
    versions = _versions(target)
    assert len(versions) == count + 1
    assert versions[0].sha256 == hashlib.sha256(before).hexdigest()


def test_capture_unowned_rejects_a_file_no_merge_owns(sample_config, mission):
    """cfgeconomycore.xml is deliberately out: its <ce folder="custom"> block is
    regenerated every merge, and a @user: marker block would compete with it."""
    from app.xml.xml_manual import ManualFileError, capture_unowned

    for target in ("cfgeconomycore.xml", "db/types.xml", "../../etc/passwd"):
        with pytest.raises(ManualFileError):
            capture_unowned(sample_config.server_dir_path, MISSION, target, manual_dir=manual_dir())


# --- excluded generated files -----------------------------------------------


def _mod_with_types(config):
    write_xml(
        config.server_dir_path / "@Bikes" / "types.xml",
        '<?xml version="1.0"?><types><type name="Old_Bike"/></types>',
    )


def test_excluded_custom_file_is_never_copied_or_registered(sample_config, mission):
    from app.xml.xml_manual import set_excluded

    _mod_with_types(sample_config)
    _merge_all(sample_config)
    assert (mission / "custom" / "bikes-types.xml").is_file()

    set_excluded("bikes-types.xml", True, manual_dir=manual_dir())
    _merge_all(sample_config)

    assert not (mission / "custom" / "bikes-types.xml").exists()
    assert "bikes-types.xml" not in (mission / "cfgeconomycore.xml").read_text()


def test_exclusion_is_reversible(sample_config, mission):
    from app.xml.xml_manual import set_excluded

    _mod_with_types(sample_config)
    set_excluded("bikes-types.xml", True, manual_dir=manual_dir())
    _merge_all(sample_config)
    set_excluded("bikes-types.xml", False, manual_dir=manual_dir())
    _merge_all(sample_config)

    assert (mission / "custom" / "bikes-types.xml").is_file()
    assert "bikes-types.xml" in (mission / "cfgeconomycore.xml").read_text()


def test_deleted_is_suppressed_like_excluded(sample_config, mission):
    """One list, two states — the merge reads the union, the page tells them apart."""
    from app.xml.xml_manual import (
        load_custom_states,
        load_deleted,
        load_exclusions,
        set_custom_state,
    )

    _mod_with_types(sample_config)
    set_custom_state("bikes-types.xml", "deleted", manual_dir=manual_dir())
    _merge_all(sample_config)

    assert not (mission / "custom" / "bikes-types.xml").exists()
    assert load_exclusions(manual_dir=manual_dir()) == {"bikes-types.xml"}
    assert load_deleted(manual_dir=manual_dir()) == {"bikes-types.xml"}
    assert load_custom_states(manual_dir=manual_dir()) == {"bikes-types.xml": "deleted"}

    set_custom_state("bikes-types.xml", "", manual_dir=manual_dir())
    _merge_all(sample_config)
    assert (mission / "custom" / "bikes-types.xml").is_file()
    assert load_exclusions(manual_dir=manual_dir()) == set()


def test_older_exclusion_files_still_load():
    """The on-disk shape gained a `deleted` bucket; the old one had one key."""
    import json

    from app.shared.json_utils import atomic_write_text
    from app.xml.xml_manual import load_exclusions, load_source_exclusions

    manual_dir().mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        manual_dir() / "excluded-custom.json", json.dumps({"excluded": ["bikes-types.xml"]})
    )
    atomic_write_text(
        manual_dir() / "excluded-sources.json", json.dumps({"excluded": ["@Bikes/types.xml"]})
    )

    assert load_exclusions(manual_dir=manual_dir()) == {"bikes-types.xml"}
    assert load_source_exclusions(manual_dir=manual_dir()) == {"@Bikes/types.xml"}


def test_owning_mod_prefers_the_longest_prefix(sample_config):
    from app.xml.xml_manual import owning_mod

    for name in ("@Trader", "@TraderPlus"):
        write_xml(sample_config.server_dir_path / name / "types.xml", "<types/>")

    assert owning_mod(sample_config.server_dir_path, "traderplus-types.xml") == "@TraderPlus"
    assert owning_mod(sample_config.server_dir_path, "trader-types.xml") == "@Trader"
    assert owning_mod(sample_config.server_dir_path, "nobody-types.xml") is None


def test_fork_creates_a_link_and_disables_the_generated_file(sample_config, mission):
    from app.xml.xml_manual import fork_custom_file, is_excluded

    _mod_with_types(sample_config)
    _merge_all(sample_config)

    link = fork_custom_file(
        sample_config.server_dir_path, MISSION, "bikes-types.xml", manual_dir=manual_dir(),
    )
    assert link["kind"] == "types"
    assert link["mod"] == "@Bikes"
    assert link["forked_from"] == "bikes-types.xml"  # the pair's only join
    assert is_excluded("bikes-types.xml", manual_dir=manual_dir())
    # Enabled since 2026 Q3: generated files can't be edited in place any
    # more, so this is the whole act of taking one over — leaving it off would
    # drop that file's content out of the mission until a second switch.
    assert link["enabled"] is True

    _merge_all(sample_config)
    core = (mission / "cfgeconomycore.xml").read_text()
    # The generated original is gone (matched on the attribute, since the
    # link's own file is named user-<id>.xml and contains the same substring)...
    assert 'name="bikes-types.xml"' not in core
    assert f'name="user-{link["id"]}.xml"' in core  # ...the editable copy took over
    assert "Old_Bike" in (mission / "custom" / f"user-{link['id']}.xml").read_text()


def test_fork_drops_a_doubled_mod_prefix_from_the_id(sample_config, mission):
    """A mod whose own file is named after itself doubles in custom/."""
    from app.xml.xml_manual import fork_custom_file

    write_xml(
        sample_config.server_dir_path / "@TraderPlus" / "TraderPlus_Types.xml",
        '<?xml version="1.0"?><types><type name="Coin"/></types>',
    )
    _merge_all(sample_config)
    assert (mission / "custom" / "traderplus-TraderPlus_Types.xml").is_file()

    link = fork_custom_file(
        sample_config.server_dir_path, MISSION, "traderplus-TraderPlus_Types.xml",
        manual_dir=manual_dir(),
    )
    assert link["id"] == "traderplus-types"

    _merge_all(sample_config)
    assert (mission / "custom" / "user-traderplus-types.xml").is_file()


def test_fork_reads_the_mod_file_when_nothing_was_generated(sample_config, mission):
    """A disabled or turned-off file has no custom/ copy — fork the mod's own."""
    from app.xml.xml_manual import fork_custom_file, is_excluded, file_content_path

    _mod_with_types(sample_config)
    source = sample_config.server_dir_path / "@Bikes" / "types.xml"
    assert not (mission / "custom" / "bikes-types.xml").exists()

    link = fork_custom_file(
        sample_config.server_dir_path, MISSION, "bikes-types.xml", source_path=source,
        manual_dir=manual_dir(),
    )
    assert link["kind"] == "types" and link["forked_from"] == "bikes-types.xml"
    assert "Old_Bike" in file_content_path(link["id"], manual_dir=manual_dir()).read_text()
    assert is_excluded("bikes-types.xml", manual_dir=manual_dir())


def test_fork_without_a_file_or_a_fallback_raises(sample_config, mission):
    from app.xml.xml_manual import ManualFileError, fork_custom_file

    with pytest.raises(ManualFileError):
        fork_custom_file(
            sample_config.server_dir_path, MISSION, "nothing-types.xml", manual_dir=manual_dir(),
        )


def test_fork_falls_back_to_the_full_stem_when_the_short_id_is_taken(sample_config, mission):
    from app.xml.xml_manual import baseline_path, create_file, fork_custom_file

    write_xml(
        sample_config.server_dir_path / "@TraderPlus" / "TraderPlus_Types.xml",
        '<?xml version="1.0"?><types><type name="Coin"/></types>',
    )
    _merge_all(sample_config)
    create_file("traderplus-types", "types", TYPES, manual_dir=manual_dir())

    link = fork_custom_file(
        sample_config.server_dir_path, MISSION, "traderplus-TraderPlus_Types.xml",
        manual_dir=manual_dir(),
    )
    assert link["id"] == "traderplus-traderplus-types"
    # The baseline is written only once the id is settled: the losing candidate
    # belongs to an unrelated file, whose own baseline this would have clobbered.
    assert not baseline_path("traderplus-types", manual_dir=manual_dir()).exists()
    assert baseline_path(link["id"], manual_dir=manual_dir()).is_file()


# --- upstream drift on a file taken over from a mod -------------------------


def test_fork_records_the_mod_file_it_was_taken_from(sample_config, mission):
    from app.xml.xml_manual import baseline_path, fork_custom_file, upstream_status

    _mod_with_types(sample_config)
    _merge_all(sample_config)

    link = fork_custom_file(
        sample_config.server_dir_path, MISSION, "bikes-types.xml", manual_dir=manual_dir(),
    )

    # Found without a `source_path`: custom_name is `kebab_case(mod)-<filename>`.
    assert link["source"] == "@Bikes/types.xml"
    assert len(link["source_sha256"]) == 64
    assert link["source_captured_at"]
    assert "Old_Bike" in baseline_path(link["id"], manual_dir=manual_dir()).read_text()
    assert upstream_status(link, sample_config.server_dir_path) == "current"


def test_upstream_reports_unknown_for_a_record_that_predates_baselines(sample_config):
    """A file forked before this existed has no baseline. It must read as
    "can't say", not raise and not claim the mod changed nothing."""
    from app.xml.xml_manual import create_file, upstream_info, upstream_status

    link = create_file("old", "types", TYPES, manual_dir=manual_dir())

    assert upstream_status(link, sample_config.server_dir_path) == "unknown"
    assert upstream_info(
        link, sample_config.server_dir_path, diff=True, manual_dir=manual_dir(),
    )["diff"] == ""


def test_upstream_notices_a_changed_mod_file_and_diffs_it(sample_config, mission):
    from app.xml.xml_manual import fork_custom_file, upstream_info

    _mod_with_types(sample_config)
    _merge_all(sample_config)
    link = fork_custom_file(
        sample_config.server_dir_path, MISSION, "bikes-types.xml", manual_dir=manual_dir(),
    )

    write_xml(
        sample_config.server_dir_path / "@Bikes" / "types.xml",
        '<?xml version="1.0"?><types><type name="Old_Bike"/><type name="New_Bike"/></types>',
    )

    info = upstream_info(link, sample_config.server_dir_path, diff=True, manual_dir=manual_dir())
    assert info["status"] == "changed"
    assert "New_Bike" in info["diff"]
    assert info["source"] == "@Bikes/types.xml"


def test_upstream_ignores_a_pure_reformat(sample_config, mission):
    """Both sides go through the same normalization a version diff uses, so a
    mod that only re-indented its file is not reported as a change."""
    from app.xml.xml_manual import fork_custom_file, upstream_info

    _mod_with_types(sample_config)
    _merge_all(sample_config)
    link = fork_custom_file(
        sample_config.server_dir_path, MISSION, "bikes-types.xml", manual_dir=manual_dir(),
    )

    write_xml(
        sample_config.server_dir_path / "@Bikes" / "types.xml",
        '<?xml version="1.0"?>\n<types>\n\t<type name="Old_Bike"/>\n</types>\n',
    )

    # The bytes differ, so the hash does — but the diff is empty, which is what
    # the page shows.
    assert upstream_info(
        link, sample_config.server_dir_path, diff=True, manual_dir=manual_dir(),
    )["diff"] == ""


def test_upstream_reports_missing_when_the_mod_is_gone(sample_config, mission):
    import shutil

    from app.xml.xml_manual import fork_custom_file, upstream_status

    _mod_with_types(sample_config)
    _merge_all(sample_config)
    link = fork_custom_file(
        sample_config.server_dir_path, MISSION, "bikes-types.xml", manual_dir=manual_dir(),
    )

    shutil.rmtree(sample_config.server_dir_path / "@Bikes")

    assert upstream_status(link, sample_config.server_dir_path) == "missing"


def test_ack_rebaselines_without_touching_the_admins_file(sample_config, mission):
    from app.xml.xml_manual import (
        ack_upstream,
        file_content_path,
        fork_custom_file,
        get_file,
        update_file,
        upstream_status,
    )

    _mod_with_types(sample_config)
    _merge_all(sample_config)
    link = fork_custom_file(
        sample_config.server_dir_path, MISSION, "bikes-types.xml", manual_dir=manual_dir(),
    )
    update_file(
        link["id"],
        content='<?xml version="1.0"?><types><type name="Mine"/></types>',
        manual_dir=manual_dir(),
    )
    write_xml(
        sample_config.server_dir_path / "@Bikes" / "types.xml",
        '<?xml version="1.0"?><types><type name="New_Bike"/></types>',
    )

    assert ack_upstream(
        link["id"], sample_config.server_dir_path, manual_dir=manual_dir(),
    )["status"] == "current"

    assert upstream_status(get_file(
        link["id"], manual_dir=manual_dir(),
    ), sample_config.server_dir_path) == "current"
    assert "Mine" in file_content_path(link["id"], manual_dir=manual_dir()).read_text()


def test_adopt_replaces_the_admins_file_and_leaves_a_version(sample_config, mission):
    from app.shared.config_versions import load_index
    from app.xml.xml_manual import (
        adopt_upstream,
        file_content_path,
        fork_custom_file,
        get_file,
        update_file,
        upstream_status,
    )

    _mod_with_types(sample_config)
    _merge_all(sample_config)
    link = fork_custom_file(
        sample_config.server_dir_path, MISSION, "bikes-types.xml", manual_dir=manual_dir(),
    )
    update_file(
        link["id"],
        content='<?xml version="1.0"?><types><type name="Mine"/></types>',
        manual_dir=manual_dir(),
    )
    write_xml(
        sample_config.server_dir_path / "@Bikes" / "types.xml",
        '<?xml version="1.0"?><types><type name="New_Bike"/></types>',
    )

    assert adopt_upstream(
        link["id"], sample_config.server_dir_path, manual_dir=manual_dir(),
    )["status"] == "current"

    own = file_content_path(link["id"], manual_dir=manual_dir())
    assert "New_Bike" in own.read_text() and "Mine" not in own.read_text()
    assert upstream_status(get_file(
        link["id"], manual_dir=manual_dir(),
    ), sample_config.server_dir_path) == "current"
    # Destructive, so the discarded version is recoverable.
    versions = load_index(sample_config.paths.versions_dir).get_versions(own.name, source_path=str(own))
    assert any(v.trigger == "upstream_adopt" for v in versions)


def test_deleting_a_file_removes_its_baseline(sample_config, mission):
    from app.xml.xml_manual import baseline_path, delete_file, fork_custom_file

    _mod_with_types(sample_config)
    _merge_all(sample_config)
    link = fork_custom_file(
        sample_config.server_dir_path, MISSION, "bikes-types.xml", manual_dir=manual_dir(),
    )
    assert baseline_path(link["id"], manual_dir=manual_dir()).is_file()

    delete_file(
        link["id"],
        server_dir=sample_config.server_dir_path,
        mission=MISSION,
        manual_dir=manual_dir(),
    )

    assert not baseline_path(link["id"], manual_dir=manual_dir()).exists()


def test_fork_refuses_a_non_ce_file(sample_config, mission):
    from app.xml.xml_manual import ManualFileError, fork_custom_file

    write_xml(
        mission / "custom" / "bikes-cfgeventspawns.xml",
        '<?xml version="1.0"?><eventposdef><event name="X"/></eventposdef>',
    )
    with pytest.raises(ManualFileError):
        fork_custom_file(
            sample_config.server_dir_path,
            MISSION,
            "bikes-cfgeventspawns.xml",
            manual_dir=manual_dir(),
        )


# --- free-form merges (kind="merge") ----------------------------------------


def test_free_form_merge_targets_any_mission_file(sample_config, mission):
    write_xml(mission / "db" / "globals.xml", '<?xml version="1.0"?><variables/>')
    xml_manual.create_file(
        "extra-globals",
        xml_manual.MERGE_KIND,
        '<?xml version="1.0"?><variables><var name="MyVar" type="0" value="1"/></variables>',
        target="db/globals.xml",
        manual_dir=manual_dir(),
    )
    assert _merge_all(sample_config) == []

    text = (mission / "db" / "globals.xml").read_text()
    assert "START MOD: @user:extra-globals" in text
    assert 'name="MyVar"' in text


def test_free_form_merge_is_idempotent_and_removable(sample_config, mission):
    write_xml(mission / "db" / "globals.xml", '<?xml version="1.0"?><variables/>')
    xml_manual.create_file(
        "extra-globals",
        xml_manual.MERGE_KIND,
        '<variables><var name="MyVar"/></variables>',
        target="db/globals.xml",
        manual_dir=manual_dir(),
    )
    for _ in range(3):
        _merge_all(sample_config)
    assert (mission / "db" / "globals.xml").read_text().count("MyVar") == 1

    xml_manual.delete_file(
        "extra-globals", server_dir=sample_config.server_dir_path, mission=MISSION,
        manual_dir=manual_dir(),
    )
    assert "MyVar" not in (mission / "db" / "globals.xml").read_text()


def test_free_form_merge_requires_a_safe_existing_target(sample_config, mission):
    for target in ("", "../../etc/passwd", "db/sub/deep.xml", "notxml", "back\\slash.xml"):
        with pytest.raises(xml_manual.ManualFileError):
            xml_manual.create_file(
                "t", xml_manual.MERGE_KIND, "<x/>", target=target, manual_dir=manual_dir(),
            )


def test_free_form_merge_missing_target_is_a_failure(sample_config, mission):
    xml_manual.create_file(
        "gone", xml_manual.MERGE_KIND, "<variables><var name='X'/></variables>",
        target="db/globals.xml",  # never created,
        manual_dir=manual_dir(),
    )
    assert _merge_all(sample_config) == ["manual link gone"]


def test_list_merge_targets_covers_mission_root_and_db(sample_config, mission):
    write_xml(mission / "db" / "globals.xml", "<variables/>")
    targets = xml_manual.list_merge_targets(sample_config.server_dir_path, MISSION)

    assert "cfgeventspawns.xml" in targets
    assert "db/globals.xml" in targets


# ---------------------------------------------------------------------------
# Merge target -> kind
# ---------------------------------------------------------------------------
#
# The web card only asks for a kind on the CE side (that's all cfgeconomycore
# can reference); a merge asks for a *file*. So the file has to pick the merge
# config that owns it — free-form root-appending into cfglimitsdefinition.xml
# would produce a file the CE can't load.


def test_target_for_kind_maps_every_manual_kind():
    assert xml_manual.target_for_kind("types") == "cfgeconomycore.xml"
    assert xml_manual.target_for_kind("cfgeventspawns") == "cfgeventspawns.xml"
    assert xml_manual.target_for_kind("cfglimitsdefinitionuser") == "cfglimitsdefinitionuser.xml"
    assert xml_manual.target_for_kind(xml_manual.MERGE_KIND) == ""


def test_kind_for_target_upgrades_known_files_only():
    assert xml_manual.kind_for_target("cfgeventspawns.xml") == "cfgeventspawns"
    assert xml_manual.kind_for_target("CfgEventSpawns.XML") == "cfgeventspawns"
    assert xml_manual.kind_for_target("db/globals.xml") == xml_manual.MERGE_KIND


def test_merge_at_a_known_file_becomes_that_kind(sample_config, mission):
    link = xml_manual.create_file(
        "heli",
        xml_manual.MERGE_KIND,
        "<eventposdef><event name='Heli'><pos x='9' z='9' a='0'/></event></eventposdef>",
        target="cfgeventspawns.xml",
        manual_dir=manual_dir(),
    )

    # Upgraded, and the now-implied target is dropped so a stale value can never
    # mis-target the merge.
    assert link["kind"] == "cfgeventspawns"
    assert link["target"] == ""

    assert _merge_all(sample_config) == []
    text = (mission / "cfgeventspawns.xml").read_text()
    assert "START MOD: @user:heli" in text
    # The merge config matched the event into the file, not the raw fragment.
    assert "<eventposdef>" not in text.split("START MOD: @user:heli")[1]


def test_merge_at_an_unknown_file_stays_free_form(sample_config, mission):
    link = xml_manual.create_file(
        "vars", xml_manual.MERGE_KIND, "<variables><var name='X'/></variables>",
        target="db/globals.xml",
        manual_dir=manual_dir(),
    )
    assert link["kind"] == xml_manual.MERGE_KIND
    assert link["target"] == "db/globals.xml"


def test_retargeting_an_existing_merge_upgrades_its_kind(sample_config, mission):
    xml_manual.create_file(
        "moved", xml_manual.MERGE_KIND, "<eventposdef><event name='A'/></eventposdef>",
        target="db/globals.xml",
        manual_dir=manual_dir(),
    )
    # Only `target` sent, as the web form does when the file picker changes.
    link = xml_manual.update_file("moved", target="cfgeventspawns.xml", manual_dir=manual_dir())

    assert link["kind"] == "cfgeventspawns"
    assert link["target"] == ""


# ---------------------------------------------------------------------------
# Sampling an element out of a merge target
# ---------------------------------------------------------------------------
#
# The builder's "copy an element from the file you're merging into" button. The
# rule it must never break: a mod's merged element is re-applied from the mod
# on every merge, so copying one into a link doubles it on every spawn.

GLOBALS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n<variables>\n'
    "\t<!-- START MOD: @Bikes -->\n"
    '\t<var name="ModVar" type="0" value="1"/>\n'
    "\t<!-- END MOD: @Bikes -->\n"
    '\t<var name="ZombieMaxCount" type="0" value="800"/>\n'
    "</variables>\n"
)


def _sample(config, target):
    return xml_manual.sample_merge_element(config.server_dir_path, MISSION, target)


def test_unowned_children_skips_mod_blocks(sample_config, mission):
    from app.xml.xml_utils import load_xml

    write_xml(mission / "db" / "globals.xml", GLOBALS)
    root = load_xml(mission / "db" / "globals.xml", preserve_comments=True).getroot()

    assert [c.get("name") for c in xml_manual.unowned_children(root)] == ["ZombieMaxCount"]


def test_sample_copies_the_first_element_the_user_owns(sample_config, mission):
    write_xml(mission / "db" / "globals.xml", GLOBALS)
    sample = _sample(sample_config, "db/globals.xml")

    assert sample.root_tag == "variables"
    assert sample.skipped_mod_blocks == 1
    assert 'name="ZombieMaxCount"' in sample.content
    assert "ModVar" not in sample.content
    assert "START MOD" not in sample.content
    assert sample.content.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<variables>')


def test_sample_strips_mod_blocks_nested_inside_the_sampled_element(sample_config, mission):
    write_xml(
        mission / "cfgeventspawns.xml",
        '<?xml version="1.0"?><eventposdef><event name="Mine">'
        '<pos x="1" z="1" a="0"/>'
        "<!-- START MOD: @Bikes -->"
        '<pos x="9" z="9" a="0"/>'
        "<!-- END MOD: @Bikes -->"
        "</event></eventposdef>",
    )
    sample = _sample(sample_config, "cfgeventspawns.xml")

    assert 'x="1"' in sample.content and 'x="9"' not in sample.content


def test_sample_trims_a_long_element_and_says_so(sample_config, mission):
    positions = "".join(f'<pos x="{i}" z="0" a="0"/>' for i in range(10))
    write_xml(
        mission / "cfgeventspawns.xml",
        f'<?xml version="1.0"?><eventposdef><event name="Many">{positions}</event></eventposdef>',
    )
    sample = _sample(sample_config, "cfgeventspawns.xml")

    assert sample.content.count("<pos") == xml_manual.SAMPLE_MAX_CHILDREN
    assert "Trimmed" in sample.content


def test_sample_reports_a_reason_but_keeps_the_root_tag(sample_config, mission):
    """The root tag is what lets the UI still offer a correct empty starter."""
    write_xml(
        mission / "db" / "globals.xml",
        '<?xml version="1.0"?><variables><!-- START MOD: @Bikes -->'
        '<var name="ModVar" type="0" value="1"/><!-- END MOD: @Bikes --></variables>',
    )
    sample = _sample(sample_config, "db/globals.xml")

    assert sample.content == "" and sample.root_tag == "variables"
    assert "nothing of yours" in sample.reason


def test_sample_refuses_an_unsafe_target(sample_config, mission):
    for target in ("../../etc/passwd", "db/sub/deep.xml", "notxml", ""):
        assert _sample(sample_config, target) is None


def test_sample_of_a_missing_file_explains_itself(sample_config, mission):
    sample = _sample(sample_config, "db/globals.xml")
    assert sample.content == "" and "part of this mission" in sample.reason


def test_sample_skips_an_oversized_file_without_parsing_it(sample_config, mission):
    path = mission / "db" / "globals.xml"
    write_xml(path, GLOBALS)
    sample = xml_manual.sample_merge_element(
        sample_config.server_dir_path, MISSION, "db/globals.xml", max_bytes=10,
    )
    assert sample.content == "" and "too large" in sample.reason


def test_sampled_content_round_trips_into_a_link_and_merges_once(sample_config, mission):
    write_xml(mission / "db" / "globals.xml", GLOBALS)
    sample = _sample(sample_config, "db/globals.xml")

    xml_manual.create_file(
        "myvar",
        xml_manual.MERGE_KIND,
        sample.content,
        target="db/globals.xml",
        manual_dir=manual_dir(),
    )
    assert _merge_all(sample_config) == []

    text = (mission / "db" / "globals.xml").read_text()
    assert text.count("START MOD: @user:myvar") == 1
    # The copy landed inside the markers; the original is still above them.
    assert text.count('name="ZombieMaxCount"') == 2


@pytest.mark.parametrize("broken", ['{"files": [{"id": "mine"},]}', "[]", '{"files": {"id": "mine"}}'])
def test_nothing_is_added_to_an_index_that_cannot_be_read(sample_config, broken):
    """files.json is a file a person may edit. Read as an empty list, the next
    file added would replace it, and every record in it would be gone."""
    store = manual_dir()
    xml_manual.create_file("mine", "types", TYPES, manual_dir=store)
    index = store / "files.json"
    index.write_text(broken, encoding="utf-8")
    content = xml_manual.file_content_path("mine", manual_dir=store)
    before = content.read_text()

    # Reading stays lenient: the page lists nothing rather than failing.
    assert xml_manual.load_files(manual_dir=store) == []

    for file_id in ("other", "mine"):
        with pytest.raises(xml_manual.ManualFileError, match="files.json"):
            xml_manual.create_file(file_id, "types", '<types><type name="Z"/></types>', manual_dir=store)

    assert index.read_text() == broken
    assert content.read_text() == before
    assert not xml_manual.file_content_path("other", manual_dir=store).exists()


@pytest.mark.parametrize("broken", ['{"excluded": ["a.xml",]}', '{"excluded": 5}', "[]"])
def test_a_state_is_not_saved_over_a_list_that_cannot_be_read(sample_config, broken):
    """Same for the two lists of turned-off files: replaced by one entry,
    every other file in them would be back on at the next merge."""
    store = manual_dir()
    store.mkdir(parents=True, exist_ok=True)
    custom = store / "excluded-custom.json"
    sources = store / "excluded-sources.json"
    custom.write_text(broken, encoding="utf-8")
    sources.write_text(broken, encoding="utf-8")

    # A merge still runs: unreadable reads as "nothing turned off".
    assert xml_manual.load_exclusions(manual_dir=store) == set()
    assert xml_manual.load_source_exclusions(manual_dir=store) == set()

    with pytest.raises(xml_manual.ManualFileError, match="excluded-custom.json"):
        xml_manual.set_custom_state("mod-types.xml", "excluded", manual_dir=store)
    with pytest.raises(xml_manual.ManualFileError, match="excluded-sources.json"):
        xml_manual.set_source_state("@Mod/types.xml", "excluded", manual_dir=store)

    assert custom.read_text() == broken
    assert sources.read_text() == broken


def test_a_fork_is_refused_before_it_creates_a_file_it_could_not_pair(sample_config, mission):
    """Taking a file over turns the original off in the same step. With that
    list unreadable the copy must not be created: both live is two files
    declaring the same classnames."""
    from app.xml.xml_manual import fork_custom_file, fork_mod_source

    _mod_with_types(sample_config)
    _merge_all(sample_config)
    store = manual_dir()
    store.mkdir(parents=True, exist_ok=True)
    (store / "excluded-custom.json").write_text("{", encoding="utf-8")
    (store / "excluded-sources.json").write_text("{", encoding="utf-8")
    source = next(
        p.relative_to(sample_config.server_dir_path).as_posix()
        for p in (sample_config.server_dir_path / "@Bikes").rglob("*.xml")
    )

    with pytest.raises(xml_manual.ManualFileError, match="excluded-custom.json"):
        fork_custom_file(sample_config.server_dir_path, MISSION, "bikes-types.xml", manual_dir=store)
    with pytest.raises(xml_manual.ManualFileError, match="excluded-sources.json"):
        fork_mod_source(sample_config.server_dir_path, source, manual_dir=store)

    assert xml_manual.load_files(manual_dir=store) == []
    assert not list(store.glob("*.xml"))
