"""Tests for cli.weather_xml — parse, generate, presets, toggles, round-trip."""

from pathlib import Path

from tests.conftest import write_xml

from app.xml.weather_xml import (
    apply_preset,
    generate_weather_xml,
    get_active_elements,
    parse_weather_file,
    save_weather_file,
    set_weather_enabled,
    set_weather_reset,
)


SAMPLE_WEATHER = """\
<?xml version="1.0" encoding="UTF-8"?>
<weather enable="1" reset="0">
    <overcast>
        <current actual="0.45" time="120" duration="240"/>
        <limits min="0.0" max="1.0"/>
        <timelimits min="900" max="1800"/>
        <changelimits min="0.0" max="1.0"/>
    </overcast>
    <fog>
        <current actual="0.1" time="120" duration="240"/>
        <limits min="0.0" max="1.0"/>
        <timelimits min="900" max="1800"/>
        <changelimits min="0.0" max="1.0"/>
    </fog>
    <rain>
        <current actual="0.0" time="120" duration="240"/>
        <limits min="0.0" max="1.0"/>
        <timelimits min="300" max="600"/>
        <changelimits min="0.0" max="1.0"/>
        <thresholds min="0.5" max="1.0" end="120"/>
    </rain>
    <storm density="1.0" threshold="0.7" timeout="25"/>
</weather>
"""


def test_parse_weather_file(tmp_path: Path):
    p = write_xml(tmp_path / "cfgweather.xml", SAMPLE_WEATHER)
    wf = parse_weather_file(p)
    assert wf.enabled is True
    assert wf.reset is False
    assert wf.overcast is not None
    assert wf.overcast.actual == "0.45"
    assert wf.overcast.limits_min == "0.0"
    assert wf.overcast.limits_max == "1.0"
    assert wf.fog is not None
    assert wf.fog.actual == "0.1"
    assert wf.rain is not None
    assert wf.rain.actual == "0.0"
    assert wf.rain.threshold_min == "0.5"
    assert wf.rain.threshold_max == "1.0"
    assert wf.rain.threshold_end == "120"
    assert wf.storm is not None
    assert wf.storm.density == "1.0"
    assert wf.storm.threshold == "0.7"
    assert wf.storm.timeout == "25"
    assert wf.snowfall is None
    assert wf.wind_magnitude is None
    assert wf.wind_direction is None


def test_set_weather_enabled(tmp_path: Path):
    p = write_xml(tmp_path / "cfgweather.xml", SAMPLE_WEATHER)
    wf = parse_weather_file(p)
    set_weather_enabled(wf, False)
    assert wf.enabled is False
    assert wf.tree.getroot().get("enable") == "0"


def test_set_weather_reset(tmp_path: Path):
    p = write_xml(tmp_path / "cfgweather.xml", SAMPLE_WEATHER)
    wf = parse_weather_file(p)
    set_weather_reset(wf, True)
    assert wf.reset is True
    assert wf.tree.getroot().get("reset") == "1"


def test_get_active_elements(tmp_path: Path):
    p = write_xml(tmp_path / "cfgweather.xml", SAMPLE_WEATHER)
    wf = parse_weather_file(p)
    active = get_active_elements(wf)
    assert "overcast" in active
    assert "fog" in active
    assert "rain" in active
    assert "storm" in active
    assert "snowfall" not in active


def test_generate_weather_xml(tmp_path: Path):
    p = tmp_path / "cfgweather.xml"
    wf = generate_weather_xml(
        p,
        elements=["overcast", "windMagnitude"],
        values={"overcast": {"actual": "0.8"}},
    )
    assert wf.overcast is not None
    assert wf.overcast.actual == "0.8"
    assert wf.wind_magnitude is not None
    assert wf.rain is None
    assert wf.storm is None


def test_apply_preset(tmp_path: Path):
    p = tmp_path / "cfgweather.xml"
    wf = apply_preset(p, "Heavy Storm")
    active = get_active_elements(wf)
    assert "overcast" in active
    assert "rain" in active
    assert "windMagnitude" in active
    assert "storm" in active
    assert wf.overcast.actual == "0.9"


def test_a_preset_keeps_weather_switched_off(tmp_path: Path):
    """A preset rebuilds the file but keeps its enable switch: weather that
    was turned off stays off."""
    p = tmp_path / "cfgweather.xml"
    wf = apply_preset(p, "Heavy Storm")
    set_weather_enabled(wf, False)
    save_weather_file(wf)
    assert apply_preset(p, "Clear Skies").enabled is False
    assert parse_weather_file(p).enabled is False


def test_a_preset_on_a_new_file_turns_weather_on(tmp_path: Path):
    assert apply_preset(tmp_path / "cfgweather.xml", "Clear Skies").enabled is True


def test_round_trip(tmp_path: Path):
    p = write_xml(tmp_path / "cfgweather.xml", SAMPLE_WEATHER)
    wf = parse_weather_file(p)
    set_weather_enabled(wf, False)
    set_weather_reset(wf, True)
    save_weather_file(wf)

    wf2 = parse_weather_file(p)
    assert wf2.enabled is False
    assert wf2.reset is True
    assert wf2.overcast is not None
    assert wf2.overcast.actual == "0.45"
    assert wf2.storm.density == "1.0"


# ---------------------------------------------------------------------------
# The Weather form (mission_forms._weather_*): a save reaches the file
# ---------------------------------------------------------------------------

import pytest

from tests.conftest import CE_1_29, bohemia_ce, ce_block, ce_lines, ce_pick, dayz_docs

from app.web.editors.mission_forms import _weather_load, _weather_save
from app.xml.xml_utils import load_xml, save_xml

def stock_shape() -> str:
    """The head of Bohemia's Chernarus cfgweather.xml, read at the pinned 1.29
    commit, comments included: the declaration, the `enable` note, <weather>
    and <overcast> with its first comment, and <rain>'s limits. The rain's
    <current> and the <storm> line are the test's own. LF line ends where the
    file has CRLF, and a final newline the file lacks."""
    lines = ce_lines(bohemia_ce(CE_1_29, "dayzOffline.chernarusplus/cfgweather.xml"))
    overcast = ce_block(lines, "<overcast>")
    rain = ce_block(lines, "<rain>")
    text = "".join(
        ce_pick(lines, "<?xml", "'enable' controls", "<weather ")
        + ce_pick(overcast, "<overcast>", "Initial conditions", "<current", "<limits", "<timelimits",
                  "<changelimits", "</overcast>")
        + ce_pick(rain, "<rain>")
        + ['        <current actual="0.0" time="300" duration="600" />\r\n']
        + ce_pick(rain, "<limits", "</rain>")
        + ['    <storm density="1.0" threshold="0.7" timeout="25"/>\r\n']
        + ce_pick(lines, "</weather>")
    )
    return text.replace("\r\n", "\n") + "\n"


def _form_values(config) -> dict:
    return {
        field["key"]: field["value"]
        for section in _weather_load(config)["sections"]
        for field in section["fields"]
    }


def _weather(tmp_mission_dir: Path, content: str | None = None) -> Path:
    p = write_xml(tmp_mission_dir / "cfgweather.xml", stock_shape() if content is None else content)
    save_xml(load_xml(p, preserve_comments=True), p)
    return p


def test_a_form_save_writes_the_edited_values(sample_config, tmp_mission_dir: Path):
    p = _weather(tmp_mission_dir)
    values = _form_values(sample_config)
    values.update({
        "enabled": True,
        "overcast.actual": "0.8",
        "overcast.timelimits_max": 1200,   # the browser may send a number
        "rain.threshold_min": "0.6",       # the file has no <thresholds> yet
        "storm.timeout": "40",
    })
    _weather_save(sample_config, values)

    root = load_xml(p).getroot()
    assert root.get("enable") == "1"
    assert root.find("overcast/current").get("actual") == "0.8"
    assert root.find("overcast/timelimits").attrib == {"min": "600", "max": "1200"}
    assert root.find("rain/thresholds").get("min") == "0.6"
    assert root.find("storm").attrib == {"density": "1.0", "threshold": "0.7", "timeout": "40"}
    assert _form_values(sample_config)["overcast.actual"] == "0.8"
    assert "<!-- Initial conditions of the overcast" in p.read_text()


def test_a_form_save_without_a_change_leaves_the_file_as_it_was(sample_config, tmp_mission_dir: Path):
    p = _weather(tmp_mission_dir)
    before = p.read_text()
    values = _form_values(sample_config)
    # What a number input sends back for "1.0" and "600".
    values.update({"overcast.limits_max": 1, "overcast.timelimits_min": 600.0})
    _weather_save(sample_config, values)
    assert p.read_text() == before


@pytest.mark.parametrize("bad", ["", "abc", "nan", "inf", None])
def test_a_value_that_is_not_a_number_is_refused_and_nothing_is_written(
    bad, sample_config, tmp_mission_dir: Path
):
    p = _weather(tmp_mission_dir)
    before = p.read_text()
    values = _form_values(sample_config)
    values.update({"overcast.actual": "0.9", "storm.timeout": bad})
    with pytest.raises(ValueError, match="timeout"):
        _weather_save(sample_config, values)
    assert p.read_text() == before


@pytest.mark.parametrize(
    "mission", ["dayzOffline.chernarusplus", "dayzOffline.enoch", "dayzOffline.sakhal"]
)
def test_a_form_save_keeps_a_stock_file_s_comments(mission: str, sample_config, tmp_mission_dir: Path):
    stock = dayz_docs("vanilla", mission, "cfgweather.xml")
    p = _weather(tmp_mission_dir, stock.read_text(encoding="utf-8-sig"))
    before = p.read_text()
    _weather_save(sample_config, _form_values(sample_config))
    assert p.read_text() == before
    # All 31 of Bohemia's, the three above <weather> included: save_xml keeps
    # everything before the root element as the file had it.
    assert before.count("<!--") == 31


# ---------------------------------------------------------------------------
# Element form, ranges, defaults and presets (weather-configuration.wiki)
# ---------------------------------------------------------------------------

from app.xml.weather_xml import (
    DEFAULT_SNOWFALL,
    DEFAULT_WIND_DIRECTION,
    DEFAULT_WIND_MAGNITUDE,
    PARAM_RANGES,
    WEATHER_PRESETS,
    element_param,
    range_problem,
    set_element_param,
)

# The element form the wiki shows (weather-configuration.wiki:96-107), mixed
# with the attribute form as the wiki allows. Not a Bohemia file: the stock
# cfgweather.xml uses attributes only, and these values are the test's own.
ELEMENT_FORM = """\
<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<weather reset="false" enable="true">
    <rain>
        <current actual="0.2" time="60" duration="120" />
        <limits>
            <min>0.2</min>
            <max>0.8</max>
        </limits>
    </rain>
    <storm>
        <density>0.5</density>
        <threshold>0.8</threshold>
        <timeout>30</timeout>
    </storm>
</weather>
"""


def test_a_parameter_written_as_an_element_is_read(tmp_path: Path):
    wf = parse_weather_file(write_xml(tmp_path / "cfgweather.xml", ELEMENT_FORM))
    assert (wf.rain.limits_min, wf.rain.limits_max) == ("0.2", "0.8")
    assert (wf.storm.density, wf.storm.threshold, wf.storm.timeout) == ("0.5", "0.8", "30")


def test_an_edit_keeps_the_form_the_file_uses(tmp_path: Path):
    p = write_xml(tmp_path / "cfgweather.xml", ELEMENT_FORM)
    wf = parse_weather_file(p)
    set_element_param(wf, "rain", "limits_max", "0.9")
    set_element_param(wf, "rain", "actual", "0.3")
    set_element_param(wf, "storm", "timeout", "40")
    set_element_param(wf, "rain", "threshold_min", "0.4")   # absent: attribute form
    save_weather_file(wf)
    root = load_xml(p).getroot()
    limits = root.find("rain/limits")
    assert limits.attrib == {} and limits.find("max").text == "0.9"
    assert root.find("rain/current").get("actual") == "0.3"
    storm = root.find("storm")
    assert storm.attrib == {} and storm.find("timeout").text == "40"
    assert root.find("rain/thresholds").get("min") == "0.4"
    assert parse_weather_file(p).rain.limits_max == "0.9"


def test_a_form_save_of_the_element_form_without_a_change_writes_nothing(
    sample_config, tmp_mission_dir: Path
):
    p = _weather(tmp_mission_dir, ELEMENT_FORM)
    before = p.read_text()
    values = _form_values(sample_config)
    assert values["rain.limits_min"] == "0.2"
    _weather_save(sample_config, values)
    assert p.read_text() == before


_WIND = (
    '</rain><windMagnitude><current actual="8"/><limits min="0" max="20"/></windMagnitude>'
    '<windDirection><current actual="0"/><limits min="-3.14" max="3.14"/></windDirection>'
)


@pytest.mark.parametrize("element,key,value", [
    ("overcast", "actual", "1.5"),
    ("fog", "limits_min", "-0.1"),
    ("rain", "changelimits_max", "2"),
    ("rain", "threshold_min", "1.2"),
    ("storm", "density", "3"),
    ("storm", "threshold", "-1"),
    ("storm", "timeout", "-5"),
    ("overcast", "timelimits_min", "-1"),
    ("windMagnitude", "actual", "-2"),
    ("windDirection", "limits_max", "4"),
])
def test_a_value_outside_its_range_is_refused(element, key, value, tmp_path: Path):
    p = write_xml(tmp_path / "cfgweather.xml", SAMPLE_WEATHER.replace("</rain>", _WIND))
    wf = parse_weather_file(p)
    with pytest.raises(ValueError, match=key):
        set_element_param(wf, element, key, value)


def test_a_value_inside_its_range_is_written(tmp_path: Path):
    p = write_xml(tmp_path / "cfgweather.xml", SAMPLE_WEATHER.replace("</rain>", _WIND))
    wf = parse_weather_file(p)
    set_element_param(wf, "overcast", "actual", "1")
    set_element_param(wf, "storm", "timeout", "0")
    set_element_param(wf, "windMagnitude", "actual", "25")      # m/s, no upper bound
    set_element_param(wf, "windDirection", "limits_max", "3.1415")
    assert wf.tree.getroot().find("overcast/current").get("actual") == "1"


@pytest.mark.parametrize(
    "edit, message",
    [
        ({"overcast.limits_min": "0.9", "overcast.limits_max": "0.2"}, "overcast limits: min 0.9 is above max 0.2"),
        ({"overcast.timelimits_min": "1000"}, "overcast timelimits: min 1000 is above max 900"),  # max stays 900
        ({"overcast.changelimits_max": "-0"}, None),  # equal bounds are a range
        ({"rain.limits_max": "-0.5"}, "outside"),  # the range check comes first
        ({"overcast.changelimits_min": "0.5", "overcast.changelimits_max": "0.3"}, "overcast changelimits"),
    ],
)
def test_a_min_above_its_max_is_refused_and_nothing_is_written(
    edit, message, sample_config, tmp_mission_dir: Path
):
    p = _weather(tmp_mission_dir)
    before = p.read_text()
    values = _form_values(sample_config)
    values.update(edit)
    if message is None:
        _weather_save(sample_config, values)
        return
    with pytest.raises(ValueError, match=message):
        _weather_save(sample_config, values)
    assert p.read_text() == before


def test_both_bounds_moved_past_each_other_s_old_value_are_compared_with_each_other(
    sample_config, tmp_mission_dir: Path
):
    # 600..900 to 1000..1200: the new min is above the old max, and that is fine.
    p = _weather(tmp_mission_dir)
    values = _form_values(sample_config)
    values.update({"overcast.timelimits_min": "1000", "overcast.timelimits_max": "1200"})
    _weather_save(sample_config, values)
    assert load_xml(p).getroot().find("overcast/timelimits").attrib == {"min": "1000", "max": "1200"}


def test_a_pair_the_save_did_not_touch_is_not_checked(sample_config, tmp_mission_dir: Path):
    p = _weather(tmp_mission_dir, stock_shape().replace('<limits min="0.0" max="1.0" />\n        <timelimits',
                                                         '<limits min="0.8" max="0.2" />\n        <timelimits'))
    values = _form_values(sample_config)
    values["overcast.actual"] = "0.5"
    _weather_save(sample_config, values)
    assert load_xml(p).getroot().find("overcast/current").get("actual") == "0.5"


def test_the_form_carries_each_field_s_range(sample_config, tmp_mission_dir: Path):
    _weather(tmp_mission_dir)
    fields = {f["key"]: f for s in _weather_load(sample_config)["sections"] for f in s["fields"]}
    assert (fields["overcast.actual"]["min"], fields["overcast.actual"]["max"]) == (0.0, 1.0)
    assert fields["overcast.time"]["min"] == 0.0 and "max" not in fields["overcast.time"]


def test_the_defaults_are_the_wiki_s_example():
    # weather-configuration.wiki:57-86
    assert DEFAULT_WIND_MAGNITUDE["actual"] == "8.0"
    assert (DEFAULT_WIND_MAGNITUDE["timelimits_min"], DEFAULT_WIND_MAGNITUDE["timelimits_max"]) == ("120", "240")
    assert (DEFAULT_WIND_DIRECTION["changelimits_min"], DEFAULT_WIND_DIRECTION["changelimits_max"]) == ("-1.0", "1.0")
    assert (DEFAULT_WIND_DIRECTION["timelimits_min"], DEFAULT_WIND_DIRECTION["timelimits_max"]) == ("60", "120")
    assert (DEFAULT_SNOWFALL["limits_min"], DEFAULT_SNOWFALL["limits_max"]) == ("0.0", "0.0")


def test_full_mix_does_not_turn_snow_on_and_snowfall_does(tmp_path: Path):
    wf = apply_preset(tmp_path / "a.xml", "Full Mix")
    assert wf.snowfall.limits_max == "0.0"
    wf = apply_preset(tmp_path / "b.xml", "Snowfall")
    assert wf.snowfall.limits_max == "1.0" and float(wf.snowfall.threshold_min) < 1


def test_clear_skies_writes_explicit_clear_values(tmp_path: Path):
    wf = apply_preset(tmp_path / "cfgweather.xml", "Clear Skies")
    assert get_active_elements(wf) == [
        "overcast", "fog", "rain", "snowfall", "windMagnitude", "windDirection", "storm",
    ]
    for channel in (wf.overcast, wf.fog, wf.rain, wf.snowfall):
        assert float(channel.actual) == 0 and float(channel.limits_max) == 0
    assert float(wf.wind_magnitude.limits_max) <= 5


@pytest.mark.parametrize("name", list(WEATHER_PRESETS))
def test_every_preset_is_within_range(name, tmp_path: Path):
    wf = apply_preset(tmp_path / "cfgweather.xml", name)
    for element in get_active_elements(wf):
        for key in PARAM_RANGES[element]:
            value = element_param(wf, element, key)
            if value is not None:
                assert range_problem(element, key, float(value)) is None, (element, key, value)
