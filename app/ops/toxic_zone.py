"""ToxicZone: read-only checks over its zone files (plans/48, ToxicZone pass).

ToxicZone is RadZone's older sibling by the same author. Its five settings
files live under ``<profiles>/ToxicZone/TZConfig/`` (all ``V5``) and are written
with Chernarus defaults on the first boot. Static zones have RadZone's shape
under other key names, so they go through ``zone_mods``' shared checks. What is
ToxicZone's own:

- **Moving zones.** A zone runs from a start point to an end point. The mod
  moves it ``Speed`` metres of **X** per minute, reads its Y off the line
  ``Y = Coeff * X + YtoOrigin`` and resets it after ``Norme / |Speed|`` minutes.
  The three derived values are typed into the file by hand, and the default
  zone ships a wrong ``YtoOrigin``: the mod computes it one line before
  ``Coeff``, so it stores ``StartY`` (measured, plans/48 Step 0). zsm's
  editors and the map compute them on save (D3) with ``apply_derived()``;
  this module reports a file whose values disagree.
- **``Norme`` is the east-west span here, not the path's length** (D7). The
  zone only resets after travelling ``Norme`` metres of X, so the straight-line
  length the mod's own formula gives makes a sloped path run past its end.
- **Direction is ``Speed``'s sign** (D8). The zone moves west on a negative
  ``Speed``; the reset timer takes ``|Speed|``.
- **``AdminUID`` ships holding the author's Steam ID** (D4): a finding, and
  the editor is where the admin replaces it. Never edited here.
- **Load order.** ToxicZone's ``CreateCharacter`` does not call the original,
  so loaded after Expansion's spawn selection it switches the picker off
  (measured by a join in 2026 Q3). The catalog puts it first; this module
  reports an order that still has it after.

The source is public (github.com/TheDmitri/ToxicZone), so unlike RadZone the
rules above are read from it, not inferred. The mod's terms forbid repacking:
nothing here is copied from its files, and this module never writes and never
reads inside ``@ToxicZone/``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.ops.log_analysis import _synthetic as finding
from app.ops.mod_files import display_path, doctor_verdict, installed_mod_name, json_number, read_json
from app.ops.zone_mods import (
    ZoneMod,
    Zone,
    current_terrain,
    decimal_findings,
    file_findings,
    folder_finding,
    points_off_map,
    read_zones,
    switched_off,
    zone_findings,
)
from app.shared.json_utils import float32_equal

GENERAL = "GeneralConfigV5.json"
CLOTHES = "ClothesProtectionConfigV5.json"
LOOT = "LootTZConfigV5.json"
STATIC = "StaticToxicZoneConfigV5.json"
MOVING = "MovingToxicZoneConfigV5.json"
FILES = (GENERAL, CLOTHES, LOOT, STATIC, MOVING)

#: The author's own Steam ID, which ``AdminUID`` holds on a fresh install.
AUTHOR_UID = "76561198047475641"

#: The moving zone the mod writes by default (Zelenogorsk towards Novy Sobor
#: on Chernarus), as name -> start point.
MOVING_DEFAULTS: dict[str, tuple[float, float]] = {"ZelenToNovy": (2000.0, 5000.0)}

#: Where the catalog loads ToxicZone relative to: Expansion's spawn selection.
SPAWN_SELECTION = ("@DayZ-Expansion-Spawn-Selection", "2804241648")

TOXICZONE = ZoneMod(
    key="toxiczone",
    mod="@ToxicZone",
    workshop_id="1752669393",
    label="ToxicZone",
    folder=("ToxicZone", "TZConfig"),
    zone_file=STATIC,
    zone_list="StaticToxicAreaLocation",
    name_key="ToxicZoneStatut",
    noun="toxic zone",
    defaults={"TZKamensk": (7890.0, 14697.0), "TZTisy": (1655.0, 14068.0)},
    defaults_are="the Kamensk military base and Tisy",
    # Measured in plans/48 Step 0: every int-typed key across the five files.
    whole_numbers=frozenset({
        "ToxicBloodLoss", "NbSickGiven", "SuitsDamage", "GP5DamageGrenade",
        "NbSickGivenforToxicSmoke", "RequiredProtection", "Protection", "TimerLoot",
        "IsActive", "IsToxicZoneActive", "IsSquareZone", "IsMsgActive", "IsOnlyGasMask",
        "IsLootActive", "NumbCratePerTime", "MaxLoot", "Max", "IsMovingZoneActive",
        "Speed", "Norme",
    }),
)

MOD_NAME = TOXICZONE.mod
LABEL = TOXICZONE.label

# The mod stores Coeff as a 32-bit float; compare with that much slack.
_COEFF_TOLERANCE = 1e-5
_ORIGIN_TOLERANCE = 1.0  # metres


def installed_name(config) -> str | None:
    return TOXICZONE.installed_name(config)


def config_dir(config):
    return TOXICZONE.config_dir(config)


# ── Moving zones ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Derived:
    """What the mod needs typed in besides the two points (D3, D7, D8)."""

    coeff: float
    y_to_origin: float
    norme: int
    direction: int  # +1 east, -1 west: the sign Speed must carry


def derived(start_x: float, start_y: float, end_x: float, end_y: float) -> Derived | None:
    """The values for a path from start to end, or None for a north-south path,
    which the mod's ``Y = Coeff * X + YtoOrigin`` cannot express."""
    dx = end_x - start_x
    if dx == 0:
        return None
    coeff = (end_y - start_y) / dx
    return Derived(
        coeff=coeff,
        y_to_origin=start_y - coeff * start_x,
        norme=int(round(abs(dx))),
        direction=1 if dx > 0 else -1,
    )


def apply_derived(entry: dict) -> None:
    """Write the derived values into one ``MovingToxicAreaLocation`` entry, in
    place: ``Coeff``, ``YtoOrigin``, ``Norme``, and ``Speed``'s sign. The one
    place the editors and the map compute them (D3). Raises ValueError when the
    ends are missing or form a north-south path."""
    ends = [json_number(entry.get(k)) for k in ("StartX", "StartY", "EndX", "EndY")]
    if None in ends:
        raise ValueError("needs StartX, StartY, EndX and EndY")
    want = derived(*ends)
    if want is None:
        raise ValueError(
            "StartX and EndX are the same. ToxicZone moves a zone along X, so a "
            "path straight north or south cannot move; move one end sideways"
        )
    for key, value in (("Coeff", want.coeff), ("YtoOrigin", want.y_to_origin), ("Norme", want.norme)):
        # The mod holds these as 32-bit floats and writes them with 17 digits,
        # so a path that did not move keeps the file's own text.
        current = json_number(entry.get(key))
        if current is None or not float32_equal(current, value):
            entry[key] = value
    speed = json_number(entry.get("Speed"))
    if speed is not None:
        speed = abs(speed) * want.direction
        entry["Speed"] = int(speed) if speed == int(speed) else speed


@dataclass(frozen=True)
class MovingZone:
    index: int
    name: str
    start: tuple[float | None, float | None]
    end: tuple[float | None, float | None]
    radius: float | None
    speed: float | None
    coeff: float | None
    y_to_origin: float | None
    norme: float | None

    @property
    def label(self) -> str:
        return repr(self.name) if self.name else f"#{self.index + 1}"

    @property
    def points_known(self) -> bool:
        return None not in (*self.start, *self.end)

    def expected(self) -> Derived | None:
        if not self.points_known:
            return None
        return derived(*self.start, *self.end)


def read_moving(doc: dict) -> list[MovingZone]:
    items = doc.get("MovingToxicAreaLocation")
    out: list[MovingZone] = []
    for i, entry in enumerate(items if isinstance(items, list) else []):
        if not isinstance(entry, dict):
            continue
        name = entry.get("MovingToxicZoneStatut")
        num = lambda key: json_number(entry.get(key))  # noqa: E731
        out.append(MovingZone(
            index=i,
            name=name if isinstance(name, str) else "",
            start=(num("StartX"), num("StartY")),
            end=(num("EndX"), num("EndY")),
            radius=num("Radius"),
            speed=num("Speed"),
            coeff=num("Coeff"),
            y_to_origin=num("YtoOrigin"),
            norme=num("Norme"),
        ))
    return out


def stale_parts(zone: MovingZone, want: Derived) -> list[str]:
    parts = []
    if zone.coeff is None or abs(zone.coeff - want.coeff) > _COEFF_TOLERANCE * max(1.0, abs(want.coeff)):
        parts.append("Coeff")
    if zone.y_to_origin is None or abs(zone.y_to_origin - want.y_to_origin) > _ORIGIN_TOLERANCE:
        parts.append("YtoOrigin")
    if zone.norme is None or round(zone.norme) != want.norme:
        parts.append("Norme")
    return parts


def _moving_findings(doc: dict, terrain: str | None) -> list[dict]:
    from app.maps.registry import display_name

    zones = read_moving(doc)
    out: list[dict] = []
    vertical = [z.label for z in zones if z.points_known and z.start[0] == z.end[0]]
    if vertical:
        out.append(finding(
            "toxiczone_moving_north_south", "error",
            "A moving zone runs straight north-south",
            ", ".join(vertical),
            "ToxicZone moves a zone along X and reads Y off the line's slope. Start "
            "and end with the same X have no slope: the mod divides by zero.",
            "Move one end a little east or west in the Moving Zones editor.",
        ))

    stale = []
    backwards = []
    still = []
    for z in zones:
        want = z.expected()
        if want is None:
            continue
        parts = stale_parts(z, want)
        if parts:
            stale.append(f"{z.label}: {', '.join(parts)}")
        if z.speed == 0:
            still.append(z.label)
        elif z.speed is not None and (z.speed > 0) != (want.direction > 0):
            backwards.append(z.label)
    if stale:
        out.append(finding(
            "toxiczone_moving_stale", "warning",
            "A moving zone's derived values do not match its start and end",
            "; ".join(stale),
            "The mod does not compute these from the two points: it uses the "
            "numbers in the file, so the zone travels a different line, or resets "
            "somewhere else, than the start and end describe. The mod's own default "
            "zone ships like this: it computes YtoOrigin before Coeff, so it stores "
            "StartY. Zedstead counts Norme as the east-west distance, because the zone "
            "resets after moving that far east or west; the straight-line length "
            "the mod's explanation file describes runs a sloped path past its end.",
            "Use Fix the paths on the ToxicZone card above the ToxicZone editors "
            "(Configs, ToxicZone). Zedstead also computes Coeff, YtoOrigin and Norme "
            "from the two points whenever the zone is saved or an end is dragged "
            "on the map.",
        ))
    if backwards:
        out.append(finding(
            "toxiczone_moving_backwards", "error",
            "A moving zone moves away from its end point",
            ", ".join(backwards),
            "The zone moves east on a positive Speed and west on a negative one. "
            "Here the sign points away from the end, so the zone leaves its path "
            "until the reset timer puts it back.",
            "Use Fix the paths on the ToxicZone card above the ToxicZone editors: "
            "it sets Speed's sign from the direction, as every save and map drag "
            "does.",
        ))
    if still:
        out.append(finding(
            "toxiczone_moving_still", "warning",
            "A moving zone has a speed of 0",
            ", ".join(still),
            "It never moves, and the reset timer divides by the speed.",
            "Give it a speed in metres per minute.",
        ))

    outside, size = points_off_map(
        [(z.label, [p for p in (z.start, z.end) if None not in p]) for z in zones], terrain
    )
    if outside:
        out.append(finding(
            "toxiczone_moving_off_map", "warning",
            "A moving zone's path leaves the map",
            ", ".join(outside),
            f"The installed map is {display_name(terrain)}, {size} m across, and a "
            "start or end point is beyond that edge.",
            f"Move the points onto the map in {MOVING}. X is east, Y is north.",
        ))

    if terrain and terrain != "chernarusplus":
        left = [
            z.label for z in zones
            if z.name in MOVING_DEFAULTS and z.points_known
            and math.hypot(z.start[0] - MOVING_DEFAULTS[z.name][0], z.start[1] - MOVING_DEFAULTS[z.name][1]) <= 1.0
        ]
        if left:
            out.append(finding(
                "toxiczone_moving_chernarus_default", "warning",
                "The moving zone is still ToxicZone's Chernarus default",
                ", ".join(left),
                f"The mod writes a moving zone from Zelenogorsk towards Novy Sobor on "
                f"its first boot, whatever the map. On {display_name(terrain)} that "
                "path means nothing.",
                "Move or replace it in the Moving Zones editor.",
            ))

    uids = doc.get("AdminUID")
    if isinstance(uids, list) and AUTHOR_UID in uids:
        only = [u for u in uids if u != AUTHOR_UID] == []
        out.append(finding(
            "toxiczone_admin_uid_author", "notice",
            "The moving zone's admin list holds ToxicZone's author" + (" only" if only else ""),
            f"AdminUID in {MOVING}",
            "Players on this list get the moving zone's position in chat every "
            "minute. The mod ships it with its author's Steam ID, so on a server "
            "that never edited it the author is the only one who gets it"
            + ("." if only else ", beside the IDs added here."),
            "Replace it with your own Steam64 ID in the Moving Zones settings "
            "(Configs, ToxicZone). Zedstead never changes it for you.",
        ))
    return out


# ── Static zones: crates ─────────────────────────────────────────────────


def _crate_findings(static: dict, zones: list[Zone], loot: dict | None) -> list[dict]:
    items = static.get("StaticToxicAreaLocation")
    lists = (loot or {}).get("ListLoots")
    loot_names = {
        e.get("Name") for e in (lists if isinstance(lists, list) else [])
        if isinstance(e, dict) and isinstance(e.get("Name"), str)
    }
    stray: list[str] = []
    no_loot: list[str] = []
    for zone, entry in zip(zones, [e for e in (items or []) if isinstance(e, dict)]):
        if json_number(entry.get("IsLootActive"), 1.0) == 0.0:
            continue
        crates = entry.get("ToxicCratePosition")
        for j, crate in enumerate(crates if isinstance(crates, list) else []):
            if not isinstance(crate, dict):
                continue
            name = crate.get("Name") if isinstance(crate.get("Name"), str) else ""
            if loot_names and name not in loot_names:
                no_loot.append(f"{zone.name}: crate {j + 1} ({name or 'no name'})")
            pos = crate.get("POS")
            if not isinstance(pos, list) or len(pos) != 3:
                continue
            x, z = json_number(pos[0]), json_number(pos[2])
            if x is not None and z is not None and zone.contains(x, z) is False:
                stray.append(f"{zone.name}: crate {j + 1} at {x:.0f}, {z:.0f}")

    def _cap(items: list[str]) -> str:
        return "; ".join(items[:10]) + (f" (+{len(items) - 10} more)" if len(items) > 10 else "")

    out = []
    if stray:
        out.append(finding(
            "toxiczone_crate_outside_zone", "warning",
            "A loot crate sits outside its toxic zone",
            _cap(stray),
            "Each zone carries its own crate positions, and every default one is "
            "inside the zone. One outside can be looted without a gas mask, which "
            "is usually not the point.",
            f"Move the crate's POS inside the zone in {STATIC}, or grow the zone. "
            "POS is x, height, z.",
        ))
    if no_loot:
        out.append(finding(
            "toxiczone_crate_loot_unknown", "warning",
            "A loot crate names no loot list",
            _cap(no_loot),
            f"A crate's Name is the loot list in {LOOT} it is filled from. One "
            "naming no list has nothing to spawn.",
            f"Set the crate's Name to one of the Name values under ListLoots in {LOOT}.",
        ))
    return out


# ── Load order ───────────────────────────────────────────────────────────


def _load_order_findings(config, name: str) -> list[dict]:
    from app.modmgr.mods import get_resolved_mod_order

    picker = installed_mod_name(config, *SPAWN_SELECTION)
    if picker is None:
        return []
    order = get_resolved_mod_order(config)
    if name not in order or picker not in order or order.index(name) < order.index(picker):
        return []
    return [finding(
        "toxiczone_after_spawn_selection", "error",
        "ToxicZone loads after Expansion's spawn selection",
        f"{picker} is at position {order.index(picker) + 1}, {name} at {order.index(name) + 1}",
        "ToxicZone replaces how a character is created and does not hand over to "
        "the mods before it, so Expansion's spawn picker never shows: new "
        "characters spawn at random. Measured by a join in 2026 Q3; with "
        "ToxicZone first, the picker works.",
        f"Move {name} above the Expansion mods on the Mods page's Load Order tab, "
        "then restart the server.",
    )]


# ── Report ───────────────────────────────────────────────────────────────


def build_report(config) -> dict | None:
    """Everything zsm can tell about the ToxicZone install, or None when the
    mod is not installed (or is disabled)."""
    name = installed_name(config)
    if name is None:
        return None
    folder = config_dir(config)
    terrain = current_terrain(config)
    findings: list[dict] = list(_load_order_findings(config, name))
    docs: dict[str, dict | None] = {}
    if not folder.is_dir():
        findings.append(folder_finding(TOXICZONE, config, folder, len(FILES)))
    else:
        for fname in FILES:
            path = folder / fname
            doc, err = read_json(path)
            findings.extend(file_findings(TOXICZONE, config, path, doc, err))
            if doc is not None:
                findings.extend(decimal_findings(TOXICZONE, path, doc))
            docs[fname] = doc

    static, moving = docs.get(STATIC), docs.get(MOVING)
    zones = read_zones(static, TOXICZONE) if static else []
    if static:
        findings.extend(switched_off(
            static, "IsToxicZoneActive", "toxiczone_static_off",
            "Static toxic zones are switched off",
            "No static zone makes anyone sick while the switch is off.", STATIC,
        ))
        findings.extend(zone_findings(TOXICZONE, zones, terrain))
        findings.extend(_crate_findings(static, zones, docs.get(LOOT)))
    if moving:
        findings.extend(switched_off(
            moving, "IsMovingZoneActive", "toxiczone_moving_off",
            "Moving toxic zones are switched off",
            "No moving zone travels or makes anyone sick while the switch is off.",
            MOVING,
        ))
        findings.extend(_moving_findings(moving, terrain))

    return {
        "mod": name,
        "folder": display_path(config, folder),
        "terrain": terrain,
        "files": {f: (folder / f).is_file() for f in FILES},
        "zones": [
            {
                "name": z.name, "shape": "square" if z.square else "circle",
                "x": z.x, "y": z.y, "radius": z.radius, "corners": list(z.corners),
            }
            for z in zones
        ],
        "moving": [
            {"name": z.name, "start": list(z.start), "end": list(z.end), "radius": z.radius}
            for z in (read_moving(moving) if moving else [])
        ],
        "findings": findings,
    }


def doctor_row(config) -> tuple[str, bool, str] | None:
    """The Doctor's one-line verdict, or None when the mod is not installed."""
    report = build_report(config)
    if report is None:
        return None
    parts = []
    if report["zones"]:
        n = len(report["zones"])
        parts.append(f"{n} zone{'' if n == 1 else 's'}")
    if report["moving"]:
        n = len(report["moving"])
        parts.append(f"{n} moving")
    return doctor_verdict(LABEL, report["findings"], parts)
