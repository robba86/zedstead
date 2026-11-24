"""What a mission makes the server complain about, read off the mission itself.

Two of the central economy's boot complaints are a pure function of the
mission's own XML (plans/39), and both were checked against a real boot of all
three stock maps on 1.29 (2026 Q3):

- ``N groups have no points`` — ``<group>`` elements in ``mapgroupproto.xml``
  with no ``<point>`` anywhere inside them. Chernarus 5, Livonia 4, Sakhal 5:
  derived and logged agree on all three.
- ``Skipping entry for non-existing event 'X'`` — ``<event>`` blocks in
  ``cfgeventspawns.xml`` naming an event no events file defines. Chernarus and
  Livonia ``VehicleTransitBus``, Sakhal none: agree on all three.

So a measured constant per map would be wrong on every map but the one it was
measured on, and stale the next time Bohemia or a map author edits a mission.
Deriving is what reaches the community maps zsm does not ship.

**``N Errors during XML parse`` is deliberately not here.** Plans/39 derived it
as "cfgspawnabletypes entries naming a class no types file defines", which
matched Chernarus exactly (10, compared case-sensitively). The Livonia and
Sakhal boots refuted it: 14 and 13 derived, **10** and **no line at all**
logged. The line is printed inside ``[CE][LoadPrototype]`` — between the
mapgroupproto group counts and ``last group name: …`` — so it is almost
certainly about ``mapgroupproto.xml``, and the Chernarus match was a
coincidence. It stays a measured constant in ``log_analysis.VANILLA_BASELINE``
until something reproduces all three numbers.

Which half is whose follows from where zsm writes (``app/xml/CLAUDE.md``):
``mapgroupproto.xml``, ``db/types.xml`` and ``db/events.xml`` are the
mission's own — no mod merge touches them — while a mod's CE files land in
``custom/`` (registered in cfgeconomycore.xml) and its event spawns are spliced
into ``cfgeventspawns.xml`` inside ``START MOD`` markers. Both are attributed to
their owner here.

Pure over *mission_dir*: no config, nothing written, so it can be pointed at a
fixture, an older mission or an unpacked bundle. A file a derivation needs that
is missing or unparseable makes **that derivation** ``None`` — never an empty
list. An unreadable ``db/events.xml`` would otherwise make every event spawn
look orphaned and report a healthy server as badly broken.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

#: Key for the mission's own entries in the per-owner maps below.
MISSION = ""

_TYPES = "db/types.xml"
_EVENTS = "db/events.xml"
_EVENTSPAWNS = "cfgeventspawns.xml"
_GROUPS = "mapgroupproto.xml"


@dataclass(frozen=True)
class MissionBaseline:
    """The two derivations for one mission, plus who defines each type.

    ``None`` on a derivation means "could not tell" — a file it needs was
    missing or unparseable (named in ``unreadable``) — and callers must fall
    back to whatever they did before, never read it as "none".
    """

    pointless_groups: tuple[str, ...] | None
    orphan_event_spawns: tuple[str, ...] | None
    #: Owner -> orphans, for everything the mission does not own itself: a mod
    #: dir name (``@TraderPlus``) or a manual file (``@user:<id>``).
    mod_orphan_event_spawns: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: classname -> owners of a types file that defines it, with ``MISSION``
    #: for ``db/types.xml``. Plans/39 B: who put a rejected type there. ``None``
    #: when ``db/types.xml`` could not be read.
    type_sources: dict[str, tuple[str, ...]] | None = None
    unreadable: tuple[str, ...] = ()


def owner_label(owner: str) -> str:
    """How an owner key reads in a sentence."""
    if owner == MISSION:
        return "your mission"
    if owner.startswith("@user:"):
        return f"your manual XML file {owner[len('@user:'):]}"
    return owner  # a mod dir name, or a folder/file path


# ── Reading ──────────────────────────────────────────────────────────────


def _parse(path: Path, *, comments: bool = False) -> ET.Element | None:
    from app.xml.xml_utils import load_xml

    try:
        return load_xml(path, preserve_comments=comments).getroot()
    except (OSError, ET.ParseError, ValueError, UnicodeDecodeError):
        return None


def _names(root: ET.Element, tag: str) -> list[str]:
    return [n for e in root.iter(tag) if (n := (e.get("name") or "").strip())]


@dataclass
class _Sources:
    """Every CE file of one kind the economy loads besides the mission's own."""

    by_kind: dict[str, list[tuple[str, Path]]] = field(default_factory=dict)

    def of(self, kind: str) -> list[tuple[str, Path]]:
        return self.by_kind.get(kind, [])


def registered_sources(mission_dir: Path) -> _Sources:
    """``(owner, path)`` per CE kind, from cfgeconomycore.xml's registrations.

    The ``<ce folder="custom">`` block's entries come first, in their order,
    then every other folder's (`xml_report._foreign_ce_entries`). That is not
    strictly document order: a foreign ``<ce>`` written above the custom block
    is still listed after it, so "the last definition wins" between a custom
    file and a foreign folder is approximate. The loot layer
    (`app.maps.loot_map`) relies on this order and says so.

    Registrations, never a glob of ``custom/``: a file there that is not
    registered is not loaded, and must not be counted. Disabled (parked)
    entries are skipped for the same reason. A registration whose path would
    leave the mission folder is not read (`xml_economy.ce_file_path`), with a
    log line; `_foreign_ce_entries` leaves those out of its rows already.
    """
    from app.xml import xml_economy, xml_report
    from app.xml.xml_manual import owning_mod

    out = _Sources()
    server_dir = mission_dir.parent.parent
    for name, entry in xml_report._ce_entries(mission_dir).items():
        if entry.get("disabled"):
            continue
        path = xml_economy.ce_file_path(mission_dir, xml_report.CUSTOM_CE_FOLDER, name)
        if path is None:
            xml_economy.log_outside_mission(mission_dir, xml_report.CUSTOM_CE_FOLDER, name)
            continue
        if name.startswith("user-"):
            owner = f"@user:{Path(name).stem[len('user-'):]}"
        else:
            try:
                owner = owning_mod(server_dir, name) or f"custom/{name}"
            except OSError:
                owner = f"custom/{name}"
        out.by_kind.setdefault(entry["type"], []).append((owner, path))
    for row in xml_report._foreign_ce_entries(mission_dir):
        out.by_kind.setdefault(row["type"], []).append(
            (f"{row['folder']}/{row['name']}", mission_dir / row["folder"] / row["name"])
        )
    return out


# ── The derivations ──────────────────────────────────────────────────────


def _pointless_groups(mission_dir: Path, unreadable: list[str]) -> tuple[str, ...] | None:
    root = _parse(mission_dir / _GROUPS)
    if root is None:
        unreadable.append(_GROUPS)
        return None
    return tuple(
        g.get("name") or "?"
        for g in root.iter("group")
        if g.find(".//point") is None
    )


def _known(
    mission_dir: Path,
    own: str,
    extra: list[tuple[str, Path]],
    tag: str,
    unreadable: list[str],
) -> dict[str, list[str]] | None:
    """``name`` -> owners defining it, or None if the mission's own
    file could not be read. An unreadable *extra* file is noted and skipped:
    the engine cannot load it either, so its classes really are undefined."""
    root = _parse(mission_dir / own)
    if root is None:
        unreadable.append(own)
        return None
    known: dict[str, list[str]] = {}
    for name in _names(root, tag):
        known.setdefault(name, []).append(MISSION)
    for owner, path in extra:
        r = _parse(path)
        if r is None:
            rel = path.relative_to(mission_dir) if path.is_relative_to(mission_dir) else path
            unreadable.append(str(rel))
            continue
        for name in _names(r, tag):
            owners = known.setdefault(name, [])
            if owner not in owners:
                owners.append(owner)
    return known


def _owned_entries(root: ET.Element, tag: str) -> list[tuple[str | None, str]]:
    """``(owner, name)`` for each top-level *tag* element, ``None`` = the file's own."""
    from app.xml.xml_manual import children_with_owner

    return [
        (owner, (child.get("name") or "").strip())
        for owner, child in children_with_owner(root)
        if child.tag == tag and (child.get("name") or "").strip()
    ]


def _split_orphans(
    entries: list[tuple[str | None, str]],
    known: dict[str, list[str]],
) -> tuple[list[str], dict[str, list[str]]]:
    """Undefined names, the file's own apart from each block owner's.

    Compared exactly. Whether the engine ignores case here is not measured;
    on all three stock missions the exact and the case-blind answer agree.
    """
    mine: list[str] = []
    theirs: dict[str, list[str]] = {}
    for owner, name in entries:
        if name in known:
            continue
        if owner is None:
            mine.append(name)
        else:
            theirs.setdefault(owner, []).append(name)
    return mine, theirs


def derive(mission_dir: Path) -> MissionBaseline:
    """Both derivations for *mission_dir*, uncached. See `baseline_for`."""
    unreadable: list[str] = []
    sources = registered_sources(mission_dir)

    groups = _pointless_groups(mission_dir, unreadable)
    known_types = _known(mission_dir, _TYPES, sources.of("types"), "type", unreadable)

    known_events = _known(mission_dir, _EVENTS, sources.of("events"), "event", unreadable)
    events: tuple[str, ...] | None = None
    mod_events: dict[str, list[str]] = {}
    if known_events is not None:
        root = _parse(mission_dir / _EVENTSPAWNS, comments=True)
        if root is None:
            unreadable.append(_EVENTSPAWNS)
        else:
            mine, mod_events = _split_orphans(_owned_entries(root, "event"), known_events)
            events = tuple(dict.fromkeys(mine))

    return MissionBaseline(
        pointless_groups=groups,
        orphan_event_spawns=events,
        mod_orphan_event_spawns={k: tuple(dict.fromkeys(v)) for k, v in mod_events.items()},
        type_sources=(
            {k: tuple(v) for k, v in known_types.items()} if known_types is not None else None
        ),
        unreadable=tuple(unreadable),
    )


# ── Cache ────────────────────────────────────────────────────────────────
#
# The Doctor card polls, and a modded Chernarus is a few MB of XML (37 ms for
# the stock five files). Keyed on every input file's (mtime_ns, size), so an
# edit, a merge or a map switch is picked up on the next read.

_CACHE: dict[Path, tuple[tuple, MissionBaseline]] = {}


def _signature(mission_dir: Path) -> tuple:
    paths = [mission_dir / p for p in (_TYPES, _EVENTS, _EVENTSPAWNS, _GROUPS, "cfgeconomycore.xml")]
    custom = mission_dir / "custom"
    try:
        paths.extend(sorted(custom.iterdir()) if custom.is_dir() else [])
    except OSError:
        pass
    sig = []
    for p in paths:
        try:
            st = p.stat()
            sig.append((str(p), st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append((str(p), None, None))
    return tuple(sig)


def baseline_for(mission_dir: Path) -> MissionBaseline | None:
    """The cached derivation for *mission_dir*, or None when it is not a mission."""
    if not mission_dir.is_dir():
        return None
    sig = _signature(mission_dir)
    hit = _CACHE.get(mission_dir)
    if hit is not None and hit[0] == sig:
        return hit[1]
    result = derive(mission_dir)
    _CACHE[mission_dir] = (sig, result)
    return result
