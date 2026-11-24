"""Table adapters for mission-relative list-shaped config: events.xml,
cfgeventspawns.xml, cfgeventgroups.xml, ignorelist, limitsdefinition,
messages.xml, economy.xml (system flags).

Table saves are upsert-in-place by row id (update existing entries, create
new ones for unmatched ids) rather than a destructive full-replace, except
for the single-column list files (ignorelist, limitsdefinition) where a row
IS the entire entry and full add/remove is safe.
"""

from __future__ import annotations

import copy
import math

from app.shared.config import DayzConfig
from app.shared.config_versions import snapshot
from app.web.editors import EditorSpec, SaveElsewhere, register
from app.web.editors._help import field_help
from app.web.editors._nested import apply_nested, nested_of
from app.web.editors._paths import mission_dir, overridable, overridden_file, restore_from
from app.web.editors._raw import raw_targets_from
from app.xml.economy_xml import get_economy_path
from app.web.editors.ce_tables import (
    apply_col_help as _apply_col_help,
    economy_load,
    economy_save,
    events_load,
    events_save,
    messages_load,
    messages_save,
    spawnabletypes_load,
    spawnabletypes_save,
)
from app.xml.events_xml import get_events_path
from app.xml.eventgroups_xml import (
    add_child,
    create_group,
    get_eventgroups_path,
    parse_eventgroups_file,
    remove_child,
    save_eventgroups_file,
    update_child,
)
from app.xml.eventspawns_xml import (
    add_position,
    create_event_spawn,
    get_eventspawns_path,
    parse_eventspawns_file,
    remove_angle,
    remove_position,
    save_eventspawns_file,
    update_position,
)
from app.xml.limitsdefinition_xml import FIELD_DESCRIPTIONS as _LIMITS_DESC


# Web-authored help for columns the FIELD_DESCRIPTIONS maps don't describe
# (identifiers, summaries).
# Column help for every central-economy kind (events, spawnabletypes, types,
# globals, economy, messages) lives in ce_tables.py beside its adapter.
# Matches ignorelist_xml.DESCRIPTION: a listed class is not spawned by the
# central economy, and what is already on the map is wiped at the next restart.
_IGNORELIST_COL_HELP = {
    "name": "Classname the central economy does not spawn; items of it already on the map "
            "are wiped at the next restart.",
}
_LIMITS_COL_HELP = {
    "section": "Which limits list this entry belongs to:\n"
    + "\n".join(f"• {k}: {field_help(_LIMITS_DESC, k)}" for k in ("category", "tag", "usage", "value")),
    "name": "Entry name within its section.",
}
#: The line under each section in the picker: the first sentence of its
#: description, since the whole one is too long for a list row.
_LIMITS_SECTION_HELP = {
    section: field_help(_LIMITS_DESC, section[:-1].replace("ie", "y")).split(". ")[0].rstrip(".") + "."
    for section in ("categories", "tags", "usages", "values")
}
_LIMITSUSER_SECTION_HELP = {
    "usage": "Stands for places (Town, Village).",
    "value": "Stands for tiers.",
}
_LIMITSUSER_COL_HELP = {
    "section": "usage or value — which kind of flag this group bundles. "
    "A usage group stands for places (Town, Village); a value group stands for "
    "tiers. They are separate lists and a name may exist in both.",
    "group": "The group's name. This is what an entry in types.xml writes "
    "instead of listing the flags one by one — Tier1234, TownVillage.",
    "flags": "The flags it stands for, comma-separated. Every one has to exist "
    "in cfglimitsdefinition.xml already; this file invents no flags, it only "
    "names combinations of them.",
}
from app.xml.ignorelist_xml import (
    add_entry as ignore_add_entry,
    get_ignorelist_path,
    parse_ignorelist_file,
    remove_entry as ignore_remove_entry,
    save_ignorelist_file,
)
from app.xml.limitsdefinition_xml import (
    add_category,
    add_tag,
    add_usage,
    add_user_group,
    add_value,
    edit_user_group_in_section,
    get_limitsdefinition_path,
    get_limitsdefinitionuser_path,
    parse_limitsdefinition,
    parse_limitsdefinitionuser,
    remove_entry as limits_remove_entry,
    remove_user_group,
    save_limitsdefinition,
    save_limitsdefinitionuser,
)
from app.xml.messages_xml import get_messages_path
from app.xml.xml_manual import manual_owner_id, owner_map

# ---------------------------------------------------------------------------
# Rows a manual XML file merged into a mission file (plans/92)
# ---------------------------------------------------------------------------
#
# A merge target's table shows every row, the mods' and the manual files' too.
# A START MOD block is put back from its source on every merge, so an edit made
# here was dropped without a word: a block's rows are read-only here. A manual
# file's row names its file (`manual`), a mod's row its mod (`mod`), and the
# table links to that file's form or to the mod's files (Take over). `owner`
# (the block's owner, a mod or `@user:<id>`) is on every row a block holds, and
# the Custom config tab's "Goes into" link filters the table on it.

_BLOCK_READONLY_REASON = (
    "Greyed-out rows come from a mod or a manual XML file, and every update "
    "puts them back. Edit a manual file's rows in that file, and take a mod's "
    "file over first, on Configs → Custom config."
)


def _tag_owner(row: dict, owners: dict, *elements) -> dict:
    """*row* with `owner`, `readonly` and `manual` (a manual file's id) or `mod`
    (a mod's name) from the first of *elements* a block holds."""
    owner = next((owners[e] for e in elements if e in owners), None)
    if owner:
        row["owner"] = owner
        row["readonly"] = True
        file_id = manual_owner_id(owner)
        if file_id:
            row["manual"] = file_id
        else:
            row["mod"] = owner
    return row


def _block_source(row: dict) -> str | None:
    """Who puts a locked row back on every merge, for a message, or None."""
    if row.get("manual"):
        return f"the manual XML file {row['manual']!r}"
    if row.get("mod"):
        return f"the mod {row['mod']}"
    return None


def _with_manual_reason(payload: dict) -> dict:
    """*payload* with the read-only note when a block owns a row."""
    if any(_block_source(r) for r in payload["rows"]):
        payload["readonly_reason"] = _BLOCK_READONLY_REASON
    return payload


def _same_value(a, b) -> bool:
    """A cell as sent equals the cell as loaded. The client sends untouched
    values back as loaded, and a typed one as text. Two numbers compare as
    numbers: JSON hands a loaded 11506.0 back as 11506."""
    blank = (None, "")
    if a in blank or b in blank:
        return a in blank and b in blank
    if not isinstance(a, bool) and not isinstance(b, bool):
        try:
            return float(a) == float(b)
        except (TypeError, ValueError):
            pass
    return str(a) == str(b)


def _keeping_manual_rows(load, save, group_key: str | None = None):
    """*save*, refusing a change to a row a mod or a manual file owns.

    Every row is re-read first: a block's row must come back with the values it
    was loaded with, and a new row must not join a group (an event, a preset)
    that only blocks hold, since it would land inside a block and be dropped on
    the next merge."""

    def guarded(config: DayzConfig, rows: list[dict]) -> None:
        current = load(config)["rows"]
        owned = [r for r in current if _block_source(r)]
        if owned:
            sent = {r.get("id"): r.get("values", r) for r in rows}
            for row in owned:
                values = sent.get(row["id"])
                if values is None or not all(
                    _same_value(values.get(k), v) for k, v in row["values"].items()
                ):
                    raise ValueError(
                        f"A row from {_block_source(row)} was changed or removed, and the next "
                        "update puts it back. Change it on Configs → Custom config."
                    )
            if group_key:
                known = {r["id"] for r in current}
                groups: dict[str, set] = {}
                for r in current:
                    groups.setdefault(str(r["values"].get(group_key, "")), set()).add(_block_source(r))
                for r in rows:
                    if r.get("id") in known:
                        continue
                    name = str(r.get("values", r).get(group_key, "")).strip()
                    files = groups.get(name, set())
                    if files and None not in files:
                        raise ValueError(
                            f"{name} comes from {sorted(files)[0]}, and the next update "
                            "drops a row added to it. Add it on Configs → Custom config."
                        )
        save(config, rows)

    return guarded

# ---------------------------------------------------------------------------
# economy.xml
# ---------------------------------------------------------------------------


@overridable("economy")
def _economy_path(config: DayzConfig):
    m_dir = mission_dir(config)
    return get_economy_path(m_dir) if m_dir else None


def _economy_exists(config: DayzConfig) -> bool:
    return _economy_path(config) is not None


def _economy_load(config: DayzConfig) -> dict:
    return economy_load(_economy_path(config))


def _economy_save(config: DayzConfig, rows: list[dict]) -> None:
    path = _economy_path(config)
    if path is None:
        raise RuntimeError("economy.xml not found for the current mission")
    economy_save(path, rows)


register(
    EditorSpec(
        id="economy",
        title="Economy",
        group="Mission",
        kind="table",
        exists=_economy_exists,
        load=_economy_load,
        save=_economy_save,
        raw_targets=raw_targets_from((_economy_path, "xml")),
    )
)


# ---------------------------------------------------------------------------
# events.xml (columns + save live in ce_tables.py — the generated custom/
# event files reuse the same table)
# ---------------------------------------------------------------------------


@overridable("events")
def _events_path(config: DayzConfig):
    m_dir = mission_dir(config)
    return get_events_path(m_dir) if m_dir else None


def _events_exists(config: DayzConfig) -> bool:
    return _events_path(config) is not None


def _events_load(config: DayzConfig) -> dict:
    return events_load(_events_path(config))


def _events_save(config: DayzConfig, rows: list[dict]) -> None:
    path = _events_path(config)
    if path is None:
        raise RuntimeError("events.xml not found for the current mission")
    # A manual file's events (the Form tab) carry no rename into the mission's
    # cfgeventspawns.xml: the positions there are not that file's.
    spawns = None if overridden_file("events") else _eventspawns_path(config)
    events_save(path, rows, spawns_path=spawns)


register(
    EditorSpec(
        id="events",
        title="Events",
        group="Mission",
        kind="table",
        exists=_events_exists,
        load=_events_load,
        save=_events_save,
        raw_targets=raw_targets_from((_events_path, "xml")),
    )
)


# ---------------------------------------------------------------------------
# cfgeventspawns.xml — one row per <pos>, grouped by event via filter_key
# ---------------------------------------------------------------------------

_EVENTSPAWNS_COL_HELP = {
    "event": "Event name — must match an <event> in events.xml (e.g. StaticHeliCrash).",
    "x": "World X coordinate (west → east) of the spawn point.",
    "z": "World Z coordinate (south → north) of the spawn point.",
    "y": "Height in metres, for the few events that place their object at a set "
         "height (StaticTrain, StaticSantaCrash, …). Empty for the rest. The map "
         "editor has no heights, so moving a position there keeps this value.",
    "a": "Spawn rotation in degrees. -1 spawns with a random angle. Empty means "
         "the file gives none.",
}


@overridable("eventspawns")
def _eventspawns_path(config: DayzConfig):
    m_dir = mission_dir(config)
    return get_eventspawns_path(m_dir) if m_dir else None


def _eventspawns_exists(config: DayzConfig) -> bool:
    return _eventspawns_path(config) is not None


def _eventspawns_load(config: DayzConfig) -> dict:
    path = _eventspawns_path(config)
    if path is None:
        return {"kind": "table", "columns": [], "rows": []}
    ef = parse_eventspawns_file(path)
    owners = owner_map(ef.tree.getroot())
    columns = _apply_col_help([
        {"key": "event", "label": "Event", "type": "str"},
        {"key": "x", "label": "X", "type": "float"},
        {"key": "z", "label": "Z", "type": "float"},
        {"key": "y", "label": "Height", "type": "float", "unit": "meters"},
        {"key": "a", "label": "Angle", "type": "float", "unit": "deg"},
    ], _EVENTSPAWNS_COL_HELP)
    rows = [
        _tag_owner({
            "id": _indexed_row_id(entry.name, k, i),
            "values": {
                "event": entry.name, "x": pos.x, "z": pos.z,
                "y": "" if pos.y is None else pos.y,
                "a": pos.a if pos.has_a else "",
            },
        }, owners, pos.element, entry.element)
        for k, entry in _with_occurrence(ef.entries)
        for i, pos in enumerate(entry.positions)
    ]
    # A vanilla file holds thousands of positions, so the client filters by
    # event instead of rendering every row at once.
    return _with_manual_reason(
        {"kind": "table", "columns": columns, "rows": rows, "filter_key": "event"}
    )


def _indexed_row_id(name: str, occurrence: int, index: int) -> str:
    """The row id of leaf *index* in the *occurrence*-th parent named *name*.

    The first parent of a name keeps the short ``<name>#<index>``. A later one
    (a mod block that repeats a mission event) gets ``<name>#<occurrence>.<index>``,
    since a shared id let one block's row overwrite the other's on save."""
    return f"{name}#{index}" if occurrence == 0 else f"{name}#{occurrence}.{index}"


def _with_occurrence(entries):
    """(occurrence, entry) per entry: how many earlier entries share its name."""
    seen: dict[str, int] = {}
    for entry in entries:
        k = seen.get(entry.name, 0)
        seen[entry.name] = k + 1
        yield k, entry


def _parse_indexed_row_id(row_id) -> tuple[str, int, int] | None:
    """Split a row id from `_indexed_row_id` into (name, occurrence, index).
    None for client-generated ids.

    Shared by the two "named parent, repeated leaf child" tables
    (cfgeventspawns' <pos>, cfgeventgroups' <child>) — both flatten one row per
    leaf and need the parent back out of the id on save.
    """
    if not isinstance(row_id, str) or "#" not in row_id:
        return None
    name, _, index = row_id.rpartition("#")
    occurrence, dot, leaf = index.partition(".")
    if not dot:
        occurrence, leaf = "0", index
    if not name or not occurrence.isdigit() or not leaf.isdigit():
        return None
    return name, int(occurrence), int(leaf)


def _number_cell(values: dict, key: str, label: str, where: str) -> float | None:
    """A table cell that must be a finite number, or None when it is empty.

    Anything else is refused: the save used to keep the old value for text it
    could not read, and write ``nan`` through."""
    raw = values.get(key)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        number = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{where}: {label} must be a number, not {raw!r}") from None
    if not math.isfinite(number):
        raise ValueError(f"{where}: {label} must be a finite number")
    return number


def _eventspawns_cells(values: dict, where: str, terrain: str) -> tuple:
    """(x, z, y, a) of one row, checked. x and z are required and must be on the
    map (the map editor's own bound); y and a may be empty."""
    from app.spawn_editor.routes import _off_map_problem

    x = _number_cell(values, "x", "X", where)
    z = _number_cell(values, "z", "Z", where)
    if x is None or z is None:
        raise ValueError(f"{where}: X and Z are required")
    problem = _off_map_problem({"x": x, "z": z}, terrain)
    if problem:
        raise ValueError(f"{where}: {problem}")
    return x, z, _number_cell(values, "y", "Height", where), _number_cell(values, "a", "Angle", where)


def _eventspawns_save(config: DayzConfig, rows: list[dict]) -> None:
    from app.maps.locations import get_terrain_from_mission

    path = _eventspawns_path(config)
    if path is None:
        raise RuntimeError("cfgeventspawns.xml not found for the current mission")
    ef = parse_eventspawns_file(path)
    terrain = get_terrain_from_mission(path.parent)

    # Positions are mutated in place (never rebuilt from scratch) so the
    # mod-merge START/END comment blocks around them stay intact, and only a
    # value that changed is written, so an untouched one keeps its text and no
    # `a` or `y` is added that the file did not have. Every row is checked
    # before anything changes.
    updates: dict[tuple[str, int, int], tuple] = {}
    additions: list[tuple] = []
    for row in rows:
        values = row.get("values", row)
        name = str(values.get("event", "")).strip()
        if not name:
            continue
        cells = _eventspawns_cells(values, f"Event {name}", terrain)
        parsed = _parse_indexed_row_id(row.get("id"))
        if parsed and parsed[0] == name:
            updates[parsed] = cells
        else:
            # New row, or one whose event was renamed — it moves to that event.
            additions.append((name, cells))

    for k, entry in list(_with_occurrence(ef.entries)):
        for i, pos in reversed(list(enumerate(entry.positions))):
            cells = updates.get((entry.name, k, i))
            if cells is None:
                remove_position(entry, pos)
            else:
                x, z, y, a = cells
                if a is None and pos.has_a:
                    remove_angle(pos)   # the cell was cleared
                update_position(pos, x, z, a, y=y)

    # The first block of a name: a later one is usually a mod's, which the next merge rebuilds.
    by_name = {entry.name: entry for k, entry in _with_occurrence(ef.entries) if k == 0}
    for name, (x, z, y, a) in additions:
        entry = by_name.get(name)
        if entry is None:
            entry = create_event_spawn(ef, name)
            by_name[name] = entry
        add_position(entry, x, z, a, y)

    snapshot(path, trigger="editor")  # after every check: a refused save leaves no version
    save_eventspawns_file(ef)


register(
    EditorSpec(
        id="eventspawns",
        title="Event Spawns",
        group="Mission",
        kind="table",
        exists=_eventspawns_exists,
        load=_eventspawns_load,
        save=_keeping_manual_rows(_eventspawns_load, _eventspawns_save, "event"),
        restore=restore_from(_eventspawns_path),
        raw_targets=raw_targets_from((_eventspawns_path, "xml")),
    )
)


# ---------------------------------------------------------------------------
# cfgeventgroups.xml — one row per <child>, grouped by <group> via filter_key
#
# Same shape as cfgeventspawns above (named parent, repeated leaf child), so it
# gets the same flattening. It was a text-kind adapter until 2026 Q3 on the
# grounds that it was "nested-section shaped" — which stopped being true once
# cfgeventspawns proved the parent-as-a-column trick.
# ---------------------------------------------------------------------------

_EVENTGROUPS_COL_HELP = {
    "group": "Group name — reference it from an <event> in events.xml as its child type.",
    "type": "Classname of the object to spawn (e.g. Wreck_UH1Y).",
    "deloot": "On: the object spawns without its loot. Off: it spawns loot normally.",
    "lootmin": "Minimum loot items spawned in this object, a whole number, at most Loot max. "
               "Empty: the child has none (Bohemia's own files leave it off some children).",
    "lootmax": "Maximum loot items spawned in this object, a whole number. Empty: none set.",
    "x": "X offset RELATIVE to the group's spawn position — not a world coordinate.",
    "y": "Y (height) offset RELATIVE to the group's spawn position. Empty: none set.",
    "z": "Z offset RELATIVE to the group's spawn position — not a world coordinate.",
    "a": "Rotation in degrees, relative to the group's orientation.",
}


@overridable("eventgroups")
def _eventgroups_path(config: DayzConfig):
    m_dir = mission_dir(config)
    return get_eventgroups_path(m_dir) if m_dir else None


def _eventgroups_exists(config: DayzConfig) -> bool:
    return _eventgroups_path(config) is not None


def _eventgroups_load(config: DayzConfig) -> dict:
    path = _eventgroups_path(config)
    if path is None:
        return {"kind": "table", "columns": [], "rows": []}
    gf = parse_eventgroups_file(path)
    owners = owner_map(gf.tree.getroot())
    columns = _apply_col_help([
        {"key": "group", "label": "Group", "type": "str"},
        {"key": "type", "label": "Type", "type": "str"},
        {"key": "deloot", "label": "Deloot", "type": "bool"},
        {"key": "lootmin", "label": "Loot min", "type": "int"},
        {"key": "lootmax", "label": "Loot max", "type": "int"},
        {"key": "x", "label": "X", "type": "float"},
        {"key": "y", "label": "Y", "type": "float"},
        {"key": "z", "label": "Z", "type": "float"},
        {"key": "a", "label": "Angle", "type": "float", "unit": "deg"},
    ], _EVENTGROUPS_COL_HELP)
    rows = [
        _tag_owner({
            "id": _indexed_row_id(entry.name, k, i),
            "values": {
                "group": entry.name,
                "type": child.type,
                "deloot": child.deloot,
                **{key: _blank_if_none(getattr(child, key))
                   for key in ("lootmin", "lootmax", "x", "y", "z", "a")},
            },
        }, owners, child.element, entry.element)
        for k, entry in _with_occurrence(gf.entries)
        for i, child in enumerate(entry.children)
    ]
    # An empty <group> has no <child> and so no row of its own. Filtering by it
    # would show nothing to add to, so the picker would be lying — the group is
    # (re)created by typing its name into a new row's Group cell.
    return _with_manual_reason(
        {"kind": "table", "columns": columns, "rows": rows, "filter_key": "group"}
    )


def _blank_if_none(value):
    return "" if value is None else value


def _child_cells(values: dict, where: str) -> dict:
    """The <child> attributes out of a table row, checked; None for an empty
    cell (which removes the attribute, or adds none)."""
    type_ = str(values.get("type") or "").strip()
    if not type_:
        raise ValueError(f"{where}: Type is required (the class the child spawns)")
    out = {"type": type_, "deloot": bool(values.get("deloot"))}
    for key, label in (("lootmin", "Loot min"), ("lootmax", "Loot max")):
        number = _number_cell(values, key, label, where)
        if number is not None and (number < 0 or number != int(number)):
            raise ValueError(f"{where}: {label} must be a whole number of 0 or more")
        out[key] = None if number is None else int(number)
    if out["lootmin"] is not None and out["lootmax"] is not None and out["lootmin"] > out["lootmax"]:
        raise ValueError(f"{where}: Loot min ({out['lootmin']}) is more than Loot max ({out['lootmax']})")
    for key, label in (("x", "X"), ("y", "Y"), ("z", "Z"), ("a", "Angle")):
        out[key] = _number_cell(values, key, label, where)
    return out


def _eventgroups_save(config: DayzConfig, rows: list[dict]) -> None:
    path = _eventgroups_path(config)
    if path is None:
        raise RuntimeError("cfgeventgroups.xml not found for the current mission")
    gf = parse_eventgroups_file(path)

    # Children are mutated in place (never rebuilt from scratch) so the
    # mod-merge START/END comment blocks around the groups stay intact, and so
    # any <child> attribute outside CHILD_ATTRS survives the round trip. Only
    # a value that changed is written: an untouched save used to add deloot,
    # lootmin, lootmax and y to every child that had none and turn 0.7 into
    # 0.700000 (874 children on Chernarus). Every row is checked first.
    updates: dict[tuple[str, int, int], dict] = {}
    additions: list[tuple[str, dict]] = []
    for row in rows:
        values = row.get("values", row)
        name = str(values.get("group", "")).strip()
        if not name:
            continue
        cells = _child_cells(values, f"Group {name}")
        parsed = _parse_indexed_row_id(row.get("id"))
        if parsed and parsed[0] == name:
            updates[parsed] = cells
        else:
            # New row, or one whose group was renamed — it moves to that group.
            additions.append((name, cells))

    for k, entry in list(_with_occurrence(gf.entries)):
        for i, child in reversed(list(enumerate(entry.children))):
            cells = updates.get((entry.name, k, i))
            if cells is None:
                remove_child(entry, child)
            else:
                update_child(child, **cells)

    # The first block of a name: a later one is usually a mod's, which the next merge rebuilds.
    by_name = {entry.name: entry for k, entry in _with_occurrence(gf.entries) if k == 0}
    for name, cells in additions:
        entry = by_name.get(name)
        if entry is None:
            entry = create_group(gf, name)
            by_name[name] = entry
        for key in ("x", "z", "a"):   # every stock child has them
            if cells[key] is None:
                cells[key] = 0.0
        add_child(entry, **cells)

    snapshot(path, trigger="editor")  # after every check: a refused save leaves no version
    save_eventgroups_file(gf)


register(
    EditorSpec(
        id="eventgroups",
        title="Event Groups",
        group="Mission",
        kind="table",
        exists=_eventgroups_exists,
        load=_eventgroups_load,
        save=_keeping_manual_rows(_eventgroups_load, _eventgroups_save, "group"),
        restore=restore_from(_eventgroups_path),
        raw_targets=raw_targets_from((_eventgroups_path, "xml")),
    )
)


# ---------------------------------------------------------------------------
# ignorelist
# ---------------------------------------------------------------------------


def _ignorelist_path(config: DayzConfig):
    m_dir = mission_dir(config)
    return get_ignorelist_path(m_dir) if m_dir else None


def _ignorelist_exists(config: DayzConfig) -> bool:
    return _ignorelist_path(config) is not None


def _ignorelist_load(config: DayzConfig) -> dict:
    path = _ignorelist_path(config)
    if path is None:
        return {"kind": "table", "columns": [], "rows": []}
    ilf = parse_ignorelist_file(path)
    columns = _apply_col_help([{"key": "name", "label": "Name", "type": "str"}], _IGNORELIST_COL_HELP)
    rows = [{"id": e.name, "values": {"name": e.name}} for e in ilf.entries]
    return {"kind": "table", "columns": columns, "rows": rows}


def _ignorelist_names(path) -> list[str]:
    """The class names a version of cfgignorelist.xml lists (its "Recently removed")."""
    return [e.name for e in parse_ignorelist_file(path).entries]


def _ignorelist_save(config: DayzConfig, rows: list[dict]) -> None:
    path = _ignorelist_path(config)
    if path is None:
        raise RuntimeError("ignorelist not found for the current mission")
    ilf = parse_ignorelist_file(path)
    # A pasted name carried its spaces into the file, where the class was then
    # never matched.
    wanted = {str((row.get("values", row)).get("name") or "").strip() for row in rows}
    current = {e.name for e in ilf.entries}
    for entry in [e for e in ilf.entries if e.name in (current - wanted)]:
        ignore_remove_entry(ilf, entry)
    for name in wanted - current:
        if name:
            ignore_add_entry(ilf, name)
    snapshot(path, trigger="editor")
    save_ignorelist_file(ilf)


register(
    EditorSpec(
        id="ignorelist",
        title="Ignore List",
        group="Mission",
        kind="table",
        exists=_ignorelist_exists,
        load=_ignorelist_load,
        save=_ignorelist_save,
        raw_targets=raw_targets_from((_ignorelist_path, "xml")),
        entry_names=_ignorelist_names,
    )
)


# ---------------------------------------------------------------------------
# limitsdefinition (categories/tags/usages/values — 4 named lists, one table)
# ---------------------------------------------------------------------------

_LIMITS_SECTIONS = ("categories", "tags", "usages", "values")
_LIMITS_ADDERS = {
    "categories": add_category,
    "tags": add_tag,
    "usages": add_usage,
    "values": add_value,
}


@overridable("limitsdefinition")
def _limitsdefinition_path(config: DayzConfig):
    m_dir = mission_dir(config)
    # get_limitsdefinition_path does a bare `mission_dir.iterdir()` scan with
    # no existence check of its own — guard here so a mission dir that
    # doesn't exist yet (fresh install, or a test with no mission scaffold)
    # doesn't raise FileNotFoundError.
    return get_limitsdefinition_path(m_dir) if m_dir and m_dir.is_dir() else None


def _limitsdefinition_exists(config: DayzConfig) -> bool:
    return _limitsdefinition_path(config) is not None


def _row_section(values: dict, name_key: str, sections: tuple, aliases: dict | None = None) -> tuple[str, str] | None:
    """A limits row's ``(section, name)``; None for a row left blank (an added
    row nobody filled in). A section that is not one of *sections*, or a name
    left empty, is a ValueError naming the row: it used to drop the row, which
    deleted the entry from the file."""
    raw = str(values.get("section") or "").strip()
    name = str(values.get(name_key) or "").strip()
    section = raw.lower()
    section = (aliases or {}).get(section, section)
    if not raw and not name:
        return None
    allowed = ", ".join(sections)
    if section not in sections:
        shown = f"Section {raw!r}" if raw else "An empty section"
        raise ValueError(f"Row {name or '(no name)'}: {shown} is not one of {allowed}.")
    if not name:
        raise ValueError(f"A row in section {section} has no name; give it one or remove the row.")
    return section, name


def _limitsdefinition_load(config: DayzConfig) -> dict:
    path = _limitsdefinition_path(config)
    if path is None:
        return {"kind": "table", "columns": [], "rows": []}
    ldf = parse_limitsdefinition(path)
    owners = owner_map(ldf.tree.getroot())
    columns = _apply_col_help([
        {
            "key": "section", "label": "Section", "type": "str", "choices": list(_LIMITS_SECTIONS),
            "choice_help": _LIMITS_SECTION_HELP,
        },
        {"key": "name", "label": "Name", "type": "str"},
    ], _LIMITS_COL_HELP)
    rows = []
    for section in _LIMITS_SECTIONS:
        for entry in getattr(ldf, section):
            rows.append(_tag_owner(
                {"id": f"{section}:{entry.name}", "values": {"section": section, "name": entry.name}},
                owners, entry.element,
            ))
    return _with_manual_reason({"kind": "table", "columns": columns, "rows": rows})


# The <type> child that names an entry of each section.
_LIMITS_TYPE_TAG = {"categories": "category", "tags": "tag", "usages": "usage", "values": "value"}


def _limits_still_used(config: DayzConfig, removed: dict[str, set[str]]) -> str | None:
    """A sentence naming the removed names the mission's types still use, or None.

    DayZ's economy needs every category, tag, usage and value a type names
    defined in cfglimitsdefinition.xml, and the Types page refuses a row that
    names one it does not define. Removing "tools" here while 316 types named
    it was saved without a word.
    """
    from app.xml.types_xml import scan_types_files
    from app.xml.xml_utils import load_xml

    wanted = {(s, n) for s, names in removed.items() for n in names}
    if not wanted:
        return None
    m_dir = mission_dir(config)
    counts: dict[tuple[str, str], int] = {}
    tag_section = {tag: s for s, tag in _LIMITS_TYPE_TAG.items()}
    for path in scan_types_files(m_dir) if m_dir else []:
        try:
            root = load_xml(path).getroot()
        except Exception:  # noqa: BLE001 - an unreadable file names nothing we can count
            continue
        for t in root.iter("type"):
            for child in t:
                key = (tag_section.get(child.tag, ""), child.get("name", ""))
                if key in wanted:
                    counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None
    shown = ", ".join(
        f"{_LIMITS_TYPE_TAG[s]} {n} ({c} type{'s' if c != 1 else ''})"
        for (s, n), c in sorted(counts.items(), key=lambda kv: -kv[1])
    )
    return (f"Types still use {shown}, and DayZ needs every name a type uses defined here. "
            "Take it off those types on the Types page first, or keep it.")


def _limitsdefinition_save(config: DayzConfig, rows: list[dict]) -> None:
    path = _limitsdefinition_path(config)
    if path is None:
        raise RuntimeError("cfglimitsdefinition.xml not found for the current mission")
    ldf = parse_limitsdefinition(path)

    wanted: dict[str, set[str]] = {s: set() for s in _LIMITS_SECTIONS}
    for row in rows:
        found = _row_section(row.get("values", row), "name", _LIMITS_SECTIONS)
        if found:
            wanted[found[0]].add(found[1])
    removed = {s: {e.name for e in getattr(ldf, s)} - wanted[s] for s in _LIMITS_SECTIONS}
    problem = _limits_still_used(config, removed)
    if problem:
        raise ValueError(problem)
    snapshot(path, trigger="editor")  # after every check: a refused save leaves no version

    for section in _LIMITS_SECTIONS:
        entries = getattr(ldf, section)
        current = {e.name for e in entries}
        for entry in [e for e in entries if e.name in (current - wanted[section])]:
            limits_remove_entry(ldf, section, entry)
        for name in wanted[section] - current:
            _LIMITS_ADDERS[section](ldf, name)

    save_limitsdefinition(ldf)


register(
    EditorSpec(
        id="limitsdefinition",
        title="Limits Definition",
        group="Mission",
        kind="table",
        exists=_limitsdefinition_exists,
        load=_limitsdefinition_load,
        save=_keeping_manual_rows(_limitsdefinition_load, _limitsdefinition_save),
        restore=restore_from(_limitsdefinition_path),
        raw_targets=raw_targets_from((_limitsdefinition_path, "xml")),
    )
)


# ---------------------------------------------------------------------------
# cfglimitsdefinitionuser.xml — one row per <user> group (TODO H4, 2026 Q3)
#
# Deliberately the same shape as its sibling above: a `section` column and a
# `section:name` row id, so the two halves of the limits vocabulary are edited
# the same way. It was the last text-kind adapter with no richer view anywhere.
#
# A row is a whole group, not one membership: a group is short (two to four
# flags) and it is the unit people think in — "Tier1234 means Tier1 to Tier4".
# Flattening to one row per flag would have meant four rows to read one idea,
# and `cfgeventgroups`' parent-as-a-column trick exists for files with
# thousands of leaves, which this is not — eight groups on Chernarus.
# Comma-separated members in one cell follows cfgEffectArea's `pos`.
# ---------------------------------------------------------------------------

_LIMITSUSER_SECTIONS = ("usage", "value")


@overridable("limitsdefinitionuser")
def _limitsdefinitionuser_path(config: DayzConfig):
    m_dir = mission_dir(config)
    # Same iterdir() guard as _limitsdefinition_path above.
    return get_limitsdefinitionuser_path(m_dir) if m_dir and m_dir.is_dir() else None


def _limitsdefinitionuser_exists(config: DayzConfig) -> bool:
    return _limitsdefinitionuser_path(config) is not None


def _groups_by_section(ludf) -> dict[str, list]:
    return {"usage": ludf.usage_groups, "value": ludf.value_groups}


def _limitsdefinitionuser_load(config: DayzConfig) -> dict:
    path = _limitsdefinitionuser_path(config)
    if path is None:
        return {"kind": "table", "columns": [], "rows": []}
    ludf = parse_limitsdefinitionuser(path)
    owners = owner_map(ludf.tree.getroot())
    columns = _apply_col_help([
        {
            "key": "section", "label": "Section", "type": "str", "choices": list(_LIMITSUSER_SECTIONS),
            "choice_help": _LIMITSUSER_SECTION_HELP,
        },
        {"key": "group", "label": "Group", "type": "str"},
        # A multi-select of the flags cfglimitsdefinition.xml defines (webui
        # TableEditor `multi`); the cell's value stays the comma-separated text.
        {"key": "flags", "label": "Flags", "type": "str", "multi": True},
    ], _LIMITSUSER_COL_HELP)
    defined = _defined_flags(config)
    rows = [
        _tag_owner({
            "id": f"{section}:{group.name}",
            "values": {
                "section": section,
                "group": group.name,
                "flags": ", ".join(group.members),
            },
            **({"choices": {"flags": defined[section]}} if defined.get(section) else {}),
        }, owners, group.element)
        for section, groups in _groups_by_section(ludf).items()
        for group in groups
    ]
    return _with_manual_reason({"kind": "table", "columns": columns, "rows": rows})


def _defined_flags(config: DayzConfig) -> dict[str, list[str]]:
    """The usage and value flags cfglimitsdefinition.xml defines, by section,
    for the Flags picker. Empty when the file is missing or unreadable: the
    picker then offers only what each row already holds."""
    m_dir = mission_dir(config)
    path = get_limitsdefinition_path(m_dir) if m_dir and m_dir.is_dir() else None
    if path is None:
        return {}
    try:
        ldf = parse_limitsdefinition(path)
    except (OSError, SyntaxError):  # ET.ParseError is a SyntaxError
        return {}
    return {
        "usage": [e.name for e in ldf.usages if e.name],
        "value": [e.name for e in ldf.values if e.name],
    }


def _split_flags(raw) -> list[str]:
    """Members out of one cell. Commas, because that is how the file reads back
    to a person; whitespace around them is theirs, not ours."""
    if isinstance(raw, (list, tuple)):
        parts = [str(item) for item in raw]
    else:
        parts = str(raw or "").split(",")
    return [part.strip() for part in parts if part.strip()]


def _limitsdefinitionuser_save(config: DayzConfig, rows: list[dict]) -> None:
    path = _limitsdefinitionuser_path(config)
    if path is None:
        raise RuntimeError("cfglimitsdefinitionuser.xml not found for the current mission")
    ludf = parse_limitsdefinitionuser(path)

    wanted: dict[str, dict[str, list[str]]] = {s: {} for s in _LIMITSUSER_SECTIONS}
    for row in rows:
        values = row.get("values", row)
        # Tolerate the XML's own spelling: someone reading the file sees
        # <usageflags>, so typing "usageflags" into the cell is a fair guess.
        found = _row_section(
            values, "group", _LIMITSUSER_SECTIONS, {"usageflags": "usage", "valueflags": "value"}
        )
        if found:
            wanted[found[0]][found[1]] = _split_flags(values.get("flags"))
    snapshot(path, trigger="editor")  # after every check: a refused save leaves no version

    # Groups are edited in place and only the ones that went away are removed,
    # so the mod-merge START/END comment blocks around them survive — the file
    # is one of ALL_MERGE_CONFIGS' direct targets.
    by_section = _groups_by_section(ludf)
    for section in _LIMITSUSER_SECTIONS:
        groups = by_section[section]
        current = {group.name for group in groups}
        for group in [g for g in list(groups) if g.name not in wanted[section]]:
            remove_user_group(ludf, section, group)
        for group in list(groups):
            members = wanted[section].get(group.name)
            if members is not None and members != group.members:
                edit_user_group_in_section(group, section, members)
        for name, members in wanted[section].items():
            if name not in current:
                add_user_group(ludf, section, name, members)

    save_limitsdefinitionuser(ludf)


register(
    EditorSpec(
        id="limitsdefinitionuser",
        title="User Limits Definition",
        group="Mission",
        kind="table",
        exists=_limitsdefinitionuser_exists,
        load=_limitsdefinitionuser_load,
        save=_keeping_manual_rows(_limitsdefinitionuser_load, _limitsdefinitionuser_save),
        restore=restore_from(_limitsdefinitionuser_path),
        raw_targets=raw_targets_from((_limitsdefinitionuser_path, "xml")),
    )
)


# ---------------------------------------------------------------------------
# messages.xml
# ---------------------------------------------------------------------------


@overridable("messages")
def _messages_path(config: DayzConfig):
    return get_messages_path(config)


def _messages_exists(config: DayzConfig) -> bool:
    path = _messages_path(config)
    return path is not None and path.is_file()


def _messages_load(config: DayzConfig) -> dict:
    return messages_load(_messages_path(config))


def _messages_save(config: DayzConfig, rows: list[dict]) -> None:
    path = _messages_path(config)
    if path is None:
        raise RuntimeError("Could not determine mission folder from serverDZ.cfg")
    messages_save(path, rows)


register(
    EditorSpec(
        id="messages",
        title="Messages",
        group="Mission",
        kind="table",
        exists=_messages_exists,
        load=_messages_load,
        save=_messages_save,
        raw_targets=raw_targets_from((_messages_path, "xml")),
    )
)


# ---------------------------------------------------------------------------
# cfgspawnabletypes.xml (upsert-in-place by type name; attachment/cargo
# groups are summarized read-only — nested items are out of table scope)
# ---------------------------------------------------------------------------


@overridable("spawnabletypes")
def _spawnabletypes_path(config: DayzConfig):
    from app.xml.spawnabletypes_xml import get_spawnabletypes_path

    m_dir = mission_dir(config)
    return get_spawnabletypes_path(m_dir) if m_dir else None


def _spawnabletypes_load(config: DayzConfig) -> dict:
    return spawnabletypes_load(_spawnabletypes_path(config))


def _spawnabletypes_save(config: DayzConfig, rows: list[dict]) -> None:
    path = _spawnabletypes_path(config)
    if path is None:
        raise RuntimeError("cfgspawnabletypes.xml not found for the current mission")
    spawnabletypes_save(path, rows)


register(
    EditorSpec(
        id="spawnabletypes",
        title="Spawnable Types",
        group="Mission",
        kind="table",
        exists=lambda config: _spawnabletypes_path(config) is not None,
        load=_spawnabletypes_load,
        save=_spawnabletypes_save,
        raw_targets=raw_targets_from((_spawnabletypes_path, "xml")),
    )
)


# ---------------------------------------------------------------------------
# cfgeconomycore.xml (full add/remove sync — a row IS an entry: either a
# <default> or a <ce>/<file> registration)
#
# The `custom` folder is the one exception: every entry in it is *generated* by
# the mod XML merge (xml_economy.py wipes and rebuilds custom/ + its <ce> block
# on every merge, install and toggle), so editing it here is a lie that survives
# until the next `zsm run`. Those rows go out `readonly: True` and are re-read
# from the file on save, never taken from the request — a stale tab must not be
# able to drop a mod's registrations.
# ---------------------------------------------------------------------------

CUSTOM_CE_FOLDER = "custom"

# The six `type=` values DayZ accepts. Offered as a per-row enum and enforced on
# save — see `_economycore_save`.
from app.xml.economycore_xml import FILE_TYPE_HELP, FILE_TYPES

_ECONOMYCORE_READONLY_REASON = (
    "Entries under the custom/ folder are generated by the mod XML merge — "
    "custom/ and its <ce> block are wiped and rebuilt on every merge, install "
    "or mod toggle, so edits made here are discarded. Add your own fragments as "
    "manual XML files instead."
)


def _economycore_path(config: DayzConfig):
    from app.xml.economycore_xml import get_economycore_path

    m_dir = mission_dir(config)
    return get_economycore_path(m_dir) if m_dir else None


# What a missing folder of the admin's own needs, and the way that needs none.
_OWN_CE_FOLDER_ADVICE = (
    "Create it, with its files, in the mission folder. A mod's XML needs no folder of "
    "your own: Zedstead registers it under custom/ when the mod is installed (Configs → "
    "Mods → Mod files). For your own XML the supported way is a manual XML file "
    "(Configs → Custom config → Manual XML files): Zedstead writes and registers it, and keeps it "
    "through every merge and server update."
)


def _economycore_load(config: DayzConfig) -> dict:
    from app.xml.economycore_xml import (
        BOOLEAN_WORDS,
        DEFAULT_TYPES,
        KNOWN_DEFAULTS,
        parse_economycore_file,
    )

    path = _economycore_path(config)
    if path is None:
        return {"kind": "table", "columns": [], "rows": []}
    ef = parse_economycore_file(path)
    columns = [
        {"key": "section", "label": "Section", "type": "str"},
        {"key": "name", "label": "Name", "type": "str"},
        # A ce: row's value is a file type, described under each in its picker.
        {"key": "value", "label": "Value / type", "type": "str", "choice_help": FILE_TYPE_HELP},
    ]
    rows = []
    for d in ef.defaults:
        row = {"id": f"default:{d.name}", "values": {"section": "defaults", "name": d.name, "value": d.value}}
        help_text = KNOWN_DEFAULTS.get(d.name)
        if help_text:
            row["help"] = {"name": help_text, "value": help_text}
        if DEFAULT_TYPES.get(d.name) == "bool":
            row["choices"] = {"value": list(BOOLEAN_WORDS)}
        rows.append(row)
    m_dir = mission_dir(config)
    warnings: list[str] = []
    warned_folders: set[str] = set()
    for rc in ef.rootclasses:
        generated = rc.folder == CUSTOM_CE_FOLDER
        folder_exists = m_dir is None or (m_dir / rc.folder).is_dir()
        if not folder_exists and rc.folder not in warned_folders:
            warned_folders.add(rc.folder)
            warning = (
                f'<ce folder="{rc.folder}"> — the mission has no {rc.folder}/ folder, '
                "so DayZ loads nothing from it."
            )
            if not generated:
                warning += f" {_OWN_CE_FOLDER_ADVICE}"
            warnings.append(warning)
        for f in rc.files:
            row = {
                "id": f"ce:{rc.folder}:{f.name}",
                "values": {"section": f"ce:{rc.folder}", "name": f.name, "value": f.type},
            }
            if generated:
                row["readonly"] = True
            else:
                # The `value` column is shared: a `defaults` row holds a number,
                # a `ce:` row holds a CE type. So the enum is per row, not per
                # column. Server-side validation on save is not redundant — a
                # stale tab or a direct API call can post anything, and an
                # unknown type makes DayZ refuse the whole economy load.
                row["choices"] = {"value": list(FILE_TYPES)}
                # custom/ is legitimately empty until the first merge, so only
                # non-generated registrations are checked per file.
                if folder_exists and m_dir is not None and not (m_dir / rc.folder / f.name).is_file():
                    warnings.append(f"{rc.folder}/{f.name} is registered but not present.")
            rows.append(row)
    return {
        "kind": "table",
        "columns": columns,
        "rows": rows,
        "readonly_reason": _ECONOMYCORE_READONLY_REASON,
        "warnings": warnings,
    }


def _economycore_save(config: DayzConfig, rows: list[dict]) -> None:
    from app.xml.economycore_xml import (
        add_default,
        add_file_to_rootclass,
        add_rootclass,
        default_value_problem,
        parse_economycore_file,
        remove_default,
        remove_file_from_rootclass,
        remove_rootclass,
        save_economycore_file,
        set_default_value,
    )

    path = _economycore_path(config)
    if path is None:
        raise RuntimeError("cfgeconomycore.xml not found for the current mission")
    current_defaults = {d.name: d.value for d in parse_economycore_file(path).defaults}

    wanted_defaults: dict[str, str] = {}
    wanted_ce: dict[str, dict[str, str]] = {}  # folder -> {file name: type}
    for row in rows:
        values = row.get("values", row)
        section = str(values.get("section", "")).strip()
        name = str(values.get("name", "")).strip()
        value = str(values.get("value", "")).strip()
        if not section and not name:
            continue  # an added row nobody filled in
        # A row this check would skip is an entry the save removes, so a typo
        # is refused rather than read as "delete it".
        folder = section[3:].strip() if section.startswith("ce:") else ""
        if section != "defaults" and not folder:
            shown = f"Section {section!r}" if section else "An empty section"
            raise ValueError(
                f"Row {name or '(no name)'}: {shown} is not defaults or ce:<folder> "
                f'(the folder of a <ce folder="…"> block, e.g. ce:db).'
            )
        if not name:
            raise ValueError(f"A row in section {section} has no name; give it one or remove the row.")
        if section == "defaults":
            # Judged only when it changes: a value the file already holds
            # (Bohemia writes a few as "1") still saves untouched.
            if current_defaults.get(name) != value:
                problem = default_value_problem(name, value)
                if problem:
                    raise ValueError(problem)
            wanted_defaults[name] = value
        else:
            # custom/ is generated — never take it from the request (see the
            # section comment above). A row that was not already there (added,
            # or moved in from another section) used to be dropped while the
            # save said "Saved."; it is refused instead.
            if folder == CUSTOM_CE_FOLDER and row.get("id") != f"ce:{folder}:{name}":
                raise SaveElsewhere(
                    f"Row {name}: the custom/ folder is written by the mod XML merge, so a "
                    "row can't be added to or moved into ce:custom here. To register a file "
                    "yourself, set Section to another folder, e.g. ce:myfiles; that folder and "
                    "the file must be in the mission folder (Zedstead doesn't create them). Or add "
                    "it as a manual XML file, which Zedstead keeps and registers for you.",
                    go="manual-xml-links",
                )
            if folder != CUSTOM_CE_FOLDER:
                if value not in FILE_TYPES:
                    # Validated before the snapshot, so a rejected save leaves
                    # no trace. DayZ refuses to load the *whole* economy on an
                    # unknown type, so this must never reach the file.
                    raise ValueError(
                        f"Unknown central-economy file type {value!r} for "
                        f"{folder}/{name} — DayZ accepts only: "
                        + ", ".join(FILE_TYPES)
                    )
                wanted_ce.setdefault(folder, {})[name] = value

    snapshot(path, trigger="editor")
    ef = parse_economycore_file(path)

    # Defaults: update / add / remove.
    for d in list(ef.defaults):
        if d.name in wanted_defaults:
            value = wanted_defaults.pop(d.name)
            if value != d.value:
                set_default_value(d, value)
        else:
            remove_default(ef, d)
    for name, value in wanted_defaults.items():
        add_default(ef, name, value)

    # CE rootclass file registrations: update / add / remove.
    for rc in list(ef.rootclasses):
        if rc.folder == CUSTOM_CE_FOLDER:
            continue
        wanted_files = wanted_ce.pop(rc.folder, {})
        for f in list(rc.files):
            if f.name in wanted_files:
                f.type = wanted_files.pop(f.name)
                f.element.set("type", f.type)
            else:
                remove_file_from_rootclass(rc, f)
        for name, file_type in wanted_files.items():
            add_file_to_rootclass(rc, name, file_type)
        if not rc.files:
            remove_rootclass(ef, rc)
    for folder, files in wanted_ce.items():
        rc = add_rootclass(ef, folder)
        for name, file_type in files.items():
            add_file_to_rootclass(rc, name, file_type)

    save_economycore_file(ef)


register(
    EditorSpec(
        id="economycore",
        title="Economy Core",
        group="Mission",
        kind="table",
        exists=lambda config: _economycore_path(config) is not None,
        load=_economycore_load,
        save=_economycore_save,
        restore=restore_from(_economycore_path),
        raw_targets=raw_targets_from((_economycore_path, "xml")),
    )
)


# ---------------------------------------------------------------------------
# cfgRandomPresets.xml (full add/remove sync of presets and their items)
# ---------------------------------------------------------------------------


@overridable("randompresets")
def _randompresets_path(config: DayzConfig):
    from app.xml.randompresets_xml import get_randompresets_path

    m_dir = mission_dir(config)
    return get_randompresets_path(m_dir) if m_dir else None


def _randompresets_load(config: DayzConfig) -> dict:
    from app.xml.randompresets_xml import FIELD_DESCRIPTIONS, PRESET_KINDS, parse_randompresets_file

    path = _randompresets_path(config)
    if path is None:
        return {"kind": "table", "columns": [], "rows": []}
    rpf = parse_randompresets_file(path)
    owners = owner_map(rpf.tree.getroot())
    columns = _apply_col_help([
        {
            "key": "kind", "label": "Kind", "type": "str", "choices": list(PRESET_KINDS),
            "choice_help": {
                "cargo": "Fills a container's inventory.",
                "attachments": "Fills its attachment slots (optics, hats, bags).",
            },
        },
        {"key": "preset", "label": "Preset", "type": "str"},
        {"key": "preset_chance", "label": "Preset chance", "type": "str", "share": True},
        {"key": "item", "label": "Item", "type": "str"},
        {"key": "chance", "label": "Item chance", "type": "str", "share": True},
    ], {
        "kind": FIELD_DESCRIPTIONS["kind"],
        "preset": FIELD_DESCRIPTIONS["preset_name"],
        "preset_chance": FIELD_DESCRIPTIONS["preset_chance"],
        "item": FIELD_DESCRIPTIONS["item_name"],
        "chance": FIELD_DESCRIPTIONS["item_chance"],
    })
    # A row id is positions, `<preset>#<item>`, never names: a mod extends a
    # preset with a second <cargo> of the same name, and a preset may list one
    # item twice.
    rows = []
    for pi, p in enumerate(rpf.presets):
        base = {"kind": p.kind, "preset": p.name, "preset_chance": p.chance}
        if not p.items:
            rows.append(_tag_owner(
                {"id": f"{pi}#", "values": {**base, "item": "", "chance": ""}}, owners, p.element,
            ))
        for ii, item in enumerate(p.items):
            rows.append(_tag_owner(
                {"id": f"{pi}#{ii}", "values": {**base, "item": item.name, "chance": item.chance}},
                owners, item.element, p.element,
            ))
    return _with_manual_reason({"kind": "table", "columns": columns, "rows": rows})


def _randompresets_row(row_id, rpf):
    """The preset and item a row id from `_randompresets_load` names (the item
    is None for an empty preset's row). None for a client-made id."""
    if not isinstance(row_id, str):
        return None
    pi, sep, ii = row_id.partition("#")
    if not sep or not pi.isdigit() or int(pi) >= len(rpf.presets):
        return None
    preset = rpf.presets[int(pi)]
    if not ii:
        return preset, None
    if not ii.isdigit() or int(ii) >= len(preset.items):
        return None
    return preset, preset.items[int(ii)]


def _randompresets_save(config: DayzConfig, rows: list[dict]) -> None:
    from app.xml.randompresets_xml import (
        PRESET_KINDS,
        add_item_to_preset,
        add_preset,
        chance_text,
        item_chance_text,
        parse_randompresets_file,
        remove_cargo_preset,
        remove_item_from_preset,
        same_chance,
        save_randompresets_file,
        set_item_chance,
        set_item_name,
        set_preset_chance,
    )

    path = _randompresets_path(config)
    if path is None:
        raise RuntimeError("cfgRandomPresets.xml not found for the current mission")
    rpf = parse_randompresets_file(path)

    # Elements are changed in place and only where a row says something else,
    # so the START/END MOD markers stay around a mod's presets and an untouched
    # element gains no attribute. Every chance is checked before anything is
    # changed: a preset's from 0 to 1 (`chance_text`), an item's 0 or more
    # (`item_chance_text`).
    was = {id(p): p.chance for p in rpf.presets}
    listed: set[int] = set()   # presets and items some row still names
    edits: list = []
    additions: list[tuple[str, str, str, str, str]] = []
    for row in rows:
        values = row.get("values", row)
        name = str(values.get("preset", "")).strip()
        if not name:
            continue
        found = _randompresets_row(row.get("id"), rpf)
        kind = str(values.get("kind") or (found[0].kind if found else "cargo")).strip()
        if kind not in PRESET_KINDS:
            raise ValueError(f"Preset {name}: the kind is cargo or attachments, not {kind!r}")
        preset_chance = str(values.get("preset_chance", "")).strip()
        if preset_chance:
            chance_text(preset_chance, f"Preset {name}")
        item_name = str(values.get("item", "")).strip()
        item_chance = str(values.get("chance", "")).strip() or "1.0"
        if item_name:
            item_chance_text(item_chance, f"Item {item_name}")
        if found is None or found[0].name != name or found[0].kind != kind:
            # A new row, or one whose preset was renamed: it moves to that preset.
            additions.append((kind, name, preset_chance, item_name, item_chance))
            continue
        edits.append((found, preset_chance, item_name, item_chance))

    # Every row of a preset repeats its chance, and the page changes only the
    # row the admin edited. A row is compared with the chance as loaded, never
    # with one an earlier row set, or the next row's old value set it back.
    new_chance: dict[int, str] = {}
    for (preset, _item), preset_chance, _item_name, _item_chance in edits:
        if not preset_chance or same_chance(was[id(preset)], preset_chance):
            continue
        other = new_chance.setdefault(id(preset), preset_chance)
        if not same_chance(other, preset_chance):
            raise ValueError(
                f"Preset {preset.name}: two rows give it different chances ({other} and {preset_chance})"
            )

    for (preset, item), preset_chance, item_name, item_chance in edits:
        listed.add(id(preset))
        if id(preset) in new_chance:
            set_preset_chance(preset, new_chance.pop(id(preset)))
        if item is None:
            if item_name:
                listed.add(id(add_item_to_preset(preset, item_name, item_chance)))
        elif item_name:
            listed.add(id(item))
            if item_name != item.name:
                set_item_name(item, item_name)
            if not same_chance(item.chance, item_chance):
                set_item_chance(item, item_chance)

    for preset in list(rpf.presets):
        if id(preset) not in listed:
            remove_cargo_preset(rpf, preset)
            continue
        for item in list(preset.items):
            if id(item) not in listed:
                remove_item_from_preset(preset, item)

    # The first preset of a name is the mission's own; a mod's comes after it.
    by_name: dict = {}
    for preset in rpf.presets:
        by_name.setdefault((preset.kind, preset.name), preset)
    for kind, name, preset_chance, item_name, item_chance in additions:
        preset = by_name.get((kind, name))
        if preset is None:
            preset = by_name[(kind, name)] = add_preset(rpf, name, preset_chance or "1.0", kind)
        if item_name:
            add_item_to_preset(preset, item_name, item_chance)

    snapshot(path, trigger="editor")  # after every check: a refused save leaves no version
    save_randompresets_file(rpf)


register(
    EditorSpec(
        id="randompresets",
        title="Random Presets",
        group="Mission",
        kind="table",
        exists=lambda config: _randompresets_path(config) is not None,
        load=_randompresets_load,
        save=_keeping_manual_rows(_randompresets_load, _randompresets_save, "preset"),
        restore=restore_from(_randompresets_path),
        raw_targets=raw_targets_from((_randompresets_path, "xml")),
    )
)


# ---------------------------------------------------------------------------
# cfgenvironment.xml (upsert-in-place: territory population rows + agent
# chance rows; add/remove of territories stays in the raw file)
# ---------------------------------------------------------------------------


def _environment_path(config: DayzConfig):
    from app.xml.environment_xml import get_environment_path

    m_dir = mission_dir(config)
    return get_environment_path(m_dir) if m_dir else None


def _environment_load(config: DayzConfig) -> dict:
    from app.xml.environment_xml import parse_environment_file

    path = _environment_path(config)
    if path is None:
        return {"kind": "table", "columns": [], "rows": []}
    ef = parse_environment_file(path)
    columns = _apply_col_help([
        {"key": "territory", "label": "Territory", "type": "str"},
        {"key": "agent", "label": "Agent", "type": "str"},
        {"key": "chance", "label": "Agent chance", "type": "str"},
        {"key": "global_max", "label": "Global max", "type": "str"},
        {"key": "zone_min", "label": "Zone min", "type": "str"},
        {"key": "zone_max", "label": "Zone max", "type": "str"},
        {"key": "zone_cooldown", "label": "Zone cooldown", "type": "str", "unit": "seconds"},
    ], _environment_col_help())
    rows = []
    for terr in ef.territories:
        rows.append(
            {
                "id": f"terr:{terr.name}",
                "values": {
                    "territory": terr.name,
                    "agent": "",
                    "chance": "",
                    "global_max": terr.item.global_count_max if terr.item else "",
                    "zone_min": terr.item.zone_count_min if terr.item else "",
                    "zone_max": terr.item.zone_count_max if terr.item else "",
                    "zone_cooldown": terr.item.zone_touch_period if terr.item else "",
                },
                "locked": list(_ENV_TERRITORY_LOCKED),
            }
        )
        for agent in terr.agents:
            rows.append(
                {
                    "id": f"agent:{terr.name}:{agent.type}",
                    "values": {
                        "territory": terr.name,
                        "agent": agent.type,
                        "chance": agent.chance,
                        "global_max": "",
                        "zone_min": "",
                        "zone_max": "",
                        "zone_cooldown": "",
                    },
                    "locked": list(_ENV_AGENT_LOCKED),
                }
            )
    # The table edits the values of what the file has; a territory or an agent
    # is added or removed in the Raw tab.
    return {"kind": "table", "columns": columns, "rows": rows, "add_row": False, "remove_row": False}


def _environment_col_help() -> dict:
    from app.xml.environment_xml import TERRITORY_DESCRIPTIONS as d

    # Bohemia's files set the three counts only on AmbientHen and the cooldown
    # only on AmbientHare and AmbientFox; no Herd territory has any. That is
    # stock evidence, not a rule, so the cells stay open and the help says it.
    stock = " A whole number of 0 or more; empty leaves it as it is."
    herd = " Bohemia's files set it only on ambient territories, never on a Herd."
    return {
        "chance": "The agent's chance, a number of 0 or more.",
        "global_max": d["global_count_max"] + stock + herd,
        "zone_min": d["zone_count_min"] + stock + herd,
        "zone_max": d["zone_count_max"] + stock + herd,
        "zone_cooldown": d["zone_touch_period"] + stock + herd,
    }


# Cells a row of each kind cannot change: its identity, and the other kind's.
_ENV_POPULATION = ("global_max", "zone_min", "zone_max", "zone_cooldown")
_ENV_ITEM_ATTRS = ("global_count_max", "zone_count_min", "zone_count_max", "zone_touch_period")
_ENV_LABELS = {"global_max": "Global max", "zone_min": "Zone min", "zone_max": "Zone max",
               "zone_cooldown": "Zone cooldown"}
_ENV_TERRITORY_LOCKED = ("territory", "agent", "chance")


def _environment_number(values: dict, key: str, label: str, where: str, *, whole: bool) -> str | None:
    """A cell as the text to write, None when it is empty; a count is a whole
    number of 0 or more, a chance any number of 0 or more."""
    number = _number_cell(values, key, label, where)
    if number is None:
        return None
    if number < 0 or (whole and number != int(number)):
        kind = "a whole number" if whole else "a number"
        raise ValueError(f"{where}: {label} must be {kind} of 0 or more")
    return str(int(number)) if whole else str(values.get(key)).strip()


def _same_number(raw, current) -> bool:
    """*raw*, a cell, is the number *current* already is (a file's text, or a
    number from a parsed JSON file); never true for an empty cell."""
    try:
        return raw is not None and float(str(raw).strip()) == float(current)
    except (TypeError, ValueError):
        return False


_ENV_AGENT_LOCKED = ("territory", "agent", *_ENV_POPULATION)


def _environment_save(config: DayzConfig, rows: list[dict]) -> None:
    from app.xml.environment_xml import (
        parse_environment_file,
        save_environment_file,
        set_agent_chance,
        set_territory_population,
    )

    path = _environment_path(config)
    if path is None:
        raise RuntimeError("cfgenvironment.xml not found for the current mission")
    ef = parse_environment_file(path)
    # Rows are found by id, never by the Territory cell: a changed cell used
    # to land the row's values on another territory, or nowhere.
    terrs: dict[str, object] = {}
    agents: dict[str, object] = {}
    for terr in ef.territories:
        terrs.setdefault(f"terr:{terr.name}", terr)
        for agent in terr.agents:
            agents.setdefault(f"agent:{terr.name}:{agent.type}", agent)

    def cell(values: dict, key: str) -> str:
        raw = values.get(key)
        return "" if raw is None else str(raw).strip()

    changes = []
    for row in rows:
        values = row.get("values", row)
        row_id = str(row.get("id") or "")
        if row_id in terrs:
            terr = terrs[row_id]
            label, identity, empty = f"Territory {terr.name}", {"territory": terr.name}, ("agent", "chance")
        elif row_id in agents:
            agent = agents[row_id]
            terr_name = row_id.split(":", 2)[1]
            label = f"Agent {agent.type} in {terr_name}"
            identity, empty = {"territory": terr_name, "agent": agent.type}, _ENV_POPULATION
        else:
            raise ValueError(
                f"Row {row_id or cell(values, 'territory') or '(blank)'} is not a territory or an agent "
                "in the file. Add or remove territories and agents in the Raw tab."
            )
        for key, name in identity.items():
            if key in values and cell(values, key) != name:
                raise ValueError(f"{label}: the {key} name cannot be changed here; rename it in the Raw tab.")
        for key in empty:
            if cell(values, key):
                raise ValueError(f"{label}: {key} does not apply to this row; leave it empty.")
        if row_id in agents:
            # Only a changed value is written: an <agent> without the
            # attribute shows 0 and must not gain `chance="0"` from a save.
            chance = _environment_number(values, "chance", "Agent chance", label, whole=False)
            if chance is not None and not _same_number(chance, agent.chance):
                changes.append((row_id, chance))
        else:
            current = terr.item
            limits = []
            for key, attr in zip(_ENV_POPULATION, _ENV_ITEM_ATTRS):
                number = _environment_number(values, key, _ENV_LABELS[key], label, whole=True)
                # A value equal to the file's keeps the file's text ("" leaves it).
                same = current is not None and _same_number(number, getattr(current, attr))
                limits.append("" if number is None or same else number)
            changes.append((row_id, limits))

    for row_id, change in changes:
        if row_id in agents:
            set_agent_chance(agents[row_id], change)
        else:
            set_territory_population(terrs[row_id], *change)
    snapshot(path, trigger="editor")  # after every check: a refused save leaves no version
    save_environment_file(ef)


register(
    EditorSpec(
        id="environment",
        title="Environment",
        group="Mission",
        kind="table",
        exists=lambda config: _environment_path(config) is not None,
        load=_environment_load,
        save=_environment_save,
        raw_targets=raw_targets_from((_environment_path, "xml")),
    )
)


# ---------------------------------------------------------------------------
# cfgEffectArea.json (the rows replace Areas[]; a row that is an existing area,
# or a copy of one, keeps every key of that area the table has no column for)
# ---------------------------------------------------------------------------


def _effectarea_path(config: DayzConfig):
    from app.parsers.effectarea_json import get_effectarea_path

    m_dir = mission_dir(config)
    return get_effectarea_path(m_dir) if m_dir else None


def _effectarea_load(config: DayzConfig) -> dict:
    from app.parsers.effectarea_json import parse_effectarea_file

    path = _effectarea_path(config)
    if path is None:
        return {"kind": "table", "columns": [], "rows": []}
    eaf = parse_effectarea_file(path)
    columns = [
        {"key": "area_name", "label": "Name", "type": "str"},
        {"key": "type", "label": "Type", "type": "str"},
        {"key": "pos", "label": "Pos (x,y,z)", "type": "str"},
        {"key": "radius", "label": "Radius", "type": "float", "unit": "meters"},
        {"key": "pos_height", "label": "Pos height", "type": "float", "unit": "meters"},
        {"key": "neg_height", "label": "Neg height", "type": "float", "unit": "meters"},
        {"key": "inner_part_dist", "label": "Inner particle dist", "type": "int", "unit": "meters"},
        {"key": "outer_offset", "label": "Outer offset", "type": "int", "unit": "meters"},
    ]
    rows = [
        {
            "id": str(i),
            "values": {
                "area_name": a.area_name,
                "type": a.type,
                "pos": ",".join(str(v) for v in a.pos),
                "radius": a.radius,
                "pos_height": a.pos_height,
                "neg_height": a.neg_height,
                "inner_part_dist": a.inner_part_dist,
                "outer_offset": a.outer_offset,
            },
        }
        for i, a in enumerate(eaf.areas)
    ]
    # Each area's Data and PlayerData, every key of them, as a tree under the
    # row (plans/76 step 2b). Data repeats the position and sizes the columns
    # show; a save takes a column only where it was changed.
    for row, a in zip(rows, eaf.areas):
        row["nested"] = nested_of(a.raw or {}, _effectarea_tree(a.raw or {}))
    return {"kind": "table", "columns": columns, "rows": rows}


_EFFECTAREA_TREE = ("Data", "PlayerData")
_EFFECTAREA_NUMBERS = ("radius", "pos_height", "neg_height", "inner_part_dist", "outer_offset")


#: The keys of an area's ``Data`` that Bohemia's wiki marks ``float``
#: (`contaminated-areas-configuration.wiki`); the ``integer`` ones (ring counts
#: and distances) stay whole. JSON writes ``300.0`` as ``300``.
EFFECTAREA_FLOAT_KEYS = frozenset({"Pos", "Radius", "PosHeight", "NegHeight"})


def _effectarea_tree(entry: dict) -> list[str]:
    return [k for k in _EFFECTAREA_TREE if isinstance(entry.get(k), dict)]


def _effectarea_save(config: DayzConfig, rows: list[dict]) -> None:
    from app.parsers.effectarea_json import (
        EffectArea,
        cell_number,
        parse_effectarea_file,
        parse_pos,
        save_effectarea_file,
    )
    from app.web.editors._mod_json import source_of

    path = _effectarea_path(config)
    if path is None:
        raise RuntimeError("cfgEffectArea.json not found for the current mission")
    eaf = parse_effectarea_file(path)
    old_areas = list(eaf.areas)

    new_areas = []
    for n, row in enumerate(rows):
        values = row.get("values", row)
        source = source_of(str(row.get("id", "")))
        was = None
        if source is not None and int(source) < len(old_areas):
            # A duplicate is its source's kind of area, not a contaminated one.
            was = old_areas[int(source)]
            area = copy.deepcopy(was)
            if area.raw is not None:
                # The tree's edits go into the entry first; the attributes are
                # then read from it, so a column left as loaded keeps them.
                try:
                    apply_nested(area.raw, values, _effectarea_tree(area.raw), floats=EFFECTAREA_FLOAT_KEYS)
                except ValueError as exc:
                    raise ValueError(f"{was.area_name or f'Row {n + 1}'}: {exc}") from exc
                area = EffectArea.from_dict(area.raw)
        else:
            area = EffectArea()
        if "area_name" in values:
            area.area_name = str(values["area_name"])
        if "type" in values:
            area.type = str(values["type"])
        try:
            if "pos" in values:
                pos = parse_pos(values["pos"])
                if was is None or pos != was.pos:
                    area.pos = pos
            for field_name in _EFFECTAREA_NUMBERS:
                raw = values.get(field_name)
                if raw is None or not str(raw).strip():
                    continue
                # A value left as loaded is the file's own, a hand-written
                # fraction in a whole-number key included.
                if was is not None and _same_number(raw, getattr(was, field_name)):
                    continue
                setattr(area, field_name, cell_number(field_name, raw))
        except ValueError as exc:
            label = (was.area_name if was is not None else "") or str(values.get("area_name") or "")
            raise ValueError(f"{label or f'Row {n + 1}'}: {exc}") from exc
        new_areas.append(area)

    snapshot(path, trigger="editor")
    eaf.areas = new_areas
    save_effectarea_file(eaf)


register(
    EditorSpec(
        id="effectarea",
        title="Effect Areas",
        group="Mission",
        kind="table",
        exists=lambda config: _effectarea_path(config) is not None,
        load=_effectarea_load,
        save=_effectarea_save,
        raw_targets=raw_targets_from((_effectarea_path, "json")),
    )
)
