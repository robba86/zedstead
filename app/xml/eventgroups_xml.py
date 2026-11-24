"""cfgeventgroups.xml parsing and editing.

``<eventgroupdef>`` holds ``<group name="...">`` entries, each holding
``<child .../>`` objects that spawn together as one unit at an event position
(a crash site plus its debris). A group is referenced from events.xml as an
``<event>``'s child type.

Mirrors ``eventspawns_xml`` — same shape (named parents, repeated leaf
children), same in-place mutation discipline, so the two table adapters over
them stay symmetrical.

``x``/``y``/``z`` on a ``<child>`` are offsets **relative to the group's spawn
position**, not world coordinates.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from app.xml.xml_utils import load_xml, save_xml


# Attributes this module reads and writes. Anything else a file (or a mod)
# puts on a <child> is left untouched, because every edit mutates the existing
# element rather than rebuilding it — see the note on `update_child`.
CHILD_ATTRS = ("type", "deloot", "lootmin", "lootmax", "x", "y", "z", "a")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class GroupChild:
    """One <child> element inside a <group>.

    A number the element does not carry is None: in Bohemia's files a child
    holds `deloot`/`lootmin`/`lootmax` or `spawnsecondary`, and `y` only
    where it is set (281 of Chernarus' 874 children have no `y`)."""
    type: str = ""
    deloot: bool = False
    lootmin: int | None = None
    lootmax: int | None = None
    x: float | None = None
    y: float | None = None
    z: float | None = None
    a: float | None = None
    element: ET.Element = field(default_factory=lambda: ET.Element("child"), repr=False)


@dataclass
class EventGroupEntry:
    """One <group name="..."> element in cfgeventgroups.xml."""
    name: str
    children: list[GroupChild] = field(default_factory=list)
    element: ET.Element = field(default_factory=lambda: ET.Element("group"), repr=False)


@dataclass
class EventGroupsFile:
    """Parsed cfgeventgroups.xml."""
    path: Path
    tree: ET.ElementTree
    entries: list[EventGroupEntry] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_eventgroups_path(mission_dir: Path) -> Path | None:
    """Return cfgeventgroups.xml in the mission root if it exists."""
    p = mission_dir / "cfgeventgroups.xml"
    return p if p.is_file() else None


# ---------------------------------------------------------------------------
# Attribute coercion
# ---------------------------------------------------------------------------

def _to_float(raw: str | None, fallback: float = 0.0) -> float:
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    # "nan" and "1e999" parse, and neither is a position or a count.
    return value if math.isfinite(value) else fallback


def _to_int(raw: str | None, fallback: int = 0) -> int:
    """int() via float() — real files write lootmin="0.0" as readily as "0"."""
    return int(_to_float(raw, fallback))


def _optional(reader, raw: str | None):
    """None for an attribute the element does not have, else ``reader(raw)``."""
    return None if raw is None else reader(raw)


def _to_bool(raw: str | None) -> bool:
    """`deloot` is a 0/1 flag, so it round-trips through a checkbox like
    events.xml's `active`. There is no dayz-docs page for cfgeventgroups.xml
    (no wiki entry, and the vendored vanilla missions are the *offline* ones,
    which don't ship the file), so this was checked against real server
    missions instead in 2026 Q3: only "0" and "1" occur. Don't widen the
    column to int on suspicion — find a file that needs it first.
    Anything non-numeric and truthy-looking still reads as on."""
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes"}


def _from_bool(value: bool) -> str:
    return "1" if value else "0"


def _fmt_float(value: float) -> str:
    """Match eventspawns' 6-decimal coordinate form."""
    return f"{value:.6f}"


# ---------------------------------------------------------------------------
# Parse & save
# ---------------------------------------------------------------------------

def parse_eventgroups_file(path: Path) -> EventGroupsFile:
    """Parse cfgeventgroups.xml into groups and their children.

    Comments are preserved: mods splice their groups in wrapped in
    ``<!-- START MOD: @X -->`` / ``<!-- END MOD: @X -->`` markers, and a save
    that dropped them would strand the merged block forever (the markers are
    matched as text by ``xml_merge.remove_mod_xml_blocks``).
    """
    tree = load_xml(path, preserve_comments=True)
    root = tree.getroot()
    entries: list[EventGroupEntry] = []

    for group_elem in root.findall("group"):
        children = [
            GroupChild(
                type=child_elem.get("type", ""),
                deloot=_to_bool(child_elem.get("deloot")),
                lootmin=_optional(_to_int, child_elem.get("lootmin")),
                lootmax=_optional(_to_int, child_elem.get("lootmax")),
                x=_optional(_to_float, child_elem.get("x")),
                y=_optional(_to_float, child_elem.get("y")),
                z=_optional(_to_float, child_elem.get("z")),
                a=_optional(_to_float, child_elem.get("a")),
                element=child_elem,
            )
            for child_elem in group_elem.findall("child")
        ]
        entries.append(
            EventGroupEntry(
                name=group_elem.get("name", ""),
                children=children,
                element=group_elem,
            )
        )

    return EventGroupsFile(path=path, tree=tree, entries=entries)


def save_eventgroups_file(gf: EventGroupsFile) -> None:
    """Write the modified tree back to disk."""
    save_xml(gf.tree, gf.path)


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------

_UNSET = object()


def update_child(
    child: GroupChild,
    *,
    type=_UNSET,
    deloot=_UNSET,
    lootmin=_UNSET,
    lootmax=_UNSET,
    x=_UNSET,
    y=_UNSET,
    z=_UNSET,
    a=_UNSET,
) -> bool:
    """Write the given known attributes onto an existing <child>; True if any changed.

    A value equal to the file's is not written, so an untouched number keeps
    its own text ("0.7", never "0.700000"). None removes the attribute; an
    argument left out leaves it alone. ``deloot=False`` on a child without one
    adds none, since an absent `deloot` reads off. Sets rather than replaces,
    so an attribute outside ``CHILD_ATTRS`` (Bohemia's `spawnsecondary`, a
    mod's own) survives the edit.
    """
    elem = child.element
    changed = False
    if type is not _UNSET and type != child.type:
        elem.set("type", type)
        child.type = type
        changed = True
    if deloot is not _UNSET and deloot != child.deloot:
        elem.set("deloot", _from_bool(deloot))
        child.deloot = deloot
        changed = True
    for key, value, text in (   # in the order Bohemia's files write them
        ("lootmax", lootmax, str), ("lootmin", lootmin, str),
        ("x", x, _fmt_float), ("z", z, _fmt_float), ("a", a, _fmt_float), ("y", y, _fmt_float),
    ):
        if value is _UNSET or value == getattr(child, key):
            continue
        if value is None:
            elem.attrib.pop(key, None)
        else:
            elem.set(key, text(value))
        setattr(child, key, value)
        changed = True
    return changed


def add_child(
    entry: EventGroupEntry,
    *,
    type: str = "",
    deloot: bool = False,
    lootmin: int | None = None,
    lootmax: int | None = None,
    x: float = 0.0,
    y: float | None = None,
    z: float = 0.0,
    a: float = 0.0,
) -> GroupChild:
    """Append a new <child> to a group and return it.

    `type`, `x`, `z` and `a` are always written, as every stock child has
    them; the loot attributes and `y` only when given."""
    child = GroupChild(element=ET.SubElement(entry.element, "child"))
    child.element.set("type", "")   # first, where Bohemia's files have it
    update_child(child, type=type, deloot=deloot, lootmin=lootmin, lootmax=lootmax,
                 x=x, y=y, z=z, a=a)
    entry.children.append(child)
    return child


def remove_child(entry: EventGroupEntry, child: GroupChild) -> None:
    """Remove a <child> element from its group."""
    try:
        entry.element.remove(child.element)
    except ValueError:
        pass
    try:
        entry.children.remove(child)
    except ValueError:
        pass


def create_group(gf: EventGroupsFile, name: str) -> EventGroupEntry:
    """Create a new empty <group name="..."> and return it."""
    group_elem = ET.SubElement(gf.tree.getroot(), "group")
    group_elem.set("name", name)
    group_elem.tail = "\n"
    entry = EventGroupEntry(name=name, children=[], element=group_elem)
    gf.entries.append(entry)
    return entry
