"""Shared path resolution for mission-relative config files."""

from __future__ import annotations

import functools
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Callable, Iterator

from app.modmgr.mods import get_current_mission
from app.shared.config import DayzConfig


def mission_dir(config: DayzConfig) -> Path | None:
    mission = get_current_mission(config.server_cfg_path)
    if not mission:
        return None
    return config.server_dir_path / "mpmissions" / mission


def restore_from(
    path_getter: Callable[[DayzConfig], Path | None],
) -> Callable[[DayzConfig], bool]:
    """Build an `EditorSpec.restore` from the editor's own path getter.

    Registered **only** on the editors whose file zsm itself backs up — the
    five `ALL_MERGE_CONFIGS` targets plus cfgeconomycore.xml, which every merge
    calls `xml_merge.ensure_backup` on. Anywhere else there is no `.backup` to
    restore from unless the admin made one by hand, so a button would 404 at
    runtime and mean nothing.

    Puts back the installed game version's own copy when Zedstead holds one
    (`mission_stock.shipped_copy`), else the `.backup`.

    Goes through `xml_utils.restore_file`, which snapshots before it copies —
    since merges stopped restoring these files (2026 Q3) this is the one
    click that discards an admin's accumulated edits, and Analysis → Change history is
    the undo. Derived from the editor's own getter rather than a second path map
    so the two can't point at different files.
    """
    from app.ops.mission_stock import shipped_copy
    from app.xml.xml_utils import restore_file

    def _restore(config: DayzConfig) -> bool:
        path = path_getter(config)
        return bool(path) and restore_file(Path(path), shipped_copy(config, Path(path)))

    return _restore


# One editor reading or writing another file than its mission file: a manual
# XML file's Form tab runs the kind's own editor on that file (plans/92).
# `(editor id, path)` while `editor_file_override` runs, else None. Keyed by
# editor id, so a save that reads a second editor's file (the Events save
# renames positions in cfgeventspawns.xml) still gets the mission's.
_FILE_OVERRIDE: ContextVar[tuple[str, Path] | None] = ContextVar("editor_file_override", default=None)


@contextmanager
def editor_file_override(editor_id: str, path: Path) -> Iterator[None]:
    """Point *editor_id*'s path getter at *path* for the `with` block."""
    token = _FILE_OVERRIDE.set((editor_id, path))
    try:
        yield
    finally:
        _FILE_OVERRIDE.reset(token)


def overridden_file(editor_id: str | None = None) -> Path | None:
    """The file an override points *editor_id* at (any editor's, for None)."""
    current = _FILE_OVERRIDE.get()
    if current is None or (editor_id is not None and current[0] != editor_id):
        return None
    return current[1]


def overridable(editor_id: str):
    """A path getter that `editor_file_override` can point at another file."""

    def decorate(getter: Callable[[DayzConfig], Path | None]):
        @functools.wraps(getter)
        def wrapped(config: DayzConfig) -> Path | None:
            return overridden_file(editor_id) or getter(config)

        return wrapped

    return decorate
