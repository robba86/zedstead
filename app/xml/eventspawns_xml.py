"""cfgeventspawns.xml parsing, editing, and backup/restore."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from app.xml.xml_utils import get_mission_dir, load_xml, save_xml


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SpawnPosition:
    """One <pos> element inside an <event>.

    ``a`` reads 0 when the element has none (222 of Chernarus' stock positions
    have no ``a``), and ``has_a`` says which it was. ``y`` is the height a few
    events carry (StaticTrain, StaticSantaCrash, …), None when absent."""
    x: float
    z: float
    a: float          # rotation angle in degrees, or -1 for random
    element: ET.Element = field(default_factory=lambda: ET.Element("pos"))
    y: float | None = None

    @property
    def has_a(self) -> bool:
        return self.element.get("a") is not None


@dataclass
class EventSpawnEntry:
    """One <event name="..."> element in cfgeventspawns.xml."""
    name: str
    positions: list[SpawnPosition]
    element: ET.Element = field(default_factory=lambda: ET.Element("event"))


@dataclass
class EventSpawnsFile:
    """Parsed cfgeventspawns.xml."""
    path: Path
    tree: ET.ElementTree
    entries: list[EventSpawnEntry] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_eventspawns_path(mission_dir: Path) -> Path | None:
    """Return cfgeventspawns.xml in mission root if it exists."""
    p = mission_dir / "cfgeventspawns.xml"
    return p if p.is_file() else None


# ---------------------------------------------------------------------------
# Parse & save
# ---------------------------------------------------------------------------

def entry_from_element(event_elem: ET.Element) -> EventSpawnEntry:
    """The `EventSpawnEntry` for one parsed ``<event>`` element: its ``<pos>``
    children, a number that is not one read as 0 (``y`` as absent)."""
    positions: list[SpawnPosition] = []
    for pos_elem in event_elem.findall("pos"):
        try:
            x = float(pos_elem.get("x", "0"))
        except ValueError:
            x = 0.0
        try:
            z = float(pos_elem.get("z", "0"))
        except ValueError:
            z = 0.0
        try:
            a = float(pos_elem.get("a", "0"))
        except ValueError:
            a = 0.0
        try:
            y = float(pos_elem.get("y")) if pos_elem.get("y") is not None else None
        except ValueError:
            y = None
        positions.append(SpawnPosition(x=x, z=z, a=a, element=pos_elem, y=y))
    return EventSpawnEntry(
        name=event_elem.get("name", ""), positions=positions, element=event_elem,
    )


def position_attrs(entry: EventSpawnEntry) -> list[tuple[str | None, str | None, str | None]]:
    """Each position as its ``(x, z, a)`` attribute text: what a merge copies
    unchanged, so the same event in a mod or manual file and in the mission
    compares equal."""
    return [(p.element.get("x"), p.element.get("z"), p.element.get("a")) for p in entry.positions]


def parse_eventspawns_file(path: Path) -> EventSpawnsFile:
    """Parse cfgeventspawns.xml, extract EventSpawnEntry for each <event>.

    Comments are preserved: mods splice their spawn positions in wrapped in
    ``<!-- START MOD: @X -->`` / ``<!-- END MOD: @X -->`` markers, and a
    save that dropped them would strand the merged block forever.
    """
    tree = load_xml(path, preserve_comments=True)
    root = tree.getroot()

    entries = [entry_from_element(e) for e in root.findall("event")]

    return EventSpawnsFile(path=path, tree=tree, entries=entries)


def save_eventspawns_file(ef: EventSpawnsFile, *, snapshot: bool = True) -> None:
    """Write modified tree back to disk."""
    save_xml(ef.tree, ef.path, snapshot=snapshot)


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------

def _number(value: float) -> str:
    return f"{value:.6f}"


def add_position(
    entry: EventSpawnEntry, x: float, z: float, a: float | None = None,
    y: float | None = None,
) -> SpawnPosition:
    """Create a <pos> element, append to entry, return new SpawnPosition.

    ``a`` and ``y`` are written only when given: a position without them is
    as valid as one with (Bohemia's own files have both kinds)."""
    pos_elem = ET.SubElement(entry.element, "pos")
    pos_elem.set("x", _number(x))
    pos_elem.set("z", _number(z))
    if a is not None:
        pos_elem.set("a", _number(a))
    if y is not None:
        pos_elem.set("y", _number(y))
    pos = SpawnPosition(x=x, z=z, a=0.0 if a is None else a, element=pos_elem, y=y)
    entry.positions.append(pos)
    return pos


def remove_position(entry: EventSpawnEntry, pos: SpawnPosition) -> None:
    """Remove a <pos> element from the event."""
    try:
        entry.element.remove(pos.element)
    except ValueError:
        pass
    try:
        entry.positions.remove(pos)
    except ValueError:
        pass


def create_event_spawn(ef: EventSpawnsFile, name: str) -> EventSpawnEntry:
    """Create a new empty <event> element in cfgeventspawns.xml."""
    root = ef.tree.getroot()
    event_elem = ET.SubElement(root, "event")
    event_elem.set("name", name)
    event_elem.tail = "\n"
    entry = EventSpawnEntry(name=name, positions=[], element=event_elem)
    ef.entries.append(entry)
    return entry


KEEP = object()   # update_position's "leave this attribute as it is"


def update_position(
    pos: SpawnPosition, x: float, z: float, a: float | None = None, y=KEEP,
) -> bool:
    """Update coordinates on an existing position element; True if any changed.

    Only a value that differs from the file's is written, so an untouched
    coordinate keeps its own text ("2593.47", not "2593.470000"). ``a=None``
    keeps the angle, and never adds an ``a`` the element did not have. ``y``
    is left alone unless given: a number sets it, None removes it."""
    changed = False
    if x != pos.x:
        pos.element.set("x", _number(x))
        pos.x = x
        changed = True
    if z != pos.z:
        pos.element.set("z", _number(z))
        pos.z = z
        changed = True
    if a is not None and a != pos.a:   # an absent a reads 0, so 0 adds none
        pos.element.set("a", _number(a))
        pos.a = a
        changed = True
    if y is not KEEP and y != pos.y:
        if y is None:
            pos.element.attrib.pop("y", None)
        else:
            pos.element.set("y", _number(y))
        pos.y = y
        changed = True
    return changed


def remove_angle(pos: SpawnPosition) -> None:
    """Drop the position's ``a``; it then reads as 0, like any position without one."""
    pos.element.attrib.pop("a", None)
    pos.a = 0.0


def duplicate_position(entry: EventSpawnEntry, pos: SpawnPosition) -> SpawnPosition:
    """Clone a position and append it to the entry. Returns the new position.

    The copy carries exactly the original's attributes and their text."""
    copy = ET.SubElement(entry.element, "pos", dict(pos.element.attrib))
    new = SpawnPosition(x=pos.x, z=pos.z, a=pos.a, element=copy, y=pos.y)
    entry.positions.append(new)
    return new


# ---------------------------------------------------------------------------
# Validation / collision detection
# ---------------------------------------------------------------------------

def distance(p1_x: float, p1_z: float, p2_x: float, p2_z: float) -> float:
    """Euclidean distance between two 2D points."""
    return math.sqrt((p1_x - p2_x) ** 2 + (p1_z - p2_z) ** 2)


def find_exact_duplicates(positions: list[SpawnPosition]) -> list[tuple[int, int]]:
    """Return index pairs that are exact duplicates (same X, Z, A)."""
    pairs: list[tuple[int, int]] = []
    for i in range(len(positions)):
        for j in range(i + 1, len(positions)):
            p1, p2 = positions[i], positions[j]
            if p1.x == p2.x and p1.z == p2.z and p1.a == p2.a:
                pairs.append((i, j))
    return pairs


def find_nearby_positions(
    positions: list[SpawnPosition],
    target_x: float,
    target_z: float,
    min_distance: float = 50.0,
    exclude_index: int = -1,
) -> list[tuple[int, float]]:
    """Return [(index, distance)] for positions within min_distance meters."""
    results: list[tuple[int, float]] = []
    for i, pos in enumerate(positions):
        if i == exclude_index:
            continue
        d = distance(pos.x, pos.z, target_x, target_z)
        if d < min_distance:
            results.append((i, d))
    return sorted(results, key=lambda t: t[1])


def auto_shift_position(
    conflict_x: float,
    conflict_z: float,
    new_x: float,
    new_z: float,
    shift_distance: float = 100.0,
) -> tuple[float, float]:
    """Shift new position 100m away from the conflicting point.

    Direction is away from the conflict. If positions are coincident, shifts north.
    """
    dx = new_x - conflict_x
    dz = new_z - conflict_z
    mag = math.sqrt(dx * dx + dz * dz)
    if mag < 0.001:
        dx, dz = 0.0, 1.0
    else:
        dx /= mag
        dz /= mag
    return new_x + dx * shift_distance, new_z + dz * shift_distance
