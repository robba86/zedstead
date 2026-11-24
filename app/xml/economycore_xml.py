"""Economy core XML parsing, editing, and backup/restore."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from app.xml.xml_classify import CE_TYPES
from app.xml.xml_utils import backup_file, get_mission_dir, load_xml, restore_file, save_xml


# ---------------------------------------------------------------------------
# Known defaults with descriptions
# ---------------------------------------------------------------------------

# Every <default> the engine reads, from the wiki's table
# (central-economy-configuration.wiki:246-363). Each help line gives the
# engine's own default and, where Bohemia's missions set another value, that
# one too. `log_ce_loop` is the spelling Bohemia's cfgeconomycore.xml uses;
# the wiki's table writes `log_celoop`, so both are known.
KNOWN_DEFAULTS: dict[str, str] = {
    "world_segments": (
        "How many segments the CE splits the world into for saving, loading and "
        "cleanup. Default: 12, set with Chernarus in mind; a map of another size "
        "may want another number."
    ),
    "backup_period": (
        "Minutes between persistence backups. Minimum 15, default 60."
    ),
    "backup_count": (
        "How many persistence backups (folders) are kept; the oldest is "
        "overwritten. Default: 12."
    ),
    "backup_startup": (
        "Make a persistence backup as soon as the server has loaded. Default: false."
    ),
    "dyn_radius": (
        "Size in metres of a dynamic infected zone. Engine default 20; "
        "Bohemia's missions set 30."
    ),
    "dyn_smin": (
        "Minimum static (always-present) infected in a dynamic zone. Default: 0."
    ),
    "dyn_smax": (
        "Maximum static (always-present) infected in a dynamic zone. Default: 0."
    ),
    "dyn_dmin": (
        "Minimum dynamic (activity-driven) infected in a dynamic zone. "
        "Engine default 0; Bohemia's missions set 1."
    ),
    "dyn_dmax": (
        "Maximum dynamic (activity-driven) infected in a dynamic zone. Default: 5."
    ),
    "save_events_startup": (
        "If disabled, no data/events.bin is created at startup. "
        "Useful for minimal hive setup. Default: on."
    ),
    "save_types_startup": (
        "If disabled, no data/types.bin is created at startup. "
        "Useful for minimal hive setup. Default: on."
    ),
    "log_hivewarning": (
        "Enable/disable some of the hive warning messages in console. Default: on."
    ),
    "log_storageinfo": (
        "Enable/disable periodic storage info messages in console, when the CE "
        "stores files. Default: off."
    ),
    "log_missionfilewarning": (
        "Enable/disable console warnings about mission files (sandbox mode does "
        "not need them all). Default: on."
    ),
    "log_ce_loop": (
        "Enable/disable logging of CE loop timing and basic statistical info. Default: off."
    ),
    "log_celoop": (
        "The wiki's spelling of log_ce_loop: logging of CE loop timing and basic "
        "statistical info. Default: off."
    ),
    "log_ce_dynamicevent": (
        "Enable/disable logging of CE dynamic events. Default: off."
    ),
    "log_ce_vehicle": (
        "Enable/disable logging of CE vehicle spawning. Default: off."
    ),
    "log_ce_lootspawn": (
        "Enable/disable logging of CE loot spawn events. Default: off."
    ),
    "log_ce_lootcleanup": (
        "Enable/disable logging of CE cleanup events (not just loot). Default: off."
    ),
    "log_ce_lootrespawn": (
        "Enable/disable logging of CE loot respawn events. Default: off."
    ),
    "log_ce_statistics": (
        "Enable/disable logging of CE statistical data. Default: off."
    ),
    "log_ce_zombie": (
        "Enable/disable logging of CE infected-related events. Default: off."
    ),
    "log_ce_animal": (
        "Enable/disable logging of CE animal-related events. Default: off."
    ),
}

# Each default's type, from the same table: Integer, Float or Boolean. A
# Boolean is written true/false, as Bohemia's cfgeconomycore.xml writes it.
_INTEGERS = ("world_segments", "backup_period", "backup_count")
_FLOATS = ("dyn_radius", "dyn_smin", "dyn_smax", "dyn_dmin", "dyn_dmax")
DEFAULT_TYPES: dict[str, str] = {
    name: "int" if name in _INTEGERS else "float" if name in _FLOATS else "bool"
    for name in KNOWN_DEFAULTS
}

# "Minimum value "15"" for backup_period (central-economy-configuration.wiki:376);
# a segment count is at least 1, every other number at least 0.
DEFAULT_MINIMUMS: dict[str, float] = {"backup_period": 15, "world_segments": 1}

BOOLEAN_WORDS = ("true", "false")


def default_value_problem(name: str, value: str) -> str | None:
    """What is wrong with ``<default name value>``, or None."""
    kind = DEFAULT_TYPES.get(name)
    if kind is None:
        return (
            f"{name!r} is not a cfgeconomycore.xml default the engine reads; the "
            "known ones are " + ", ".join(KNOWN_DEFAULTS) + "."
        )
    text = str(value).strip()
    if kind == "bool":
        if text not in BOOLEAN_WORDS:
            return f"{name} is true or false, not {value!r}"
        return None
    try:
        number = float(text) if kind == "float" else int(text)
    except ValueError:
        what = "a whole number" if kind == "int" else "a number"
        return f"{name} must be {what}, not {value!r}"
    if not math.isfinite(number):
        return f"{name} must be a number, not {value!r}"
    low = DEFAULT_MINIMUMS.get(name, 0)
    if number < low:
        return f"{name} must be at least {low:g}, not {value}"
    return None


# The `type=` values DayZ accepts in a `<ce><file>` registration. Canonical
# list — the cfgeconomycore table editor offers exactly these and rejects
# anything else, since an unknown type makes the engine refuse the whole
# economy load.
FILE_TYPES: tuple[str, ...] = CE_TYPES

#: What each FILE_TYPES value holds, the line under it in the web picker.
FILE_TYPE_HELP: dict[str, str] = {
    "economy": "Which CE systems load, respawn and save.",
    "globals": "Engine variables of the central economy.",
    "types": "Item spawn rules: nominal, lifetime, flags.",
    "spawnabletypes": "Cargo and attachments that spawn with an item.",
    "events": "Dynamic events: vehicles, crash sites, animals.",
    "messages": "Server messages to players.",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class EconomyDefault:
    """One <default> element from cfgeconomycore.xml."""
    name: str
    value: str
    element: ET.Element = field(default_factory=lambda: ET.Element("default"))


@dataclass
class EconomyFile:
    """One <file> element inside a <ce> rootclass."""
    name: str
    type: str
    element: ET.Element = field(default_factory=lambda: ET.Element("file"))


@dataclass
class EconomyRootclass:
    """One <ce> element from cfgeconomycore.xml."""
    folder: str
    files: list[EconomyFile] = field(default_factory=list)
    element: ET.Element = field(default_factory=lambda: ET.Element("ce"))


@dataclass
class EconomycoreFile:
    """Parsed cfgeconomycore.xml file."""
    path: Path
    tree: ET.ElementTree
    defaults: list[EconomyDefault] = field(default_factory=list)
    rootclasses: list[EconomyRootclass] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_economycore_path(mission_dir: Path) -> Path | None:
    """Return cfgeconomycore.xml if it exists."""
    p = mission_dir / "cfgeconomycore.xml"
    return p if p.is_file() else None


# ---------------------------------------------------------------------------
# Parse & save
# ---------------------------------------------------------------------------

def parse_economycore_file(path: Path) -> EconomycoreFile:
    """Parse cfgeconomycore.xml, keeping its comments.

    `preserve_comments` is not cosmetic here: a disabled mod's registrations are
    parked in `<!-- DISABLED @Mod: ... -->` wrappers that only
    `xml_economy.enable_mod_economy_entries` may remove, so a parse/save round
    trip through the web table editor must carry them through. A comment above
    the root element is still lost (ElementTree only emits comments while an
    element is open) — that is the one case `save_economycore_file` can't keep.
    """
    tree = load_xml(path, preserve_comments=True)
    root = tree.getroot()

    defaults: list[EconomyDefault] = []
    defaults_elem = root.find("defaults")
    if defaults_elem is not None:
        for elem in defaults_elem.findall("default"):
            defaults.append(EconomyDefault(
                name=elem.get("name", ""),
                value=elem.get("value", ""),
                element=elem,
            ))

    rootclasses: list[EconomyRootclass] = []
    for ce_elem in root.findall("ce"):
        files: list[EconomyFile] = []
        for file_elem in ce_elem.findall("file"):
            files.append(EconomyFile(
                name=file_elem.get("name", ""),
                type=file_elem.get("type", ""),
                element=file_elem,
            ))
        rootclasses.append(EconomyRootclass(
            folder=ce_elem.get("folder", ""),
            files=files,
            element=ce_elem,
        ))

    return EconomycoreFile(path=path, tree=tree, defaults=defaults, rootclasses=rootclasses)


def save_economycore_file(ef: EconomycoreFile) -> None:
    """Write modified tree back to disk."""
    save_xml(ef.tree, ef.path)


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def backup_economycore_file(path: Path) -> Path:
    """Create .backup copy. Returns backup path."""
    return backup_file(path)


def restore_economycore_file(path: Path) -> bool:
    """Restore from .backup. Returns True if restored."""
    return restore_file(path)


# ---------------------------------------------------------------------------
# CRUD — defaults
# ---------------------------------------------------------------------------

def set_default_value(entry: EconomyDefault, value: str) -> None:
    """Update value attribute on element + dataclass."""
    entry.element.set("value", value)
    entry.value = value


def add_default(ef: EconomycoreFile, name: str, value: str) -> EconomyDefault:
    """Add a new <default> element."""
    root = ef.tree.getroot()
    defaults_elem = root.find("defaults")
    if defaults_elem is None:
        defaults_elem = ET.SubElement(root, "defaults")
        # Insert defaults before any <ce> elements
        root.remove(defaults_elem)
        root.insert(0, defaults_elem)

    elem = ET.SubElement(defaults_elem, "default")
    elem.set("name", name)
    elem.set("value", value)
    entry = EconomyDefault(name=name, value=value, element=elem)
    ef.defaults.append(entry)
    return entry


def remove_default(ef: EconomycoreFile, entry: EconomyDefault) -> None:
    """Remove a <default> element."""
    root = ef.tree.getroot()
    defaults_elem = root.find("defaults")
    if defaults_elem is not None:
        defaults_elem.remove(entry.element)
    ef.defaults.remove(entry)


# ---------------------------------------------------------------------------
# CRUD — rootclasses
# ---------------------------------------------------------------------------

def find_ce_entry(
    ef: EconomycoreFile, folder: str, filename: str, file_type: str,
) -> EconomyRootclass | None:
    """Return existing <ce> matching folder+file+type, or None."""
    for rc in ef.rootclasses:
        if rc.folder == folder and any(
            f.name == filename and f.type == file_type for f in rc.files
        ):
            return rc
    return None


def add_rootclass(ef: EconomycoreFile, folder: str) -> EconomyRootclass:
    """Add a new <ce folder="..."> element."""
    root = ef.tree.getroot()
    elem = ET.SubElement(root, "ce")
    elem.set("folder", folder)
    rc = EconomyRootclass(folder=folder, files=[], element=elem)
    ef.rootclasses.append(rc)
    return rc


def remove_rootclass(ef: EconomycoreFile, rc: EconomyRootclass) -> None:
    """Remove a <ce> element."""
    root = ef.tree.getroot()
    root.remove(rc.element)
    ef.rootclasses.remove(rc)


def add_file_to_rootclass(rc: EconomyRootclass, name: str, file_type: str) -> EconomyFile:
    """Add a <file> element under an existing <ce>."""
    elem = ET.SubElement(rc.element, "file")
    elem.set("name", name)
    elem.set("type", file_type)
    ef = EconomyFile(name=name, type=file_type, element=elem)
    rc.files.append(ef)
    return ef


def remove_file_from_rootclass(rc: EconomyRootclass, ef: EconomyFile) -> None:
    """Remove a <file> element from a <ce>."""
    rc.element.remove(ef.element)
    rc.files.remove(ef)
