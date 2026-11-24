"""cfglimitsdefinition.xml + cfglimitsdefinitionuser.xml parser/writer."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from app.xml.xml_utils import backup_file, load_xml, restore_file, save_xml


# ---------------------------------------------------------------------------
# Dataclasses — cfglimitsdefinition.xml
# ---------------------------------------------------------------------------

@dataclass
class LimitsEntry:
    """One <category>, <tag>, <usage>, or <value> element."""
    name: str
    element: ET.Element


@dataclass
class LimitsDefinitionFile:
    """Parsed cfglimitsdefinition.xml."""
    path: Path
    tree: ET.ElementTree
    categories: list[LimitsEntry] = field(default_factory=list)
    tags: list[LimitsEntry] = field(default_factory=list)
    usages: list[LimitsEntry] = field(default_factory=list)
    values: list[LimitsEntry] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Dataclasses — cfglimitsdefinitionuser.xml
# ---------------------------------------------------------------------------

@dataclass
class UserGroup:
    """One <user> element inside a <usageflags> or <valueflags> section."""
    name: str
    members: list[str]
    element: ET.Element


@dataclass
class LimitsDefinitionUserFile:
    """Parsed cfglimitsdefinitionuser.xml."""
    path: Path
    tree: ET.ElementTree
    usage_groups: list[UserGroup] = field(default_factory=list)
    value_groups: list[UserGroup] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_limitsdefinition_path(mission_dir: Path) -> Path | None:
    """Return path to cfglimitsdefinition.xml if it exists (case-insensitive)."""
    for p in mission_dir.iterdir():
        if p.is_file() and p.name.lower() == "cfglimitsdefinition.xml":
            return p
    return None


def get_limitsdefinitionuser_path(mission_dir: Path) -> Path | None:
    """Return path to cfglimitsdefinitionuser.xml if it exists (case-insensitive)."""
    for p in mission_dir.iterdir():
        if p.is_file() and p.name.lower() == "cfglimitsdefinitionuser.xml":
            return p
    return None


# ---------------------------------------------------------------------------
# Parse cfglimitsdefinition.xml
# ---------------------------------------------------------------------------

def parse_limitsdefinition(path: Path) -> LimitsDefinitionFile:
    """Parse cfglimitsdefinition.xml.

    `preserve_comments` is not optional here. Both this file and its `user`
    sibling are direct mod-merge targets (`ALL_MERGE_CONFIGS`), so a mod's
    entries arrive wrapped in `<!-- START MOD: @X -->` / `<!-- END MOD: @X -->`
    markers that `xml_merge.remove_mod_xml_blocks` matches **as text**. Parsing
    without them and saving dropped the markers, which left the mod's entries
    in the file with nothing able to take them out again — measured
    2026 Q3, through the Limits Definition table editor, which is live.
    `eventgroups_xml` and `eventspawns_xml` pass it for the same reason; these
    two were the merge targets that did not.

    A comment node's tag is a callable, so it never matches the string tags in
    `findall` below and no entry list picks one up.
    """
    tree = load_xml(path, preserve_comments=True)
    root = tree.getroot()

    categories = [LimitsEntry(e.get("name", ""), e) for e in root.findall(".//category")]
    tags = [LimitsEntry(e.get("name", ""), e) for e in root.findall(".//tag")]
    usages = [LimitsEntry(e.get("name", ""), e) for e in root.findall(".//usage")]
    values = [LimitsEntry(e.get("name", ""), e) for e in root.findall(".//value")]

    return LimitsDefinitionFile(
        path=path, tree=tree,
        categories=categories, tags=tags, usages=usages, values=values,
    )


def save_limitsdefinition(ldf: LimitsDefinitionFile) -> None:
    """Write cfglimitsdefinition.xml back to disk."""
    save_xml(ldf.tree, ldf.path)


# ---------------------------------------------------------------------------
# Parse cfglimitsdefinitionuser.xml
# ---------------------------------------------------------------------------

#: The attribute naming a member inside a <user> group. Bohemia writes `name`
#: — all three stock missions, 78 elements, not one `user`. The code read
#: `user` until 2026 Q3 and so returned an empty member list for every real
#: file; `user` stays accepted on read because a mission that already has it
#: should not silently lose its groups, but nothing writes it any more.
_MEMBER_ATTRS = ("name", "user")


def _member_name(elem: ET.Element) -> str:
    for attr in _MEMBER_ATTRS:
        value = elem.get(attr)
        if value:
            return value
    return ""


def parse_limitsdefinitionuser(path: Path) -> LimitsDefinitionUserFile:
    """Parse cfglimitsdefinitionuser.xml.

    `preserve_comments=True` for the reason spelled out on
    `parse_limitsdefinition` above. A comment inside a `<user>` group is
    skipped by `_member_name`, which requires a naming attribute.
    """
    tree = load_xml(path, preserve_comments=True)
    root = tree.getroot()

    usage_groups: list[UserGroup] = []
    value_groups: list[UserGroup] = []

    for section_tag, target in [("usageflags", usage_groups), ("valueflags", value_groups)]:
        section = root.find(section_tag)
        if section is None:
            continue
        for user_elem in section.findall("user"):
            name = user_elem.get("name", "")
            members = [
                member for e in user_elem.findall("*")
                if (member := _member_name(e))
            ]
            target.append(UserGroup(name=name, members=members, element=user_elem))

    return LimitsDefinitionUserFile(
        path=path, tree=tree,
        usage_groups=usage_groups, value_groups=value_groups,
    )


def limits_names(mission_dir: Path) -> dict[str, list[str]]:
    """The category, tag, usage and value names cfglimitsdefinition.xml
    defines, for an editor's choices. Empty lists for a mission without the
    file or with one that cannot be read: a list of suggestions must never
    stop the editor that shows it."""
    names: dict[str, list[str]] = {"category": [], "tag": [], "usage": [], "value": []}
    try:
        path = get_limitsdefinition_path(mission_dir) if mission_dir.is_dir() else None
        if path is None:
            return names
        ldf = parse_limitsdefinition(path)
    except (ET.ParseError, OSError, ValueError):
        return names
    for key, entries in (
        ("category", ldf.categories), ("tag", ldf.tags), ("usage", ldf.usages), ("value", ldf.values),
    ):
        names[key] = [e.name for e in entries if e.name]
    return names


def save_limitsdefinitionuser(ludf: LimitsDefinitionUserFile) -> None:
    """Write cfglimitsdefinitionuser.xml back to disk."""
    save_xml(ludf.tree, ludf.path)


# ---------------------------------------------------------------------------
# CRUD — cfglimitsdefinition.xml
# ---------------------------------------------------------------------------


#: The section each kind of entry lives in, in Bohemia's order. The root
#: element *is* `<lists>`; an entry goes into its section, never straight
#: under the root, where the game does not look for it.
_SECTION_TAGS = (
    ("category", "categories"),
    ("tag", "tags"),
    ("usage", "usageflags"),
    ("value", "valueflags"),
)


def _section(ldf: LimitsDefinitionFile, entry_tag: str) -> ET.Element:
    """The section element *entry_tag* belongs in, created in Bohemia's order
    when the file lacks it."""
    root = ldf.tree.getroot()
    order = [section for _, section in _SECTION_TAGS]
    section_tag = dict(_SECTION_TAGS)[entry_tag]
    found = root.find(section_tag)
    if found is not None:
        return found
    later = set(order[order.index(section_tag) + 1:])
    children = list(root)
    index = next((i for i, c in enumerate(children) if c.tag in later), len(children))
    elem = ET.Element(section_tag)
    root.insert(index, elem)
    return elem


def _add(ldf: LimitsDefinitionFile, entry_tag: str, name: str, target: list) -> LimitsEntry:
    elem = ET.SubElement(_section(ldf, entry_tag), entry_tag, name=name)
    entry = LimitsEntry(name=name, element=elem)
    target.append(entry)
    return entry


def add_category(ldf: LimitsDefinitionFile, name: str) -> LimitsEntry:
    """Add a new category entry to <categories>."""
    return _add(ldf, "category", name, ldf.categories)


def add_tag(ldf: LimitsDefinitionFile, name: str) -> LimitsEntry:
    """Add a new tag entry to <tags>."""
    return _add(ldf, "tag", name, ldf.tags)


def add_usage(ldf: LimitsDefinitionFile, name: str) -> LimitsEntry:
    """Add a new usage entry to <usageflags>."""
    return _add(ldf, "usage", name, ldf.usages)


def add_value(ldf: LimitsDefinitionFile, name: str) -> LimitsEntry:
    """Add a new value entry to <valueflags>."""
    return _add(ldf, "value", name, ldf.values)


def remove_entry(ldf: LimitsDefinitionFile, section: str, entry: LimitsEntry) -> None:
    """Remove an entry from the appropriate section list and XML tree."""
    root = ldf.tree.getroot()
    # Find parent and remove element
    for parent in root.iter():
        if entry.element in list(parent):
            parent.remove(entry.element)
            break
    # Remove from dataclass list
    target = getattr(ldf, section, [])
    if entry in target:
        target.remove(entry)


# ---------------------------------------------------------------------------
# CRUD — cfglimitsdefinitionuser.xml
# ---------------------------------------------------------------------------

def add_user_group(
    ludf: LimitsDefinitionUserFile,
    section: str,
    name: str,
    members: list[str],
) -> UserGroup:
    """Add a user group to usageflags or valueflags section."""
    root = ludf.tree.getroot()
    section_tag = "usageflags" if section == "usage" else "valueflags"
    section_elem = root.find(section_tag)
    if section_elem is None:
        section_elem = ET.SubElement(root, section_tag)

    user_elem = ET.SubElement(section_elem, "user", name=name)
    for member in members:
        ET.SubElement(user_elem, "usage" if section == "usage" else "value", name=member)

    group = UserGroup(name=name, members=members, element=user_elem)
    target = ludf.usage_groups if section == "usage" else ludf.value_groups
    target.append(group)
    return group


def remove_user_group(ludf: LimitsDefinitionUserFile, section: str, group: UserGroup) -> None:
    """Remove a user group."""
    root = ludf.tree.getroot()
    for parent in root.iter():
        if group.element in list(parent):
            parent.remove(group.element)
            break
    target = ludf.usage_groups if section == "usage" else ludf.value_groups
    if group in target:
        target.remove(group)


def edit_user_group(group: UserGroup, new_members: list[str]) -> None:
    """Update a group's member list, keeping whichever child tag it already had.

    The tag has to be read **before** the children are cleared. This used to
    hardcode `usage`, which rewrote every <valueflags> group as <usage> entries
    — the server then read a value group with no values. Prefer
    `edit_user_group_in_section` where the caller knows the section: a group
    that has been emptied has no child left to learn the tag from, and this
    falls back to `usage`.
    """
    existing = [c for c in group.element if isinstance(c.tag, str)]
    _set_members(group, existing[0].tag if existing else "usage", new_members)


def edit_user_group_in_section(
    group: UserGroup, section: str, new_members: list[str],
) -> None:
    """Update member list of a user group with explicit section."""
    _set_members(group, "usage" if section == "usage" else "value", new_members)


def _set_members(group: UserGroup, child_tag: str, new_members: list[str]) -> None:
    """Make *group*'s member elements *new_members*, changing only what differs.

    A member that stays keeps its element, and comments inside the group stay
    where they are: clearing every child dropped them on each member change.
    """
    elem = group.element
    old = [c for c in elem if isinstance(c.tag, str)]
    names = [_member_name(c) for c in old]
    ops = SequenceMatcher(a=names, b=new_members, autojunk=False).get_opcodes()
    for op, i1, i2, j1, j2 in reversed(ops):  # back to front, so indices hold
        if op == "equal":
            continue
        at = list(elem).index(old[i1]) if i1 < len(old) else (
            list(elem).index(old[-1]) + 1 if old else len(elem)
        )
        for child in old[i1:i2]:
            elem.remove(child)
        for k, member in enumerate(new_members[j1:j2]):
            new = ET.Element(child_tag, name=member)
            elem.insert(at + k, new)
    group.members = new_members


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def backup_limitsdefinition(path: Path) -> Path:
    return backup_file(path)


def restore_limitsdefinition(path: Path) -> bool:
    return restore_file(path)


def backup_limitsdefinitionuser(path: Path) -> Path:
    return backup_file(path)



# Field help text (shared with the web UI, which strips the Rich
# console markup; see app/web/editors/_help.py).
FIELD_DESCRIPTIONS: dict[str, str] = {
    "category": (
        "Coarse item-class bucket used by mapgroupproto.xml loot points to decide what kinds of items spawn "
        "at each loot position. Vanilla: tools, containers, clothes, lootdispatch, food, weapons, books, explosives. "
        "Each types.xml entry has exactly one <category>. Custom categories require matching mapgroupproto entries to take effect."
    ),
    "tag": (
        "Surface/placement classifier on loot points. Vanilla tags: floor, shelves, ground. Items use these "
        "alongside <category> to refine where they physically appear inside a building (a tin can on a shelf vs. on the floor)."
    ),
    "usage": (
        "Building usage flag. Items spawn in a building if at least one of the item's usages matches one of the building's usages (OR semantics). "
        "Vanilla Chernarus: Military, Police, Medic, Firefighter, Industrial, Farm, Coast, Town, Village, Hunting, Office, School, Prison, Lunapark, "
        "SeasonalEvent, ContaminatedArea, Historical. Sakhal adds Special and Underground; Livonia drops Lunapark and Historical."
    ),
    "value": (
        "Map-zone classification. Tiers are geographic areas painted on the map (mapgroupproto/area definitions), not item rarity. "
        "Tier1 = coastal/starter areas, Tier4 = deep-inland high-value zones, Unique = one-off locations. An item with "
        "<value name=\"Tier4\"/> only spawns in Tier4 zones. Vanilla: Tier1-4 + Unique on Chernarus/Sakhal; Tier1-3 + Unique on Livonia."
    ),
    "usage_group": (
        "Named alias bundling multiple usage flags (OR-semantic). An item with <usage name=\"TownVillage\"/> spawns in any building tagged Town OR Village. "
        "Vanilla user-groups: TownVillage, TownVillageOfficeSchool. Referenced from types.xml as <usage name=\"GroupName\"/>."
    ),
    "value_group": (
        "Named alias bundling multiple value (zone) flags (OR-semantic). An item with <value name=\"Tier1234\"/> spawns in any tiered zone. "
        "Vanilla user-groups (Chernarus/Sakhal): Tier12, Tier23, Tier34, Tier123, Tier234, Tier1234. Livonia drops the Tier4 variants. "
        "Referenced from types.xml as <value name=\"GroupName\"/>."
    ),
}
