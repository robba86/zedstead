"""Tests for cli.xml_utils — load, save, backup, restore."""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tests.conftest import CE_1_29, bohemia_ce, ce_block, ce_lines, ce_pick, write_xml

from app.xml.xml_utils import backup_file, load_xml, restore_file, save_xml, validate_xml_file


SIMPLE_XML = '<?xml version="1.0" encoding="UTF-8"?>\n<root><item name="a"/></root>\n'

# External-entity payload: classic XXE that reads a local file. A safe parser
# must refuse to expand the entity rather than resolve &xxe;.
XXE_XML = (
    '<?xml version="1.0"?>\n'
    '<!DOCTYPE root [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>\n'
    "<root><item>&xxe;</item></root>\n"
)

# Billion-laughs: nested entity expansion that blows up memory in naive parsers.
BILLION_LAUGHS = (
    '<?xml version="1.0"?>\n'
    "<!DOCTYPE lolz [\n"
    '  <!ENTITY lol "lol">\n'
    '  <!ENTITY lol2 "&lol;&lol;&lol;&lol;">\n'
    '  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;">\n'
    "]>\n"
    "<root>&lol3;</root>\n"
)


def test_load_xml_rejects_xxe(tmp_path: Path):
    p = write_xml(tmp_path / "xxe.xml", XXE_XML)
    with pytest.raises(ET.ParseError):
        load_xml(p)
    assert validate_xml_file(p) is False


def test_load_xml_rejects_billion_laughs(tmp_path: Path):
    p = write_xml(tmp_path / "lol.xml", BILLION_LAUGHS)
    with pytest.raises(ET.ParseError):
        load_xml(p)


# A Windows-1252 file with a non-ASCII byte, as a mod author's editor saves it.
CP1252_XML = '<?xml version="1.0" encoding="windows-1252"?>\n<doc>café</doc>\n'.encode("cp1252")


def test_a_file_that_is_not_utf8_is_not_valid(tmp_path: Path):
    p = tmp_path / "cp1252.xml"
    p.write_bytes(CP1252_XML)
    assert validate_xml_file(p) is False


def test_a_directory_is_not_a_valid_xml_file(tmp_path: Path):
    (tmp_path / "docs.xml").mkdir()
    assert validate_xml_file(tmp_path / "docs.xml") is False


def test_load_xml(tmp_path: Path):
    p = write_xml(tmp_path / "test.xml", SIMPLE_XML)
    tree = load_xml(p)
    assert tree.getroot().tag == "root"
    assert tree.getroot().find("item").get("name") == "a"


def test_load_xml_with_bom(tmp_path: Path):
    content = "\ufeff" + SIMPLE_XML
    p = write_xml(tmp_path / "test.xml", content)
    tree = load_xml(p)
    assert tree.getroot().tag == "root"


def test_load_xml_with_comment_before_decl(tmp_path: Path):
    content = "<!-- comment -->\n" + SIMPLE_XML
    p = write_xml(tmp_path / "test.xml", content)
    tree = load_xml(p)
    assert tree.getroot().tag == "root"


def test_save_xml_trailing_newline(tmp_path: Path):
    p = write_xml(tmp_path / "test.xml", SIMPLE_XML)
    tree = load_xml(p)
    save_xml(tree, p)
    text = p.read_text()
    assert text.endswith("\n")


def test_save_xml_leaves_no_temp_file(tmp_path: Path):
    p = write_xml(tmp_path / "test.xml", SIMPLE_XML)
    tree = load_xml(p)
    save_xml(tree, p)
    # Atomic write must clean up its temp file (allow the auto-snapshot backup).
    leftovers = [
        f.name for f in tmp_path.iterdir()
        if f.name not in ("test.xml", "test.xml.backup")
    ]
    assert leftovers == []


def test_save_xml_preserves_original_when_write_fails(tmp_path: Path, monkeypatch):
    p = write_xml(tmp_path / "test.xml", SIMPLE_XML)
    original = p.read_text(encoding="utf-8")
    tree = load_xml(p)

    # Simulate a crash during the atomic write.
    monkeypatch.setattr(
        "app.xml.xml_utils.atomic_write_text",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    with pytest.raises(OSError):
        save_xml(tree, p)

    # The live mission XML is intact — not truncated or half-written.
    assert p.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    ("build", "where"),
    [
        (lambda root: ET.SubElement(root, "text").__setattr__("text", "Restart soon\x0b!"), "<text>"),
        (lambda root: ET.SubElement(root, "type", name="Apple\x1c"), 'the "name" attribute of <type>'),
        (lambda root: ET.SubElement(root, "item").__setattr__("tail", "\x00"), "the text after <item>"),
        (lambda root: ET.SubElement(root, "text").__setattr__("text", "\ufffe"), "<text>"),
    ],
)
def test_save_xml_refuses_a_character_xml_cannot_hold(tmp_path: Path, monkeypatch, build, where):
    """ElementTree writes it as it is and the file no longer parses (plans/68
    A-2). Refused before the snapshot and the write."""
    from app.xml.xml_utils import XmlCharError

    snapshots = []
    monkeypatch.setattr("app.xml.xml_utils.snapshot_before_write", snapshots.append)
    p = write_xml(tmp_path / "test.xml", SIMPLE_XML)
    original = p.read_text(encoding="utf-8")
    tree = load_xml(p)
    build(tree.getroot())

    with pytest.raises(XmlCharError, match=f"^{re.escape(where)} contains the character U\\+"):
        save_xml(tree, p)

    assert p.read_text(encoding="utf-8") == original
    assert snapshots == []
    load_xml(p)


def test_save_xml_still_writes_tabs_newlines_and_any_other_text(tmp_path: Path):
    p = write_xml(tmp_path / "test.xml", SIMPLE_XML)
    tree = load_xml(p)
    ET.SubElement(tree.getroot(), "text").text = "a\tb\nc\r d \u00e9 \u2028 \x85 \U0001F600"
    save_xml(tree, p)
    assert load_xml(p).getroot().find("text").text == "a\tb\nc\n d \u00e9 \u2028 \x85 \U0001F600"


def test_backup_and_restore(tmp_path: Path):
    p = write_xml(tmp_path / "test.xml", SIMPLE_XML)
    backup_path = backup_file(p)
    assert backup_path.exists()
    assert backup_path.name == "test.xml.backup"

    # Modify original
    p.write_text("modified")

    # Restore
    assert restore_file(p)
    assert p.read_text().startswith("<?xml")


def test_restore_snapshots_what_it_discards(tmp_path: Path):
    """Restore-from-backup is the one button that throws away an admin's whole
    accumulated edit history in one click, now that merges no longer wipe these
    files themselves (2026 Q3). The version is the undo."""
    from app.shared.config_versions import _read_version_content, load_index, store_for

    p = write_xml(tmp_path / "test.xml", SIMPLE_XML)
    backup_file(p)
    p.write_text("<root><mine/></root>", encoding="utf-8")

    assert restore_file(p)

    store = store_for(p)
    versions = load_index(store).get_versions("test.xml", source_path=str(p))
    assert [v.trigger for v in versions] == ["restore_backup"]
    assert "<mine/>" in _read_version_content(versions[0], store)


def test_restore_no_backup(tmp_path: Path):
    p = tmp_path / "test.xml"
    p.write_text("content")
    assert not restore_file(p)


def test_backup_nonexistent_file(tmp_path: Path):
    p = tmp_path / "missing.xml"
    backup_path = backup_file(p)
    assert not backup_path.exists()


def stock_layouts() -> str:
    """Lines of Bohemia's 1.29 stock files, read at the pinned commit and put
    into one document: Livonia's and Sakhal's cfgspawnabletypes.xml one-line
    cargo groups (from their AmmoBox, here under an SVD), Chernarus'
    cfgrandompresets.xml items two tabs under their <cargo> and its
    `chance= "0.03"`, Livonia's cfgeventspawns.xml with events at four spaces,
    positions at two tabs and the closing tag at one, and Chernarus'
    cfgeventgroups.xml with a child at column 0. The `<randompresets>` wrapper
    lines, the foodHermit line with its `<!--a note -->` and the FUTURE comment
    are zsm's, to test that a comment stays where it was."""
    def read(path: str) -> list[str]:
        return ce_lines(bohemia_ce(CE_1_29, path))

    livonia = read("dayzOffline.enoch/cfgspawnabletypes.xml")
    sakhal = read("dayzOffline.sakhal/cfgspawnabletypes.xml")
    presets = read("dayzOffline.chernarusplus/cfgrandompresets.xml")
    hermit = ce_block(presets, 'name="foodHermit"')
    torches = ce_block(presets, 'name="headtorches"')
    heli = ce_block(read("dayzOffline.enoch/cfgeventspawns.xml"), 'name="StaticHeliCrash"')
    train = ce_block(read("dayzOffline.chernarusplus/cfgeventgroups.xml"), 'name="Train_Abandoned_Cherno"')
    return "".join(
        ce_pick(livonia, "<?xml", "<spawnabletypes>", '<type name="SVD">')
        + ce_pick(livonia, 'name="AmmoBox_9x39_20Rnd" /> </cargo>')
        + ce_pick(sakhal, 'name="AmmoBox_9x39_20Rnd" /></cargo>')
        + ce_pick(livonia, '<type name="SVD">', "</type>")[1:]
        + ["\t<randompresets>\n", '\t\t<cargo chance="0.15" name="foodHermit"> <!--a note -->\n']
        + hermit[1:3] + hermit[-1:]
        + torches[:1] + ce_pick(torches, 'name="Headtorch_Black"') + torches[-1:]
        + ["\t</randompresets>\n"]
        + heli[:3] + heli[-1:]
        + train[:2] + train[-1:]
        + ['\n<!-- FUTURE\n\t<type name="X"/>\n-->\n']
        + ce_pick(livonia, "</spawnabletypes>")
    )


def test_a_save_that_changes_nothing_leaves_the_bytes_and_takes_no_version(tmp_path: Path, monkeypatch):
    """ElementTree cannot write `chance= "0.03"` back; nothing was changed, so
    nothing is written."""
    snapshots = []
    monkeypatch.setattr("app.xml.xml_utils.snapshot_before_write", snapshots.append)
    p = tmp_path / "cfgrandompresets.xml"
    p.write_bytes(stock_layouts().encode())
    save_xml(load_xml(p, preserve_comments=True), p)
    assert p.read_bytes() == stock_layouts().encode()
    assert snapshots == []


def test_an_edit_keeps_the_file_s_own_indents_and_one_line_elements(tmp_path: Path):
    p = tmp_path / "x.xml"
    p.write_bytes(stock_layouts().encode())
    tree = load_xml(p, preserve_comments=True)
    tree.getroot().find("type").set("name", "SVD2")
    save_xml(tree, p)
    after = p.read_text()
    changed = [line for line in after.splitlines() if line not in stock_layouts().splitlines()]
    assert changed == ['\t<type name="SVD2">', '\t\t<attachments chance="0.03" name="headtorches">']


def test_a_child_appended_takes_its_siblings_indent(tmp_path: Path):
    p = tmp_path / "x.xml"
    p.write_bytes(stock_layouts().encode())
    tree = load_xml(p, preserve_comments=True)
    cargo = tree.getroot().find("randompresets/cargo")
    ET.SubElement(cargo, "item", name="Apple", chance="0.07")
    one_line = tree.getroot().find("type").find("cargo")
    ET.SubElement(one_line, "item", name="Ammo_9x39")
    save_xml(tree, p)
    after = p.read_text()
    assert '\t\t\t\t<item name="SardinesCan" chance="0.11" />\n\t\t\t\t<item name="Apple" chance="0.07" />\n\t\t</cargo>' in after
    assert '<cargo chance="0.20"> <item name="AmmoBox_9x39_20Rnd" /> <item name="Ammo_9x39" /></cargo>' in after


@pytest.mark.parametrize("after_root", [
    "\n<!-- kept for 1.30 -->\n",
    "\n<!-- one -->\n<?pi two?>\n\n<!-- three -->",
])
@pytest.mark.parametrize("crlf", [False, True])
def test_what_follows_the_root_element_is_kept(tmp_path: Path, after_root: str, crlf: bool):
    """A comment after the closing root tag is not in the tree, so a save
    dropped it, and a save that changed nothing still rewrote the file."""
    text = '<?xml version="1.0"?>\n<types>\n\t<type name="a"/>\n</types>' + after_root
    if crlf:
        text = text.replace("\n", "\r\n")
    p = tmp_path / "types.xml"
    p.write_bytes(text.encode())

    save_xml(load_xml(p, preserve_comments=True), p, snapshot=False)
    assert p.read_bytes() == text.encode()

    tree = load_xml(p, preserve_comments=True)
    tree.getroot().find("type").set("name", "b")
    save_xml(tree, p, snapshot=False)
    assert p.read_bytes() == text.replace('name="a"', 'name="b"').encode()
