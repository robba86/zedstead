"""RadZone, and the zone checks it shares with ToxicZone (plans/48 A).

RadZone (by TraderPlus' author) keeps its settings as three JSON files under
``<profiles>/RadZone/RZConfig/`` and writes them with Chernarus defaults on its
first boot. A zone is a named centre plus a radius, or a rectangle when
``IsSquareZone`` is 1; ``Y`` is the map's Z. The crate system places loot
crates per area, and its areas carry the same names as the zones.

RadZone's source is not public, so unlike ``dynamic_ai.py`` nothing here can
restate the mod's own load rules. Every check below comes from the shape of
the files it writes (measured in plans/48 "Step 0 results — RadZone"), and
where the conclusion is inference it says "probably". What RadZone itself
rejects or corrects is unknown and is not claimed.

This module never writes, and never reads inside ``@RadZone/``: the author's
terms forbid re-use, so zsm summarises what the files mean and ships none of
them.

ToxicZone (``toxic_zone.py``, the same author's older mod) writes its static
zones in the same shape under other key names. What both need — reading the
zones, the file, duplicate, rectangle, off-map and "still the Chernarus
defaults" checks, and whole-number decimals — takes a ``ZoneMod`` that names
the keys, so the two mods say the same thing the same way. RadZone's own
module-level names (``RADIATION``, ``installed_name``, ``read_zones`` ...) stay
as they were; the editors, the map layer and the tests import them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from app.ops.log_analysis import _synthetic as finding
from app.ops.log_analysis import profiles_dir
from app.ops.mod_files import (
    display_path,
    doctor_verdict,
    installed_mod_name,
    json_number,
    read_json,
)

@dataclass(frozen=True)
class ZoneMod:
    """What differs between the zone mods: names, files and keys."""

    key: str  # finding-id prefix, e.g. "radzone"
    mod: str  # the mod's name in the mod list
    workshop_id: str
    label: str  # how the mod reads in a sentence
    folder: tuple[str, ...]  # the config folder, under the profiles dir
    zone_file: str
    zone_list: str  # the key holding the zone list
    name_key: str  # the key holding a zone's name
    noun: str  # "radiation zone"
    #: The zones the mod writes on its first boot, as name -> centre.
    defaults: dict[str, tuple[float, float]]
    defaults_are: str  # what those defaults are, in words
    #: Keys the mod writes as whole numbers, wherever they sit in its files.
    whole_numbers: frozenset[str]

    def installed_name(self, config) -> str | None:
        """The active mod-list entry that is this mod, by workshop ID."""
        return installed_mod_name(config, self.mod, self.workshop_id)

    def config_dir(self, config) -> Path:
        return profiles_dir(config).joinpath(*self.folder)


MOD_NAME = "@RadZone"
WORKSHOP_ID = "2294323732"
LABEL = "RadZone"

RADIATION = "RadiationConfigV1.json"
CRATES = "CrateSystemConfigV1.json"
CLOTHES = "ClothesProtectionConfigV1.json"

#: The two zones RadZone writes on its first boot (the airfields NWAF and NEAF
#: on Chernarus), as name -> centre. On another map these are still Chernarus
#: coordinates: they land somewhere meaningless, or off the map.
CHERNARUS_DEFAULTS: dict[str, tuple[float, float]] = {
    "NWAF": (4584.0, 10254.0),
    "NEAF": (12153.0, 12540.0),
}
_DEFAULT_TOLERANCE = 1.0  # metres; the defaults are whole numbers

#: Keys RadZone writes as whole numbers (plans/48 Step 0), wherever they sit in
#: its three files. DayZ's JSON loader reads them as ints.
WHOLE_NUMBER_KEYS = frozenset({
    "IsRadiationZoneActive", "UseChatMessagesInsteadOfNotifications", "SuitsDamage",
    "RequiredProtection", "CriticalProtection", "IsSquareZone", "IsMsgActive",
    "NbSickGivenForRadiation", "Protection", "IsWeapon", "RZNbCrate", "Max",
    "RZMaxLoot", "TimerLoot", "IsCrateSystemActive",
})


RADZONE = ZoneMod(
    key="radzone",
    mod=MOD_NAME,
    workshop_id=WORKSHOP_ID,
    label=LABEL,
    folder=("RadZone", "RZConfig"),
    zone_file=RADIATION,
    zone_list="RadiationAreaLocation",
    name_key="RadiationZoneStatut",
    noun="radiation zone",
    defaults=CHERNARUS_DEFAULTS,
    defaults_are="Chernarus' two airfields",
    whole_numbers=WHOLE_NUMBER_KEYS,
)


def decimals_in_whole_numbers(
    doc, where: str = "", keys: frozenset[str] = WHOLE_NUMBER_KEYS
) -> list[str]:
    """``path = value`` for every whole-number key holding a decimal."""
    bad: list[str] = []
    if isinstance(doc, dict):
        for key, value in doc.items():
            here = f"{where}.{key}" if where else key
            if key in keys and isinstance(value, float):
                bad.append(f"{here} = {value!r}")
            else:
                bad.extend(decimals_in_whole_numbers(value, here, keys))
    elif isinstance(doc, list):
        for i, item in enumerate(doc):
            bad.extend(decimals_in_whole_numbers(item, f"{where}[{i}]", keys))
    return bad


def installed_name(config) -> str | None:
    """The active mod-list entry that is RadZone, by workshop ID."""
    return RADZONE.installed_name(config)


def config_dir(config) -> Path:
    return RADZONE.config_dir(config)


@dataclass(frozen=True)
class Zone:
    """One ``RadiationAreaLocation`` entry, numbers already read."""

    index: int
    name: str
    square: bool
    x: float | None
    y: float | None
    radius: float | None
    corners: tuple[float | None, float | None, float | None, float | None]

    @property
    def label(self) -> str:
        return repr(self.name) if self.name else f"#{self.index + 1}"

    def contains(self, x: float, y: float) -> bool | None:
        """Whether a point lies in the zone; None when the zone's own numbers
        are missing, so a broken zone never makes a crate look misplaced."""
        if self.square:
            x1, y1, x2, y2 = self.corners
            if None in self.corners:
                return None
            return min(x1, x2) <= x <= max(x1, x2) and min(y1, y2) <= y <= max(y1, y2)
        if self.x is None or self.y is None or self.radius is None:
            return None
        return math.hypot(x - self.x, y - self.y) <= self.radius


def _unset(zone: Zone) -> bool:
    """A square zone with a corner still -1 (or missing)."""
    return any(c is None or c < 0 for c in zone.corners)


def _anchor_points(zone: Zone) -> list[tuple[float, float]]:
    """The centre of a circle, or both corners of a rectangle. A rectangle with
    an unset corner has none: that is its own finding, not an off-map one."""
    if zone.square:
        if _unset(zone):
            return []
        x1, y1, x2, y2 = zone.corners
        return [(x1, y1), (x2, y2)]
    return [(zone.x, zone.y)] if zone.x is not None and zone.y is not None else []


def read_zones(doc: dict, spec: ZoneMod = RADZONE) -> list[Zone]:
    items = doc.get(spec.zone_list)
    if not isinstance(items, list):
        return []
    zones = []
    for i, entry in enumerate(items):
        if not isinstance(entry, dict):
            continue
        name = entry.get(spec.name_key)
        zones.append(
            Zone(
                index=i,
                name=name if isinstance(name, str) else "",
                square=json_number(entry.get("IsSquareZone"), 0.0) == 1.0,
                x=json_number(entry.get("X")),
                y=json_number(entry.get("Y")),
                radius=json_number(entry.get("Radius")),
                corners=tuple(json_number(entry.get(k)) for k in ("X1", "Y1", "X2", "Y2")),
            )
        )
    return zones


def current_terrain(config) -> str | None:
    from app.maps.registry import terrain_from_mission
    from app.modmgr.mods import get_current_mission

    mission = get_current_mission(config.server_cfg_path)
    return terrain_from_mission(mission) if mission else None


def file_findings(spec: ZoneMod, config, path: Path, doc, err) -> list[dict]:
    if err is not None:
        return [finding(
            f"{spec.key}_file_unreadable", "error",
            f"{path.name} cannot be read",
            f"{display_path(config, path)}: {err}",
            f"DayZ cannot load a file that is not valid JSON, so {spec.label} does not "
            "get the settings in it. What the mod does instead is not documented.",
            "Fix the syntax at that position, or delete the file so the mod writes "
            "defaults on the next boot.",
        )]
    if doc is None:
        return [finding(
            f"{spec.key}_file_missing", "notice",
            f"{path.name} does not exist yet",
            display_path(config, path),
            f"{spec.label} writes each of its files with defaults on the first boot "
            "that finds it missing.",
            "Start the server once.",
        )]
    return []


def folder_finding(spec: ZoneMod, config, folder: Path, files: int) -> dict:
    return finding(
        f"{spec.key}_folder_missing", "notice",
        f"{spec.label}'s config folder does not exist yet",
        display_path(config, folder),
        f"The mod creates it and writes its {files} files with defaults on its "
        "first boot.",
        "Start the server once.",
    )


def switched_off(doc: dict, switch: str, fid: str, title: str, why: str, path_name: str) -> list[dict]:
    """A notice when a mod's on/off *switch* is 0 (a missing key counts as on)."""
    if json_number(doc.get(switch), 1.0) != 0.0:
        return []
    return [finding(
        fid, "notice", title, f"{switch} is 0 in {path_name}", why,
        f"Set {switch} to 1 when you want them back.",
    )]


def points_off_map(points: list[tuple[str, list[tuple[float, float]]]], terrain: str | None):
    """Labels whose points lie beyond the installed map's edge, and its size;
    ``([], None)`` for a map whose size zsm does not know."""
    from app.maps.registry import MAP_SIZES

    size = MAP_SIZES.get(terrain or "")
    if not size:
        return [], None
    return [
        label for label, pts in points
        if any(not (0 <= px <= size and 0 <= py <= size) for px, py in pts)
    ], size


def zone_findings(spec: ZoneMod, zones: list[Zone], terrain: str | None) -> list[dict]:
    from app.maps.registry import display_name

    out: list[dict] = []

    seen: dict[str, int] = {}
    for z in zones:
        if z.name:
            seen[z.name] = seen.get(z.name, 0) + 1
    dupes = sorted(n for n, c in seen.items() if c > 1)
    if dupes:
        out.append(finding(
            f"{spec.key}_duplicate_zone", "warning",
            f"Two {spec.noun}s share a name",
            ", ".join(dupes),
            "The enter/exit messages are per zone, and the logs report zones by "
            "name. Two zones with one name make it ambiguous which one is meant.",
            f"Give each zone in {spec.zone_file} its own {spec.name_key}.",
        ))

    no_corners = [z.label for z in zones if z.square and _unset(z)]
    if no_corners:
        out.append(finding(
            f"{spec.key}_square_without_corners", "warning",
            "A square zone has no corners set",
            ", ".join(no_corners),
            "IsSquareZone is 1, so the zone is the rectangle X1/Y1 to X2/Y2, and at "
            "least one corner is still -1 (unset). The zone is then a rectangle at "
            "the map's edge, and probably never covers anyone.",
            "Set X1/Y1 to the bottom-left corner and X2/Y2 to the top-right, or set "
            "IsSquareZone back to 0 to use X, Y and Radius.",
        ))

    flipped = [
        z.label
        for z in zones
        if z.square
        and not _unset(z)
        and (z.corners[0] >= z.corners[2] or z.corners[1] >= z.corners[3])
    ]
    if flipped:
        out.append(finding(
            f"{spec.key}_square_flipped", "warning",
            "A square zone's corners are the wrong way round",
            ", ".join(flipped),
            f"{spec.label} documents X1/Y1 as the bottom-left corner and X2/Y2 as "
            "the top-right. Here X1 is not below X2, or Y1 not below Y2. Without "
            "checking the mod's code it is not certain, but a zone like that "
            "probably never triggers.",
            "Swap the corners so X1 < X2 and Y1 < Y2.",
        ))

    outside, size = points_off_map([(z.label, _anchor_points(z)) for z in zones], terrain)
    if outside:
        out.append(finding(
            f"{spec.key}_zone_off_map", "warning",
            f"A {spec.noun} lies outside the map",
            ", ".join(outside),
            f"The installed map is {display_name(terrain)}, {size} m across. A zone "
            "whose centre or corner is beyond that edge covers nothing players can "
            "reach.",
            f"Move the zone onto the map in {spec.zone_file}. X is east, Y is north "
            "(the map's Z).",
        ))

    if terrain and terrain != "chernarusplus":
        stale = [
            z.name
            for z in zones
            if not z.square
            and z.name in spec.defaults
            and z.x is not None
            and z.y is not None
            and math.hypot(z.x - spec.defaults[z.name][0], z.y - spec.defaults[z.name][1])
            <= _DEFAULT_TOLERANCE
        ]
        if stale:
            out.append(finding(
                f"{spec.key}_chernarus_defaults", "warning",
                f"The zones are still {spec.label}'s Chernarus defaults",
                ", ".join(stale),
                f"{spec.label} writes zones for {spec.defaults_are} on its first "
                f"boot, whatever the map. On {display_name(terrain)} those "
                "coordinates point at an unrelated spot, or off the map.",
                f"Move or replace the zones in {spec.zone_file} for this map. The "
                "crate positions the mod writes beside them are Chernarus ones too.",
            ))
    return out


def _crate_findings(doc: dict, zones: list[Zone]) -> list[dict]:
    active = json_number(doc.get("IsCrateSystemActive"), 1.0)
    areas = doc.get("AreaLocation")
    if active == 0.0 or not isinstance(areas, list):
        return []
    by_name = {z.name: z for z in zones if z.name}
    lists = doc.get("ListLoots")
    loot_names = {
        entry.get("Name") for entry in (lists if isinstance(lists, list) else [])
        if isinstance(entry, dict) and isinstance(entry.get("Name"), str)
    }
    unmatched: list[str] = []
    stray: list[str] = []
    no_loot: list[str] = []
    for i, area in enumerate(areas):
        if not isinstance(area, dict):
            continue
        name = area.get("RZName") if isinstance(area.get("RZName"), str) else ""
        zone = by_name.get(name)
        if zone is None:
            unmatched.append(repr(name) if name else f"#{i + 1}")
            continue
        crates = area.get("RZCrateLocations")
        if not isinstance(crates, list):
            continue
        for j, crate in enumerate(crates):
            if not isinstance(crate, dict):
                continue
            loot = crate.get("Name") if isinstance(crate.get("Name"), str) else ""
            if loot_names and loot not in loot_names:
                no_loot.append(f"{name}: crate {j + 1} ({loot or 'no name'})")
            pos = crate.get("POS")
            if not isinstance(pos, list) or len(pos) != 3:
                continue
            x, z = json_number(pos[0]), json_number(pos[2])
            if x is None or z is None:
                continue
            if zone.contains(x, z) is False:
                stray.append(f"{name}: crate {j + 1} at {x:.0f}, {z:.0f}")

    out = []
    if unmatched:
        out.append(finding(
            "radzone_crate_area_unmatched", "warning",
            "A crate area names no radiation zone",
            ", ".join(unmatched),
            "In the files RadZone writes, every crate area carries the name of a "
            "zone. The mod's source is not public, so this is the likely pairing "
            "rather than a documented one; an area whose name matches no zone "
            "probably spawns its crates without a zone around them.",
            "Set RZName in CrateSystemConfigV1.json to the RadiationZoneStatut of the "
            "zone it belongs to.",
        ))
    if stray:
        out.append(finding(
            "radzone_crate_outside_zone", "warning",
            "A loot crate sits outside its zone",
            "; ".join(stray[:10]) + (f" (+{len(stray) - 10} more)" if len(stray) > 10 else ""),
            "Every crate RadZone places by default is inside the zone its area is "
            "named after. One outside it can be looted without any radiation "
            "protection, which is usually not the point.",
            "Move the crate's POS inside the zone, or grow the zone. POS is x, "
            "height, z.",
        ))
    if no_loot:
        out.append(finding(
            "radzone_crate_loot_unknown", "warning",
            "A loot crate names no loot list",
            "; ".join(no_loot[:10]) + (f" (+{len(no_loot) - 10} more)" if len(no_loot) > 10 else ""),
            "A crate's Name is the loot list it is filled from: in the default files "
            "every crate names one of ListLoots, and the boot log reports the crate "
            "under that name. One naming no list probably spawns empty.",
            "Set the crate's Name to one of the Name values under ListLoots in "
            "CrateSystemConfigV1.json.",
        ))
    return out


def decimal_findings(spec: ZoneMod, path: Path, doc: dict) -> list[dict]:
    bad = decimals_in_whole_numbers(doc, keys=spec.whole_numbers)
    if not bad:
        return []
    return [finding(
        f"{spec.key}_decimal_in_whole_number", "warning",
        f"A whole-number field in {path.name} holds a decimal",
        ", ".join(bad[:5]) + (f" (+{len(bad) - 5} more)" if len(bad) > 5 else ""),
        f"{spec.label} writes these as whole numbers, and DayZ's JSON loader reads "
        "them as ints. What the mod does with a decimal there is not documented; on "
        "another mod the same mistake stopped a server from starting.",
        "Write the value without a decimal point.",
    )]


def build_report(config) -> dict | None:
    """Everything zsm can tell about the RadZone install, or None when the mod
    is not installed (or is disabled)."""
    name = installed_name(config)
    if name is None:
        return None

    folder = config_dir(config)
    radiation_path = folder / RADIATION
    crates_path = folder / CRATES
    clothes_path = folder / CLOTHES
    findings: list[dict] = []
    zones: list[Zone] = []
    terrain = current_terrain(config)

    if not folder.is_dir():
        findings.append(folder_finding(RADZONE, config, folder, 3))
    else:
        doc, err = read_json(radiation_path)
        findings.extend(file_findings(RADZONE, config, radiation_path, doc, err))
        if doc is not None:
            findings.extend(decimal_findings(RADZONE, radiation_path, doc))
            zones = read_zones(doc)
            findings.extend(switched_off(
                doc, "IsRadiationZoneActive", "radzone_zones_off",
                "Radiation zones are switched off",
                "No zone makes anyone sick while the switch is off.", RADIATION,
            ))
            findings.extend(zone_findings(RADZONE, zones, terrain))
        crates, crates_err = read_json(crates_path)
        findings.extend(file_findings(RADZONE, config, crates_path, crates, crates_err))
        if crates is not None:
            findings.extend(decimal_findings(RADZONE, crates_path, crates))
            if doc is not None:
                findings.extend(_crate_findings(crates, zones))
        clothes, clothes_err = read_json(clothes_path)
        findings.extend(file_findings(RADZONE, config, clothes_path, clothes, clothes_err))
        if clothes is not None:
            findings.extend(decimal_findings(RADZONE, clothes_path, clothes))

    return {
        "mod": name,
        "folder": display_path(config, folder),
        "terrain": terrain,
        "files": {
            "radiation": radiation_path.is_file(),
            "crates": crates_path.is_file(),
            "clothes": clothes_path.is_file(),
        },
        "zones": [
            {
                "name": z.name,
                "shape": "square" if z.square else "circle",
                "x": z.x,
                "y": z.y,
                "radius": z.radius,
                "corners": list(z.corners),
            }
            for z in zones
        ],
        "findings": findings,
    }


def doctor_row(config) -> tuple[str, bool, str] | None:
    """The Doctor's one-line verdict, or None when the mod is not installed."""
    report = build_report(config)
    if report is None:
        return None
    zones = len(report["zones"])
    return doctor_verdict(
        LABEL, report["findings"], [f"{zones} zone{'' if zones == 1 else 's'}"] if zones else []
    )
