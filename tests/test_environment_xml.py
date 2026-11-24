"""Tests for cli.environment_xml — parse, CRUD, round-trip."""

from pathlib import Path

import pytest

from tests.conftest import dayz_docs, write_xml

from app.web.editors.mission_tables import _environment_load, _environment_save
from app.xml.xml_utils import load_xml, save_xml
from app.xml.environment_xml import (
    add_agent,
    add_spawn_to_agent,
    add_territory,
    parse_environment_file,
    remove_agent,
    remove_spawn_from_agent,
    remove_territory,
    save_environment_file,
    set_agent_chance,
    set_territory_population,
)


SAMPLE_ENVIRONMENT = """\
<?xml version="1.0" encoding="UTF-8"?>
<env>
    <territories>
        <file path="env/zombie_territories.xml" usable="1"/>
        <territory type="Herd" name="DeerHerd" behavior="DZDeerGroupBeh">
            <file path="env/deer_territories.xml" usable="1"/>
            <agent type="Deer" chance="0.5">
                <spawn configName="DeerM" chance="0.4"/>
                <spawn configName="DeerF" chance="0.6"/>
            </agent>
            <item name="globalCountMax" val="100"/>
            <item name="zoneCountMin" val="2"/>
            <item name="zoneCountMax" val="5"/>
            <item name="herdsCount" val="30"/>
        </territory>
        <territory type="Ambient" name="HareAmbient" behavior="DZHareGroupBeh">
            <agent type="Hare" chance="1.0"/>
        </territory>
    </territories>
</env>
"""


def test_parse_environment_file(tmp_path: Path):
    p = write_xml(tmp_path / "cfgenvironment.xml", SAMPLE_ENVIRONMENT)
    ef = parse_environment_file(p)
    assert len(ef.file_refs) == 1
    assert ef.file_refs[0].path == "env/zombie_territories.xml"
    assert ef.file_refs[0].usable == "1"

    assert len(ef.territories) == 2
    deer = ef.territories[0]
    assert deer.type == "Herd"
    assert deer.name == "DeerHerd"
    assert deer.behavior == "DZDeerGroupBeh"
    assert len(deer.file_refs) == 1
    assert len(deer.agents) == 1
    assert deer.agents[0].type == "Deer"
    assert deer.agents[0].chance == "0.5"
    assert len(deer.agents[0].spawns) == 2
    assert deer.agents[0].spawns[0].config_name == "DeerM"
    assert deer.agents[0].spawns[0].chance == "0.4"
    assert deer.item is not None
    assert deer.item.global_count_max == "100"
    assert deer.item.zone_count_min == "2"
    assert deer.item.zone_count_max == "5"

    hare = ef.territories[1]
    assert hare.type == "Ambient"
    assert hare.item is None


def test_add_and_remove_territory(tmp_path: Path):
    p = write_xml(tmp_path / "cfgenvironment.xml", SAMPLE_ENVIRONMENT)
    ef = parse_environment_file(p)
    assert len(ef.territories) == 2
    new_terr = add_territory(ef, "Herd", "WolfPack", "DZWolfGroupBeh")
    assert len(ef.territories) == 3
    assert new_terr.name == "WolfPack"
    remove_territory(ef, new_terr)
    assert len(ef.territories) == 2


def test_set_territory_population(tmp_path: Path):
    p = write_xml(tmp_path / "cfgenvironment.xml", SAMPLE_ENVIRONMENT)
    ef = parse_environment_file(p)
    terr = ef.territories[0]
    set_territory_population(terr, "200", "5", "10")
    assert terr.item.global_count_max == "200"
    assert terr.item.zone_count_min == "5"
    assert terr.item.zone_count_max == "10"


def test_set_territory_population_creates_item(tmp_path: Path):
    p = write_xml(tmp_path / "cfgenvironment.xml", SAMPLE_ENVIRONMENT)
    ef = parse_environment_file(p)
    terr = ef.territories[1]  # HareAmbient has no item
    assert terr.item is None
    set_territory_population(terr, "50", "1", "3")
    assert terr.item is not None
    assert terr.item.global_count_max == "50"


def test_add_and_remove_agent(tmp_path: Path):
    p = write_xml(tmp_path / "cfgenvironment.xml", SAMPLE_ENVIRONMENT)
    ef = parse_environment_file(p)
    terr = ef.territories[0]
    assert len(terr.agents) == 1
    new_agent = add_agent(terr, "Wolf", "0.3")
    assert len(terr.agents) == 2
    assert new_agent.type == "Wolf"
    remove_agent(terr, new_agent)
    assert len(terr.agents) == 1


def test_set_agent_chance(tmp_path: Path):
    p = write_xml(tmp_path / "cfgenvironment.xml", SAMPLE_ENVIRONMENT)
    ef = parse_environment_file(p)
    agent = ef.territories[0].agents[0]
    set_agent_chance(agent, "0.9")
    assert agent.chance == "0.9"
    assert agent.element.get("chance") == "0.9"


def test_add_and_remove_spawn(tmp_path: Path):
    p = write_xml(tmp_path / "cfgenvironment.xml", SAMPLE_ENVIRONMENT)
    ef = parse_environment_file(p)
    agent = ef.territories[0].agents[0]
    assert len(agent.spawns) == 2
    new_spawn = add_spawn_to_agent(agent, "DeerBaby", "0.2")
    assert len(agent.spawns) == 3
    assert new_spawn.config_name == "DeerBaby"
    remove_spawn_from_agent(agent, new_spawn)
    assert len(agent.spawns) == 2


def test_round_trip(tmp_path: Path):
    p = write_xml(tmp_path / "cfgenvironment.xml", SAMPLE_ENVIRONMENT)
    ef = parse_environment_file(p)
    set_territory_population(ef.territories[0], "999", "10", "20")
    add_agent(ef.territories[1], "Fox", "0.7")
    save_environment_file(ef)

    ef2 = parse_environment_file(p)
    assert ef2.territories[0].item.global_count_max == "999"
    assert len(ef2.territories[1].agents) == 2
    assert ef2.territories[1].agents[1].type == "Fox"


# ---------------------------------------------------------------------------
# The population limits: one <item name=... val=...> per setting, as Bohemia
# writes them. zsm read and wrote all three as attributes of one <item>.
# ---------------------------------------------------------------------------

# Invented, in the shape of Bohemia's cfgenvironment.xml (plans/86 D6): an
# ambient territory with a comment above it, a Female agent with no chance,
# the three limits as named items and one more item after them, and a herd
# with no population limits.
STOCK_SHAPE = """\
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<env>
    <territories>
        <!-- GOOSE -->
        <territory type="Ambient" name="AmbientGoose" behavior="DZAmbientLifeGroupBeh">
            <file usable="goose_territories" />
            <agent type="Male" chance="1">
                <spawn configName="Animal_TestGoose" chance="1" />
            </agent>
            <agent type="Female">
                <spawn configName="Animal_TestGooseF" chance="1" />
            </agent>
            <item name="globalCountMax" val="40" />
            <item name="zoneCountMin" val="2" />
            <item name="zoneCountMax" val="3" />
            <item name="playerSpawnRadiusNear" val="30" />
        </territory>
        <territory type="Herd" name="Elk" behavior="DZDeerGroupBeh">
            <file usable="elk_territories" />
            <agent type="Male" chance="1" />
            <item name="herdsCount" val="12" />
        </territory>
    </territories>
</env>
"""


def _items(path: Path, territory: str) -> list[dict]:
    root = load_xml(path).getroot()
    terr = next(t for t in root.iter("territory") if t.get("name") == territory)
    return [dict(i.attrib) for i in terr.findall("item")]


def test_the_limits_are_read_from_the_named_items(tmp_path: Path):
    ef = parse_environment_file(write_xml(tmp_path / "cfgenvironment.xml", STOCK_SHAPE))
    goose, elk = ef.territories
    assert (goose.item.global_count_max, goose.item.zone_count_min, goose.item.zone_count_max) == ("40", "2", "3")
    assert elk.item is None


def test_a_limit_is_written_to_the_named_item_and_nowhere_else(tmp_path: Path):
    p = write_xml(tmp_path / "cfgenvironment.xml", STOCK_SHAPE)
    ef = parse_environment_file(p)
    set_territory_population(ef.territories[0], "99", "2", "")
    save_environment_file(ef)
    assert _items(p, "AmbientGoose") == [
        {"name": "globalCountMax", "val": "99"},
        {"name": "zoneCountMin", "val": "2"},
        {"name": "zoneCountMax", "val": "3"},
        {"name": "playerSpawnRadiusNear", "val": "30"},
    ]
    assert "<!-- GOOSE -->" in p.read_text()


def test_a_limit_a_territory_lacks_is_added_as_a_named_item(tmp_path: Path):
    p = write_xml(tmp_path / "cfgenvironment.xml", STOCK_SHAPE)
    ef = parse_environment_file(p)
    set_territory_population(ef.territories[1], "", "2", "")
    save_environment_file(ef)
    assert _items(p, "Elk") == [{"name": "herdsCount", "val": "12"}, {"name": "zoneCountMin", "val": "2"}]


def test_the_form_zsm_used_to_write_is_read_and_moved_on_a_write(tmp_path: Path):
    old = STOCK_SHAPE.replace(
        '<item name="herdsCount" val="12" />',
        '<item name="herdsCount" val="12" />'
        '<item globalCountMax="100" zoneCountMin="2" zoneCountMax="5" />',
    ).replace(
        '<item name="globalCountMax" val="40" />',
        '<item name="globalCountMax" val="40" globalCountMax="99" zoneCountMin="0" zoneCountMax="0" />',
    )
    p = write_xml(tmp_path / "cfgenvironment.xml", old)
    ef = parse_environment_file(p)
    goose, elk = ef.territories
    # The game reads `val`, so that is the value shown.
    assert goose.item.global_count_max == "40"
    assert (elk.item.global_count_max, elk.item.zone_count_min, elk.item.zone_count_max) == ("100", "2", "5")

    set_territory_population(goose, "60", "", "")
    set_territory_population(elk, "", "3", "")
    save_environment_file(ef)
    assert _items(p, "AmbientGoose")[0] == {"name": "globalCountMax", "val": "60"}
    assert _items(p, "Elk") == [
        {"name": "herdsCount", "val": "12"},
        {"name": "globalCountMax", "val": "100"},
        {"name": "zoneCountMin", "val": "3"},
        {"name": "zoneCountMax", "val": "5"},
    ]


def test_the_table_shows_the_limits_and_a_save_without_a_change_writes_nothing(
    sample_config, tmp_mission_dir: Path
):
    p = write_xml(tmp_mission_dir / "cfgenvironment.xml", STOCK_SHAPE)
    save_xml(load_xml(p, preserve_comments=True), p)
    before = p.read_text()

    rows = _environment_load(sample_config)["rows"]
    goose = next(r["values"] for r in rows if r["id"] == "terr:AmbientGoose")
    assert (goose["global_max"], goose["zone_min"], goose["zone_max"]) == ("40", "2", "3")
    elk = next(r["values"] for r in rows if r["id"] == "terr:Elk")
    assert (elk["global_max"], elk["zone_min"], elk["zone_max"]) == ("", "", "")

    _environment_save(sample_config, rows)
    assert p.read_text() == before


def test_the_table_writes_an_edited_limit_where_the_game_reads_it(sample_config, tmp_mission_dir: Path):
    p = write_xml(tmp_mission_dir / "cfgenvironment.xml", STOCK_SHAPE)
    rows = _environment_load(sample_config)["rows"]
    next(r for r in rows if r["id"] == "terr:AmbientGoose")["values"]["global_max"] = "99"
    next(r for r in rows if r["id"] == "agent:AmbientGoose:Female")["values"]["chance"] = "3"
    _environment_save(sample_config, rows)

    assert _items(p, "AmbientGoose")[0] == {"name": "globalCountMax", "val": "99"}
    assert 'agent type="Female" chance="3"' in p.read_text()
    assert _items(p, "Elk") == [{"name": "herdsCount", "val": "12"}]


@pytest.mark.parametrize(
    "mission", ["dayzOffline.chernarusplus", "dayzOffline.enoch", "dayzOffline.sakhal"]
)
def test_a_save_without_a_change_leaves_a_stock_file_as_it_was(
    mission: str, sample_config, tmp_mission_dir: Path
):
    stock = dayz_docs("vanilla", mission, "cfgenvironment.xml")
    p = tmp_mission_dir / "cfgenvironment.xml"
    p.write_text(stock.read_text(encoding="utf-8-sig"), encoding="utf-8")
    save_xml(load_xml(p, preserve_comments=True), p)
    before = p.read_text()
    _environment_save(sample_config, _environment_load(sample_config)["rows"])
    assert p.read_text() == before
