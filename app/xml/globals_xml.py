"""Globals XML parsing, editing, and backup/restore."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from app.xml.xml_utils import backup_file, get_mission_dir, load_xml, restore_file, save_xml


# ---------------------------------------------------------------------------
# Known variable descriptions for rich display
# ---------------------------------------------------------------------------

VAR_DESCRIPTIONS: dict[str, str] = {
    "AnimalMaxCount": (
        "Maximal limit of spawned animals (not ambient) across all zones in map. Default: 200."
    ),
    "CleanupAvoidance": (
        "Distance in meters from player required for item deletion by the CE. Default: 100m."
    ),
    "CleanupLifetimeDeadAnimal": (
        "Lifetime in seconds for dead animal corpses before cleanup. Default: 1200s (20 min)."
    ),
    "CleanupLifetimeDeadInfected": (
        "Lifetime in seconds for dead infected corpses before cleanup. Default: 330s (5.5 min)."
    ),
    "CleanupLifetimeDeadPlayer": (
        "Lifetime in seconds for dead player bodies before cleanup. Default: 3600s (1 hour)."
    ),
    "CleanupLifetimeDefault": (
        "Cleanup delay in seconds for dead or fully-ruined entities that don't have their own "
        "lifetime set. Used as the fallback sweep interval for generic junk once it's destroyed. "
        "Default: 45s."
    ),
    "CleanupLifetimeLimit": (
        "How many items can be deleted at once during standard cleanup. Default: 50."
    ),
    "CleanupLifetimeRuined": (
        "Lifetime in seconds for ruined loot before cleanup. Default: 330s."
    ),
    "FlagRefreshFrequency": (
        "Territory flag refreshes items under its influence this often. "
        "Default: 5 days (432000s)."
    ),
    "FlagRefreshMaxDuration": (
        "Maximum time a flag keeps refreshing items after the last territory action. "
        "Default: 40 days (3456000s)."
    ),
    "FoodDecay": (
        "Allow decay on food. Requires WorldWetTempUpdate set to 1. "
        "Values: 0 = disabled, 1 = enabled."
    ),
    "IdleModeCountdown": (
        "Activate economy idle mode on empty server after given time in seconds. Default: 60s."
    ),
    "IdleModeStartup": (
        "0 to disable idle mode on server startup. Will still switch on later if "
        "IdleModeCountdown is not 0. Default: 1."
    ),
    "InitialSpawn": (
        "How much loot will be spawned on server initial start without storage, "
        "as a percentage. Default: 100%."
    ),
    "LootDamageMax": (
        "Maximum applied damage to any item spawned through the CE. Range: 0.0-1.0. "
        "Default: 0.82. Setting this to 0 means every spawned item is pristine — rarely desirable, "
        "as it removes the 'find one in good condition' loot grind."
    ),
    "LootDamageMin": (
        "Minimum applied damage to any item spawned through the CE. "
        "Range: 0.0-1.0. Default: 0."
    ),
    "LootProxyPlacement": (
        "Allow dispatch containers to receive the loot. "
        "Values: 0 = disabled, 1 = enabled. Default: 1."
    ),
    "LootSpawnAvoidance": (
        "How far in meters a player should be from a loot group for loot to spawn "
        "inside of it. Default: 100m."
    ),
    "RespawnAttempt": (
        "How many attempts are performed during a single item respawn. Default: 2."
    ),
    "RespawnLimit": (
        "How many items of one type can be spawned at once. Default: 20."
    ),
    "RespawnTypes": (
        "How many different types can be respawned at once. Default: 12."
    ),
    "RestartSpawn": (
        "How much loot should be respawned during restart to nominal, "
        "as a percentage. Default: 0%."
    ),
    "SpawnInitial": (
        "How many initial tests are allowed for item spawn. Default: 1200."
    ),
    "TimeHopping": (
        "Penalty time in seconds for server hoppers. Default: 60s."
    ),
    "TimeLogin": (
        "Default login time in seconds. Max value: 65536. Default: 15s."
    ),
    "TimeLogout": (
        "Default logout time in seconds. Max value: 65536. Default: 15s."
    ),
    "TimePenalty": (
        "Penalty time in seconds for player still in play session (combat log). Default: 20s."
    ),
    "WorldWetTempUpdate": (
        "Allow update of wetness and temperature values on all items in the world. "
        "Values: 0 = disabled, 1 = enabled."
    ),
    "ZombieMaxCount": (
        "Maximal limit of spawned zombies across all zones in map. Default: 1000."
    ),
    "ZoneSpawnDist": (
        "Distance in meters to invoke infected spawn in nearby zone (dynamic infected). "
        "Default: 300m."
    ),
}

# Short descriptions for DataTable column display
# The unit of a globals.xml number, shown in the web form's box beside the
# value (webui UnitInput.jsx). Taken from VAR_DESCRIPTIONS above.
VAR_UNITS: dict[str, str] = {
    **dict.fromkeys((
        "CleanupLifetimeDeadAnimal", "CleanupLifetimeDeadInfected", "CleanupLifetimeDeadPlayer",
        "CleanupLifetimeDefault", "CleanupLifetimeRuined", "FlagRefreshFrequency",
        "FlagRefreshMaxDuration", "IdleModeCountdown", "TimeHopping", "TimeLogin",
        "TimeLogout", "TimePenalty",
    ), "seconds"),
    **dict.fromkeys(("CleanupAvoidance", "LootSpawnAvoidance", "ZoneSpawnDist"), "meters"),
    **dict.fromkeys(("InitialSpawn", "RestartSpawn"), "%"),
}

# The 0/1 vars (VAR_DESCRIPTIONS: "0 = disabled, 1 = enabled"), drawn as a
# switch in the web form. Their names do not read as a flag to the web UI's
# isFlagKey, so the form names them (widget "flag").
VAR_SWITCHES = frozenset({"FoodDecay", "IdleModeStartup", "LootProxyPlacement", "WorldWetTempUpdate"})

VAR_SHORT_DESCRIPTIONS: dict[str, str] = {
    "AnimalMaxCount": "Max spawned animals across all zones (def: 200)",
    "CleanupAvoidance": "Cleanup distance from player in m (def: 100)",
    "CleanupLifetimeDeadAnimal": "Dead animal cleanup in s (def: 1200)",
    "CleanupLifetimeDeadInfected": "Dead infected cleanup in s (def: 330)",
    "CleanupLifetimeDeadPlayer": "Dead player cleanup in s (def: 3600)",
    "CleanupLifetimeDefault": "Fallback cleanup for ruined/dead entities in s (def: 45)",
    "CleanupLifetimeLimit": "Max items deleted per cleanup (def: 50)",
    "CleanupLifetimeRuined": "Ruined loot cleanup in s (def: 330)",
    "FlagRefreshFrequency": "Flag refresh interval (def: 5d / 432000s)",
    "FlagRefreshMaxDuration": "Flag refresh max duration (def: 40d / 3456000s)",
    "FoodDecay": "Food decay toggle (0/1, needs WorldWetTempUpdate)",
    "IdleModeCountdown": "Idle mode countdown in s (def: 60)",
    "IdleModeStartup": "Idle mode on startup (0/1, def: 1)",
    "InitialSpawn": "Initial spawn percentage (def: 100%)",
    "LootDamageMax": "Max spawned loot damage (0-1, def: 0.82)",
    "LootDamageMin": "Min spawned loot damage (0-1, def: 0)",
    "LootProxyPlacement": "Dispatch container loot (0/1, def: 1)",
    "LootSpawnAvoidance": "Loot spawn distance from player in m (def: 100)",
    "RespawnAttempt": "Respawn attempts per item (def: 2)",
    "RespawnLimit": "Max items of one type spawned at once (def: 20)",
    "RespawnTypes": "Max different types respawned at once (def: 12)",
    "RestartSpawn": "Restart respawn to nominal % (def: 0%)",
    "SpawnInitial": "Initial spawn tests allowed (def: 1200)",
    "TimeHopping": "Server hop penalty in s (def: 60)",
    "TimeLogin": "Login time in s (def: 15, max: 65536)",
    "TimeLogout": "Logout time in s (def: 15, max: 65536)",
    "TimePenalty": "Combat log penalty in s (def: 20)",
    "WorldWetTempUpdate": "World wetness/temp update (0/1)",
    "ZombieMaxCount": "Max spawned zombies across all zones (def: 1000)",
    "ZoneSpawnDist": "Dynamic infected spawn distance in m (def: 300)",
}

# Type codes: "0 for integer, 1 for float, 2 for string"
# (central-economy-configuration.wiki:4).
TYPE_LABELS = {0: "int", 1: "float", 2: "string"}

# Each documented variable's type, from the wiki's db\globals.xml table
# (central-economy-configuration.wiki:5-180). Bohemia's own file agrees.
WIKI_VAR_TYPES: dict[str, int] = {
    **{name: 0 for name in VAR_DESCRIPTIONS},
    "LootDamageMin": 1,
    "LootDamageMax": 1,
}

# Bounds the wiki states: "% (0..1)" for the loot damage pair (:173, :179)
# and "max value 65536" for the login/logout times (:132, :138). Every other
# documented variable is a count, a time in seconds or a 0/1 switch, so it
# is at least 0.
VAR_RANGES: dict[str, tuple[float, float | None]] = {
    "LootDamageMin": (0.0, 1.0),
    "LootDamageMax": (0.0, 1.0),
    "TimeLogin": (0, 65536),
    "TimeLogout": (0, 65536),
}

_WHOLE = re.compile(r"-?[0-9]+")


def parse_var_value(type_val: int, value: str) -> float | str | None:
    """*value* read as its type: an int, a finite float, or the text itself.

    ``None`` when it is not a value of that type.
    """
    text = str(value).strip()
    if type_val == 2:
        return str(value)
    if type_val == 0:
        return int(text) if _WHOLE.fullmatch(text) else None
    if type_val == 1:
        try:
            number = float(text)
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None


def var_problem(name: str, type_val: object, value: str) -> str | None:
    """What is wrong with ``<var name type value>``, or None.

    The type must be one of the three codes and the value must read as that
    type; a documented variable must keep the type the wiki gives it and stay
    inside the wiki's bounds (or at least 0).
    """
    if type_val not in TYPE_LABELS:
        return f"{name}: type must be 0 (integer), 1 (float) or 2 (string), not {type_val!r}"
    wiki_type = WIKI_VAR_TYPES.get(name)
    if wiki_type is not None and type_val != wiki_type:
        return f"{name} is {TYPE_LABELS[wiki_type]} (type {wiki_type}), not type {type_val}"
    number = parse_var_value(type_val, value)
    if number is None:
        what = "a whole number" if type_val == 0 else "a number"
        return f"{name} must be {what}, not {value!r}"
    if wiki_type is None or isinstance(number, str):
        return None
    low, high = VAR_RANGES.get(name, (0, None))
    if number < low or (high is not None and number > high):
        bound = f"{low:g} to {high:g}" if high is not None else f"at least {low:g}"
        return f"{name} must be {bound}, not {value}"
    return None


def same_var_value(type_val: int, old: str, new: str) -> bool:
    """Whether *new* is *old* as the game reads it (``0.0`` and ``0`` are one float)."""
    a, b = parse_var_value(type_val, old), parse_var_value(type_val, new)
    if a is None or b is None:
        return str(old) == str(new)
    return a == b


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class GlobalVar:
    """One <var> element from globals.xml."""
    name: str
    type: int
    value: str
    element: ET.Element = field(default_factory=lambda: ET.Element("var"))


@dataclass
class GlobalsFile:
    """Parsed globals.xml file."""
    path: Path
    tree: ET.ElementTree
    entries: list[GlobalVar] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_globals_path(mission_dir: Path) -> Path | None:
    """Return db/globals.xml if it exists."""
    p = mission_dir / "db" / "globals.xml"
    return p if p.is_file() else None


# ---------------------------------------------------------------------------
# Parse & save
# ---------------------------------------------------------------------------

def parse_globals_file(path: Path) -> GlobalsFile:
    """Parse globals.xml, extract GlobalVar for each <var> element."""
    # Comments kept: the tree is saved, and a Mod XML link's START/END MOD
    # markers are how its block is found again.
    tree = load_xml(path, preserve_comments=True)
    root = tree.getroot()
    entries: list[GlobalVar] = []

    for elem in root.findall("var"):
        name = elem.get("name", "")
        # A present-but-empty (type="") or non-numeric type attribute would
        # crash int() and abort the whole parse; consumers ("What Changed",
        # preset snapshots, the globals editor) don't catch ValueError. Fall
        # back to 0 for a malformed type instead.
        try:
            type_val = int(elem.get("type", "0"))
        except ValueError:
            type_val = 0
        value = elem.get("value", "")
        entries.append(GlobalVar(name=name, type=type_val, value=value, element=elem))

    return GlobalsFile(path=path, tree=tree, entries=entries)


def save_globals_file(gf: GlobalsFile) -> None:
    """Write modified tree back to disk."""
    save_xml(gf.tree, gf.path)


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def backup_globals_file(path: Path) -> Path:
    """Create .backup copy (overwrites previous). Returns backup path."""
    return backup_file(path)


def restore_globals_file(path: Path) -> bool:
    """Restore from .backup. Returns True if restored."""
    return restore_file(path)


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------

def set_var_value(entry: GlobalVar, value: str) -> None:
    """Update value attribute on element + dataclass."""
    entry.element.set("value", value)
    entry.value = value


def set_var_type(entry: GlobalVar, type_val: int) -> None:
    """Update type attribute on element + dataclass."""
    entry.element.set("type", str(type_val))
    entry.type = type_val


def add_var(gf: GlobalsFile, name: str, type_val: int, value: str) -> GlobalVar:
    """Create a new <var> element, append to tree and entries list."""
    root = gf.tree.getroot()
    elem = ET.SubElement(root, "var")
    elem.set("name", name)
    elem.set("type", str(type_val))
    elem.set("value", value)
    entry = GlobalVar(name=name, type=type_val, value=value, element=elem)
    gf.entries.append(entry)
    return entry


def remove_var(gf: GlobalsFile, entry: GlobalVar) -> None:
    """Remove element from tree and entries list."""
    root = gf.tree.getroot()
    root.remove(entry.element)
    gf.entries.remove(entry)
