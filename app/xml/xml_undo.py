"""Undo of a merge, and the strip that `zsm run rollback-xml` runs.

The pipeline's `merge_xml` undo used to copy every `<file>.backup` back. Those
are written once, at the first merge, and never again, so an undo put back the
files of the game version and the admin edits of that day: after DayZ 1.30 it
removed `VehicleMotorbike` and the `MotorbikeScript` rootclass (browser test
2026-11-17, F8). Now the merge step keeps the files it is about to write, one
generation, and the undo puts back exactly those.

`strip_mod_xml` is the rollback without a run to undo: it removes everything
Zedstead owns in the mission (every START/END MOD block, the `<ce folder="custom">`
registrations and `custom/`) and leaves Bohemia's content and the admin's own
lines. The next merge puts the mods and the manual files back.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Callable

from app.shared.config import DayzConfig
from app.shared.json_utils import atomic_write_text, copy_file

LogFn = Callable[[str], None]

_MANIFEST = "manifest.json"
_FILES = "files"
_CUSTOM = "custom"


class NothingToUndo(RuntimeError):
    """No merge of this mission was kept, so there is nothing to put back."""


class ChangedSinceMerge(RuntimeError):
    """A file the merge wrote was changed afterwards (an editor, the Mod XML
    page), so putting back the old copy would delete that change. The undo
    refuses and writes nothing (maintainer's choice, 2026-11-17)."""


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _state(mission_dir: Path) -> dict[str, str]:
    """{mission-relative name: sha256} for every merge target and custom/ file."""
    out = {
        p.relative_to(mission_dir).as_posix(): _digest(p)
        for p in _targets(mission_dir) if p.is_file()
    }
    custom = mission_dir / _CUSTOM
    if custom.is_dir():
        for p in sorted(custom.rglob("*")):
            if p.is_file():
                out[p.relative_to(mission_dir).as_posix()] = _digest(p)
    return out


def _read_manifest(undo_dir: Path) -> dict | None:
    try:
        manifest = json.loads((undo_dir / _MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return manifest if isinstance(manifest, dict) else None


def record_after_merge(config: DayzConfig, mission: str) -> None:
    """Note what the merge left, so the undo can tell a later change apart."""
    undo_dir = config.paths.xml_undo_dir
    manifest = _read_manifest(undo_dir)
    if manifest is None or manifest.get("mission") != mission:
        return
    manifest["after"] = _state(config.server_dir_path / "mpmissions" / mission)
    atomic_write_text(undo_dir / _MANIFEST, json.dumps(manifest, indent=2) + "\n")


def _targets(mission_dir: Path) -> list[Path]:
    """Every file a merge writes, apart from `custom/`."""
    from app.xml.xml_configs import ALL_MERGE_CONFIGS
    from app.xml.xml_merge import mission_target_path

    server_dir = mission_dir.parent.parent
    return [mission_dir / "cfgeconomycore.xml"] + [
        mission_target_path(server_dir, mission_dir.name, cfg)
        for cfg in ALL_MERGE_CONFIGS.values()
    ]


def keep_before_merge(config: DayzConfig, mission: str) -> None:
    """Keep the files a merge of *mission* is about to write, replacing the
    previous generation. A file that does not exist is not listed, so the undo
    leaves it alone."""
    mission_dir = config.server_dir_path / "mpmissions" / mission
    undo_dir = config.paths.xml_undo_dir
    staging = undo_dir.with_name(undo_dir.name + ".new")
    if staging.exists():
        shutil.rmtree(staging)
    files_dir = staging / _FILES
    files_dir.mkdir(parents=True)

    kept: list[str] = []
    for path in _targets(mission_dir):
        if path.is_file():
            rel = path.relative_to(mission_dir).as_posix()
            (files_dir / rel).parent.mkdir(parents=True, exist_ok=True)
            copy_file(path, files_dir / rel)
            kept.append(rel)
    custom = mission_dir / _CUSTOM
    has_custom = custom.is_dir()
    if has_custom:
        shutil.copytree(custom, files_dir / _CUSTOM, symlinks=True)
    atomic_write_text(
        staging / _MANIFEST,
        json.dumps({"mission": mission, "files": kept, "custom": has_custom}, indent=2) + "\n",
    )

    if undo_dir.exists():
        shutil.rmtree(undo_dir)
    os.replace(staging, undo_dir)


def undo_merge(config: DayzConfig, mission: str, *, log: LogFn | None = None) -> list[str]:
    """Put back the files `keep_before_merge` kept for *mission*, then drop
    them, so a second undo cannot apply them again. Returns the files put back.

    Raises `NothingToUndo` when no merge of this mission was kept: a rollback
    that restores nothing must not read as done. Raises `ChangedSinceMerge`,
    and writes nothing, when a file differs from what the merge left, or when
    the merge never recorded what it left.
    """
    from app.xml.xml_utils import one_version_per_file, snapshot_before_write

    undo_dir = config.paths.xml_undo_dir
    manifest = _read_manifest(undo_dir)
    if manifest is None or manifest.get("mission") != mission:
        raise NothingToUndo(
            f"No copy of {mission}'s mission files from before the last merge is kept, "
            "so the merge cannot be undone."
        )

    mission_dir = config.server_dir_path / "mpmissions" / mission
    after = manifest.get("after")
    if not isinstance(after, dict):
        raise ChangedSinceMerge(
            "The last merge did not finish, so Zedstead cannot tell what it changed. "
            "Nothing was undone. Use Analysis → Change history to put back a file."
        )
    now = _state(mission_dir)
    changed = sorted(name for name in set(after) | set(now) if after.get(name) != now.get(name))
    if changed:
        shown = ", ".join(changed[:5]) + (f" and {len(changed) - 5} more" if len(changed) > 5 else "")
        raise ChangedSinceMerge(
            f"{shown} changed after the merge, and the undo would delete that change. "
            "Nothing was undone. Use Analysis → Change history to put back a file."
        )
    files_dir = undo_dir / _FILES
    allowed = {p.relative_to(mission_dir).as_posix() for p in _targets(mission_dir)}
    restored: list[str] = []
    with one_version_per_file():
        for rel in manifest.get("files", []):
            # The manifest is a file on disk: only a name the merge writes is
            # joined onto the mission path.
            if rel not in allowed or not (files_dir / rel).is_file():
                continue
            target = mission_dir / rel
            snapshot_before_write(target, trigger="merge_undo")
            copy_file(files_dir / rel, target)
            restored.append(rel)
    if manifest.get("custom") and (files_dir / _CUSTOM).is_dir():
        custom = mission_dir / _CUSTOM
        if custom.exists():
            shutil.rmtree(custom)
        shutil.copytree(files_dir / _CUSTOM, custom, symlinks=True)
        restored.append(f"{_CUSTOM}/")

    shutil.rmtree(undo_dir)
    if log:
        log(f"Put back {len(restored)} mission file(s) as they were before the merge.")
    return restored


def strip_mod_xml(server_dir: Path, mission: str, *, log: LogFn | None = None) -> int:
    """Remove everything a merge put into *mission*. Returns how many mod
    blocks and custom/ registrations went."""
    from defusedxml.minidom import parse as safe_minidom_parse

    from app.xml.xml_economy import _clear_custom_folder, _custom_ce_elements, _update_cfgeconomycore
    from app.xml.xml_merge import strip_all_mod_blocks
    from app.xml.xml_utils import one_version_per_file, snapshot_before_write

    mission_dir = server_dir / "mpmissions" / mission
    removed = 0
    with one_version_per_file():
        for path in _targets(mission_dir)[1:]:
            removed += strip_all_mod_blocks(path, log=log)
        core = mission_dir / "cfgeconomycore.xml"
        if core.is_file():
            # Read the registrations themselves, not `registered_custom_entries`,
            # which leaves out one whose custom/ file is already gone.
            blocks = _custom_ce_elements(safe_minidom_parse(str(core)))
            if blocks:
                snapshot_before_write(core, trigger="merge_undo")
                _update_cfgeconomycore(mission_dir, [], log)
                removed += sum(len(ce.getElementsByTagName("file")) for ce in blocks)
    if (mission_dir / _CUSTOM).is_dir():
        _clear_custom_folder(mission_dir)
    return removed
