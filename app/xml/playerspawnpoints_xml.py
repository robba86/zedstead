"""Player spawn points XML parsing, editing, and backup/restore.

Handles cfgPlayerSpawnPoints.xml with three sections: fresh, hop, travel.
Each section has spawn_params, generator_params, group_params, and
generator_posbubbles containing <group> elements with <pos> children.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from app.xml.xml_utils import keep_first_backup, load_xml, save_xml


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SECTION_NAMES = ["fresh", "hop", "travel"]

SPAWN_PARAM_FIELDS = [
    "min_dist_infected", "max_dist_infected",
    "min_dist_player", "max_dist_player",
    "min_dist_static", "max_dist_static",
    "min_dist_trigger", "max_dist_trigger",
]

GENERATOR_PARAM_FIELDS = [
    "grid_density", "grid_width", "grid_height",
    "min_dist_static", "max_dist_static",
    "min_steepness", "max_steepness",
]

GROUP_PARAM_FIELDS = [
    "enablegroups", "groups_as_regular", "lifetime", "counter",
]

SPAWN_PARAM_DESCRIPTIONS: dict[str, str] = {
    "min_dist_infected": "Min distance from infected (m)",
    "max_dist_infected": "Max distance from infected (m)",
    "min_dist_player": "Min distance from players (m)",
    "max_dist_player": "Max distance from players (m)",
    "min_dist_static": "Min distance from static objects (m)",
    "max_dist_static": "Max distance from static objects (m)",
    "min_dist_trigger": "Min distance from triggers (m)",
    "max_dist_trigger": "Max distance from triggers (m)",
}

GENERATOR_PARAM_DESCRIPTIONS: dict[str, str] = {
    "grid_density": "Grid point density",
    "grid_width": "Grid width (m)",
    "grid_height": "Grid height (m)",
    "min_dist_static": "Min distance from static objects (m)",
    "max_dist_static": "Max distance from static objects (m)",
    "min_steepness": "Min terrain steepness (degrees)",
    "max_steepness": "Max terrain steepness (degrees)",
}

GROUP_PARAM_DESCRIPTIONS: dict[str, str] = {
    "enablegroups": "Enable spawn groups (true/false)",
    "groups_as_regular": "Treat groups as regular spawns (true/false)",
    "lifetime": "Group lifetime in seconds (-1 = disabled)",
    "counter": "Group counter (-1 = disabled)",
}

SECTION_DESCRIPTIONS: dict[str, str] = {
    "fresh": "Newly created characters",
    "hop": "Server hoppers",
    "travel": "Travellers",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

# A field is the element's text, or None when the file does not have it:
# DayZ then uses its own default, and the map editor shows the field empty.
# A made-up default here was sent back by the Players tab's save and written
# into the file (`<min_dist_trigger>0</min_dist_trigger>` on Chernarus).


@dataclass
class SpawnParams:
    """The <spawn_params> block — 8 distance fields."""
    min_dist_infected: str | None = None
    max_dist_infected: str | None = None
    min_dist_player: str | None = None
    max_dist_player: str | None = None
    min_dist_static: str | None = None
    max_dist_static: str | None = None
    min_dist_trigger: str | None = None
    max_dist_trigger: str | None = None
    element: ET.Element | None = field(default=None, repr=False)


@dataclass
class GeneratorParams:
    """The <generator_params> block — grid + distance + steepness."""
    grid_density: str | None = None
    grid_width: str | None = None
    grid_height: str | None = None
    min_dist_static: str | None = None
    max_dist_static: str | None = None
    min_steepness: str | None = None
    max_steepness: str | None = None
    element: ET.Element | None = field(default=None, repr=False)


@dataclass
class GroupParams:
    """The <group_params> block — group spawning behaviour."""
    enablegroups: str | None = None
    groups_as_regular: str | None = None
    lifetime: str | None = None
    counter: str | None = None
    element: ET.Element | None = field(default=None, repr=False)


@dataclass
class SpawnPos:
    """One <pos> inside a <group>."""
    x: str
    z: str
    element: ET.Element = field(default_factory=lambda: ET.Element("pos"), repr=False)


@dataclass
class PosGroup:
    """One <group> inside <generator_posbubbles>."""
    name: str
    positions: list[SpawnPos] = field(default_factory=list)
    element: ET.Element = field(default_factory=lambda: ET.Element("group"), repr=False)


@dataclass
class SpawnSection:
    """One of fresh/hop/travel sections."""
    name: str
    spawn_params: SpawnParams = field(default_factory=SpawnParams)
    generator_params: GeneratorParams = field(default_factory=GeneratorParams)
    group_params: GroupParams = field(default_factory=GroupParams)
    groups: list[PosGroup] = field(default_factory=list)
    element: ET.Element | None = field(default=None, repr=False)


@dataclass
class PlayerSpawnPointsFile:
    """Parsed cfgPlayerSpawnPoints.xml file."""
    path: Path
    tree: ET.ElementTree
    sections: dict[str, SpawnSection] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_spawnpoints_path(mission_dir: Path) -> Path | None:
    """Return cfgPlayerSpawnPoints.xml if it exists (check root + db/)."""
    for p in [
        mission_dir / "cfgPlayerSpawnPoints.xml",
        mission_dir / "cfgplayerspawnpoints.xml",
        mission_dir / "db" / "cfgPlayerSpawnPoints.xml",
        mission_dir / "db" / "cfgplayerspawnpoints.xml",
    ]:
        if p.is_file():
            return p
    return None


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------

def _parse_text_fields(
    parent_elem: ET.Element | None,
    fields: list[str],
    obj: SpawnParams | GeneratorParams | GroupParams,
) -> None:
    """Read child element text into dataclass attributes."""
    if parent_elem is None:
        return
    for f in fields:
        child = parent_elem.find(f)
        if child is not None and child.text:
            setattr(obj, f, child.text.strip())


def _parse_groups(gen_bubbles: ET.Element | None) -> list[PosGroup]:
    """Parse <group> children from a <generator_posbubbles> element."""
    if gen_bubbles is None:
        return []
    groups: list[PosGroup] = []
    for group_elem in gen_bubbles.findall("group"):
        positions: list[SpawnPos] = []
        for pos_elem in group_elem.findall("pos"):
            positions.append(SpawnPos(
                x=pos_elem.get("x", "0"),
                z=pos_elem.get("z", "0"),
                element=pos_elem,
            ))
        groups.append(PosGroup(
            name=group_elem.get("name", ""),
            positions=positions,
            element=group_elem,
        ))

    # Some maps (e.g. Namalsk) have flat <pos> directly in generator_posbubbles
    direct_positions: list[SpawnPos] = []
    for pos_elem in gen_bubbles.findall("pos"):
        direct_positions.append(SpawnPos(
            x=pos_elem.get("x", "0"),
            z=pos_elem.get("z", "0"),
            element=pos_elem,
        ))
    if direct_positions:
        groups.append(PosGroup(
            name="Spawn Points",
            positions=direct_positions,
            element=gen_bubbles,
        ))

    return groups


def _parse_section(section_elem: ET.Element, name: str) -> SpawnSection:
    """Parse one section (fresh/hop/travel)."""
    # Spawn params
    sp = SpawnParams()
    sp_elem = section_elem.find("spawn_params")
    if sp_elem is not None:
        sp.element = sp_elem
    _parse_text_fields(sp_elem, SPAWN_PARAM_FIELDS, sp)

    # Generator params
    gp = GeneratorParams()
    gp_elem = section_elem.find("generator_params")
    if gp_elem is not None:
        gp.element = gp_elem
    _parse_text_fields(gp_elem, GENERATOR_PARAM_FIELDS, gp)

    # Group params
    grp = GroupParams()
    grp_elem = section_elem.find("group_params")
    if grp_elem is not None:
        grp.element = grp_elem
    _parse_text_fields(grp_elem, GROUP_PARAM_FIELDS, grp)

    # Groups from generator_posbubbles
    gen_bubbles = section_elem.find("generator_posbubbles")
    groups = _parse_groups(gen_bubbles)

    return SpawnSection(
        name=name,
        spawn_params=sp,
        generator_params=gp,
        group_params=grp,
        groups=groups,
        element=section_elem,
    )


# ---------------------------------------------------------------------------
# Parse & save
# ---------------------------------------------------------------------------

def parse_spawnpoints_file(path: Path) -> PlayerSpawnPointsFile:
    """Parse cfgPlayerSpawnPoints.xml."""
    # Comments kept: the map editor saves this tree, and Sakhal's stock file
    # explains every parameter in one.
    tree = load_xml(path, preserve_comments=True)
    root = tree.getroot()

    sections: dict[str, SpawnSection] = {}
    for section_name in SECTION_NAMES:
        section_elem = root.find(section_name)
        if section_elem is not None:
            sections[section_name] = _parse_section(section_elem, section_name)

    return PlayerSpawnPointsFile(path=path, tree=tree, sections=sections)


def save_spawnpoints_file(spf: PlayerSpawnPointsFile, *, snapshot: bool = True) -> None:
    """Write modified tree back to disk."""
    save_xml(spf.tree, spf.path, snapshot=snapshot)


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def backup_spawnpoints_file(path: Path) -> Path:
    """Create a .backup copy unless one exists. Returns backup path."""
    return keep_first_backup(path)




# ---------------------------------------------------------------------------
# CRUD — section params
# ---------------------------------------------------------------------------

# A section's blocks, in the order Bohemia's file holds them.
_SECTION_BLOCKS = ("spawn_params", "generator_params", "group_params", "generator_posbubbles")


def _add_block(section_elem: ET.Element, tag: str) -> ET.Element:
    """Create a section's missing params block, before the blocks that follow it."""
    later = _SECTION_BLOCKS[_SECTION_BLOCKS.index(tag) + 1:]
    elem = ET.Element(tag)
    for index, child in enumerate(section_elem):
        if child.tag in later:
            section_elem.insert(index, elem)
            return elem
    section_elem.append(elem)
    return elem


def _set_param(
    section: SpawnSection,
    tag: str,
    params_obj: SpawnParams | GeneratorParams | GroupParams,
    field_name: str,
    value: str,
) -> None:
    """Update a parameter on a params dataclass + its XML element."""
    setattr(params_obj, field_name, value)
    if params_obj.element is None:
        if section.element is None:
            return
        # A section without this block: created, since the save writes the
        # tree and the value would otherwise be saved nowhere.
        params_obj.element = _add_block(section.element, tag)
    child = params_obj.element.find(field_name)
    if child is None:
        child = ET.SubElement(params_obj.element, field_name)
    child.text = value


def set_spawn_param(section: SpawnSection, field_name: str, value: str) -> None:
    """Update a spawn_params field."""
    _set_param(section, "spawn_params", section.spawn_params, field_name, value)


def set_generator_param(section: SpawnSection, field_name: str, value: str) -> None:
    """Update a generator_params field."""
    _set_param(section, "generator_params", section.generator_params, field_name, value)


def set_group_param(section: SpawnSection, field_name: str, value: str) -> None:
    """Update a group_params field."""
    _set_param(section, "group_params", section.group_params, field_name, value)


# ---------------------------------------------------------------------------
# CRUD — groups
# ---------------------------------------------------------------------------

def add_group(section: SpawnSection, name: str) -> PosGroup:
    """Add a new <group> to a section's generator_posbubbles."""
    if section.element is None:
        raise ValueError(f"Section '{section.name}' has no XML element")

    gen_bubbles = section.element.find("generator_posbubbles")
    if gen_bubbles is None:
        gen_bubbles = ET.SubElement(section.element, "generator_posbubbles")

    elem = ET.SubElement(gen_bubbles, "group")
    elem.set("name", name)
    group = PosGroup(name=name, positions=[], element=elem)
    section.groups.append(group)
    return group


def remove_group(section: SpawnSection, group: PosGroup) -> None:
    """Remove a <group> from a section's generator_posbubbles."""
    if section.element is None:
        return
    gen_bubbles = section.element.find("generator_posbubbles")
    if gen_bubbles is not None and group.element is gen_bubbles:
        # The flat layout's "Spawn Points": its <pos> elements are the group.
        for pos in group.positions:
            gen_bubbles.remove(pos.element)
    elif gen_bubbles is not None:
        gen_bubbles.remove(group.element)
    section.groups.remove(group)


def add_pos_to_group(group: PosGroup, x: str, z: str) -> SpawnPos:
    """Add a <pos> to a group."""
    elem = ET.SubElement(group.element, "pos")
    elem.set("x", x)
    elem.set("z", z)
    pos = SpawnPos(x=x, z=z, element=elem)
    group.positions.append(pos)
    return pos


def remove_pos_from_group(group: PosGroup, pos: SpawnPos) -> None:
    """Remove a <pos> from a group."""
    group.element.remove(pos.element)
    group.positions.remove(pos)


def update_pos_in_group(pos: SpawnPos, x: str, z: str) -> None:
    """Update a position's coordinates in both the dataclass and XML element."""
    pos.x = x
    pos.z = z
    pos.element.set("x", x)
    pos.element.set("z", z)
