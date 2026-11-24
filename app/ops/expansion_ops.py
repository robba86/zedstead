"""Expansion's mission files: the templates, the filter, and how they land.

DayZ-Expansion ships its mission-side content — types, spawnabletypes, events
and event spawn positions — **outside** the mods, in
``ExpansionModTeam/DayZ-Expansion-Missions``. This module fetches that repo's
``Template/`` folder, filters a map's template down to what the installed
modules actually define, and hands the result to the manual-XML machinery.

Four decisions here are load-bearing; changing one silently breaks a mission.

1. **The templates are never vendored into zsm.** The repo ships no LICENSE
   file, so `download_templates` unpacks the GitHub zip into
   ``CONFIG_DIR/expansion-templates`` at the user's request (the same
   download-and-unpack path the map missions use) and `expansion_template_dir`
   can point at a copy they unpacked themselves.

2. **The XML goes in as manual XML files, not as a hand-written `<ce>` block.**
   The upstream README says to register ``<ce folder="expansion_ce">``; zsm only
   ever writes ``<ce folder="custom">`` (`app/xml/CLAUDE.md`), and `custom/` is
   wiped and regenerated on every merge — so a plain copy into it would vanish
   on the next `zsm run`. `xml_manual` is the mechanism that survives that: the
   file of record lives in ``config.paths.custom_xml_dir``, every merge re-copies it
   into `custom/` and re-registers it, and deleting the record undoes both. The
   content is Expansion's; only the folder differs from their README.

3. **Everything is filtered against the classes the installed modules define.**
   Expansion is 19 separate mods and the template assumes all of them: on a
   server without `-Vehicles`, 184 of Chernarus' 484 types name classes nobody
   defines, and the server logs every one of them. The class universe is the
   installed modules' PBOs (`mod_conflict.extract_mod_classnames_cached` over
   `TYPE_CLASS_SECTIONS`) plus the mission's own ``db/types.xml``. A dropped
   entry is always *reported*, with the reason — a silent drop is
   indistinguishable from a bug.

4. **Event spawn positions follow their events.** `cfgeventspawns.xml` holds
   positions for the events in `expansion_events.xml`; a position whose event
   was filtered out is exactly the "no such event in their events.xml" finding
   `app/ops/log_analysis.py` reports against Bohemia's own files. So the
   positions are filtered to the events that survived plus the mission's own.

**Two template events override stock ones** (`VehicleHatchback02`,
`VehicleTruck01`). DayZ takes the last definition, so the template wins over
``db/events.xml`` — which is also what plan 35's Vehicles quick tweak edits.
`MissionPlan.overrides` names them so a surface can say so before applying.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree as ET

from app.shared.config import DayzConfig
from app.shared.json_utils import copy_file
from app.xml.xml_utils import load_xml

LogFn = Callable[[str], None]

TEMPLATE_REPO_URL = "https://github.com/ExpansionModTeam/DayZ-Expansion-Missions"
TEMPLATE_ZIP_URL = f"{TEMPLATE_REPO_URL}/archive/refs/heads/master.zip"
#: The folder inside the repo that holds one directory per map.
TEMPLATE_SUBDIR = "Template"
#: Maps upstream's own README calls unfinished — offered, but labelled.
INCOMPLETE_DIR = "0_INCOMPLETE"

#: CE kind -> the template file that carries it.
CE_TEMPLATE_FILES: dict[str, str] = {
    "types": "expansion_types.xml",
    "spawnabletypes": "expansion_spawnabletypes.xml",
    "events": "expansion_events.xml",
}
CE_SUBDIR = "expansion_ce"
EVENTSPAWNS_FILE = "cfgeventspawns.xml"

#: Manual-XML record ids. Stable: re-applying updates these records rather than
#: piling up a second copy of the same content, and deleting one is the undo.
MANUAL_FILE_IDS: dict[str, str] = {
    "types": "expansion-types",
    "spawnabletypes": "expansion-spawnabletypes",
    "events": "expansion-events",
    "cfgeventspawns": "expansion-eventspawns",
}

#: Mod the generated records are attributed to on the Mods page. Core is the
#: one module every other module needs, so it is the honest owner of "you have
#: Expansion's mission files".
MANUAL_FILE_MOD = "@DayZ-Expansion-Core"

#: The `.map` folders copied verbatim, and the module that must be installed
#: for them to mean anything (every class in traders/ is a Market class, and
#: objects/ is the trader camps those traders stand in).
#:
#: Copied **verbatim and unfiltered**, unlike the XML. Their lines are mostly
#: vanilla map objects (`Land_Ship_Big_FrontA`), and zsm has no list of the
#: base game's building classes — filtering them against the class universe the
#: XML uses reported 341 of 375 objects as undefined on a full 21-mod install,
#: which is a wrong answer, not a strict one. The trader lines are not plain
#: class names either (`ExpansionTraderJudy.Aircraft` is class + category).
MAP_FOLDERS = ("objects", "traders")
MAP_FOLDER_MODULE = "2572328470"  # -Market


# ---------------------------------------------------------------------------
# Templates on disk
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TemplateMap:
    """One map's template folder."""

    #: Display name, e.g. ``Chernarus`` or ``Namalsk/Regular``.
    name: str
    #: Path on disk.
    path: Path
    #: Resolved zsm terrain (``chernarusplus``), or "" for a map zsm's
    #: registry doesn't know (Chiemsee, Iztek, Rostow, Valning).
    terrain: str
    #: False for the maps upstream parks in ``0_INCOMPLETE/``.
    complete: bool
    #: Which of the four source files this template actually ships.
    files: tuple[str, ...]

    @property
    def has_map_folders(self) -> bool:
        return any((self.path / "expansion" / sub).is_dir() for sub in MAP_FOLDERS)


def template_root(config: DayzConfig) -> Path:
    """Where the unpacked ``Template/`` folder lives.

    `CONFIG_DIR` is resolved at call time — tests monkeypatch it, and an
    import-time constant would capture the real one.
    """
    if config.expansion_template_dir:
        return Path(config.expansion_template_dir)
    from app.shared.paths import CONFIG_DIR

    return CONFIG_DIR / "expansion-templates"


def _map_dirs(root: Path) -> list[tuple[str, Path, bool]]:
    """``(display name, path, complete)`` for every template folder under *root*.

    Namalsk is two templates in one folder (``Namalsk/Regular``,
    ``Namalsk/Hardcore``), so a directory that holds no template files but does
    hold sub-directories that do is descended into — one level, no deeper.
    """
    if not root.is_dir():
        return []

    def _is_template(path: Path) -> bool:
        return (path / CE_SUBDIR).is_dir() or (path / EVENTSPAWNS_FILE).is_file()

    found: list[tuple[str, Path, bool]] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name == INCOMPLETE_DIR:
            continue
        if _is_template(entry):
            found.append((entry.name, entry, True))
            continue
        for child in sorted(entry.iterdir()):
            if child.is_dir() and _is_template(child):
                found.append((f"{entry.name}/{child.name}", child, True))

    incomplete = root / INCOMPLETE_DIR
    if incomplete.is_dir():
        for entry in sorted(incomplete.iterdir()):
            if entry.is_dir() and _is_template(entry):
                found.append((entry.name, entry, False))
    return found


def _terrain_for(name: str) -> str:
    """zsm terrain for a template folder name, "" when the registry has none.

    ``Namalsk/Regular`` is matched on its parent, and the lookup goes through
    `registry.terrain_for_token` so a map added to the catalog resolves here
    with no second list (`Enoch` -> ``enoch`` -> Livonia).
    """
    from app.maps import registry

    for candidate in (name.replace("/", ""), name.split("/")[0]):
        terrain = registry.terrain_for_token(candidate.lower())
        if terrain:
            return terrain
    return ""


def list_templates(config: DayzConfig) -> list[TemplateMap]:
    """Every map template found under `template_root`, complete ones first."""
    root = template_root(config)
    out: list[TemplateMap] = []
    for name, path, complete in _map_dirs(root):
        files = [f for f in (EVENTSPAWNS_FILE,) if (path / f).is_file()]
        files += [
            name_ for name_ in CE_TEMPLATE_FILES.values() if (path / CE_SUBDIR / name_).is_file()
        ]
        out.append(
            TemplateMap(
                name=name,
                path=path,
                terrain=_terrain_for(name),
                complete=complete,
                files=tuple(files),
            )
        )
    return out


def find_template(config: DayzConfig, terrain: str) -> TemplateMap | None:
    """The complete template for *terrain*, or None."""
    if not terrain:
        return None
    for tpl in list_templates(config):
        if tpl.complete and tpl.terrain == terrain:
            return tpl
    return None


#: Written into the template folder by `download_templates` after the unpack.
#: A later download empties a folder only when zsm filled it, and this file is
#: how it knows: the folder is a setting, and the action is an rmtree.
TEMPLATE_MARKER = ".zsm-templates"


class TemplateDirRefused(ValueError):
    """The template folder holds something the download did not put there."""


def _is_unpacked_template(path: Path) -> bool:
    """A map folder as the download unpacks it, and not a mission.

    Every real mission keeps its economy in ``db/``; a template never has one.
    That is what keeps ``mpmissions`` (whose folders all carry a
    ``cfgeventspawns.xml``) from passing for a folder of templates.
    """
    return (
        not path.is_symlink()
        and path.is_dir()
        and ((path / CE_SUBDIR).is_dir() or (path / EVENTSPAWNS_FILE).is_file())
        and not (path / "db").exists()
    )


def _is_upstream_readme(path: Path) -> bool:
    """A README*.txt the download unpacks beside templates (``0_INCOMPLETE/README.txt``)."""
    name = path.name.lower()
    return (
        name.startswith("readme")
        and name.endswith(".txt")
        and not path.is_symlink()
        and path.is_file()
    )


def _is_template_download(root: Path) -> bool:
    """True when *root* holds nothing but map templates, as the download leaves it.

    For a folder an older zsm filled before `TEMPLATE_MARKER` existed. Every
    entry has to be a template, or a folder of them (``Namalsk/``,
    ``0_INCOMPLETE/``), and at least one has to carry Expansion's own
    ``expansion_ce/``. One stray file refuses: better a download that asks the
    admin to empty the folder than an rmtree of something nobody meant.
    """
    found_ce = False
    for entry in root.iterdir():
        if _is_unpacked_template(entry):
            found_ce = found_ce or (entry / CE_SUBDIR).is_dir()
            continue
        if entry.is_symlink() or not entry.is_dir():
            return False
        # Upstream's 0_INCOMPLETE/ carries a README.txt beside its templates.
        children = [c for c in entry.iterdir() if not _is_upstream_readme(c)]
        if not children or not all(_is_unpacked_template(c) for c in children):
            return False
        found_ce = found_ce or any((c / CE_SUBDIR).is_dir() for c in children)
    return found_ce


def template_dir_refusal(config: DayzConfig) -> str | None:
    """Why `download_templates` will not empty the template folder, or None.

    It empties zsm's own folder under the config dir, a folder that does not
    exist or is empty, one carrying `TEMPLATE_MARKER`, and one an older zsm
    filled (`_is_template_download`). Anything else is refused, because
    ``expansion_template_dir`` can name any folder on the machine.
    """
    if not config.expansion_template_dir:
        return None
    dest = template_root(config)
    if not dest.exists() and not dest.is_symlink():
        return None
    if not dest.is_dir():
        return f"The template folder {dest} is not a folder."
    try:
        if (
            (dest / TEMPLATE_MARKER).is_file()
            or not any(dest.iterdir())
            or _is_template_download(dest)
        ):
            return None
    except OSError as e:
        return f"The template folder {dest} could not be read ({e.strerror or e})."
    return (
        f"The template folder {dest} holds files that are not Expansion templates "
        "Zedstead downloaded, and the download empties the folder first. Point "
        "expansion_template_dir at an empty or new folder, or clear the setting to "
        "use Zedstead's own folder."
    )


def download_templates(config: DayzConfig, log: LogFn | None = None) -> int:
    """Fetch the missions repo and unpack its ``Template/`` folder.

    Reuses `map_ops.download_and_extract_mission`, which is the same
    "GitHub zip -> one subfolder, path-safe" extraction the map missions use;
    the old content is replaced, never merged, so a template deleted upstream does not
    survive as a stale folder, but only when `template_dir_refusal` says the
    folder is zsm's (`TemplateDirRefused` otherwise). The archive is unpacked
    beside the folder and swapped in only once it is out, so a failed download
    keeps the old templates. A symlinked folder is resolved first, so the link
    stays and the staging folder sits on the target's filesystem. Returns the
    number of files written.
    """
    from app.maps.map_ops import download_and_extract_mission

    refusal = template_dir_refusal(config)
    if refusal:
        raise TemplateDirRefused(refusal)
    dest = template_root(config)
    if dest.is_symlink():
        dest = dest.resolve()
    if dest.is_dir() and log:
        log(f"  Replacing the templates in {dest}")
    count = download_and_extract_mission(TEMPLATE_ZIP_URL, TEMPLATE_SUBDIR, dest, log=log)
    (dest / TEMPLATE_MARKER).write_text(
        "Zedstead downloaded the Expansion mission templates into this folder.\n"
        "The next download empties it first; delete this file to stop that.\n",
        encoding="utf-8",
    )
    if log:
        maps = _map_dirs(dest)
        log(f"  {len(maps)} map template(s) available.")
    return count


# ---------------------------------------------------------------------------
# The class universe a template is filtered against
# ---------------------------------------------------------------------------


def installed_modules(config: DayzConfig) -> list[str]:
    """Expansion module ids in the mod list, in catalog order.

    The mod list is what the server loads, so it — not a folder on disk — decides
    which modules the filter may count on.
    """
    from app.modmgr import expansion, mod_ops, modlist

    listed = modlist.installed(config)
    cached = modlist.workshop_ids(config)
    have = set(mod_ops.resolve_names_to_ids(listed, cached).values())
    return [m.id for m in expansion.MODULES if m.id in have]


def installed_module_labels(config: DayzConfig) -> list[str]:
    """The labels of the installed Expansion modules, frameworks left out, in
    catalog order. For the texts that explain a settings file that is missing."""
    from app.modmgr import expansion

    have = set(installed_modules(config))
    return [m.label for m in expansion.MODULES if m.id in have and not m.framework]


def missing_settings_message(config: DayzConfig, filename: str) -> str:
    """Why an Expansion settings file is missing. Expansion writes the settings
    of its installed modules only, so a restart never creates a file whose
    module is not installed. Which module writes which file is not known here,
    so the text names the installed ones instead of guessing."""
    labels = installed_module_labels(config)
    installed = f" Installed modules: {', '.join(labels)}." if labels else ""
    return (
        f"{filename} does not exist yet. Expansion writes it only when the module that uses it "
        f"is installed.{installed} If that module is installed, start the server once."
    )


def module_class_index(config: DayzConfig, module_ids: list[str]) -> dict[str, str]:
    """``{classname: module label}`` for the given modules' PBOs.

    Catalog order decides the owner when two modules define the same name, so
    the report is stable. Covers CfgVehicles, CfgWeapons and CfgMagazines —
    a types.xml entry can name any of the three.
    """
    from app.modmgr import expansion
    from app.modmgr.mod_conflict import TYPE_CLASS_SECTIONS, extract_mod_classnames_cached

    wanted = [m for m in expansion.MODULES if m.id in set(module_ids)]
    names = {expansion.module_name(m.id): m.label for m in wanted}
    by_mod = extract_mod_classnames_cached(
        config.server_dir_path, [n for n in names if n], TYPE_CLASS_SECTIONS, prune=False
    )
    index: dict[str, str] = {}
    for mod_name, label in names.items():
        for classname in by_mod.get(mod_name, []):
            index.setdefault(classname, label)
    return index


def mission_class_names(mission_dir: Path) -> set[str]:
    """Class names the mission's own ``db/types.xml`` carries.

    Deliberately not `types_xml.scan_types_files`: that also reads ``custom/``,
    which is where a previous apply put Expansion's own types — counting those
    as "the mission already knows this class" would make the filter pass
    everything on the second run.
    """
    path = mission_dir / "db" / "types.xml"
    root = _parse(path) if path.is_file() else None
    if root is None:
        return set()
    return {t.get("name", "") for t in root.findall("type") if t.get("name")}


def mission_event_names(mission_dir: Path) -> set[str]:
    """Event names the mission's own ``db/events.xml`` defines."""
    path = mission_dir / "db" / "events.xml"
    root = _parse(path) if path.is_file() else None
    if root is None:
        return set()
    return {e.get("name", "") for e in root.findall("event") if e.get("name")}


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Dropped:
    """One entry the filter removed, and why."""

    kind: str  # types | spawnabletypes | events | cfgeventspawns
    element: str  # type | item | child | event
    name: str
    #: Module that defines the class but is not installed, "" when nothing does.
    owner: str
    reason: str


@dataclass
class MissionPlan:
    """What applying a template would write, and what it would leave out."""

    template: str
    terrain: str
    mission: str
    modules: list[str] = field(default_factory=list)  # labels, catalog order
    kept: dict[str, int] = field(default_factory=dict)
    total: dict[str, int] = field(default_factory=dict)
    per_module: dict[str, int] = field(default_factory=dict)
    dropped: list[Dropped] = field(default_factory=list)
    overrides: list[str] = field(default_factory=list)
    new_events: list[str] = field(default_factory=list)
    map_files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: kind -> the XML that would be written. Not part of the API payload.
    content: dict[str, str] = field(default_factory=dict)
    #: The template folder this was built from — where `map_files` are copied
    #: from at apply time. Not part of the API payload either.
    source: Path | None = None

    @property
    def orphans(self) -> list[str]:
        """Names *nothing* installed defines — template leftovers, not choices."""
        seen: list[str] = []
        for d in self.dropped:
            if not d.owner and d.name not in seen:
                seen.append(d.name)
        return seen

    def summary(self) -> dict:
        """JSON-safe view for an API/CLI surface (without `content`)."""
        return {
            "template": self.template,
            "terrain": self.terrain,
            "mission": self.mission,
            "modules": self.modules,
            "kept": self.kept,
            "total": self.total,
            "per_module": self.per_module,
            "dropped": [
                {
                    "kind": d.kind,
                    "element": d.element,
                    "name": d.name,
                    "owner": d.owner,
                    "reason": d.reason,
                }
                for d in self.dropped
            ],
            "dropped_count": len(self.dropped),
            "orphans": self.orphans,
            "overrides": self.overrides,
            "new_events": self.new_events,
            "map_files": self.map_files,
            "warnings": self.warnings,
        }


def _parse(path: Path) -> ET.Element | None:
    """The root element of *path*, or None when it cannot be read.

    Through `load_xml`, so defusedxml parses it: the templates come from the
    Expansion repository, and the stdlib parser expands entity declarations.
    """
    try:
        return load_xml(path).getroot()
    except (ET.ParseError, OSError, ValueError):
        return None


def _serialize(root: ET.Element) -> str:
    tree = ET.ElementTree(root)
    ET.indent(tree, space="\t")
    text = ET.tostring(root, encoding="unicode", xml_declaration=True)
    return text if text.endswith("\n") else text + "\n"


def build_plan(
    config: DayzConfig,
    template: TemplateMap,
    *,
    module_ids: list[str] | None = None,
) -> MissionPlan:
    """Filter *template* against the installed modules and report the result.

    `module_ids` defaults to the Expansion modules in the mod list — the filter has
    to match what the server loads, or the types it keeps name classes that are
    not there.
    """
    from app.modmgr import expansion, mods

    mission = mods.get_current_mission(config.server_cfg_path) or ""
    mission_dir = config.server_dir_path / "mpmissions" / mission if mission else Path()

    ids = installed_modules(config) if module_ids is None else list(module_ids)
    labels = [m.label for m in expansion.MODULES if m.id in set(ids)]
    index = module_class_index(config, ids)
    # Every installed module, so a drop can say *which* module owns the class
    # instead of only that the selection doesn't.
    full_index = module_class_index(config, installed_modules(config))
    vanilla = mission_class_names(mission_dir)
    known = set(index) | vanilla

    plan = MissionPlan(
        template=template.name,
        terrain=template.terrain,
        mission=mission,
        modules=labels,
        source=template.path,
    )
    if not mission:
        plan.warnings.append(
            "No mission is configured in serverDZ.cfg — nothing can be applied yet."
        )
    if not ids:
        plan.warnings.append(
            "No Expansion modules are installed, so every entry would be dropped. "
            "Install the modules first."
        )
    if not vanilla and mission:
        plan.warnings.append(
            f"The mission's db/types.xml could not be read, so vanilla classes count as "
            f"undefined. Check mpmissions/{mission}/db/types.xml."
        )

    def _reason(name: str) -> tuple[str, str]:
        """Which module would have to be there for this entry to work.

        `full_index` covers every *installed* module, so an owner can only be
        named when the caller filtered for a subset of what is installed (the
        "what would Core + Vehicles alone give me" preview). In the normal
        path the two indexes are the same and every drop is an orphan, which
        is exactly what the wording says.
        """
        owner = full_index.get(name, "")
        if owner:
            return owner, f"needs {owner}"
        return "", "nothing installed defines it"

    def _drop(kind: str, element: str, name: str) -> None:
        owner, reason = _reason(name)
        plan.dropped.append(Dropped(kind, element, name, owner, reason))

    # --- types ------------------------------------------------------------
    root = _parse(template.path / CE_SUBDIR / CE_TEMPLATE_FILES["types"])
    if root is not None:
        entries = root.findall("type")
        plan.total["types"] = len(entries)
        for entry in entries:
            name = entry.get("name", "")
            if name in known:
                owner = index.get(name) or ("vanilla" if name in vanilla else "unknown")
                plan.per_module[owner] = plan.per_module.get(owner, 0) + 1
            else:
                _drop("types", "type", name)
                root.remove(entry)
        plan.kept["types"] = len(root.findall("type"))
        plan.content["types"] = _serialize(root)

    # --- spawnabletypes ---------------------------------------------------
    root = _parse(template.path / CE_SUBDIR / CE_TEMPLATE_FILES["spawnabletypes"])
    if root is not None:
        entries = root.findall("type")
        plan.total["spawnabletypes"] = len(entries)
        for entry in entries:
            name = entry.get("name", "")
            if name not in known:
                _drop("spawnabletypes", "type", name)
                root.remove(entry)
                continue
            # An attachment naming a class nobody defines is the same error as
            # a type doing so — the engine complains per item, not per type.
            for holder in list(entry):
                for item in list(holder.findall("item")):
                    item_name = item.get("name", "")
                    if item_name not in known:
                        _drop("spawnabletypes", "item", item_name)
                        holder.remove(item)
                if len(holder) == 0:
                    entry.remove(holder)
        plan.kept["spawnabletypes"] = len(root.findall("type"))
        plan.content["spawnabletypes"] = _serialize(root)

    # --- events -----------------------------------------------------------
    stock_events = mission_event_names(mission_dir)
    kept_events: set[str] = set()
    root = _parse(template.path / CE_SUBDIR / CE_TEMPLATE_FILES["events"])
    if root is not None:
        entries = root.findall("event")
        plan.total["events"] = len(entries)
        for entry in entries:
            name = entry.get("name", "")
            for children in entry.findall("children"):
                for child in list(children.findall("child")):
                    child_type = child.get("type", "")
                    if child_type not in known:
                        _drop("events", "child", child_type)
                        children.remove(child)
                if len(children) == 0:
                    entry.remove(children)
            # An event with nothing left to spawn is dead weight that the
            # economy still loads and complains about.
            if not entry.findall("children/child"):
                _drop("events", "event", name)
                root.remove(entry)
                continue
            kept_events.add(name)
            if name in stock_events:
                plan.overrides.append(name)
            else:
                plan.new_events.append(name)
        plan.kept["events"] = len(root.findall("event"))
        plan.content["events"] = _serialize(root)

    # --- cfgeventspawns ---------------------------------------------------
    root = _parse(template.path / EVENTSPAWNS_FILE)
    if root is not None:
        entries = root.findall("event")
        plan.total["cfgeventspawns"] = len(entries)
        live = kept_events | stock_events
        for entry in entries:
            name = entry.get("name", "")
            if name not in live:
                plan.dropped.append(
                    Dropped(
                        "cfgeventspawns",
                        "event",
                        name,
                        "",
                        "its event is not in the mission",
                    )
                )
                root.remove(entry)
        plan.kept["cfgeventspawns"] = len(root.findall("event"))
        plan.content["cfgeventspawns"] = _serialize(root)

    # --- the .map folders -------------------------------------------------
    if template.has_map_folders:
        if MAP_FOLDER_MODULE in set(ids):
            for sub in MAP_FOLDERS:
                folder = template.path / "expansion" / sub
                if folder.is_dir():
                    plan.map_files += [f"expansion/{sub}/{p.name}" for p in sorted(folder.glob("*.map"))]
        else:
            plan.warnings.append(
                "The trader and object .map files are skipped: every class in them belongs to "
                "-Market, which is not installed."
            )

    if not plan.content:
        plan.warnings.append(f"{template.name} ships none of the files this applies.")
    return plan


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


def apply_plan(
    config: DayzConfig,
    plan: MissionPlan,
    *,
    log: LogFn | None = None,
) -> dict:
    """Write *plan* into the mission through the manual-XML machinery.

    Idempotent: re-applying updates the same four records rather than adding a
    second copy, which is what makes "install another module, apply again"
    work. Returns ``{files, applied, copied, mission}``.
    """
    from app.xml import xml_manual

    def _log(line: str) -> None:
        if log:
            log(line)

    mission = plan.mission
    if not mission:
        raise ValueError("No mission is configured in serverDZ.cfg.")
    mission_dir = config.server_dir_path / "mpmissions" / mission
    if not mission_dir.is_dir():
        raise ValueError(f"Mission folder not found: {mission_dir}")
    # Without it the CE half copies the files and registers nothing, which
    # looks like a success and loads no content at all.
    if not (mission_dir / "cfgeconomycore.xml").is_file():
        raise ValueError(
            f"{mission} has no cfgeconomycore.xml — the economy cannot be told about these files."
        )

    # An unknown terrain means "every map" — better than mislabelling the file
    # as a map it isn't, but worth saying out loud.
    terrain = plan.terrain if plan.terrain in xml_manual.known_terrain_tokens() else ""
    if not terrain:
        _log(
            f"Note: Zedstead has no terrain for the {plan.template} template, so these files are "
            "not scoped to a map. Delete them on the Custom config tab if you switch maps."
        )

    manual_dir = config.paths.custom_xml_dir
    written: list[str] = []
    applied: list[str] = []
    for kind, content in plan.content.items():
        file_id = MANUAL_FILE_IDS[kind]
        # A kind the installed modules leave empty gets no file: an empty one
        # made every later merge warn "contributed nothing" (browser test
        # 2026-11-17, F2). An earlier apply's record for it goes, so a module
        # removed since leaves no old entries behind.
        if not plan.kept.get(kind, 0):
            if xml_manual.delete_file(
                file_id, manual_dir=manual_dir, server_dir=config.server_dir_path, mission=mission,
            ):
                _log(f"Removed manual XML file {file_id}: the installed modules have no entries for it.")
            continue
        note = (
            f"DayZ-Expansion {plan.template} template, filtered for "
            f"{len(plan.modules)} installed module(s)."
        )
        if xml_manual.get_file(file_id, manual_dir=manual_dir):
            xml_manual.update_file(
                file_id,
                manual_dir=manual_dir,
                content=content,
                kind=kind,
                mod=MANUAL_FILE_MOD,
                terrain=terrain,
                note=note,
                enabled=True,
            )
            _log(f"Updated manual XML file {file_id} ({plan.kept.get(kind, 0)} entries).")
        else:
            xml_manual.create_file(
                file_id,
                kind,
                content,
                manual_dir=manual_dir,
                mod=MANUAL_FILE_MOD,
                terrain=terrain,
                note=note,
            )
            _log(f"Created manual XML file {file_id} ({plan.kept.get(kind, 0)} entries).")
        written.append(file_id)
        if xml_manual.apply_manual_file(
            config.server_dir_path, mission, file_id, manual_dir=manual_dir, log=log,
        ):
            applied.append(file_id)
        else:
            _log(f"Warning: {file_id} was stored but not applied to {mission}.")

    copied: list[str] = []
    for rel in plan.map_files:
        src = _template_file(plan.source, rel)
        dest = mission_dir / rel
        if src is None or not src.is_file():
            continue
        copy_file(src, dest)
        copied.append(rel)
    if copied:
        _log(f"Copied {len(copied)} trader/object .map file(s) into the mission.")

    for line in plan.warnings:
        _log(f"Note: {line}")
    if plan.overrides:
        _log(
            "These events override the mission's own: "
            + ", ".join(plan.overrides)
            + ". The template's counts win."
        )
    _log(f"Dropped {len(plan.dropped)} entr(y/ies) — see the report for each one.")

    return {
        "files": written,
        "applied": applied,
        "copied": copied,
        "mission": mission,
    }


def _template_file(source: Path | None, rel: str) -> Path | None:
    """Resolve a `map_files` entry back to the template file it came from.

    `rel` is generated from a directory listing, never user input, but it is
    joined onto a path and then copied — so it is re-checked against the
    template root the same way every other path join in the app is.
    """
    if source is None:
        return None
    root = source.resolve()
    path = (source / rel).resolve()
    return path if path.is_relative_to(root) else None


def applied_files(config: DayzConfig) -> list[dict]:
    """The generated manual-XML records that exist right now.

    Read off the record rather than off the mission: the record is what every
    merge re-applies, so it is what "this mission has Expansion's files" means.
    Per-file state (disabled, deleted, the custom/ copy) lives on the Mod XML
    page, which owns that view.
    """
    from app.xml import xml_manual

    out: list[dict] = []
    for kind, file_id in MANUAL_FILE_IDS.items():
        row = xml_manual.get_file(file_id, manual_dir=config.paths.custom_xml_dir)
        if row:
            out.append(
                {
                    "id": file_id,
                    "kind": kind,
                    "enabled": bool(row.get("enabled", True)),
                    "terrain": row.get("terrain", ""),
                    "note": row.get("note", ""),
                }
            )
    return out


def remove_applied(config: DayzConfig, *, log: LogFn | None = None) -> list[str]:
    """Delete the four generated records and undo what they applied.

    The `.map` files copied into the mission are left alone: they are mission
    content once copied, and nothing else records that zsm put them there.
    """
    from app.modmgr import mods
    from app.xml import xml_manual

    mission = mods.get_current_mission(config.server_cfg_path) or ""
    removed: list[str] = []
    for file_id in MANUAL_FILE_IDS.values():
        if xml_manual.delete_file(
            file_id,
            manual_dir=config.paths.custom_xml_dir,
            server_dir=config.server_dir_path,
            mission=mission,
        ):
            removed.append(file_id)
            if log:
                log(f"Removed {file_id}.")
    return removed
