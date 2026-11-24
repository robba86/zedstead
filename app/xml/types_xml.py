"""Types XML parsing, filtering, bulk editing, and backup/restore."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from app.xml.xml_utils import (
    backup_file,
    get_mission_dir,
    load_xml,
    restore_file,
    save_xml,
    subelement_in_order,
)


# ---------------------------------------------------------------------------
# Constants — known values for UI suggestions
# ---------------------------------------------------------------------------

# Bohemia's order of a <type>'s children (every stock types.xml): where a
# first one of a kind goes when the element had none.
TYPE_CHILD_ORDER = [
    "nominal", "lifetime", "restock", "min", "quantmin", "quantmax", "cost",
    "flags", "category", "tag", "usage", "value",
]

FLAG_NAMES = [
    "count_in_cargo", "count_in_hoarder", "count_in_map",
    "count_in_player", "crafted", "deloot",
]

# The names below are only a fallback for a mission without a
# cfglimitsdefinition.xml: a type's category, tag, usage and value names are
# whatever the mission's own limits file defines (dayz-docs/CLAUDE.md), and
# Livonia's, for one, has no Tier4. No stock limits file defines a
# `vehiclesparts` category; vehicle parts are `lootdispatch`.
KNOWN_CATEGORIES = [
    "clothes", "containers", "explosives", "food",
    "tools", "weapons",
]

KNOWN_USAGES = [
    "Coast", "Farm", "Firefighter", "Hunting", "Industrial",
    "Lunapark", "Medic", "Military", "Office", "Police",
    "Prison", "School", "Town", "Village",
]

KNOWN_VALUES = ["Tier1", "Tier2", "Tier3", "Tier4"]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TypeEntry:
    """One <type> element from a types XML file."""
    name: str
    nominal: int
    min: int
    lifetime: int
    restock: int
    quantmin: int
    quantmax: int
    cost: int
    category: str
    tag: str
    flags: dict[str, int] = field(default_factory=dict)
    usages: list[str] = field(default_factory=list)
    values: list[str] = field(default_factory=list)
    source_file: Path = field(default_factory=lambda: Path())
    element: ET.Element = field(default_factory=lambda: ET.Element("type"))


@dataclass
class TypesFile:
    """One parsed types XML file."""
    path: Path
    tree: ET.ElementTree
    entries: list[TypeEntry] = field(default_factory=list)


@dataclass
class MultiplierConfig:
    """Four float multipliers for bulk editing."""
    nominal: float = 1.0
    min: float = 1.0
    lifetime: float = 1.0
    restock: float = 1.0


# ---------------------------------------------------------------------------
# Scanning & parsing
# ---------------------------------------------------------------------------

def scan_types_files(mission_dir: Path) -> list[Path]:
    """Find db/types.xml + all custom/*.xml whose root is <types>.

    The root decides, not the presence of <type>: a cfgspawnabletypes file is
    <spawnabletypes> full of <type> elements too. Read as a types file, Expansion's
    listed its attachment entries on the Types page as types, and a nominal
    write (Only X, the multiplier) inserted <nominal>/<min> into it.
    """
    results: list[Path] = []

    # Main types.xml
    main_types = mission_dir / "db" / "types.xml"
    if main_types.is_file():
        results.append(main_types)

    # Custom XMLs
    custom_dir = mission_dir / "custom"
    if custom_dir.is_dir():
        for xml_file in sorted(custom_dir.glob("*.xml")):
            if _file_has_type_elements(xml_file):
                results.append(xml_file)

    return results


def _file_has_type_elements(path: Path) -> bool:
    """Quick check if an XML file is a <types> file with <type> elements."""
    try:
        root = load_xml(path).getroot()
        return root.tag.lower() == "types" and root.find("type") is not None
    except (ET.ParseError, OSError, ValueError):
        # ValueError: a file that is not UTF-8. custom/ holds every mod's XML.
        return False


def _int_text(elem: ET.Element | None, default: int) -> int:
    """Parse an element's text as int, falling back to *default*.

    Mirrors events_xml._get_text_int: a missing, empty, whitespace-only, or
    non-numeric value (e.g. a hand-edited "10.0" or stray text) yields the
    default instead of raising, so one malformed <type> entry can't abort the
    whole parse — which would crash the economy analyzer, the "What Changed"
    view, preset snapshots, and the spawn editor (none of which catch ValueError).
    """
    if elem is not None and elem.text:
        try:
            return int(elem.text.strip())
        except ValueError:
            return default
    return default


def parse_types_file(path: Path, *, preserve_comments: bool = False) -> TypesFile:
    """Parse one file, extract TypeEntry for each <type>."""
    tree = load_xml(path, preserve_comments=preserve_comments)
    root = tree.getroot()
    entries: list[TypeEntry] = []

    for elem in root.findall("type"):
        name = elem.get("name", "")

        nominal_el = elem.find("nominal")
        min_el = elem.find("min")
        lifetime_el = elem.find("lifetime")
        restock_el = elem.find("restock")
        quantmin_el = elem.find("quantmin")
        quantmax_el = elem.find("quantmax")
        cost_el = elem.find("cost")
        category_el = elem.find("category")
        tag_el = elem.find("tag")
        flags_el = elem.find("flags")

        # Extract flags
        flags: dict[str, int] = {}
        if flags_el is not None:
            for flag_name in FLAG_NAMES:
                val = flags_el.get(flag_name)
                if val is not None:
                    try:
                        flags[flag_name] = int(val)
                    except ValueError:
                        flags[flag_name] = 0

        # Extract usage and value elements
        usages = [u.get("name", "") for u in elem.findall("usage") if u.get("name")]
        values = [v.get("name", "") for v in elem.findall("value") if v.get("name")]

        entries.append(TypeEntry(
            name=name,
            nominal=_int_text(nominal_el, 0),
            min=_int_text(min_el, 0),
            lifetime=_int_text(lifetime_el, 0),
            restock=_int_text(restock_el, 0),
            quantmin=_int_text(quantmin_el, -1),
            quantmax=_int_text(quantmax_el, -1),
            cost=_int_text(cost_el, 100),
            category=category_el.get("name", "") if category_el is not None else "",
            tag=tag_el.get("name", "") if tag_el is not None else "",
            flags=flags,
            usages=usages,
            values=values,
            source_file=path,
            element=elem,
        ))

    return TypesFile(path=path, tree=tree, entries=entries)


# ---------------------------------------------------------------------------
# Category helpers
# ---------------------------------------------------------------------------

def collect_categories(types_files: list[TypesFile]) -> list[str]:
    """Sorted unique category names across all files."""
    cats: set[str] = set()
    for tf in types_files:
        for entry in tf.entries:
            if entry.category:
                cats.add(entry.category)
    return sorted(cats)


def filter_entries(
    types_files: list[TypesFile],
    categories: list[str] | None = None,
) -> list[TypeEntry]:
    """Filter entries by category. None = all entries."""
    if categories is None:
        return [e for tf in types_files for e in tf.entries]

    result: list[TypeEntry] = []
    for tf in types_files:
        for entry in tf.entries:
            cat = entry.category if entry.category else "(uncategorized)"
            if cat in categories:
                result.append(entry)
    return result


# ---------------------------------------------------------------------------
# Multiplier operations
# ---------------------------------------------------------------------------

def apply_multiplier(entry: TypeEntry, mult: MultiplierConfig) -> dict[str, tuple[int, int]]:
    """Preview: returns {field: (old, new)} without modifying XML."""
    return {
        "nominal": (entry.nominal, max(0, int(entry.nominal * mult.nominal))),
        "min": (entry.min, max(0, int(entry.min * mult.min))),
        "lifetime": (entry.lifetime, max(0, int(entry.lifetime * mult.lifetime))),
        "restock": (entry.restock, max(0, int(entry.restock * mult.restock))),
    }


def write_multiplier(entry: TypeEntry, mult: MultiplierConfig) -> None:
    """Apply multiplier to live ET.Element in-place."""
    elem = entry.element

    for field_name, multiplier in [
        ("nominal", mult.nominal),
        ("min", mult.min),
        ("lifetime", mult.lifetime),
        ("restock", mult.restock),
    ]:
        child = elem.find(field_name)
        if child is not None and child.text:
            try:
                old_val = int(child.text.strip())
            except ValueError:
                continue  # leave a malformed value untouched
            new_val = max(0, int(old_val * multiplier))
            child.text = str(new_val)

    # Update dataclass fields too
    entry.nominal = max(0, int(entry.nominal * mult.nominal))
    entry.min = max(0, int(entry.min * mult.min))
    entry.lifetime = max(0, int(entry.lifetime * mult.lifetime))
    entry.restock = max(0, int(entry.restock * mult.restock))


# ---------------------------------------------------------------------------
# What a type's numbers may be
# ---------------------------------------------------------------------------

# F30 (plans/55 A): the lowest value each number of a type row can take.
# Bohemia's own types.xml writes -1 for quantmin/quantmax that do not apply;
# the rest count or time something.
TYPE_NUMBER_FLOORS = {
    "nominal": 0, "min": 0, "lifetime": 0, "restock": 0, "cost": 0,
    "quantmin": -1, "quantmax": -1,
}
# quantmin/quantmax are a fill percentage (FIELD_DESCRIPTIONS), so a value is
# -1 (does not apply) or 0..100; every stock types.xml stays inside that.
QUANT_MAX = 100


def type_number_problems(current: dict[str, int], changes: dict[str, int]) -> list[str]:
    """What is wrong with writing *changes* over a type whose numbers are
    *current*; empty when nothing is.

    Only what the write changes is judged. Bohemia's own files hold pairs this
    refuses for a new value (805 stock types have nominal 0 and min 1, the
    Crossbow quantmin 80 and quantmax 0), and a save that leaves them alone
    must not fail on them. A min above a nominal of 0 is that stock "never
    spawned" shape and is let through.
    """
    changes = {k: v for k, v in changes.items() if v != current.get(k)}
    problems = [
        f"{name} must be at least {floor}"
        for name, floor in TYPE_NUMBER_FLOORS.items()
        if name in changes and changes[name] < floor
    ]
    for name in ("quantmin", "quantmax"):
        if name in changes and changes[name] > QUANT_MAX:
            problems.append(f"{name} is a percentage: -1 or 0 to {QUANT_MAX}, not {changes[name]}")
    after = {**current, **changes}
    if "nominal" in changes or "min" in changes:
        nominal, minimum = after.get("nominal"), after.get("min")
        if nominal is not None and minimum is not None and nominal > 0 and minimum > nominal:
            problems.append(f"min ({minimum}) cannot be above nominal ({nominal})")
    if "quantmin" in changes or "quantmax" in changes:
        qmin, qmax = after.get("quantmin"), after.get("quantmax")
        if qmin is not None and qmax is not None and qmin != -1 and qmax != -1 and qmin > qmax:
            problems.append(f"quantmin ({qmin}) cannot be above quantmax ({qmax})")
    return problems


def entry_numbers(entry: TypeEntry) -> dict[str, int]:
    """The numbers *entry* has, keyed as `type_number_problems` reads them."""
    return {name: getattr(entry, name) for name in TYPE_NUMBER_FLOORS}


def multiplier_problems(entries: list[TypeEntry], mult: MultiplierConfig) -> list[str]:
    """The entries a multiplier would leave with min above nominal, by name."""
    bad = []
    for entry in entries:
        changes = {name: new for name, (_old, new) in apply_multiplier(entry, mult).items()}
        if type_number_problems(entry_numbers(entry), changes):
            bad.append(entry.name)
    return bad


def fill_changes(entry: TypeEntry, fill: dict[str, int]) -> dict[str, int]:
    """The Filler's writes for *entry*: each number in *fill* that the entry
    has at 0, with its fill value. A multiplier cannot move a 0."""
    return {name: value for name, value in fill.items() if getattr(entry, name) == 0}


def fill_problems(entries: list[TypeEntry], fill: dict[str, int]) -> list[str]:
    """The entries a fill would leave with min above nominal, by name."""
    return [
        entry.name for entry in entries
        if type_number_problems(entry_numbers(entry), fill_changes(entry, fill))
    ]


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save_types_files(types_files: list[TypesFile]) -> None:
    """Write modified trees back to disk."""
    for tf in types_files:
        save_xml(tf.tree, tf.path)


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def backup_types_file(path: Path) -> Path:
    """Create .backup copy (overwrites previous backup). Returns backup path."""
    return backup_file(path)


def restore_types_file(path: Path, shipped: Path | None = None) -> bool:
    """Restore the game's copy (*shipped*), else the .backup. True if restored."""
    return restore_file(path, shipped)


def find_restorable_files(mission_dir: Path) -> list[Path]:
    """Types files that have .backup counterparts."""
    types_files = scan_types_files(mission_dir)
    return [
        p for p in types_files
        if p.with_suffix(p.suffix + ".backup").exists()
    ]


# ---------------------------------------------------------------------------
# Individual-entry write helpers
# ---------------------------------------------------------------------------

def set_numeric_field(entry: TypeEntry, field_name: str, value: int) -> None:
    """Update a numeric field (nominal/min/lifetime/restock) on entry + element."""
    elem = entry.element
    child = elem.find(field_name)
    if child is None:
        child = subelement_in_order(elem, field_name, TYPE_CHILD_ORDER)
    child.text = str(value)
    setattr(entry, field_name, value)


def set_flag(entry: TypeEntry, flag_name: str, value: int) -> None:
    """Set a flag attribute on the <flags> element."""
    elem = entry.element
    flags_el = elem.find("flags")
    if flags_el is None:
        flags_el = subelement_in_order(elem, "flags", TYPE_CHILD_ORDER)
    flags_el.set(flag_name, str(value))
    entry.flags[flag_name] = value


def set_category(entry: TypeEntry, name: str | None) -> None:
    """Set or remove the <category name="..."/> element."""
    elem = entry.element
    cat_el = elem.find("category")
    if name:
        if cat_el is None:
            cat_el = subelement_in_order(elem, "category", TYPE_CHILD_ORDER)
        cat_el.set("name", name)
        entry.category = name
    else:
        if cat_el is not None:
            elem.remove(cat_el)
        entry.category = ""


def set_tag(entry: TypeEntry, name: str | None) -> None:
    """Set or remove the <tag name="..."/> element."""
    elem = entry.element
    tag_el = elem.find("tag")
    if name:
        if tag_el is None:
            tag_el = subelement_in_order(elem, "tag", TYPE_CHILD_ORDER)
        tag_el.set("name", name)
        entry.tag = name
    else:
        if tag_el is not None:
            elem.remove(tag_el)
        entry.tag = ""


def set_usages(entry: TypeEntry, names: list[str]) -> None:
    """Replace the <usage name> elements; a <usage user> group reference stays."""
    elem = entry.element
    for old in elem.findall("usage"):
        if old.get("user") is None:
            elem.remove(old)
    for n in names:
        subelement_in_order(elem, "usage", TYPE_CHILD_ORDER).set("name", n)
    entry.usages = list(names)


def set_values(entry: TypeEntry, names: list[str]) -> None:
    """Replace the <value name> elements; a <value user> group reference stays."""
    elem = entry.element
    for old in elem.findall("value"):
        if old.get("user") is None:
            elem.remove(old)
    for n in names:
        subelement_in_order(elem, "value", TYPE_CHILD_ORDER).set("name", n)
    entry.values = list(names)


# ---------------------------------------------------------------------------
# Collection helpers
# ---------------------------------------------------------------------------

def collect_all_categories(types_files: list[TypesFile]) -> list[str]:
    """Sorted unique categories the files use."""
    return sorted({e.category for tf in types_files for e in tf.entries if e.category})


def collect_usages(types_files: list[TypesFile]) -> list[str]:
    """Sorted unique usages the files use."""
    return sorted({u for tf in types_files for e in tf.entries for u in e.usages})


def collect_values(types_files: list[TypesFile]) -> list[str]:
    """Sorted unique value/tier names the files use."""
    return sorted({v for tf in types_files for e in tf.entries for v in e.values})


#: The children of a <type> that name a cfglimitsdefinition.xml entry.
LIMIT_KINDS = ("category", "tag", "usage", "value")


def limit_names_of(elem: ET.Element) -> dict[str, set[str]]:
    """The limits names a <type> element uses, by kind (``name=`` only; a
    ``user=`` group reference is not a name)."""
    return {
        kind: {c.get("name") for c in elem.findall(kind) if c.get("name")}
        for kind in LIMIT_KINDS
    }


def undefined_limit_names(
    used: dict[str, set[str]],
    defined: dict[str, set[str]],
    before: dict[str, set[str]] | None = None,
) -> list[str]:
    """``"usage 'Moon'"`` for each name *used* that *defined* lacks.

    With *before*, only names that are new since then count: a save judges
    what it adds, so a type that already named something undefined can
    still be saved without touching it.
    """
    problems = []
    for kind in LIMIT_KINDS:
        new = used.get(kind, set()) - (before or {}).get(kind, set())
        for name in sorted(new - defined.get(kind, set())):
            problems.append(f"{kind} {name!r}")
    return problems


# Field help text (shared with the web UI, which strips the Rich
# console markup; see app/web/editors/_help.py).
FIELD_DESCRIPTIONS: dict[str, str] = {
    "nominal": "Target quantity of this item the Central Economy tries to maintain across the map.",
    "min": "Minimum number before CE triggers respawn. Must be lower than nominal.",
    "lifetime": "Seconds the item persists before cleanup. The timer resets whenever a player interacts with the item.",
    "restock": "Time in seconds before CE attempts to respawn this type after dropping below minimum.",
    "quantmin": "Minimum fill quantity on spawn — ammo count, liquid fill, food %. Use -1 for non-stackable items (weapons, clothing).",
    "quantmax": "Maximum fill quantity on spawn. Use -1 for non-stackable items (weapons, clothing).",
    "cost": "Spawn priority weight. Higher = more likely to win the spawn roll against competing items. Default: 100.",
    "count_in_cargo": "On: copies inside containers (backpacks, crates, car trunks) count towards nominal and min, so they hold back new spawns. Off: the economy does not count them.",
    "count_in_hoarder": "On: copies in player stashes (tents, barrels, buried stashes) count towards nominal and min, so they hold back new spawns. Off: the economy does not count them.",
    "count_in_map": "On: copies on the ground and in buildings count towards nominal and min, so they hold back new spawns. Off: the economy does not count them.",
    "count_in_player": "On: copies that players carry count towards nominal and min, so they hold back new spawns. Off: the economy does not count them.",
    "crafted": "Enable to remove this item from natural spawns — it can only be obtained via crafting by players. Off by default.",
    "deloot": "On: the item spawns only at dynamic events, such as helicopter crashes. Off: it spawns as usual.",
    "category": "Limiter category tag from cfglimitsdefinition.xml.",
    "tag": "Additional limiter tag.",
}
