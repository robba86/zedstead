"""Tests for cli.limitsdefinition_xml — parse, CRUD, round-trip."""

import xml.etree.ElementTree as ET
from pathlib import Path

from tests.conftest import CE_1_29, bohemia_ce, ce_block, ce_lines, ce_pick, write_xml

from app.xml.limitsdefinition_xml import (
    add_category,
    add_tag,
    add_usage,
    add_user_group,
    add_value,
    edit_user_group,
    edit_user_group_in_section,
    parse_limitsdefinition,
    parse_limitsdefinitionuser,
    remove_entry,
    remove_user_group,
    save_limitsdefinition,
    save_limitsdefinitionuser,
)


SAMPLE_LIMITSDEFINITION = """\
<?xml version="1.0" encoding="UTF-8"?>
<lists>
    <category name="weapons"/>
    <category name="food"/>
    <tag name="shelves"/>
    <tag name="floor"/>
    <usage name="Military"/>
    <usage name="Town"/>
    <value name="Tier1"/>
    <value name="Tier2"/>
    <value name="Tier3"/>
</lists>
"""


# Shaped like Bohemia's, because the previous fixture was not and that is how
# this file went two years unable to read a real mission. Theirs spells the
# member attribute `name` and roots the document at <user_lists>; this one said
# `user` and <lists>, so every test passed against a file no server ships.
# Now it is Bohemia's own file, read at the pinned 1.29 commit.
def limits_user() -> str:
    """Sakhal's cfglimitsdefinitionuser.xml, its first group of each kind:
    lines 1-7 and 14-20 and the last two. Chernarus' file has the same lines
    with CRLF; Sakhal's are LF, as the tests that use this have always had."""
    lines = ce_lines(bohemia_ce(CE_1_29, "dayzOffline.sakhal/cfglimitsdefinitionuser.xml"))
    return "".join(lines[0:7] + lines[13:20] + lines[-2:])


# The spelling the code used to require. Kept because a mission that already
# has it must not silently lose its groups — nothing writes it any more.
LEGACY_USER_ATTR = """\
<?xml version="1.0" encoding="UTF-8"?>
<user_lists>
    <usageflags>
        <user name="IndustrialZone">
            <usage user="Industrial"/>
            <usage user="Town"/>
        </user>
    </usageflags>
</user_lists>
"""


def test_parse_limitsdefinition(tmp_path: Path):
    p = write_xml(tmp_path / "cfglimitsdefinition.xml", SAMPLE_LIMITSDEFINITION)
    ldf = parse_limitsdefinition(p)
    assert len(ldf.categories) == 2
    assert ldf.categories[0].name == "weapons"
    assert ldf.categories[1].name == "food"
    assert len(ldf.tags) == 2
    assert ldf.tags[0].name == "shelves"
    assert len(ldf.usages) == 2
    assert ldf.usages[0].name == "Military"
    assert len(ldf.values) == 3
    assert ldf.values[2].name == "Tier3"


def test_add_and_remove_category(tmp_path: Path):
    p = write_xml(tmp_path / "cfglimitsdefinition.xml", SAMPLE_LIMITSDEFINITION)
    ldf = parse_limitsdefinition(p)
    assert len(ldf.categories) == 2
    new_cat = add_category(ldf, "tools")
    assert len(ldf.categories) == 3
    assert new_cat.name == "tools"
    remove_entry(ldf, "categories", new_cat)
    assert len(ldf.categories) == 2


def test_add_tag_and_usage_and_value(tmp_path: Path):
    p = write_xml(tmp_path / "cfglimitsdefinition.xml", SAMPLE_LIMITSDEFINITION)
    ldf = parse_limitsdefinition(p)
    new_tag = add_tag(ldf, "ground")
    assert len(ldf.tags) == 3
    new_usage = add_usage(ldf, "Coast")
    assert len(ldf.usages) == 3
    new_value = add_value(ldf, "Tier4")
    assert len(ldf.values) == 4
    assert new_tag.name == "ground"
    assert new_usage.name == "Coast"
    assert new_value.name == "Tier4"


def test_parse_limitsdefinitionuser(tmp_path: Path):
    p = write_xml(tmp_path / "cfglimitsdefinitionuser.xml", limits_user())
    ludf = parse_limitsdefinitionuser(p)
    assert len(ludf.usage_groups) == 1
    assert ludf.usage_groups[0].name == "TownVillage"
    assert ludf.usage_groups[0].members == ["Town", "Village"]
    assert len(ludf.value_groups) == 1
    assert ludf.value_groups[0].name == "Tier12"
    assert ludf.value_groups[0].members == ["Tier1", "Tier2"]


def test_a_group_whose_members_use_the_old_user_attribute_still_reads(tmp_path: Path):
    """Read-only tolerance. The point of the fix is that `name` works, not that
    `user` stops working — a mission carrying the old spelling would otherwise
    have every group silently emptied, which is exactly the bug being fixed."""
    p = write_xml(tmp_path / "cfglimitsdefinitionuser.xml", LEGACY_USER_ATTR)
    ludf = parse_limitsdefinitionuser(p)
    assert ludf.usage_groups[0].members == ["Industrial", "Town"]


def test_a_new_group_is_written_the_way_bohemia_writes_it(tmp_path: Path):
    """`add_user_group` emitted `user=`, so a group added by zsm was one the
    game could not read back — the same bug from the writing side."""
    p = write_xml(tmp_path / "cfglimitsdefinitionuser.xml", limits_user())
    ludf = parse_limitsdefinitionuser(p)
    add_user_group(ludf, "usage", "CoastZone", ["Coast", "Village"])
    save_limitsdefinitionuser(ludf)

    text = p.read_text(encoding="utf-8")
    assert '<usage name="Coast"' in text
    assert "user=" not in text


def test_editing_a_value_group_keeps_it_a_value_group(tmp_path: Path):
    """`edit_user_group` hardcoded the child tag to `usage`, so editing a
    <valueflags> group rewrote its values as usages and the server read a value
    group with no values in it."""
    p = write_xml(tmp_path / "cfglimitsdefinitionuser.xml", limits_user())
    ludf = parse_limitsdefinitionuser(p)
    edit_user_group(ludf.value_groups[0], ["Tier3", "Tier4"])
    save_limitsdefinitionuser(ludf)

    reparsed = parse_limitsdefinitionuser(p)
    assert reparsed.value_groups[0].members == ["Tier3", "Tier4"]
    assert [e.tag for e in reparsed.value_groups[0].element] == ["value", "value"]


def test_add_and_remove_user_group(tmp_path: Path):
    p = write_xml(tmp_path / "cfglimitsdefinitionuser.xml", limits_user())
    ludf = parse_limitsdefinitionuser(p)
    assert len(ludf.usage_groups) == 1
    new_group = add_user_group(ludf, "usage", "CoastZone", ["Coast", "Village"])
    assert len(ludf.usage_groups) == 2
    assert new_group.name == "CoastZone"
    assert new_group.members == ["Coast", "Village"]
    remove_user_group(ludf, "usage", new_group)
    assert len(ludf.usage_groups) == 1


def test_edit_user_group(tmp_path: Path):
    p = write_xml(tmp_path / "cfglimitsdefinitionuser.xml", limits_user())
    ludf = parse_limitsdefinitionuser(p)
    group = ludf.value_groups[0]
    edit_user_group_in_section(group, "value", ["Tier1", "Tier2", "Tier3"])
    assert group.members == ["Tier1", "Tier2", "Tier3"]


_COMMENTED_GROUP = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    "<user_lists>\n"
    "    <valueflags>\n"
    '        <user name="TierCoast">\n'
    "            <!-- coast first -->\n"
    '            <value name="Tier1"/>\n'
    "            <!-- then the towns -->\n"
    '            <value name="Tier2"/>\n'
    "        </user>\n"
    "    </valueflags>\n"
    "</user_lists>\n"
)


def test_changing_a_groups_members_keeps_its_comments(tmp_path: Path):
    p = tmp_path / "cfglimitsdefinitionuser.xml"
    p.write_text(_COMMENTED_GROUP, encoding="utf-8")
    ludf = parse_limitsdefinitionuser(p)
    edit_user_group_in_section(ludf.value_groups[0], "value", ["Tier1", "Tier2", "Tier3"])
    save_limitsdefinitionuser(ludf)
    assert p.read_text(encoding="utf-8") == _COMMENTED_GROUP.replace(
        '            <value name="Tier2"/>\n',
        '            <value name="Tier2"/>\n            <value name="Tier3"/>\n',
    )

    ludf = parse_limitsdefinitionuser(p)
    edit_user_group(ludf.value_groups[0], ["Tier2", "Tier3"])
    save_limitsdefinitionuser(ludf)
    assert p.read_text(encoding="utf-8") == _COMMENTED_GROUP.replace(
        '            <value name="Tier1"/>\n', "",
    ).replace(
        '            <value name="Tier2"/>\n',
        '            <value name="Tier2"/>\n            <value name="Tier3"/>\n',
    )


def test_round_trip_limitsdefinition(tmp_path: Path):
    p = write_xml(tmp_path / "cfglimitsdefinition.xml", SAMPLE_LIMITSDEFINITION)
    ldf = parse_limitsdefinition(p)
    add_category(ldf, "vehicles")
    save_limitsdefinition(ldf)

    ldf2 = parse_limitsdefinition(p)
    cat_names = [c.name for c in ldf2.categories]
    assert "vehicles" in cat_names
    assert "weapons" in cat_names


def test_round_trip_limitsdefinitionuser(tmp_path: Path):
    p = write_xml(tmp_path / "cfglimitsdefinitionuser.xml", limits_user())
    ludf = parse_limitsdefinitionuser(p)
    add_user_group(ludf, "value", "LowTier", ["Tier1"])
    save_limitsdefinitionuser(ludf)

    ludf2 = parse_limitsdefinitionuser(p)
    assert len(ludf2.value_groups) == 2
    assert ludf2.value_groups[1].name == "LowTier"
    assert ludf2.value_groups[1].members == ["Tier1"]


# ---------------------------------------------------------------------------
# Mod-merge markers (2026 Q3)
# ---------------------------------------------------------------------------

MERGED_LIMITSDEFINITION = """\
<?xml version="1.0" encoding="UTF-8"?>
<lists>
    <category name="weapons"/>
    <!-- START MOD: @SomeMod -->
    <category name="modstuff"/>
    <!-- END MOD: @SomeMod -->
</lists>
"""

def merged_limits_user() -> str:
    """Bohemia's Tier12 group in Sakhal's cfglimitsdefinitionuser.xml, read at
    the pinned 1.29 commit, and a mod's group merged in after it (the test's own)."""
    lines = ce_lines(bohemia_ce(CE_1_29, "dayzOffline.sakhal/cfglimitsdefinitionuser.xml"))
    return "".join(
        lines[:2] + ce_pick(lines, "<valueflags>") + ce_block(lines, '<user name="Tier12">')
        + [
            "        <!-- START MOD: @SomeMod -->\n",
            '        <user name="ModTier">\n',
            '            <value name="Tier1" />\n',
            "        </user>\n",
            "        <!-- END MOD: @SomeMod -->\n",
        ]
        + lines[-2:]
    )


def test_a_mod_merge_marker_survives_a_limitsdefinition_save(tmp_path: Path):
    """Both files are direct ALL_MERGE_CONFIGS targets, and
    xml_merge.remove_mod_xml_blocks matches those markers as text. Saving
    without them stranded a mod's entries in the file with nothing able to
    remove them — measured through the live Limits Definition table editor."""
    p = write_xml(tmp_path / "cfglimitsdefinition.xml", MERGED_LIMITSDEFINITION)
    ldf = parse_limitsdefinition(p)
    add_category(ldf, "vehicles")
    save_limitsdefinition(ldf)

    text = p.read_text(encoding="utf-8")
    assert "START MOD: @SomeMod" in text
    assert "END MOD: @SomeMod" in text
    assert [c.name for c in parse_limitsdefinition(p).categories] == [
        "weapons", "modstuff", "vehicles",
    ]


def test_a_mod_merge_marker_survives_a_limitsdefinitionuser_save(tmp_path: Path):
    p = write_xml(tmp_path / "cfglimitsdefinitionuser.xml", merged_limits_user())
    ludf = parse_limitsdefinitionuser(p)
    add_user_group(ludf, "value", "LowTier", ["Tier1"])
    save_limitsdefinitionuser(ludf)

    text = p.read_text(encoding="utf-8")
    assert "START MOD: @SomeMod" in text
    assert "END MOD: @SomeMod" in text


def test_a_comment_inside_a_group_is_not_read_as_a_member(tmp_path: Path):
    """Preserving comments puts them in the tree, where the member scan walks
    every child. A comment has no naming attribute, so it drops out."""
    p = write_xml(tmp_path / "cfglimitsdefinitionuser.xml", merged_limits_user())
    ludf = parse_limitsdefinitionuser(p)
    assert [g.name for g in ludf.value_groups] == ["Tier12", "ModTier"]
    assert ludf.value_groups[0].members == ["Tier1", "Tier2"]


def stock_shaped_limits() -> str:
    """Bohemia's shape: every entry inside its section. Chernarus'
    cfglimitsdefinition.xml, read at the pinned 1.29 commit: each section's
    first entry between the section's own lines."""
    lines = ce_lines(bohemia_ce(CE_1_29, "dayzOffline.chernarusplus/cfglimitsdefinition.xml"))
    return "".join(ce_pick(
        lines, "<?xml", "<lists>",
        "<categories>", 'name="tools"', "</categories>",
        "<tags>", 'name="floor"', "</tags>",
        "<usageflags>", 'name="Military"', "</usageflags>",
        "<valueflags>", 'name="Tier1"', "</valueflags>",
        "</lists>",
    ))


def test_a_new_entry_goes_into_its_section(tmp_path: Path):
    """The root is <lists> itself, so `root.find("lists") or root` put every
    new entry straight under the root, outside the section the game reads."""
    p = write_xml(tmp_path / "cfglimitsdefinition.xml", stock_shaped_limits())
    ldf = parse_limitsdefinition(p)
    add_category(ldf, "vehicles")
    add_tag(ldf, "ground")
    add_usage(ldf, "Coast")
    add_value(ldf, "Tier4")
    save_limitsdefinition(ldf)

    root = ET.parse(p).getroot()
    assert [c.tag for c in root] == ["categories", "tags", "usageflags", "valueflags"]
    assert [e.get("name") for e in root.find("categories")] == ["tools", "vehicles"]
    assert [e.get("name") for e in root.find("tags")] == ["floor", "ground"]
    assert [e.get("name") for e in root.find("usageflags")] == ["Military", "Coast"]
    assert [e.get("name") for e in root.find("valueflags")] == ["Tier1", "Tier4"]


def test_a_missing_section_is_created_in_bohemias_order(tmp_path: Path):
    text = stock_shaped_limits().replace('    <tags>\n        <tag name="floor"/>\n    </tags>\n', "")
    p = write_xml(tmp_path / "cfglimitsdefinition.xml", text)
    ldf = parse_limitsdefinition(p)
    add_tag(ldf, "ground")
    save_limitsdefinition(ldf)

    root = ET.parse(p).getroot()
    assert [c.tag for c in root] == ["categories", "tags", "usageflags", "valueflags"]
    assert [e.get("name") for e in root.find("tags")] == ["ground"]
