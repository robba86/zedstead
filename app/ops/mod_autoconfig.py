"""Auto configure: types.xml entries for a mod that ships no mission XML.

Many Workshop mods ship items in their PBOs but no types.xml, and the admin
writes the entries by hand. `build` writes them instead, from two sources:

1. The mod's Workshop description, when its author pasted ``<type>`` blocks
   there (as most do). The author's values win.
2. The mod's own PBO classes for every item the description does not cover.
   An item copies the values of the first ancestor class that the mission's
   types files know (a gun that extends a vanilla gun spawns like it). An item
   with no known ancestor gets safe defaults with nominal 0, so nothing spawns
   until the admin edits it.

Three options (`AutoConfigOptions`, all on by default) make the PBO entries
fairer; the admin turns each off in the dialog or with a CLI flag:

- split_nominal: variants that copy the same vanilla item share its nominal
  and min, so 10 colours of one gun do not spawn 10 times as many.
- guess_category: an item with no known ancestor gets a category from its
  config section (CfgWeapons, CfgMagazines) or a whole word of its class name
  (TunaCan is food, Showcase is nothing).
- starter_nominal: such an item gets a small nominal by kind (weapon,
  magazine, other item), so it spawns at once. A CfgVehicles class gets it
  only when its parent chain leaves the mod at a known loot base
  (`_LOOT_BASES`): that section also holds cars, buildings and placed
  objects, which must never spawn as loot. Such a class gets no category
  guess either.

`apply` stores the result as a manual types file of the mod (the Mod XML page,
Manual files), so the admin edits or deletes it like any other manual file.
The CLI (`zsm mods xml-autoconfig`) and the web route wrap these two functions.
"""

from __future__ import annotations

import copy
import html
import re
from dataclasses import dataclass, field
from typing import Callable
from xml.etree import ElementTree as ET

from app.shared.config import DayzConfig

SOURCE_WORKSHOP = "workshop"
SOURCE_PBO = "pbo"

# Bohemia's flags on a stock item that spawns on the map (every stock types.xml).
_DEFAULT_FLAGS = {
    "count_in_cargo": "0", "count_in_hoarder": "0", "count_in_map": "1",
    "count_in_player": "0", "crafted": "0", "deloot": "0",
}
# Values for an item with no known ancestor. nominal 0: nothing spawns until
# the admin sets a number, which beats a guess that floods the map.
_DEFAULT_VALUES = (
    ("nominal", "0"), ("lifetime", "3888000"), ("restock", "0"), ("min", "0"),
    ("quantmin", "-1"), ("quantmax", "-1"), ("cost", "100"),
)
# A parent chain longer than this is a loop in a broken config, not a real one.
_MAX_DEPTH = 64

# The types.xml category for an `economy_analyzer` category. Vanilla files
# file medical items under "tools".
_TYPES_CATEGORY = {
    "weapons": "weapons", "food": "food", "clothing": "clothes",
    "containers": "containers", "tools": "tools", "vehicles": "vehiclesparts",
    "explosives": "explosives", "medical": "tools",
}
# starter_nominal's (nominal, min) per kind: few guns, more magazines.
_STARTER_VALUES = {"weapon": (2, 1), "magazine": (4, 2), "item": (3, 1)}
# Vanilla CfgVehicles base classes of loot. A class whose chain leaves the mod
# at another class (CarScript, House, BuildingBase, an unknown one) is no
# loot to starter_nominal and keeps nominal 0.
_LOOT_BASES = frozenset(
    name.lower()
    for name in (
        "Inventory_Base", "ItemBase", "Clothing", "Clothing_Base", "Container_Base",
        "Edible_Base", "Box_Base", "Bottle_Base", "Grenade_Base", "ItemOptics",
        "ItemSuppressor", "ItemBook", "ItemMap", "ItemCompass", "ItemWatch",
    )
)

_BBCODE_RE = re.compile(r"\[/?[a-zA-Z*][a-zA-Z0-9]*(?:=[^\]]*)?\]")
_TYPE_BLOCK_RE = re.compile(r"<type\b[^>]*>.*?</type>", re.DOTALL | re.IGNORECASE)


class AutoConfigError(Exception):
    """Auto configure cannot run for this mod; the message says why."""


@dataclass(frozen=True)
class AutoConfigOptions:
    """The three switches of the Auto configure dialog (module docstring)."""

    split_nominal: bool = True
    guess_category: bool = True
    starter_nominal: bool = True

    @classmethod
    def from_dict(cls, data: dict | None) -> "AutoConfigOptions":
        """Options from a request body: a missing key keeps its default."""
        data = data or {}
        return cls(**{k: bool(data[k]) for k in ("split_nominal", "guess_category", "starter_nominal") if k in data})

    def to_dict(self) -> dict:
        return {
            "split_nominal": self.split_nominal,
            "guess_category": self.guess_category,
            "starter_nominal": self.starter_nominal,
        }


@dataclass
class AutoConfigPreview:
    """What `apply` would write, and why each class was or was not included."""

    mod: str
    file_id: str
    xml: str
    # The names in the XML, by source.
    from_workshop: list[str] = field(default_factory=list)
    from_parent: list[dict] = field(default_factory=list)  # {name, parent}
    from_defaults: list[str] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)  # {name, reason}
    # Why the Workshop description gave nothing, or "".
    workshop_note: str = ""
    # Entries whose nominal is 0 or missing, from any source: registered, but
    # the game never spawns them until the admin sets nominal and min.
    zero_nominal: list[str] = field(default_factory=list)
    options: AutoConfigOptions = field(default_factory=AutoConfigOptions)
    # split_nominal's groups: {parent, names, nominal, min}.
    split: list[dict] = field(default_factory=list)
    # What the options set on a default entry: {name, category, nominal, min}.
    guessed: list[dict] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.from_workshop) + len(self.from_parent) + len(self.from_defaults)

    @property
    def source(self) -> str:
        """``workshop``, ``pbo`` or ``workshop+pbo``."""
        parts = [SOURCE_WORKSHOP] if self.from_workshop else []
        if self.from_parent or self.from_defaults:
            parts.append(SOURCE_PBO)
        return "+".join(parts)

    def to_dict(self) -> dict:
        return {
            "mod": self.mod,
            "file_id": self.file_id,
            "xml": self.xml,
            "source": self.source,
            "count": self.count,
            "from_workshop": self.from_workshop,
            "from_parent": self.from_parent,
            "from_defaults": self.from_defaults,
            "skipped": self.skipped,
            "workshop_note": self.workshop_note,
            "zero_nominal": self.zero_nominal,
            "options": self.options.to_dict(),
            "split": self.split,
            "guessed": self.guessed,
        }


def file_id_for(mod_name: str) -> str:
    """The manual file id Auto configure gives *mod_name*: ``auto-`` plus a
    slug that `xml_manual.is_safe_file_id` accepts."""
    slug = re.sub(r"[^a-z0-9_]+", "-", mod_name.lower().lstrip("@")).strip("-_")
    slug = re.sub(r"-{2,}", "-", slug)[:58].strip("-_") or "mod"
    return f"auto-{slug}"


def workshop_type_blocks(description: str) -> list[ET.Element]:
    """The ``<type>`` elements an author pasted into a Workshop description.

    The description is BBCode text: tags such as ``[code]`` are dropped and
    entities unescaped before the blocks are cut out. Each block is parsed on
    its own with the defused parser, so one broken block costs only itself.
    """
    from app.xml.xml_text import parse_xml_text

    text = html.unescape(_BBCODE_RE.sub("", description))
    out: list[ET.Element] = []
    for block in _TYPE_BLOCK_RE.findall(text):
        try:
            root = parse_xml_text(block, strip_leading_junk=False).getroot()
        except ET.ParseError:
            continue
        if root.tag == "type" and root.get("name"):
            out.append(root)
    return out


def _mission_types(config: DayzConfig) -> dict:
    """``{lowercase name: TypeEntry}`` over every types file of the mission,
    ``custom/`` included, so a mod that extends another mod's item finds it."""
    from app.xml.types_xml import parse_types_file, scan_types_files
    from app.xml.xml_utils import get_mission_dir

    mission_dir = get_mission_dir(config)
    if mission_dir is None:
        raise AutoConfigError("Could not determine the current mission from serverDZ.cfg.")
    entries: dict = {}
    for path in scan_types_files(mission_dir):
        try:
            parsed = parse_types_file(path)
        except (ET.ParseError, OSError):
            continue
        for entry in parsed.entries:
            entries.setdefault(entry.name.lower(), entry)
    return entries


def _default_element(name: str) -> ET.Element:
    el = ET.Element("type", name=name)
    for tag, value in _DEFAULT_VALUES:
        ET.SubElement(el, tag).text = value
    ET.SubElement(el, "flags", dict(_DEFAULT_FLAGS))
    return el


def _render(elements: list[ET.Element]) -> str:
    root = ET.Element("types")
    root.extend(elements)
    ET.indent(root, space="\t")
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n' + ET.tostring(
        root, encoding="unicode"
    ) + "\n"


def build(
    config: DayzConfig,
    mod_name: str,
    *,
    fetch_description: Callable[[str], str | None] | None = None,
    options: AutoConfigOptions | None = None,
) -> AutoConfigPreview:
    """The types entries Auto configure would write for *mod_name*.

    *fetch_description* defaults to the keyless Workshop details call. Raises
    `AutoConfigError` when the mod defines no classes or there is no mission.
    """
    from app.modmgr import modlist, workshop_api
    from app.modmgr.mod_conflict import extract_mod_class_info

    fetch = fetch_description or workshop_api.get_public_description
    classes: dict = {}
    for info in extract_mod_class_info(config.server_dir_path, mod_name):
        classes.setdefault(info.name.lower(), info)
    if not classes:
        raise AutoConfigError(
            f"{mod_name} defines no item classes in its PBOs, so there is nothing to configure."
        )
    known = _mission_types(config)
    options = options or AutoConfigOptions()
    preview = AutoConfigPreview(mod=mod_name, file_id=file_id_for(mod_name), xml="", options=options)
    elements: list[ET.Element] = []
    done: set[str] = set()

    # 1. The author's own blocks. Only a class this mod defines is kept: the
    #    description is third-party text, and a block for a vanilla item would
    #    change how that item spawns.
    mod_id = modlist.workshop_ids(config).get(mod_name, "")
    description = fetch(mod_id) if mod_id else None
    if not mod_id:
        preview.workshop_note = "No Workshop ID is known for this mod."
    elif description is None:
        preview.workshop_note = "Steam did not answer with the Workshop description."
    else:
        blocks = workshop_type_blocks(description)
        if not blocks:
            preview.workshop_note = "The Workshop description has no <type> entries."
        for el in blocks:
            name = el.get("name", "")
            key = name.lower()
            if key in done:
                continue
            if key not in classes:
                preview.skipped.append({"name": name, "reason": "in the Workshop text, but not a class of this mod"})
                continue
            done.add(key)
            if key in known:
                preview.skipped.append({"name": name, "reason": "already in the mission's types"})
                continue
            el.set("name", classes[key].name)
            elements.append(el)
            preview.from_workshop.append(classes[key].name)

    # 2. Every other item, from the PBO classes.
    copies: dict[str, list[ET.Element]] = {}  # ancestor name -> its copies
    for key, info in classes.items():
        if key in done:
            continue
        if key in known:
            preview.skipped.append({"name": info.name, "reason": "already in the mission's types"})
            continue
        scope, ancestor = _resolve(info, classes, known)
        if scope is not None and scope != 2:
            preview.skipped.append({"name": info.name, "reason": "a base class (scope below 2), not an item"})
            continue
        if scope is None and ancestor is None:
            preview.skipped.append({"name": info.name, "reason": "no scope and no known parent, so maybe not an item"})
            continue
        if ancestor is not None:
            el = copy.deepcopy(ancestor.element)
            el.set("name", info.name)
            elements.append(el)
            copies.setdefault(ancestor.name, []).append(el)
            preview.from_parent.append({"name": info.name, "parent": ancestor.name})
        else:
            el = _default_element(info.name)
            guess = _guess_defaults(el, info, options, _outside_parent(info, classes))
            if guess:
                preview.guessed.append({"name": info.name, **guess})
            elements.append(el)
            preview.from_defaults.append(info.name)

    if options.split_nominal:
        for parent, group in copies.items():
            if len(group) > 1:
                preview.split.append({"parent": parent, "names": [el.get("name", "") for el in group], **_split(group)})

    preview.zero_nominal = [el.get("name", "") for el in elements if _nominal(el) == 0]
    preview.xml = _render(elements) if elements else ""
    return preview


def _int_of(el: ET.Element, tag: str) -> int:
    """A whole-number child value of *el*, 0 when missing or not a number."""
    try:
        return int((el.findtext(tag) or "0").strip())
    except ValueError:
        return 0


def _set(el: ET.Element, tag: str, value: int) -> None:
    child = el.find(tag)
    if child is None:
        child = ET.SubElement(el, tag)
    child.text = str(value)


def _split(group: list[ET.Element]) -> dict:
    """Share the ancestor's nominal and min across its *group* of copies (each
    copy holds the same values). A spawning item keeps at least nominal 1, and
    min stays at most nominal. Returns the new values."""
    count = len(group)
    nominal, min_ = _int_of(group[0], "nominal"), _int_of(group[0], "min")
    new_nominal = max(1, round(nominal / count)) if nominal > 0 else 0
    new_min = min(new_nominal, max(1, round(min_ / count))) if min_ > 0 else 0
    for el in group:
        _set(el, "nominal", new_nominal)
        _set(el, "min", new_min)
    return {"nominal": new_nominal, "min": new_min}


def _kind(info) -> str:
    """``weapon``, ``magazine`` or ``item``, from the class's config section."""
    return {"CfgWeapons": "weapon", "CfgMagazines": "magazine"}.get(info.section, "item")


def _outside_parent(info, classes: dict) -> str:
    """The root of *info*'s parent chain: the first class this mod does not
    define (``Inventory_Base``, ``CarScript``), or the mod's own topmost class
    when the mod restates a vanilla base with an empty body. "" for none."""
    parent = info.parent
    for _ in range(_MAX_DEPTH):
        if not parent:
            return ""
        own = classes.get(parent.lower())
        if own is None or not own.parent:
            return parent
        parent = own.parent
    return ""


def _guess_defaults(el: ET.Element, info, options: AutoConfigOptions, outside: str = "") -> dict:
    """Apply guess_category and starter_nominal to a default entry *el*.
    *outside* is the first parent outside the mod (`_outside_parent`).
    Returns what was set ({category, nominal, min}, or ``not_loot`` with that
    parent when the starter was held back), or {} for nothing."""
    from app.ops.economy_analyzer import category_from_classname

    out: dict = {}
    kind = _kind(info)
    # A car, a building or an unknown base is no loot: no category either.
    not_loot = kind == "item" and outside.lower() not in _LOOT_BASES
    if options.guess_category and not not_loot:
        category = "weapons" if kind != "item" else _TYPES_CATEGORY.get(category_from_classname(info.name, whole_words=True), "")
        if category:
            # After flags, where a vanilla entry has it.
            el.append(ET.Element("category", name=category))
            out["category"] = category
    if not_loot and (options.starter_nominal or options.guess_category):
        out["not_loot"] = outside or "no parent"
    if options.starter_nominal and not not_loot:
        nominal, min_ = _STARTER_VALUES[kind]
        _set(el, "nominal", nominal)
        _set(el, "min", min_)
        out.update(nominal=nominal, min=min_)
    return out


def _nominal(el: ET.Element) -> int:
    """An entry's nominal, 0 when it is missing or not a whole number (the game
    then spawns none either)."""
    try:
        return int((el.findtext("nominal") or "0").strip())
    except ValueError:
        return 0


def _resolve(info, classes: dict, known: dict):
    """``(scope, ancestor TypeEntry)`` for one class: its scope from itself or
    the first of its in-mod parents that sets one, and the first ancestor the
    mission's types know (None for either when there is none)."""
    scope = info.scope
    ancestor = None
    parent = info.parent
    for _ in range(_MAX_DEPTH):
        if not parent:
            break
        key = parent.lower()
        if ancestor is None and key in known:
            ancestor = known[key]
        own = classes.get(key)
        if own is None:
            break
        if scope is None:
            scope = own.scope
        if ancestor is not None and scope is not None:
            break
        parent = own.parent
    return scope, ancestor


def apply(config: DayzConfig, preview: AutoConfigPreview) -> dict:
    """Store *preview* as the mod's manual types file and put it into the mission.

    Raises `AutoConfigError` when the preview is empty or the mod already has
    an Auto configure file (the admin edits that one on the Mod XML page).
    """
    from app.modmgr.mods import get_current_mission
    from app.xml import xml_manual

    if not preview.xml:
        raise AutoConfigError(f"There is nothing to add for {preview.mod}.")
    manual_dir = config.paths.custom_xml_dir
    if xml_manual.get_file(preview.file_id, manual_dir=manual_dir) is not None:
        raise AutoConfigError(
            f"{preview.mod} already has the manual file {preview.file_id}. "
            "Edit or delete it on the Mod XML page."
        )
    mission = get_current_mission(config.server_cfg_path)
    if not mission:
        raise AutoConfigError("Could not determine the current mission from serverDZ.cfg.")
    link = xml_manual.create_file(
        preview.file_id, "types", preview.xml, manual_dir=manual_dir, mod=preview.mod,
        note=f"Auto configure ({preview.source})",
    )
    applied = xml_manual.apply_manual_file(
        config.server_dir_path, mission, preview.file_id, manual_dir=manual_dir,
    )
    return {"file_id": link["id"], "applied": applied, "count": preview.count, "source": preview.source}
