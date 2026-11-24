"""Mod rollback — pre-update snapshots of workshop mods.

Before downloading mod updates, the current mod directory is snapshotted
into ``<instance>/mod_backups/<mod_id>_<timestamp>/`` — per instance, since
the mod set differs per server; callers pass ``config.paths.mod_backups_dir``
(plans/44).
Old snapshots are rotated so only ``mod_backup_max_versions`` are kept
per mod.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.shared.zip_utils import copytree_without_links

if TYPE_CHECKING:
    from app.shared.config import DayzConfig



@dataclass
class ModSnapshot:
    """Metadata for a single mod backup snapshot."""

    mod_id: str
    timestamp: float  # Unix epoch
    mod_name: str = ""
    size_bytes: int = 0

    @property
    def dir_name(self) -> str:
        # Use microsecond precision to avoid collisions in rapid succession
        return f"{self.mod_id}_{int(self.timestamp * 1000)}"

    @property
    def label(self) -> str:
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.timestamp))
        name = self.mod_name or self.mod_id
        size = _format_size(self.size_bytes)
        return f"{name} — {ts} ({size})"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ModSnapshot:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Snapshot creation
# ---------------------------------------------------------------------------


def create_snapshot(
    workshop_dir: Path,
    mod_id: str,
    mod_name: str = "",
    *,
    backup_dir: Path,
) -> ModSnapshot | None:
    """Snapshot a mod directory before updating.

    Returns the snapshot metadata, or None if the mod directory doesn't exist.
    """
    src = workshop_dir / mod_id
    if not src.is_dir():
        return None

    now = time.time()
    snap = ModSnapshot(
        mod_id=mod_id,
        timestamp=now,
        mod_name=mod_name,
        size_bytes=dir_size(src),
    )

    dest_root = backup_dir
    dest = dest_root / snap.dir_name
    dest.mkdir(parents=True, exist_ok=True)

    # Copy mod contents, without its links: copytree's default copies what a
    # link points at, which can be any file on this machine (plans/68 CON-4).
    copytree_without_links(src, dest / "mod", dirs_exist_ok=True)

    # Write metadata sidecar
    meta_path = dest / "meta.json"
    meta_path.write_text(json.dumps(snap.to_dict(), indent=2), encoding="utf-8")

    return snap


def snapshot_mods(
    config: DayzConfig,
    mod_ids: list[str],
    mod_names: dict[str, str] | None = None,
) -> list[ModSnapshot]:
    """Snapshot multiple mods before an update batch.

    Respects ``config.mod_backup_before_update``. Automatically rotates
    old snapshots after creating new ones.
    """
    if not getattr(config, "mod_backup_before_update", True):
        return []

    names = mod_names or {}
    created: list[ModSnapshot] = []

    for mid in mod_ids:
        snap = create_snapshot(
            config.workshop_dir, mid, mod_name=names.get(mid, ""),
            backup_dir=config.paths.mod_backups_dir,
        )
        if snap:
            created.append(snap)
            max_ver = getattr(config, "mod_backup_max_versions", 2)
            rotate_snapshots(mid, max_versions=max_ver, backup_dir=config.paths.mod_backups_dir)

    return created


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def list_snapshots(
    mod_id: str | None = None,
    *,
    backup_dir: Path,
) -> list[ModSnapshot]:
    """List available snapshots, newest first.

    If ``mod_id`` is given, only return snapshots for that mod.
    """
    root = backup_dir
    if not root.is_dir():
        return []

    snapshots: list[ModSnapshot] = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        meta_path = entry / "meta.json"
        if not meta_path.is_file():
            continue
        snap = _read_meta(meta_path)
        if snap is None:
            continue
        if mod_id and snap.mod_id != mod_id:
            continue
        snapshots.append(snap)

    snapshots.sort(key=lambda s: s.timestamp, reverse=True)
    return snapshots


def list_mod_ids_with_snapshots(*, backup_dir: Path) -> list[str]:
    """Return unique mod IDs that have at least one snapshot."""
    seen: set[str] = set()
    for snap in list_snapshots(backup_dir=backup_dir):
        seen.add(snap.mod_id)
    return sorted(seen)


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def restore_snapshot(
    workshop_dir: Path,
    snapshot: ModSnapshot,
    *,
    backup_dir: Path,
) -> bool:
    """Restore a mod from a snapshot.

    Replaces the current mod directory with the snapshot contents.
    Returns True on success.

    The snapshot is copied beside the folder first and swapped in only once
    the copy is whole, so a failed copy leaves the current folder, which the
    workshop ACF still lists. A dot name, so nothing reads it as a Workshop ID.
    """
    snap_dir = backup_dir / snapshot.dir_name / "mod"
    if not snap_dir.is_dir():
        return False

    dest = workshop_dir / snapshot.mod_id
    staging = Path(tempfile.mkdtemp(prefix=f".{snapshot.mod_id}.restore-", dir=workshop_dir))
    old = staging.with_name(staging.name + "-old")
    try:
        copytree_without_links(snap_dir, staging / "mod")
        if dest.exists():
            dest.rename(old)
        try:
            (staging / "mod").rename(dest)
        except BaseException:
            if old.exists():
                old.rename(dest)
            raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(old, ignore_errors=True)
    return True


# ---------------------------------------------------------------------------
# Rotation / deletion
# ---------------------------------------------------------------------------


def rotate_snapshots(
    mod_id: str,
    max_versions: int = 2,
    *,
    backup_dir: Path,
) -> int:
    """Delete oldest snapshots for a mod, keeping ``max_versions``.

    Returns the number of snapshots deleted.
    """
    snaps = list_snapshots(mod_id=mod_id, backup_dir=backup_dir)
    if len(snaps) <= max_versions:
        return 0

    root = backup_dir
    to_remove = snaps[max_versions:]
    deleted = 0
    for snap in to_remove:
        snap_path = root / snap.dir_name
        if snap_path.is_dir():
            shutil.rmtree(snap_path)
            deleted += 1

    return deleted


def delete_snapshot(
    snapshot: ModSnapshot,
    *,
    backup_dir: Path,
) -> bool:
    """Delete a specific snapshot. Returns True if removed."""
    snap_path = backup_dir / snapshot.dir_name
    if snap_path.is_dir():
        shutil.rmtree(snap_path)
        return True
    return False


def delete_all_snapshots(
    mod_id: str | None = None,
    *,
    backup_dir: Path,
) -> int:
    """Delete all snapshots (or all for a specific mod). Returns count."""
    snaps = list_snapshots(mod_id=mod_id, backup_dir=backup_dir)
    root = backup_dir
    deleted = 0
    for snap in snaps:
        snap_path = root / snap.dir_name
        if snap_path.is_dir():
            shutil.rmtree(snap_path)
            deleted += 1
    return deleted


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_meta(meta_path: Path) -> ModSnapshot | None:
    """A snapshot's ``meta.json``, or None when it is not UTF-8 JSON, not an
    object, or has no numeric timestamp (the sort and the folder name need one)."""
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # JSON or its encoding
        return None
    if not isinstance(data, dict):
        return None
    timestamp = data.get("timestamp")
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        return None
    try:
        return ModSnapshot.from_dict(data)
    except TypeError:  # a required field is missing
        return None


def dir_size(path: Path) -> int:
    """Recursively calculate directory size in bytes."""
    total = 0
    try:
        for f in path.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    except OSError:
        pass
    return total


def _format_size(size_bytes: int) -> str:
    """Human-readable file size."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"
