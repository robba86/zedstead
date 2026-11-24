"""Spawn coordinate collision detection engine.

Scans XML configuration files for coordinate collisions — duplicate
positions, positions within distanceradius, and cross-event overlaps.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from app.xml.eventspawns_xml import (
    EventSpawnsFile,
    distance,
    find_exact_duplicates,
)
from app.xml.events_xml import EventEntry, EventsFile
from app.xml.playerspawnpoints_xml import PlayerSpawnPointsFile
from app.xml.zombie_territories_xml import ZombieTerritoriesFile
from app.parsers.effectarea_json import EffectAreaFile
from app.traderplus.traderplus_safezone_json import TraderPlusSafeZoneConfig
from app.traderplus.traderplus_general_json import TraderPlusGeneralConfig
from app.traderplus.traderplus_garage_json import TraderPlusGarageConfig


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Collision:
    """One detected collision between two positions."""

    kind: str           # exact_duplicate, radius_conflict, cross_event_overlap
    severity: str       # error, warning, info
    event_a: str
    pos_a_index: int
    pos_a: tuple[float, float]
    event_b: str
    pos_b_index: int
    pos_b: tuple[float, float]
    distance: float     # metres between the two positions
    radius: float       # distanceradius that's violated (0 for exact dupes)
    description: str


@dataclass
class CollisionReport:
    """Full scan results."""

    collisions: list[Collision] = field(default_factory=list)
    scanned_events: int = 0
    scanned_positions: int = 0
    timestamp: str = ""
    # What the scan could not read and left out: a file by name, or
    # "<file>: N positions without a readable x and z". Never raised: one bad
    # file must not cost the findings in every other.
    skipped: list[str] = field(default_factory=list)


# What reading one scanned file can raise: missing, not XML or JSON, not UTF-8
# (a ValueError), or a value that is not the number or list its parser expects.
_UNREADABLE = (OSError, ET.ParseError, ValueError, TypeError, AttributeError, KeyError, IndexError)

_TP_GENERAL_FILE = "TraderPlusGeneralConfig.json"
_TP_GARAGE_FILE = "TraderPlusGarageConfig.json"


def _unread(path: Path) -> CollisionReport:
    """The report for a file that could not be read at all."""
    return CollisionReport(skipped=[path.name], timestamp=datetime.now(timezone.utc).isoformat())


def _xz(x, z) -> tuple[float, float] | None:
    """(x, z) as finite floats, or None for a position no scan can place."""
    try:
        fx, fz = float(x), float(z)
    except (TypeError, ValueError):
        return None
    return (fx, fz) if math.isfinite(fx) and math.isfinite(fz) else None


def _unplaced(file_name: str, count: int) -> list[str]:
    if not count:
        return []
    return [f"{file_name}: {count} position{'s' if count != 1 else ''} without a readable x and z"]


# ---------------------------------------------------------------------------
# Severity ordering
# ---------------------------------------------------------------------------

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def build_collision_query(
    mode: str,
    types: list[str] | None = None,
    check_radius: bool = False,
    check_overlap: bool = False,
) -> str:
    """URL query string for the Visual Map Editor's collision overlay
    (the web AnalysisPage deep link)."""
    parts = [f"collisions={mode}"]
    if types:
        parts.append(f"types={','.join(types)}")
    if check_overlap:
        parts.append("cross-event-overlaps=true")
    if check_radius:
        parts.append("radius-conflicts=true")
    return "&".join(parts)


# ---------------------------------------------------------------------------
# Eventspawn collision scanner
# ---------------------------------------------------------------------------

def _build_event_lookup(
    events_file: EventsFile | None,
    mission_dir: Path | None = None,
    skipped: list[str] | None = None,
) -> dict[str, EventEntry]:
    """Map event name → EventEntry for radius lookups.

    A mod's events are not in db/events.xml: they are in a `custom/` file that
    cfgeconomycore.xml registers, so with *mission_dir* those are read too.
    db/events.xml wins for a name both define.
    """
    lookup: dict[str, EventEntry] = {}
    if mission_dir is not None:
        from app.xml.events_xml import parse_events_file
        from app.xml.xml_economy import registered_custom_entries

        try:
            registered = registered_custom_entries(mission_dir)
        except _UNREADABLE:
            registered = []
        for filename, ce_type in registered:
            if ce_type != "events":
                continue
            try:
                custom = parse_events_file(mission_dir / "custom" / filename)
            except _UNREADABLE:
                if skipped is not None:
                    skipped.append(f"custom/{filename}")
                continue
            for entry in custom.entries:
                lookup.setdefault(entry.name, entry)
    if events_file is not None:
        lookup.update({e.name: e for e in events_file.entries})
    return lookup


def scan_eventspawn_collisions(
    spawns: EventSpawnsFile,
    events: EventsFile | None = None,
    event_names: set[str] | None = None,
    check_radius: bool = False,
    check_overlap: bool = False,
) -> CollisionReport:
    """Scan cfgeventspawns.xml for coordinate collisions.

    Checks:
    1. Exact duplicates within same event (same x, z)
    2. Radius conflicts within same event (within distanceradius) — opt-in
    3. Cross-event overlaps (different events, positions within 5m) — opt-in

    If *event_names* is given, only entries whose name is in the set are
    scanned.  ``None`` (the default) scans all entries.

    *check_radius* and *check_overlap* default to ``False`` because
    overlapping spawn zones are normal in vanilla DayZ.  Enable for
    density analysis.
    """
    skipped: list[str] = []
    # Only the radius check asks about an event, and it needs the mods' too.
    event_lookup = _build_event_lookup(events, spawns.path.parent, skipped) if check_radius else {}
    collisions: list[Collision] = []
    total_positions = 0

    entries = spawns.entries
    if event_names is not None:
        entries = [e for e in entries if e.name in event_names]

    for entry in entries:
        total_positions += len(entry.positions)

    # 1. Exact duplicates within same event
    exact: dict[int, set[tuple[int, int]]] = {}
    for entry in entries:
        dupes = find_exact_duplicates(entry.positions)
        exact[id(entry)] = set(dupes)
        for i, j in dupes:
            p1, p2 = entry.positions[i], entry.positions[j]
            collisions.append(Collision(
                kind="exact_duplicate",
                severity="info",
                event_a=entry.name,
                pos_a_index=i,
                pos_a=(p1.x, p1.z),
                event_b=entry.name,
                pos_b_index=j,
                pos_b=(p2.x, p2.z),
                distance=0.0,
                radius=0.0,
                description=(
                    f"Exact duplicate in '{entry.name}': "
                    f"pos[{i}] and pos[{j}] at ({p1.x:.1f}, {p1.z:.1f})"
                ),
            ))

    # 2. Radius conflicts within same event (opt-in)
    if check_radius:
        for entry in entries:
            ev = event_lookup.get(entry.name)
            dist_radius = ev.distanceradius if ev else 0
            if dist_radius <= 0:
                continue

            positions = entry.positions
            for i in range(len(positions)):
                for j in range(i + 1, len(positions)):
                    p1, p2 = positions[i], positions[j]
                    # Skip pairs already reported as exact duplicates. The same
                    # spot at another angle is not one, so it is a conflict.
                    if (i, j) in exact[id(entry)]:
                        continue
                    d = distance(p1.x, p1.z, p2.x, p2.z)
                    if d < dist_radius:
                        collisions.append(Collision(
                            kind="radius_conflict",
                            severity="info",
                            event_a=entry.name,
                            pos_a_index=i,
                            pos_a=(p1.x, p1.z),
                            event_b=entry.name,
                            pos_b_index=j,
                            pos_b=(p2.x, p2.z),
                            distance=round(d, 1),
                            radius=dist_radius,
                            description=(
                                f"'{entry.name}' pos[{i}] and pos[{j}] are "
                                f"{d:.0f}m apart (distanceradius={dist_radius}m)"
                            ),
                        ))

    # 3. Cross-event overlaps (different events, positions within 5m) — opt-in
    if check_overlap:
        cross_threshold = 5.0
        for a_idx in range(len(entries)):
            for b_idx in range(a_idx + 1, len(entries)):
                entry_a, entry_b = entries[a_idx], entries[b_idx]
                for i, pa in enumerate(entry_a.positions):
                    for j, pb in enumerate(entry_b.positions):
                        d = distance(pa.x, pa.z, pb.x, pb.z)
                        if d < cross_threshold:
                            collisions.append(Collision(
                                kind="cross_event_overlap",
                                severity="info",
                                event_a=entry_a.name,
                                pos_a_index=i,
                                pos_a=(pa.x, pa.z),
                                event_b=entry_b.name,
                                pos_b_index=j,
                                pos_b=(pb.x, pb.z),
                                distance=round(d, 1),
                                radius=cross_threshold,
                                description=(
                                    f"'{entry_a.name}' pos[{i}] and "
                                    f"'{entry_b.name}' pos[{j}] overlap at "
                                    f"({pa.x:.1f}, {pa.z:.1f}), {d:.1f}m apart"
                                ),
                            ))

    # Sort: errors first, then warnings, then info
    collisions.sort(key=lambda c: _SEVERITY_ORDER.get(c.severity, 9))

    return CollisionReport(
        collisions=collisions,
        scanned_events=len(entries),
        scanned_positions=total_positions,
        timestamp=datetime.now(timezone.utc).isoformat(),
        skipped=skipped,
    )


# ---------------------------------------------------------------------------
# Scan from paths (convenience)
# ---------------------------------------------------------------------------

def scan_eventspawn_collisions_from_paths(
    eventspawns_path: Path,
    events_path: Path | None = None,
    event_names: set[str] | None = None,
    check_radius: bool = False,
    check_overlap: bool = False,
) -> CollisionReport:
    """Parse files and run collision scan."""
    return _scan_eventspawn_files(
        eventspawns_path, events_path, event_names, check_radius, check_overlap,
    )[0]


def _scan_eventspawn_files(
    eventspawns_path: Path,
    events_path: Path | None,
    event_names: set[str] | None,
    check_radius: bool,
    check_overlap: bool,
) -> tuple[CollisionReport, EventSpawnsFile | None]:
    """The report, and the parsed spawns for a caller that scans them further."""
    from app.xml.eventspawns_xml import parse_eventspawns_file
    from app.xml.events_xml import parse_events_file

    try:
        spawns = parse_eventspawns_file(eventspawns_path)
    except _UNREADABLE:
        return _unread(eventspawns_path), None

    skipped: list[str] = []
    events = None
    if events_path and check_radius:  # nothing else reads events.xml
        try:
            events = parse_events_file(events_path)
        except _UNREADABLE:
            skipped.append(events_path.name)
    report = scan_eventspawn_collisions(
        spawns, events, event_names=event_names,
        check_radius=check_radius, check_overlap=check_overlap,
    )
    report.skipped = skipped + report.skipped
    return report, spawns


# ---------------------------------------------------------------------------
# Player spawn collision scanner
# ---------------------------------------------------------------------------

def _pos_floats(pos) -> tuple[float, float] | None:
    """A SpawnPos' string coords as floats, None when they are not numbers."""
    return _xz(pos.x, pos.z)


def _placed(positions) -> list[tuple[int, float, float]]:
    """(index, x, z) for every position with readable coords; the index is the
    position's own, so a finding still points at the right <pos>."""
    out: list[tuple[int, float, float]] = []
    for i, pos in enumerate(positions):
        xz = _pos_floats(pos)
        if xz is not None:
            out.append((i, *xz))
    return out


def scan_playerspawn_collisions(
    spf: PlayerSpawnPointsFile,
    cluster_radius: float = 25.0,
) -> CollisionReport:
    """Scan cfgPlayerSpawnPoints.xml for coordinate collisions.

    Checks:
    1. Exact duplicates within same group
    2. Positions within cluster_radius across groups in the same section
    3. Cross-section overlap (fresh/hop/travel at same spot, within 5m)
    """
    collisions: list[Collision] = []
    total_positions = 0
    unreadable = 0

    # Collect all positions per section for cross-section check
    section_positions: dict[str, list[tuple[float, float, str, int, int]]] = {}
    # Each entry: (x, z, section_name, group_idx, pos_idx)

    for sec_name, section in spf.sections.items():
        sec_pos_list: list[tuple[float, float, str, int, int]] = []
        for g_idx, group in enumerate(section.groups):
            placed = _placed(group.positions)
            unreadable += len(group.positions) - len(placed)
            total_positions += len(placed)

            # 1. Exact duplicates within same group
            for a, (i, xi, zi) in enumerate(placed):
                for j, xj, zj in placed[a + 1:]:
                    if xi == xj and zi == zj:
                        collisions.append(Collision(
                            kind="playerspawn_duplicate",
                            severity="info",
                            event_a=f"{sec_name}/{group.name}",
                            pos_a_index=i,
                            pos_a=(xi, zi),
                            event_b=f"{sec_name}/{group.name}",
                            pos_b_index=j,
                            pos_b=(xj, zj),
                            distance=0.0,
                            radius=0.0,
                            description=(
                                f"Duplicate in {sec_name} group '{group.name}': "
                                f"pos[{i}] and pos[{j}] at ({xi:.0f}, {zi:.0f})"
                            ),
                        ))

            for p_idx, x, z in placed:
                sec_pos_list.append((x, z, sec_name, g_idx, p_idx))

        section_positions[sec_name] = sec_pos_list

    # 2. Cluster detection across groups within same section
    for sec_name, positions in section_positions.items():
        section = spf.sections[sec_name]
        for i in range(len(positions)):
            for j in range(i + 1, len(positions)):
                xi, zi, _, gi, pi = positions[i]
                xj, zj, _, gj, pj = positions[j]
                if gi == gj:
                    continue  # same group — already checked for exact dupes
                if xi == xj and zi == zj:
                    # Exact duplicate across groups
                    g_name_i = section.groups[gi].name if gi < len(section.groups) else str(gi)
                    g_name_j = section.groups[gj].name if gj < len(section.groups) else str(gj)
                    collisions.append(Collision(
                        kind="playerspawn_duplicate",
                        severity="info",
                        event_a=f"{sec_name}/{g_name_i}",
                        pos_a_index=pi,
                        pos_a=(xi, zi),
                        event_b=f"{sec_name}/{g_name_j}",
                        pos_b_index=pj,
                        pos_b=(xj, zj),
                        distance=0.0,
                        radius=0.0,
                        description=(
                            f"Duplicate in {sec_name}: "
                            f"group '{g_name_i}' pos[{pi}] and "
                            f"group '{g_name_j}' pos[{pj}] at ({xi:.0f}, {zi:.0f})"
                        ),
                    ))
                    continue
                d = distance(xi, zi, xj, zj)
                if d < cluster_radius:
                    g_name_i = section.groups[gi].name if gi < len(section.groups) else str(gi)
                    g_name_j = section.groups[gj].name if gj < len(section.groups) else str(gj)
                    collisions.append(Collision(
                        kind="playerspawn_cluster",
                        severity="info",
                        event_a=f"{sec_name}/{g_name_i}",
                        pos_a_index=pi,
                        pos_a=(xi, zi),
                        event_b=f"{sec_name}/{g_name_j}",
                        pos_b_index=pj,
                        pos_b=(xj, zj),
                        distance=round(d, 1),
                        radius=cluster_radius,
                        description=(
                            f"{sec_name}: group '{g_name_i}' pos[{pi}] and "
                            f"group '{g_name_j}' pos[{pj}] are "
                            f"{d:.0f}m apart (cluster < {cluster_radius:.0f}m)"
                        ),
                    ))

    # 3. Cross-section overlap (fresh vs hop vs travel, within 5m)
    cross_threshold = 5.0
    sec_names = list(section_positions.keys())
    for a_idx in range(len(sec_names)):
        for b_idx in range(a_idx + 1, len(sec_names)):
            sa, sb = sec_names[a_idx], sec_names[b_idx]
            for xi, zi, _, gi, pi in section_positions[sa]:
                for xj, zj, _, gj, pj in section_positions[sb]:
                    d = distance(xi, zi, xj, zj)
                    if d < cross_threshold:
                        sec_a = spf.sections[sa]
                        sec_b = spf.sections[sb]
                        gn_a = sec_a.groups[gi].name if gi < len(sec_a.groups) else str(gi)
                        gn_b = sec_b.groups[gj].name if gj < len(sec_b.groups) else str(gj)
                        collisions.append(Collision(
                            kind="playerspawn_cross_section",
                            severity="info",
                            event_a=f"{sa}/{gn_a}",
                            pos_a_index=pi,
                            pos_a=(xi, zi),
                            event_b=f"{sb}/{gn_b}",
                            pos_b_index=pj,
                            pos_b=(xj, zj),
                            distance=round(d, 1),
                            radius=cross_threshold,
                            description=(
                                f"{sa} and {sb} overlap: "
                                f"({xi:.0f}, {zi:.0f}), {d:.1f}m apart"
                            ),
                        ))

    collisions.sort(key=lambda c: _SEVERITY_ORDER.get(c.severity, 9))

    scanned_sections = sum(1 for s in spf.sections.values() if s.groups)
    return CollisionReport(
        collisions=collisions,
        scanned_events=scanned_sections,
        scanned_positions=total_positions,
        timestamp=datetime.now(timezone.utc).isoformat(),
        skipped=_unplaced(spf.path.name, unreadable),
    )


def scan_playerspawn_collisions_from_path(
    spawnpoints_path: Path,
    cluster_radius: float = 25.0,
) -> CollisionReport:
    """Parse file and run player spawn collision scan."""
    from app.xml.playerspawnpoints_xml import parse_spawnpoints_file

    try:
        spf = parse_spawnpoints_file(spawnpoints_path)
    except _UNREADABLE:
        return _unread(spawnpoints_path)
    return scan_playerspawn_collisions(spf, cluster_radius)


# ---------------------------------------------------------------------------
# Territory zone collision scanner
# ---------------------------------------------------------------------------

def _circle_overlap_pct(d: float, r1: float, r2: float) -> float:
    """Rough overlap indicator: 100% if fully contained, else ratio."""
    if d <= 0.001:
        return 100.0
    r_small, r_large = min(r1, r2), max(r1, r2)
    if d + r_small <= r_large:
        return 100.0  # fully contained
    if r_small <= 0:
        return 0.0  # degenerate zero-radius zone, not contained → no overlap
    overlap_depth = (r1 + r2) - d
    return min(100.0, round(overlap_depth / (r_small * 2) * 100, 1))


def scan_territory_collisions(
    files: list[tuple[str, ZombieTerritoriesFile]],
) -> CollisionReport:
    """Scan territory files for overlapping zones.

    *files* is a list of (label, parsed_file) tuples, e.g.
    [("zombie_territories.xml", ztf), ("wolf_territories.xml", wtf)].

    Checks:
    1. Overlapping zones within the same file
    2. Overlapping zones across different files
    """
    collisions: list[Collision] = []
    total_zones = 0

    # Flatten all zones with source labels
    all_zones: list[tuple[str, str, int, float, float, float]] = []
    # (file_label, zone_name, flat_index, x, z, r)

    for file_label, ztf in files:
        for territory in ztf.territories:
            for zone in territory.zones:
                all_zones.append((
                    file_label, zone.name, total_zones,
                    zone.x, zone.z, zone.r,
                ))
                total_zones += 1

    for i in range(len(all_zones)):
        fl_a, name_a, idx_a, xa, za, ra = all_zones[i]
        for j in range(i + 1, len(all_zones)):
            fl_b, name_b, idx_b, xb, zb, rb = all_zones[j]
            d = distance(xa, za, xb, zb)
            if d >= ra + rb:
                continue  # no overlap

            overlap = _circle_overlap_pct(d, ra, rb)
            same_file = fl_a == fl_b

            # Full containment
            r_small, r_large = min(ra, rb), max(ra, rb)
            contained = d + r_small <= r_large

            if contained:
                severity = "info"
                kind = "territory_contained"
                # The smaller zone is the one inside, whichever the file lists first.
                inner, outer = (name_a, fl_a), (name_b, fl_b)
                if ra > rb:
                    inner, outer = outer, inner
                desc = (
                    f"'{inner[0]}' ({inner[1]}) fully inside "
                    f"'{outer[0]}' ({outer[1]}) — "
                    f"spawns stack in overlap"
                )
            else:
                severity = "info"
                kind = "territory_overlap"
                desc = (
                    f"'{name_a}' ({fl_a}) and '{name_b}' ({fl_b}) "
                    f"overlap ~{overlap:.0f}%, {d:.0f}m apart"
                )

            collisions.append(Collision(
                kind=kind,
                severity=severity,
                event_a=f"{fl_a}/{name_a}",
                pos_a_index=idx_a,
                pos_a=(xa, za),
                event_b=f"{fl_b}/{name_b}",
                pos_b_index=idx_b,
                pos_b=(xb, zb),
                distance=round(d, 1),
                radius=round(ra + rb, 1),
                description=desc,
            ))

    collisions.sort(key=lambda c: _SEVERITY_ORDER.get(c.severity, 9))

    return CollisionReport(
        collisions=collisions,
        scanned_events=len(files),
        scanned_positions=total_zones,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def read_territory_files(
    mission_dir: Path,
    only: set[str] | None = None,
) -> tuple[list[tuple[str, ZombieTerritoriesFile]], list[str]]:
    """Every env/*territories*.xml that can be read (named in *only*, when
    given), and the names of those that cannot."""
    from app.xml.zombie_territories_xml import parse_zombie_territories

    files: list[tuple[str, ZombieTerritoriesFile]] = []
    skipped: list[str] = []
    env_dir = mission_dir / "env"
    if not env_dir.is_dir():
        return files, skipped
    for p in sorted(env_dir.glob("*territories*.xml")):
        if only and p.name not in only:
            continue
        try:
            files.append((p.name, parse_zombie_territories(p)))
        except _UNREADABLE:
            skipped.append(p.name)
    return files, skipped


def scan_territory_collisions_from_dir(
    mission_dir: Path,
    only: set[str] | None = None,
) -> CollisionReport:
    """Find all *_territories.xml in env/ and scan for overlaps."""
    files, skipped = read_territory_files(mission_dir, only)
    report = scan_territory_collisions(files)
    report.skipped = skipped
    return report


# ---------------------------------------------------------------------------
# Effect area collision scanner
# ---------------------------------------------------------------------------

def scan_effectarea_collisions(eaf: EffectAreaFile) -> CollisionReport:
    """Scan cfgEffectArea.json for overlapping zones."""
    collisions: list[Collision] = []

    areas: list[tuple[str, int, float, float, float]] = []
    # (label, index, x, z, radius)
    for i, a in enumerate(eaf.areas):
        x = a.pos[0]
        z = a.pos[2] if len(a.pos) > 2 else 0.0
        label = f"{a.area_name or a.type}"
        areas.append((label, i, x, z, a.radius))

    for i in range(len(areas)):
        la, idx_a, xa, za, ra = areas[i]
        for j in range(i + 1, len(areas)):
            lb, idx_b, xb, zb, rb = areas[j]
            d = distance(xa, za, xb, zb)
            if d >= ra + rb:
                continue
            overlap = _circle_overlap_pct(d, ra, rb)
            r_small = min(ra, rb)
            contained = d + r_small <= max(ra, rb)
            severity = "info"
            kind = "effectarea_contained" if contained else "effectarea_overlap"
            collisions.append(Collision(
                kind=kind,
                severity=severity,
                event_a=la,
                pos_a_index=idx_a,
                pos_a=(xa, za),
                event_b=lb,
                pos_b_index=idx_b,
                pos_b=(xb, zb),
                distance=round(d, 1),
                radius=round(ra + rb, 1),
                description=(
                    f"{la} and {lb} overlap ~{overlap:.0f}%, {d:.0f}m apart"
                ),
            ))

    collisions.sort(key=lambda c: _SEVERITY_ORDER.get(c.severity, 9))
    return CollisionReport(
        collisions=collisions,
        scanned_events=1,
        scanned_positions=len(areas),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def read_effect_areas(path: Path) -> EffectAreaFile:
    """parse_effectarea_file, raising for an area no scan can place."""
    from app.parsers.effectarea_json import parse_effectarea_file

    eaf = parse_effectarea_file(path)
    for area in eaf.areas:
        if _xz(area.pos[0], area.pos[2] if len(area.pos) > 2 else 0.0) is None:
            raise ValueError(f"{area.area_name or area.type}: no readable position")
        if _xz(area.radius, 0.0) is None:
            raise ValueError(f"{area.area_name or area.type}: no readable radius")
    return eaf


def scan_effectarea_collisions_from_path(path: Path) -> CollisionReport:
    """Parse file and run the effect area scan."""
    try:
        eaf = read_effect_areas(path)
    except _UNREADABLE:
        return _unread(path)
    return scan_effectarea_collisions(eaf)


# ---------------------------------------------------------------------------
# TraderPlus collision scanner
# ---------------------------------------------------------------------------

def _trader_xz(position) -> tuple[float, float] | None:
    """(x, z) of a TraderPlus `[x, y, z]`, None when it is not one."""
    if not isinstance(position, (list, tuple)) or len(position) < 3:
        return None
    return _xz(position[0], position[2])


def read_traderplus_configs(config) -> tuple[
    TraderPlusSafeZoneConfig | None,
    TraderPlusGeneralConfig | None,
    TraderPlusGarageConfig | None,
    list[str],
]:
    """The three TraderPlus files the scans read. One that cannot be read is
    None and its name is in the list."""
    from app.traderplus.traderplus_garage_json import get_garage_config_path, parse_garage_config
    from app.traderplus.traderplus_general_json import get_general_config_path, parse_general_config
    from app.traderplus.traderplus_safezone_json import get_safezone_config_path, parse_safezone_config

    skipped: list[str] = []

    def read(get_path, parse):
        try:
            path = get_path(config)
            return parse(path) if path else None
        except _UNREADABLE:
            skipped.append(path.name)
            return None

    return (
        read(get_safezone_config_path, parse_safezone_config),
        read(get_general_config_path, parse_general_config),
        read(get_garage_config_path, parse_garage_config),
        skipped,
    )


def scan_traderplus_collisions(
    safezone_cfg: TraderPlusSafeZoneConfig | None = None,
    general_cfg: TraderPlusGeneralConfig | None = None,
    garage_cfg: TraderPlusGarageConfig | None = None,
    npc_proximity: float = 5.0,
) -> CollisionReport:
    """Scan TraderPlus configs for coordinate collisions.

    Checks:
    1. Safe zone circle overlaps
    2. Trader NPC positions too close to each other
    3. Garage NPC/parking positions overlapping
    """
    collisions: list[Collision] = []
    total_positions = 0
    skipped: list[str] = []

    # 1. Safe zone circle overlaps
    if safezone_cfg:
        zones = safezone_cfg.safe_area_locations
        total_positions += len(zones)
        for i in range(len(zones)):
            for j in range(i + 1, len(zones)):
                za, zb = zones[i], zones[j]
                # SafeAreaLocation uses x=game_x, y=game_z
                d = distance(za.x, za.y, zb.x, zb.y)
                if d < za.radius + zb.radius:
                    overlap = _circle_overlap_pct(d, za.radius, zb.radius)
                    contained = d + min(za.radius, zb.radius) <= max(za.radius, zb.radius)
                    collisions.append(Collision(
                        kind="safezone_overlap",
                        severity="info",
                        event_a=f"SafeZone/{za.safe_zone_statut or i}",
                        pos_a_index=i,
                        pos_a=(za.x, za.y),
                        event_b=f"SafeZone/{zb.safe_zone_statut or j}",
                        pos_b_index=j,
                        pos_b=(zb.x, zb.y),
                        distance=round(d, 1),
                        radius=round(za.radius + zb.radius, 1),
                        description=(
                            f"Safe zones '{za.safe_zone_statut}' and "
                            f"'{zb.safe_zone_statut}' overlap ~{overlap:.0f}%"
                        ),
                    ))

    # 2. Trader NPC proximity
    if general_cfg:
        npcs = [
            (i, npc.given_name or npc.name or str(i), xz)
            for i, npc in enumerate(general_cfg.traders)
            if (xz := _trader_xz(npc.position)) is not None
        ]
        skipped += _unplaced(_TP_GENERAL_FILE, len(general_cfg.traders) - len(npcs))
        total_positions += len(npcs)
        for a, (i, label_a, (xa, za_)) in enumerate(npcs):
            for j, label_b, (xb, zb_) in npcs[a + 1:]:
                d = distance(xa, za_, xb, zb_)
                if d < npc_proximity:
                    collisions.append(Collision(
                        kind="trader_npc_proximity",
                        severity="info",
                        event_a=f"Trader/{label_a}",
                        pos_a_index=i,
                        pos_a=(xa, za_),
                        event_b=f"Trader/{label_b}",
                        pos_b_index=j,
                        pos_b=(xb, zb_),
                        distance=round(d, 1),
                        radius=npc_proximity,
                        description=(
                            f"Traders '{label_a}' and '{label_b}' "
                            f"are {d:.1f}m apart"
                        ),
                    ))

    # 3. Garage NPC/parking proximity
    if garage_cfg:
        garages = [
            (i, garage, xz)
            for i, garage in enumerate(garage_cfg.npcs)
            if (xz := _trader_xz(garage.parking_position)) is not None
        ]
        skipped += _unplaced(_TP_GARAGE_FILE, len(garage_cfg.npcs) - len(garages))
        total_positions += len(garages) * 2  # NPC + parking per garage
        for a, (i, ga, (xa, za_)) in enumerate(garages):
            for j, gb, (xb, zb_) in garages[a + 1:]:
                # Check parking positions overlap
                d = distance(xa, za_, xb, zb_)
                if d < npc_proximity:
                    collisions.append(Collision(
                        kind="garage_parking_proximity",
                        severity="info",
                        event_a=f"Garage/{ga.class_name or i}",
                        pos_a_index=i,
                        pos_a=(xa, za_),
                        event_b=f"Garage/{gb.class_name or j}",
                        pos_b_index=j,
                        pos_b=(xb, zb_),
                        distance=round(d, 1),
                        radius=npc_proximity,
                        description=(
                            f"Garage parking spots {i} and {j} "
                            f"are {d:.1f}m apart"
                        ),
                    ))

    collisions.sort(key=lambda c: _SEVERITY_ORDER.get(c.severity, 9))
    return CollisionReport(
        collisions=collisions,
        scanned_events=sum(1 for x in [safezone_cfg, general_cfg, garage_cfg] if x),
        scanned_positions=total_positions,
        timestamp=datetime.now(timezone.utc).isoformat(),
        skipped=skipped,
    )


# ---------------------------------------------------------------------------
# Cross-file collision scanner
# ---------------------------------------------------------------------------

def scan_cross_file_collisions(
    spawns: EventSpawnsFile | None = None,
    spf: PlayerSpawnPointsFile | None = None,
    territory_files: list[tuple[str, ZombieTerritoriesFile]] | None = None,
    eaf: EffectAreaFile | None = None,
    safezone_cfg: TraderPlusSafeZoneConfig | None = None,
    event_names: set[str] | None = None,
) -> CollisionReport:
    """Check for coordinate conflicts across different file types.

    Checks:
    1. Eventspawn positions inside trader safe zones
    2. Eventspawn positions inside territory zones
    3. Player spawns inside effect areas
    """
    collisions: list[Collision] = []
    total_positions = 0

    # Flatten spawn positions
    spawn_positions: list[tuple[str, int, float, float]] = []
    if spawns:
        iter_entries = spawns.entries
        if event_names is not None:
            iter_entries = [e for e in iter_entries if e.name in event_names]
        for entry in iter_entries:
            for i, pos in enumerate(entry.positions):
                spawn_positions.append((entry.name, i, pos.x, pos.z))
                total_positions += 1

    # Flatten player spawn positions
    player_positions: list[tuple[str, int, float, float]] = []
    if spf:
        for sec_name, section in spf.sections.items():
            for g_idx, group in enumerate(section.groups):
                for p_idx, x, z in _placed(group.positions):
                    label = f"{sec_name}/{group.name}"
                    player_positions.append((label, p_idx, x, z))
                    total_positions += 1

    # Flatten territory zones
    territory_zones: list[tuple[str, int, float, float, float]] = []
    if territory_files:
        flat_idx = 0
        for file_label, ztf in territory_files:
            for territory in ztf.territories:
                for zone in territory.zones:
                    territory_zones.append((
                        f"{file_label}/{zone.name}", flat_idx,
                        zone.x, zone.z, zone.r,
                    ))
                    flat_idx += 1

    # Flatten effect areas
    effect_areas: list[tuple[str, int, float, float, float]] = []
    if eaf:
        for i, a in enumerate(eaf.areas):
            x = a.pos[0]
            z = a.pos[2] if len(a.pos) > 2 else 0.0
            effect_areas.append((a.area_name or a.type, i, x, z, a.radius))

    # Flatten safe zones
    safe_zones: list[tuple[str, int, float, float, float]] = []
    if safezone_cfg:
        for i, sz in enumerate(safezone_cfg.safe_area_locations):
            safe_zones.append((
                f"SafeZone/{sz.safe_zone_statut or i}", i,
                sz.x, sz.y, float(sz.radius),
            ))

    # 1. Eventspawn positions inside trader safe zones
    for ev_name, ev_idx, ex, ez in spawn_positions:
        for sz_label, sz_idx, sx, sy, sr in safe_zones:
            d = distance(ex, ez, sx, sy)
            if d < sr:
                collisions.append(Collision(
                    kind="spawn_in_safezone",
                    severity="info",
                    event_a=ev_name,
                    pos_a_index=ev_idx,
                    pos_a=(ex, ez),
                    event_b=sz_label,
                    pos_b_index=sz_idx,
                    pos_b=(sx, sy),
                    distance=round(d, 1),
                    radius=sr,
                    description=(
                        f"'{ev_name}' pos[{ev_idx}] is inside "
                        f"{sz_label} (r={sr:.0f}m)"
                    ),
                ))

    # 2. Eventspawn positions inside territory zones
    for ev_name, ev_idx, ex, ez in spawn_positions:
        for tz_label, tz_idx, tx, tz_, tr in territory_zones:
            d = distance(ex, ez, tx, tz_)
            if d < tr:
                collisions.append(Collision(
                    kind="spawn_in_territory",
                    severity="info",
                    event_a=ev_name,
                    pos_a_index=ev_idx,
                    pos_a=(ex, ez),
                    event_b=tz_label,
                    pos_b_index=tz_idx,
                    pos_b=(tx, tz_),
                    distance=round(d, 1),
                    radius=tr,
                    description=(
                        f"'{ev_name}' pos[{ev_idx}] is inside "
                        f"territory '{tz_label}' (r={tr:.0f}m)"
                    ),
                ))

    # 3. Player spawns inside effect areas
    for ps_label, ps_idx, px, pz in player_positions:
        for ea_label, ea_idx, eax, eaz, ear in effect_areas:
            d = distance(px, pz, eax, eaz)
            if d < ear:
                collisions.append(Collision(
                    kind="playerspawn_in_effectarea",
                    severity="info",
                    event_a=ps_label,
                    pos_a_index=ps_idx,
                    pos_a=(px, pz),
                    event_b=ea_label,
                    pos_b_index=ea_idx,
                    pos_b=(eax, eaz),
                    distance=round(d, 1),
                    radius=ear,
                    description=(
                        f"Player spawn '{ps_label}' pos[{ps_idx}] is inside "
                        f"effect area '{ea_label}' (r={ear:.0f}m)"
                    ),
                ))

    collisions.sort(key=lambda c: _SEVERITY_ORDER.get(c.severity, 9))
    return CollisionReport(
        collisions=collisions,
        scanned_events=0,
        scanned_positions=total_positions,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Scan All — orchestrator
# ---------------------------------------------------------------------------

def scan_all_collisions(
    mission_dir: Path,
    config=None,
    event_names: set[str] | None = None,
    check_radius: bool = False,
    check_overlap: bool = False,
) -> CollisionReport:
    """Run all collision scans and merge results.

    *config* is a DayzConfig, needed for TraderPlus path resolution.
    """
    from app.parsers.effectarea_json import get_effectarea_path
    from app.xml.eventspawns_xml import get_eventspawns_path
    from app.xml.events_xml import get_events_path
    from app.xml.playerspawnpoints_xml import get_spawnpoints_path, parse_spawnpoints_file

    all_collisions: list[Collision] = []
    skipped: list[str] = []
    total_events = 0
    total_positions = 0

    def add(report: CollisionReport) -> None:
        nonlocal total_events, total_positions
        all_collisions.extend(report.collisions)
        skipped.extend(name for name in report.skipped if name not in skipped)
        total_events += report.scanned_events
        total_positions += report.scanned_positions

    # --- Eventspawns ---
    spawns = None
    spawns_path = get_eventspawns_path(mission_dir)
    if spawns_path:
        r, spawns = _scan_eventspawn_files(
            spawns_path, get_events_path(mission_dir), event_names, check_radius, check_overlap,
        )
        add(r)

    # --- Player spawns ---
    spf = None
    sp_path = get_spawnpoints_path(mission_dir)
    if sp_path:
        try:
            spf = parse_spawnpoints_file(sp_path)
        except _UNREADABLE:
            add(_unread(sp_path))
        else:
            add(scan_playerspawn_collisions(spf))

    # --- Territories ---
    territory_files, unread_territories = read_territory_files(mission_dir)
    skipped.extend(unread_territories)
    if territory_files:
        add(scan_territory_collisions(territory_files))

    # --- Effect areas ---
    eaf = None
    ea_path = get_effectarea_path(mission_dir)
    if ea_path:
        try:
            eaf = read_effect_areas(ea_path)
        except _UNREADABLE:
            add(_unread(ea_path))
        else:
            add(scan_effectarea_collisions(eaf))

    # --- TraderPlus ---
    sz_cfg = None
    if config:
        sz_cfg, gen_cfg, gar_cfg, unread_traderplus = read_traderplus_configs(config)
        skipped.extend(unread_traderplus)
        if any([sz_cfg, gen_cfg, gar_cfg]):
            add(scan_traderplus_collisions(sz_cfg, gen_cfg, gar_cfg))

    # --- Cross-file ---
    # Its positions are the ones counted above, so only its findings are added.
    r = scan_cross_file_collisions(
        spawns=spawns,
        spf=spf,
        territory_files=territory_files or None,
        eaf=eaf,
        safezone_cfg=sz_cfg,
        event_names=event_names,
    )
    all_collisions.extend(r.collisions)

    all_collisions.sort(key=lambda c: _SEVERITY_ORDER.get(c.severity, 9))

    return CollisionReport(
        collisions=all_collisions,
        scanned_events=total_events,
        scanned_positions=total_positions,
        timestamp=datetime.now(timezone.utc).isoformat(),
        skipped=skipped,
    )

