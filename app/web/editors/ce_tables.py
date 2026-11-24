"""Path-addressed table adapters for the six central-economy XML kinds.

Same ``{kind:"table", columns, rows}`` payload the config editors serve, but
keyed on a ``Path`` instead of an ``EditorSpec`` closure over the mission
layout. That lets the *generated* ``mpmissions/<mission>/custom/*.xml`` files
— which have no fixed identity, so no EditorSpec — get the identical
structured "Basic" view the mission files have (``routers/modxml.py``).

``mission_tables``' events / spawnabletypes / economy / messages adapters
delegate here, so each kind has exactly one column set and one save path. The
exception is ``globals``, whose mission file keeps a *form* editor — see the
section comment above `globals_load`.
"""

from __future__ import annotations

import math
from pathlib import Path

import xml.etree.ElementTree as ET

from app.shared.config_versions import snapshot
from app.web.editors._help import described_choices, field_help, unit_attr
from app.web.editors._nested import NESTED, NESTED_BASE, _same
from app.web.editors._xml_nested import (
    Part,
    after,
    apply_parts,
    attrs_of,
    attrs_part,
    name_list,
    nested_of,
    set_attrs,
    set_texts,
    sync_list,
    texts_of,
)
from app.xml.events_xml import EVENT_CHILD_ORDER
from app.xml.types_xml import TYPE_CHILD_ORDER

# Web-authored help for columns the FIELD_DESCRIPTIONS maps don't describe
# (identifiers, summaries).
EVENTS_COL_HELP = {"name": "Event name — the <event> type in events.xml (e.g. spawn/loot events)."}
SPAWNABLE_COL_HELP = {
    "name": "Classname of the spawnable type.",
    "groups": "The attachment and cargo groups. Click to open them under the row and edit them.",
}
TYPES_COL_HELP = {
    "name": "Classname of the item this entry governs.",
    "nominal": "Target number of this item alive on the map at once.",
    "min": "Minimum number kept alive — the economy respawns below this.",
    "lifetime": "Seconds an untouched item survives before cleanup.",
    "restock": "Seconds before the economy may respawn the item after it falls below min.",
    "quantmin": "Minimum fill percentage when spawned (-1 = not applicable).",
    "quantmax": "Maximum fill percentage when spawned (-1 = not applicable).",
    "cost": "Spawn priority weight relative to other types.",
    "category": "Economy category (food, tools, weapons, …).",
    "tag": "Placement tag (floor, shelves, ground).",
}

GLOBALS_COL_HELP = {
    "name": "Engine variable name — globals.xml is a fixed set, inventing one does nothing.",
    "type": (
        "Value type: 0 = integer, 1 = float, 2 = string. An existing variable "
        "keeps its type; a documented one takes the wiki's."
    ),
}
ECONOMY_COL_HELP = {
    "name": "The central-economy system this row toggles (animals, vehicles, dynamic events, …).",
}
# Every time in messages.xml is in MINUTES, delay and deadline included — the
# labels below said seconds until 2026 Q3. Bohemia's own shipped file settles
# it: `<deadline>600</deadline>` is annotated "shuts down server in 10 hours"
# and `<delay>10</delay>` "10 minutes after player connect"
# (dayz-docs/vanilla/dayzOffline.chernarusplus/db/messages.xml, and DayZ:Server
# Messages in dayz-docs/wiki/). `server_status.get_shutdown_deadline` already
# reads the same field as minutes — these columns were the odd one out.
MESSAGES_COL_HELP = {
    "type": "When the message fires: OnConnect, Shutdown, Repeat, or Other. "
    "Repeat and Other are read off the repeat value: a message with a repeat "
    "above 0 and neither other flag is a Repeat message.",
    "text": "The message text shown to players in chat. Max 160 characters; "
    "#name, #port and #tmin are replaced with the server name, its port and the "
    "minutes left on a countdown.",
    "delay": "For OnConnect messages only: minutes after a player connects "
    "before it is shown. 0 = immediately. Every time is a whole number of minutes.",
    "repeat": "Minutes between repeats; above 0 sends the message to everyone "
    "at that interval, whatever its type. An OnConnect "
    "message with a repeat is also sent to everyone at that interval.",
    "deadline": "For countdown messages: minutes until the countdown reaches "
    "zero. Broadcast automatically at 90, 60, 45, 30, 20, 15, 10, 5, 2 and 1 "
    "minutes left; with Shutdown, the server stops when it hits zero, so a "
    "Shutdown message needs a deadline above 0.",
}

EVENT_NUMERIC_COLUMNS = ["nominal", "min", "max", "lifetime", "restock"]
# The unit of an events.xml number, shown in its cell (webui TableEditor).
_EVENT_UNITS = {"lifetime": "seconds", "restock": "seconds"}
TYPES_NUMERIC_COLUMNS = ["nominal", "min", "lifetime", "restock", "quantmin", "quantmax", "cost"]


# ---------------------------------------------------------------------------
# The nested part of each row (plans/76 step 3, `_xml_nested`): the children a
# column cannot show, as the JSON shape the row's tree edits.
# ---------------------------------------------------------------------------

# Bohemia's order of a <type>'s and an <event>'s children: where a first one
# of a kind goes when the element had none. Kept beside their parsers, whose
# setters insert by the same order (imported at the top).


def type_parts(*keys: str) -> dict[str, Part]:
    """A <type>'s usage, value and tag names and its flags, the ones *keys*
    names (a surface leaves out what it already has a column for)."""
    from app.xml.types_xml import FLAG_NAMES

    every = {
        "usage": name_list("usage", TYPE_CHILD_ORDER),
        "value": name_list("value", TYPE_CHILD_ORDER),
        "tag": name_list("tag", TYPE_CHILD_ORDER),
        "flags": attrs_part("flags", TYPE_CHILD_ORDER, {f: 0 for f in FLAG_NAMES}),
    }
    return {k: every[k] for k in keys}


#: Tree keys whose 0/1 values are on/off flags (``nested_switches``): the
#: tree draws them as switches and they still save as 0 or 1. A type's and an
#: event's ``<flags>`` attributes are all 0/1 in Bohemia's files.
FLAG_PARTS = ["flags"]


# The custom/ types table already has Category and Tag columns.
TYPES_TABLE_PARTS = type_parts("usage", "value", "flags")

# An event's leaf settings no column shows, by tag, as the event has them.
_EVENT_COLUMN_TAGS = {*EVENT_NUMERIC_COLUMNS, "active", "flags", "children"}


def _event_children_read(event: ET.Element) -> list[dict]:
    box = event.find("children")
    return [] if box is None else [attrs_of(c) for c in box.findall("child")]


def _need_name(entries: list[dict], key: str, what: str) -> None:
    """A new entry from a template arrives with its name blank; DayZ has no use
    for a child or an item that names no class, so it is refused, not written."""
    for n, entry in enumerate(entries, 1):
        if isinstance(entry, dict) and key in entry and not str(entry[key] or "").strip():
            raise ValueError(f"{what} {n} needs a class name in {key!r}")


def _event_children_write(event: ET.Element, entries: list[dict]) -> None:
    _need_name(entries, "type", "child")
    box = event.find("children")
    if box is None:
        if not entries:
            return
        box = ET.Element("children")
        event.insert(after(EVENT_CHILD_ORDER, "children")(event), box)
    sync_list(
        box, box.findall("child"), entries,
        read=attrs_of, write=set_attrs, make=lambda _e: ET.Element("child"), at=len,
    )


def _event_parts() -> dict[str, Part]:
    from app.xml.events_xml import EVENT_FLAG_NAMES

    return {
        "settings": Part(
            lambda e: texts_of(e, _EVENT_COLUMN_TAGS),
            lambda e, new: set_texts(e, new, _EVENT_COLUMN_TAGS),
        ),
        "flags": attrs_part("flags", EVENT_CHILD_ORDER, {f: 0 for f in EVENT_FLAG_NAMES}),
        "children": Part(_event_children_read, _event_children_write),
    }


EVENT_PARTS = _event_parts()

# A spawnable type's <attachments> and <cargo> groups, one list in file order:
# {"kind": tag, its attributes, "items": [each <item>'s attributes]}.
SPAWNABLE_GROUP_TAGS = ("attachments", "cargo")


def _group_read(group: ET.Element) -> dict:
    return {"kind": group.tag, **attrs_of(group), "items": [attrs_of(i) for i in group.findall("item")]}


def _set_only(elem: ET.Element, attrs: dict) -> None:
    """*elem*'s attributes made *attrs*: changed ones written, ones the entry
    no longer has dropped. Every attribute is in the tree (`attrs_of`), so a
    missing one was taken out, or the entry is new and was copied from a
    neighbour whose `preset` or `chance` it must not inherit."""
    for key in [k for k in elem.attrib if k not in attrs]:
        del elem.attrib[key]
    set_attrs(elem, attrs)


def _group_write(group: ET.Element, entry: dict) -> None:
    kind = str(entry.get("kind", group.tag)).strip()
    if kind not in SPAWNABLE_GROUP_TAGS:
        raise ValueError(f"a group is attachments or cargo, not {kind!r}")
    group.tag = kind
    _need_name(entry.get("items") or [], "name", "item")
    _set_only(group, {k: v for k, v in entry.items() if k not in ("kind", "items")})
    sync_list(
        group, group.findall("item"), entry.get("items") or [],
        read=attrs_of, write=_set_only, make=lambda _e: ET.Element("item"), at=len,
    )


def _groups_of(spawnable: ET.Element) -> list[ET.Element]:
    return [c for c in spawnable if c.tag in SPAWNABLE_GROUP_TAGS]


def _groups_write(spawnable: ET.Element, entries: list[dict]) -> None:
    sync_list(
        spawnable, _groups_of(spawnable), entries,
        read=_group_read, write=_group_write,
        make=lambda e: ET.Element(str(e.get("kind") or "attachments")), at=len,
    )


def _fit_attr(key: str, value, where: str, *, item: bool = False):
    """One attribute of a group or an item as the tree sent it, checked: a
    group's chance is a fraction from 0 to 1 (`spawnabletypes_xml.fraction`),
    an item's a number of 0 or more (`item_weight`: Sakhal's stock items
    carry 40.00), any other attribute a single value."""
    from app.xml.spawnabletypes_xml import fraction, item_weight

    if isinstance(value, (dict, list)):
        raise ValueError(f"{where}: {key} must be a single value")
    if key == "chance":
        number = (item_weight if item else fraction)(value, f"{where} chance")
        if number is None:
            raise ValueError(f"{where}: chance can't be left empty")
        return number
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{where}: {key} is not a number")
    return value


def _fit_groups(new, where: str) -> list[dict]:
    """The groups a row's tree sends, checked one attribute at a time.

    Not `_nested.conform`: that fits each entry to the keys a sibling has,
    and a group or item here may carry `chance` or not (FirstAidKit's items
    have none; a preset group has `preset` where an inline one has items),
    so adding one from the template was refused."""
    if not isinstance(new, list):
        raise ValueError(f"{where}: groups must be a list")
    out = []
    for i, group in enumerate(new, 1):
        at = f"{where}: group {i}"
        if not isinstance(group, dict):
            raise ValueError(f"{at} must be an object")
        items = group.get("items") or []
        if not isinstance(items, list) or not all(isinstance(it, dict) for it in items):
            raise ValueError(f"{at}: items must be a list of objects")
        fitted = {k: _fit_attr(k, v, at) for k, v in group.items() if k != "items"}
        fitted["items"] = [
            {k: _fit_attr(k, v, f"{at}, item {j}", item=True) for k, v in item.items()}
            for j, item in enumerate(items, 1)
        ]
        out.append(fitted)
    return out


def _apply_groups(spawnable: ET.Element, values: dict, where: str) -> None:
    """Write a row's edited groups (`$nested.groups`) into *spawnable*. A
    stale base is refused the way `apply_nested` refuses one."""
    edits = (values or {}).get(NESTED)
    if not isinstance(edits, dict) or "groups" not in edits:
        return
    bases = (values or {}).get(NESTED_BASE) or {}
    current = SPAWNABLE_PARTS["groups"].read(spawnable)
    if not isinstance(bases, dict) or "groups" not in bases or not _same(bases["groups"], current):
        raise ValueError(
            f"{where}: groups changed in the file since this page loaded it. Reload the page and "
            "make the change again."
        )
    fitted = _fit_groups(edits["groups"], where)
    try:
        _groups_write(spawnable, fitted)
    except ValueError as e:
        raise ValueError(f"{where}: {e}") from None


#: What "Add" puts into an empty list (plans/76, ``nested_templates``): the
#: attributes Bohemia's own entries carry, the class name left for the admin.
EVENT_CHILD_TEMPLATE = {"lootmax": 0, "lootmin": 0, "max": 0, "min": 0, "type": ""}
SPAWNABLE_ITEM_TEMPLATE = {"name": "", "chance": 1.0}
SPAWNABLE_TEMPLATES = {
    "groups": {"kind": "attachments", "chance": 1.0, "items": [SPAWNABLE_ITEM_TEMPLATE]},
    "items": SPAWNABLE_ITEM_TEMPLATE,
}

SPAWNABLE_PARTS = {
    "groups": Part(lambda t: [_group_read(g) for g in _groups_of(t)], _groups_write),
}


def mission_limits(mission_dir: Path | None) -> dict[str, set[str]] | None:
    """The category, tag, usage and value names the mission defines, or
    None when it has no cfglimitsdefinition.xml that can be read.

    A usage or value also takes the group names in cfglimitsdefinitionuser.xml
    (dayz-docs/CLAUDE.md: the vocabulary is the mission's own limits files).
    """
    from app.xml.limitsdefinition_xml import (
        get_limitsdefinition_path,
        get_limitsdefinitionuser_path,
        limits_names,
        parse_limitsdefinitionuser,
    )

    if mission_dir is None or not mission_dir.is_dir() or get_limitsdefinition_path(mission_dir) is None:
        return None
    names = {kind: set(found) for kind, found in limits_names(mission_dir).items()}
    if not any(names.values()):
        return None  # there, but unreadable: limits_names answers empty
    try:
        user_path = get_limitsdefinitionuser_path(mission_dir)
        user = parse_limitsdefinitionuser(user_path) if user_path else None
    except (ET.ParseError, OSError, ValueError):
        user = None
    if user is not None:
        names["usage"] |= {g.name for g in user.usage_groups if g.name}
        names["value"] |= {g.name for g in user.value_groups if g.name}
    return names


def limits_choices(mission_dir: Path | None) -> dict[str, list[str]]:
    """The names a type's category, tag, usage and value may take: what the
    mission's limits files define, or zsm's stock names when it has none."""
    from app.xml.types_xml import KNOWN_CATEGORIES, KNOWN_USAGES, KNOWN_VALUES

    defined = mission_limits(mission_dir)
    if defined is None:
        defined = {"category": set(KNOWN_CATEGORIES), "tag": set(),
                   "usage": set(KNOWN_USAGES), "value": set(KNOWN_VALUES)}
    return {kind: sorted(defined[kind]) for kind in ("category", "tag", "usage", "value")}


def undefined_names_problem(
    where: str, elem: ET.Element, before: dict[str, set[str]], defined: dict[str, set[str]] | None
) -> str | None:
    """A sentence for the limits names *elem* now uses that the mission does
    not define and it did not use before; None when there are none or the
    mission has no limits file to check against."""
    from app.xml.types_xml import limit_names_of, undefined_limit_names

    if defined is None:
        return None
    missing = undefined_limit_names(limit_names_of(elem), defined, before)
    if not missing:
        return None
    return (
        f"{where}: {', '.join(missing)} is not defined in the mission's "
        "cfglimitsdefinition.xml; the economy ignores a name it does not define."
    )


def nested_help(desc_map: dict[str, str]) -> dict[str, str]:
    """`nested_help` for a payload: each key of a row's nested parts (a type's
    flags, an event's flags) with its help, which the tree shows in the key's
    tooltip (webui JsonTree.jsx)."""
    return {key: text for key in desc_map if (text := field_help(desc_map, key))}


def apply_col_help(columns: list[dict], *maps: dict) -> list[dict]:
    """Set `col["help"]` from the first `maps` entry that has the column key."""
    for col in columns:
        for m in maps:
            help_text = field_help(m, col["key"])
            if help_text:
                col["help"] = help_text
                break
    return columns


def _empty() -> dict:
    return {"kind": "table", "columns": [], "rows": []}


def _int(value, fallback: int) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return fallback


def _row_name(row: dict, values: dict, known) -> str:
    """The entry a row names: its id when that is an entry of the file, else
    its Name cell. A row added in the table carries the table's own id
    ("new-<n>-<time>"), which is nobody's name; "" for one left unnamed."""
    row_id = str(row.get("id") or "").strip()
    if row_id in known:
        return row_id
    name = str(values.get("name") or "").strip()
    return name or ("" if row_id.startswith("new-") else row_id)


def _sync_rows(rows: list[dict], by_name: dict, *, what: str, remove, rename) -> list[tuple[str, dict]]:
    """Bring a file's entries in line with a table that sends all its rows.

    An entry no row's id names was removed in the table and goes
    (`remove(entry)`); a row whose Name cell differs from its id renames the
    entry (`rename(entry, new_name)`), and a name another entry has is refused,
    for an added row too (it would otherwise edit that entry). Returns
    ``(name, values)`` per row, in row order, for the caller to apply;
    *by_name* is kept up to date, so a name it lacks is an added row. Raises
    before the caller writes anything: the tree is only edited in memory.
    """
    ids = {str(row.get("id") or "").strip() for row in rows}
    for name in [n for n in by_name if n not in ids]:
        remove(by_name.pop(name))
    known = set(by_name)
    added: set[str] = set()
    out: list[tuple[str, dict]] = []
    for row in rows:
        values = row.get("values", row)
        name = _row_name(row, values, by_name)
        typed = str(values.get("name") or "").strip()
        if name in by_name and typed and typed != name:
            if typed in by_name or typed in added:
                raise ValueError(f"There is already {what} named {typed!r}; pick another name.")
            entry = by_name.pop(name)
            rename(entry, typed)
            by_name[typed] = entry
            name = typed
        elif name and str(row.get("id") or "").strip() not in known:
            # An added row: its name must be free (a duplicate in the file
            # itself has one row per entry and is left as it is).
            if name in by_name or name in added:
                raise ValueError(f"There is already {what} named {name!r}; pick another name.")
            added.add(name)
        if name:
            out.append((name, values))
    return out


# ---------------------------------------------------------------------------
# events.xml
# ---------------------------------------------------------------------------


def events_load(path: Path | None) -> dict:
    from app.xml.events_xml import (
        FIELD_DESCRIPTIONS, LIMIT_HELP, LIMIT_VALUES, POSITION_HELP, POSITION_VALUES, parse_events_file,
    )

    if path is None:
        return _empty()
    ef = parse_events_file(path)
    columns = [{"key": "name", "label": "Name", "type": "str"}]
    columns += [
        {"key": c, "label": c.capitalize(), "type": "int", **unit_attr(_EVENT_UNITS, c)}
        for c in EVENT_NUMERIC_COLUMNS
    ]
    columns.append({"key": "active", "label": "Active", "type": "bool"})
    apply_col_help(columns, FIELD_DESCRIPTIONS, EVENTS_COL_HELP)
    rows = [
        {
            "id": e.name,
            "values": {
                "name": e.name,
                **{c: getattr(e, c) for c in EVENT_NUMERIC_COLUMNS},
                "active": e.active,
            },
            "nested": nested_of(e.element, EVENT_PARTS),
        }
        for e in ef.entries
    ]
    return {
        "kind": "table", "columns": columns, "rows": rows,
        "nested_choices": {
            "position": described_choices(POSITION_VALUES, POSITION_HELP),
            "limit": described_choices(LIMIT_VALUES, LIMIT_HELP),
        },
        "nested_templates": {"children": EVENT_CHILD_TEMPLATE},
        "nested_switches": FLAG_PARTS,
        "nested_help": nested_help(FIELD_DESCRIPTIONS),
        # The table's Smart view (webui SMART_TABLES in TableEditor.jsx).
        "smart": "event",
    }


def _carry_spawns(path: Path | None, renames: list[tuple[str, str]]):
    """cfgeventspawns.xml with each renamed event's positions under its new
    name, edited in memory; None when there is nothing to write.

    Renaming an event in the Events table left its positions under the old
    name, so the event spawned nowhere and DayZ skipped the old block, with
    no warning. Only the mission's own <event> blocks
    are renamed. A mod's or a manual file's START/END MOD block is put back
    from its source by every merge, so a rename that would leave positions
    there under the old name is refused, and so is one onto a name whose own
    positions the mission already holds (which list to keep is the admin's
    call). An event with no positions of its own takes whatever the new name
    has, so renaming back after the old bug works.
    """
    if path is None or not renames:
        return None
    from app.ops.mission_baseline import owner_label
    from app.web.editors import SaveElsewhere
    from app.xml.eventspawns_xml import parse_eventspawns_file
    from app.xml.xml_manual import children_with_owner

    try:
        spawns = parse_eventspawns_file(path)
    except (OSError, ET.ParseError, ValueError) as e:
        raise ValueError(
            f"cfgeventspawns.xml cannot be read ({e}), so the event's positions cannot "
            "be renamed with it. Fix it on its Raw tab first."
        ) from None
    root = spawns.tree.getroot()
    changed = False
    for old, new in renames:
        own: dict[str, list[ET.Element]] = {old: [], new: []}
        others: list[str] = []
        for owner, elem in children_with_owner(root):
            name = elem.get("name", "")
            if elem.tag != "event" or name not in own:
                continue
            if owner is None:
                own[name].append(elem)
                if name == old:  # a mod's positions merged inside the mission's block
                    others += [o for o, _pos in children_with_owner(elem) if o]
            elif name == old:
                others.append(owner)
        if others:
            who = ", ".join(owner_label(o) for o in dict.fromkeys(others))
            raise SaveElsewhere(
                f"{who} also adds positions for {old} to cfgeventspawns.xml, and every "
                "merge puts them back under that name, where nothing would spawn. Rename "
                "the event in that file first (a mod's through Take over), or keep the name.",
                go="mod-xml",
            )
        if own[old] and own[new]:
            raise SaveElsewhere(
                f"cfgeventspawns.xml already has positions for {new}. Move or remove them, "
                f"or the {old} ones, there first.",
                go="eventspawns",
            )
        for elem in own[old]:
            elem.set("name", new)
            changed = True
    return spawns if changed else None


def events_save(path: Path, rows: list[dict], *, spawns_path: Path | None = None) -> None:
    """*spawns_path* is the mission's cfgeventspawns.xml: a renamed event's
    positions there are renamed with it (`_carry_spawns`)."""
    from app.ops.mission_baseline import owner_label
    from app.web.editors import SaveElsewhere
    from app.xml.eventspawns_xml import save_eventspawns_file
    from app.xml.xml_manual import children_with_owner
    from app.xml.events_xml import (
        NUMERIC_FIELDS,
        create_event,
        parse_events_file,
        remove_event,
        rename_event,
        save_events_file,
        set_event_active,
        set_event_numeric,
    )

    ef = parse_events_file(path, preserve_comments=True)
    by_name = {e.name: e for e in ef.entries}
    merged = {elem: owner for owner, elem in children_with_owner(ef.tree.getroot()) if owner}
    renames: list[tuple[str, str]] = []

    def rename(entry, new_name: str) -> None:
        if entry.element in merged:  # every merge puts it back under its old name
            raise SaveElsewhere(
                f"{entry.name} comes from {owner_label(merged[entry.element])}, which every "
                "merge puts back under this name. Rename it in that file.",
                go="manual-xml-links",
            )
        renames.append((entry.name, new_name))
        rename_event(entry, new_name)

    named = _sync_rows(
        rows, by_name, what="an event", remove=lambda e: remove_event(ef, e), rename=rename,
    )
    for name, values in named:
        entry = by_name.get(name)
        if entry is None:
            entry = by_name[name] = create_event(ef, name)
        # Only what changed is written, and a cell that is not a number keeps
        # the event's own: an untouched row is saved as the file had it.
        for field_name in EVENT_NUMERIC_COLUMNS:
            if field_name in values and field_name in NUMERIC_FIELDS:
                current = getattr(entry, field_name)
                number = _int(values[field_name], current)
                if number != current:
                    set_event_numeric(entry, field_name, number)
        if "active" in values and bool(values["active"]) != entry.active:
            set_event_active(entry, bool(values["active"]))
        apply_parts(entry.element, EVENT_PARTS, values, where=name)
    # Another event's <secondary> names it too; after the rows, so no row's
    # tree base changed under it.
    for old, new in renames:
        for entry in ef.entries:
            secondary = entry.element.find("secondary")
            if secondary is not None and entry.element not in merged and (
                (secondary.text or "").strip() == old
            ):
                secondary.text = new
    spawns = _carry_spawns(spawns_path, renames)
    snapshot(path, trigger="editor")  # after every check: a refused save leaves no version
    if spawns is not None:
        snapshot(spawns.path, trigger="editor")
    save_events_file(ef)
    if spawns is not None:
        save_eventspawns_file(spawns)


# ---------------------------------------------------------------------------
# cfgspawnabletypes.xml (the attachment/cargo groups are the row's tree; the
# Attachments/cargo column stays as their summary, which the search reads)
# ---------------------------------------------------------------------------


def spawnabletypes_load(path: Path | None) -> dict:
    from app.xml.spawnabletypes_xml import FIELD_DESCRIPTIONS, parse_spawnabletypes_file

    if path is None:
        return _empty()
    sf = parse_spawnabletypes_file(path)
    columns = apply_col_help([
        {"key": "name", "label": "Type", "type": "str"},
        # A 0-1 share, typed as a percentage (webui ShareInput).
        {"key": "damage_min", "label": "Damage min", "type": "str", "share": True},
        {"key": "damage_max", "label": "Damage max", "type": "str", "share": True},
        {"key": "tag", "label": "Tag", "type": "str"},
        {"key": "hoarder", "label": "Hoarder", "type": "bool"},
        # A summary of the row's tree, which is where the groups are edited;
        # the save never reads this cell. `opens_nested`: the cell is a button
        # that opens the tree, so the groups are one click from the summary.
        {"key": "groups", "label": "Attachments/cargo", "type": "str", "readonly": True, "opens_nested": True},
    ], FIELD_DESCRIPTIONS, SPAWNABLE_COL_HELP)
    rows = []
    for index, e in enumerate(sf.entries):
        summary = ", ".join(f"{g.tag}[{g.preset or len(g.items)}]" for g in e.attachment_groups)
        rows.append(
            {
                # By position (`_spawnable_of`): stock Livonia and Sakhal list
                # some types twice, so a name is not an address.
                "id": f"#{index}",
                "values": {
                    "name": e.name,
                    "damage_min": "" if e.damage_min is None else str(e.damage_min),
                    "damage_max": "" if e.damage_max is None else str(e.damage_max),
                    "tag": e.tag_name or "",
                    "hoarder": e.has_hoarder,
                    "groups": summary,
                },
                "nested": nested_of(e.element, SPAWNABLE_PARTS),
            }
        )
    return {
        "kind": "table", "columns": columns, "rows": rows,
        "nested_choices": {"kind": list(SPAWNABLE_GROUP_TAGS)},
        "nested_templates": SPAWNABLE_TEMPLATES,
        # An added row starts with an empty tree, so its attachments and cargo
        # can be added before the first save (they used to appear only after).
        "new_row_nested": {"groups": []},
    }


def _spawnable_of(row_id, entries: list):
    """The entry a row id from `spawnabletypes_load` names; None for a row
    the table added ("new-<n>-<time>")."""
    text = str(row_id or "")
    if text.startswith("#") and text[1:].isdigit() and int(text[1:]) < len(entries):
        return entries[int(text[1:])]
    return None


def _damage_cells(entry, values: dict) -> tuple | None:
    """The row's damage bounds, or None when they are the entry's own. A
    cell is a fraction from 0 to 1 or empty; clearing one bound while the
    other stays is refused (no stock file has a one-sided <damage>), and so
    is a min above the max."""
    from app.xml.spawnabletypes_xml import check_damage, fraction

    current = (entry.damage_min, entry.damage_max)
    new = tuple(
        fraction(values[key], f"{entry.name}: {label}") if key in values else old
        for key, label, old in (
            ("damage_min", "damage min", current[0]), ("damage_max", "damage max", current[1]),
        )
    )
    if new == current:
        return None
    if (new[0] is None) != (new[1] is None):
        raise ValueError(
            f"{entry.name}: set both damage min and damage max, or clear both to remove <damage>."
        )
    check_damage(*new, where=entry.name)
    return new


def spawnabletypes_save(path: Path, rows: list[dict]) -> None:
    from app.xml.spawnabletypes_xml import (
        add_spawnable_type,
        parse_spawnabletypes_file,
        remove_spawnable_type,
        rename_spawnable_type,
        save_spawnabletypes_file,
        set_damage,
        set_hoarder,
        set_tag,
    )

    sf = parse_spawnabletypes_file(path)
    entries = list(sf.entries)
    # (entry or None for an added row, the name it ends with, the row's values)
    plan: list[tuple] = []
    kept: set[int] = set()
    for row in rows:
        values = row.get("values", row)
        entry = _spawnable_of(row.get("id"), entries)
        typed = str(values.get("name") or "").strip()
        if entry is None:
            if typed:
                plan.append((None, typed, values))
            continue
        if id(entry) in kept:
            raise ValueError(f"Two rows are the same spawnable type {entry.name!r}; reload the page.")
        kept.add(id(entry))
        plan.append((entry, typed or entry.name, values))

    # A name typed for an added or renamed row must be free; two entries the
    # file itself has under one name stay as they are.
    final = [name for _, name, _ in plan]
    for entry, name, _ in plan:
        if (entry is None or name != entry.name) and final.count(name) > 1:
            raise ValueError(f"There is already a spawnable type named {name!r}; pick another name.")

    for entry in entries:
        if id(entry) not in kept:
            remove_spawnable_type(sf, entry)
    for entry, name, values in plan:
        if entry is None:
            entry = add_spawnable_type(sf, name)
        elif name != entry.name:
            rename_spawnable_type(entry, name)
        _apply_groups(entry.element, values, entry.name)
        damage = _damage_cells(entry, values)
        if damage is not None:
            set_damage(entry, *damage)
        if "tag" in values:
            set_tag(entry, str(values["tag"]).strip() or None)
        if "hoarder" in values:
            set_hoarder(entry, bool(values["hoarder"]))
    snapshot(path, trigger="editor")  # after every check: a refused save leaves no version
    save_spawnabletypes_file(sf)


# ---------------------------------------------------------------------------
# types.xml (the mission's own db/types.xml has the far richer /types page —
# this compact table exists for the generated custom/ files)
# ---------------------------------------------------------------------------


def types_load(path: Path | None) -> dict:
    from app.xml.types_xml import FIELD_DESCRIPTIONS, parse_types_file

    if path is None:
        return _empty()
    tf = parse_types_file(path)
    columns = [{"key": "name", "label": "Name", "type": "str"}]
    columns += [{"key": c, "label": c.capitalize(), "type": "int"} for c in TYPES_NUMERIC_COLUMNS]
    columns += [
        {"key": "category", "label": "Category", "type": "str"},
        {"key": "tag", "label": "Tag", "type": "str"},
    ]
    apply_col_help(columns, TYPES_COL_HELP)
    rows = [
        {
            "id": e.name,
            "values": {
                "name": e.name,
                **{c: getattr(e, c) for c in TYPES_NUMERIC_COLUMNS},
                "category": e.category or "",
                "tag": e.tag or "",
            },
            "nested": nested_of(e.element, TYPES_TABLE_PARTS),
        }
        for e in tf.entries
    ]
    # custom/<file>.xml: the mission is two levels up.
    mission = path.parent.parent
    data = {
        "kind": "table", "columns": columns, "rows": rows,
        "nested_choices": limits_choices(mission),
        "nested_switches": FLAG_PARTS,
        "nested_help": nested_help(FIELD_DESCRIPTIONS),
    }
    defined = mission_limits(mission)
    warnings = [
        problem for e in tf.entries
        if (problem := undefined_names_problem(e.name, e.element, {}, defined))
    ]
    if warnings:
        data["warnings"] = warnings
    return data


def types_save(path: Path, rows: list[dict]) -> None:
    from app.xml.types_xml import (
        entry_numbers,
        limit_names_of,
        parse_types_file,
        save_types_files,
        set_category,
        set_numeric_field,
        set_tag,
        type_number_problems,
    )

    tf = parse_types_file(path, preserve_comments=True)
    by_name = {e.name: e for e in tf.entries}
    defined = mission_limits(path.parent.parent)
    for row in rows:
        values = row.get("values", row)
        entry = by_name.get(row.get("id") or values.get("name"))
        if entry is None:
            # This table edits types in place and has no add, remove or rename;
            # a row it cannot place used to be dropped while the save succeeded.
            raise ValueError(
                f"Row {values.get('name') or row.get('id')!r} names no type in this file; "
                "types are added, removed or renamed on the Types page or the Raw tab."
            )
        before = limit_names_of(entry.element)
        apply_parts(entry.element, TYPES_TABLE_PARTS, values, where=entry.name)
        # Only what changed: an untouched row must not gain the <min> or
        # <cost> a mod's type leaves out.
        changes = {
            f: _int(values[f], getattr(entry, f)) for f in TYPES_NUMERIC_COLUMNS if f in values
        }
        problems = type_number_problems(entry_numbers(entry), changes)
        if problems:
            raise ValueError(f"{entry.name}: " + "; ".join(problems))
        for field_name, number in changes.items():
            if number != getattr(entry, field_name):
                set_numeric_field(entry, field_name, number)
        if "category" in values:
            set_category(entry, str(values["category"]).strip() or None)
        if "tag" in values:
            set_tag(entry, str(values["tag"]).strip() or None)
        problem = undefined_names_problem(entry.name, entry.element, before, defined)
        if problem:
            raise ValueError(problem)
    snapshot(path, trigger="editor")  # after every check: a refused save leaves no version
    save_types_files([tf])


# ---------------------------------------------------------------------------
# globals.xml
#
# The mission's own db/globals.xml keeps its **form** editor
# (`mission_forms.py`): it is a fixed set of engine variables and the form's
# per-field help is the better surface for it. This table exists for the
# generated custom/ copies, which list an arbitrary handful of vars and are
# rendered by `routers/modxml.py`'s drawer — that drawer renders TableEditor and
# cannot render a form. Don't "unify" the two.
# ---------------------------------------------------------------------------


def globals_load(path: Path | None) -> dict:
    from app.xml.globals_xml import VAR_DESCRIPTIONS, parse_globals_file

    if path is None:
        return _empty()
    gf = parse_globals_file(path)
    columns = [
        {"key": "name", "label": "Variable", "type": "str"},
        {
            "key": "type", "label": "Type", "type": "int", "choices": ["0", "1", "2"],
            "choice_help": {"0": "Integer.", "1": "Float.", "2": "String."},
        },
        {"key": "value", "label": "Value", "type": "str"},
    ]
    apply_col_help(columns, VAR_DESCRIPTIONS, GLOBALS_COL_HELP)
    rows = [
        {"id": v.name, "values": {"name": v.name, "type": v.type, "value": v.value}}
        for v in gf.entries
    ]
    return {"kind": "table", "columns": columns, "rows": rows}


def _var_type(value, fallback: int) -> object:
    """A row's type cell: the code it holds, or the raw text when it is not a
    whole number (refused by `var_problem`); *fallback* when it is empty."""
    text = str(value).strip() if value is not None else ""
    if text == "":
        return fallback
    try:
        return int(text)
    except ValueError:
        return text


def globals_save(path: Path, rows: list[dict]) -> None:
    from app.xml.globals_xml import (
        WIKI_VAR_TYPES,
        add_var,
        parse_globals_file,
        same_var_value,
        save_globals_file,
        set_var_value,
        var_problem,
    )

    gf = parse_globals_file(path)
    by_name = {v.name: v for v in gf.entries}
    changes: list[tuple[str, object, str]] = []
    problems: list[str] = []
    for row in rows:
        values = row.get("values", row)
        name = _row_name(row, values, by_name)
        if not name:
            continue
        var = by_name.get(name)
        if var is None:
            # DayZ needs all three attributes on every <var>. A documented
            # variable takes the wiki's type when the row leaves it out.
            type_val = _var_type(values.get("type"), WIKI_VAR_TYPES.get(name, 0))
            value = str(values.get("value", ""))
        else:
            type_val = _var_type(values.get("type"), var.type) if "type" in values else var.type
            value = str(values["value"]) if "value" in values else var.value
            if type_val != var.type:
                # "Types of existing variables should never be modified"
                # (central-economy-mission-files-modding.wiki:49).
                problems.append(f"{name}: the type of an existing variable cannot be changed")
                continue
            if same_var_value(var.type, var.value, value):
                continue  # untouched: the file keeps its own text ("0.0")
        problem = var_problem(name, type_val, value)
        if problem:
            problems.append(problem)
            continue
        changes.append((name, type_val, value))
    if problems:
        raise ValueError("; ".join(problems))
    for name, type_val, value in changes:
        var = by_name.get(name)
        if var is None:
            by_name[name] = add_var(gf, name, type_val, value)
        else:
            set_var_value(var, value)
    snapshot(path, trigger="editor")  # after every check: a refused save leaves no version
    save_globals_file(gf)


# ---------------------------------------------------------------------------
# economy.xml
# ---------------------------------------------------------------------------


def economy_load(path: Path | None) -> dict:
    from app.xml.economy_xml import FLAG_DESCRIPTIONS, SYSTEM_NAMES, parse_economy

    if path is None:
        return _empty()
    ef = parse_economy(path)
    columns = [
        {"key": "name", "label": "System", "type": "str", "choices": list(SYSTEM_NAMES)},
        {"key": "init", "label": "Init", "type": "bool"},
        {"key": "load", "label": "Load", "type": "bool"},
        {"key": "respawn", "label": "Respawn", "type": "bool"},
        {"key": "save", "label": "Save", "type": "bool"},
    ]
    apply_col_help(columns, FLAG_DESCRIPTIONS, ECONOMY_COL_HELP)
    rows = [
        {
            "id": s.name,
            "values": {
                "name": s.name,
                "init": s.init,
                "load": s.load,
                "respawn": s.respawn,
                "save": s.save,
            },
        }
        for s in ef.systems
    ]
    return {"kind": "table", "columns": columns, "rows": rows}


def economy_save(path: Path, rows: list[dict]) -> None:
    from app.xml.economy_xml import (
        add_system,
        parse_economy,
        remove_system,
        rename_system,
        save_economy,
        set_system_flags,
    )

    ef = parse_economy(path)
    by_name = {s.name: s for s in ef.systems}
    named = _sync_rows(
        rows, by_name, what="a system", remove=lambda s: remove_system(ef, s), rename=rename_system,
    )
    for name, values in named:
        system = by_name.get(name)
        if system is None:
            # A system's name is an XML *tag*, so `add_system` validates it —
            # letting a stray row through would write an unparseable file.
            system = add_system(ef, name)
            by_name[name] = system
        set_system_flags(
            system,
            bool(values.get("init", system.init)),
            bool(values.get("load", system.load)),
            bool(values.get("respawn", system.respawn)),
            bool(values.get("save", system.save)),
        )
    snapshot(path, trigger="editor")  # after every check: a refused save leaves no version
    save_economy(ef)


# ---------------------------------------------------------------------------
# messages.xml
#
# The one CE kind DayZ only ever *appends*: a linked file can add broadcasts but
# never override or remove one the mission already has. The table is still a
# full sync of the file it is pointed at — that file is the link's own copy, not
# the mission's messages.xml.
# ---------------------------------------------------------------------------


def messages_load(path: Path | None) -> dict:
    from app.xml.messages_xml import MessageType, classify_message, elem_text, read_messages

    if path is None:
        return _empty()
    _tree, messages = read_messages(path)
    columns = [
        {
            "key": "type", "label": "Type", "type": "str", "choices": [t.value for t in MessageType],
            "choice_help": {
                MessageType.ON_CONNECT.value: "Shown when a player connects.",
                MessageType.SHUTDOWN.value: "A countdown to the server's shutdown.",
                MessageType.REPEAT.value: "Shown again every repeat minutes.",
                MessageType.OTHER.value: "None of the above.",
            },
        },
        {"key": "text", "label": "Text", "type": "str"},
        {"key": "delay", "label": "Delay", "type": "str", "unit": "minutes"},
        {"key": "repeat", "label": "Repeat", "type": "str", "unit": "minutes"},
        {"key": "deadline", "label": "Deadline", "type": "str", "unit": "minutes"},
    ]
    apply_col_help(columns, MESSAGES_COL_HELP)
    rows = [
        {
            "id": str(i),
            "values": {
                "type": classify_message(msg).value,
                "text": elem_text(msg, "text"),
                "delay": elem_text(msg, "delay"),
                "repeat": elem_text(msg, "repeat"),
                "deadline": elem_text(msg, "deadline"),
            },
        }
        for i, msg in enumerate(messages)
    ]
    return {"kind": "table", "columns": columns, "rows": rows}


MESSAGE_VALUE_KEYS = ("text", "delay", "repeat", "deadline")


def _positive(value: str) -> bool:
    try:
        return float(value) > 0
    except ValueError:
        return False


def messages_save(path: Path, rows: list[dict]) -> None:
    """Write the rows as the file's messages, in row order.

    A row whose id names a message of the file starts from that message, so
    what the row does not carry stays: a value it leaves out, an explicit
    `<onConnect>0</onConnect>`, a comment inside the message, and a flag of
    another type while the row keeps the message's own type (Bohemia's
    example greets on connect and repeats; one message may also both greet
    and shut down). A value the row carries is written when it changed, and
    one it carries empty is removed, so a cleared field clears. A row with no
    such id is a new message.

    The messages go back into the places the file's messages held, so a
    comment between two of them stays where it was, and the file's closing
    whitespace stays at its end.
    """
    import copy
    import xml.etree.ElementTree as ET

    from app.xml.messages_xml import (
        MessageType,
        classify_message,
        elem_text,
        message_value_problem,
        on_connect_tag,
        read_messages,
        remove_elem,
        save_messages,
        set_elem_text,
    )

    if path.is_file():
        # Raises for a file it cannot read, before the snapshot and the write:
        # building a new file from the rows would replace it.
        tree, _messages = read_messages(path)
        root = tree.getroot()
    else:
        root = ET.Element("messages")
    originals = root.findall("message")
    connect_tag = on_connect_tag(originals)
    by_id = {str(i): msg for i, msg in enumerate(originals)}
    known_types = [t.value for t in MessageType]

    built = []
    copies: dict[int, ET.Element] = {}
    problems: list[str] = []
    for n, row in enumerate(rows, start=1):
        values = row.get("values", row)
        row_type = values.get("type", "Other")
        # The type is read back from the flags (classify_message), so a value
        # that is none of the four would come back as something else.
        if row_type not in known_types:
            raise ValueError(f"{row_type!r} is not a message type: use one of {', '.join(known_types)}.")
        source = by_id.get(str(row["id"])) if row.get("id") is not None else None
        msg = copy.deepcopy(source) if source is not None else ET.Element("message")
        if source is not None:
            copies[id(source)] = msg
        changed = source is None
        if source is None:
            set_elem_text(msg, "text", "")
        where = f"Message {n}" + (f" ({elem_text(msg, 'text')[:40]!r})" if source is not None else "")
        for key in MESSAGE_VALUE_KEYS:
            if key not in values:
                continue
            value = "" if values[key] is None else str(values[key])
            if key != "text":
                value = value.strip()
            if value.strip() == elem_text(msg, key):
                continue  # untouched: the file keeps its own text
            changed = True
            problem = message_value_problem(key, value)
            if problem:
                problems.append(f"{where}: {problem}")
            elif key == "text" or value:
                set_elem_text(msg, key, value)
            else:
                remove_elem(msg, key)
        # OnConnect and Shutdown are flags of their own. A row that keeps the
        # message's type keeps every flag it had; one whose type moved loses
        # the flags of the others (a 0 the file wrote stays a 0).
        original_type = classify_message(source).value if source is not None else None
        if row_type != original_type:
            changed = True
            for flag_type, tag in (("OnConnect", connect_tag), ("Shutdown", "shutdown")):
                if row_type == flag_type:
                    set_elem_text(msg, tag, "1")
                elif elem_text(msg, tag) == "1":
                    remove_elem(msg, tag)
        # Repeat is not a flag element: DayZ reads a repeat above 0 as the
        # Repeat flag (dayz-docs/wiki/pages/server-messages.wiki). So the
        # repeat is written for every type, as Bohemia's own example greets on
        # connect and repeats, and an Other row given one comes back a Repeat
        # message. Until 2026 Q4 an Other row's repeat was dropped on save
        # while the editor offered a box for it.
        if row_type == "Repeat" and not _positive(elem_text(msg, "repeat")):
            problems.append(f"{where}: a Repeat message needs a repeat interval above 0 minutes.")
        # "If the Countdown flag is not set, this flag is ignored."
        # (server-messages.wiki:21): a Shutdown message with no deadline
        # never shuts the server down.
        if changed and row_type == "Shutdown" and not _positive(elem_text(msg, "deadline")):
            problems.append(
                f"{where}: a Shutdown message needs a deadline above 0 minutes; "
                "without a countdown DayZ ignores the shutdown."
            )
        built.append(msg)
    if problems:
        raise ValueError("; ".join(problems))

    if path.is_file():
        snapshot(path, trigger="editor")
    old_last = root[-1] if len(root) else None
    closing = old_last.tail if old_last is not None else None
    kids = list(root)
    slots = [next(i for i, k in enumerate(kids) if k is m) for m in originals]
    for slot, msg in zip(slots, built):
        root[slot] = msg
    for msg in originals[len(built):]:
        root.remove(msg)
    if len(built) > len(slots):
        at = slots[-1] + 1 if slots else len(root)
        for offset, msg in enumerate(built[len(slots):]):
            root.insert(at + offset, msg)
    if closing is not None and not closing.strip() and len(root):
        carrier = copies.get(id(old_last), old_last)
        if root[-1] is not carrier:
            carrier.tail = None
            root[-1].tail = closing
    save_messages(ET.ElementTree(root), path)


_LOADERS = {
    "types": types_load,
    "events": events_load,
    "spawnabletypes": spawnabletypes_load,
    "globals": globals_load,
    "economy": economy_load,
    "messages": messages_load,
}
_SAVERS = {
    "types": types_save,
    "events": events_save,
    "spawnabletypes": spawnabletypes_save,
    "globals": globals_save,
    "economy": economy_save,
    "messages": messages_save,
}

# Derived, not a second literal: a kind with a loader has a table view by
# definition, and the two lists drifting is a 422 on a file that works fine.
CE_TABLE_KINDS: tuple[str, ...] = tuple(_LOADERS)


def ce_table_load(path: Path, kind: str) -> dict:
    """Structured table payload for `path`, read as central-economy `kind`."""
    loader = _LOADERS.get(kind)
    if loader is None:
        raise ValueError(f"no table view for kind {kind!r}")
    return loader(path)


def ce_table_save(path: Path, kind: str, rows: list[dict]) -> None:
    """Write table `rows` back into `path`, upserting entries in place."""
    saver = _SAVERS.get(kind)
    if saver is None:
        raise ValueError(f"no table view for kind {kind!r}")
    saver(path, rows)
