"""Tests for cli.types_xml — parse, filter, multiplier, CRUD, round-trip."""

from pathlib import Path

from tests.conftest import write_xml

from app.xml.types_xml import (
    MultiplierConfig,
    apply_multiplier,
    collect_categories,
    filter_entries,
    parse_types_file,
    save_types_files,
    scan_types_files,
    set_category,
    set_flag,
    set_numeric_field,
    set_tag,
    set_usages,
    set_values,
    write_multiplier,
)


SAMPLE_TYPES = """\
<?xml version="1.0" encoding="UTF-8"?>
<types>
    <type name="AKM">
        <nominal>10</nominal>
        <lifetime>14400</lifetime>
        <restock>1800</restock>
        <min>5</min>
        <quantmin>-1</quantmin>
        <quantmax>-1</quantmax>
        <cost>100</cost>
        <flags count_in_cargo="0" count_in_hoarder="0" count_in_map="1" count_in_player="0" crafted="0" deloot="0"/>
        <category name="weapons"/>
        <tag name="shelves"/>
        <usage name="Military"/>
        <value name="Tier3"/>
        <value name="Tier4"/>
    </type>
    <type name="Apple">
        <nominal>50</nominal>
        <lifetime>3600</lifetime>
        <restock>600</restock>
        <min>20</min>
        <quantmin>-1</quantmin>
        <quantmax>-1</quantmax>
        <cost>100</cost>
        <category name="food"/>
        <usage name="Town"/>
    </type>
</types>
"""


def test_scan_types_files(tmp_path: Path):
    db = tmp_path / "db"
    db.mkdir()
    write_xml(db / "types.xml", SAMPLE_TYPES)
    files = scan_types_files(tmp_path)
    assert len(files) == 1
    assert files[0].name == "types.xml"


def test_scan_includes_custom_types(tmp_path: Path):
    db = tmp_path / "db"
    db.mkdir()
    write_xml(db / "types.xml", SAMPLE_TYPES)
    custom = tmp_path / "custom"
    custom.mkdir()
    write_xml(custom / "extra_types.xml", SAMPLE_TYPES)
    files = scan_types_files(tmp_path)
    assert len(files) == 2


def test_scan_skips_spawnabletypes_in_custom(tmp_path: Path):
    """A <spawnabletypes> file is full of <type> elements too; only the root
    tells it apart, and the Types page listed Expansion's as types."""
    db = tmp_path / "db"
    db.mkdir()
    write_xml(db / "types.xml", SAMPLE_TYPES)
    custom = tmp_path / "custom"
    custom.mkdir()
    write_xml(
        custom / "mod-spawnabletypes.xml",
        '<spawnabletypes>\n  <type name="AKM">\n'
        '    <attachments chance="1.00"><item name="Mag_AKM_30Rnd" chance="1.00"/></attachments>\n'
        "  </type>\n</spawnabletypes>\n",
    )
    assert [p.name for p in scan_types_files(tmp_path)] == ["types.xml"]


def test_parse_types_file(tmp_path: Path):
    p = write_xml(tmp_path / "types.xml", SAMPLE_TYPES)
    tf = parse_types_file(p)
    assert len(tf.entries) == 2
    akm = tf.entries[0]
    assert akm.name == "AKM"
    assert akm.nominal == 10
    assert akm.min == 5
    assert akm.lifetime == 14400
    assert akm.restock == 1800
    assert akm.category == "weapons"
    assert akm.tag == "shelves"
    assert akm.flags.get("count_in_map") == 1
    assert "Military" in akm.usages
    assert "Tier3" in akm.values
    assert "Tier4" in akm.values


MALFORMED_TYPES = """\
<?xml version="1.0" encoding="UTF-8"?>
<types>
    <type name="Decimal"><nominal>10.0</nominal><lifetime>3600</lifetime></type>
    <type name="Whitespace"><nominal> </nominal></type>
    <type name="Typo"><min>ten</min></type>
    <type name="BadFlag"><nominal>5</nominal><flags count_in_map="yes"/></type>
    <type name="Good"><nominal>42</nominal><min>7</min></type>
</types>
"""


def test_parse_tolerates_malformed_numeric_values(tmp_path: Path):
    # A non-integer value (decimal, whitespace, typo) in one <type> must not
    # abort the whole parse — consumers (economy analyzer, "What Changed",
    # preset snapshot, spawn editor) don't catch ValueError.
    p = write_xml(tmp_path / "types.xml", MALFORMED_TYPES)
    tf = parse_types_file(p)
    by_name = {e.name: e for e in tf.entries}
    assert len(tf.entries) == 5
    # Malformed numerics fall back to defaults instead of raising.
    assert by_name["Decimal"].nominal == 0
    assert by_name["Whitespace"].nominal == 0
    assert by_name["Typo"].min == 0
    assert by_name["BadFlag"].flags.get("count_in_map") == 0
    # Well-formed entries are unaffected.
    assert by_name["Good"].nominal == 42
    assert by_name["Good"].min == 7


def test_write_multiplier_skips_malformed_value(tmp_path: Path):
    p = write_xml(tmp_path / "types.xml", MALFORMED_TYPES)
    tf = parse_types_file(p)
    decimal = next(e for e in tf.entries if e.name == "Decimal")
    # Multiplying must not raise on the malformed "10.0" nominal text.
    write_multiplier(decimal, MultiplierConfig(nominal=2.0, lifetime=2.0))
    assert decimal.element.find("nominal").text == "10.0"  # left untouched
    assert decimal.element.find("lifetime").text == "7200"  # valid field scaled


def test_collect_categories(tmp_path: Path):
    p = write_xml(tmp_path / "types.xml", SAMPLE_TYPES)
    tf = parse_types_file(p)
    cats = collect_categories([tf])
    assert "weapons" in cats
    assert "food" in cats


def test_filter_entries(tmp_path: Path):
    p = write_xml(tmp_path / "types.xml", SAMPLE_TYPES)
    tf = parse_types_file(p)
    filtered = filter_entries([tf], categories=["weapons"])
    assert len(filtered) == 1
    assert filtered[0].name == "AKM"


def test_filter_entries_all(tmp_path: Path):
    p = write_xml(tmp_path / "types.xml", SAMPLE_TYPES)
    tf = parse_types_file(p)
    all_entries = filter_entries([tf])
    assert len(all_entries) == 2


def test_apply_multiplier(tmp_path: Path):
    p = write_xml(tmp_path / "types.xml", SAMPLE_TYPES)
    tf = parse_types_file(p)
    entry = tf.entries[0]
    mult = MultiplierConfig(nominal=2.0, min=2.0, lifetime=1.0, restock=1.0)
    result = apply_multiplier(entry, mult)
    assert result["nominal"] == (10, 20)
    assert result["min"] == (5, 10)


def test_write_multiplier(tmp_path: Path):
    p = write_xml(tmp_path / "types.xml", SAMPLE_TYPES)
    tf = parse_types_file(p)
    entry = tf.entries[0]
    mult = MultiplierConfig(nominal=3.0)
    write_multiplier(entry, mult)
    assert entry.nominal == 30
    assert entry.element.find("nominal").text == "30"


def test_set_numeric_field(tmp_path: Path):
    p = write_xml(tmp_path / "types.xml", SAMPLE_TYPES)
    tf = parse_types_file(p)
    entry = tf.entries[0]
    set_numeric_field(entry, "nominal", 99)
    assert entry.nominal == 99
    assert entry.element.find("nominal").text == "99"


# A mod's override in custom/ (central-economy-mission-files-modding.wiki:38:
# "not required to define all attributes if you are only overriding").
OVERRIDE_TYPES = """\
<?xml version="1.0" encoding="UTF-8"?>
<types>
    <type name="Apple">
        <nominal>5</nominal>
        <min>2</min>
        <usage name="Town"/>
        <value name="Tier1"/>
    </type>
</types>
"""


def test_a_first_child_goes_where_bohemia_puts_it(tmp_path: Path):
    p = write_xml(tmp_path / "types.xml", OVERRIDE_TYPES)
    tf = parse_types_file(p)
    entry = tf.entries[0]
    set_numeric_field(entry, "lifetime", 3600)
    set_numeric_field(entry, "cost", 100)
    set_flag(entry, "count_in_map", 1)
    set_category(entry, "food")
    set_tag(entry, "shelves")
    set_usages(entry, ["Village"])
    assert [c.tag for c in entry.element] == [
        "nominal", "lifetime", "min", "cost", "flags", "category", "tag", "usage", "value",
    ]
    save_types_files([tf])
    text = p.read_text()
    assert "<lifetime>3600</lifetime>\n        <min>2</min>" in text
    assert '<tag name="shelves" />\n        <usage name="Village" />' in text or (
        '<tag name="shelves"/>\n        <usage name="Village"/>' in text
    )


def test_set_flag(tmp_path: Path):
    p = write_xml(tmp_path / "types.xml", SAMPLE_TYPES)
    tf = parse_types_file(p)
    entry = tf.entries[0]
    set_flag(entry, "count_in_map", 0)
    assert entry.flags["count_in_map"] == 0


def test_set_category(tmp_path: Path):
    p = write_xml(tmp_path / "types.xml", SAMPLE_TYPES)
    tf = parse_types_file(p)
    entry = tf.entries[0]
    set_category(entry, "tools")
    assert entry.category == "tools"
    # Remove category
    set_category(entry, None)
    assert entry.category == ""


def test_set_tag(tmp_path: Path):
    p = write_xml(tmp_path / "types.xml", SAMPLE_TYPES)
    tf = parse_types_file(p)
    entry = tf.entries[0]
    set_tag(entry, "floor")
    assert entry.tag == "floor"
    set_tag(entry, None)
    assert entry.tag == ""


def test_set_usages_and_values(tmp_path: Path):
    p = write_xml(tmp_path / "types.xml", SAMPLE_TYPES)
    tf = parse_types_file(p)
    entry = tf.entries[0]
    set_usages(entry, ["Coast", "Village"])
    assert entry.usages == ["Coast", "Village"]
    set_values(entry, ["Tier1"])
    assert entry.values == ["Tier1"]


def test_round_trip(tmp_path: Path):
    p = write_xml(tmp_path / "types.xml", SAMPLE_TYPES)
    tf = parse_types_file(p)
    set_numeric_field(tf.entries[0], "nominal", 77)
    save_types_files([tf])

    tf2 = parse_types_file(p)
    assert tf2.entries[0].nominal == 77
    assert tf2.entries[1].nominal == 50  # unchanged


# ---------------------------------------------------------------------------
# The types table (ce_tables.types_*) and a file in another encoding
# ---------------------------------------------------------------------------

COMMENTED_TYPES = """\
<types>
    <!-- raised for the weekend event -->
    <type name="AK74">
        <nominal>5</nominal>
        <lifetime>7200</lifetime>
        <restock>0</restock>
        <min>2</min>
        <quantmin>-1</quantmin>
        <quantmax>-1</quantmax>
        <cost>100</cost>
    </type>
    <!-- START MOD: @user:extra -->
    <type name="ExtraGun">
        <nominal>1</nominal>
        <lifetime>7200</lifetime>
        <restock>0</restock>
        <min>1</min>
        <quantmin>-1</quantmin>
        <quantmax>-1</quantmax>
        <cost>100</cost>
    </type>
    <!-- END MOD: @user:extra -->
</types>
"""


def test_a_table_save_keeps_comments_and_merge_markers(tmp_path: Path):
    from app.web.editors.ce_tables import types_load, types_save

    p = write_xml(tmp_path / "types.xml", COMMENTED_TYPES)
    rows = types_load(p)["rows"]
    rows[0]["values"]["nominal"] = 9
    types_save(p, rows)

    content = p.read_text()
    assert "<nominal>9</nominal>" in content
    for comment in ("<!-- raised for the weekend event -->", "<!-- START MOD: @user:extra -->", "<!-- END MOD: @user:extra -->"):
        assert comment in content


def test_the_custom_types_table_refuses_what_the_types_page_refuses(tmp_path: Path):
    import pytest

    from app.web.editors.ce_tables import types_load, types_save

    p = write_xml(tmp_path / "types.xml", SAMPLE_TYPES)
    before = p.read_bytes()
    for key, value, message in (
        ("quantmax", 150, "quantmax is a percentage"),
        ("min", 99, "min (99) cannot be above nominal (10)"),
    ):
        rows = types_load(p)["rows"]
        rows[0]["values"][key] = value
        with pytest.raises(ValueError, match=message.replace("(", r"\(").replace(")", r"\)")):
            types_save(p, rows)
        assert p.read_bytes() == before


def test_type_number_problems_judges_only_what_changes():
    from app.xml.types_xml import type_number_problems

    # Bohemia's own pairs (nominal 0 / min 1 on 805 types, Crossbow_Black's
    # quantmin 80 / quantmax 0) are left alone when untouched.
    stock = {"nominal": 0, "min": 1, "quantmin": 80, "quantmax": 0}
    assert type_number_problems(stock, {"nominal": 0, "min": 1, "quantmin": 80}) == []
    assert type_number_problems(stock, {"lifetime": 5}) == []
    assert type_number_problems({"nominal": 5, "min": 2}, {"min": 6}) == ["min (6) cannot be above nominal (5)"]
    assert type_number_problems({"quantmin": -1, "quantmax": -1}, {"quantmin": 101}) == [
        "quantmin is a percentage: -1 or 0 to 100, not 101"
    ]


def test_a_custom_file_in_another_encoding_is_not_a_types_file(tmp_path: Path):
    from app.xml.types_xml import scan_types_files

    mission = tmp_path / "mission"
    (mission / "db").mkdir(parents=True)
    (mission / "custom").mkdir()
    write_xml(mission / "db" / "types.xml", COMMENTED_TYPES)
    # Saved by an ANSI editor: not UTF-8.
    (mission / "custom" / "mod-types.xml").write_bytes(
        b'<types><!-- caf\xe9 --><type name="X"><nominal>1</nominal></type></types>'
    )
    assert [p.name for p in scan_types_files(mission)] == ["types.xml"]
