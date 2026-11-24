"""Tests for cli.economycore_xml — parse, CRUD, round-trip."""

from pathlib import Path

from tests.conftest import write_xml

from app.xml.economycore_xml import (
    add_default,
    add_file_to_rootclass,
    add_rootclass,
    parse_economycore_file,
    remove_default,
    remove_file_from_rootclass,
    remove_rootclass,
    save_economycore_file,
    set_default_value,
)


SAMPLE_ECONOMYCORE = """\
<?xml version="1.0" encoding="UTF-8"?>
<economycore>
    <defaults>
        <default name="dyn_radius" value="20"/>
        <default name="save_events_startup" value="true"/>
    </defaults>
    <ce folder="db">
        <file name="types.xml" type="types"/>
        <file name="events.xml" type="events"/>
    </ce>
    <ce folder="custom">
        <file name="extra_types.xml" type="types"/>
    </ce>
</economycore>
"""


def test_parse_economycore_file(tmp_path: Path):
    p = write_xml(tmp_path / "cfgeconomycore.xml", SAMPLE_ECONOMYCORE)
    ef = parse_economycore_file(p)
    assert len(ef.defaults) == 2
    assert ef.defaults[0].name == "dyn_radius"
    assert ef.defaults[0].value == "20"
    assert ef.defaults[1].name == "save_events_startup"

    assert len(ef.rootclasses) == 2
    assert ef.rootclasses[0].folder == "db"
    assert len(ef.rootclasses[0].files) == 2
    assert ef.rootclasses[0].files[0].name == "types.xml"
    assert ef.rootclasses[0].files[0].type == "types"
    assert ef.rootclasses[1].folder == "custom"
    assert len(ef.rootclasses[1].files) == 1


def test_set_default_value(tmp_path: Path):
    p = write_xml(tmp_path / "cfgeconomycore.xml", SAMPLE_ECONOMYCORE)
    ef = parse_economycore_file(p)
    entry = ef.defaults[0]
    set_default_value(entry, "50")
    assert entry.value == "50"
    assert entry.element.get("value") == "50"


def test_add_and_remove_default(tmp_path: Path):
    p = write_xml(tmp_path / "cfgeconomycore.xml", SAMPLE_ECONOMYCORE)
    ef = parse_economycore_file(p)
    assert len(ef.defaults) == 2
    new_default = add_default(ef, "log_ce_loop", "true")
    assert len(ef.defaults) == 3
    assert new_default.name == "log_ce_loop"
    remove_default(ef, new_default)
    assert len(ef.defaults) == 2


def test_add_and_remove_rootclass(tmp_path: Path):
    p = write_xml(tmp_path / "cfgeconomycore.xml", SAMPLE_ECONOMYCORE)
    ef = parse_economycore_file(p)
    assert len(ef.rootclasses) == 2
    new_rc = add_rootclass(ef, "mods")
    assert len(ef.rootclasses) == 3
    assert new_rc.folder == "mods"
    remove_rootclass(ef, new_rc)
    assert len(ef.rootclasses) == 2


def test_add_and_remove_file_from_rootclass(tmp_path: Path):
    p = write_xml(tmp_path / "cfgeconomycore.xml", SAMPLE_ECONOMYCORE)
    ef = parse_economycore_file(p)
    rc = ef.rootclasses[0]
    assert len(rc.files) == 2
    new_file = add_file_to_rootclass(rc, "globals.xml", "globals")
    assert len(rc.files) == 3
    assert new_file.name == "globals.xml"
    assert new_file.type == "globals"
    remove_file_from_rootclass(rc, new_file)
    assert len(rc.files) == 2


def test_round_trip(tmp_path: Path):
    p = write_xml(tmp_path / "cfgeconomycore.xml", SAMPLE_ECONOMYCORE)
    ef = parse_economycore_file(p)
    set_default_value(ef.defaults[0], "100")
    add_rootclass(ef, "newmod")
    save_economycore_file(ef)

    ef2 = parse_economycore_file(p)
    assert ef2.defaults[0].value == "100"
    assert len(ef2.rootclasses) == 3
    assert ef2.rootclasses[2].folder == "newmod"
