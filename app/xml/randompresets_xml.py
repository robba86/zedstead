"""Random presets XML parsing, editing, and backup/restore."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from app.xml.xml_utils import backup_file, load_xml, restore_file, save_xml


# ---------------------------------------------------------------------------
# Field descriptions
# ---------------------------------------------------------------------------

FIELD_DESCRIPTIONS: dict[str, str] = {
    "kind": (
        "cargo presets fill a container's inventory; attachments presets fill its "
        "attachment slots (optics, hats, bags). cfgspawnabletypes.xml names a preset "
        "from the group of the same kind: <cargo preset=\"…\"/> or <attachments preset=\"…\"/>."
    ),
    "preset_name": (
        "Name identifier for this random cargo preset, referenced by items in "
        "cfgspawnabletypes.xml. When a spawnable type lists this preset, the CE uses "
        "it to determine what items can appear inside the container's cargo."
    ),
    "preset_chance": (
        "Probability (0.0-1.0) that this preset is selected when a matching "
        "container spawns. A value of 1.0 means the preset is always used. Lower "
        "values make this preset's contents rarer across the map."
    ),
    "item_name": (
        "Classname of the item that can appear in this preset's cargo. Must match "
        "an existing item classname from types.xml or a mod. The CE draws from the "
        "preset's item list to fill containers when they spawn."
    ),
    "item_chance": (
        "How likely this item is against the other items of its preset, usually "
        "from 0.0 to 1.0. Bohemia's Sakhal cfgspawnabletypes.xml writes the items of "
        "an attachments group as numbers that add up to 100 (40.00, 30.00, ...), "
        "which DayZ also reads, so a number above 1 is taken here too."
    ),
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PresetItem:
    """One <item> element inside a <cargo> preset."""
    name: str
    chance: str
    element: ET.Element = field(default_factory=lambda: ET.Element("item"))


#: The two kinds of preset, by element: Bohemia's three files hold both
#: (Chernarus: 35 <attachments> presets such as "optics" beside the <cargo> ones).
PRESET_KINDS = ("cargo", "attachments")


@dataclass
class CargoPreset:
    """One <cargo> or <attachments> preset from cfgRandomPresets.xml."""
    name: str
    chance: str
    items: list[PresetItem] = field(default_factory=list)
    element: ET.Element = field(default_factory=lambda: ET.Element("cargo"))
    kind: str = "cargo"


@dataclass
class RandomPresetsFile:
    """Parsed cfgRandomPresets.xml file."""
    path: Path
    tree: ET.ElementTree
    presets: list[CargoPreset] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_randompresets_path(mission_dir: Path) -> Path | None:
    """Return cfgRandomPresets.xml if it exists (check root + db/)."""
    for p in [
        mission_dir / "cfgRandomPresets.xml",
        mission_dir / "cfgrandompresets.xml",
        mission_dir / "db" / "cfgRandomPresets.xml",
        mission_dir / "db" / "cfgrandompresets.xml",
    ]:
        if p.is_file():
            return p
    return None


# ---------------------------------------------------------------------------
# Parse & save
# ---------------------------------------------------------------------------

def parse_randompresets_file(path: Path) -> RandomPresetsFile:
    """Parse cfgRandomPresets.xml.

    Comments are kept: the file is a direct-merge target, and a save without
    them strands every mod's presets outside their START/END MOD markers.
    """
    tree = load_xml(path, preserve_comments=True)
    root = tree.getroot()
    presets: list[CargoPreset] = []

    for cargo_elem in root:
        if cargo_elem.tag not in PRESET_KINDS:
            continue
        items: list[PresetItem] = []
        for item_elem in cargo_elem.findall("item"):
            items.append(PresetItem(
                name=item_elem.get("name", ""),
                chance=item_elem.get("chance", "0"),
                element=item_elem,
            ))
        presets.append(CargoPreset(
            name=cargo_elem.get("name", ""),
            chance=cargo_elem.get("chance", "0"),
            items=items,
            element=cargo_elem,
            kind=cargo_elem.tag,
        ))

    return RandomPresetsFile(path=path, tree=tree, presets=presets)


def save_randompresets_file(rpf: RandomPresetsFile) -> None:
    """Write modified tree back to disk."""
    save_xml(rpf.tree, rpf.path)


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def backup_randompresets_file(path: Path) -> Path:
    return backup_file(path)


def restore_randompresets_file(path: Path) -> bool:
    return restore_file(path)


# ---------------------------------------------------------------------------
# CRUD — presets
# ---------------------------------------------------------------------------

def chance_text(raw, what: str) -> str:
    """A chance as the file writes it: a number from 0 to 1, as typed.

    Every chance in Bohemia's files is a fraction (0.15, 1.00); "50%", "x",
    NaN and 50 were written into the file as they were typed."""
    text = "" if raw is None else str(raw).strip()
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"{what}: {text or 'an empty chance'!r} is not a number from 0 to 1") from None
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{what}: {text} is outside 0 to 1 (0.15 is 15%)")
    return text


def item_chance_text(raw, what: str) -> str:
    """An item's chance as the file writes it: a number of 0 or more, as
    typed. Not a fraction: within a group the items' chances weigh them against
    each other (`spawnabletypes_xml.item_weight`, Sakhal's 40.00)."""
    from app.xml.spawnabletypes_xml import item_weight

    text = "" if raw is None else str(raw).strip()
    if not text:
        raise ValueError(f"{what}: an empty chance is not a number")
    item_weight(text, what)
    return text


def same_chance(old: str, new: str) -> bool:
    """*old* and *new* are the same number (`0.2` is `0.20`)."""
    try:
        return float(old) == float(new)
    except (TypeError, ValueError):
        return old == new


def add_preset(rpf: RandomPresetsFile, name: str, chance: str, kind: str = "cargo") -> CargoPreset:
    """Add a new <cargo> or <attachments> preset at the end of the file (never
    inside a mod's START/END MOD block), `chance` before `name` as Bohemia
    writes them."""
    if kind not in PRESET_KINDS:
        raise ValueError(f"A preset is cargo or attachments, not {kind!r}")
    chance = chance_text(chance, f"Preset {name}")
    root = rpf.tree.getroot()
    elem = ET.Element(kind)
    elem.set("chance", chance)
    elem.set("name", name)
    root.append(elem)
    preset = CargoPreset(name=name, chance=chance, items=[], element=elem, kind=kind)
    rpf.presets.append(preset)
    return preset


def add_cargo_preset(rpf: RandomPresetsFile, name: str, chance: str) -> CargoPreset:
    """Add a new <cargo> preset."""
    return add_preset(rpf, name, chance, "cargo")


def remove_cargo_preset(rpf: RandomPresetsFile, preset: CargoPreset) -> None:
    """Remove a <cargo> element."""
    root = rpf.tree.getroot()
    root.remove(preset.element)
    rpf.presets.remove(preset)


def set_preset_chance(preset: CargoPreset, chance: str) -> None:
    """Update chance attribute on a preset (a number from 0 to 1)."""
    chance = chance_text(chance, f"Preset {preset.name}")
    preset.element.set("chance", chance)
    preset.chance = chance


# ---------------------------------------------------------------------------
# CRUD — items
# ---------------------------------------------------------------------------

def add_item_to_preset(preset: CargoPreset, name: str, chance: str) -> PresetItem:
    """Add an <item> to a preset."""
    chance = item_chance_text(chance, f"Item {name}")
    elem = ET.SubElement(preset.element, "item")
    elem.set("name", name)
    elem.set("chance", chance)
    item = PresetItem(name=name, chance=chance, element=elem)
    preset.items.append(item)
    return item


def remove_item_from_preset(preset: CargoPreset, item: PresetItem) -> None:
    """Remove an <item> from a <cargo> preset."""
    preset.element.remove(item.element)
    preset.items.remove(item)


def set_item_name(item: PresetItem, name: str) -> None:
    """Rename an item where it stands."""
    item.element.set("name", name)
    item.name = name


def set_item_chance(item: PresetItem, chance: str) -> None:
    """Update chance attribute on an item (a number of 0 or more)."""
    chance = item_chance_text(chance, f"Item {item.name}")
    item.element.set("chance", chance)
    item.chance = chance
