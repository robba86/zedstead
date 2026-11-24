"""Text-kind (raw, validated) adapters for config files whose structure is
too tree-shaped or list-of-objects-heavy to fit a flat form/table cleanly.

Every save snapshots first, then validates (defusedxml for XML, json for
JSON) before writing — the file is still fully editable, just not rendered
field-by-field.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from app.shared.config import DayzConfig
from app.web.editors import EditorSpec, register
from app.web.editors._paths import mission_dir, restore_from
from app.web.editors._raw import load_raw, save_raw


def _payload_content(payload) -> str:
    return payload if isinstance(payload, str) else payload.get("content", "")


def _make_mission_text_adapter(
    editor_id: str,
    title: str,
    path_getter: Callable[[Path], Path | None],
    *,
    language: str,
    filename: str = "",
    restorable: bool = False,
):
    """`restorable` is opt-in: only the files zsm backs up during a merge have
    a `.backup` to restore from. See `_paths.restore_from`.

    `filename` names the file in the save error. `title` is the editor's
    human name ("Event Groups"), so it can no longer double as that string —
    "not found for the current mission" has to say *which* file is missing."""
    def _path(config: DayzConfig) -> Path | None:
        m_dir = mission_dir(config)
        return path_getter(m_dir) if m_dir else None

    def _exists(config: DayzConfig) -> bool:
        return _path(config) is not None

    def _load(config: DayzConfig) -> dict:
        path = _path(config)
        content = load_raw(path) if path else ""
        return {"kind": "text", "content": content, "language": language}

    def _save(config: DayzConfig, payload) -> None:
        path = _path(config)
        if path is None:
            raise RuntimeError(f"{filename or title} not found for the current mission")
        save_raw(path, language, _payload_content(payload))

    from app.web.editors._raw import raw_targets_from

    register(
        EditorSpec(
            id=editor_id,
            title=title,
            group="Mission",
            kind="text",
            exists=_exists,
            load=_load,
            save=_save,
            restore=restore_from(_path) if restorable else None,
            raw_targets=raw_targets_from((_path, language)),
        )
    )


# economycore/environment/randompresets/spawnabletypes/effectarea graduated
# to structured table adapters in mission_tables.py (Stage 11). The
# _make_mission_text_adapter helper stays for future files that don't fit a
# form/table yet.


def _mission_file(filename: str):
    """Path getter for a mission-root file, None when it isn't there."""

    def _get(m_dir: Path) -> Path | None:
        path = m_dir / filename
        return path if path.is_file() else None

    return _get


def _mission_file_ci(filename: str):
    """`_mission_file`, but matching the name case-insensitively.

    Bohemia is not consistent between terrains: the same file is
    `cfgEffectArea.json` on Chernarus and `cfgeffectarea.json` on Sakhal, and
    `cfgIgnoreList.xml` differs from `cfgignorelist.xml` the same way. All
    three stock missions spell the underground triggers file in lower case
    today, which is exactly the sort of thing a patch changes. Same approach as
    `limitsdefinition_xml.get_limitsdefinition_path`, plus a guard for a
    mission folder that isn't there — `iterdir()` raises where `is_file()`
    would just answer False.
    """
    wanted = filename.lower()

    def _get(m_dir: Path) -> Path | None:
        if not m_dir.is_dir():
            return None
        for path in m_dir.iterdir():
            if path.is_file() and path.name.lower() == wanted:
                return path
        return None

    return _get


# cfglimitsdefinitionuser.xml graduated to a table adapter in mission_tables.py
# in 2026 Q3 — the third text-kind adapter to do so, after cfgeventspawns.xml
# and cfgeventgroups.xml (2026 Q3). The grounds it stayed text on were that
# its <user> entries "nest a list of usage/value refs, which no flat renderer
# shows usefully"; that was true of one row per ref and false of one row per
# group, which is the shape it got. The Raw tab is unchanged, so nothing that
# could be edited before cannot be edited now.

# Player spawn points graduated to a table with a tree in 2026 Q4 (plans/76
# step 3): `editors/playerspawnpoints.py`, one row per section, the params as
# columns and the groups and positions as each row's tree.


# Underground triggers (TODO H4) graduated to a form with a tree on
# 2026 Q4 (plans/76 step 2b): `editors/underground.py`. It still finds its
# file through `_mission_file_ci` above.
