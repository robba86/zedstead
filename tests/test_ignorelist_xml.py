"""Tests for cli.ignorelist_xml — parse, CRUD, round-trip."""

from pathlib import Path

from tests.conftest import CE_1_29, bohemia_ce, ce_lines, write_xml

from app.xml.ignorelist_xml import (
    add_entry,
    has_entry,
    parse_ignorelist_file,
    remove_entry,
    save_ignorelist_file,
)


SAMPLE_IGNORELIST = """\
<?xml version="1.0" encoding="UTF-8"?>
<ignore>
    <item name="FenceKit"/>
    <item name="WatchtowerKit"/>
    <type name="TerritoryFlagKit"/>
</ignore>
"""


def test_parse_ignorelist_file(tmp_path: Path):
    p = write_xml(tmp_path / "cfgIgnoreList.xml", SAMPLE_IGNORELIST)
    ilf = parse_ignorelist_file(p)
    assert len(ilf.entries) == 3
    assert ilf.entries[0].name == "FenceKit"
    assert ilf.entries[1].name == "WatchtowerKit"
    assert ilf.entries[2].name == "TerritoryFlagKit"


def test_has_entry(tmp_path: Path):
    p = write_xml(tmp_path / "cfgIgnoreList.xml", SAMPLE_IGNORELIST)
    ilf = parse_ignorelist_file(p)
    assert has_entry(ilf, "FenceKit") is True
    assert has_entry(ilf, "NonExistent") is False


def test_add_and_remove_entry(tmp_path: Path):
    p = write_xml(tmp_path / "cfgIgnoreList.xml", SAMPLE_IGNORELIST)
    ilf = parse_ignorelist_file(p)
    assert len(ilf.entries) == 3
    new_entry = add_entry(ilf, "SeaChest")
    assert len(ilf.entries) == 4
    assert has_entry(ilf, "SeaChest") is True
    assert new_entry.name == "SeaChest"
    remove_entry(ilf, new_entry)
    assert len(ilf.entries) == 3
    assert has_entry(ilf, "SeaChest") is False


def test_round_trip(tmp_path: Path):
    p = write_xml(tmp_path / "cfgIgnoreList.xml", SAMPLE_IGNORELIST)
    ilf = parse_ignorelist_file(p)
    add_entry(ilf, "CombinationLock")
    remove_entry(ilf, ilf.entries[0])  # remove FenceKit
    save_ignorelist_file(ilf)

    ilf2 = parse_ignorelist_file(p)
    names = [e.name for e in ilf2.entries]
    assert "FenceKit" not in names
    assert "CombinationLock" in names
    assert "WatchtowerKit" in names


def test_a_new_entry_is_written_as_the_stock_file_writes_them(tmp_path: Path):
    # Bohemia's three cfgignorelist.xml hold <type name="..."></type> and
    # nothing else; zsm added <item>, a tag no stock file has.
    p = write_xml(
        tmp_path / "cfgignorelist.xml",
        '<ignore>\n    <!-- kept -->\n    <type name="Bandage"></type>\n</ignore>\n',
    )
    ilf = parse_ignorelist_file(p)
    add_entry(ilf, "Rag")
    save_ignorelist_file(ilf)
    content = p.read_text()
    assert '<type name="Rag"' in content
    assert "<item" not in content
    assert "<!-- kept -->" in content


def test_entries_are_listed_in_the_file_s_order(tmp_path: Path):
    p = write_xml(
        tmp_path / "cfgignorelist.xml",
        '<ignore><type name="A"/><item name="B"/><type name="C"/></ignore>',
    )
    assert [e.name for e in parse_ignorelist_file(p).entries] == ["A", "B", "C"]


def test_an_add_and_a_remove_keep_the_stock_file_s_trailing_tab(tmp_path: Path):
    # Bohemia's Chernarus cfgIgnoreList.xml, read at the pinned 1.29 commit,
    # its first two and last three lines: the last entry ends with a tab
    # before the line break, and there is no final newline. Adding after it
    # used to drop the tab.
    lines = ce_lines(bohemia_ce(CE_1_29, "dayzOffline.chernarusplus/cfgIgnoreList.xml"))
    stock = "".join(lines[:2] + lines[-3:])
    p = tmp_path / "cfgignorelist.xml"
    p.write_bytes(stock.encode())
    ilf = parse_ignorelist_file(p)
    add_entry(ilf, "BarbedWire")
    save_ignorelist_file(ilf)
    assert '<type name="Wreck_Mi8"></type>\t\n\t<type name="BarbedWire"></type>\n</ignore>' in p.read_bytes().decode()
    ilf = parse_ignorelist_file(p)
    remove_entry(ilf, next(e for e in ilf.entries if e.name == "BarbedWire"))
    save_ignorelist_file(ilf)
    assert p.read_bytes() == stock.encode()
