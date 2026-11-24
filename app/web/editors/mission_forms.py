"""Form adapters for mission-relative config: globals.xml, cfgweather.xml."""

from __future__ import annotations

from app.shared.config import DayzConfig
from app.shared.config_versions import snapshot
from app.web.editors import EditorSpec, register
from app.web.editors._help import field_help, unit_attr, unit_from_label
from app.web.editors._paths import mission_dir, overridable
from app.web.editors._raw import raw_targets_from
from app.xml.globals_xml import (
    VAR_DESCRIPTIONS,
    VAR_RANGES,
    VAR_SWITCHES,
    VAR_UNITS,
    WIKI_VAR_TYPES,
    get_globals_path,
    parse_globals_file,
    parse_var_value,
    same_var_value,
    save_globals_file,
    set_var_value,
    var_problem,
)
from app.xml.weather_xml import (
    PARAM_DESCRIPTIONS,
    PARAM_RANGES,
    STATUS_DESCRIPTIONS,
    check_ordered_pairs,
    get_active_elements,
    get_element_config_items,
    get_weather_path,
    parse_weather_file,
    save_weather_file,
    set_element_param,
    set_weather_enabled,
    set_weather_reset,
)

_WEATHER_ELEMENT_TITLES = {
    "overcast": "Overcast",
    "fog": "Fog",
    "rain": "Rain",
    "snowfall": "Snowfall",
    "windMagnitude": "Wind Magnitude",
    "windDirection": "Wind Direction",
    "storm": "Storm",
}

# ---------------------------------------------------------------------------
# globals.xml
# ---------------------------------------------------------------------------


@overridable("globals")
def _globals_path(config: DayzConfig):
    m_dir = mission_dir(config)
    return get_globals_path(m_dir) if m_dir else None


def _globals_exists(config: DayzConfig) -> bool:
    path = _globals_path(config)
    return path is not None


def _globals_field(entry) -> dict:
    """One <var> as a form field, typed by its own type code."""
    kind = {0: "int", 1: "float"}.get(entry.type, "str")
    number = parse_var_value(entry.type, entry.value) if kind != "str" else None
    field = {
        "key": entry.name,
        "label": entry.name,
        "type": kind,
        # A value the file holds that is not its type stays visible as text.
        "value": number if number is not None else entry.value,
        "help": field_help(VAR_DESCRIPTIONS, entry.name),
        **(unit_attr(VAR_UNITS, entry.name) if kind != "str" else {}),
    }
    # A 0/1 var is a switch, while the file holds 0 or 1: any other value
    # stays a box, so it is seen and can be put right.
    if entry.name in VAR_SWITCHES and kind == "int" and number in (0, 1):
        field["widget"] = "flag"
    if kind != "str" and entry.name in WIKI_VAR_TYPES:
        low, high = VAR_RANGES.get(entry.name, (0, None))
        field["min"] = low
        if high is not None:
            field["max"] = high
    if field.get("unit") == "%" and kind == "int":
        # A whole percentage (InitialSpawn, RestartSpawn) is a 0-100 slider
        # (webui RangeField). Display only: the save keeps var_problem's check.
        field["min"], field["max"] = 0, 100
    return field


def _globals_load(config: DayzConfig) -> dict:
    path = _globals_path(config)
    if path is None:
        return {"kind": "form", "sections": []}
    gf = parse_globals_file(path)
    fields = [_globals_field(e) for e in sorted(gf.entries, key=lambda e: e.name)]
    return {"kind": "form", "sections": [{"title": "globals.xml", "fields": fields}]}


def _globals_save(config: DayzConfig, values: dict) -> None:
    path = _globals_path(config)
    if path is None:
        raise RuntimeError("globals.xml not found for the current mission")
    gf = parse_globals_file(path)
    by_name = {e.name: e for e in gf.entries}
    writes = []
    problems = []
    for key, value in values.items():
        entry = by_name.get(key)
        text = "" if value is None else str(value)
        if entry is None or same_var_value(entry.type, entry.value, text):
            continue  # untouched: the file keeps its own text ("0.0")
        problem = var_problem(entry.name, entry.type, text)
        if problem:
            problems.append(problem)
        else:
            writes.append((entry, text))
    if problems:
        raise ValueError("; ".join(problems))
    snapshot(path, trigger="editor")  # after every check: a refused save leaves no version
    for entry, text in writes:
        set_var_value(entry, text)
    save_globals_file(gf)


register(
    EditorSpec(
        id="globals",
        title="Globals",
        group="Mission",
        kind="form",
        exists=_globals_exists,
        load=_globals_load,
        save=_globals_save,
        raw_targets=raw_targets_from((_globals_path, "xml")),
    )
)


# ---------------------------------------------------------------------------
# cfgweather.xml
# ---------------------------------------------------------------------------


def _weather_path(config: DayzConfig):
    m_dir = mission_dir(config)
    return get_weather_path(m_dir) if m_dir else None


def _weather_exists(config: DayzConfig) -> bool:
    return _weather_path(config) is not None


def _weather_load(config: DayzConfig) -> dict:
    path = _weather_path(config)
    if path is None:
        return {"kind": "form", "sections": []}
    wf = parse_weather_file(path)
    sections = [
        {
            "title": "General",
            "fields": [
                # The switch for the whole file: the same master button as
                # BattlEye's (FormEditor's HeaderToggle), saved by Save.
                {"key": "enabled", "label": "Enable Weather", "type": "bool", "widget": "header",
                 "value": wf.enabled, "help": field_help(STATUS_DESCRIPTIONS, "sw-enabled")},
                {"key": "reset", "label": "Reset each restart", "type": "bool",
                 "value": wf.reset, "help": field_help(STATUS_DESCRIPTIONS, "sw-reset")},
            ],
        }
    ]
    for element in get_active_elements(wf):
        items = get_element_config_items(wf, element)
        fields = []
        for label, key, kind, value in items:
            shown_label, unit = unit_from_label(label)
            field = {"key": f"{element}.{key}", "label": shown_label, "type": kind, "value": value,
                     "help": field_help(PARAM_DESCRIPTIONS, key), **unit}
            # The range set_element_param checks on save, for the input.
            low, high = PARAM_RANGES.get(element, {}).get(key, (None, None))
            if low is not None:
                field["min"] = low
            if high is not None:
                field["max"] = high
            fields.append(field)
        sections.append({"title": _WEATHER_ELEMENT_TITLES.get(element, element), "fields": fields})
    return {"kind": "form", "sections": sections}


def _weather_text(value) -> str:
    """A form value as the file would hold it: a whole number without `.0`."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return "" if value is None else str(value).strip()


def _weather_save(config: DayzConfig, values: dict) -> None:
    path = _weather_path(config)
    if path is None:
        raise RuntimeError("cfgweather.xml not found for the current mission")
    wf = parse_weather_file(path)
    # Every value goes into the tree, which is what gets written: setting only
    # the parsed fields (as this did) saved the file unchanged. A value that
    # is not a number raises before the snapshot and the write.
    if "enabled" in values and bool(values["enabled"]) != wf.enabled:
        set_weather_enabled(wf, bool(values["enabled"]))
    if "reset" in values and bool(values["reset"]) != wf.reset:
        set_weather_reset(wf, bool(values["reset"]))
    edited: set[tuple[str, str]] = set()
    for key, value in values.items():
        if "." not in key:
            continue
        element, field_name = key.split(".", 1)
        if set_element_param(wf, element, field_name, _weather_text(value)):
            edited.add((element, field_name))
    check_ordered_pairs(wf, edited)
    snapshot(path, trigger="editor")
    save_weather_file(wf)


register(
    EditorSpec(
        id="weather",
        title="Weather",
        group="Mission",
        kind="form",
        exists=_weather_exists,
        load=_weather_load,
        save=_weather_save,
        raw_targets=raw_targets_from((_weather_path, "xml")),
    )
)
