"""economy.xml parser/writer — manages db/economy.xml system flags."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.xml.xml_utils import backup_file, load_xml, restore_file, save_xml


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class EconomySystem:
    """One system element inside db/economy.xml (e.g. <dynamic>, <animals>)."""
    name: str
    init: bool
    load: bool
    respawn: bool
    save: bool
    element: ET.Element

    @property
    def flags_str(self) -> str:
        parts = []
        for flag in ("init", "load", "respawn", "save"):
            val = getattr(self, flag)
            mark = "[green]\u2713[/green]" if val else "[red]\u2717[/red]"
            parts.append(f"{flag}={mark}")
        return "  ".join(parts)


@dataclass
class EconomyFile:
    """Parsed db/economy.xml."""
    path: Path
    tree: ET.ElementTree
    systems: list[EconomySystem] = field(default_factory=list)


# ---------------------------------------------------------------------------
# System descriptions
# ---------------------------------------------------------------------------

SYSTEM_DESCRIPTIONS: dict[str, str] = {
    "dynamic": (
        "Controls the Central Economy dynamic loot spawning system. "
        "Manages all ground loot, container contents, and item distribution across the map. "
        "Disabling this stops all loot from spawning or respawning."
    ),
    "animals": (
        "Controls animal and wildlife spawning across all territories. "
        "Governs wolves, bears, deer, cattle, and other fauna defined in cfgenvironment.xml. "
        "Disabling this removes all wild animals from the server."
    ),
    "zombies": (
        "Controls infected (zombie) spawning at towns, military areas, and other trigger zones. "
        "Zombie density and placement are driven by zombie_territories.xml. "
        "Disabling this creates a zombie-free server."
    ),
    "vehicles": (
        "Controls vehicle spawning, placement, and persistence across the map. "
        "Vehicles are distributed based on types.xml nominal values and position data. "
        "The save flag determines whether vehicle state persists across restarts."
    ),
    "randoms": (
        "Controls random container loot defined by cfgRandomPresets.xml cargo presets. "
        "These presets determine what items can appear inside containers like backpacks and clothing. "
        "Disabling this means containers spawn empty."
    ),
    "custom": (
        "Controls custom and modded object spawning managed by the Central Economy. "
        "Used by mods that register their items through the CE system. "
        "Disabling this prevents mod-added CE items from spawning."
    ),
    "building": (
        "Controls base-building persistence for player-constructed structures. "
        "Covers fences, watchtowers, territory flags, and other buildable objects. "
        "The save flag is critical: disabling it wipes all bases on restart."
    ),
    "player": (
        "Controls player character persistence including inventory, position, and health state. "
        "When save is enabled, players resume where they logged out with their gear intact. "
        "Disabling save resets all players to fresh spawns on every restart."
    ),
}

FLAG_DESCRIPTIONS: dict[str, str] = {
    "init": (
        "Spawn fresh entities of this group when the server boots. "
        "Turn off if you only want persistence-loaded entries (requires load)."
    ),
    "load": (
        "Restore previously-saved entries from storage on boot. "
        "Required alongside save for full persistence — save alone just writes files nothing reads."
    ),
    "save": (
        "Write this group's state to persistent storage during the save cycle. "
        "Pair with load to survive restarts — building=on with save=off wipes bases every reboot."
    ),
    "respawn": (
        "Allow the Central Economy to spawn more of this group during runtime as things are removed. "
        "Off means the world count only drops, never recovers."
    ),
}


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_economy_path(mission_dir: Path) -> Path | None:
    """Return path to db/economy.xml if it exists."""
    candidates = [
        mission_dir / "db" / "economy.xml",
        mission_dir / "db" / "Economy.xml",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


# ---------------------------------------------------------------------------
# Parse / save
# ---------------------------------------------------------------------------

def parse_economy(path: Path) -> EconomyFile:
    """Parse db/economy.xml into an EconomyFile."""
    # Comments kept: the tree is saved, and a Mod XML link's START/END MOD
    # markers are how its block is found again.
    tree = load_xml(path, preserve_comments=True)
    root = tree.getroot()

    systems: list[EconomySystem] = []
    for elem in root:
        if not isinstance(elem.tag, str):  # a comment
            continue
        systems.append(EconomySystem(
            name=elem.tag,
            init=_flag(elem, "init"),
            load=_flag(elem, "load"),
            respawn=_flag(elem, "respawn"),
            save=_flag(elem, "save"),
            element=elem,
        ))

    return EconomyFile(path=path, tree=tree, systems=systems)


def save_economy(ef: EconomyFile) -> None:
    """Write economy.xml back to disk."""
    save_xml(ef.tree, ef.path)


def _flag(elem: ET.Element, attr: str) -> bool:
    """Read a 0/1 attribute as bool."""
    return elem.get(attr, "0") == "1"


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

# The systems the engine reads from economy.xml: the eight elements of
# Bohemia's own file, each named in SYSTEM_DESCRIPTIONS. Any other element is
# read by nothing (central-economy-setup-for-custom-terrains.wiki:58 names
# the file as the switches "for selected groups of entities in CE").
SYSTEM_NAMES: tuple[str, ...] = (
    "dynamic", "animals", "zombies", "vehicles", "randoms", "custom", "building", "player",
)


def check_system_name(name: str) -> None:
    """Raise ValueError unless *name* is one of the engine's economy systems.

    A system is an element tag, so a free name could also make the file
    unparseable; the fixed list rules that out as well.
    """
    if name not in SYSTEM_NAMES:
        raise ValueError(
            f"{name!r} is not an economy system. economy.xml holds only "
            + ", ".join(SYSTEM_NAMES) + "."
        )


def remove_system(ef: EconomyFile, system: EconomySystem) -> None:
    """Remove a system element and its entry."""
    ef.tree.getroot().remove(system.element)
    ef.systems.remove(system)


def rename_system(system: EconomySystem, name: str) -> None:
    """Give a system another name: its element tag. Its flags stay."""
    check_system_name(name)
    system.element.tag = name
    system.name = name


def add_system(ef: EconomyFile, name: str) -> EconomySystem:
    """Append a new system element (flags off) and return it.

    A system is named by its *element tag*, not by a ``name=`` attribute, so an
    arbitrary string can't be joined on: an invalid tag makes the file
    unparseable and takes the whole economy down. Rejected loudly instead.
    """
    check_system_name(name)
    root = ef.tree.getroot()
    elem = ET.SubElement(root, name)
    for flag in ("init", "load", "respawn", "save"):
        elem.set(flag, "0")
    system = EconomySystem(
        name=name, init=False, load=False, respawn=False, save=False, element=elem,
    )
    ef.systems.append(system)
    return system


def set_system_flags(
    system: EconomySystem,
    init: bool,
    load: bool,
    respawn: bool,
    save: bool,
) -> None:
    """Update flags on a system entry."""
    system.init = init
    system.load = load
    system.respawn = respawn
    system.save = save
    system.element.set("init", "1" if init else "0")
    system.element.set("load", "1" if load else "0")
    system.element.set("respawn", "1" if respawn else "0")
    system.element.set("save", "1" if save else "0")


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def backup_economy(path: Path) -> Path:
    """Create .backup copy. Returns backup path."""
    return backup_file(path)


def restore_economy(path: Path) -> bool:
    """Restore from .backup. Returns True if restored."""
    return restore_file(path)
