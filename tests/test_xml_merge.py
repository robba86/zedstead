"""Characterization tests for the XML merge engine (app/xml/xml_merge.py).

These pin the CURRENT behavior of merge_xml so a later refactor
can be shown to preserve it. Do not change assertions to match a "better"
behavior — assert what the code does today.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.xml.xml_configs import EVENTS_CONFIG
from app.xml.xml_merge import merge_xml
from tests.conftest import manual_dir, write_xml

MISSION = "dayzOffline.chernarusplus"

BASE_EVENTS = """\
<?xml version="1.0" encoding="UTF-8"?>
<events>
    <event name="VanillaEvent"><nominal>10</nominal></event>
</events>"""

MOD_EVENTS = """\
<?xml version="1.0" encoding="UTF-8"?>
<events>
    <event name="ModEvent"><nominal>5</nominal></event>
</events>"""


@pytest.fixture()
def events_target(tmp_mission_dir: Path) -> Path:
    return write_xml(tmp_mission_dir / "db" / "events.xml", BASE_EVENTS)


@pytest.fixture()
def mod_with_events(tmp_server_dir: Path) -> Path:
    mod_dir = tmp_server_dir / "@TestMod"
    write_xml(mod_dir / "events.xml", MOD_EVENTS)
    return mod_dir


def test_merge_xml_inserts_mod_block(tmp_server_dir, events_target, mod_with_events):
    result = merge_xml(tmp_server_dir, MISSION, EVENTS_CONFIG, manual_dir=manual_dir())
    assert result is not None
    rel_target, mod_pairs = result
    assert rel_target == "mpmissions/dayzOffline.chernarusplus/db/events.xml"
    assert any(name == "@TestMod" for name, _ in mod_pairs)
    merged = events_target.read_text(encoding="utf-8")
    assert "START MOD: @TestMod" in merged
    assert "END MOD: @TestMod" in merged
    assert 'name="ModEvent"' in merged
    assert 'name="VanillaEvent"' in merged
    backup = events_target.with_suffix(events_target.suffix + ".backup")
    assert backup.is_file()
    assert 'name="ModEvent"' not in backup.read_text(encoding="utf-8")


def test_merge_xml_is_idempotent(tmp_server_dir, events_target, mod_with_events):
    merge_xml(tmp_server_dir, MISSION, EVENTS_CONFIG, manual_dir=manual_dir())
    merge_xml(tmp_server_dir, MISSION, EVENTS_CONFIG, manual_dir=manual_dir())
    merged = events_target.read_text(encoding="utf-8")
    assert merged.count("START MOD: @TestMod") == 1
    assert merged.count('name="ModEvent"') == 1


def test_merge_xml_returns_none_when_target_missing(tmp_server_dir, mod_with_events):
    result = merge_xml(tmp_server_dir, MISSION, EVENTS_CONFIG, manual_dir=manual_dir())
    assert result is None


def test_one_unreadable_mod_file_does_not_stop_the_merge(
    tmp_server_dir, events_target, mod_with_events
):
    # Another mod ships a non-UTF-8 events file and a directory named like one.
    other = tmp_server_dir / "@Other"
    (other / "docs").mkdir(parents=True)
    (other / "docs" / "events.xml").write_bytes(
        '<events><event name="Café"/></events>\n'.encode("cp1252")
    )
    (other / "events.xml").mkdir()

    result = merge_xml(tmp_server_dir, MISSION, EVENTS_CONFIG, manual_dir=manual_dir())
    assert result is not None
    assert [name for name, _ in result[1]] == ["@TestMod"]
    assert 'name="ModEvent"' in events_target.read_text(encoding="utf-8")


def _nested(depth: int) -> str:
    return "<a>" * depth + "</a>" * depth


def test_a_mod_file_nested_too_deep_is_skipped_and_named(
    tmp_server_dir, events_target, mod_with_events
):
    # Parses fine, and used to raise RecursionError while the merge wrote the
    # mission file, after every mod's block had been stripped from it.
    merge_xml(tmp_server_dir, MISSION, EVENTS_CONFIG, manual_dir=manual_dir())
    write_xml(
        tmp_server_dir / "@Deep" / "events.xml",
        '<events><event name="DeepEvent"><nominal>1</nominal>' + _nested(3000) + "</event></events>",
    )
    lines: list[str] = []

    result = merge_xml(
        tmp_server_dir, MISSION, EVENTS_CONFIG, manual_dir=manual_dir(), log=lines.append,
    )

    assert result is not None
    assert [name for name, _ in result[1]] == ["@TestMod"]
    merged = events_target.read_text(encoding="utf-8")
    assert merged.count("START MOD: @TestMod") == 1
    assert 'name="ModEvent"' in merged
    assert "DeepEvent" not in merged
    assert (
        "Skipped @Deep/events.xml: nested 3002 levels deep, more than the 200 a merge can write."
        in lines
    )


def test_a_target_too_deep_to_write_is_left_as_it_was(
    tmp_server_dir, tmp_mission_dir, mod_with_events
):
    target = write_xml(
        tmp_mission_dir / "db" / "events.xml",
        '<?xml version="1.0" encoding="UTF-8"?>\n<events>\n'
        '<event name="VanillaEvent"><nominal>1</nominal>' + _nested(3000) + "</event>\n"
        "<!-- START MOD: @Old -->\n<event name=\"OldEvent\"/>\n<!-- END MOD: @Old -->\n"
        "</events>\n",
    )
    before = target.read_text(encoding="utf-8")
    lines: list[str] = []

    result = merge_xml(
        tmp_server_dir, MISSION, EVENTS_CONFIG, manual_dir=manual_dir(), log=lines.append,
    )

    assert result is None
    assert target.read_text(encoding="utf-8") == before
    assert any(line.startswith("Error: events.xml is nested too deeply") for line in lines)


def test_merge_xml_returns_none_when_target_invalid(tmp_server_dir, mod_with_events):
    target = tmp_server_dir / "mpmissions" / MISSION / "db" / "events.xml"
    write_xml(target, "<events><event name='Broken'>")  # unclosed
    result = merge_xml(tmp_server_dir, MISSION, EVENTS_CONFIG, manual_dir=manual_dir())
    assert result is None


def test_a_target_no_mod_merges_into_is_no_failure_even_if_unreadable(tmp_server_dir):
    """User test in 2026 Q4: switching to Esseker said "Mod XML could not be
    applied for: cfgeventgroups, cfgrandompresets" although no installed mod
    merges into either; the map's own files are what Zedstead could not read.
    With nothing to merge, a missing or unreadable target is left alone."""
    target = tmp_server_dir / "mpmissions" / MISSION / "db" / "events.xml"
    write_xml(target, "<events><!-- --banner-- --><event name='A'/></events>")
    lines: list[str] = []
    result = merge_xml(tmp_server_dir, MISSION, EVENTS_CONFIG, manual_dir=manual_dir(), log=lines.append)
    assert result is not None and result[1] == []
    assert target.read_text(encoding="utf-8") == "<events><!-- --banner-- --><event name='A'/></events>"
    assert any("Zedstead cannot read it as XML, and no mod merges into it" in line for line in lines)

    target.unlink()
    assert merge_xml(tmp_server_dir, MISSION, EVENTS_CONFIG, manual_dir=manual_dir()) is not None


def test_a_crlf_mission_file_stays_crlf_through_a_re_merge_and_a_removal(
    tmp_server_dir, events_target, mod_with_events,
):
    """The strip before each re-splice read the file in text mode and wrote it
    back with "\\n", so the second merge turned every line of a CRLF file to
    LF, and so did removing a mod."""
    from app.xml.xml_merge import remove_mod_xml_blocks

    events_target.write_bytes(BASE_EVENTS.replace("\n", "\r\n").encode("utf-8") + b"\r\n")
    for _ in range(2):
        merge_xml(tmp_server_dir, MISSION, EVENTS_CONFIG, manual_dir=manual_dir())
        data = events_target.read_bytes()
        assert b"ModEvent" in data
        assert data.count(b"\n") == data.count(b"\r\n")

    remove_mod_xml_blocks(tmp_server_dir, MISSION, "@TestMod")
    data = events_target.read_bytes()
    assert b"ModEvent" not in data
    assert data.count(b"\n") == data.count(b"\r\n")
