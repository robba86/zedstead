"""Tests for cli.globals_xml — parse, save, CRUD, round-trip."""

from pathlib import Path

from tests.conftest import write_xml

from app.xml.globals_xml import (
    add_var,
    get_globals_path,
    parse_globals_file,
    remove_var,
    save_globals_file,
    set_var_type,
    set_var_value,
)


SAMPLE_GLOBALS = """\
<?xml version="1.0" encoding="UTF-8"?>
<variables>
    <var name="ZombieMaxCount" type="0" value="800"/>
    <var name="AnimalMaxCount" type="0" value="200"/>
    <var name="LootDamageMin" type="1" value="0.0"/>
</variables>
"""


def test_get_globals_path(tmp_path: Path):
    db = tmp_path / "db"
    db.mkdir()
    assert get_globals_path(tmp_path) is None
    write_xml(db / "globals.xml", SAMPLE_GLOBALS)
    assert get_globals_path(tmp_path) == db / "globals.xml"


def test_parse_globals_file(tmp_path: Path):
    p = write_xml(tmp_path / "globals.xml", SAMPLE_GLOBALS)
    gf = parse_globals_file(p)
    assert len(gf.entries) == 3
    assert gf.entries[0].name == "ZombieMaxCount"
    assert gf.entries[0].value == "800"
    assert gf.entries[0].type == 0


def test_parse_tolerates_malformed_type_attr(tmp_path: Path):
    # A present-but-empty (type="") or non-numeric type attribute must not
    # abort the whole parse — consumers don't catch ValueError.
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<variables>\n'
        '  <var name="Good" type="0" value="123"/>\n'
        '  <var name="EmptyType" type="" value="1"/>\n'
        '  <var name="TypoType" type="x" value="2"/>\n'
        "</variables>\n"
    )
    p = write_xml(tmp_path / "globals.xml", xml)
    gf = parse_globals_file(p)
    by_name = {e.name: e for e in gf.entries}
    assert len(gf.entries) == 3
    assert by_name["Good"].type == 0
    assert by_name["EmptyType"].type == 0  # malformed -> default
    assert by_name["TypoType"].type == 0
    assert by_name["EmptyType"].value == "1"  # value preserved


def test_set_var_value(tmp_path: Path):
    p = write_xml(tmp_path / "globals.xml", SAMPLE_GLOBALS)
    gf = parse_globals_file(p)
    entry = gf.entries[0]
    set_var_value(entry, "1200")
    assert entry.value == "1200"
    assert entry.element.get("value") == "1200"


def test_set_var_type(tmp_path: Path):
    p = write_xml(tmp_path / "globals.xml", SAMPLE_GLOBALS)
    gf = parse_globals_file(p)
    entry = gf.entries[0]
    set_var_type(entry, 1)
    assert entry.type == 1
    assert entry.element.get("type") == "1"


def test_add_and_remove_var(tmp_path: Path):
    p = write_xml(tmp_path / "globals.xml", SAMPLE_GLOBALS)
    gf = parse_globals_file(p)

    new_entry = add_var(gf, "TestVar", 2, "hello")
    assert len(gf.entries) == 4
    assert new_entry.name == "TestVar"

    remove_var(gf, new_entry)
    assert len(gf.entries) == 3


def test_round_trip(tmp_path: Path):
    p = write_xml(tmp_path / "globals.xml", SAMPLE_GLOBALS)
    gf = parse_globals_file(p)

    set_var_value(gf.entries[0], "999")
    save_globals_file(gf)

    gf2 = parse_globals_file(p)
    assert gf2.entries[0].value == "999"
    assert gf2.entries[1].value == "200"  # unchanged


def test_a_save_keeps_comments_and_merge_markers(tmp_path: Path):
    # A Mod XML link can splice a block into any mission file; the markers
    # are how it is found again.
    p = write_xml(
        tmp_path / "globals.xml",
        "<variables>\n"
        '    <!-- raised for the weekend event -->\n'
        '    <var name="ZombieMaxCount" type="0" value="800"/>\n'
        "    <!-- START MOD: @user:extra -->\n"
        '    <var name="ExtraVar" type="0" value="1"/>\n'
        "    <!-- END MOD: @user:extra -->\n"
        "</variables>\n",
    )
    gf = parse_globals_file(p)
    assert [e.name for e in gf.entries] == ["ZombieMaxCount", "ExtraVar"]
    set_var_value(gf.entries[0], "900")
    save_globals_file(gf)

    content = p.read_text()
    assert 'value="900"' in content
    for comment in ("<!-- raised for the weekend event -->", "<!-- START MOD: @user:extra -->", "<!-- END MOD: @user:extra -->"):
        assert comment in content
