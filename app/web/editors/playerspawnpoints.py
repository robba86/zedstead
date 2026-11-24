"""Player spawn points — ``cfgplayerspawnpoints.xml`` as a table (plans/76 step 3).

The file has three sections, ``<fresh>`` (required), ``<hop>`` and
``<travel>`` (`dayz-docs/wiki/pages/player-spawning-configuration.wiki`), and
each holds the same four blocks: ``<spawn_params>``, ``<generator_params>``,
``<group_params>`` and ``<generator_posbubbles>``. So the table has a row per
section the file has, the three params blocks as columns, and the positions
as the row's tree (``nested``, `_xml_nested`):

- ``groups``: each ``<group>`` as ``{name, its other attributes (a group's own
  lifetime/counter), pos: [{x, z}, …]}``, in file order;
- ``pos``: the ``<pos>`` elements straight under ``<generator_posbubbles>``,
  the layout without groups (Namalsk's, and the wiki's "not using spawning
  groups" example). Shown only when the section has some.

The Visual Map Editor's Players tab edits the same file on a map and is the
easier way to place a position; this table is the whole file at once, and
the Raw tab still edits it as text.

A save follows the step 3 rules: the file is re-read
(``parse_spawnpoints_file``, comments kept, so Sakhal's explanations, the
``START/END MOD`` markers and a parked mod stay where they are), elements are
changed in place and only where a value changed, compared as a number and
written in the file's number style, and ``save_xml`` keeps the file's layout.
A stale tab is refused per cell and per tree key: a row carries the values it
was loaded with (``$base``) beside its cells, and a cell the page changed is
written only while the file still holds what the page loaded. A cell the page
did not change is never written, so an older tab cannot put back a value
someone changed since.

Rows cannot be added or removed: a section is one of three names DayZ knows,
and a removed row is refused rather than ignored (the Raw tab removes one).
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from app.shared.config import DayzConfig
from app.shared.config_versions import snapshot
from app.web.editors import EditorSpec, register
from app.web.editors._nested import _same
from app.web.editors._help import unit_attr
from app.web.editors._paths import mission_dir
from app.web.editors._raw import raw_targets_from
from app.web.editors._xml_nested import (
    Part,
    after,
    apply_parts,
    attrs_of,
    is_element,
    nested_of,
    set_attrs,
    sync_list,
    typed,
    value_text,
)
from app.xml.playerspawnpoints_xml import (
    GENERATOR_PARAM_DESCRIPTIONS,
    GENERATOR_PARAM_FIELDS,
    GROUP_PARAM_DESCRIPTIONS,
    GROUP_PARAM_FIELDS,
    SPAWN_PARAM_DESCRIPTIONS,
    SPAWN_PARAM_FIELDS,
    get_spawnpoints_path,
    parse_spawnpoints_file,
    save_spawnpoints_file,
    set_generator_param,
    set_group_param,
    set_spawn_param,
)

EDITOR_ID = "playerspawnpoints"

#: The loaded cells, carried in each row's values so a save can tell a cell
#: the page changed from one the file changed since (`_apply_cells`).
BASE = "$base"

# A section's blocks in Bohemia's order (`playerspawnpoints_xml._SECTION_BLOCKS`).
SECTION_BLOCKS = ["spawn_params", "generator_params", "group_params", "generator_posbubbles"]

_BLOCKS = {
    "spawn_params": ("Spawn", SPAWN_PARAM_FIELDS, SPAWN_PARAM_DESCRIPTIONS, set_spawn_param),
    "generator_params": ("Grid", GENERATOR_PARAM_FIELDS, GENERATOR_PARAM_DESCRIPTIONS, set_generator_param),
    "group_params": ("Groups", GROUP_PARAM_FIELDS, GROUP_PARAM_DESCRIPTIONS, set_group_param),
}

# What each block does, from the wiki page and Sakhal's own comments.
_BLOCK_HELP = {
    "spawn_params": (
        "Scored when a player spawns: a point closer than the min distance is very unlikely "
        "to be picked, one between min and max is preferred."
    ),
    "generator_params": (
        "Used to build the candidate points: a grid_width x grid_height rectangle around each "
        "position, sampled grid_density times per side (0 = the position alone). Points on "
        "water, inside objects or outside the slope range are dropped."
    ),
    "group_params": (
        "Spawn groups: the server rotates between the groups, each active for lifetime "
        "seconds or counter spawns (-1 = off). A group's own lifetime/counter overrides these."
    ),
}

BOOL_PARAMS = {"enablegroups": False, "groups_as_regular": True}
INT_PARAMS = frozenset({"grid_density", "lifetime", "counter"})
# The unit of a parameter, from its description (playerspawnpoints_xml), shown
# in its cell (webui TableEditor).
_PARAM_UNITS = {
    **dict.fromkeys((
        "min_dist_infected", "max_dist_infected", "min_dist_player", "max_dist_player",
        "min_dist_static", "max_dist_static", "min_dist_trigger", "max_dist_trigger",
        "grid_width", "grid_height",
    ), "meters"),
    **dict.fromkeys(("min_steepness", "max_steepness"), "deg"),
    "lifetime": "seconds",
}

#: Attributes the game reads as decimals; Bohemia writes ``x="10595"`` beside
#: ``x="6063.018555"``.
COORD_KEYS = frozenset({"x", "z"})
GROUP_KEYS = frozenset({"name", "lifetime", "counter", "pos"})
#: A group's own lifetime and counter, which override the section's
#: <group_params> for that group ("it can be overwritten for each specific
#: group", player-spawning-configuration.wiki). Every group offers them,
#: empty when the file has none, and one is written only once it is set.
GROUP_OPTIONAL = {"lifetime": "", "counter": ""}
_ATTR_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")

#: What "Add" puts into an empty list. A new position is refused until it is
#: moved off 0, 0 (`_check_pos`); the map places one by clicking.
TEMPLATES = {
    "groups": {"name": "", **GROUP_OPTIONAL, "pos": []},
    "pos": {"x": 0.0, "z": 0.0},
}


def _path(config: DayzConfig) -> Path | None:
    m_dir = mission_dir(config)
    return get_spawnpoints_path(m_dir) if m_dir else None


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------


def _label(prefix: str, tag: str) -> str:
    return f"{prefix}: {tag.replace('_', ' ')}"


def _columns(sections: list[ET.Element]) -> list[dict]:
    """The section column, then each block's known params and any other leaf
    a section's block holds (a key no version of zsm knows is still shown)."""
    columns = [{
        "key": "section",
        "label": "Section",
        "type": "str",
        "help": "fresh: new characters (required); hop: players from another server on this "
        "map; travel: players from another map. The name is fixed.",
    }]
    for block, (prefix, fields, descriptions, _setter) in _BLOCKS.items():
        tags = list(fields)
        for section in sections:
            elem = section.find(block)
            if elem is None:
                continue
            for child in elem:
                if is_element(child) and not any(is_element(c) for c in child) and child.tag not in tags:
                    tags.append(child.tag)
        for tag in tags:
            kind = "bool" if tag in BOOL_PARAMS else "int" if tag in INT_PARAMS else "float"
            help_text = descriptions.get(tag, "")
            help_text = f"{help_text}. {_BLOCK_HELP[block]}" if help_text else _BLOCK_HELP[block]
            if kind != "bool":
                help_text += " Empty: not in the file, so DayZ uses its default."
            column = {"key": f"{block}.{tag}", "label": _label(prefix, tag), "type": kind, "help": help_text}
            if kind != "bool":
                column.update(unit_attr(_PARAM_UNITS, tag))
            columns.append(column)
    return columns


def _split(key: str) -> tuple[str, str]:
    block, _, tag = key.partition(".")
    return block, tag


def _param(section: ET.Element, key: str) -> ET.Element | None:
    block, tag = _split(key)
    elem = section.find(block)
    return None if elem is None else elem.find(tag)


def _as_bool(text: str | None, default: bool) -> bool:
    text = (text or "").strip().lower()
    if text in ("true", "1", "yes"):
        return True
    if text in ("false", "0", "no"):
        return False
    return default


def _cell(section: ET.Element, key: str):
    """A cell as the table shows it: a bool, a number, or ``""`` when absent."""
    tag = _split(key)[1]
    child = _param(section, key)
    if tag in BOOL_PARAMS:
        return _as_bool(None if child is None else child.text, BOOL_PARAMS[tag])
    if child is None:
        return ""
    return typed(child.text)


def _number(raw) -> float | None:
    """*raw* as a finite number, ``None`` for an empty cell."""
    if isinstance(raw, bool):
        raise ValueError("expected a number")
    text = "" if raw is None else str(raw).strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"{text!r} is not a number") from None
    if not math.isfinite(value):
        raise ValueError(f"{text!r} is not a number")
    return value


def _flag(value) -> bool:
    """A switch cell: a bool from the page, or its text from a hand-made request."""
    return value if isinstance(value, bool) else _as_bool(str(value), False)


def _same_cell(tag: str, a, b) -> bool:
    if tag in BOOL_PARAMS:
        return _flag(a) == _flag(b)
    try:
        x, y = _number(a), _number(b)
    except ValueError:
        return str(a).strip() == str(b).strip()
    return x == y


def _text_for(tag: str, new, old: str | None) -> str:
    """*new* as the file writes it, in the style of *old* (the element's text)."""
    if tag in BOOL_PARAMS:
        if (old or "").strip() in ("0", "1"):
            return "1" if _flag(new) else "0"
        return "true" if _flag(new) else "false"
    value = _number(new)
    if value is None:
        raise ValueError("can't be left empty here (remove it on the Raw tab)")
    if value == int(value):
        if tag in INT_PARAMS or "." not in (old or ""):
            return str(int(value))
    elif tag in INT_PARAMS:
        raise ValueError(f"{new!r} is not a whole number")
    return value_text(value, old)


_BOOL_TEXTS = ("true", "false", "1", "0", "yes", "no")


def param_text(section: ET.Element, key: str, new, loaded=None, where: str = "") -> str | None:
    """What a param cell's *new* value writes into *section*, or ``None`` when
    nothing is to be written: *new* equals *loaded* (what the page showed; the
    file's own value when not given), or the param is not in the file and
    *new* is empty. Raises ``ValueError`` for a value DayZ cannot read and for
    a cell the file changed since the page loaded it.

    The table's save and the map editor's Players tab both go through it."""
    tag = _split(key)[1]
    where = where or f"{section.tag} {key}"
    child = _param(section, key)
    if child is None and (new is None or (isinstance(new, str) and not new.strip())):
        return None  # still not in the file
    if tag in BOOL_PARAMS and not isinstance(new, bool) and str(new).strip().lower() not in _BOOL_TEXTS:
        raise ValueError(f"{where}: {new!r} is not true or false")
    current = _cell(section, key)
    if loaded is None:
        loaded = current
    if _same_cell(tag, new, loaded):
        return None  # not changed on the page: whatever the file holds now stays
    if not _same_cell(tag, current, loaded):
        raise ValueError(
            f"{where} changed in the file since this page loaded it. Reload the page and "
            "make the change again."
        )
    try:
        if tag not in BOOL_PARAMS and _number(new) is None and child is None:
            return None
        return _text_for(tag, new, None if child is None else child.text)
    except ValueError as exc:
        raise ValueError(f"{where}: {exc}") from None


#: grid_width and grid_height are 1 to 4096 metres, and grid_density is at
#: least 1 and no larger than the smaller of the two, or 0 for the position
#: alone: Bohemia's own comments in Sakhal's cfgplayerspawnpoints.xml.
GRID_SIDES = ("grid_width", "grid_height")
GRID_SIDE_MAX = 4096


def check_grid(section: ET.Element, pending: dict[str, str]) -> None:
    """Refuse a generator grid those comments rule out, as it reads once
    *pending* ({tag: text} for ``<generator_params>``) is written. Only an
    edit of one of the three is checked, so a value already in the file never
    blocks an edit of something else."""
    if not any(tag in pending for tag in ("grid_density", *GRID_SIDES)):
        return

    def value(tag: str) -> float | None:
        child = _param(section, f"generator_params.{tag}")
        text = pending[tag] if tag in pending else None if child is None else child.text
        try:
            return _number(text)
        except ValueError:
            return None  # unreadable in the file already: not this edit's to judge

    for tag in GRID_SIDES:
        side = value(tag)
        if tag in pending and side is not None and not 1 <= side <= GRID_SIDE_MAX:
            raise ValueError(f"{section.tag} {tag} is {side:g}: it must be 1 to {GRID_SIDE_MAX} metres")
    density = value("grid_density")
    if density is None:
        return
    if density < 0:
        raise ValueError(f"{section.tag} grid_density is {density:g}: it must be 0 or more")
    sides = [s for s in (value(t) for t in GRID_SIDES) if s is not None]
    if sides and density > min(sides):
        raise ValueError(
            f"{section.tag} grid_density is {density:g}: it can't be larger than the grid's "
            f"smaller side ({min(sides):g} m). 0 takes the position alone."
        )


def _apply_cells(section_obj, section: ET.Element, columns: list[dict], values: dict) -> bool:
    """Write the cells the page changed; returns whether it wrote any. Every
    cell is checked before any is written."""
    base = values.get(BASE)
    base = base if isinstance(base, dict) else {}
    writes = []
    for col in columns:
        key = col["key"]
        if key == "section" or key not in values:
            continue
        text = param_text(section, key, values[key], base.get(key), f"{section.tag} {col['label']}")
        if text is not None:
            writes.append((*_split(key), text))
    check_grid(section, {tag: text for block, tag, text in writes if block == "generator_params"})
    for block, tag, text in writes:
        _BLOCKS[block][3](section_obj, tag, text)
    return bool(writes)


# ---------------------------------------------------------------------------
# The tree: groups and positions
# ---------------------------------------------------------------------------


def _bubbles(section: ET.Element, create: bool = False) -> ET.Element | None:
    elem = section.find("generator_posbubbles")
    if elem is None and create:
        elem = ET.Element("generator_posbubbles")
        section.insert(after(SECTION_BLOCKS, "generator_posbubbles")(section), elem)
    return elem


def _check_pos(entries: list, where: str) -> None:
    for n, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            raise ValueError(f"{where} position {n} must be an object with x and z")
        odd = sorted(k for k in entry if not _ATTR_NAME.fullmatch(str(k)))
        if odd:
            raise ValueError(f"{where} position {n}: {odd[0]!r} is not an attribute name")
        coords = []
        for key in ("x", "z"):
            try:
                value = _number(entry.get(key))
            except ValueError as exc:
                raise ValueError(f"{where} position {n} {key}: {exc}") from None
            if value is None:
                raise ValueError(f"{where} position {n} needs {key}")
            coords.append(value)
        if coords == [0.0, 0.0]:
            raise ValueError(
                f"{where} position {n} is still at 0, 0: enter its x and z, or place it on the "
                "Map Editor's Players tab"
            )


def _sync_pos(parent: ET.Element, entries: list, where: str) -> None:
    _check_pos(entries, where)
    sync_list(
        parent, parent.findall("pos"), entries,
        read=attrs_of, write=set_attrs, make=lambda _e: ET.Element("pos"), at=len,
    )


def _group_read(group: ET.Element) -> dict:
    return {**attrs_of(group, GROUP_OPTIONAL), "pos": [attrs_of(p) for p in group.findall("pos")]}


def _group_count(value, where: str) -> int | None:
    """A group's lifetime or counter as the whole number it writes, ``None``
    for an empty value; -1 turns it off (the wiki)."""
    try:
        number = _number(value)
    except ValueError as exc:
        raise ValueError(f"{where}: {exc}") from None
    if number is None:
        return None
    if number != int(number):
        raise ValueError(f"{where}: {value!r} is not a whole number")
    if number < -1:
        raise ValueError(f"{where} is {number:g}: it must be -1 (off) or more")
    return int(number)


def _group_write(group: ET.Element, entry: dict) -> None:
    name = str(entry.get("name") or "").strip()
    unknown = sorted(k for k in entry if k not in GROUP_KEYS and group.get(k) is None)
    if unknown:
        raise ValueError(f"group {name or '(new)'} has no key {unknown[0]!r}")
    attrs = {k: v for k, v in entry.items() if k != "pos"}
    for key in GROUP_OPTIONAL:
        if key in attrs:
            count = _group_count(attrs[key], f"group {name or '(new)'} {key}")
            if count is None and group.get(key) is not None:
                raise ValueError(
                    f"group {name or '(new)'} {key} can't be left empty here (-1 turns it "
                    "off; remove it on the Raw tab)"
                )
            attrs[key] = "" if count is None else count
    set_attrs(group, attrs, GROUP_OPTIONAL)
    _sync_pos(group, entry.get("pos") or [], f"group {name}")


def _groups_read(section: ET.Element) -> list[dict]:
    bubbles = _bubbles(section)
    return [] if bubbles is None else [_group_read(g) for g in bubbles.findall("group")]


def _groups_write(section: ET.Element, entries: list) -> None:
    for n, entry in enumerate(entries, 1):
        if not isinstance(entry, dict) or not str(entry.get("name") or "").strip():
            raise ValueError(f"{section.tag} group {n} needs a name")
    bubbles = _bubbles(section, create=bool(entries))
    if bubbles is None:
        return
    sync_list(
        bubbles, bubbles.findall("group"), entries,
        read=_group_read, write=_group_write, make=lambda _e: ET.Element("group"), at=len,
    )


def _pos_read(section: ET.Element) -> list[dict]:
    bubbles = _bubbles(section)
    return [] if bubbles is None else [attrs_of(p) for p in bubbles.findall("pos")]


def _pos_write(section: ET.Element, entries: list) -> None:
    bubbles = _bubbles(section, create=bool(entries))
    if bubbles is not None:
        _sync_pos(bubbles, entries, section.tag)


PARTS = {
    "groups": Part(_groups_read, _groups_write),
    "pos": Part(_pos_read, _pos_write),
}


def _shown_parts(section: ET.Element) -> dict[str, Part]:
    """``groups`` unless the section lists bare positions and no group;
    ``pos`` only when it has bare positions."""
    bubbles = _bubbles(section)
    has_groups = bubbles is not None and bubbles.find("group") is not None
    has_pos = bubbles is not None and bubbles.find("pos") is not None
    shown = {}
    if has_groups or not has_pos:
        shown["groups"] = PARTS["groups"]
    if has_pos:
        shown["pos"] = PARTS["pos"]
    return shown


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------


def table_load(path: Path | None) -> dict:
    if path is None:
        return {"kind": "table", "columns": [], "rows": []}
    spf = parse_spawnpoints_file(path)
    sections = [s.element for s in spf.sections.values() if s.element is not None]
    columns = _columns(sections)
    rows = []
    for section in sections:
        cells = {col["key"]: _cell(section, col["key"]) for col in columns[1:]}
        rows.append({
            "id": section.tag,
            "values": {"section": section.tag, **cells, BASE: dict(cells)},
            "nested": nested_of(section, _shown_parts(section)),
        })
    return {
        "kind": "table",
        "columns": columns,
        "rows": rows,
        "add_row": False,
        "nested_templates": TEMPLATES,
        "warnings": [] if "fresh" in spf.sections else [
            "The file has no <fresh> section, which DayZ requires (it is where new characters "
            "spawn). Add it on the Raw tab."
        ],
    }


def table_save(path: Path, rows: list[dict]) -> None:
    spf = parse_spawnpoints_file(path)
    sections = spf.sections
    columns = _columns([s.element for s in sections.values() if s.element is not None])
    sent: dict[str, dict] = {}
    for row in rows:
        row_id = str(row.get("id") or "").strip()
        values = row.get("values", row)
        if row_id not in sections:
            raise ValueError(f"The file has no <{row_id}> section; sections are added on the Raw tab.")
        if str(values.get("section", row_id)).strip() != row_id:
            raise ValueError(f"A section's name is fixed: {row_id} can't be renamed.")
        sent[row_id] = values
    gone = [name for name in sections if name not in sent]
    if gone:
        raise ValueError(f"A section can't be removed here ({', '.join(gone)}); use the Raw tab.")
    changed = False
    for name, values in sent.items():
        section = sections[name]
        changed |= _apply_cells(section, section.element, columns, values)
        changed |= apply_parts(section.element, PARTS, values, where=name, floats=COORD_KEYS)
    if not changed:
        return  # nothing edited: the file is left exactly as it is, no version
    snapshot(path, trigger="editor")  # after every check: a refused save leaves no version
    save_spawnpoints_file(spf)


def _load(config: DayzConfig) -> dict:
    return table_load(_path(config))


def _save(config: DayzConfig, rows: list[dict]) -> None:
    path = _path(config)
    if path is None:
        raise RuntimeError("cfgPlayerSpawnPoints.xml not found for the current mission")
    table_save(path, rows)


def search_fields() -> list[tuple[str, str, str]]:
    """The columns a stock file has, for the search palette (`routers/search.py`)."""
    return [(c["key"], c["label"], c["help"]) for c in _columns([])]


register(
    EditorSpec(
        id=EDITOR_ID,
        title="Player Spawn Points",
        group="Mission",
        kind="table",
        exists=lambda config: _path(config) is not None,
        load=_load,
        save=_save,
        raw_targets=raw_targets_from((_path, "xml")),
    )
)
