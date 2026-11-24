"""Quick tweaks — focused single-category presets (zombie count, etc.)."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.presets.presets import STOCK_ITEMS_ONLY
from app.presets.field_baselines import (
    Compute,
    entry_records as _entry_records,
    file_records,
    int_value,
    load_baselines,
    prune_only,
    reset_field,
    save_baselines,
    scale_field,
    write_elements,
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TweakTier:
    """One selectable tier within a quick tweak."""
    name: str
    multiplier: float
    description: str = ""


@dataclass(frozen=True)
class ItemTarget:
    """Which db/types.xml entries a stack scales.

    An entry matches when its category is in ``categories`` or its name is in
    ``names``, and its name is not in ``exclude_names``. An entry without a
    category never matches on category.
    """
    categories: frozenset[str] = frozenset()
    names: frozenset[str] = frozenset()
    exclude_names: frozenset[str] = frozenset()

    def matches(self, name: str, category: str) -> bool:
        if name in self.exclude_names:
            return False
        return bool(category and category in self.categories) or name in self.names


@dataclass(frozen=True)
class EventTarget:
    """Which db/events.xml vehicle events a stack scales.

    The ``Vehicle`` prefix is required: Sakhal's StaticBoatFishing and
    StaticBoatMilitary contain "boat" but spawn static wrecks, not boats.
    A non-empty ``names`` narrows the stack to those events only, and
    ``exclude_names`` leaves those events out.
    """
    boats: bool
    names: frozenset[str] = frozenset()
    exclude_names: frozenset[str] = frozenset()

    def matches(self, name: str) -> bool:
        if (self.names and name not in self.names) or name in self.exclude_names:
            return False
        return name.startswith("Vehicle") and ("boat" in name.lower()) == self.boats


@dataclass(frozen=True)
class VariantSpec:
    """What one stack scales. An item is in scope when any of ``items`` matches."""
    items: tuple[ItemTarget, ...] = ()
    events: EventTarget | None = None

    @property
    def has_events(self) -> bool:
        return self.events is not None


@dataclass
class TweakVariant:
    """One selectable stack within a quick tweak (e.g. Gear → Medical)."""
    key: str
    name: str
    description: str = ""
    spec: VariantSpec | None = None
    # The db/events.xml event the card's "Edit spawns on map" link opens in the
    # Map Editor (?event=). The web route clears it when the mission lacks it.
    map_event: str = ""


@dataclass
class QuickTweak:
    """A single-category tweak with named tiers."""
    name: str
    description: str
    tiers: list[TweakTier]
    apply_fn: str  # name of the apply function in this module
    variants: list[TweakVariant] = field(default_factory=list)
    zero_warning: str = ""  # a tier of 0 leaves what already spawned in place
    spec: VariantSpec | None = None  # for a stock-file tweak without variants


# ---------------------------------------------------------------------------
# Zombie count tiers (reused for animal categories)
# ---------------------------------------------------------------------------

ZOMBIE_COUNT_TIERS: list[TweakTier] = [
    TweakTier("None",    0.0,   "No zombies at all"),
    TweakTier("Minimal", 0.25,  "Very few zombies — relaxed exploration"),
    TweakTier("Low",     0.5,   "Reduced zombie presence"),
    TweakTier("Normal",  1.0,   "Vanilla zombie density"),
    TweakTier("High",    3.0,   "Significantly more zombies"),
    TweakTier("Brutal",  8.0,   "Overwhelming zombie hordes"),
    TweakTier("Insane",  10.0,  "Maximum chaos"),
]

ANIMAL_COUNT_TIERS: list[TweakTier] = [
    TweakTier("None",    0.0,   "No animals at all"),
    TweakTier("Minimal", 0.25,  "Very few animals"),
    TweakTier("Low",     0.5,   "Reduced animal presence"),
    TweakTier("Normal",  1.0,   "Vanilla animal density"),
    TweakTier("High",    3.0,   "Significantly more animals"),
    TweakTier("Brutal",  8.0,   "Overwhelming animal numbers"),
    TweakTier("Insane",  10.0,  "Maximum animal spawns"),
]

# Food and gear: loot items in db/types.xml.
ITEM_AMOUNT_TIERS: list[TweakTier] = [
    TweakTier("None",     0.0,  "Nothing from this group spawns"),
    TweakTier("Scarce",   0.25, "A quarter of the usual amount"),
    TweakTier("Low",      0.5,  "Half the usual amount"),
    TweakTier("Normal",   1.0,  "The amount before any quick tweak"),
    TweakTier("High",     2.0,  "Twice the usual amount"),
    TweakTier("Abundant", 3.0,  "Three times the usual amount"),
]

# Vehicles and boats: spawn events in db/events.xml.
VEHICLE_COUNT_TIERS: list[TweakTier] = [
    TweakTier("None",   0.0, "No new spawns"),
    TweakTier("Few",    0.5, "Half the usual number"),
    TweakTier("Normal", 1.0, "The number before any quick tweak"),
    TweakTier("More",   2.0, "Twice the usual number"),
    TweakTier("Lots",   3.0, "Three times the usual number"),
]


# ---------------------------------------------------------------------------
# Animal territory file groupings
# ---------------------------------------------------------------------------

PREDATOR_FILES = [
    "wolf_territories.xml", "bear_territories.xml", "BearBlack_territories.xml",
    "brownbear_territories.xml", "polarbear_territories.xml", "arcticwolf_territories.xml",
    "lion_territories.xml", "fox_territories.xml", "predator_territories.xml",
]

WILD_ANIMAL_FILES = [
    "red_deer_territories.xml", "roe_deer_territories.xml", "reindeer_territories.xml",
    "hare_territories.xml", "moose_territories.xml",
    "wild_boar_territories.xml", "wildboar_territories.xml",
]

FARM_ANIMAL_FILES = [
    "cattle_territories.xml", "pig_territories.xml", "hen_territories.xml",
    "sheep_goat_territories.xml", "domestic_animals_territories.xml",
    "domestic_territories.xml",
]

_ANIMAL_TWEAK_TRACKING = ".animal_tweaks.json"


# ---------------------------------------------------------------------------
# Stock item and event groupings (db/types.xml, db/events.xml)
# ---------------------------------------------------------------------------

# Measured against Bohemia's stock chernarusplus/enoch/sakhal types.xml and
# events.xml, 2026 Q3 (plans/35).
CAR_ENGINE_PARTS = frozenset({
    "CarBattery", "TruckBattery", "CarRadiator", "SparkPlug", "GlowPlug",
    "HeadlightH7_Box", "TireRepairKit",
})

MEDICAL_ITEMS = frozenset({
    "BandageDressing", "Morphine", "Epinephrine", "SalineBag", "BloodBagEmpty",
    "BloodTestKit", "StartKitIV", "TetracyclineAntibiotics", "PainkillerTablets",
    "CharcoalTablets", "VitaminBottle", "IodineTincture", "DisinfectantAlcohol",
    "DisinfectantSpray", "PurificationTablets", "AntiChemInjector", "ChelatingTablets",
    "FirstAidKit",
})

DRINK_ITEMS = frozenset({"WaterBottle", "GlassBottle"})

UNCATEGORISED_GEAR = frozenset({
    "PlateCarrierVest_Black", "PlateCarrierVest_Green", "Mich2001Helmet", "GhillieAtt_Winter",
})

# The exclusions keep every item on exactly one card: FirstAidKit is Medical,
# Livonia's GlassBottle is Food & drink, engine parts are Vehicles.
_CAR_PARTS = ItemTarget(categories=frozenset({"lootdispatch"}), names=CAR_ENGINE_PARTS)
# DayZ 1.30 ships this event, its seven bikes and their spawn points on every
# stock map, Badlands' DLC not needed (measured 2026-11-22, plans/95). Only the
# motorbike variant scales it, so a car apply never undoes a bike apply.
MOTORBIKE_EVENT = "VehicleMotorbike"
_CARS = EventTarget(boats=False, exclude_names=frozenset({MOTORBIKE_EVENT}))
_MOTORBIKES = EventTarget(boats=False, names=frozenset({MOTORBIKE_EVENT}))
_CLOTHING = ItemTarget(categories=frozenset({"clothes"}), names=UNCATEGORISED_GEAR)
_CONTAINERS = ItemTarget(
    categories=frozenset({"containers"}), exclude_names=MEDICAL_ITEMS | DRINK_ITEMS,
)
_MEDICAL = ItemTarget(names=MEDICAL_ITEMS)
_TOOLS = ItemTarget(categories=frozenset({"tools"}), exclude_names=MEDICAL_ITEMS | CAR_ENGINE_PARTS)

_STOCK_ONLY = STOCK_ITEMS_ONLY

VEHICLE_ZERO_WARNING = (
    "None stops new spawns only. Cars already on the map stay until they are destroyed, "
    "cleaned up by the server, or persistence is wiped."
)
BOAT_ZERO_WARNING = (
    "None stops new spawns only. Boats already on the map stay until they are destroyed, "
    "cleaned up by the server, or persistence is wiped."
)

_TWEAK_BASELINES = ".zsm-tweaks.json"
_GLOBALS_KEY = "db/globals.xml"
# The engine's value for a cap globals.xml leaves out (central-economy-configuration.wiki).
_CAP_DEFAULTS = {"ZombieMaxCount": 1000, "AnimalMaxCount": 200}
_TYPES_KEY = "db/types.xml"
_EVENTS_KEY = "db/events.xml"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

QUICK_TWEAKS: dict[str, QuickTweak] = {
    "zombie_count": QuickTweak(
        name="Zombie count",
        description=(
            "Scale zombie spawns across all territories.\n"
            "Modifies ZombieMaxCount in globals.xml and smin/smax/dmin/dmax in zombie_territories.xml."
        ),
        tiers=ZOMBIE_COUNT_TIERS,
        apply_fn="apply_zombie_count",
    ),
    "predator_count": QuickTweak(
        name="Predator count",
        description=(
            "Scale predator spawns (wolves, bears, etc.).\n"
            "Modifies territory files and AnimalMaxCount in globals.xml."
        ),
        tiers=ANIMAL_COUNT_TIERS,
        apply_fn="apply_predator_count",
    ),
    "wild_animal_count": QuickTweak(
        name="Wild animal count",
        description=(
            "Scale wild animal spawns (deer, hare, boar, etc.).\n"
            "Modifies territory files and AnimalMaxCount in globals.xml."
        ),
        tiers=ANIMAL_COUNT_TIERS,
        apply_fn="apply_wild_animal_count",
    ),
    "farm_animal_count": QuickTweak(
        name="Farm animal count",
        description=(
            "Scale farm animal spawns (cattle, pigs, hens, etc.).\n"
            "Modifies territory files and AnimalMaxCount in globals.xml."
        ),
        tiers=ANIMAL_COUNT_TIERS,
        apply_fn="apply_farm_animal_count",
    ),
    "vehicle_count": QuickTweak(
        name="Vehicles",
        description=(
            "Scale car spawns, motorbike spawns, car parts, or both.\n"
            "Modifies nominal/min/max and each child's min/max in db/events.xml, "
            "and nominal/min in db/types.xml.\n"
            f"{_STOCK_ONLY}\n{VEHICLE_ZERO_WARNING}"
        ),
        tiers=VEHICLE_COUNT_TIERS,
        apply_fn="apply_vehicle_count",
        variants=[
            TweakVariant(
                "cars", "Only cars", "Sedans, hatchbacks, offroads and trucks",
                VariantSpec(events=_CARS),
            ),
            TweakVariant(
                "cars_and_parts", "Cars and parts", "Car spawns plus wheels, doors, hoods, trunks and engine parts",
                VariantSpec(items=(_CAR_PARTS,), events=_CARS),
            ),
            TweakVariant(
                "parts", "Only car parts",
                "Wheels, doors, hoods, trunks, batteries, radiators, plugs, H7 bulbs and tire repair kits",
                VariantSpec(items=(_CAR_PARTS,)),
            ),
            TweakVariant(
                "motorbikes", "Only motorbikes",
                "Motorbike spawns (VehicleMotorbike, DayZ 1.30 and later). Their wheels count as car parts",
                VariantSpec(events=_MOTORBIKES),
                map_event=MOTORBIKE_EVENT,
            ),
        ],
        zero_warning=VEHICLE_ZERO_WARNING,
    ),
    "boat_count": QuickTweak(
        name="Boats",
        description=(
            "Scale boat spawns (VehicleBoat, VehicleMilitaryBoat).\n"
            "Modifies nominal/min/max and each child's min/max in db/events.xml.\n"
            f"{_STOCK_ONLY}\n{BOAT_ZERO_WARNING}"
        ),
        tiers=VEHICLE_COUNT_TIERS,
        apply_fn="apply_boat_count",
        zero_warning=BOAT_ZERO_WARNING,
        spec=VariantSpec(events=EventTarget(boats=True)),
    ),
    "food_amount": QuickTweak(
        name="Food & drink",
        description=(
            "Scale food, drinks and water bottles.\n"
            "Modifies nominal/min in db/types.xml.\n"
            f"{_STOCK_ONLY}"
        ),
        tiers=ITEM_AMOUNT_TIERS,
        apply_fn="apply_food_amount",
        spec=VariantSpec(items=(ItemTarget(categories=frozenset({"food"}), names=DRINK_ITEMS),)),
    ),
    "gear_amount": QuickTweak(
        name="Gear",
        description=(
            "Scale one group of loot.\n"
            "Modifies nominal/min in db/types.xml.\n"
            f"{_STOCK_ONLY}"
        ),
        tiers=ITEM_AMOUNT_TIERS,
        apply_fn="apply_gear_amount",
        variants=[
            TweakVariant(
                "clothing", "Clothing", "Clothing, helmets, vests, belts and holsters",
                VariantSpec(items=(_CLOTHING,)),
            ),
            TweakVariant(
                "containers", "Backpacks & containers", "Backpacks, pouches, tents and ammo boxes",
                VariantSpec(items=(_CONTAINERS,)),
            ),
            TweakVariant(
                "medical", "Medical", "Bandages, medicine, blood kits and first aid kits",
                VariantSpec(items=(_MEDICAL,)),
            ),
            TweakVariant(
                "tools", "Tools", "Melee, base building, lights, electronics, cooking and fishing",
                VariantSpec(items=(_TOOLS,)),
            ),
            TweakVariant(
                "weapons", "Weapons & ammo", "Guns, ammo, magazines, optics and attachments",
                VariantSpec(items=(ItemTarget(categories=frozenset({"weapons"})),)),
            ),
            TweakVariant(
                "explosives", "Explosives", "Grenades, mines, smoke and plastic explosives",
                VariantSpec(items=(ItemTarget(categories=frozenset({"explosives"})),)),
            ),
            TweakVariant(
                "all_gear", "All gear",
                "Clothing, containers, medical and tools — not weapons, explosives, food or car parts",
                VariantSpec(items=(_CLOTHING, _CONTAINERS, _MEDICAL, _TOOLS)),
            ),
        ],
    ),
}


# ---------------------------------------------------------------------------
# Apply functions
# ---------------------------------------------------------------------------

def apply_zombie_count(
    config: Any, mission_dir: Path, multiplier: float, variant: str | None = None,
) -> list[str]:
    """Apply zombie count multiplier to globals.xml and zombie_territories.xml.

    Returns list of modified file paths.
    """
    modified: list[str] = []

    # --- globals.xml: ZombieMaxCount ---
    modified.extend(_apply_globals_zombie_max(mission_dir, multiplier))

    # --- env/zombie_territories.xml ---
    modified.extend(_apply_zombie_territories(mission_dir, multiplier))

    return modified


def _cap_value(values: dict[str, str], name: str, default: int) -> int | None:
    """A globals.xml cap to scale: *default* when the var is missing, None
    when it is there but not a whole number. Such a cap is left alone, as
    ``int_value`` leaves a malformed types.xml value: ``int()`` raised on it
    and stopped the tweak, for the animal ones after the territory files had
    already been written."""
    if name not in values:
        return default
    return int_value(values[name])


def _apply_globals_zombie_max(mission_dir: Path, multiplier: float) -> list[str]:
    """Scale ZombieMaxCount in globals.xml from its field baseline.

    Only ZombieMaxCount changes; other globals are left untouched. A globals.xml
    without the var runs on the engine's default, 1000
    (central-economy-configuration.wiki, ZombieMaxCount).
    """
    def scale(records: dict, key: str, on_disk: int) -> int:
        return scale_field(records, key, on_disk, multiplier)

    return _write_globals_cap(mission_dir, "ZombieMaxCount", 1000, scale)


def _write_globals_cap(mission_dir: Path, name: str, default: int, compute: Compute) -> list[str]:
    """Rewrite one globals.xml cap through the tweak sidecar's baseline.

    The base is never read from globals.xml.backup, which holds the file as it
    was at the first tweak: a hand edit made since then was thrown away by the
    next tweak. A value on disk that is not what the last tweak wrote becomes
    the new base (`scale_field`). A cap that is not a whole number is left alone.
    """
    from app.xml.globals_xml import add_var, get_globals_path, parse_globals_file, save_globals_file, set_var_value

    gpath = get_globals_path(mission_dir)
    if not gpath:
        return []
    gf = parse_globals_file(gpath)
    entry = {e.name: e for e in gf.entries}.get(name)
    on_disk = _cap_value({name: entry.value} if entry else {}, name, default)
    if on_disk is None:
        return []
    fields = load_baselines(mission_dir, _TWEAK_BASELINES)
    records = file_records(fields, _GLOBALS_KEY)
    new = compute(_entry_records(records, name), "value", on_disk)
    fields[_GLOBALS_KEY] = records
    if new is None or (entry is not None and new == on_disk):
        save_baselines(mission_dir, fields, _TWEAK_BASELINES)
        return []
    if entry is not None:
        set_var_value(entry, str(new))
    else:
        add_var(gf, name, 0, str(new))
    save_globals_file(gf)
    save_baselines(mission_dir, fields, _TWEAK_BASELINES)
    return [str(gpath)]


def _apply_zombie_territories(mission_dir: Path, multiplier: float) -> list[str]:
    """Scale all zones in zombie_territories.xml from their field baselines."""
    return _apply_territory_files(mission_dir, ["zombie_territories.xml"], multiplier)


_ZONE_FIELDS = ("smin", "smax", "dmin", "dmax")


def _write_territories(mission_dir: Path, filenames: list[str], compute: Compute) -> list[str]:
    """Rewrite every zone count of the env/ territory files through the tweak
    sidecar's baselines, keyed by territory and zone position.

    The base is never read from the file's .backup, which holds it as it was
    at the first tweak: a zone edited by hand since then was put back by the
    next tweak. A file whose counts do not change is not written.
    """
    from app.xml.zombie_territories_xml import get_territories_path, parse_territories, save_territories

    fields = load_baselines(mission_dir, _TWEAK_BASELINES)
    modified: list[str] = []
    for filename in filenames:
        path = get_territories_path(mission_dir, filename)
        if not path:
            continue
        file_key = f"env/{filename}"
        tf = parse_territories(path)
        records = file_records(fields, file_key)
        changed = False
        for ti, territory in enumerate(tf.territories):
            for zi, zone in enumerate(territory.zones):
                zone_records = _entry_records(records, f"{ti}.{zi}")
                for attr in _ZONE_FIELDS:
                    current = int_value(zone.element.get(attr))
                    if current is None:
                        continue
                    new = compute(zone_records, attr, current)
                    if new is None or new == current:
                        continue
                    zone.element.set(attr, str(new))
                    setattr(zone, attr, new)
                    changed = True
        fields[file_key] = records
        if changed:
            save_territories(tf)
            modified.append(str(path))
    save_baselines(mission_dir, fields, _TWEAK_BASELINES)
    return modified


def _apply_territory_files(mission_dir: Path, filenames: list[str], multiplier: float) -> list[str]:
    def scale(records: dict, key: str, on_disk: int) -> int:
        return scale_field(records, key, on_disk, multiplier)

    return _write_territories(mission_dir, filenames, scale)


# ---------------------------------------------------------------------------
# Animal territory apply helpers
# ---------------------------------------------------------------------------

def _apply_animal_territories(mission_dir: Path, filenames: list[str], multiplier: float) -> list[str]:
    """Apply multiplier to a set of animal territory files, from their field
    baselines. Returns list of modified file paths."""
    return _apply_territory_files(mission_dir, filenames, multiplier)


def _animal_tracking_path(mission_dir: Path) -> Path:
    return mission_dir / "env" / _ANIMAL_TWEAK_TRACKING


def _load_animal_tracking(mission_dir: Path) -> dict[str, float]:
    """Per-category multipliers of the animal cards; empty when none was applied.

    An absent or unreadable file means "no animal card has run here", which is
    what a reset checks before it writes anything.
    """
    path = _animal_tracking_path(mission_dir)
    if not path.is_file():
        return {}
    try:
        tracking = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return tracking if isinstance(tracking, dict) else {}


def _apply_animal_max_count(
    mission_dir: Path, tweak_key: str, multiplier: float | None,
) -> list[str]:
    """Scale AnimalMaxCount in globals.xml based on the highest multiplier
    among all animal tweak categories.

    Tracks per-category multipliers in env/.animal_tweaks.json.
    ``multiplier=None`` drops this category from the tracking file — a reset,
    which lets the remaining categories (or the baseline, once none is left)
    set the cap.
    """
    tracking_path = _animal_tracking_path(mission_dir)
    tracking: dict[str, float] = _load_animal_tracking(mission_dir)
    if multiplier is None and tweak_key not in tracking:
        return []  # nothing ever applied: no baseline to go back to

    if multiplier is None:
        tracking.pop(tweak_key, None)
    else:
        tracking[tweak_key] = multiplier
    if tracking:
        tracking_path.write_text(json.dumps(tracking, indent=2), encoding="utf-8")
    else:
        tracking_path.unlink(missing_ok=True)

    # Treat 0 as 0 — if all are "None", AnimalMaxCount should be 0
    # But if any category has animals, use the highest multiplier.
    # No categories left at all (every one reset) means the baseline.
    if not tracking:
        return _write_globals_cap(mission_dir, "AnimalMaxCount", 200, reset_field)
    if any(v > 0 for v in tracking.values()):
        effective = max(v for v in tracking.values() if v > 0)
    else:
        effective = 0.0

    def scale(records: dict, key: str, on_disk: int) -> int:
        return scale_field(records, key, on_disk, effective)

    return _write_globals_cap(mission_dir, "AnimalMaxCount", 200, scale)


def apply_predator_count(
    config: Any, mission_dir: Path, multiplier: float, variant: str | None = None,
) -> list[str]:
    """Apply predator count multiplier."""
    modified: list[str] = []
    modified.extend(_apply_animal_territories(mission_dir, PREDATOR_FILES, multiplier))
    modified.extend(_apply_animal_max_count(mission_dir, "predator_count", multiplier))
    return modified


def apply_wild_animal_count(
    config: Any, mission_dir: Path, multiplier: float, variant: str | None = None,
) -> list[str]:
    """Apply wild animal count multiplier."""
    modified: list[str] = []
    modified.extend(_apply_animal_territories(mission_dir, WILD_ANIMAL_FILES, multiplier))
    modified.extend(_apply_animal_max_count(mission_dir, "wild_animal_count", multiplier))
    return modified


def apply_farm_animal_count(
    config: Any, mission_dir: Path, multiplier: float, variant: str | None = None,
) -> list[str]:
    """Apply farm animal count multiplier."""
    modified: list[str] = []
    modified.extend(_apply_animal_territories(mission_dir, FARM_ANIMAL_FILES, multiplier))
    modified.extend(_apply_animal_max_count(mission_dir, "farm_animal_count", multiplier))
    return modified


# ---------------------------------------------------------------------------
# Stock-file apply helpers (db/types.xml, db/events.xml)
# ---------------------------------------------------------------------------
#
# The baseline lives in <mission>/.zsm-tweaks.json, never in a .backup: a
# .backup holds the file as it was first backed up, so a baseline read from
# there ignores every edit made since. Records are kept per entry field, not per tweak, so
# overlapping stacks (Gear → Medical, then All gear) share one baseline and the
# last apply wins. The machinery is shared with the preset engine, which keeps
# its own sidecar (app/presets/field_baselines.py).

def _write_item_targets(
    mission_dir: Path, fields: dict, targets: tuple[ItemTarget, ...], compute: Compute,
) -> list[str]:
    """Rewrite nominal/min of every db/types.xml entry any of *targets* matches.

    Only db/types.xml — never scan_types_files, which includes custom/*.xml that
    every merge wipes and regenerates. Mod items are out of reach by design.
    """
    from app.xml.types_xml import parse_types_file, save_types_files, set_numeric_field

    path = mission_dir / "db" / "types.xml"
    if not targets or not path.is_file():
        return []

    tf = parse_types_file(path, preserve_comments=True)
    records = file_records(fields, _TYPES_KEY)
    changed = False
    for entry in tf.entries:
        if not any(t.matches(entry.name, entry.category) for t in targets):
            continue
        entry_records = _entry_records(records, entry.name)
        if write_elements(entry, entry_records, ("nominal", "min"), set_numeric_field, compute):
            changed = True
    if not changed:
        return prune_only(mission_dir, fields, _TYPES_KEY, records, _TWEAK_BASELINES)

    save_types_files([tf])
    fields[_TYPES_KEY] = records
    save_baselines(mission_dir, fields, _TWEAK_BASELINES)
    return [str(path)]


def _write_event_target(
    mission_dir: Path, fields: dict, target: EventTarget, compute: Compute,
) -> list[str]:
    """Rewrite nominal/min/max and each child's min/max of the matching db/events.xml events.

    ``limit=mixed`` combines event and child counts in a way the wiki doesn't
    document, so both are scaled. active/lifetime/restock are never touched.
    """
    from app.xml.events_xml import get_events_path, parse_events_file, save_events_file, set_event_numeric

    path = get_events_path(mission_dir)
    if path is None:
        return []

    ef = parse_events_file(path, preserve_comments=True)
    records = file_records(fields, _EVENTS_KEY)
    changed = False
    for entry in ef.entries:
        if not target.matches(entry.name):
            continue
        entry_records = _entry_records(records, entry.name)
        if write_elements(entry, entry_records, ("nominal", "min", "max"), set_event_numeric, compute):
            changed = True
        for child in entry.children:
            for attr in ("min", "max"):
                current = int_value(child.element.get(attr))
                if current is None:
                    continue
                new = compute(entry_records, f"child:{child.type}:{attr}", current)
                if new is None:
                    continue
                child.element.set(attr, str(new))
                setattr(child, attr, new)
                changed = True
    if not changed:
        return prune_only(mission_dir, fields, _EVENTS_KEY, records, _TWEAK_BASELINES)

    save_events_file(ef)
    fields[_EVENTS_KEY] = records
    save_baselines(mission_dir, fields, _TWEAK_BASELINES)
    return [str(path)]


def _write_spec(mission_dir: Path, spec: VariantSpec, compute: Compute) -> list[str]:
    fields = load_baselines(mission_dir, _TWEAK_BASELINES)
    modified = _write_item_targets(mission_dir, fields, spec.items, compute)
    if spec.events is not None:
        modified.extend(_write_event_target(mission_dir, fields, spec.events, compute))
    return modified


def _apply_spec(mission_dir: Path, spec: VariantSpec, multiplier: float) -> list[str]:
    def scale(records: dict, key: str, on_disk: int) -> int:
        return scale_field(records, key, on_disk, multiplier)

    return _write_spec(mission_dir, spec, scale)


def _reset_spec(mission_dir: Path, spec: VariantSpec) -> list[str]:
    """Put a stack's fields back to their recorded baselines and forget them."""
    return _write_spec(mission_dir, spec, reset_field)


def apply_vehicle_count(
    config: Any, mission_dir: Path, multiplier: float, variant: str | None = None,
) -> list[str]:
    """Scale car spawn events, car parts, or both."""
    return _apply_spec(mission_dir, variant_spec("vehicle_count", variant), multiplier)


def apply_boat_count(
    config: Any, mission_dir: Path, multiplier: float, variant: str | None = None,
) -> list[str]:
    """Scale boat spawn events."""
    return _apply_spec(mission_dir, variant_spec("boat_count", variant), multiplier)


def apply_food_amount(
    config: Any, mission_dir: Path, multiplier: float, variant: str | None = None,
) -> list[str]:
    """Scale food and drink loot."""
    return _apply_spec(mission_dir, variant_spec("food_amount", variant), multiplier)


def apply_gear_amount(
    config: Any, mission_dir: Path, multiplier: float, variant: str | None = None,
) -> list[str]:
    """Scale one group of gear loot."""
    return _apply_spec(mission_dir, variant_spec("gear_amount", variant), multiplier)


# ---------------------------------------------------------------------------
# Reset functions — one card back to its pre-tweak values
# ---------------------------------------------------------------------------
#
# Every card keeps its baseline in the sidecar, and a reset puts it back and
# forgets it (`reset_field`). A value changed since the last tweak is the
# admin's and stays. The animal cards also drop their category from the
# shared AnimalMaxCount tracking file.

def _reset_globals_var(mission_dir: Path, var_name: str) -> list[str]:
    """Put one globals.xml var back to its baseline, and nothing else.

    No baseline, or a value changed since the tweak, means no write at all:
    globals.xml carries settings the admin owns.
    """
    return _write_globals_cap(mission_dir, var_name, _CAP_DEFAULTS.get(var_name, 0), reset_field)


def _reset_territories(mission_dir: Path, filenames: list[str]) -> list[str]:
    """Put each zone count back to its baseline. A file with nothing to undo
    is not written."""
    return _write_territories(mission_dir, filenames, reset_field)


def reset_zombie_count(
    config: Any, mission_dir: Path, variant: str | None = None,
) -> list[str]:
    """Put ZombieMaxCount and the zombie zones back to their backup values."""
    modified = _reset_globals_var(mission_dir, "ZombieMaxCount")
    modified.extend(_reset_territories(mission_dir, ["zombie_territories.xml"]))
    return modified


def _reset_animal_count(mission_dir: Path, filenames: list[str], tweak_key: str) -> list[str]:
    if tweak_key not in _load_animal_tracking(mission_dir):
        return []  # this card was never applied to this mission
    modified = _reset_territories(mission_dir, filenames)
    modified.extend(_apply_animal_max_count(mission_dir, tweak_key, None))
    return modified


def reset_predator_count(
    config: Any, mission_dir: Path, variant: str | None = None,
) -> list[str]:
    """Put predator territories and the animal cap back to the backup values."""
    return _reset_animal_count(mission_dir, PREDATOR_FILES, "predator_count")


def reset_wild_animal_count(
    config: Any, mission_dir: Path, variant: str | None = None,
) -> list[str]:
    """Put wild animal territories and the animal cap back to the backup values."""
    return _reset_animal_count(mission_dir, WILD_ANIMAL_FILES, "wild_animal_count")


def reset_farm_animal_count(
    config: Any, mission_dir: Path, variant: str | None = None,
) -> list[str]:
    """Put farm animal territories and the animal cap back to the backup values."""
    return _reset_animal_count(mission_dir, FARM_ANIMAL_FILES, "farm_animal_count")


def reset_vehicle_count(
    config: Any, mission_dir: Path, variant: str | None = None,
) -> list[str]:
    """Put the selected vehicle stack back to its baseline."""
    return _reset_spec(mission_dir, variant_spec("vehicle_count", variant))


def reset_boat_count(
    config: Any, mission_dir: Path, variant: str | None = None,
) -> list[str]:
    """Put the boat events back to their baseline."""
    return _reset_spec(mission_dir, variant_spec("boat_count", variant))


def reset_food_amount(
    config: Any, mission_dir: Path, variant: str | None = None,
) -> list[str]:
    """Put food and drink loot back to its baseline."""
    return _reset_spec(mission_dir, variant_spec("food_amount", variant))


def reset_gear_amount(
    config: Any, mission_dir: Path, variant: str | None = None,
) -> list[str]:
    """Put the selected gear stack back to its baseline."""
    return _reset_spec(mission_dir, variant_spec("gear_amount", variant))


# ---------------------------------------------------------------------------
# Dispatch helper
# ---------------------------------------------------------------------------

_APPLY_FUNCTIONS = {
    "apply_zombie_count": apply_zombie_count,
    "apply_predator_count": apply_predator_count,
    "apply_wild_animal_count": apply_wild_animal_count,
    "apply_farm_animal_count": apply_farm_animal_count,
    "apply_vehicle_count": apply_vehicle_count,
    "apply_boat_count": apply_boat_count,
    "apply_food_amount": apply_food_amount,
    "apply_gear_amount": apply_gear_amount,
}

# Keyed by tweak id, not by apply_fn name: every tweak has exactly one reset,
# so there is nothing for the registry to point at.
_RESET_FUNCTIONS = {
    "zombie_count": reset_zombie_count,
    "predator_count": reset_predator_count,
    "wild_animal_count": reset_wild_animal_count,
    "farm_animal_count": reset_farm_animal_count,
    "vehicle_count": reset_vehicle_count,
    "boat_count": reset_boat_count,
    "food_amount": reset_food_amount,
    "gear_amount": reset_gear_amount,
}


def resolve_variant(tweak_key: str, variant: str | None) -> str | None:
    """The variant an apply uses: None picks a tweak's first variant.

    Raises KeyError for an unknown tweak, or a variant the tweak doesn't have
    (any variant at all, on a tweak without variants).
    """
    tweak = QUICK_TWEAKS[tweak_key]
    if variant is None:
        return tweak.variants[0].key if tweak.variants else None
    if variant not in {v.key for v in tweak.variants}:
        raise KeyError(f"Unknown variant {variant!r} for {tweak.name}")
    return variant


def variant_spec(tweak_key: str, variant: str | None = None) -> VariantSpec | None:
    """What an apply of *tweak_key* scales; None for the territory/globals tweaks."""
    tweak = QUICK_TWEAKS[tweak_key]
    key = resolve_variant(tweak_key, variant)
    return next((v.spec for v in tweak.variants if v.key == key), tweak.spec)


def apply_tweak(
    tweak_key: str, config: Any, mission_dir: Path, multiplier: float, variant: str | None = None,
) -> list[str]:
    """Look up and call the apply function for a given tweak key."""
    tweak = QUICK_TWEAKS[tweak_key]
    fn = _APPLY_FUNCTIONS[tweak.apply_fn]
    return fn(config, mission_dir, multiplier, resolve_variant(tweak_key, variant))


def _spec_records(mission_dir: Path, spec: VariantSpec) -> list[tuple[int, int, int]]:
    """``(base, written, on disk)`` of every recorded field in a stack. It writes nothing."""
    from app.xml.events_xml import get_events_path, parse_events_file
    from app.xml.types_xml import parse_types_file

    fields = load_baselines(mission_dir, _TWEAK_BASELINES)
    found: list[tuple[int, int, int]] = []

    def collect(records: dict, key: str, on_disk: int) -> None:
        rec = records.get(key)
        if isinstance(rec, dict) and isinstance(rec.get("base"), int) and isinstance(rec.get("written"), int):
            found.append((rec["base"], rec["written"], on_disk))

    def records_of(file_key: str, name: str) -> dict:
        file_fields = fields.get(file_key)
        entry = file_fields.get(name) if isinstance(file_fields, dict) else None
        return entry if isinstance(entry, dict) else {}

    types_path = mission_dir / "db" / "types.xml"
    if spec.items and types_path.is_file():
        for entry in parse_types_file(types_path, preserve_comments=True).entries:
            if any(t.matches(entry.name, entry.category) for t in spec.items):
                write_elements(entry, records_of(_TYPES_KEY, entry.name), ("nominal", "min"), None, collect)
    events_path = get_events_path(mission_dir) if spec.events is not None else None
    if events_path is not None:
        for entry in parse_events_file(events_path, preserve_comments=True).entries:
            if not spec.events.matches(entry.name):
                continue
            records = records_of(_EVENTS_KEY, entry.name)
            write_elements(entry, records, ("nominal", "min", "max"), None, collect)
            for child in entry.children:
                for attr in ("min", "max"):
                    current = int_value(child.element.get(attr))
                    if current is not None:
                        collect(records, f"child:{child.type}:{attr}", current)
    return found


def _matching_tier(tweak: QuickTweak, fits: Callable[[float], bool]) -> float | None:
    """The tier that *fits*, 1x first when several do. None means Custom."""
    tiers = sorted((t.multiplier for t in tweak.tiers), key=lambda m: m != 1)
    return next((m for m in tiers if fits(m)), None)


def _zombie_multiplier(mission_dir: Path) -> float | None:
    """The zombie tier from ZombieMaxCount and its sidecar baseline: 1x with
    no record, Custom (None) when the cap is no longer what the tweak wrote."""
    from app.xml.globals_xml import get_globals_path, parse_globals_file

    gpath = get_globals_path(mission_dir)
    if gpath is None:
        return 1.0
    now = _cap_value({e.name: e.value for e in parse_globals_file(gpath).entries}, "ZombieMaxCount", 1000)
    if now is None:
        return None
    records = load_baselines(mission_dir, _TWEAK_BASELINES).get(_GLOBALS_KEY, {})
    rec = records.get("ZombieMaxCount", {}).get("value") if isinstance(records, dict) else None
    if not (isinstance(rec, dict) and isinstance(rec.get("base"), int)):
        return 1.0
    if rec.get("written") != now:
        return None
    base = rec["base"]
    return _matching_tier(QUICK_TWEAKS["zombie_count"], lambda m: max(0, math.floor(base * m + 0.5)) == now)


def current_multiplier(tweak_key: str, mission_dir: Path, variant: str | None = None) -> float | None:
    """The tier this tweak is on now, or None (Custom) when no tier matches the files.

    A field whose value is not what the last tweak wrote is the new base, as in
    ``scale_field``, so a stack with only such fields is at 1x. A stack with both
    kinds matches no tier. It reads the files and writes nothing.
    """
    tweak = QUICK_TWEAKS[tweak_key]
    spec = variant_spec(tweak_key, variant)
    if spec is None:
        if tweak_key == "zombie_count":
            return _zombie_multiplier(mission_dir)
        tracked = _load_animal_tracking(mission_dir).get(tweak_key, 1.0)
        return _matching_tier(tweak, lambda m: m == tracked)
    records = _spec_records(mission_dir, spec)
    live = [(base, written) for base, written, on_disk in records if written == on_disk]
    if not live:
        return 1.0
    if len(live) != len(records):
        return None
    return _matching_tier(
        tweak, lambda m: all(math.floor(base * m + 0.5) == written for base, written in live),
    )


def reset_tweak(
    tweak_key: str, config: Any, mission_dir: Path, variant: str | None = None,
) -> list[str]:
    """Put one tweak's values back to what they were before the first tweak.

    Returns the files actually rewritten — empty when there was nothing to undo.
    Raises KeyError for an unknown tweak or variant, like apply_tweak.
    """
    return _RESET_FUNCTIONS[tweak_key](config, mission_dir, resolve_variant(tweak_key, variant))
