"""Weather XML parsing, editing, and backup/restore for cfgweather.xml."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from app.xml.xml_utils import backup_file, load_xml, restore_file, save_xml


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WEATHER_ELEMENTS = [
    "overcast", "fog", "rain", "snowfall",
    "windMagnitude", "windDirection", "storm",
]

# Map XML element names to Python attribute names on WeatherFile
ELEMENT_ATTR: dict[str, str] = {
    "overcast": "overcast",
    "fog": "fog",
    "rain": "rain",
    "snowfall": "snowfall",
    "windMagnitude": "wind_magnitude",
    "windDirection": "wind_direction",
    "storm": "storm",
}

ELEMENT_DESCRIPTIONS: dict[str, str] = {
    "overcast": (
        "Cloud cover level. Range: 0-1. Affects lighting and is the primary driver "
        "for rain and snowfall thresholds."
    ),
    "fog": (
        "Fog density. Range: 0-1. Reduces player and AI visibility."
    ),
    "rain": (
        "Precipitation intensity. Range: 0-1. Restricted by overcast thresholds "
        "-- only occurs when overcast is within the configured threshold range."
    ),
    "snowfall": (
        "Snowfall intensity. Range: 0-1. Like rain, only occurs when overcast is within the configured threshold range. "
        "Vanilla maps disable this on Chernarus and Livonia (limits=0..0); Sakhal is the only vanilla map that ships with snowfall enabled."
    ),
    "windMagnitude": (
        "Wind speed in meters per second. Range: 0-20 m/s."
    ),
    "windDirection": (
        "Wind direction in radians. Range: -3.14 to 3.14 (full circle, -pi to pi)."
    ),
    "storm": (
        "Lightning behavior. Tied to overcast threshold -- lightning only appears when "
        "overcast exceeds the threshold value."
    ),
}

PARAM_DESCRIPTIONS: dict[str, str] = {
    "actual": (
        "Target value the channel animates toward on first transition. Not the literal starting value — "
        "the engine smoothly interpolates from the prior state (or 0 on cold start) over `time` seconds."
    ),
    "time": (
        "Seconds for the first transition (to `actual`). Subsequent transitions pick a duration "
        "randomly between `timelimits min`/`max`."
    ),
    "duration": (
        "How long in seconds the phenomenon stays at the target value once reached."
    ),
    "limits_min": (
        "Hard minimum the channel value can ever reach. The engine never picks targets below this."
    ),
    "limits_max": (
        "Hard maximum the channel value can ever reach. The engine never picks targets above this."
    ),
    "timelimits_min": (
        "Minimum time in seconds for the phenomenon to transition between values."
    ),
    "timelimits_max": (
        "Maximum time in seconds for the phenomenon to transition between values."
    ),
    "changelimits_min": (
        "Minimum delta a single transition can make (same units as the channel — 0-1 for "
        "overcast/fog/rain/snowfall, m/s for windMagnitude, radians for windDirection)."
    ),
    "changelimits_max": (
        "Maximum delta a single transition can make (same units as the channel — 0-1 for "
        "overcast/fog/rain/snowfall, m/s for windMagnitude, radians for windDirection)."
    ),
    "threshold_min": (
        "Lower bound of the overcast range in which rain/snow can occur. Outside this range, "
        "the channel tapers off over `threshold_end` seconds."
    ),
    "threshold_max": (
        "Upper bound of the overcast range in which rain/snow can occur. Outside this range, "
        "the channel tapers off over `threshold_end` seconds."
    ),
    "threshold_end": (
        "Time in seconds for phenomenon to stop when overcast exits the threshold range."
    ),
    "density": (
        "Lightning frequency/intensity multiplier (0-1). Vanilla: 1.0 on all maps. "
        "Lower values reduce strikes; not a literal strikes-per-second number."
    ),
    "threshold": (
        "Overcast value required for lightning to start. Vanilla: 0.9 (Chernarus), 0.7 (Livonia), "
        "0.98 (Sakhal). Lower = lightning during lighter overcast."
    ),
    "timeout": (
        "Seconds between lightning strikes once active. Vanilla: 45s (Chernarus), 20s (Livonia), 45s (Sakhal)."
    ),
}

DEFAULT_OVERCAST = {
    "actual": "0.45", "time": "120", "duration": "240",
    "limits_min": "0.0", "limits_max": "1.0",
    "timelimits_min": "900", "timelimits_max": "1800",
    "changelimits_min": "0.0", "changelimits_max": "1.0",
}

DEFAULT_FOG = {
    "actual": "0.1", "time": "120", "duration": "240",
    "limits_min": "0.0", "limits_max": "1.0",
    "timelimits_min": "900", "timelimits_max": "1800",
    "changelimits_min": "0.0", "changelimits_max": "1.0",
}

DEFAULT_RAIN = {
    "actual": "0.0", "time": "120", "duration": "240",
    "limits_min": "0.0", "limits_max": "1.0",
    "timelimits_min": "300", "timelimits_max": "600",
    "changelimits_min": "0.0", "changelimits_max": "1.0",
    "threshold_min": "0.5", "threshold_max": "1.0", "threshold_end": "120",
}

# The wiki's example (weather-configuration.wiki:76-87) keeps snowfall at 0:
# limits and changelimits 0..0 and a threshold of a full overcast. Every map
# but Sakhal ships that, so a file built from the defaults never snows.
DEFAULT_SNOWFALL = {
    "actual": "0.0", "time": "0", "duration": "32768",
    "limits_min": "0.0", "limits_max": "0.0",
    "timelimits_min": "300", "timelimits_max": "3600",
    "changelimits_min": "0.0", "changelimits_max": "0.0",
    "threshold_min": "1.0", "threshold_max": "1.0", "threshold_end": "120",
}

# Wind as in the wiki's example (:55-74), which every stock map ships.
DEFAULT_WIND_MAGNITUDE = {
    "actual": "8.0", "time": "120", "duration": "240",
    "limits_min": "0.0", "limits_max": "20.0",
    "timelimits_min": "120", "timelimits_max": "240",
    "changelimits_min": "0.0", "changelimits_max": "20.0",
}

DEFAULT_WIND_DIRECTION = {
    "actual": "0.0", "time": "120", "duration": "240",
    "limits_min": "-3.14", "limits_max": "3.14",
    "timelimits_min": "60", "timelimits_max": "120",
    "changelimits_min": "-1.0", "changelimits_max": "1.0",
}

DEFAULT_STORM = {
    "density": "1.0", "threshold": "0.7", "timeout": "25",
}

ELEMENT_DEFAULTS: dict[str, dict[str, str]] = {
    "overcast": DEFAULT_OVERCAST,
    "fog": DEFAULT_FOG,
    "rain": DEFAULT_RAIN,
    "snowfall": DEFAULT_SNOWFALL,
    "windMagnitude": DEFAULT_WIND_MAGNITUDE,
    "windDirection": DEFAULT_WIND_DIRECTION,
    "storm": DEFAULT_STORM,
}

# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

WEATHER_PRESETS: dict[str, dict] = {
    # Every element written out: the wiki does not say what the engine does
    # with an enabled file that leaves one out.
    "Clear Skies": {
        "description": "Clear and calm: no clouds, fog, rain or snow, light wind",
        "elements": ["overcast", "fog", "rain", "snowfall", "windMagnitude", "windDirection", "storm"],
        "overrides": {
            "overcast": {"actual": "0.0", "limits_min": "0.0", "limits_max": "0.0",
                         "changelimits_min": "0.0", "changelimits_max": "0.0"},
            "fog": {"actual": "0.0", "limits_min": "0.0", "limits_max": "0.0",
                    "changelimits_min": "0.0", "changelimits_max": "0.0"},
            "rain": {"actual": "0.0", "limits_min": "0.0", "limits_max": "0.0",
                     "changelimits_min": "0.0", "changelimits_max": "0.0"},
            "windMagnitude": {"actual": "2.0", "limits_min": "0.0", "limits_max": "4.0",
                              "changelimits_min": "0.0", "changelimits_max": "4.0"},
        },
    },
    "Overcast": {
        "description": "Cloudy skies, no precipitation",
        "elements": ["overcast"],
        "overrides": {"overcast": {"actual": "0.6"}},
    },
    "Light Rain": {
        "description": "Overcast with light rain showers",
        "elements": ["overcast", "rain"],
        "overrides": {
            "overcast": {"actual": "0.65"},
            "rain": {"actual": "0.3", "limits_max": "0.5"},
        },
    },
    "Heavy Storm": {
        "description": "Full storm with heavy rain, wind, and lightning",
        "elements": ["overcast", "rain", "windMagnitude", "windDirection", "storm"],
        "overrides": {
            "overcast": {"actual": "0.9", "limits_min": "0.7"},
            "rain": {"actual": "0.8", "limits_min": "0.5"},
            "windMagnitude": {"actual": "15.0", "limits_min": "10.0"},
            "storm": {"density": "1.0", "threshold": "0.6", "timeout": "15"},
        },
    },
    "Foggy": {
        "description": "Dense fog, reduced visibility",
        "elements": ["fog"],
        "overrides": {"fog": {"actual": "0.6", "limits_min": "0.3"}},
    },
    "Foggy Rain": {
        "description": "Overcast with fog and rain",
        "elements": ["overcast", "fog", "rain"],
        "overrides": {
            "overcast": {"actual": "0.7"},
            "fog": {"actual": "0.4"},
            "rain": {"actual": "0.3"},
        },
    },
    "Windy": {
        "description": "Strong wind, no precipitation",
        "elements": ["windMagnitude", "windDirection"],
        "overrides": {"windMagnitude": {"actual": "12.0", "limits_min": "5.0"}},
    },
    # The one preset that turns snow on, with Sakhal's snowfall block.
    "Snowfall": {
        "description": "Overcast with snowfall",
        "elements": ["overcast", "snowfall"],
        "overrides": {
            "overcast": {"actual": "0.7"},
            "snowfall": {
                "actual": "0.5", "time": "60", "duration": "120",
                "limits_min": "0.0", "limits_max": "1.0",
                "timelimits_min": "60", "timelimits_max": "120",
                "changelimits_min": "0.0", "changelimits_max": "1.0",
                "threshold_min": "0.3", "threshold_max": "1.0", "threshold_end": "60",
            },
        },
    },
    "Full Mix": {
        "description": "Every weather element with the wiki's example values (snowfall stays at 0)",
        "elements": ["overcast", "fog", "rain", "snowfall", "windMagnitude", "windDirection", "storm"],
        "overrides": {},
    },
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class WeatherLimits:
    """Shared structure for overcast/fog/windMagnitude/windDirection elements."""
    actual: str = "0.0"
    time: str = "120"
    duration: str = "240"
    limits_min: str = "0.0"
    limits_max: str = "1.0"
    timelimits_min: str = "900"
    timelimits_max: str = "1800"
    changelimits_min: str = "0.0"
    changelimits_max: str = "1.0"


@dataclass
class RainConfig(WeatherLimits):
    """Rain/snowfall extends WeatherLimits with thresholds."""
    threshold_min: str = "0.5"
    threshold_max: str = "1.0"
    threshold_end: str = "120"


@dataclass
class StormConfig:
    """Storm element configuration."""
    density: str = "1.0"
    threshold: str = "0.7"
    timeout: str = "25"


@dataclass
class WeatherFile:
    """Parsed cfgweather.xml file."""
    path: Path
    tree: ET.ElementTree
    enabled: bool = True
    reset: bool = False
    overcast: WeatherLimits | None = None
    fog: WeatherLimits | None = None
    rain: RainConfig | None = None
    snowfall: RainConfig | None = None
    wind_magnitude: WeatherLimits | None = None
    wind_direction: WeatherLimits | None = None
    storm: StormConfig | None = None


# A parameter of an element -> (child tag, attribute). <storm> has no
# children: its three parameters are its own attributes.
_PARAM_XML: dict[str, tuple[str, str]] = {
    "actual": ("current", "actual"),
    "time": ("current", "time"),
    "duration": ("current", "duration"),
    "limits_min": ("limits", "min"),
    "limits_max": ("limits", "max"),
    "timelimits_min": ("timelimits", "min"),
    "timelimits_max": ("timelimits", "max"),
    "changelimits_min": ("changelimits", "min"),
    "changelimits_max": ("changelimits", "max"),
    "threshold_min": ("thresholds", "min"),
    "threshold_max": ("thresholds", "max"),
    "threshold_end": ("thresholds", "end"),
}


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_weather_path(mission_dir: Path) -> Path | None:
    """Return cfgweather.xml if it exists in mission root."""
    p = mission_dir / "cfgweather.xml"
    return p if p.is_file() else None


# ---------------------------------------------------------------------------
# Parse & save
# ---------------------------------------------------------------------------

def _parse_bool_attr(elem: ET.Element, attr: str, default: bool = True) -> bool:
    """Parse boolean attribute (supports 0/1, true/false, yes/no)."""
    val = elem.get(attr, "").lower()
    if val in ("1", "true", "yes"):
        return True
    if val in ("0", "false", "no"):
        return False
    return default


def _param_text(elem: ET.Element | None, name: str, default: str) -> str:
    """One float parameter of `elem`, read as an attribute or as a child element.

    Bohemia's wiki (weather-configuration.wiki, "Everything except for reset and
    enable is a float and can be read either as attribute or element"): both
    `<limits min="0"/>` and `<limits><min>0</min></limits>` are valid, and a
    file may mix them.
    """
    if elem is None:
        return default
    value = elem.get(name)
    if value is not None:
        return value
    child = elem.find(name)
    if child is not None and child.text is not None and child.text.strip():
        return child.text.strip()
    return default


def _write_param(elem: ET.Element, name: str, value: str) -> None:
    """Write one parameter into the form the file already uses for it.

    An attribute stays an attribute and a child element stays an element. A
    parameter the file lacks is added as an element when its parent holds
    only elements (the wiki's element form), else as an attribute.
    """
    if elem.get(name) is not None:
        elem.set(name, value)
        return
    child = elem.find(name)
    if child is None and not elem.attrib and any(isinstance(c.tag, str) for c in elem):
        child = ET.SubElement(elem, name)
    if child is not None:
        child.text = value
    else:
        elem.set(name, value)


def _parse_limits_element(elem: ET.Element) -> WeatherLimits:
    """Parse overcast/fog/windMagnitude/windDirection element into WeatherLimits."""
    wl = WeatherLimits()
    for key, (tag, attr) in _PARAM_XML.items():
        if hasattr(wl, key):
            setattr(wl, key, _param_text(elem.find(tag), attr, getattr(wl, key)))
    return wl


def _parse_rain_element(elem: ET.Element) -> RainConfig:
    """Parse rain/snowfall element into RainConfig."""
    wl = _parse_limits_element(elem)
    rc = RainConfig(**{k: getattr(wl, k) for k in WeatherLimits.__dataclass_fields__})
    thresholds = elem.find("thresholds")
    rc.threshold_min = _param_text(thresholds, "min", rc.threshold_min)
    rc.threshold_max = _param_text(thresholds, "max", rc.threshold_max)
    rc.threshold_end = _param_text(thresholds, "end", rc.threshold_end)
    return rc


def _parse_storm_element(elem: ET.Element) -> StormConfig:
    """Parse storm element into StormConfig."""
    return StormConfig(
        density=_param_text(elem, "density", "1.0"),
        threshold=_param_text(elem, "threshold", "0.7"),
        timeout=_param_text(elem, "timeout", "25"),
    )


def parse_weather_file(path: Path) -> WeatherFile:
    """Parse cfgweather.xml into WeatherFile dataclass."""
    # Bohemia's file explains every element in a comment; a save keeps them.
    tree = load_xml(path, preserve_comments=True)
    root = tree.getroot()

    wf = WeatherFile(
        path=path,
        tree=tree,
        enabled=_parse_bool_attr(root, "enable", True),
        reset=_parse_bool_attr(root, "reset", False),
    )

    overcast_elem = root.find("overcast")
    if overcast_elem is not None:
        wf.overcast = _parse_limits_element(overcast_elem)

    fog_elem = root.find("fog")
    if fog_elem is not None:
        wf.fog = _parse_limits_element(fog_elem)

    rain_elem = root.find("rain")
    if rain_elem is not None:
        wf.rain = _parse_rain_element(rain_elem)

    snowfall_elem = root.find("snowfall")
    if snowfall_elem is not None:
        wf.snowfall = _parse_rain_element(snowfall_elem)

    wind_mag_elem = root.find("windMagnitude")
    if wind_mag_elem is not None:
        wf.wind_magnitude = _parse_limits_element(wind_mag_elem)

    wind_dir_elem = root.find("windDirection")
    if wind_dir_elem is not None:
        wf.wind_direction = _parse_limits_element(wind_dir_elem)

    storm_elem = root.find("storm")
    if storm_elem is not None:
        wf.storm = _parse_storm_element(storm_elem)

    return wf


def save_weather_file(wf: WeatherFile) -> None:
    """Write tree back to disk."""
    save_xml(wf.tree, wf.path)


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def backup_weather_file(path: Path) -> Path:
    """Create .backup copy. Returns backup path."""
    return backup_file(path)


def restore_weather_file(path: Path) -> bool:
    """Restore from .backup. Returns True if restored."""
    return restore_file(path)


# ---------------------------------------------------------------------------
# Generation helpers
# ---------------------------------------------------------------------------

def _build_limits_element(parent: ET.Element, tag: str, values: dict[str, str]) -> ET.Element:
    """Build an overcast/fog/windMagnitude/windDirection/rain/snowfall-style element."""
    elem = ET.SubElement(parent, tag)
    current = ET.SubElement(elem, "current")
    current.set("actual", values.get("actual", "0.0"))
    current.set("time", values.get("time", "120"))
    current.set("duration", values.get("duration", "240"))
    limits = ET.SubElement(elem, "limits")
    limits.set("min", values.get("limits_min", "0.0"))
    limits.set("max", values.get("limits_max", "1.0"))
    timelimits = ET.SubElement(elem, "timelimits")
    timelimits.set("min", values.get("timelimits_min", "900"))
    timelimits.set("max", values.get("timelimits_max", "1800"))
    changelimits = ET.SubElement(elem, "changelimits")
    changelimits.set("min", values.get("changelimits_min", "0.0"))
    changelimits.set("max", values.get("changelimits_max", "1.0"))
    return elem


def _build_rain_element(parent: ET.Element, tag: str, values: dict[str, str]) -> ET.Element:
    """Build rain/snowfall element (limits + thresholds)."""
    elem = _build_limits_element(parent, tag, values)
    thresholds = ET.SubElement(elem, "thresholds")
    thresholds.set("min", values.get("threshold_min", "0.5"))
    thresholds.set("max", values.get("threshold_max", "1.0"))
    thresholds.set("end", values.get("threshold_end", "120"))
    return elem


def _build_storm_element(parent: ET.Element, values: dict[str, str]) -> ET.Element:
    """Build storm element."""
    elem = ET.SubElement(parent, "storm")
    elem.set("density", values.get("density", "1.0"))
    elem.set("threshold", values.get("threshold", "0.7"))
    elem.set("timeout", values.get("timeout", "25"))
    return elem


def generate_weather_xml(
    path: Path,
    elements: list[str],
    values: dict[str, dict[str, str]] | None = None,
) -> WeatherFile:
    """Create cfgweather.xml from scratch with specified elements.

    Args:
        path: Destination file path.
        elements: List of element names to include.
        values: Optional per-element value overrides (merged with defaults).
    """
    values = values or {}
    root = ET.Element("weather")
    root.set("reset", "0")
    root.set("enable", "1")

    for elem_name in WEATHER_ELEMENTS:
        if elem_name not in elements:
            continue
        merged = dict(ELEMENT_DEFAULTS.get(elem_name, {}))
        merged.update(values.get(elem_name, {}))

        if elem_name in ("overcast", "fog", "windMagnitude", "windDirection"):
            _build_limits_element(root, elem_name, merged)
        elif elem_name in ("rain", "snowfall"):
            _build_rain_element(root, elem_name, merged)
        elif elem_name == "storm":
            _build_storm_element(root, merged)

    tree = ET.ElementTree(root)
    save_xml(tree, path)

    return parse_weather_file(path)


def apply_preset(path: Path, preset_name: str) -> WeatherFile:
    """Apply a named preset (generates the file from scratch). The file's
    on/off switch (`enable`) is kept: a preset sets the weather, it does not
    turn weather back on for a server that switched it off. An unreadable old
    file counts as on, like a missing one."""
    preset = WEATHER_PRESETS[preset_name]
    enabled = True
    if path.is_file():
        try:
            enabled = parse_weather_file(path).enabled
        except Exception:  # noqa: BLE001
            enabled = True
    wf = generate_weather_xml(path, preset["elements"], preset.get("overrides"))
    if not enabled:
        set_weather_enabled(wf, False)
        save_weather_file(wf)
    return wf


# ---------------------------------------------------------------------------
# Toggle helpers
# ---------------------------------------------------------------------------

def set_weather_enabled(wf: WeatherFile, enabled: bool) -> None:
    """Toggle enable attribute on root."""
    root = wf.tree.getroot()
    root.set("enable", "1" if enabled else "0")
    wf.enabled = enabled


def set_weather_reset(wf: WeatherFile, reset: bool) -> None:
    """Toggle reset attribute on root."""
    root = wf.tree.getroot()
    root.set("reset", "1" if reset else "0")
    wf.reset = reset


def _finite(text: str) -> float | None:
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# What each parameter can hold, (low, high) with None for no bound. From
# weather-configuration.wiki: overcast, fog, rain and snowfall are 0..1
# (:26/36/46/79, their changelimits :30/40/50/83), the rain and snowfall
# thresholds and the storm's density and threshold are overcast values
# (:52, :88), the wind magnitude is in m/s (:58) and the wind direction an
# angle in radians, -3.14..3.14 in the wiki (:68; pi itself is let through).
# Every time is in seconds and cannot be negative. A wind change limit is a
# signed delta (Bohemia's windDirection uses -1..1), so only finite.
_UNIT = (0.0, 1.0)
_SECONDS = (0.0, None)
_TIME_KEYS = ("time", "duration", "timelimits_min", "timelimits_max", "threshold_end")
_FRACTION_KEYS = ("actual", "limits_min", "limits_max", "changelimits_min",
                  "changelimits_max", "threshold_min", "threshold_max")
PARAM_RANGES: dict[str, dict[str, tuple[float | None, float | None]]] = {
    **{
        name: {**{k: _UNIT for k in _FRACTION_KEYS}, **{k: _SECONDS for k in _TIME_KEYS}}
        for name in ("overcast", "fog", "rain", "snowfall")
    },
    "windMagnitude": {
        "actual": (0.0, None), "limits_min": (0.0, None), "limits_max": (0.0, None),
        **{k: _SECONDS for k in _TIME_KEYS},
    },
    "windDirection": {
        "actual": (-math.pi, math.pi), "limits_min": (-math.pi, math.pi),
        "limits_max": (-math.pi, math.pi),
        **{k: _SECONDS for k in _TIME_KEYS},
    },
    "storm": {"density": _UNIT, "threshold": _UNIT, "timeout": _SECONDS},
}


def _bound_text(number: float) -> str:
    return f"{number:.2f}" if abs(number) == math.pi else f"{number:g}"


def range_problem(element: str, key: str, number: float) -> str | None:
    """Why `number` cannot be `element`'s `key`, or None when it can."""
    low, high = PARAM_RANGES.get(element, {}).get(key, (None, None))
    if low is not None and high is not None and not low <= number <= high:
        return f"{number:g} is outside {_bound_text(low)}..{_bound_text(high)}"
    if low is not None and number < low:
        return f"{number:g} is below {_bound_text(low)}"
    if high is not None and number > high:
        return f"{number:g} is above {_bound_text(high)}"
    return None


def element_param(wf: WeatherFile, element: str, key: str) -> str | None:
    """The current value of one parameter, None when the file has no such
    element or the element no such parameter."""
    obj = getattr(wf, ELEMENT_ATTR.get(element, ""), None)
    if obj is None or key not in obj.__dataclass_fields__:
        return None
    return getattr(obj, key)


def set_element_param(wf: WeatherFile, element: str, key: str, value: str) -> bool:
    """Write one parameter of a weather element into the tree.

    The same number as the file has (`1` for `1.0`) is left as written, so a
    save rewrites only what changed. Raises ValueError for a value that is
    not a finite number or is outside `PARAM_RANGES`; an unknown element or
    parameter is ignored. A value the file already holds is not checked, so
    a file written out of range still saves its other fields. Returns True
    when the value was written.
    """
    current = element_param(wf, element, key)
    if current is None:
        return False
    number = _finite(value)
    if number is None:
        raise ValueError(f"{element} {key}: {value!r} is not a number")
    if number == _finite(current):
        return False

    problem = range_problem(element, key, number)
    if problem:
        raise ValueError(f"{element} {key}: {problem}")

    elem = wf.tree.getroot().find(element)
    if element == "storm":
        _write_param(elem, key, value)
    else:
        tag, attr = _PARAM_XML[key]
        child = elem.find(tag)
        if child is None:
            child = ET.SubElement(elem, tag)
        _write_param(child, attr, value)
    setattr(getattr(wf, ELEMENT_ATTR[element]), key, value)
    return True


#: The min/max pairs a save keeps in order: a range whose min is above its
#: max has no value in it.
ORDERED_PAIRS = ("limits", "timelimits", "changelimits")


def check_ordered_pairs(wf: WeatherFile, edited: set[tuple[str, str]]) -> None:
    """Raise ValueError when an edited min/max pair has its min above its max.

    Run after every edit is in, so a pair edited on both sides is compared
    with itself, and a pair edited on one side with the other side's current
    value. A pair no edit touched is not checked: a file written that way by
    hand still saves its other fields."""
    for element, key in sorted(edited):
        pair = key.rsplit("_", 1)[0]
        if pair not in ORDERED_PAIRS or key not in (f"{pair}_min", f"{pair}_max"):
            continue
        low = _finite(element_param(wf, element, f"{pair}_min") or "")
        high = _finite(element_param(wf, element, f"{pair}_max") or "")
        if low is not None and high is not None and low > high:
            raise ValueError(f"{element} {pair}: min {low:g} is above max {high:g}")


# ---------------------------------------------------------------------------
# Introspection helpers
# ---------------------------------------------------------------------------

def get_active_elements(wf: WeatherFile) -> list[str]:
    """Return which weather elements are present in the file."""
    active = []
    for name in WEATHER_ELEMENTS:
        attr = ELEMENT_ATTR[name]
        if getattr(wf, attr) is not None:
            active.append(name)
    return active


def get_element_config_items(
    wf: WeatherFile, element: str,
) -> list[tuple[str, str, str, str]]:
    """Return ConfigModal-compatible items for an element's parameters.

    Returns list of (label, key, kind, current_value) tuples.
    """
    attr = ELEMENT_ATTR.get(element, element)
    obj = getattr(wf, attr, None)
    if obj is None:
        return []

    if element in ("overcast", "fog"):
        return [
            ("Initial value (0-1)", "actual", "float", obj.actual),
            ("Change time (s)", "time", "float", obj.time),
            ("Hold duration (s)", "duration", "float", obj.duration),
            ("Range min (0-1)", "limits_min", "float", obj.limits_min),
            ("Range max (0-1)", "limits_max", "float", obj.limits_max),
            ("Time min (s)", "timelimits_min", "float", obj.timelimits_min),
            ("Time max (s)", "timelimits_max", "float", obj.timelimits_max),
            ("Change min (0-1)", "changelimits_min", "float", obj.changelimits_min),
            ("Change max (0-1)", "changelimits_max", "float", obj.changelimits_max),
        ]

    if element in ("rain", "snowfall"):
        return [
            ("Initial value (0-1)", "actual", "float", obj.actual),
            ("Change time (s)", "time", "float", obj.time),
            ("Hold duration (s)", "duration", "float", obj.duration),
            ("Range min (0-1)", "limits_min", "float", obj.limits_min),
            ("Range max (0-1)", "limits_max", "float", obj.limits_max),
            ("Time min (s)", "timelimits_min", "float", obj.timelimits_min),
            ("Time max (s)", "timelimits_max", "float", obj.timelimits_max),
            ("Change min (0-1)", "changelimits_min", "float", obj.changelimits_min),
            ("Change max (0-1)", "changelimits_max", "float", obj.changelimits_max),
            ("Overcast threshold min", "threshold_min", "float", obj.threshold_min),
            ("Overcast threshold max", "threshold_max", "float", obj.threshold_max),
            ("Stop time (s)", "threshold_end", "float", obj.threshold_end),
        ]

    if element == "windMagnitude":
        return [
            ("Initial speed (0-20 m/s)", "actual", "float", obj.actual),
            ("Change time (s)", "time", "float", obj.time),
            ("Hold duration (s)", "duration", "float", obj.duration),
            ("Speed min (m/s)", "limits_min", "float", obj.limits_min),
            ("Speed max (m/s)", "limits_max", "float", obj.limits_max),
            ("Time min (s)", "timelimits_min", "float", obj.timelimits_min),
            ("Time max (s)", "timelimits_max", "float", obj.timelimits_max),
            ("Change min (m/s)", "changelimits_min", "float", obj.changelimits_min),
            ("Change max (m/s)", "changelimits_max", "float", obj.changelimits_max),
        ]

    if element == "windDirection":
        return [
            ("Initial direction (rad)", "actual", "float", obj.actual),
            ("Change time (s)", "time", "float", obj.time),
            ("Hold duration (s)", "duration", "float", obj.duration),
            ("Direction min (rad)", "limits_min", "float", obj.limits_min),
            ("Direction max (rad)", "limits_max", "float", obj.limits_max),
            ("Time min (s)", "timelimits_min", "float", obj.timelimits_min),
            ("Time max (s)", "timelimits_max", "float", obj.timelimits_max),
            ("Change min (rad)", "changelimits_min", "float", obj.changelimits_min),
            ("Change max (rad)", "changelimits_max", "float", obj.changelimits_max),
        ]

    if element == "storm":
        return [
            ("Lightning density (0-1)", "density", "float", obj.density),
            ("Overcast threshold (0-1)", "threshold", "float", obj.threshold),
            ("Strike interval (s)", "timeout", "float", obj.timeout),
        ]

    return []


# Field help text (shared with the web UI; see app/web/editors/_help.py).
STATUS_DESCRIPTIONS: dict[str, str] = {
    "sw-enabled": (
        "Master toggle for this file. Off (vanilla default on all maps): cfgweather.xml is ignored "
        "and the engine uses its built-in weather. On: the values in this file drive weather behaviour. "
        "Toggling off does NOT freeze weather — it just hands control back to the engine."
    ),
    "sw-reset": (
        "When enabled, weather resets to the configured initial values on "
        "every server restart instead of persisting the last state."
    ),
}
