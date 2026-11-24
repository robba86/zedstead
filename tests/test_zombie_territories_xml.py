"""Tests for cli.zombie_territories_xml — parse, multiplier, round-trip."""

from pathlib import Path

from tests.conftest import write_xml

from app.xml.zombie_territories_xml import (
    apply_zone_multiplier,
    count_zones,
    parse_zombie_territories,
    save_zombie_territories,
)


SAMPLE_ZOMBIE_TERRITORIES = """\
<?xml version="1.0" encoding="UTF-8"?>
<zombie_territories>
    <territory color="#ff0000">
        <zone name="Elektro" smin="3" smax="8" dmin="1" dmax="3" x="10500.0" z="2200.0" r="200.0"/>
        <zone name="Cherno" smin="5" smax="12" dmin="2" dmax="5" x="6700.0" z="2500.0" r="300.0"/>
    </territory>
    <territory color="#00ff00">
        <zone name="NWAF" smin="10" smax="20" dmin="3" dmax="8" x="4500.0" z="10200.0" r="500.0"/>
    </territory>
</zombie_territories>
"""


def test_parse_zombie_territories(tmp_path: Path):
    p = write_xml(tmp_path / "zombie_territories.xml", SAMPLE_ZOMBIE_TERRITORIES)
    ztf = parse_zombie_territories(p)
    assert len(ztf.territories) == 2

    t0 = ztf.territories[0]
    assert t0.color == "#ff0000"
    assert len(t0.zones) == 2

    elektro = t0.zones[0]
    assert elektro.name == "Elektro"
    assert elektro.smin == 3
    assert elektro.smax == 8
    assert elektro.dmin == 1
    assert elektro.dmax == 3
    assert elektro.x == 10500.0
    assert elektro.z == 2200.0
    assert elektro.r == 200.0

    t1 = ztf.territories[1]
    assert t1.color == "#00ff00"
    assert len(t1.zones) == 1
    assert t1.zones[0].name == "NWAF"


def test_count_zones(tmp_path: Path):
    p = write_xml(tmp_path / "zombie_territories.xml", SAMPLE_ZOMBIE_TERRITORIES)
    ztf = parse_zombie_territories(p)
    assert count_zones(ztf) == 3


def test_apply_zone_multiplier(tmp_path: Path):
    p = write_xml(tmp_path / "zombie_territories.xml", SAMPLE_ZOMBIE_TERRITORIES)
    ztf = parse_zombie_territories(p)
    apply_zone_multiplier(ztf, 2.0)

    elektro = ztf.territories[0].zones[0]
    assert elektro.smin == 6
    assert elektro.smax == 16
    assert elektro.dmin == 2
    assert elektro.dmax == 6
    assert elektro.element.get("smin") == "6"
    assert elektro.element.get("smax") == "16"


def test_apply_zone_multiplier_floors_at_zero(tmp_path: Path):
    p = write_xml(tmp_path / "zombie_territories.xml", SAMPLE_ZOMBIE_TERRITORIES)
    ztf = parse_zombie_territories(p)
    apply_zone_multiplier(ztf, 0.0)

    for terr in ztf.territories:
        for zone in terr.zones:
            assert zone.smin == 0
            assert zone.smax == 0
            assert zone.dmin == 0
            assert zone.dmax == 0


def test_round_trip(tmp_path: Path):
    p = write_xml(tmp_path / "zombie_territories.xml", SAMPLE_ZOMBIE_TERRITORIES)
    ztf = parse_zombie_territories(p)
    apply_zone_multiplier(ztf, 3.0)
    save_zombie_territories(ztf)

    ztf2 = parse_zombie_territories(p)
    nwaf = ztf2.territories[1].zones[0]
    assert nwaf.smin == 30
    assert nwaf.smax == 60
    assert nwaf.dmin == 9
    assert nwaf.dmax == 24
    # First territory unchanged in coordinate data
    assert ztf2.territories[0].zones[0].x == 10500.0


def test_a_count_written_as_a_decimal_or_left_empty_does_not_stop_the_file_loading(tmp_path: Path):
    p = write_xml(
        tmp_path / "zombie_territories.xml",
        '<territory-type><territory color="1">'
        '<zone name="A" smin="2.0" smax="" dmin="x" dmax="6" x="1.0" z="2.0" r="50"/>'
        "</territory></territory-type>",
    )
    ztf = parse_zombie_territories(p)
    zone = ztf.territories[0].zones[0]
    assert (zone.smin, zone.smax, zone.dmin, zone.dmax) == (2, 0, 0, 6)

    apply_zone_multiplier(ztf, 2.0)
    assert zone.element.get("smin") == "4" and zone.element.get("dmax") == "12"


def test_the_multiplier_s_save_keeps_comments_and_merge_markers(tmp_path: Path):
    p = write_xml(
        tmp_path / "zombie_territories.xml",
        "<territory-type>\n"
        "    <!-- raised for the weekend event -->\n"
        '    <territory color="1">\n'
        '        <zone name="A" smin="2" smax="4" dmin="1" dmax="2" x="1.0" z="2.0" r="50"/>\n'
        "    </territory>\n"
        "    <!-- START MOD: @user:extra -->\n"
        '    <territory color="2">\n'
        '        <zone name="B" smin="1" smax="1" dmin="0" dmax="0" x="5.0" z="6.0" r="10"/>\n'
        "    </territory>\n"
        "    <!-- END MOD: @user:extra -->\n"
        "</territory-type>\n",
    )
    ztf = parse_zombie_territories(p)
    apply_zone_multiplier(ztf, 2.0)
    save_zombie_territories(ztf)

    content = p.read_text()
    assert 'smin="4"' in content
    for comment in ("<!-- raised for the weekend event -->", "<!-- START MOD: @user:extra -->", "<!-- END MOD: @user:extra -->"):
        assert comment in content
