"""Ignore list XML parsing, editing, and backup/restore."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from app.xml.xml_utils import backup_file, load_xml, restore_file, save_xml


# ---------------------------------------------------------------------------
# Description
# ---------------------------------------------------------------------------

DESCRIPTION: str = (
    "Items added to cfgIgnoreList.xml will not spawn and are wiped on server restart. "
    "Used to remove specific items, reset vehicle spawns, or prevent certain classnames "
    "from appearing."
)


# ---------------------------------------------------------------------------
# Common base-building items for quick-add suggestions
# ---------------------------------------------------------------------------

COMMON_BASE_BUILDING_ITEMS: list[str] = [
    "FenceKit",
    "WatchtowerKit",
    "TerritoryFlagKit",
    "Fence",
    "Watchtower",
    "TerritoryFlag",
    "BarrelHoles_Blue",
    "BarrelHoles_Green",
    "BarrelHoles_Red",
    "BarrelHoles_Yellow",
    "SeaChest",
    "WoodenCrate",
    "Tent_Large",
    "Tent_Medium",
    "CamoNet",
    "BarbedWire",
    "CombinationLock",
    "CombinationLock4",
    "CodeLock",
]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class IgnoreEntry:
    """One <item> element from cfgIgnoreList.xml."""
    name: str
    element: ET.Element = field(default_factory=lambda: ET.Element("item"))


@dataclass
class IgnoreListFile:
    """Parsed cfgIgnoreList.xml file."""
    path: Path
    tree: ET.ElementTree
    entries: list[IgnoreEntry] = field(default_factory=list)


# The element the game's own cfgignorelist.xml lists a class with, and the
# tags read as an entry.
ENTRY_TAG = "type"
ENTRY_TAGS = (ENTRY_TAG, "item")


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_ignorelist_path(mission_dir: Path) -> Path | None:
    """Return cfgIgnoreList.xml if it exists (check root + db/)."""
    for p in [
        mission_dir / "cfgIgnoreList.xml",
        mission_dir / "cfgignorelist.xml",
        mission_dir / "db" / "cfgIgnoreList.xml",
        mission_dir / "db" / "cfgignorelist.xml",
    ]:
        if p.is_file():
            return p
    return None


# ---------------------------------------------------------------------------
# Parse & save
# ---------------------------------------------------------------------------

def parse_ignorelist_file(path: Path) -> IgnoreListFile:
    """Parse cfgIgnoreList.xml."""
    tree = load_xml(path, preserve_comments=True)
    root = tree.getroot()
    # Bohemia's files hold <type name="..."></type> only. <item> is what zsm
    # wrote until 2026 Q4, so it is still read (and removable).
    entries = [
        IgnoreEntry(name=elem.get("name", ""), element=elem)
        for elem in root
        if elem.tag in ENTRY_TAGS
    ]
    return IgnoreListFile(path=path, tree=tree, entries=entries)


def save_ignorelist_file(ilf: IgnoreListFile) -> None:
    """Write modified tree back to disk."""
    save_xml(ilf.tree, ilf.path)


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def backup_ignorelist_file(path: Path) -> Path:
    return backup_file(path)


def restore_ignorelist_file(path: Path) -> bool:
    return restore_file(path)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def has_entry(ilf: IgnoreListFile, name: str) -> bool:
    """Check if an entry with this name already exists."""
    return any(e.name == name for e in ilf.entries)


def add_entry(ilf: IgnoreListFile, name: str) -> IgnoreEntry:
    """Add an entry, as a <type> like the stock file's own."""
    root = ilf.tree.getroot()
    elem = ET.SubElement(root, ENTRY_TAG)
    elem.set("name", name)
    entry = IgnoreEntry(name=name, element=elem)
    ilf.entries.append(entry)
    return entry


def remove_entry(ilf: IgnoreListFile, entry: IgnoreEntry) -> None:
    """Remove an entry's element."""
    root = ilf.tree.getroot()
    root.remove(entry.element)
    ilf.entries.remove(entry)
