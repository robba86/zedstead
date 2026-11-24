"""Tests for cli.economy_xml — parse, mutations, round-trip."""

from pathlib import Path

from tests.conftest import write_xml

from app.xml.economy_xml import (
    parse_economy,
    save_economy,
    set_system_flags,
)


SAMPLE_ECONOMY = """\
<?xml version="1.0" encoding="UTF-8"?>
<economy>
    <dynamic init="1" load="1" respawn="0" save="1"/>
    <animals init="1" load="1" respawn="1" save="0"/>
    <zombies init="0" load="0" respawn="0" save="0"/>
</economy>
"""


def test_parse_economy(tmp_path: Path):
    p = write_xml(tmp_path / "economy.xml", SAMPLE_ECONOMY)
    ef = parse_economy(p)
    assert len(ef.systems) == 3

    dynamic = ef.systems[0]
    assert dynamic.name == "dynamic"
    assert dynamic.init is True
    assert dynamic.load is True
    assert dynamic.respawn is False
    assert dynamic.save is True

    animals = ef.systems[1]
    assert animals.name == "animals"
    assert animals.respawn is True
    assert animals.save is False

    zombies = ef.systems[2]
    assert zombies.name == "zombies"
    assert zombies.init is False
    assert zombies.load is False


def test_set_system_flags(tmp_path: Path):
    p = write_xml(tmp_path / "economy.xml", SAMPLE_ECONOMY)
    ef = parse_economy(p)
    system = ef.systems[2]  # zombies
    set_system_flags(system, init=True, load=True, respawn=True, save=True)
    assert system.init is True
    assert system.load is True
    assert system.respawn is True
    assert system.save is True
    assert system.element.get("init") == "1"
    assert system.element.get("save") == "1"


def test_flags_str(tmp_path: Path):
    p = write_xml(tmp_path / "economy.xml", SAMPLE_ECONOMY)
    ef = parse_economy(p)
    # Just verify it runs without error and returns a string
    flags = ef.systems[0].flags_str
    assert isinstance(flags, str)
    assert "init" in flags


def test_round_trip(tmp_path: Path):
    p = write_xml(tmp_path / "economy.xml", SAMPLE_ECONOMY)
    ef = parse_economy(p)
    set_system_flags(ef.systems[0], init=False, load=False, respawn=True, save=False)
    save_economy(ef)

    ef2 = parse_economy(p)
    dynamic = ef2.systems[0]
    assert dynamic.init is False
    assert dynamic.load is False
    assert dynamic.respawn is True
    assert dynamic.save is False
    # unchanged systems
    assert ef2.systems[1].respawn is True
    assert ef2.systems[2].init is False


def test_a_comment_is_not_a_system_and_a_save_keeps_it(tmp_path: Path):
    p = write_xml(
        tmp_path / "economy.xml",
        "<economy>\n"
        "    <!-- raised for the weekend event -->\n"
        '    <dynamic init="1" load="1" respawn="0" save="1"/>\n'
        "    <!-- START MOD: @user:extra -->\n"
        '    <extras init="1" load="1" respawn="1" save="1"/>\n'
        "    <!-- END MOD: @user:extra -->\n"
        "</economy>\n",
    )
    ef = parse_economy(p)
    assert [s.name for s in ef.systems] == ["dynamic", "extras"]
    save_economy(ef)

    content = p.read_text()
    for comment in ("<!-- raised for the weekend event -->", "<!-- START MOD: @user:extra -->", "<!-- END MOD: @user:extra -->"):
        assert comment in content
