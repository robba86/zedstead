"""What Changed — snapshot & diff logic with content summaries and baseline."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from xml.etree import ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from app.parsers.servercfg import is_storage_folder
from app.shared.file_views import overflow_link_line
from app.shared.json_utils import atomic_write_text

if TYPE_CHECKING:
    from app.shared.config import DayzConfig

# Both snapshots belong to one instance: config.paths.start_snapshot and
# config.paths.baseline_snapshot (plans/44).

TRACKED_CFG_KEYS = [
    "maxPlayers",
    "password",
    "passwordAdmin",
    "verifySignatures",
    "hostname",
    "disable3rdPerson",
    "thirdPersonView",
    "disableCrosshair",
    "respawnTime",
    "enableCfgGameplayFile",
    "template",
]

# Files that get deep content summaries (matched by exact relative path)
_SUMMARIZED_FILES = {
    "globals.xml",
    "types.xml",
    "events.xml",
    "cfggameplay.json",
}

def _storage_dirs(mission_dir: Path) -> set[str]:
    """Directories to skip when scanning the mission folder: its persistence,
    ``storage_<instanceId>`` whatever the id."""
    if not mission_dir.is_dir():
        return set()
    return {p.name for p in mission_dir.iterdir() if is_storage_folder(p.name)}

# Directories to skip when scanning the server root
_SERVER_EXCLUDED_DIRS = {
    "mpmissions",    # handled separately (current mission only)
    "steamapps",     # SteamCMD workshop cache
    "addons",        # game PBO files
    "dta",           # game data
    "bliss",         # terrain data
    "enoch",         # terrain data
    "keys",          # bikey files
    "logs",          # log files
}


def _scan_xml_json(
    root: Path,
    excluded_dirs: set[str],
    *,
    skip_at_dirs: bool = False,
) -> list[str]:
    """Recursively find all XML/JSON files under *root*.

    Args:
        root: Directory to scan.
        excluded_dirs: Top-level directory names to skip.
        skip_at_dirs: If True, skip top-level dirs starting with ``@``
            (mod PBO folders at the server root).
    """
    if not root.is_dir():
        return []
    result: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in (".xml", ".json"):
            continue
        rel = path.relative_to(root)
        # Skip hidden dirs/files
        if any(part.startswith(".") for part in rel.parts):
            continue
        top = rel.parts[0] if rel.parts else ""
        if top in excluded_dirs:
            continue
        if skip_at_dirs and top.startswith("@"):
            continue
        result.append(str(rel))
    return result


@dataclass
class StartSnapshot:
    timestamp: str
    mission: str
    mods: list[str]
    server_cfg: dict[str, str | None]
    file_hashes: dict[str, str]
    file_summaries: dict[str, Any] = field(default_factory=dict)
    file_sizes: dict[str, int] = field(default_factory=dict)
    server_file_hashes: dict[str, str] = field(default_factory=dict)
    server_file_sizes: dict[str, int] = field(default_factory=dict)


@dataclass
class ChangeItem:
    category: str
    description: str
    file_path: str = ""  # absolute path to the file (empty if not a file item)


@dataclass
class ChangeReport:
    snapshot_time: str
    changes: list[ChangeItem] = field(default_factory=list)
    unchanged_count: int = 0


# ---------------------------------------------------------------------------
# Content summary extractors
# ---------------------------------------------------------------------------

def _summarize_globals(path: Path) -> dict[str, str]:
    """Extract all variable name→value pairs from globals.xml."""
    from app.xml.globals_xml import parse_globals_file

    try:
        gf = parse_globals_file(path)
        return {v.name: v.value for v in gf.entries}
    except (OSError, ET.ParseError):
        return {}


def _summarize_types(path: Path) -> dict[str, Any]:
    """Extract type classnames and count from types.xml."""
    from app.xml.types_xml import parse_types_file

    try:
        tf = parse_types_file(path)
        names = sorted(e.name for e in tf.entries)
        return {"count": len(names), "names": names}
    except (OSError, ET.ParseError):
        return {"count": 0, "names": []}


def _summarize_events(path: Path) -> dict[str, Any]:
    """Extract event names and count from events.xml."""
    from app.xml.events_xml import parse_events_file

    try:
        ef = parse_events_file(path)
        names = sorted(e.name for e in ef.entries)
        return {"count": len(names), "names": names}
    except (OSError, ET.ParseError):
        return {"count": 0, "names": []}


def _flatten_dict(d: dict, prefix: str = "") -> dict[str, str]:
    """Flatten nested dict to dot-separated key→str(value) pairs."""
    out: dict[str, str] = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten_dict(v, key))
        elif isinstance(v, list):
            out[key] = json.dumps(v)
        else:
            out[key] = str(v)
    return out


def _summarize_cfggameplay(path: Path) -> dict[str, str]:
    """Extract flattened key→value pairs from cfggameplay.json."""
    from app.parsers.cfggameplay import read_cfggameplay

    try:
        data = read_cfggameplay(path)
        return _flatten_dict(data)
    except (OSError, ValueError):  # GameplayFileError: a file that can't be read
        return {}


def _extract_summary(rel: str, mission_dir: Path) -> Any | None:
    """Extract content summary for a known file, or None if not summarized."""
    if rel not in _SUMMARIZED_FILES:
        return None

    path = mission_dir / rel
    if not path.is_file():
        return None

    if rel == "globals.xml":
        return _summarize_globals(path)
    if rel == "types.xml":
        return _summarize_types(path)
    if rel == "events.xml":
        return _summarize_events(path)
    if rel == "cfggameplay.json":
        return _summarize_cfggameplay(path)
    return None


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def _hash_file(path: Path) -> str:
    """SHA256 of a file. Returns empty string if missing."""
    if not path.is_file():
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


# ---------------------------------------------------------------------------
# Size formatting
# ---------------------------------------------------------------------------

def _format_size(size: int) -> str:
    """Format byte count as human-readable size."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


# ---------------------------------------------------------------------------
# Snapshot capture / save / load
# ---------------------------------------------------------------------------

def capture_snapshot(config: DayzConfig) -> StartSnapshot:
    """Read current server state into a snapshot."""
    from app.modmgr.mods import get_current_mission
    from app.modmgr import modlist
    from app.parsers.servercfg import read_cfg_values, read_mission_template

    mission = get_current_mission(config.server_cfg_path) or ""
    mods = modlist.installed(config)
    server_cfg = read_cfg_values(
        config.server_cfg_path, [key for key in TRACKED_CFG_KEYS if key != "template"]
    )
    # Not a root key: DayZ reads it inside class Missions { class DayZ }. As
    # written, so a value `mission` cannot hold (no folder name) still shows.
    server_cfg["template"] = read_mission_template(config.server_cfg_path)

    file_hashes: dict[str, str] = {}
    file_summaries: dict[str, Any] = {}
    file_sizes: dict[str, int] = {}
    if mission:
        mission_dir = config.server_dir_path / "mpmissions" / mission
        for rel in _scan_xml_json(mission_dir, _storage_dirs(mission_dir)):
            fpath = mission_dir / rel
            file_hashes[rel] = _hash_file(fpath)
            try:
                file_sizes[rel] = fpath.stat().st_size
            except OSError:
                file_sizes[rel] = 0
            summary = _extract_summary(rel, mission_dir)
            if summary is not None:
                file_summaries[rel] = summary

    # Server-level files (profiles/, battleye/, etc.)
    server_file_hashes: dict[str, str] = {}
    server_file_sizes: dict[str, int] = {}
    server_dir = config.server_dir_path
    for rel in _scan_xml_json(
        server_dir, _SERVER_EXCLUDED_DIRS, skip_at_dirs=True
    ):
        fpath = server_dir / rel
        server_file_hashes[rel] = _hash_file(fpath)
        try:
            server_file_sizes[rel] = fpath.stat().st_size
        except OSError:
            server_file_sizes[rel] = 0

    return StartSnapshot(
        timestamp=datetime.now().isoformat(timespec="seconds"),
        mission=mission,
        mods=mods,
        server_cfg=server_cfg,
        file_hashes=file_hashes,
        file_summaries=file_summaries,
        file_sizes=file_sizes,
        server_file_hashes=server_file_hashes,
        server_file_sizes=server_file_sizes,
    )


def _save(snapshot: StartSnapshot, path: Path) -> None:
    """Write snapshot to disk, owner-only.

    A snapshot keeps serverDZ.cfg's tracked values in clear, `password` and
    `passwordAdmin` among them, so the change report can show what they were.
    `atomic_write_text` creates its file through mkstemp, which is 0600, and
    ``owner_only`` keeps it 0600 over an older 0644 file.
    """
    atomic_write_text(path, json.dumps(asdict(snapshot), indent=2), owner_only=True)


def _load(path: Path) -> StartSnapshot | None:
    """Load saved snapshot, or None if missing/corrupt."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # Backward compat: old snapshots lack newer fields
        data.setdefault("file_summaries", {})
        data.setdefault("file_sizes", {})
        data.setdefault("server_file_hashes", {})
        data.setdefault("server_file_sizes", {})
        # Before 2026 Q4 `template` was read as a root key and was always
        # None; the snapshot's mission is what it held. Without this, every
        # older snapshot reports `template: (empty) → <mission>`.
        cfg = data.get("server_cfg")
        if isinstance(cfg, dict) and cfg.get("template") is None and data.get("mission"):
            cfg["template"] = data["mission"]
        return StartSnapshot(**data)
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def save_snapshot(config: DayzConfig, snapshot: StartSnapshot) -> None:
    """Write last-start snapshot to disk."""
    _save(snapshot, config.paths.start_snapshot)


def load_snapshot(config: DayzConfig) -> StartSnapshot | None:
    """Load last-start snapshot."""
    return _load(config.paths.start_snapshot)


def take_snapshot(config: DayzConfig) -> None:
    """Capture and save a last-start snapshot (convenience wrapper).

    Also auto-creates the baseline on first run.
    """
    snap = capture_snapshot(config)
    save_snapshot(config, snap)
    if not has_baseline(config):
        save_baseline(config, snap)


def take_start_snapshot(config: DayzConfig) -> None:
    """``take_snapshot`` for a start path: a failure is logged, never raised.

    Every way zsm starts the server calls this just before it does:
    ``service_control.start_server`` / ``restart_server`` (web Start and
    Restart, the scheduled update), the CLI's ``zsm start`` / ``restart`` /
    ``service update``, and ``zsm supervise``'s launch loop. Only the web
    Start button and the update pipeline used to, so "Since last start"
    compared against a snapshot two days and ~14 starts old (test pass 1,
    F51). A start none of these made is caught afterwards by
    ``snapshot_unrecorded_start``. Nothing about a snapshot is worth refusing
    a start over.
    """
    import logging

    try:
        take_snapshot(config)
    except Exception:
        logging.getLogger(__name__).warning("Start snapshot failed", exc_info=True)


# How far before a start a snapshot may be taken and still count as that
# start's: the start paths above snapshot a moment before they start. The
# Analysis page's "snapshot is older than this run" line uses the same minute.
START_SNAPSHOT_SLACK = timedelta(minutes=1)


def snapshot_missing_for(config: DayzConfig, started_at: datetime) -> bool:
    """True when no snapshot was taken for the run that started at *started_at*.

    *started_at* is naive local time, as ``server_status.get_server_start_time``
    returns it and as snapshots are stamped.
    """
    snap = load_snapshot(config)
    if snap is None:
        return True
    try:
        taken = datetime.fromisoformat(snap.timestamp)
    except (TypeError, ValueError):
        return True
    return taken < started_at - START_SNAPSHOT_SLACK


def snapshot_unrecorded_start(config: DayzConfig, started_at: datetime | None) -> bool:
    """Take the start snapshot for a run zsm did not start itself.

    The scheduled ``restart`` task (a root oneshot running ``systemctl
    restart``) and systemd's own restart after a crash both start the server
    without any zsm code running first, so ``take_start_snapshot`` never ran
    for them. ``zsm web``'s notifier calls this on a timer instead. Taken
    after the start rather than before it, so an edit made in the first
    seconds of a run is part of the snapshot. Returns True when it took one.
    """
    if started_at is None or not snapshot_missing_for(config, started_at):
        return False
    take_start_snapshot(config)
    return True


# ---------------------------------------------------------------------------
# Baseline snapshot
# ---------------------------------------------------------------------------

def save_baseline(config: DayzConfig, snapshot: StartSnapshot) -> None:
    """Write baseline snapshot to disk."""
    _save(snapshot, config.paths.baseline_snapshot)


def load_baseline(config: DayzConfig) -> StartSnapshot | None:
    """Load baseline snapshot."""
    return _load(config.paths.baseline_snapshot)


def has_baseline(config: DayzConfig) -> bool:
    return config.paths.baseline_snapshot.is_file()


def take_baseline(config: DayzConfig) -> None:
    """Capture and save a new baseline snapshot."""
    save_baseline(config, capture_snapshot(config))


def reset_baseline(config: DayzConfig) -> None:
    """Remove the baseline snapshot."""
    config.paths.baseline_snapshot.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Diffing with content summaries
# ---------------------------------------------------------------------------

def _diff_globals(old_sum: dict[str, str], new_sum: dict[str, str]) -> list[str]:
    """Diff globals.xml variable values, return detail lines."""
    lines: list[str] = []
    all_keys = sorted(set(old_sum) | set(new_sum))
    for k in all_keys:
        old_v = old_sum.get(k)
        new_v = new_sum.get(k)
        if old_v == new_v:
            continue
        if old_v is None:
            lines.append(f"    + {k} = {new_v}")
        elif new_v is None:
            lines.append(f"    - {k}")
        else:
            lines.append(f"    {k}: {old_v} → {new_v}")
    return lines


def _diff_names(old_sum: dict, new_sum: dict, label: str, views_dir: Path) -> list[str]:
    """Diff types.xml or events.xml name lists, return detail lines."""
    old_names = set(old_sum.get("names", []))
    new_names = set(new_sum.get("names", []))
    old_count = old_sum.get("count", len(old_names))
    new_count = new_sum.get("count", len(new_names))
    added = sorted(new_names - old_names)
    removed = sorted(old_names - new_names)
    lines: list[str] = []
    if old_count != new_count:
        lines.append(f"    {label} count: {old_count} → {new_count}")
    if added:
        show = added[:15]
        lines.append(f"    + {', '.join(show)}")
        if len(added) > 15:
            lines.append(
                overflow_link_line(
                    views_dir=views_dir,
                    indent="      ",
                    hidden=len(added) - 15,
                    full_lines=list(added),
                    file_label=f"{label.lower()}s-added",
                )
            )
    if removed:
        show = removed[:15]
        lines.append(f"    - {', '.join(show)}")
        if len(removed) > 15:
            lines.append(
                overflow_link_line(
                    views_dir=views_dir,
                    indent="      ",
                    hidden=len(removed) - 15,
                    full_lines=list(removed),
                    file_label=f"{label.lower()}s-removed",
                )
            )
    return lines


def _diff_cfggameplay(
    old_sum: dict[str, str], new_sum: dict[str, str], views_dir: Path
) -> list[str]:
    """Diff cfggameplay.json flattened values, return detail lines."""
    lines: list[str] = []
    all_keys = sorted(set(old_sum) | set(new_sum))
    for k in all_keys:
        old_v = old_sum.get(k)
        new_v = new_sum.get(k)
        if old_v == new_v:
            continue
        if old_v is None:
            lines.append(f"    + {k} = {new_v}")
        elif new_v is None:
            lines.append(f"    - {k}")
        else:
            lines.append(f"    {k}: {old_v} → {new_v}")
    # Cap very long diffs
    if len(lines) > 20:
        full = list(lines)
        truncated = len(full) - 15
        lines = full[:15]
        lines.append(
            overflow_link_line(
                views_dir=views_dir,
                indent="    ",
                hidden=truncated,
                full_lines=full,
                file_label="cfggameplay-changes",
                suffix="changes",
            )
        )
    return lines


def _get_file_detail(
    rel: str,
    old_sum: Any | None,
    new_sum: Any | None,
    views_dir: Path,
) -> list[str]:
    """Get detailed diff lines for a summarized file."""
    if old_sum is None or new_sum is None:
        return []

    if rel == "globals.xml":
        return _diff_globals(old_sum, new_sum)
    if rel == "types.xml":
        return _diff_names(old_sum, new_sum, "Type", views_dir)
    if rel == "events.xml":
        return _diff_names(old_sum, new_sum, "Event", views_dir)
    if rel == "cfggameplay.json":
        return _diff_cfggameplay(old_sum, new_sum, views_dir)
    return []


# ---------------------------------------------------------------------------
# Mod identity helpers
# ---------------------------------------------------------------------------

def _parse_mod_entry(entry: str) -> tuple[str, str]:
    """Parse '@Name=workshopID' → (name, id).  No '=' → (entry, '')."""
    from app.modmgr.mods import split_name_id

    return split_name_id(entry) or (entry, "")


def _build_id_lookup(config: DayzConfig | None = None) -> dict[str, str]:
    """Build @Name → workshopID lookup from seed_data and *config*'s cached IDs.

    The config is the caller's, never `DayzConfig.load()`: in a panel serving
    several instances that would be the startup instance's (plans/62 step 0).
    """
    from app.modmgr.mods import split_name_id
    from app.shared.seed_data import WORKSHOP_MOD_IDS

    lookup: dict[str, str] = {}
    for line in WORKSHOP_MOD_IDS.strip().splitlines():
        entry = split_name_id(line)
        if entry:
            lookup[entry[0]] = entry[1]
    if config is None:
        return lookup
    try:
        from app.modmgr import modlist

        lookup.update(modlist.workshop_ids(config))
    except OSError:
        pass
    return lookup


def _mods_by_id(mods: list[str], lookup: dict[str, str]) -> dict[str, str]:
    """Map workshop ID → mod name.  Resolves bare @Name entries via lookup."""
    out: dict[str, str] = {}
    for entry in mods:
        name, mid = _parse_mod_entry(entry)
        if not mid:
            mid = lookup.get(name, "")
        out[mid or name] = name
    return out


def _mod_display(name: str, mid: str) -> str:
    """Format a mod for display: '@Name (id)' or just the name."""
    return f"{name} ({mid})" if mid and mid.isdigit() else name


# ---------------------------------------------------------------------------
# Compute changes
# ---------------------------------------------------------------------------

def _diff_snapshots(
    old: StartSnapshot,
    now: StartSnapshot,
    config: DayzConfig | None = None,
) -> ChangeReport:
    """Compare two snapshots and produce a ChangeReport."""
    from app.shared.config import DayzConfig

    report = ChangeReport(snapshot_time=old.timestamp)
    # Long lists overflow into this instance's views/ (a blank config's paths
    # are the pre-instances layout).
    views_dir = (config or DayzConfig()).paths.views_dir

    # Resolve base directories for file links
    _mission_dir = (
        config.server_dir_path / "mpmissions" / now.mission
        if config and now.mission
        else None
    )
    _server_dir = config.server_dir_path if config else None

    # Mission change
    map_changed = bool(old.mission and now.mission and old.mission != now.mission)
    if map_changed:
        report.changes.append(
            ChangeItem("mission", f"Map changed: {old.mission} → {now.mission}")
        )

    # Mod diff — compare by workshop ID so reinstalls with different names
    # don't show as removed+added
    id_lookup = _build_id_lookup(config)
    old_by_id = _mods_by_id(old.mods, id_lookup)
    new_by_id = _mods_by_id(now.mods, id_lookup)
    added_ids = sorted(set(new_by_id) - set(old_by_id))
    removed_ids = sorted(set(old_by_id) - set(new_by_id))
    renamed: list[tuple[str, str]] = []
    for mid in sorted(set(old_by_id) & set(new_by_id)):
        old_name = old_by_id[mid]
        new_name = new_by_id[mid]
        if old_name != new_name:
            renamed.append((old_name, new_name))
    if added_ids or removed_ids or renamed:
        parts = []
        if added_ids:
            parts.append(f"{len(added_ids)} added")
        if removed_ids:
            parts.append(f"{len(removed_ids)} removed")
        if renamed:
            parts.append(f"{len(renamed)} renamed")
        report.changes.append(ChangeItem("mods_header", f"Mods: {', '.join(parts)}"))
        for mid in added_ids:
            report.changes.append(
                ChangeItem("mods_add", f"  + {_mod_display(new_by_id[mid], mid)}")
            )
        for mid in removed_ids:
            report.changes.append(
                ChangeItem("mods_remove", f"  - {_mod_display(old_by_id[mid], mid)}")
            )
        for old_name, new_name in renamed:
            report.changes.append(
                ChangeItem("mods_rename", f"  ~ {old_name} → {new_name}")
            )

    # Server config diff
    cfg_changes: list[str] = []
    for key in TRACKED_CFG_KEYS:
        if key == "template" and map_changed:
            continue  # the same switch, said once by "Map changed" above
        old_val = old.server_cfg.get(key)
        new_val = now.server_cfg.get(key)
        if old_val != new_val:
            old_display = old_val if old_val else "(empty)"
            new_display = new_val if new_val else "(empty)"
            cfg_changes.append(f"  {key}: {old_display} → {new_display}")
    if cfg_changes:
        report.changes.append(
            ChangeItem("cfg_header", f"serverDZ.cfg: {len(cfg_changes)} change(s)")
        )
        for line in cfg_changes:
            report.changes.append(ChangeItem("cfg", line))

    # File diff with content summaries
    unchanged = 0
    file_changes: list[ChangeItem] = []
    all_rels = sorted(set(old.file_hashes) | set(now.file_hashes))
    for rel in all_rels:
        old_hash = old.file_hashes.get(rel, "")
        new_hash = now.file_hashes.get(rel, "")
        if old_hash == new_hash:
            if old_hash:
                unchanged += 1
            continue

        _abs = str(_mission_dir / rel) if _mission_dir else ""

        if not old_hash and new_hash:
            size_info = ""
            new_size = now.file_sizes.get(rel)
            if new_size is not None:
                size_info = f", {_format_size(new_size)}"
            file_changes.append(
                ChangeItem("files_new", f"  {rel} [green](new{size_info})[/green]", file_path=_abs)
            )
        elif old_hash and not new_hash:
            file_changes.append(
                ChangeItem("files_removed", f"  {rel} [red](removed)[/red]")
            )
        else:
            # Modified — show size delta when available
            size_info = ""
            old_size = old.file_sizes.get(rel)
            new_size = now.file_sizes.get(rel)
            if old_size is not None and new_size is not None:
                size_info = (
                    f" ({_format_size(old_size)} → {_format_size(new_size)})"
                )
            file_changes.append(ChangeItem("files", f"  {rel}{size_info}", file_path=_abs))
            # Add detailed diff from content summaries
            old_sum = old.file_summaries.get(rel)
            new_sum = now.file_summaries.get(rel)
            for detail in _get_file_detail(rel, old_sum, new_sum, views_dir):
                file_changes.append(ChangeItem("files_detail", detail))

    if file_changes:
        total_changed = len([c for c in file_changes if c.category != "files_detail"])
        report.changes.append(
            ChangeItem(
                "files_header",
                f"Mission files: {total_changed} of {len(all_rels)} changed",
            )
        )
        report.changes.extend(file_changes)

    # Server-level file diff (profiles/, battleye/, etc.)
    srv_changes: list[ChangeItem] = []
    srv_rels = sorted(set(old.server_file_hashes) | set(now.server_file_hashes))
    for rel in srv_rels:
        old_hash = old.server_file_hashes.get(rel, "")
        new_hash = now.server_file_hashes.get(rel, "")
        if old_hash == new_hash:
            if old_hash:
                unchanged += 1
            continue

        _srv_abs = str(_server_dir / rel) if _server_dir else ""

        if not old_hash and new_hash:
            size_info = ""
            new_size = now.server_file_sizes.get(rel)
            if new_size is not None:
                size_info = f", {_format_size(new_size)}"
            srv_changes.append(
                ChangeItem("files_new", f"  {rel} [green](new{size_info})[/green]", file_path=_srv_abs)
            )
        elif old_hash and not new_hash:
            srv_changes.append(
                ChangeItem("files_removed", f"  {rel} [red](removed)[/red]")
            )
        else:
            size_info = ""
            old_size = old.server_file_sizes.get(rel)
            new_size = now.server_file_sizes.get(rel)
            if old_size is not None and new_size is not None:
                size_info = (
                    f" ({_format_size(old_size)} → {_format_size(new_size)})"
                )
            srv_changes.append(ChangeItem("files", f"  {rel}{size_info}", file_path=_srv_abs))

    if srv_changes:
        total_srv = len([c for c in srv_changes if c.category != "files_detail"])
        report.changes.append(
            ChangeItem(
                "files_header",
                f"Server files: {total_srv} of {len(srv_rels)} changed",
            )
        )
        report.changes.extend(srv_changes)

    report.unchanged_count = unchanged
    return report


def compute_changes(config: DayzConfig) -> ChangeReport | None:
    """Compare current state against saved last-start snapshot."""
    old = load_snapshot(config)
    if old is None:
        return None
    now = capture_snapshot(config)
    return _diff_snapshots(old, now, config=config)


def compute_baseline_changes(config: DayzConfig) -> ChangeReport | None:
    """Compare current state against saved baseline snapshot."""
    old = load_baseline(config)
    if old is None:
        return None
    now = capture_snapshot(config)
    return _diff_snapshots(old, now, config=config)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _format_time_ago(snapshot_time: str) -> str:
    """Format a snapshot timestamp as 'Xh Ym ago (YYYY-MM-DD HH:MM)'."""
    try:
        snap_dt = datetime.fromisoformat(snapshot_time)
        delta = datetime.now() - snap_dt
        total_secs = int(delta.total_seconds())
        if total_secs < 60:
            ago = f"{total_secs}s ago"
        elif total_secs < 3600:
            ago = f"{total_secs // 60}m ago"
        elif total_secs < 86400:
            hours = total_secs // 3600
            mins = (total_secs % 3600) // 60
            ago = f"{hours}h {mins}m ago" if mins else f"{hours}h ago"
        else:
            days = total_secs // 86400
            ago = f"{days}d ago"
        time_str = snap_dt.strftime("%Y-%m-%d %H:%M")
        return f"{ago} ({time_str})"
    except (ValueError, TypeError):
        return snapshot_time


def format_change_report(
    report: ChangeReport,
    *,
    label: str = "last start",
    file_link_fn: Callable[[str, str], str] | None = None,
) -> list[str]:
    """Format a ChangeReport as Rich-markup lines.

    Args:
        report: The change report to format.
        label: Human label for the comparison base (e.g. "last start").
        file_link_fn: Optional callback ``(display_name, abs_path) -> markup``
            that wraps file names with clickable links. When *None*, plain
            text is used (CLI default).
    """
    lines: list[str] = []

    lines.append(f"Snapshot from {_format_time_ago(report.snapshot_time)}")

    if not report.changes:
        lines.append("")
        lines.append(f"[green]No changes since {label}.[/green]")
        return lines

    lines.append("")
    for item in report.changes:
        if item.category.endswith("_header"):
            lines.append(f"[bold]{item.description}[/bold]")
        elif item.category == "mission":
            lines.append(f"[yellow]{item.description}[/yellow]")
        elif item.category == "mods_add":
            lines.append(f"[green]{item.description}[/green]")
        elif item.category == "mods_remove":
            lines.append(f"[red]{item.description}[/red]")
        elif item.category == "mods_rename":
            lines.append(f"[yellow]{item.description}[/yellow]")
        elif item.category == "files_detail":
            lines.append(f"[dim]{item.description}[/dim]")
        elif item.category in ("cfg", "files", "files_new", "files_removed"):
            if item.file_path and file_link_fn:
                lines.append(_linkify_file_line(item, file_link_fn))
            else:
                lines.append(item.description)
        else:
            lines.append(item.description)

    if report.unchanged_count:
        lines.append("")
        lines.append(f"[dim]{report.unchanged_count} file(s) unchanged[/dim]")

    return lines


def _linkify_file_line(
    item: ChangeItem,
    file_link_fn: Callable[[str, str], str],
) -> str:
    """Replace the filename portion of a file ChangeItem with a clickable link."""
    desc = item.description
    stripped = desc.lstrip()
    spaces = desc[: len(desc) - len(stripped)]

    # Split at first markup tag or size annotation to isolate the filename
    for sep in (" [", " ("):
        pos = stripped.find(sep)
        if pos > 0:
            name_part = stripped[:pos]
            rest = stripped[pos:]
            return f"{spaces}{file_link_fn(name_part, item.file_path)}{rest}"

    return f"{spaces}{file_link_fn(stripped, item.file_path)}"
