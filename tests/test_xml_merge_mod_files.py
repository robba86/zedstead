"""Merging real-shaped mod XML into mission files.

Regression suite for the class of bug found with the Bicycles mod: files were
picked up, wrapped in START/END MOD comments, and merged to *nothing* because
the merge keyed on the mod file's root tag.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from app.xml.xml_configs import ALL_MERGE_CONFIGS
from app.xml.xml_merge import merge_all_direct_configs, merge_mod_direct_configs, merge_xml
from tests.conftest import manual_dir, write_xml

MISSION = "dayzOffline.chernarusplus"

BASE_EVENTSPAWNS = """\
<?xml version="1.0" encoding="UTF-8"?>
<eventposdef>
    <event name="StaticHeliCrash"><pos x="1" z="2" a="0"/></event>
</eventposdef>"""

# Root <events>, not <eventposdef> — the shape mods actually ship.
MOD_EVENTSPAWNS = """\
<?xml version="1.0" encoding="UTF-8"?>
<events>
    <event name="VehicleHunterz_Old_Bike">
        <pos x="11506" y="220.9" z="7503.6" a="177.8"/>
        <pos x="11439" y="220.9" z="7556.1" a="-6.7"/>
    </event>
</events>"""

BASE_LIMITS_USER = """\
<?xml version="1.0" encoding="UTF-8"?>
<lists>
    <usageflags><user name="vanilla"><usage name="Town"/></user></usageflags>
    <valueflags><user name="tiers"><value name="Tier1"/></user></valueflags>
</lists>"""

MOD_LIMITS_USER = """\
<?xml version="1.0" encoding="UTF-8"?>
<lists>
    <usageflags><user name="bikeshed"><usage name="Town"/></user></usageflags>
</lists>"""

# cfglimitsdefinition.xml's own shape: flags directly under the section, with no
# <user> wrapper — that nesting is what makes a file the *user* twin.
BASE_LIMITS = """\
<?xml version="1.0" encoding="UTF-8"?>
<lists>
    <usageflags><usage name="Town"/></usageflags>
    <valueflags><value name="Tier1"/></valueflags>
</lists>"""

MOD_LIMITS = """\
<?xml version="1.0" encoding="UTF-8"?>
<lists>
    <usageflags><usage name="Bikeshed"/></usageflags>
</lists>"""

BASE_RANDOMPRESETS = """\
<?xml version="1.0" encoding="UTF-8"?>
<randompresets>
    <cargo name="vanilla" chance="1.0"/>
</randompresets>"""

MOD_RANDOMPRESETS = """\
<?xml version="1.0" encoding="UTF-8"?>
<randompresets>
    <cargo name="bike_bag" chance="0.5"><item name="Rag" chance="1.0"/></cargo>
</randompresets>"""


@pytest.fixture()
def eventspawns_target(tmp_mission_dir: Path) -> Path:
    return write_xml(tmp_mission_dir / "cfgeventspawns.xml", BASE_EVENTSPAWNS)


@pytest.fixture()
def bike_mod(tmp_server_dir: Path) -> Path:
    mod_dir = tmp_server_dir / "@Bicycles"
    write_xml(mod_dir / "xml" / "cfgeventspawns_Chernarus.xml", MOD_EVENTSPAWNS)
    return mod_dir


def test_mod_eventspawns_with_events_root_is_merged(
    tmp_server_dir, eventspawns_target, bike_mod
):
    result = merge_xml(
        tmp_server_dir, MISSION, ALL_MERGE_CONFIGS["cfgeventspawns"], manual_dir=manual_dir(),
    )
    assert result is not None

    text = eventspawns_target.read_text(encoding="utf-8")
    assert "START MOD: @Bicycles" in text
    assert "VehicleHunterz_Old_Bike" in text
    assert text.count("<pos ") == 3  # 1 vanilla + 2 from the mod
    assert "StaticHeliCrash" in text


@pytest.mark.parametrize("folder", ["@Foo--Bar", "@Foo-"])
def test_a_mod_folder_name_a_comment_cannot_hold_is_skipped(
    tmp_server_dir, eventspawns_target, bike_mod, folder
):
    """The name goes into <!-- START MOD: … -->, where "--" is invalid XML:
    the merge wrote a cfgeventspawns.xml DayZ cannot read."""
    from app.xml.xml_utils import load_xml

    write_xml(tmp_server_dir / folder / "cfgeventspawns.xml", MOD_EVENTSPAWNS.replace("Old_Bike", "Other"))
    lines: list[str] = []
    merge_xml(
        tmp_server_dir, MISSION, ALL_MERGE_CONFIGS["cfgeventspawns"], manual_dir=manual_dir(), log=lines.append,
    )

    load_xml(eventspawns_target, preserve_comments=True)  # still well-formed
    text = eventspawns_target.read_text(encoding="utf-8")
    assert "START MOD: @Bicycles" in text
    assert folder not in text
    assert any(line.startswith(f"Skipped {folder}/cfgeventspawns.xml") for line in lines)


def test_a_file_named_in_mixed_case_is_merged(tmp_server_dir, eventspawns_target):
    """`cfgEventSpawns.xml` was skipped without a word: the name filter
    compared its lower-case token against the name as written."""
    write_xml(tmp_server_dir / "@Bicycles" / "cfgEventSpawns.xml", MOD_EVENTSPAWNS)

    merge_xml(tmp_server_dir, MISSION, ALL_MERGE_CONFIGS["cfgeventspawns"], manual_dir=manual_dir())

    text = eventspawns_target.read_text(encoding="utf-8")
    assert "START MOD: @Bicycles" in text
    assert "VehicleHunterz_Old_Bike" in text


def test_other_maps_variant_is_not_merged(tmp_server_dir, eventspawns_target, bike_mod):
    write_xml(
        bike_mod / "xml" / "cfgeventspawns_Livonia.xml",
        MOD_EVENTSPAWNS.replace("11506", "999"),
    )

    merge_xml(tmp_server_dir, MISSION, ALL_MERGE_CONFIGS["cfgeventspawns"], manual_dir=manual_dir())

    text = eventspawns_target.read_text(encoding="utf-8")
    assert "999" not in text
    assert "11506" in text


def test_economy_events_file_never_lands_in_eventspawns(
    tmp_server_dir, eventspawns_target, bike_mod
):
    """Both files use <events><event>; only the position table belongs here."""
    write_xml(
        bike_mod / "xml" / "events.xml",
        '<?xml version="1.0"?><events><event name="Eco"><nominal>20</nominal></event></events>',
    )

    merge_xml(tmp_server_dir, MISSION, ALL_MERGE_CONFIGS["cfgeventspawns"], manual_dir=manual_dir())

    assert "Eco" not in eventspawns_target.read_text(encoding="utf-8")


def test_zero_contribution_is_reported(tmp_server_dir, eventspawns_target, bike_mod):
    """A matched file with no usable elements must say so, not fail silently."""
    write_xml(
        bike_mod / "xml" / "cfgeventspawns_Chernarus.xml",
        '<?xml version="1.0"?><eventposdef></eventposdef>',
    )
    lines: list[str] = []

    merge_xml(
        tmp_server_dir,
        MISSION,
        ALL_MERGE_CONFIGS["cfgeventspawns"],
        log=lines.append,
        manual_dir=manual_dir(),
    )

    assert any("contributed nothing" in line for line in lines)


def test_merge_is_idempotent(tmp_server_dir, eventspawns_target, bike_mod):
    merge_xml(tmp_server_dir, MISSION, ALL_MERGE_CONFIGS["cfgeventspawns"], manual_dir=manual_dir())
    merge_xml(tmp_server_dir, MISSION, ALL_MERGE_CONFIGS["cfgeventspawns"], manual_dir=manual_dir())

    text = eventspawns_target.read_text(encoding="utf-8")
    assert text.count("START MOD: @Bicycles") == 1


def test_disabled_mod_is_dropped_on_remerge(tmp_server_dir, eventspawns_target, bike_mod):
    merge_xml(tmp_server_dir, MISSION, ALL_MERGE_CONFIGS["cfgeventspawns"], manual_dir=manual_dir())
    assert "VehicleHunterz_Old_Bike" in eventspawns_target.read_text(encoding="utf-8")

    merge_xml(
        tmp_server_dir,
        MISSION,
        ALL_MERGE_CONFIGS["cfgeventspawns"],
        disabled_mods={"@Bicycles"},
        manual_dir=manual_dir(),
    )

    text = eventspawns_target.read_text(encoding="utf-8")
    assert "VehicleHunterz_Old_Bike" not in text
    assert "StaticHeliCrash" in text


def test_sections_merge_targets_matching_section(tmp_server_dir, tmp_mission_dir):
    target = write_xml(tmp_mission_dir / "cfglimitsdefinitionuser.xml", BASE_LIMITS_USER)
    write_xml(tmp_server_dir / "@Bicycles" / "cfglimitsdefinitionuser.xml", MOD_LIMITS_USER)

    merge_xml(
        tmp_server_dir,
        MISSION,
        ALL_MERGE_CONFIGS["cfglimitsdefinitionuser"],
        manual_dir=manual_dir(),
    )

    text = target.read_text(encoding="utf-8")
    usage_block = text.split("<usageflags>")[1].split("</usageflags>")[0]
    assert "bikeshed" in usage_block
    assert "bikeshed" not in text.split("<valueflags>")[1]
    # Comments go inside the section so uninstall can strip exactly this block.
    assert "START MOD: @Bicycles" in usage_block


def test_randompresets_merge(tmp_server_dir, tmp_mission_dir):
    target = write_xml(tmp_mission_dir / "cfgrandompresets.xml", BASE_RANDOMPRESETS)
    write_xml(tmp_server_dir / "@Bicycles" / "cfgrandompresets.xml", MOD_RANDOMPRESETS)

    merge_xml(
        tmp_server_dir, MISSION, ALL_MERGE_CONFIGS["cfgrandompresets"], manual_dir=manual_dir(),
    )

    text = target.read_text(encoding="utf-8")
    assert "bike_bag" in text
    assert "vanilla" in text


def test_optional_target_missing_is_not_a_failure(tmp_server_dir, tmp_mission_dir, bike_mod):
    """No cfgrandompresets.xml in the mission is normal — it must not fail the
    whole merge step the way a missing cfgeventspawns.xml does."""
    write_xml(tmp_mission_dir / "cfgeventspawns.xml", BASE_EVENTSPAWNS)
    write_xml(tmp_mission_dir / "cfgeventgroups.xml", "<?xml version='1.0'?><eventgroupdef/>")

    failed = merge_all_direct_configs(tmp_server_dir, MISSION, manual_dir=manual_dir())

    assert failed == []
    assert not (tmp_mission_dir / "cfgrandompresets.xml").exists()


# --- per-mod re-configure (web Mods page "Re-configure" button) -------------


@pytest.fixture()
def second_mod(tmp_server_dir: Path) -> Path:
    """A second mod contributing to the same mission file as @Bicycles."""
    mod_dir = tmp_server_dir / "@Boats"
    write_xml(
        mod_dir / "xml" / "cfgeventspawns_Chernarus.xml",
        MOD_EVENTSPAWNS.replace("VehicleHunterz_Old_Bike", "Boat_Zodiac").replace("11506", "500"),
    )
    return mod_dir


def test_per_mod_merge_leaves_other_mods_untouched(
    tmp_server_dir, eventspawns_target, bike_mod, second_mod
):
    merge_all_direct_configs(tmp_server_dir, MISSION, manual_dir=manual_dir())
    before = eventspawns_target.read_text(encoding="utf-8")
    assert "Boat_Zodiac" in before and "VehicleHunterz_Old_Bike" in before

    failed = merge_mod_direct_configs(tmp_server_dir, MISSION, "@Bicycles", manual_dir=manual_dir())

    assert failed == []
    text = eventspawns_target.read_text(encoding="utf-8")
    # The other mod's block and the vanilla content survive verbatim.
    assert text.count("START MOD: @Boats") == 1
    assert "Boat_Zodiac" in text
    assert "StaticHeliCrash" in text
    # The target mod is still merged exactly once, with all of its positions.
    assert text.count("START MOD: @Bicycles") == 1
    assert text.count("<pos ") == before.count("<pos ")


def test_per_mod_merge_is_idempotent(tmp_server_dir, eventspawns_target, bike_mod):
    merge_mod_direct_configs(tmp_server_dir, MISSION, "@Bicycles", manual_dir=manual_dir())
    merge_mod_direct_configs(tmp_server_dir, MISSION, "@Bicycles", manual_dir=manual_dir())

    text = eventspawns_target.read_text(encoding="utf-8")
    assert text.count("START MOD: @Bicycles") == 1
    assert text.count("<pos ") == 3  # 1 vanilla + 2 from the mod


def test_per_mod_merge_never_restores_from_backup(
    tmp_server_dir, eventspawns_target, bike_mod, second_mod
):
    """The backup exists after a full merge; a scoped merge must not restore it
    (that would silently drop every other mod's blocks)."""
    merge_all_direct_configs(tmp_server_dir, MISSION, manual_dir=manual_dir())
    backup = eventspawns_target.with_suffix(".xml.backup")
    assert backup.exists()

    merge_mod_direct_configs(tmp_server_dir, MISSION, "@Boats", manual_dir=manual_dir())

    assert "VehicleHunterz_Old_Bike" in eventspawns_target.read_text(encoding="utf-8")


def test_per_mod_merge_covers_sections_mode(tmp_server_dir, tmp_mission_dir, bike_mod):
    limits = write_xml(tmp_mission_dir / "cfglimitsdefinitionuser.xml", BASE_LIMITS_USER)
    write_xml(tmp_server_dir / "@Bicycles" / "cfglimitsdefinitionuser.xml", MOD_LIMITS_USER)

    merge_mod_direct_configs(tmp_server_dir, MISSION, "@Bicycles", manual_dir=manual_dir())
    merge_mod_direct_configs(tmp_server_dir, MISSION, "@Bicycles", manual_dir=manual_dir())

    text = limits.read_text(encoding="utf-8")
    usage_block = text.split("<usageflags>")[1].split("</usageflags>")[0]
    assert "bikeshed" in usage_block
    assert text.count("START MOD: @Bicycles") == 1


def test_per_mod_merge_ignores_other_mods_files(
    tmp_server_dir, eventspawns_target, bike_mod, second_mod
):
    """Scoping to a mod with nothing for this config leaves the file alone."""
    failed = merge_mod_direct_configs(tmp_server_dir, MISSION, "@Boats", manual_dir=manual_dir())

    assert failed == []
    text = eventspawns_target.read_text(encoding="utf-8")
    assert "Boat_Zodiac" in text
    assert "VehicleHunterz_Old_Bike" not in text


# --- the mission file's own content survives a merge (2026 Q3) -----------
#
# `merge_xml` used to restore every target from its `.backup` before re-splicing
# the mods, which deleted anything the admin had written outside a START/END MOD
# block: map-editor positions, a hand-added event, an extra <usage>. It now
# creates the backup and strips the mod blocks by name instead.


def test_hand_placed_content_survives_a_merge(tmp_server_dir, eventspawns_target, bike_mod):
    merge_all_direct_configs(tmp_server_dir, MISSION, manual_dir=manual_dir())

    # What the map editor (or the Raw tab) leaves behind: an element that no mod
    # owns, appended after the mods' blocks.
    text = eventspawns_target.read_text(encoding="utf-8")
    eventspawns_target.write_text(
        text.replace(
            "</eventposdef>",
            '\t<event name="MyHandPlaced"><pos x="1" z="2" a="0"/></event>\n</eventposdef>',
        ),
        encoding="utf-8",
    )

    merge_all_direct_configs(tmp_server_dir, MISSION, manual_dir=manual_dir())

    after = eventspawns_target.read_text(encoding="utf-8")
    assert "MyHandPlaced" in after
    assert after.count("START MOD: @Bicycles") == 1
    assert "StaticHeliCrash" in after


def test_uninstalled_mods_block_still_goes(tmp_server_dir, eventspawns_target, bike_mod):
    """The strip is name-agnostic, so a mod that is no longer on disk loses its
    block even though nothing re-splices it — what the restore used to do."""
    merge_all_direct_configs(tmp_server_dir, MISSION, manual_dir=manual_dir())
    assert "VehicleHunterz_Old_Bike" in eventspawns_target.read_text(encoding="utf-8")

    shutil.rmtree(bike_mod)
    merge_all_direct_configs(tmp_server_dir, MISSION, manual_dir=manual_dir())

    text = eventspawns_target.read_text(encoding="utf-8")
    assert "VehicleHunterz_Old_Bike" not in text
    assert "START MOD: @Bicycles" not in text
    assert "StaticHeliCrash" in text


def test_consecutive_merges_are_byte_identical(tmp_server_dir, tmp_mission_dir, bike_mod):
    write_xml(tmp_mission_dir / "cfgeventspawns.xml", BASE_EVENTSPAWNS)
    write_xml(tmp_mission_dir / "cfgeventgroups.xml", "<?xml version='1.0'?><eventgroupdef/>")
    write_xml(tmp_mission_dir / "cfglimitsdefinitionuser.xml", BASE_LIMITS_USER)
    write_xml(tmp_server_dir / "@Bicycles" / "cfglimitsdefinitionuser.xml", MOD_LIMITS_USER)

    merge_all_direct_configs(tmp_server_dir, MISSION, manual_dir=manual_dir())
    first = {p.name: p.read_bytes() for p in tmp_mission_dir.glob("*.xml")}
    merge_all_direct_configs(tmp_server_dir, MISSION, manual_dir=manual_dir())

    assert {p.name: p.read_bytes() for p in tmp_mission_dir.glob("*.xml")} == first


def test_sections_merge_keeps_a_user_added_flag(tmp_server_dir, tmp_mission_dir):
    """Section mode puts its markers *inside* the section, so the strip has to
    leave the section's other children — including the admin's — alone."""
    target = write_xml(tmp_mission_dir / "cfglimitsdefinition.xml", BASE_LIMITS)
    write_xml(tmp_server_dir / "@Bicycles" / "cfglimitsdefinition.xml", MOD_LIMITS)

    merge_xml(
        tmp_server_dir, MISSION, ALL_MERGE_CONFIGS["cfglimitsdefinition"], manual_dir=manual_dir(),
    )
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "</usageflags>", '<usage name="MyOwnUsage"/></usageflags>',
        ),
        encoding="utf-8",
    )

    merge_xml(
        tmp_server_dir, MISSION, ALL_MERGE_CONFIGS["cfglimitsdefinition"], manual_dir=manual_dir(),
    )

    text = target.read_text(encoding="utf-8")
    usage_block = text.split("<usageflags>")[1].split("</usageflags>")[0]
    assert "MyOwnUsage" in usage_block
    assert "Town" in usage_block
    assert text.count("START MOD: @Bicycles") == 1
    assert "Bikeshed" in usage_block


def test_unterminated_marker_is_left_alone_and_reported(tmp_server_dir, eventspawns_target):
    """The back-referenced pattern refuses to guess where a damaged block ends —
    pairing @A's START with @B's END would delete everything between them."""
    from app.xml.xml_merge import strip_all_mod_blocks

    eventspawns_target.write_text(
        BASE_EVENTSPAWNS.replace(
            "</eventposdef>",
            "\t<!-- START MOD: @Broken -->\n"
            '\t<event name="Orphaned"><pos x="9" z="9" a="0"/></event>\n'
            "</eventposdef>",
        ),
        encoding="utf-8",
    )
    before = eventspawns_target.read_bytes()
    lines: list[str] = []

    removed = strip_all_mod_blocks(eventspawns_target, log=lines.append)

    assert removed == 0
    assert eventspawns_target.read_bytes() == before
    assert any('unterminated "START MOD: @Broken"' in line for line in lines)
    assert any("Restore from backup" in line for line in lines)


@pytest.mark.parametrize(
    "junk",
    [
        "\t<!-- START MOD: @A -->\n" * 1000,
        "\t<!-- START MOD: @A\n" * 5000 + "-->\n",
        "\t<!-- END MOD: @A -->\n" * 1000,
        " " * 100_000 + "<!-- note -->\n",
    ],
    ids=["unmatched-starts", "starts-sharing-one-close", "unmatched-ends", "long-whitespace"],
)
def test_unmatched_markers_are_stripped_in_linear_time(eventspawns_target, junk):
    """The regex this scan replaced took more than a minute on 1000 unmatched
    START markers (and 20s on the second case, 7s on the fourth), because it
    retried each of them against the rest of the file. A mod's XML can carry
    any number of them into the mission."""
    from app.xml.xml_merge import strip_all_mod_blocks

    eventspawns_target.write_text(
        BASE_EVENTSPAWNS.replace(
            "</eventposdef>",
            "\t<!-- START MOD: @Real -->\n"
            + '\t<event name="Real"><pos x="1" z="1" a="0"/></event>\n'
            + "\t<!-- END MOD: @Real -->\n"
            + junk
            + "</eventposdef>",
        ),
        encoding="utf-8",
    )
    lines: list[str] = []

    started = time.perf_counter()
    removed = strip_all_mod_blocks(eventspawns_target, log=lines.append)
    elapsed = time.perf_counter() - started

    assert removed == 1
    assert "@Real" not in eventspawns_target.read_text(encoding="utf-8")
    assert elapsed < 1.0, f"took {elapsed:.2f}s"


@pytest.mark.parametrize(
    "junk",
    ["\t<!-- START MOD: @Real -->\n" * 10_000, " " * 100_000 + "<!-- note -->\n"],
    ids=["unmatched-starts-of-that-mod", "long-whitespace"],
)
def test_removing_one_mods_blocks_runs_in_linear_time(tmp_server_dir, eventspawns_target, junk):
    """Uninstall strips one mod's blocks through the same scan. Its own regex
    took 27s and 7s on these, growing with the square of the file."""
    from app.xml.xml_merge import remove_mod_xml_blocks

    eventspawns_target.write_text(
        BASE_EVENTSPAWNS.replace(
            "</eventposdef>",
            "\t<!-- START MOD: @Real -->\n"
            + '\t<event name="Real"><pos x="1" z="1" a="0"/></event>\n'
            + "\t<!-- END MOD: @Real -->\n"
            + "\t<!-- START MOD: @Other -->\n"
            + '\t<event name="Other"><pos x="2" z="2" a="0"/></event>\n'
            + "\t<!-- END MOD: @Other -->\n"
            + junk
            + "</eventposdef>",
        ),
        encoding="utf-8",
    )

    started = time.perf_counter()
    remove_mod_xml_blocks(tmp_server_dir, MISSION, "@Real")
    elapsed = time.perf_counter() - started

    text = eventspawns_target.read_text(encoding="utf-8")
    assert '"Real"' not in text
    assert '"Other"' in text and text.count("MOD: @Other") == 2
    assert elapsed < 1.0, f"took {elapsed:.2f}s"


def test_a_block_inside_an_unterminated_one_is_stripped_and_the_outer_one_reported(
    eventspawns_target,
):
    """The same-name rule, with the damaged START before a whole block."""
    from app.xml.xml_merge import strip_all_mod_blocks

    eventspawns_target.write_text(
        BASE_EVENTSPAWNS.replace(
            "</eventposdef>",
            "\t<!-- START MOD: @Broken -->\n"
            '\t<event name="Kept"><pos x="9" z="9" a="0"/></event>\n'
            "\t<!-- START MOD: @Whole -->\n"
            '\t<event name="Gone"><pos x="1" z="1" a="0"/></event>\n'
            "\t<!-- END MOD: @Whole -->\n"
            "</eventposdef>",
        ),
        encoding="utf-8",
    )
    lines: list[str] = []

    removed = strip_all_mod_blocks(eventspawns_target, log=lines.append)

    text = eventspawns_target.read_text(encoding="utf-8")
    assert removed == 1
    assert '"Kept"' in text and "START MOD: @Broken" in text
    assert '"Gone"' not in text and "@Whole" not in text
    assert text.endswith('<event name="Kept"><pos x="9" z="9" a="0"/></event>\n</eventposdef>')
    assert any('unterminated "START MOD: @Broken"' in line for line in lines)


def test_free_form_manual_merge_survives_a_full_merge(
    sample_config, tmp_mission_dir, bike_mod
):
    """db/globals.xml is not a merge target, so `merge_xml` never touches it —
    but `apply_direct_files` re-splices the block on every merge anyway."""
    from app.xml import xml_manual

    write_xml(tmp_mission_dir / "cfgeventspawns.xml", BASE_EVENTSPAWNS)
    write_xml(tmp_mission_dir / "cfgeventgroups.xml", "<?xml version='1.0'?><eventgroupdef/>")
    write_xml(
        tmp_mission_dir / "db" / "globals.xml",
        '<?xml version="1.0"?><variables>'
        '<var name="ZombieMaxCount" type="0" value="1000"/></variables>',
    )
    xml_manual.create_file(
        "zeds",
        "merge",
        '<variables><var name="CleanupLifetimeDeadPlayer" type="0" value="3600"/></variables>',
        target="db/globals.xml",
        manual_dir=manual_dir(),
    )

    assert merge_all_direct_configs(
        sample_config.server_dir_path, MISSION, manual_dir=manual_dir(),
    ) == []
    assert merge_all_direct_configs(
        sample_config.server_dir_path, MISSION, manual_dir=manual_dir(),
    ) == []

    text = (tmp_mission_dir / "db" / "globals.xml").read_text(encoding="utf-8")
    assert text.count("START MOD: @user:zeds") == 1
    assert text.count("CleanupLifetimeDeadPlayer") == 1
    assert "ZombieMaxCount" in text


def test_uninstall_strips_merged_blocks(tmp_server_dir, tmp_mission_dir, bike_mod):
    from app.xml.xml_merge import remove_mod_xml_blocks

    target = write_xml(tmp_mission_dir / "cfgeventspawns.xml", BASE_EVENTSPAWNS)
    limits = write_xml(tmp_mission_dir / "cfglimitsdefinitionuser.xml", BASE_LIMITS_USER)
    write_xml(tmp_server_dir / "@Bicycles" / "cfglimitsdefinitionuser.xml", MOD_LIMITS_USER)
    merge_all_direct_configs(tmp_server_dir, MISSION, manual_dir=manual_dir())

    remove_mod_xml_blocks(tmp_server_dir, MISSION, "@Bicycles")

    assert "VehicleHunterz_Old_Bike" not in target.read_text(encoding="utf-8")
    assert "bikeshed" not in limits.read_text(encoding="utf-8")


def test_uninstall_skips_a_mission_file_it_cannot_read(tmp_server_dir, tmp_mission_dir, bike_mod):
    """Every mission *.xml is read, files no merge writes included. A map
    author's Windows-1252 file, or a directory named *.xml, used to stop the
    removal before it reached the file holding the mod's block."""
    from app.xml.xml_merge import remove_mod_xml_blocks

    target = write_xml(tmp_mission_dir / "cfgeventspawns.xml", BASE_EVENTSPAWNS)
    merge_all_direct_configs(tmp_server_dir, MISSION, manual_dir=manual_dir())
    (tmp_mission_dir / "db" / "env.xml").mkdir()
    (tmp_mission_dir / "mapgroupproto.xml").write_bytes(
        "<!-- Map by José -->\n<prototype/>\n".encode("cp1252")
    )
    lines: list[str] = []

    remove_mod_xml_blocks(tmp_server_dir, MISSION, "@Bicycles", log=lines.append)

    assert "VehicleHunterz_Old_Bike" not in target.read_text(encoding="utf-8")
    assert lines == ["Skipped mapgroupproto.xml while removing @Bicycles: not a UTF-8 text file."]


def _versions(path: Path) -> list[str]:
    """Every version Analysis -> Change history holds for *path*, newest first."""
    from app.shared.config_versions import _version_filename, load_index, store_for

    store = store_for(path)
    entries = load_index(store).get_versions(path.name, source_path=str(path))
    return [(store / _version_filename(e)).read_text(encoding="utf-8") for e in entries]


def _hand_edit(target: Path) -> str:
    text = target.read_text(encoding="utf-8").replace(
        "</eventposdef>",
        '\t<event name="MyHandPlaced"><pos x="1" z="2" a="0"/></event>\n</eventposdef>',
    )
    target.write_text(text, encoding="utf-8")
    return text


@pytest.mark.parametrize("scoped", [False, True])
def test_a_merge_keeps_the_file_as_it_was_not_half_merged(
    tmp_server_dir, eventspawns_target, bike_mod, second_mod, scoped
):
    """A merge strips the mods' blocks, then splices them back: two writes.
    Each write snapshotted, so the stripped half became a version, a file
    with every mod block gone that nobody ever had (seen on a mod disable
    in the test container, 2026 Q4). A merge keeps one version per file:
    what it held before the merge. The file the first merge wrote is kept
    too: the hand edit after it is a change made outside Zedstead, whose version
    is that content (config_versions._record_outside)."""
    merge_all_direct_configs(tmp_server_dir, MISSION, manual_dir=manual_dir())
    merged = eventspawns_target.read_text(encoding="utf-8")
    edited = _hand_edit(eventspawns_target)

    if scoped:
        merge_mod_direct_configs(tmp_server_dir, MISSION, "@Bicycles", manual_dir=manual_dir())
    else:
        merge_all_direct_configs(tmp_server_dir, MISSION, manual_dir=manual_dir())

    kept = _versions(eventspawns_target)
    assert kept[0] == edited
    assert set(kept) <= {BASE_EVENTSPAWNS, merged, edited}, "a half-merged file was kept"


def test_an_uninstall_keeps_the_file_it_strips(tmp_server_dir, eventspawns_target, bike_mod):
    from app.xml.xml_merge import remove_mod_xml_blocks

    merge_all_direct_configs(tmp_server_dir, MISSION, manual_dir=manual_dir())
    merged = eventspawns_target.read_text(encoding="utf-8")

    remove_mod_xml_blocks(tmp_server_dir, MISSION, "@Bicycles")

    assert _versions(eventspawns_target)[0] == merged
