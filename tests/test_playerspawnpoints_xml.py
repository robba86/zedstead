"""Tests for cli.playerspawnpoints_xml — parse, CRUD, round-trip."""

from pathlib import Path

from tests.conftest import CE_1_29, bohemia_ce, ce_lines, ce_pick, write_xml

from app.xml.playerspawnpoints_xml import (
    add_group,
    add_pos_to_group,
    parse_spawnpoints_file,
    remove_group,
    remove_pos_from_group,
    save_spawnpoints_file,
    set_generator_param,
    set_group_param,
    set_spawn_param,
    update_pos_in_group,
)


SAMPLE_SPAWNPOINTS = """\
<?xml version="1.0" encoding="UTF-8"?>
<playerspawnpoints>
    <fresh>
        <spawn_params>
            <min_dist_infected>50</min_dist_infected>
            <max_dist_infected>200</max_dist_infected>
            <min_dist_player>100</min_dist_player>
            <max_dist_player>500</max_dist_player>
        </spawn_params>
        <generator_params>
            <grid_density>4</grid_density>
            <grid_width>200</grid_width>
            <grid_height>200</grid_height>
        </generator_params>
        <group_params>
            <enablegroups>true</enablegroups>
            <lifetime>3600</lifetime>
        </group_params>
        <generator_posbubbles>
            <group name="Coast_South">
                <pos x="2500.0" z="2000.0"/>
                <pos x="2600.0" z="2100.0"/>
            </group>
            <group name="Coast_East">
                <pos x="12000.0" z="5000.0"/>
            </group>
        </generator_posbubbles>
    </fresh>
    <hop>
        <spawn_params>
            <min_dist_infected>30</min_dist_infected>
        </spawn_params>
        <generator_params>
            <grid_density>2</grid_density>
        </generator_params>
        <group_params>
            <enablegroups>false</enablegroups>
        </group_params>
    </hop>
</playerspawnpoints>
"""


def test_parse_spawnpoints_file(tmp_path: Path):
    p = write_xml(tmp_path / "cfgPlayerSpawnPoints.xml", SAMPLE_SPAWNPOINTS)
    spf = parse_spawnpoints_file(p)
    assert "fresh" in spf.sections
    assert "hop" in spf.sections
    assert "travel" not in spf.sections

    fresh = spf.sections["fresh"]
    assert fresh.spawn_params.min_dist_infected == "50"
    assert fresh.spawn_params.max_dist_infected == "200"
    assert fresh.spawn_params.min_dist_player == "100"
    assert fresh.generator_params.grid_density == "4"
    assert fresh.generator_params.grid_width == "200"
    assert fresh.group_params.enablegroups == "true"
    assert fresh.group_params.lifetime == "3600"

    assert len(fresh.groups) == 2
    assert fresh.groups[0].name == "Coast_South"
    assert len(fresh.groups[0].positions) == 2
    assert fresh.groups[0].positions[0].x == "2500.0"
    assert fresh.groups[0].positions[0].z == "2000.0"
    assert fresh.groups[1].name == "Coast_East"
    assert len(fresh.groups[1].positions) == 1


def test_set_spawn_param(tmp_path: Path):
    p = write_xml(tmp_path / "cfgPlayerSpawnPoints.xml", SAMPLE_SPAWNPOINTS)
    spf = parse_spawnpoints_file(p)
    section = spf.sections["fresh"]
    set_spawn_param(section, "min_dist_infected", "75")
    assert section.spawn_params.min_dist_infected == "75"


def test_set_generator_param(tmp_path: Path):
    p = write_xml(tmp_path / "cfgPlayerSpawnPoints.xml", SAMPLE_SPAWNPOINTS)
    spf = parse_spawnpoints_file(p)
    section = spf.sections["fresh"]
    set_generator_param(section, "grid_density", "8")
    assert section.generator_params.grid_density == "8"


def test_set_group_param(tmp_path: Path):
    p = write_xml(tmp_path / "cfgPlayerSpawnPoints.xml", SAMPLE_SPAWNPOINTS)
    spf = parse_spawnpoints_file(p)
    section = spf.sections["fresh"]
    set_group_param(section, "enablegroups", "false")
    assert section.group_params.enablegroups == "false"


def test_add_and_remove_group(tmp_path: Path):
    p = write_xml(tmp_path / "cfgPlayerSpawnPoints.xml", SAMPLE_SPAWNPOINTS)
    spf = parse_spawnpoints_file(p)
    section = spf.sections["fresh"]
    assert len(section.groups) == 2
    new_group = add_group(section, "Coast_West")
    assert len(section.groups) == 3
    assert new_group.name == "Coast_West"
    remove_group(section, new_group)
    assert len(section.groups) == 2


def test_add_and_remove_pos(tmp_path: Path):
    p = write_xml(tmp_path / "cfgPlayerSpawnPoints.xml", SAMPLE_SPAWNPOINTS)
    spf = parse_spawnpoints_file(p)
    group = spf.sections["fresh"].groups[0]
    assert len(group.positions) == 2
    new_pos = add_pos_to_group(group, "3000.0", "2500.0")
    assert len(group.positions) == 3
    assert new_pos.x == "3000.0"
    remove_pos_from_group(group, new_pos)
    assert len(group.positions) == 2


def test_update_pos(tmp_path: Path):
    p = write_xml(tmp_path / "cfgPlayerSpawnPoints.xml", SAMPLE_SPAWNPOINTS)
    spf = parse_spawnpoints_file(p)
    pos = spf.sections["fresh"].groups[0].positions[0]
    update_pos_in_group(pos, "9999.0", "8888.0")
    assert pos.x == "9999.0"
    assert pos.z == "8888.0"
    assert pos.element.get("x") == "9999.0"
    assert pos.element.get("z") == "8888.0"


def test_round_trip(tmp_path: Path):
    p = write_xml(tmp_path / "cfgPlayerSpawnPoints.xml", SAMPLE_SPAWNPOINTS)
    spf = parse_spawnpoints_file(p)
    set_spawn_param(spf.sections["fresh"], "min_dist_infected", "999")
    add_pos_to_group(spf.sections["fresh"].groups[0], "5555.0", "6666.0")
    save_spawnpoints_file(spf)

    spf2 = parse_spawnpoints_file(p)
    assert spf2.sections["fresh"].spawn_params.min_dist_infected == "999"
    assert len(spf2.sections["fresh"].groups[0].positions) == 3
    assert spf2.sections["fresh"].groups[0].positions[2].x == "5555.0"


# ---------------------------------------------------------------------------
# A save keeps the file's comments (Sakhal's stock file explains itself in 75)
# ---------------------------------------------------------------------------

def commented() -> str:
    """Lines of Bohemia's Sakhal cfgplayerspawnpoints.xml, read at the pinned
    1.29 commit, two of its comments among them. The declaration and the Coast
    group (one position, one switched off) are the test's own."""
    lines = ce_lines(bohemia_ce(CE_1_29, "dayzOffline.sakhal/cfgplayerspawnpoints.xml"))
    return "".join(
        ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n']
        + ce_pick(lines, "<playerspawnpoints>", "<!--Spawn points settings", "<fresh>", "<spawn_params>",
                  "<!--Minimum distance from infected", "<min_dist_infected>", "</spawn_params>",
                  "<generator_posbubbles>\n")
        + [
            '            <group name="Coast">\n',
            '                <pos x="100.5" z="200.5" />\n',
            '                <!-- <pos x="1.0" z="2.0" /> switched off for the event -->\n',
            '            </group>\n',
        ]
        + ce_pick(lines, "</generator_posbubbles>", "</fresh>", "</playerspawnpoints>")
    )


def test_a_save_keeps_the_comments(tmp_path: Path):
    p = write_xml(tmp_path / "cfgplayerspawnpoints.xml", commented())
    spf = parse_spawnpoints_file(p)
    group = spf.sections["fresh"].groups[0]
    assert [(pos.x, pos.z) for pos in group.positions] == [("100.5", "200.5")]

    update_pos_in_group(group.positions[0], "150.0", "250.0")
    save_spawnpoints_file(spf, snapshot=False)

    content = p.read_text()
    assert '<pos x="150.0" z="250.0" />' in content
    assert "<!--Spawn points settings for newly created player characters-->" in content
    assert "<!--Minimum distance from infected in meters (weight: 2x)-->" in content
    assert '<!-- <pos x="1.0" z="2.0" /> switched off for the event -->' in content


def test_a_save_without_a_change_keeps_sakhal_s_stock_comments(tmp_path: Path):
    from tests.conftest import dayz_docs

    stock = dayz_docs("vanilla", "dayzOffline.sakhal", "cfgplayerspawnpoints.xml")
    p = write_xml(tmp_path / "cfgplayerspawnpoints.xml", stock.read_text(encoding="utf-8-sig"))
    save_spawnpoints_file(parse_spawnpoints_file(p), snapshot=False)
    assert p.read_text().count("<!--") == 75


def test_removing_the_flat_layout_s_group_removes_its_positions(tmp_path: Path):
    # Namalsk lists <pos> straight under generator_posbubbles, with no
    # <group>; the parser shows them as one group named "Spawn Points".
    p = write_xml(
        tmp_path / "cfgplayerspawnpoints.xml",
        "<playerspawnpoints>\n"
        "    <fresh>\n"
        "        <generator_posbubbles>\n"
        '            <group name="Coast">\n'
        '                <pos x="1" z="2"/>\n'
        "            </group>\n"
        '            <pos x="10" z="20"/>\n'
        '            <pos x="30" z="40"/>\n'
        "        </generator_posbubbles>\n"
        "    </fresh>\n"
        "</playerspawnpoints>\n",
    )
    spf = parse_spawnpoints_file(p)
    section = spf.sections["fresh"]
    flat = section.groups[-1]
    assert (flat.name, len(flat.positions)) == ("Spawn Points", 2)

    remove_group(section, flat)
    save_spawnpoints_file(spf)

    again = parse_spawnpoints_file(p).sections["fresh"]
    assert [(g.name, len(g.positions)) for g in again.groups] == [("Coast", 1)]


def test_a_parameter_is_saved_when_the_section_has_no_block_for_it(tmp_path: Path):
    p = write_xml(
        tmp_path / "cfgplayerspawnpoints.xml",
        "<playerspawnpoints>\n"
        "    <fresh>\n"
        "        <spawn_params>\n"
        "            <min_dist_infected>30</min_dist_infected>\n"
        "        </spawn_params>\n"
        "        <generator_posbubbles>\n"
        '            <group name="Coast"><pos x="1" z="2"/></group>\n'
        "        </generator_posbubbles>\n"
        "    </fresh>\n"
        "</playerspawnpoints>\n",
    )
    spf = parse_spawnpoints_file(p)
    set_group_param(spf.sections["fresh"], "lifetime", "240")
    save_spawnpoints_file(spf)

    again = parse_spawnpoints_file(p)
    assert again.sections["fresh"].group_params.lifetime == "240"
    # Written between the other blocks, as Bohemia's file orders them.
    assert [c.tag for c in again.sections["fresh"].element] == [
        "spawn_params", "group_params", "generator_posbubbles",
    ]
