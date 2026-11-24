"""Shared business logic for bundle operations.

Used by both app/commands/bundle.py (Click CLI) and
app/web/routers/backups.py (web UI).
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from app.parsers.battleye_cfg import live_beserver_cfg, read_beserver_cfg, refuse_while_running
from app.shared.config import DayzConfig
from app.shared.zip_utils import is_bare_name, private_zip, safe_extract_path


# Fields carried over during import (safe to overwrite on target machine)
IMPORTABLE_CONFIG_FIELDS = {
    "server_name",
    "server_args",
    "server_port",
    "steam_username",
    "battleye_path",
    "profiles_path",
    "config_path",
    "check_steam_login",
}

# The mod list, its flags and its load order travel as one file of their own
# (`modlist`, plans/77). Its archive name.
MODS_ARC_NAME = "mods.json"


def importable_config(
    bundle_cfg: dict, config: DayzConfig | None = None
) -> tuple[dict, list[str]]:
    """The bundle's ``config.json`` fields an import applies, and a line for
    each one it left out.

    A bundle may come from someone else, so a field whose own setter refuses a
    value is not applied from a bundle either: the Steam username goes to
    SteamCMD as an argument, and the three launch paths and ``server_args`` go
    through the Run Command save's launch-command check
    (`service_control.launch_field_problem`). ``server_name`` is written into
    serverDZ.cfg as the quoted ``hostname`` (`container.sync_server_cfg`), so
    it gets that writer's check (plans/68 LB-6). *config* is the install being
    imported into; the check reads which mods it marks server-side. The lines
    are markup-free, for a job log and the CLI.
    """
    # Imported here: app.shared.steam pulls in requests and bs4.
    from app.parsers.servercfg import cfg_value_problem
    from app.server.service_control import launch_field_problem
    from app.shared.server_args import RUNCOMMAND_CONFIG_FIELDS
    from app.shared.steam import steam_username_problem

    updates = {k: v for k, v in bundle_cfg.items() if k in IMPORTABLE_CONFIG_FIELDS}
    skipped: list[str] = []
    if "server_name" in updates:
        name = updates["server_name"]
        found = cfg_value_problem(name, quoted=True) if isinstance(name, str) else "is not text"
        problem = f"it {found}." if found else None
        if problem:
            del updates["server_name"]
            skipped.append(f"server_name was not imported: {problem}")
    if "steam_username" in updates:
        username = updates["steam_username"]
        problem = (
            "it is not text." if not isinstance(username, str)
            else steam_username_problem(username) if username else None
        )
        if problem:
            del updates["steam_username"]
            skipped.append(f"steam_username was not imported: {problem}")
    for key in [k for k in updates if k in (*RUNCOMMAND_CONFIG_FIELDS, "server_args")]:
        value = updates[key]
        problem = (
            "it is not text." if not isinstance(value, str)
            else launch_field_problem(config, key, value)
        )
        if problem:
            del updates[key]
            skipped.append(f"{key} was not imported: {problem}")
    if "server_port" in updates and config is not None:
        problem = _port_taken_problem(config, updates["server_port"])
        if problem:
            del updates["server_port"]
            skipped.append(f"server_port was not imported: {problem}")
    return updates, skipped


def _port_taken_problem(config: DayzConfig, port) -> str | None:
    """Why *port* would clash with another instance on this machine, or None.

    A bundle from main imported into a second instance put that instance on
    main's game port 2302, and four ports clashed with no word in the log
    Ports belong to the machine, as paths do, so the
    instance keeps its own (`instances.port_clash`).
    """
    from app.server.instances import port_clash

    try:
        game = int(port)
    except (TypeError, ValueError):
        return None  # the setter's own check refuses it
    clash = port_clash(config, game)
    return f"{clash} on this machine, so this instance keeps {config.server_port}." if clash else None

def import_mod_list(config: DayzConfig, names: list[str], data) -> str:
    """Make the bundle's mod list this instance's, and say what happened in one
    line for the import's log. *data* is the bundle entry's bytes, or None
    when it has none.

    A bundle without one is from before the list moved to mods.json; its
    mods.txt is not read (plans/77 D4), so the list stays as it was. A list
    that is not one zsm can read is refused whole, never imported in part.
    """
    from app.modmgr import modlist

    if data is None:
        if "mods.txt" in names:
            return (f"- mods.txt was not imported: this bundle is from before Zedstead kept the mod "
                    f"list in {MODS_ARC_NAME}. Install its mods again from the Mods page.")
        return ""
    try:
        count = modlist.replace_from(config, data, f"the bundle's {MODS_ARC_NAME}")
    except modlist.ModListError as e:
        return f"- {MODS_ARC_NAME} was not imported: {e}"
    return f"+ {MODS_ARC_NAME} ({count} mod(s))"


# Mission XML files to include in export (relative to mission dir)
MISSION_XML_FILES = [
    "db/types.xml",
    "db/events.xml",
    "db/messages.xml",
    "db/globals.xml",
    "db/economy.xml",
    "cfgeventspawns.xml",
    "cfgeventgroups.xml",
    "cfgspawnabletypes.xml",
    "cfgeconomycore.xml",
]

# Mission JSON files to include in export (relative to mission dir): the three
# every stock mission ships (dayz-docs/vanilla). A mission without one packs
# the others.
MISSION_JSON_FILES = [
    "cfggameplay.json",
    "cfgEffectArea.json",
    "cfgundergroundtriggers.json",
]

# The other top-level mission files the panel edits (Configs: player spawn
# points, weather, limits, environment, ignore list, random presets) and the
# mission's init.c. A bundle left them out, so a migration lost those edits.
# Matched like MISSION_JSON_FILES. The mapgroup*.xml
# files stay out: stock map data, megabytes each, and no editor writes them.
MISSION_TOP_FILES = [
    "cfgplayerspawnpoints.xml",
    "cfgweather.xml",
    "cfglimitsdefinition.xml",
    "cfglimitsdefinitionuser.xml",
    "cfgenvironment.xml",
    "cfgignorelist.xml",
    "cfgrandompresets.xml",
    "init.c",
]


# Fields in config.json that are credentials, not settings. `config.json` goes
# into every bundle **verbatim** — which is right for a migration (you want
# your RCon password on the new box) and wrong for the other thing bundles are
# used for: sending one to somebody so they can look at your setup.
#
# Measured in 2026 Q3: an exported bundle carries all four of these in
# cleartext. `discord_webhook_url` is the sharpest — a webhook URL *is* the
# credential, so anyone holding it can post to that channel.
#
# zsm does not strip them, by the same reasoning that keeps it from writing
# `RConIP` into beserver_x64.cfg (see app/ops/CLAUDE.md): silently changing
# what a file contains would break the migration this feature exists for, and
# an admin who wants them out can say so. **The warning is the intervention.**
SENSITIVE_CONFIG_FIELDS = (
    ("rcon_password", "your RCon password"),
    ("discord_webhook_url", "your Discord webhook URL"),
    ("steam_api_key", "your Steam API key"),
    ("web_password_hash", "the app password hash"),
    # plans/57 B7. Not only a credential: it travels with the install ID, so a
    # second box restored from the bundle publishes to zedstead.com as the same
    # server. Warned about (the sentence in `sensitive_warning`), not stripped.
    ("site_secret", "your zedstead.com listing key"),
)

# Said once, after the list, whenever the listing key is in it.
_SHARED_LISTING_NOTE = (
    " A second machine restored from it would also publish to zedstead.com as this same server."
)


# The bundle packs serverDZ.cfg and the BattlEye cfg as well, and those hold
# passwords of their own (test pass 2, N24: the warning named only config.json's
# fields). Label templates take the file's real name, which differs in case
# between Linux and Windows.
SENSITIVE_CFG_KEYS = (
    ("password", "the server join password ({file})"),
    ("passwordAdmin", "the server admin password ({file})"),
)
SENSITIVE_BESERVER_KEYS = (
    ("RConPassword", "the BattlEye RCon password ({file})"),
)


def sensitive_fields_in(config: DayzConfig) -> list[str]:
    """Which credentials the bundle would carry: set config fields, then the
    non-empty password keys of the two .cfg files it packs.

    Names only — never values. This list is rendered into a job log and a CLI
    screen, and a function that reported the secrets would be the leak it
    exists to warn about.
    """
    from app.parsers.servercfg import read_cfg_values

    found = [
        label
        for field, label in SENSITIVE_CONFIG_FIELDS
        if str(getattr(config, field, "") or "").strip()
    ]
    server_cfg = config.server_cfg_path
    values = read_cfg_values(server_cfg, [key for key, _ in SENSITIVE_CFG_KEYS])
    found += [
        label.format(file=server_cfg.name)
        for key, label in SENSITIVE_CFG_KEYS
        if (values.get(key) or "").strip()
    ]
    # What the export packs: BattlEye's active copy while the server runs.
    be_values = read_beserver_cfg(live_beserver_cfg(config))
    found += [
        label.format(file=config.beserver_cfg_path.name)
        for key, label in SENSITIVE_BESERVER_KEYS
        if (be_values.get(key) or "").strip()
    ]
    return found


def _and_join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def sensitive_warning(config: DayzConfig) -> str | None:
    """The one-line warning for an export, or None when nothing is set."""
    found = sensitive_fields_in(config)
    if not found:
        return None
    return (
        "Note: this bundle carries "
        + _and_join(found)
        + ". Fine for moving to your own machine; strip them before sending it to anyone."
        + (_SHARED_LISTING_NOTE if str(config.site_secret or "").strip() else "")
    )


def get_bundle_dir(config: DayzConfig) -> Path:
    """Return the bundle export/import directory."""
    if config.bundle_export_dir:
        return Path(config.bundle_export_dir).expanduser()
    return config.paths.bundles_dir


def list_available_bundles(config: DayzConfig) -> list[Path]:
    """Return sorted list of dayz-server-*-bundle-*.zip files in bundle dir."""
    bundle_dir = get_bundle_dir(config)
    if not bundle_dir.is_dir():
        return []
    return sorted(bundle_dir.glob("dayz-server-*bundle-*.zip"), reverse=True)


def file_entries(zf: zipfile.ZipFile) -> list[str]:
    """The bundle's file entries, which is what both importers read.

    A directory entry (``battleye/``, ``mpmissions/<m>/db/``) is left out: a
    zip made with ``zip -r`` carries one per folder, and taken as a file it
    wrote an empty ``battleye`` file into the BattlEye dir, or an empty file
    where a mission folder goes (plans/68 B12). Folders are created for the
    files that go in them.
    """
    return [info.filename for info in zf.infolist() if not info.is_dir()]


def _is_beserver_cfg_entry(config: DayzConfig, name: str) -> bool:
    """True for ``battleye/<this platform's cfg name>``, in any casing, so a
    Windows bundle's BEServer_x64.cfg imports on Linux too."""
    return name.lower() == f"battleye/{config.beserver_cfg_path.name.lower()}"


def battleye_entries(config: DayzConfig, names: list[str]) -> list[str]:
    """The bundle's BattlEye cfg entry, the one file an export packs there.
    Any other ``battleye/`` entry (a BEServer_x64.dll or beserver_x64.so the
    server loads) is never written: see ``refused_battleye_entries``."""
    return [n for n in names if _is_beserver_cfg_entry(config, n)]


def refused_battleye_entries(config: DayzConfig, names: list[str]) -> list[str]:
    """The ``battleye/`` entries the importers skip and log."""
    return [
        n for n in names
        if n.startswith("battleye/") and not n.endswith("/") and not _is_beserver_cfg_entry(config, n)
    ]


def refuse_battleye_import_while_running(config: DayzConfig, names: list[str]) -> None:
    """Raise ``BeserverCfgInUse`` for a bundle carrying the BattlEye cfg while
    the server runs: BattlEye would put its own copy back over it at the next
    stop. Both importers ask before they write anything."""
    if battleye_entries(config, names):
        refuse_while_running(config)


def remap_mission_entry(arc_name: str, source_mission: str | None, dest_mission: str | None) -> str:
    """A bundle's ``mpmissions/<source>/…`` entry, moved to ``dest_mission``."""
    if dest_mission and source_mission and dest_mission != source_mission:
        return arc_name.replace(f"mpmissions/{source_mission}/", f"mpmissions/{dest_mission}/", 1)
    return arc_name


def existing_casing(path: Path) -> Path:
    """``path``, or the file beside it whose name differs only in case.

    A bundle exported on Windows carries ``cfgEffectArea.json``; the Linux
    server download has ``cfgeffectarea.json`` (dayz-docs/CLAUDE.md). Writing
    the bundle's name next to the server's would leave two files, so an import
    writes over the one already there, as Windows' file system does anyway.
    """
    if path.exists() or not path.parent.is_dir():
        return path
    wanted = path.name.lower()
    for sibling in sorted(path.parent.iterdir()):
        if sibling.is_file() and sibling.name.lower() == wanted:
            return sibling
    return path


def mission_entry_dest(server_dir: Path, dest_name: str) -> tuple[str, Path]:
    """Where a (remapped) mission entry is written, and its label under the
    name actually written.

    ``ValueError`` unless the entry lands inside ``mpmissions/<mission>/`` for
    a mission that is a single folder name. Containment in ``server_dir``
    alone let ``mpmissions/<m>/../../<file>`` (or a target mission of ``..``)
    write over any file of the server install, its binary included.
    """
    parts = dest_name.split("/", 2)
    if len(parts) != 3 or parts[0] != "mpmissions" or not is_bare_name(parts[1]):
        raise ValueError(f"not a mission entry: {dest_name!r}")
    mission_dir = server_dir / "mpmissions" / parts[1]
    target = safe_extract_path(mission_dir, parts[2])
    if target == mission_dir.resolve():
        raise ValueError(f"not a mission entry: {dest_name!r}")
    dest = existing_casing(target)
    if dest == target:
        return dest_name, dest
    return f"{dest_name.rsplit('/', 1)[0]}/{dest.name}", dest


def profiles_entries(config: DayzConfig, names: list[str]) -> list[str]:
    """The bundle's entries under the profiles folder, as the export names them."""
    prefix = config.profiles_path + "/"
    return [n for n in names if n.startswith(prefix)]


def profiles_entry_dest(config: DayzConfig, arc_name: str) -> Path:
    """Where a ``<profiles_path>/…`` entry is written. ``ValueError`` unless it
    lands inside the profiles folder: ``profiles/../<file>`` is inside the
    server dir but is not a profiles file."""
    rel = arc_name[len(config.profiles_path) + 1:]
    profiles_dir = config.server_dir_path / config.profiles_path
    target = safe_extract_path(profiles_dir, rel)
    if target == profiles_dir.resolve():
        raise ValueError(f"not a profiles entry: {arc_name!r}")
    return target


def detect_source_mission(names: list[str]) -> str | None:
    """Detect the mission folder name from zip archive paths."""
    for name in names:
        if name.startswith("mpmissions/"):
            parts = name.split("/")
            if len(parts) >= 2 and parts[1]:
                return parts[1]
    return None


# ── Export manifest ──────────────────────────────────────────────────


# Expansion keeps its configuration and its player data side by side, so a
# bundle names the configuration folders it takes. A folder Expansion adds stays
# out until it is listed here: it may hold player data.
EXPANSION_PROFILE_CONFIG_DIRS = ("Settings", "Market", "Traders", "Loadouts", "AI")
# Folders of the mission's expansion/ that hold player data: P2P market listings.
EXPANSION_MISSION_PLAYER_DATA = frozenset({"p2pmarket"})
# Expansion's configuration is JSON, its object placements .map files.
_EXPANSION_SUFFIXES = {".json", ".map", ".xml"}


def _expansion_files(directory: Path, skip: frozenset[str]) -> list[Path]:
    """The configuration files under *directory*, sorted, leaving out every
    top-level folder named in *skip* (compared without case)."""
    if not directory.is_dir():
        return []
    skip_lower = {s.lower() for s in skip}
    out = []
    for f in sorted(directory.rglob("*")):
        if not f.is_file() or f.is_symlink() or f.suffix.lower() not in _EXPANSION_SUFFIXES:
            continue
        top = f.relative_to(directory).parts[0].lower()
        if top in skip_lower:
            continue
        out.append(f)
    return out


@dataclass
class ExportFileInfo:
    arc_name: str
    path: Path
    size: int


@dataclass
class ExportManifest:
    included: list[ExportFileInfo] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def collect_export_manifest(config: DayzConfig) -> ExportManifest:
    """Collect all files that would be included in a bundle export."""
    manifest = ExportManifest()

    # The mod list (mods.json)
    mods_json = config.paths.mods_json
    if mods_json.is_file():
        manifest.included.append(ExportFileInfo(MODS_ARC_NAME, mods_json, mods_json.stat().st_size))
    else:
        manifest.skipped.append(f"{MODS_ARC_NAME} (no mods installed)")

    # serverDZ.cfg
    server_cfg = config.server_cfg_path
    if server_cfg.exists():
        manifest.included.append(ExportFileInfo(
            "serverDZ.cfg", server_cfg, server_cfg.stat().st_size,
        ))
    else:
        manifest.skipped.append("serverDZ.cfg (not found)")

    # config.json (this instance's app config)
    app_config = config.paths.config_file
    if app_config.exists():
        manifest.included.append(ExportFileInfo(
            "config.json", app_config, app_config.stat().st_size,
        ))

    # Mission XMLs
    try:
        from app.modmgr.mods import get_current_mission
        mission = get_current_mission(config.server_cfg_path)
    except OSError:
        mission = None

    if mission:
        mission_dir = config.server_dir_path / "mpmissions" / mission
        for rel in MISSION_XML_FILES:
            full = mission_dir / rel
            arc_name = f"mpmissions/{mission}/{rel}"
            if full.exists():
                manifest.included.append(ExportFileInfo(
                    arc_name, full, full.stat().st_size,
                ))
            else:
                manifest.skipped.append(f"{arc_name} (not found)")

        # Mission JSON files (MISSION_JSON_FILES) and the other top-level
        # files (MISSION_TOP_FILES), matched case-insensitively and packed
        # under the name on disk: the Linux server download has
        # cfgeffectarea.json where the CE repo has cfgEffectArea.json
        # (dayz-docs/CLAUDE.md).
        wanted = {rel.lower() for rel in (*MISSION_JSON_FILES, *MISSION_TOP_FILES)}
        on_disk = sorted(mission_dir.iterdir()) if mission_dir.is_dir() else []
        for full in on_disk:
            if full.is_file() and full.name.lower() in wanted:
                manifest.included.append(ExportFileInfo(
                    f"mpmissions/{mission}/{full.name}", full, full.stat().st_size,
                ))

        # Additional XML files in subdirectories (custom/, expansion/, etc.)
        already_added = set(MISSION_XML_FILES)
        for xml_file in sorted(mission_dir.rglob("*.xml")):
            # as_posix: str() has backslashes on Windows, where "/" was then
            # never in it and every subfolder file was left out.
            rel_str = xml_file.relative_to(mission_dir).as_posix()
            if rel_str in already_added or "/" not in rel_str:
                continue
            arc_name = f"mpmissions/{mission}/{rel_str}"
            manifest.included.append(ExportFileInfo(
                arc_name, xml_file, xml_file.stat().st_size,
            ))

        # Expansion's mission data: settings, traders, trader zones, objects.
        # Its XML is in already, from the loop above.
        exp_dir = mission_dir / "expansion"
        for f in _expansion_files(exp_dir, EXPANSION_MISSION_PLAYER_DATA):
            if f.suffix.lower() == ".xml":
                continue
            rel_str = f.relative_to(mission_dir).as_posix()
            manifest.included.append(ExportFileInfo(
                f"mpmissions/{mission}/{rel_str}", f, f.stat().st_size,
            ))
    else:
        manifest.skipped.append("mission XMLs (mission not detected)")

    # BattlEye config (contains RCON password). While the server runs only
    # BattlEye's active copy exists; it goes in under the file's own name.
    be_cfg = live_beserver_cfg(config)
    if be_cfg.exists():
        manifest.included.append(ExportFileInfo(
            f"battleye/{config.beserver_cfg_path.name}", be_cfg, be_cfg.stat().st_size,
        ))

    # Profiles config files (.cfg, .json — skip logs, RPT, mdmp)
    profiles_dir = config.server_dir_path / config.profiles_path
    _LOG_SUFFIXES = {".log", ".rpt", ".mdmp", ".adm"}
    if profiles_dir.is_dir():
        # TraderPlus configs (config files only, skip player databases)
        tp_dir = profiles_dir / "TraderPlus" / "TraderPlusConfig"
        if tp_dir.is_dir():
            for f in sorted(tp_dir.rglob("*.json")):
                rel = f.relative_to(config.server_dir_path)
                manifest.included.append(ExportFileInfo(
                    str(rel), f, f.stat().st_size,
                ))

        # Expansion's configuration, folder by folder: player data (ATM) and
        # logs share the parent folder and stay out.
        for sub in EXPANSION_PROFILE_CONFIG_DIRS:
            for f in _expansion_files(profiles_dir / "ExpansionMod" / sub, frozenset()):
                rel = f.relative_to(config.server_dir_path)
                manifest.included.append(ExportFileInfo(
                    rel.as_posix(), f, f.stat().st_size,
                ))

        # Other profiles config files (top-level .cfg/.json only, skip TraderPlus dir)
        for f in sorted(profiles_dir.iterdir()):
            if not f.is_file():
                continue
            if f.suffix.lower() in _LOG_SUFFIXES:
                continue
            if f.suffix.lower() in (".cfg", ".json"):
                rel = f.relative_to(config.server_dir_path)
                manifest.included.append(ExportFileInfo(
                    str(rel), f, f.stat().st_size,
                ))

    return manifest


# ── Export ──────────────────────────────────────────────────────────


def bundle_zip_path(config: DayzConfig) -> Path:
    """Next free bundle path: ``dayz-server-<mission>-bundle-<today>[-N].zip``."""
    try:
        from app.modmgr.mods import get_current_mission
        mission = get_current_mission(config.server_cfg_path) or ""
    except OSError:
        mission = ""

    today = str(date.today())
    base_name = f"dayz-server-{mission}-bundle-{today}" if mission else f"dayz-server-bundle-{today}"

    export_dir = get_bundle_dir(config)
    zip_path = export_dir / f"{base_name}.zip"
    counter = 2
    while zip_path.exists():
        zip_path = export_dir / f"{base_name}-{counter}.zip"
        counter += 1
    return zip_path


def export_bundle(
    config: DayzConfig,
    log: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> Path | None:
    """Write the export zip and report every entry through *log*.

    Shared by the CLI (`zsm bundle export`) and the web export job — both need
    the same per-file account of what went in, so the lines are built once here
    and are **markup-free** (the web job log renders them raw). Returns the zip
    path, or None when *should_cancel* asked to stop (the partial zip is removed).
    """
    emit = log or (lambda _line: None)

    export_dir = get_bundle_dir(config)
    export_dir.mkdir(parents=True, exist_ok=True)
    zip_path = bundle_zip_path(config)

    manifest = collect_export_manifest(config)
    emit(f"Creating bundle: {zip_path}")
    emit(f"{len(manifest.included)} file(s) to include.")

    try:
        # Owner-only: it carries config.json and the server's passwords.
        with private_zip(zip_path, compression=zipfile.ZIP_DEFLATED) as zf:
            for file_info in manifest.included:
                if should_cancel is not None and should_cancel():
                    emit("Cancelled — removing the partial bundle.")
                    raise _BundleCancelled
                zf.write(file_info.path, file_info.arc_name)
                emit(f"  + {file_info.arc_name}")
    except _BundleCancelled:
        zip_path.unlink(missing_ok=True)
        return None

    for skipped in manifest.skipped:
        emit(f"  - {skipped}")
    emit(f"Bundle exported to {zip_path}")
    # Last line on purpose: it is about the file that now exists, and it is
    # the thing to have read before sending it anywhere.
    warning = sensitive_warning(config)
    if warning:
        emit(warning)
    return zip_path


class _BundleCancelled(Exception):
    """Internal signal — the caller asked to stop mid-zip."""

