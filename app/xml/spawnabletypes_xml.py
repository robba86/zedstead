"""cfgspawnabletypes.xml parsing, editing, and backup/restore."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from app.xml.xml_utils import backup_file, get_mission_dir, load_xml, restore_file, save_xml


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class AttachmentItem:
    """One <item> element inside an <attachments> or <cargo> group."""
    name: str
    chance: float | None  # None if no chance attr
    element: ET.Element = field(default_factory=lambda: ET.Element("item"))


@dataclass
class AttachmentGroup:
    """One <attachments> or <cargo> element (may have items or preset only)."""
    tag: str               # "attachments" or "cargo"
    chance: float | None   # outer group spawn chance
    preset: str | None     # preset="..." attribute value, if any
    items: list[AttachmentItem] = field(default_factory=list)
    element: ET.Element = field(default_factory=lambda: ET.Element("attachments"))


@dataclass
class SpawnableType:
    """One <type name="..."> element."""
    name: str
    damage_min: float | None
    damage_max: float | None
    has_hoarder: bool
    tag_name: str | None     # <tag name="...">
    attachment_groups: list[AttachmentGroup] = field(default_factory=list)
    element: ET.Element = field(default_factory=lambda: ET.Element("type"))
    source_file: Path = field(default_factory=Path)


@dataclass
class SpawnableTypesFile:
    """Parsed cfgspawnabletypes.xml file."""
    path: Path
    tree: ET.ElementTree
    entries: list[SpawnableType] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Percent helpers
# ---------------------------------------------------------------------------

def decimal_to_percent(val: float) -> int:
    """0.30 -> 30"""
    return round(val * 100)


def percent_to_decimal(val: int) -> float:
    """30 -> 0.30"""
    return round(val / 100, 2)


def fmt_percent(val: float | None) -> str:
    """Format a decimal as '30%', or '-' if None."""
    if val is None:
        return "-"
    return f"{decimal_to_percent(val)}%"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_spawnabletypes_path(mission_dir: Path) -> Path | None:
    """Return cfgspawnabletypes.xml at the mission root if it exists."""
    p = mission_dir / "cfgspawnabletypes.xml"
    return p if p.is_file() else None


# ---------------------------------------------------------------------------
# Parse & save
# ---------------------------------------------------------------------------

def _opt_number(text: str | None) -> float | None:
    """An attribute as a float; None when it is missing or not a number, so
    one mod's typo does not stop the whole file from loading."""
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _number_text(value: float) -> str:
    """Two decimals, as the stock file writes a chance, unless that would be
    another number (0.125 is not 0.12)."""
    text = f"{value:.2f}"
    return text if float(text) == value else repr(float(value))


def _set_number(elem: ET.Element, attr: str, value: float) -> None:
    """Set a numeric attribute unless it already holds that number: `0.3`
    stays `0.3`, and a save rewrites only what changed."""
    if _opt_number(elem.get(attr)) != value:
        elem.set(attr, _number_text(value))


def fraction(raw, what: str) -> float | None:
    """*raw* as a number from 0 to 1, or None when it is left empty.

    A damage and a group's chance in this file are fractions: Bohemia's stock
    files write `<damage min="0.0" max="0.35" />` and a group's
    `chance="0.10"`, never above 1, and an item at damage 1.0 is dead
    (central-economy-configuration.wiki:42, LootDamageMin/Max "% (0..1)" at
    :170-180). An item's chance is not: see `item_weight`. Anything else, NaN
    included, is a ValueError naming *what*."""
    text = "" if raw is None else str(raw).strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"{what}: {text!r} is not a number") from None
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{what}: {text} is outside 0 to 1 (0.35 is 35%)")
    return value


def item_weight(raw, what: str) -> float | None:
    """*raw* as an item's chance, a finite number of 0 or more, or None when
    it is left empty.

    Not a fraction: Sakhal's stock file (1.29 and 1.30 alike) gives the items
    of one group chances that add up to 100, `<item name="AK_FoldingBttstck"
    chance="40.00" />` beside 30.00, 20.00, 5.00 and 5.00 under AKM. Within a
    group the chances weigh the items against each other, so only a negative
    or non-finite one means nothing."""
    text = "" if raw is None else str(raw).strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"{what}: {text!r} is not a number") from None
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{what}: {text} is not a number of 0 or more")
    return value


def check_damage(min_val: float | None, max_val: float | None, where: str = "") -> None:
    """Refuse a damage bound outside 0..1 or a min above the max."""
    prefix = f"{where}: " if where else ""
    for what, value in (("damage min", min_val), ("damage max", max_val)):
        if value is not None:
            fraction(value, f"{prefix}{what}")
    if min_val is not None and max_val is not None and min_val > max_val:
        raise ValueError(f"{prefix}damage min {min_val:g} is above damage max {max_val:g}")


#: Bohemia's order of a <type>'s leading children; its <attachments> and
#: <cargo> groups follow them (all three stock files).
TYPE_CHILD_ORDER = ["hoarder", "tag", "damage"]


def _insert_child(type_elem: ET.Element, tag: str) -> ET.Element:
    """A new *tag* child where Bohemia puts it: after the leading children
    before it in `TYPE_CHILD_ORDER`, ahead of the groups."""
    rank = TYPE_CHILD_ORDER.index(tag)
    index = 0
    for i, child in enumerate(type_elem):
        if isinstance(child.tag, str) and child.tag in TYPE_CHILD_ORDER[:rank + 1]:
            index = i + 1
    elem = ET.Element(tag)
    type_elem.insert(index, elem)
    return elem


def _parse_group(elem: ET.Element) -> AttachmentGroup:
    """Parse an <attachments> or <cargo> element into an AttachmentGroup."""
    tag = elem.tag  # "attachments" or "cargo"

    chance = _opt_number(elem.get("chance"))

    preset = elem.get("preset")

    items: list[AttachmentItem] = []
    for item_elem in elem.findall("item"):
        item_name = item_elem.get("name", "")
        item_chance = _opt_number(item_elem.get("chance"))
        items.append(AttachmentItem(name=item_name, chance=item_chance, element=item_elem))

    return AttachmentGroup(tag=tag, chance=chance, preset=preset, items=items, element=elem)


def parse_spawnabletypes_file(path: Path) -> SpawnableTypesFile:
    """Parse cfgspawnabletypes.xml, extract SpawnableType for each <type>."""
    # Comments kept: a Mod XML link can splice a block into this file, and a
    # save without its START/END MOD markers strands the block in it.
    tree = load_xml(path, preserve_comments=True)
    root = tree.getroot()
    entries: list[SpawnableType] = []

    for type_elem in root.findall("type"):
        name = type_elem.get("name", "")

        # <damage min="..." max="..." />
        damage_elem = type_elem.find("damage")
        damage_min: float | None = None
        damage_max: float | None = None
        if damage_elem is not None:
            damage_min = _opt_number(damage_elem.get("min"))
            damage_max = _opt_number(damage_elem.get("max"))

        # <hoarder />
        has_hoarder = type_elem.find("hoarder") is not None

        # <tag name="..." />
        tag_elem = type_elem.find("tag")
        tag_name = tag_elem.get("name") if tag_elem is not None else None

        # <attachments> and <cargo> groups
        groups: list[AttachmentGroup] = []
        for child in type_elem:
            if child.tag in ("attachments", "cargo"):
                groups.append(_parse_group(child))

        entries.append(SpawnableType(
            name=name,
            damage_min=damage_min,
            damage_max=damage_max,
            has_hoarder=has_hoarder,
            tag_name=tag_name,
            attachment_groups=groups,
            element=type_elem,
            source_file=path,
        ))

    return SpawnableTypesFile(path=path, tree=tree, entries=entries)


def save_spawnabletypes_file(sf: SpawnableTypesFile) -> None:
    """Write modified tree back to disk."""
    save_xml(sf.tree, sf.path)


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def backup_spawnabletypes_file(path: Path) -> Path:
    """Create .backup copy (overwrites previous). Returns backup path."""
    return backup_file(path)


def restore_spawnabletypes_file(path: Path) -> bool:
    """Restore from .backup. Returns True if restored."""
    return restore_file(path)


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------

def add_spawnable_type(sf: SpawnableTypesFile, name: str) -> SpawnableType:
    """Append an empty <type name=…> to the file and return its entry."""
    elem = ET.SubElement(sf.tree.getroot(), "type")
    elem.set("name", name)
    entry = SpawnableType(
        name=name, damage_min=None, damage_max=None, has_hoarder=False, tag_name=None,
        element=elem, source_file=sf.path,
    )
    sf.entries.append(entry)
    return entry


def remove_spawnable_type(sf: SpawnableTypesFile, entry: SpawnableType) -> None:
    """Remove a <type> and its entry; the comments around it stay."""
    sf.tree.getroot().remove(entry.element)
    sf.entries.remove(entry)


def rename_spawnable_type(entry: SpawnableType, name: str) -> None:
    """Give a type another class name; everything inside it stays."""
    entry.element.set("name", name)
    entry.name = name


def set_damage(entry: SpawnableType, min_val: float | None, max_val: float | None) -> None:
    """Update or create/remove the <damage> element; a bound given as None is
    left out of it. Raises ValueError (nothing changed) for a bound outside
    0..1 or a min above the max."""
    check_damage(min_val, max_val, entry.name)
    existing = entry.element.find("damage")

    if min_val is None and max_val is None:
        # Remove damage element if it exists
        if existing is not None:
            entry.element.remove(existing)
        entry.damage_min = None
        entry.damage_max = None
        return

    if existing is None:
        existing = _insert_child(entry.element, "damage")

    for attr, value in (("min", min_val), ("max", max_val)):
        if value is None:
            existing.attrib.pop(attr, None)
        else:
            _set_number(existing, attr, value)

    entry.damage_min = min_val
    entry.damage_max = max_val


def set_attachment_chance(group: AttachmentGroup, chance: float) -> None:
    """Update the group's chance attribute (0..1)."""
    fraction(chance, "chance")
    _set_number(group.element, "chance", chance)
    group.chance = chance


def set_item_chance(item: AttachmentItem, chance: float) -> None:
    """Update an item's chance attribute (0 or more, `item_weight`)."""
    item_weight(chance, f"{item.name} chance")
    _set_number(item.element, "chance", chance)
    item.chance = chance


def add_attachment_item(group: AttachmentGroup, name: str, chance: float) -> AttachmentItem:
    """Create and append a new <item> element to a group."""
    item_weight(chance, f"{name} chance")
    item_elem = ET.SubElement(group.element, "item")
    item_elem.set("name", name)
    item_elem.set("chance", _number_text(chance))
    item = AttachmentItem(name=name, chance=chance, element=item_elem)
    group.items.append(item)
    return item


def remove_attachment_item(group: AttachmentGroup, item: AttachmentItem) -> None:
    """Remove an item element from a group."""
    group.element.remove(item.element)
    group.items.remove(item)


def add_attachment_group(
    entry: SpawnableType,
    tag: str,
    chance: float | None,
    preset: str | None,
) -> AttachmentGroup:
    """Create and append a new <attachments> or <cargo> element."""
    if chance is not None:
        fraction(chance, f"{tag} chance")
    group_elem = ET.SubElement(entry.element, tag)
    if chance is not None:
        group_elem.set("chance", _number_text(chance))
    if preset:
        group_elem.set("preset", preset)
    group = AttachmentGroup(tag=tag, chance=chance, preset=preset, items=[], element=group_elem)
    entry.attachment_groups.append(group)
    return group


def remove_attachment_group(entry: SpawnableType, group: AttachmentGroup) -> None:
    """Remove a group element from a type entry."""
    entry.element.remove(group.element)
    entry.attachment_groups.remove(group)


def set_hoarder(entry: SpawnableType, enabled: bool) -> None:
    """Add or remove <hoarder /> element."""
    existing = entry.element.find("hoarder")
    if enabled and existing is None:
        _insert_child(entry.element, "hoarder")
    elif not enabled and existing is not None:
        entry.element.remove(existing)
    entry.has_hoarder = enabled


def set_tag(entry: SpawnableType, name: str | None) -> None:
    """Set or remove <tag> element."""
    existing = entry.element.find("tag")
    if name:
        if existing is None:
            existing = _insert_child(entry.element, "tag")
        if existing.get("name") != name:
            existing.set("name", name)
    else:
        if existing is not None:
            entry.element.remove(existing)
    entry.tag_name = name


# Field help text (shared with the web UI, which strips the Rich
# console markup; see app/web/editors/_help.py).
FIELD_DESCRIPTIONS: dict[str, str] = {
    "damage_min": "Least damage the item spawns with, as a fraction from 0 to 1 (0.35 is 35%; 1 is ruined). Set both bounds or neither.",
    "damage_max": "Most damage the item spawns with, as a fraction from 0 to 1 (0.35 is 35%; 1 is ruined). Not below the min.",
    "hoarder": "Enable for storage containers (barrels, tents, stashes, crates) so the Central Economy counts items stored inside toward the limits in types.xml. Off for ordinary items that aren't meant as player hoards.",
    "tag": "Optional placement tag (e.g. 'shelves', 'floor') that hints where this item fits when it spawns inside a container. Rarely used in vanilla — leave blank unless a mod requires it.",
    "group_tag": "Group type: 'attachments' or 'cargo'.",
    "group_chance": "Probability that this attachment/cargo group is used when the item spawns, from 0 to 1 (0.30 is 30%).",
    "group_preset": "Preset name from cfgRandomPresets.xml to use instead of inline items.",
    "item_name": "Classname of the item that can appear in this group.",
    "item_chance": "How likely this item is against the other items of its group, usually from 0 to 1 (0.30 is 30%). Bohemia's Sakhal file writes them as numbers that add up to 100 instead (40.00, 30.00, ...), which DayZ also reads.",
}
