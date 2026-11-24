"""Shared business logic for cleanup operations.

Used by both app/commands/cleanup.py (Click CLI) and
app/web/routers/cleanup.py (web UI).
"""

from __future__ import annotations

import errno
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.shared.config import DayzConfig
from app.modmgr.mods import get_current_mission
from app.parsers.servercfg import is_storage_folder, storage_folder_name


def reinstall_refusal(config) -> str | None:
    """Why Reinstall server must not delete *config*'s server dir, or None.

    The reinstall's first step is ``rmtree`` of the whole dir, and an
    instance could hold /home/zsm, so it would have deleted every server,
    the Steam cache, Zedstead's config with the accounts, and the backups.
    Asked by the web route and the CLI before anything
    is deleted: the dir must not be or hold anything `server_dir_conflict`
    protects, and a dir with files in it must look like a DayZ server.
    """
    from app.server.instances import server_dir_conflict
    from app.shared.platform import IS_WINDOWS

    server_dir = config.server_dir_path
    problem = server_dir_conflict(server_dir, own_id=getattr(config, "instance_id", ""))
    if problem:
        return f"Reinstall server is refused: {problem}"
    try:
        empty = not server_dir.exists() or not any(server_dir.iterdir())
    except OSError:
        empty = False
    binary = "DayZServer_x64.exe" if IS_WINDOWS else "DayZServer"
    looks_like_a_server = (
        (server_dir / binary).exists()
        or config.server_cfg_path.exists()
        or (server_dir / "steamapps").is_dir()
    )
    if not empty and not looks_like_a_server:
        return (f"Reinstall server is refused: {server_dir} holds files but no DayZ server "
                f"({binary}, serverDZ.cfg or steamapps), and a reinstall deletes everything in it. "
                "Check the server directory in Settings.")
    return None


def rmtree_mount_safe(path: Path) -> None:
    """Remove a directory tree, tolerating *path* being a mount point.

    In the Docker deployment the server/steamcmd dirs are compose bind
    mounts: rmtree deletes their contents but the final rmdir on the mount
    point itself fails with EBUSY. Fall back to emptying the directory and
    leaving it in place — callers recreate/repopulate it anyway.
    """
    try:
        shutil.rmtree(path)
    except OSError as exc:
        if exc.errno != errno.EBUSY:
            raise
        for child in Path(path).iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()


# ── Wipe Server ──────────────────────────────────────────────────────


@dataclass
class StorageInfo:
    mission: str
    path: Path


def storage_name(config: DayzConfig) -> str:
    """The persistence folder's name on this server: ``storage_<instanceId>``."""
    return storage_folder_name(config.server_cfg_path)


def storage_dir(config: DayzConfig, mission: str) -> Path:
    """Where *mission* keeps its persistence on this server. A server whose
    ``instanceId`` is not 1 saves somewhere other than ``storage_1``: a host's
    cfg often has another number, and the admin keeps it when they move."""
    return config.server_dir_path / "mpmissions" / mission / storage_name(config)


def find_storage(config: DayzConfig) -> StorageInfo | None:
    """The active mission's persistence folder, or None when no mission is
    set or the folder does not exist yet."""
    mission = get_current_mission(config.server_cfg_path)
    if not mission:
        return None
    storage = storage_dir(config, mission)
    if not storage.is_dir():
        return None
    return StorageInfo(mission=mission, path=storage)


def storage_folders(mission_dir: Path) -> list[Path]:
    """Every persistence folder in *mission_dir*: the one in use and any an
    earlier ``instanceId`` left behind."""
    if not mission_dir.is_dir():
        return []
    return sorted(p for p in mission_dir.iterdir() if p.is_dir() and is_storage_folder(p.name))



# ── Clear Logs ───────────────────────────────────────────────────────


@dataclass
class LogFilesInfo:
    files: list[Path]
    total_size: int
    profiles_dir: Path


def find_log_files(config: DayzConfig) -> LogFilesInfo | None:
    """The files Clear logs counts and deletes. Returns None if profiles dir missing.

    The automatic cleanup's set (`_collect_log_files`): .log, .ADM, .RPT and
    .mdmp in the server root and the profiles tree, error.log kept. Clear
    logs used its own glob, which left every .ADM file and deleted error.log.
    """
    profiles_dir = config.server_dir_path / config.profiles_path
    if not profiles_dir.is_dir():
        return None
    files = sorted(f for group in _collect_log_files(config).values() for f in group)
    total = sum(f.stat().st_size for f in files) if files else 0
    return LogFilesInfo(files=files, total_size=total, profiles_dir=profiles_dir)


def clear_log_files(files: list[Path], *, running: bool) -> tuple[int, int]:
    """Delete *files* (from `find_log_files`). Returns (deleted, kept).

    While the server runs, the newest file of each extension in each folder
    is kept: it is the log the server or a mod is writing now. Deleting it
    failed on Windows, where the open file is locked, and on Linux left the
    log readers with no current log until the next restart. A file that
    cannot be deleted is counted as kept, never an error halfway through.
    """
    keep: set[Path] = set()
    if running:
        newest: dict[tuple[Path, str], tuple[float, Path]] = {}
        for f in files:
            try:
                mtime = f.stat().st_mtime
            except OSError:
                continue
            slot = (f.parent, f.suffix.lower())
            if slot not in newest or mtime > newest[slot][0]:
                newest[slot] = (mtime, f)
        keep = {f for _mtime, f in newest.values()}
    deleted = 0
    for f in files:
        if f in keep:
            continue
        try:
            f.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            keep.add(f)
            continue
        deleted += 1
    return deleted, len(keep)


# ── Auto Log Cleanup ─────────────────────────────────────────────────

_LOG_EXTENSIONS = (".log", ".adm", ".rpt", ".mdmp")
_PROTECTED_NAMES = {"error.log"}


def _collect_log_files(config: DayzConfig) -> dict[str, list[Path]]:
    """Collect log files from server root and profiles dir, grouped by extension.

    Extensions are lowercased for grouping.  Files in ``_PROTECTED_NAMES``
    (e.g. ``error.log`` in the profiles directory) are excluded.
    """
    server_dir = config.server_dir_path
    profiles_dir = server_dir / config.profiles_path

    by_ext: dict[str, list[Path]] = {ext: [] for ext in _LOG_EXTENSIONS}

    # Server root — non-recursive
    if server_dir.is_dir():
        for f in server_dir.iterdir():
            if f.is_file() and f.suffix.lower() in by_ext:
                by_ext[f.suffix.lower()].append(f)

    # Profiles — recursive
    if profiles_dir.is_dir():
        for f in profiles_dir.rglob("*"):
            if not f.is_file():
                continue
            ext = f.suffix.lower()
            if ext not in by_ext:
                continue
            # Always keep protected files in profiles dir
            if f.name.lower() in _PROTECTED_NAMES:
                continue
            by_ext[ext].append(f)

    return by_ext


def auto_cleanup_logs(config: DayzConfig) -> int:
    """Delete old / excess log files based on config settings.

    Rules (both can apply at the same time):
    * ``log_cleanup_days > 0`` — delete files older than N days.
    * ``log_cleanup_keep > 0`` — per extension, keep only the N newest files.

    Returns the total number of deleted files.
    """
    days = config.log_cleanup_days
    keep = config.log_cleanup_keep
    if days <= 0 and keep <= 0:
        return 0

    now = time.time()
    cutoff = now - days * 86400 if days > 0 else 0
    deleted = 0

    for _ext, files in _collect_log_files(config).items():
        to_delete: set[Path] = set()

        # Age-based removal
        if days > 0:
            for f in files:
                try:
                    if f.stat().st_mtime < cutoff:
                        to_delete.add(f)
                except OSError:
                    continue

        # Count-based removal (keep newest N per extension)
        if keep > 0 and len(files) > keep:
            sorted_files = sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)
            to_delete.update(sorted_files[keep:])

        for f in to_delete:
            try:
                f.unlink()
                deleted += 1
            except OSError:
                continue

    return deleted


# ── Remove Workshop Mods ─────────────────────────────────────────────


@dataclass
class WorkshopModInfo:
    path: Path
    workshop_id: str
    mod_name: str | None


def list_workshop_mods(config: DayzConfig) -> list[WorkshopModInfo] | None:
    """List workshop mod dirs with names. Returns None if dir missing."""
    from app.modmgr import modlist

    workshop_dir = config.workshop_dir
    if not workshop_dir.is_dir():
        return None

    mod_dirs = sorted(
        [d for d in workshop_dir.iterdir() if d.is_dir()],
        key=lambda d: d.name,
    )
    if not mod_dirs:
        return []

    cached_ids = modlist.workshop_ids(config)
    id_to_name = {v: k for k, v in cached_ids.items()}

    return [
        WorkshopModInfo(
            path=d,
            workshop_id=d.name,
            mod_name=id_to_name.get(d.name),
        )
        for d in mod_dirs
    ]


def remove_workshop_mods(
    config: DayzConfig,
    paths: list[Path],
    *,
    log: Callable[[str], None] | None = None,
) -> int:
    """Delete workshop item dirs *and* drop them from the workshop ACF.

    The ACF half is not optional: SteamCMD keeps trusting a deleted item's
    manifest and tries to reuse its chunks on the next download, which then
    dies with ``Missing game files`` / ``Download item <id> failed (Failure)``
    for *every* mod until the entry is gone. Shared by the CLI and web
    surfaces — never rmtree a workshop dir without going through here.

    Returns the number of directories removed. A removal that fails still
    drops every item it reached from the ACF, the failed one included (it may
    be half deleted; dropped, it is downloaded again), before the error is
    raised.
    """
    _emit = log or (lambda s: None)
    removed_ids: list[str] = []
    touched: list[str] = []
    try:
        for d in paths:
            _emit(f"Removing {d}...")
            touched.append(Path(d).name)
            shutil.rmtree(d)
            removed_ids.append(Path(d).name)
    finally:
        if touched:
            from app.parsers.acf_parser import remove_workshop_items

            if remove_workshop_items(config.workshop_acf_path, touched):
                _emit("Updated SteamCMD's workshop manifest.")
    return len(removed_ids)


def find_stale_workshop_entries(config: DayzConfig) -> list[str]:
    """Workshop IDs the ACF claims are installed but whose folder is gone/empty.

    Each one poisons *every* later workshop download for the app (SteamCMD
    reuses chunks from items it believes are installed and aborts on the first
    missing file), so this is what the repair below removes.
    """
    from app.parsers.acf_parser import get_installed_timestamps, parse_acf_file
    from app.shared.steam import is_mod_downloaded

    acf_path = config.workshop_acf_path
    if not acf_path.is_file():
        return []
    try:
        installed = get_installed_timestamps(parse_acf_file(acf_path))
    except Exception:
        return []
    return [mid for mid in sorted(installed) if not is_mod_downloaded(config, mid)]


def repair_workshop_manifest(
    config: DayzConfig, *, log: Callable[[str], None] | None = None
) -> list[str]:
    """Drop stale entries from the workshop ACF. Returns the IDs removed."""
    from app.parsers.acf_parser import remove_workshop_items

    _emit = log or (lambda s: None)
    stale = find_stale_workshop_entries(config)
    if not stale:
        _emit("Workshop manifest is consistent — nothing to repair.")
        return []
    remove_workshop_items(config.workshop_acf_path, stale)
    _emit(f"Removed {len(stale)} stale entry(s): {', '.join(stale)}")
    return stale


def calculate_dirs_size(dirs: list[Path]) -> int:
    """Calculate total file size across directories."""
    return sum(
        f.stat().st_size for d in dirs for f in d.rglob("*") if f.is_file()
    )


# ── Leftover mod data (profiles/<Mod>/) ──────────────────────────────
#
# Mods that write their own config/state do it into `<profiles>/<Name>/` —
# @TraderPlus's JSON configs are the common case, seeded on install by
# `trader_common.copy_traderplus_default_config`.  Uninstalling a mod
# deliberately leaves that directory alone: it holds live server data (trader
# stock, garage vehicles, bank balances) that must survive an
# uninstall/reinstall round trip.  It is *reported* instead — at uninstall time
# in the job log, and here so the Cleanup page can offer the deletion.


@dataclass
class LeftoverModData:
    """A `<profiles>/<Name>/` directory whose mod is no longer installed."""

    mod: str  # the mod that owns it, e.g. "@TraderPlus"
    name: str  # the profiles subdirectory name, e.g. "TraderPlus"
    path: Path
    size: int
    files: int


def mod_profile_dir(config: DayzConfig, mod_name: str) -> Path:
    """Return the profiles subdirectory a mod writes its own data into."""
    return config.server_dir_path / config.profiles_path / mod_name.lstrip("@")


def _describe_mod_data(mod: str, path: Path) -> LeftoverModData:
    entries = [f for f in path.rglob("*") if f.is_file()]
    return LeftoverModData(
        mod=mod,
        name=path.name,
        path=path,
        size=sum(f.stat().st_size for f in entries),
        files=len(entries),
    )


def find_mod_profile_data(config: DayzConfig, mod_name: str) -> LeftoverModData | None:
    """Return *mod_name*'s profiles data directory if it exists, else None."""
    path = mod_profile_dir(config, mod_name)
    if not path.is_dir() or path.is_symlink():
        return None
    return _describe_mod_data(mod_name, path)


def _installed_mod_names(config: DayzConfig) -> set[str]:
    """Mods present on the server — mod-list entries plus `@` dirs on disk."""
    from app.modmgr.mod_ops import ensure_at_prefix
    from app.modmgr import modlist

    names = {ensure_at_prefix(n) for n in modlist.installed(config)}
    server_dir = config.server_dir_path
    if server_dir.is_dir():
        names |= {d.name for d in server_dir.iterdir() if d.is_dir() and d.name.startswith("@")}
    return names


def find_leftover_mod_data(config: DayzConfig) -> list[LeftoverModData]:
    """Profiles subdirs owned by a mod zsm knows of that is not installed.

    Matching is deliberately conservative — only directories whose name is a
    known mod name minus its ``@`` are reported, because the delete this feeds
    is destructive and the profiles directory also holds server-owned files.
    """
    from app.modmgr.mod_ops import ensure_at_prefix, registry_mod_ids
    from app.modmgr import modlist

    profiles_dir = config.server_dir_path / config.profiles_path
    if not profiles_dir.is_dir():
        return []

    known: dict[str, str] = {}
    for mod in registry_mod_ids():
        known.setdefault(mod.lstrip("@").casefold(), mod)
    for name in modlist.workshop_ids(config):
        mod = ensure_at_prefix(name)
        known.setdefault(mod.lstrip("@").casefold(), mod)

    installed = {n.casefold() for n in _installed_mod_names(config)}

    out: list[LeftoverModData] = []
    for child in sorted(profiles_dir.iterdir()):
        if not child.is_dir() or child.is_symlink():
            continue
        mod = known.get(child.name.casefold())
        if mod is None or mod.casefold() in installed:
            continue
        out.append(_describe_mod_data(mod, child))
    return out


def remove_mod_data_dirs(
    config: DayzConfig,
    names: list[str],
    log: Callable[[str], None] | None = None,
) -> list[str]:
    """Delete leftover profiles data dirs, by profiles subdirectory *name*.

    Only names in the current `find_leftover_mod_data` result are touched — a
    raw name is never joined onto the profiles path, and an installed mod's
    data can't be deleted through here.
    """
    _emit = log or (lambda s: None)
    wanted = {n.casefold() for n in names}
    removed: list[str] = []
    for entry in find_leftover_mod_data(config):
        if entry.name.casefold() not in wanted:
            continue
        shutil.rmtree(entry.path)
        _emit(f"Removed {entry.path}")
        removed.append(entry.name)
    return removed


# ── Migrate Players ─────────────────────────────────────────────────


def get_players_db_path(config: DayzConfig, mission: str) -> Path:
    """Get players.db path for a mission."""
    return storage_dir(config, mission) / "players.db"


def backup_players_db(target_db: Path) -> Path:
    """Backup players.db. Returns backup path."""
    backup = target_db.with_suffix(".db.backup")
    shutil.copy2(target_db, backup)
    return backup


def copy_players_db(source_db: Path, target_db: Path) -> None:
    """Copy players.db from source to target, creating dirs as needed."""
    target_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_db, target_db)


# ── Clear XML Backups ────────────────────────────────────────────────


@dataclass
class BackupFilesInfo:
    files: list[Path]
    total_size: int
    mission: str
    mission_dir: Path


def find_xml_backups(config: DayzConfig) -> BackupFilesInfo | None:
    """Find .backup files in mission dir. Returns None on error.

    A storage folder is left out: `backup_players_db` keeps the only copy of
    the player database from before a migration there as players.db.backup."""
    mission = get_current_mission(config.server_cfg_path)
    if not mission:
        return None
    mission_dir = config.server_dir_path / "mpmissions" / mission
    if not mission_dir.is_dir():
        return None
    files = [
        f for f in mission_dir.rglob("*.backup")
        if not any(is_storage_folder(part) for part in f.relative_to(mission_dir).parts[:-1])
    ]
    total = sum(f.stat().st_size for f in files) if files else 0
    return BackupFilesInfo(
        files=files, total_size=total, mission=mission, mission_dir=mission_dir,
    )


def delete_backup_files(files: list[Path]) -> int:
    """Delete backup files (files or dirs). Returns count."""
    for f in files:
        if f.is_dir():
            shutil.rmtree(f)
        else:
            f.unlink()
    return len(files)


# ── Reset Mpmission ─────────────────────────────────────────────────


@dataclass
class MissionResetInfo:
    template: str
    path: Path
    map_name: str
    source_type: str  # "vanilla" or "zip"
    zip_url: str | None = None
    mission_path: str | None = None
    has_storage: bool = False


def list_resettable_missions(config: DayzConfig) -> list[MissionResetInfo]:
    """Scan mpmissions/ and return missions with a known reset source."""
    from app.maps.map_ops import vanilla_mission
    from app.shared.seed_data import VANILLA_MAPS, WORKSHOP_MAPS

    mpmissions_dir = config.server_dir_path / "mpmissions"
    if not mpmissions_dir.is_dir():
        return []

    # Build lookup: template -> reset info
    template_lookup: dict[str, dict] = {}
    for name in VANILLA_MAPS:
        # An unreleased map has no mission yet, and None is not a template.
        template = vanilla_mission(config, name)
        if not template:
            continue
        template_lookup[template] = {"map_name": name, "source_type": "vanilla"}
    for name, data in WORKSHOP_MAPS.items():
        m = data["mission"]
        template_lookup[m["template"]] = {
            "map_name": name,
            "source_type": m.get("source", "zip"),
            "zip_url": m.get("zip_url"),
            "mission_path": m.get("mission_path"),
        }

    results: list[MissionResetInfo] = []
    for d in sorted(mpmissions_dir.iterdir()):
        if not d.is_dir():
            continue
        info = template_lookup.get(d.name)
        if not info:
            continue
        results.append(MissionResetInfo(
            template=d.name,
            path=d,
            map_name=info["map_name"],
            source_type=info["source_type"],
            zip_url=info.get("zip_url"),
            mission_path=info.get("mission_path"),
            has_storage=bool(storage_folders(d)),
        ))
    return results


def remerge_reset_mission(config: DayzConfig, template: str, log: Callable[[str], None]) -> None:
    """Put the mods' XML back into a community mission that `reset_mission_zip`
    just replaced, when it is the active mission.

    A plain Start does not merge, so the server would run every mod with
    none of their types. An inactive mission gets them at the switch to it
    (`map_ops.apply_mod_xml_to_new_mission`), and a stock mission's reset
    puts them back through `mission_stock`.
    """
    from app.maps.map_ops import apply_mod_xml_to_new_mission

    if template == get_current_mission(config.server_cfg_path):
        apply_mod_xml_to_new_mission(config, log)


def clear_mission_dir(mission_dir: Path, preserve_storage: bool) -> None:
    """Delete all contents of mission dir, optionally preserving its
    persistence: every ``storage_<n>`` folder, since one the server is not
    using now is still somebody's world."""
    for item in mission_dir.iterdir():
        if preserve_storage and is_storage_folder(item.name):
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()


def reset_mission_zip(
    zip_url: str,
    mission_path: str,
    mission_dir: Path,
    preserve_storage: bool,
    log=None,
) -> int:
    """Reset a workshop mission by re-downloading from GitHub.

    The download replaces the mission only once the archive is unpacked, and
    a kept world folder never moves (`map_ops.download_and_extract_mission`),
    so a failed download leaves the mission and its storage as they were.
    Moving the storage to a temp folder used to lose it when a failed
    download skipped the move back. Returns count of extracted files.
    """
    from app.maps.map_ops import download_and_extract_mission

    return download_and_extract_mission(
        zip_url, mission_path, mission_dir, log,
        keep_storage=preserve_storage, record_source=True,
    )
