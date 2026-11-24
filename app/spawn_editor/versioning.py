"""One version snapshot per file per map-editor session (plans/54 C, F74).

The editor saves on every drag and has no undo, so an accidental drag of a
zone could only be put back by eye: none of the JSON layers took a version
snapshot at all. They now take one, of the content from before the first
write of a session, and none after it. Not one per drag: ``config_versions``
keeps 50 versions per file, and a drag writes on every ``dragend``, so
per-drag snapshots would push the pre-session copy out within one sitting.

A session starts when someone opens the editor (``reload_all``) and when the
state is built (first use, a map switch, a manual reload). The set lives at
module level because the editor state is one per web process.

The three XML layers (event spawns, events, player spawns) call
:func:`snapshot_once` themselves and pass ``snapshot=False`` to ``save_xml``,
which otherwise snapshots every write; the other XML editors keep that.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.shared import config_versions
from app.shared.json_patch import write_json_patched
from app.shared.json_utils import atomic_write_text

log = logging.getLogger(__name__)

TRIGGER = "map_editor"

_snapshotted: set[Path] = set()


def new_session() -> None:
    """Forget which files this session has snapshotted."""
    _snapshotted.clear()


def snapshot_once(path: Path) -> None:
    """Snapshot *path* the first time this session writes it.

    A file that does not exist yet (the first add creates it) has nothing to
    keep, and counts as done: a later write must not snapshot the content
    this session made. Never blocks a save.
    """
    if path in _snapshotted:
        return
    _snapshotted.add(path)
    try:
        config_versions.snapshot(path, trigger=TRIGGER)
    except Exception:
        log.warning("Map editor could not snapshot %s", path, exc_info=True)


def write_text(path: Path, text: str) -> None:
    """Atomic write, after this session's one snapshot of *path*."""
    snapshot_once(path)
    atomic_write_text(path, text)


def write_json(path: Path, data: object, *, inline_scalar_lists: bool = False) -> None:
    """*data* written over *path* after this session's one snapshot.

    Only the values that changed are replaced in the file's text
    (``app.shared.json_patch``): Bohemia's hand-laid-out files and the mods'
    own Enforce output keep their layout, line endings and number text, and
    an edit diffs as the lines it changed (plans/54 D, F75).
    ``inline_scalar_lists`` lays out a list the save adds the way Bohemia
    writes ``"Pos": [ x, y, z ]``."""
    snapshot_once(path)
    write_json_patched(path, data, inline_scalar_lists=inline_scalar_lists)
