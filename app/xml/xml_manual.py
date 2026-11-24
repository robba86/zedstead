"""Manual XML files — user-authored mission XML with a mod's lifecycle.

Since 2026 Q3 a merge leaves the mission files alone apart from the mod
blocks it owns, so hand-written content in cfgeventspawns.xml & co is no longer
something you need one of these to *keep*. Two jobs are left that only a manual
file can do, and they are why it exists:

* it lives outside the mission (``<config.paths.custom_xml_dir>/<id>.xml``), so it also
  survives a map switch, a server reinstall and a bundle import — all of which
  replace the whole mission folder;
* it is applied **after** every mod, so it wins where DayZ takes the last
  definition of a classname.

A third case is not optional: ``mpmissions/<mission>/custom/`` is wiped and
rebuilt from the mods on every merge (`xml_economy._clear_custom_folder`), so a
CE fragment of your own can only live there by being re-copied from here.

Every merge re-applies each file to the mission:

* CE kinds (``xml_classify.CE_TYPES``) are copied to ``custom/user-<id>.xml``
  and registered in cfgeconomycore.xml, exactly like a mod's CE fragment.
* Direct kinds (cfgeventspawns, cfgeventgroups, …) are spliced into the mission
  file inside ``<!-- START MOD: @user:<id> -->`` markers.

The ``@user:`` marker prefix is the whole trick: `xml_merge.remove_mod_xml_blocks`,
`insert_data_from_mods` and `load_xml(preserve_comments=True)` all treat a mod
name as opaque marker text, so one of these is strippable and idempotently
re-mergeable with no new machinery. A real mod dir can never collide with it —
mod dirs are ``@Name`` path components and cannot contain ``:``.

Named "links" until 2026 Q3 — the storage, the API and this module's
vocabulary were renamed with the UI. The ``@user:`` marker and the
``custom/user-<id>.xml`` filenames were NOT: they are written into mission
files, and renaming them would orphan every block an older zsm merged.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Iterator

from app.shared.json_utils import atomic_write_text, copy_file
from app.xml.xml_classify import (
    CE_KINDS,
    TERRAIN_FILENAME_TOKENS,
    ce_root_tag,
    file_matches_terrain,
    terrain_from_mission,
)
from app.xml.xml_configs import ALL_MERGE_CONFIGS, MODE_SECTIONS
from app.xml.xml_merge import (
    _collect_children,
    ensure_backup,
    mission_target_path,
    remove_mod_xml_blocks,
    splice_mod_files,
    strip_mod_blocks_text,
)
from app.xml.xml_text import XML_DECLARATION, check_xml_text, format_xml_text, parse_xml_text
from app.xml.xml_utils import load_xml, validate_xml_file

LogFn = Callable[[str], None]

# Mod-name prefix used for a link's START/END MOD markers and its custom/ files.
# A hand-kept copy lives in webui/src/crossLinks.js (MANUAL_PREFIX).
MANUAL_MOD_PREFIX = "@user:"
CUSTOM_FILE_PREFIX = "user-"

# A free-form merge into a mission file the merge configs don't cover. The
# five ALL_MERGE_CONFIGS kinds know their target, their child tags and their
# section mode; "merge" knows none of that, so the user names the target file
# and every element under the fragment's root is spliced into the mission
# root. Everything downstream (markers, strip, re-merge) is the same.
MERGE_KIND = "merge"

DIRECT_KINDS = frozenset(ALL_MERGE_CONFIGS) | {MERGE_KIND}
MANUAL_KINDS = frozenset(CE_KINDS) | DIRECT_KINDS

# No "--": the id goes into a START/END MOD comment, where "--" is invalid XML.
_ID_RE = re.compile(r"^(?!.*--)[a-z0-9][a-z0-9_-]{0,63}$")


class ManualFileError(ValueError):
    """A file is malformed — bad id, unknown kind, or content that isn't XML."""


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


# Every function that reads or writes the store takes ``manual_dir``, the
# instance's ``config.paths.custom_xml_dir`` (plans/44 step 3b). Required and
# keyword-only: a default would be the one config dir every instance shares, and
# most of these also take a server dir, which is a Path too.


def _index_path(manual_dir: Path) -> Path:
    return manual_dir / "files.json"


# Renamed from links.json (and its `links` payload key) in 2026 Q3, when the
# feature stopped calling itself "manual XML links". Read-side only: an existing
# install keeps working without a migration step, and the first save writes the
# new name. The old file is left alone rather than deleted — downgrading to an
# older zsm then still finds its index.
_LEGACY_INDEX_NAME = "links.json"


def file_content_path(file_id: str, *, manual_dir: Path) -> Path:
    """Path of a manual file's XML. Raises if the id isn't a safe slug.

    The id reaches here from a URL path segment, so it is validated before it is
    ever joined onto a directory (same discipline as `presets.py`).
    """
    if not is_safe_file_id(file_id):
        raise ManualFileError(f"Invalid manual XML file id: {file_id!r}")
    return manual_dir / f"{file_id}.xml"


def is_safe_file_id(file_id: str) -> bool:
    """True for a bare lowercase slug — no separators, no traversal."""
    return bool(file_id) and bool(_ID_RE.match(file_id)) and Path(file_id).name == file_id


def marker_name(file_id: str) -> str:
    """The pseudo mod name a manual file's merged blocks are tagged with."""
    return f"{MANUAL_MOD_PREFIX}{file_id}"


def custom_file_name(file_id: str) -> str:
    return f"{CUSTOM_FILE_PREFIX}{file_id}.xml"


def _read_index(manual_dir: Path) -> tuple[list[dict], str]:
    """The stored rows, and why the index could not be read ("" when it could,
    or when there is none yet)."""
    path = _index_path(manual_dir)
    if not path.is_file():
        path = manual_dir / _LEGACY_INDEX_NAME
        if not path.is_file():
            return [], ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], f"{path} could not be read ({exc})"
    rows = (data.get("files") or data.get("links") or []) if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return [], f"{path} does not hold a list of files"
    return [dict(row) for row in rows if isinstance(row, dict)], ""


def load_files(*, manual_dir: Path) -> list[dict]:
    """Every stored file, oldest first. Missing/broken index reads as empty."""
    return _read_index(manual_dir)[0]


def _files_to_add_to(manual_dir: Path) -> list[dict]:
    """`load_files` for the one writer that can add to an empty list. An index
    that is there and cannot be read raises: read as empty, the save would
    replace it and every record in it would be gone."""
    rows, problem = _read_index(manual_dir)
    if problem:
        raise ManualFileError(
            f"{problem}, so nothing is added: saving would replace it. "
            "Correct that file, or move it away to start a new list."
        )
    return rows


def save_files(rows: list[dict], *, manual_dir: Path) -> None:
    manual_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(_index_path(manual_dir), json.dumps({"files": rows}, indent=2))


def get_file(file_id: str, *, manual_dir: Path) -> dict | None:
    rows = load_files(manual_dir=manual_dir)
    return next((row for row in rows if row.get("id") == file_id), None)


def known_terrain_tokens() -> set[str]:
    """Every accepted `terrain` value: the terrain names and the map spellings
    mods use for them (`enoch` and `livonia` both mean the same map)."""
    return set(TERRAIN_FILENAME_TOKENS) | {
        token for tokens in TERRAIN_FILENAME_TOKENS.values() for token in tokens
    }


def _validate(file_id: str, kind: str, content: str, terrain: str = "", target: str = "") -> None:
    if not is_safe_file_id(file_id):
        raise ManualFileError(
            "Id must be lowercase letters, digits, '-' or '_' (e.g. livonia-heli-spawns)."
        )
    if kind not in MANUAL_KINDS:
        raise ManualFileError(f"Unknown kind: {kind!r}")
    # An unknown terrain would silently mean "every map": `file_matches_terrain`
    # only skips a file whose name carries a *recognised* foreign map token.
    if terrain and terrain.lower() not in known_terrain_tokens():
        raise ManualFileError(f"Unknown terrain: {terrain!r}")
    if kind == MERGE_KIND and not is_safe_target(target):
        raise ManualFileError(
            "A merge needs a target mission XML file (e.g. cfgeconomycore.xml or db/globals.xml)."
        )
    # `fragment=True`: a bare fragment (several sibling elements, no single
    # root) is legal input for a mod XML and equally legal here — the CE copy
    # wraps it.
    problem = check_xml_text(content, fragment=True)
    if problem is not None:
        raise ManualFileError(f"Not valid XML: {problem.summary}")


def root_tag_for(kind: str) -> str:
    """The root element a link of *kind* is wrapped in when it needs one."""
    if kind in CE_KINDS:
        # The CE `type=` is not always the root tag — globals' root is
        # <variables>. `xml_classify.CE_ROOT_TAGS` is the single map.
        return ce_root_tag(kind)
    if kind == MERGE_KIND:
        return "fragment"  # only used to wrap a rootless fragment for parsing
    return ALL_MERGE_CONFIGS[kind]["MOD_XML_ROOT_TAG"]


def is_safe_target(target: str) -> bool:
    """True for a mission-relative XML path this feature may write into.

    Deliberately narrow: the mission root or its ``db/`` folder, one level, no
    traversal. `remove_mod_xml_blocks` scans exactly those two directories, so
    a target outside them could be merged but never cleanly removed.
    """
    if not target or not target.lower().endswith(".xml") or "\\" in target:
        return False
    parts = PurePosixPath(target).parts
    if ".." in parts or any(p in ("", ".") for p in parts):
        return False
    return len(parts) == 1 or (len(parts) == 2 and parts[0] == "db")


def target_for_kind(kind: str) -> str:
    """The mission-relative file a link of *kind* lands in ("" for a free-form
    merge, which names its own target)."""
    if kind in CE_KINDS:
        return "cfgeconomycore.xml"
    cfg = ALL_MERGE_CONFIGS.get(kind)
    if not cfg:
        return ""
    rel = cfg["MISSION_FOLDER_TEMPLATE"].split("{mission_folder}")[-1].strip("/")
    name = cfg["FILE_NAME"] + cfg["FILE_EXT"]
    return f"{rel}/{name}" if rel else name


def kind_for_target(target: str) -> str:
    """The merge kind that owns *target*, or MERGE_KIND when nothing does.

    The five `ALL_MERGE_CONFIGS` kinds know their file's child tags and section
    mode; a free-form merge knows neither. So the user only ever picks the
    *file* and the smarter merge is used whenever one exists for it — picking
    `cfgeventspawns.xml` must not append `<event>` blindly at the root when the
    merge config would have matched positions into it.
    """
    for kind in ALL_MERGE_CONFIGS:
        if target_for_kind(kind).lower() == target.strip().lower():
            return kind
    return MERGE_KIND


def normalize_kind_target(kind: str, target: str) -> tuple[str, str]:
    """Resolve the (kind, target) pair a caller asked for into a stored one.

    A merge whose target is one of the known files is upgraded to that kind and
    drops the target (the kind implies it); every other kind keeps its derived
    target out of the record so a stale value can never mis-target a merge.
    """
    if kind == MERGE_KIND and target:
        upgraded = kind_for_target(target)
        if upgraded != MERGE_KIND:
            return upgraded, ""
        return kind, target
    return kind, target if kind == MERGE_KIND else ""


def list_merge_targets(server_dir: Path, mission_name: str) -> list[str]:
    """Mission XML files a free-form merge may target, mission-relative."""
    mission_dir = server_dir / "mpmissions" / mission_name
    if not mission_dir.is_dir():
        return []
    found = [p.name for p in mission_dir.glob("*.xml")]
    found += [f"db/{p.name}" for p in (mission_dir / "db").glob("*.xml")]
    # .backup files are copies, never merge targets.
    return sorted(t for t in found if t.lower().endswith(".xml"))


def create_file(
    file_id: str,
    kind: str,
    content: str,
    *,
    manual_dir: Path,
    mod: str = "",
    terrain: str = "",
    note: str = "",
    target: str = "",
    forked_from: str = "",
    enabled: bool = True,
) -> dict:
    """Store a new link. Raises `ManualFileError` on a bad id/kind/content."""
    kind, target = normalize_kind_target(kind, target)
    _validate(file_id, kind, content, terrain, target)
    links = _files_to_add_to(manual_dir)
    if any(link.get("id") == file_id for link in links):
        raise ManualFileError(f"A link named {file_id!r} already exists.")

    link = {
        "id": file_id,
        "kind": kind,
        "mod": mod,
        "terrain": terrain,
        "enabled": enabled,
        "note": note,
        # Only meaningful for MERGE_KIND; the other kinds derive their target.
        "target": target,
        # The generated custom/ file this link replaced, when it came from a
        # fork. The pair is otherwise unjoinable: the original keeps its own
        # name in the exclusion list while the link generates user-<id>.xml.
        "forked_from": forked_from,
    }
    path = file_content_path(file_id, manual_dir=manual_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, content)
    save_files(links + [link], manual_dir=manual_dir)
    return link


def update_file(file_id: str, *, manual_dir: Path, content: str | None = None, **fields) -> dict:
    """Update a link's content and/or metadata. Raises if it doesn't exist."""
    links = load_files(manual_dir=manual_dir)
    link = next((link for link in links if link.get("id") == file_id), None)
    if link is None:
        raise ManualFileError(f"No such link: {file_id!r}")

    kind = fields.get("kind", link["kind"])
    terrain = fields.get("terrain", link.get("terrain", ""))
    target = fields.get("target", link.get("target", ""))
    # Same upgrade `create_file` does: retargeting a free-form merge at a file
    # one of the merge configs owns must switch it to that smarter merge, even
    # when the caller only sent `target`.
    kind, target = normalize_kind_target(kind, target)
    # Validate metadata even when only fields changed — an unknown kind, terrain
    # or target saved here would silently mis-target the next merge.
    _validate(file_id, kind, content if content is not None else "<x/>", terrain, target)
    if content is not None:
        atomic_write_text(file_content_path(file_id, manual_dir=manual_dir), content)

    for key in ("mod", "terrain", "enabled", "note"):
        if key in fields:
            link[key] = fields[key]
    if "kind" in fields or "target" in fields:
        link["kind"], link["target"] = kind, target
    save_files(links, manual_dir=manual_dir)
    return link


def delete_file(
    file_id: str, *, manual_dir: Path, server_dir: Path | None = None, mission: str = "",
) -> bool:
    """Forget a link and, when a mission is given, undo what it applied.

    Removing the record alone would leave the merged block and the custom/ copy
    live until the next full merge, so the mission keeps serving content the
    user just deleted.
    """
    links = load_files(manual_dir=manual_dir)
    remaining = [link for link in links if link.get("id") != file_id]
    if len(remaining) == len(links):
        return False

    if server_dir and mission:
        remove_file_artifacts(server_dir, mission, file_id)
    path = file_content_path(file_id, manual_dir=manual_dir)
    if path.is_file():
        path.unlink()
    # The forked-from copy goes with it: nothing reads a baseline whose record
    # is gone, and a new file reusing the id would inherit a stale one.
    baseline_path(file_id, manual_dir=manual_dir).unlink(missing_ok=True)
    save_files(remaining, manual_dir=manual_dir)
    return True


# ---------------------------------------------------------------------------
# Files the user turned off
# ---------------------------------------------------------------------------
#
# The other half of "user decides what the mission loads": custom/ is generated
# and direct merges are re-spliced, so deleting either by hand is undone by the
# next merge. A record here is the durable form of that delete — the two merge
# halves drop the file before it is ever copied, registered or spliced.
#
# Three states, not two. "Disabled" and "deleted" suppress the file identically;
# they differ only in the UI, where a disabled file keeps its listing (greyed,
# with an Enable button) and a deleted one drops out of the grid behind a "show
# deleted" toggle. Keeping deleted files in the same record is what makes Delete
# reversible instead of a one-way door.

STATE_ON = ""
STATE_EXCLUDED = "excluded"
STATE_DELETED = "deleted"
_STATES = (STATE_EXCLUDED, STATE_DELETED)


def _read_states(path: Path, normalize) -> tuple[dict[str, str], str]:
    """A two-bucket state file, and why it could not be read ("" when it
    could, or when there is none). Tolerates the older ``excluded``-only shape."""
    if not path.is_file():
        return {}, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, f"{path} could not be read ({exc})"
    buckets = [data.get(state) or [] for state in _STATES] if isinstance(data, dict) else [None]
    if not all(isinstance(bucket, list) for bucket in buckets):
        return {}, f"{path} does not hold lists of file names"
    states: dict[str, str] = {}
    for state, bucket in zip(_STATES, buckets):
        for name in bucket:
            states[normalize(str(name))] = state
    return states, ""


def _load_states(path: Path, normalize) -> dict[str, str]:
    """`_read_states` for a reader: a list that cannot be read turns nothing off."""
    return _read_states(path, normalize)[0]


def _states_to_change(path: Path, normalize) -> dict[str, str]:
    """`_read_states` for a writer. A list that is there and cannot be read
    raises: read as empty, the save would replace it with one entry."""
    states, problem = _read_states(path, normalize)
    if problem:
        raise ManualFileError(
            f"{problem}, so nothing is changed: saving would replace it. "
            "Correct that file, or move it away to start a new list."
        )
    return states


def _save_states(path: Path, states: dict[str, str]) -> None:
    payload = {state: sorted(n for n, s in states.items() if s == state) for state in _STATES}
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(payload, indent=2))


def _set_state(states: dict[str, str], key: str, state: str) -> dict[str, str]:
    if state in _STATES:
        states[key] = state
    else:
        states.pop(key, None)
    return states


def _exclusions_path(manual_dir: Path) -> Path:
    return manual_dir / "excluded-custom.json"


def _valid_custom_name(custom_name: str) -> str:
    # "\\" explicitly: PurePosixPath doesn't treat it as a separator, so
    # `sub\dir.xml` would survive the name check here and only become a path
    # once the exclusion is applied on a Windows server.
    if (
        not custom_name
        or Path(custom_name).name != custom_name
        or "\\" in custom_name
        or "/" in custom_name
    ):
        raise ManualFileError(f"Invalid custom file name: {custom_name!r}")
    return custom_name


def load_custom_states(*, manual_dir: Path) -> dict[str, str]:
    """custom/ filename -> ``excluded`` | ``deleted``."""
    return _load_states(_exclusions_path(manual_dir), str)


def load_exclusions(*, manual_dir: Path) -> set[str]:
    """Every custom/ filename the merge must skip — disabled *and* deleted.

    The union on purpose: both states mean "don't generate this", and
    `find_and_copy_mods_xml` is the one place that has to honour it.
    """
    return set(load_custom_states(manual_dir=manual_dir))


def load_deleted(*, manual_dir: Path) -> set[str]:
    """custom/ filenames deleted outright (suppressed *and* unlisted)."""
    states = load_custom_states(manual_dir=manual_dir)
    return {name for name, state in states.items() if state == STATE_DELETED}


def set_custom_state(custom_name: str, state: str, *, manual_dir: Path) -> dict[str, str]:
    """Turn one generated file off (``excluded``/``deleted``) or back on (``""``)."""
    _valid_custom_name(custom_name)
    path = _exclusions_path(manual_dir)
    states = _set_state(_states_to_change(path, str), custom_name, state)
    _save_states(path, states)
    return states


def set_excluded(custom_name: str, excluded: bool, *, manual_dir: Path) -> set[str]:
    """Add/remove one generated file from the exclusion list. Returns the set."""
    set_custom_state(custom_name, STATE_EXCLUDED if excluded else STATE_ON, manual_dir=manual_dir)
    return load_exclusions(manual_dir=manual_dir)


def is_excluded(custom_name: str, *, manual_dir: Path) -> bool:
    return custom_name in load_exclusions(manual_dir=manual_dir)


# The same idea one step earlier in the chain. `excluded-custom.json` turns off a
# *generated* file, which only exists for CE kinds that were already registered;
# a mod XML that merges straight into a mission file (cfgeventspawns and friends)
# never becomes a custom/ file and so had no off switch at all. This list is
# keyed by the mod file itself — server-dir-relative, forward slashes — and is
# consulted by both merge halves, so it covers every row the Mod XML page shows.


def _source_exclusions_path(manual_dir: Path) -> Path:
    return manual_dir / "excluded-sources.json"


def normalize_source(source: str) -> str:
    """A mod XML's key in the exclusion list: ``@Mod/xml/events.xml``.

    Report rows are built with `Path.relative_to`, so the separator is the
    server's — normalized here so a list written on Windows still matches on
    Linux (a bind-mounted server dir is shared between the two).
    """
    return PurePosixPath(str(source).replace("\\", "/")).as_posix()


def load_source_states(*, manual_dir: Path) -> dict[str, str]:
    """Mod XML path -> ``excluded`` | ``deleted``, server-dir-relative."""
    return _load_states(_source_exclusions_path(manual_dir), normalize_source)


def load_source_exclusions(*, manual_dir: Path) -> set[str]:
    """Every mod XML the merge must skip — disabled *and* deleted."""
    return set(load_source_states(manual_dir=manual_dir))


def load_deleted_sources(*, manual_dir: Path) -> set[str]:
    """Mod XML files deleted outright (suppressed *and* unlisted)."""
    states = load_source_states(manual_dir=manual_dir)
    return {name for name, state in states.items() if state == STATE_DELETED}


def set_source_state(source: str, state: str, *, manual_dir: Path) -> dict[str, str]:
    """Turn one mod XML off (``excluded``/``deleted``) or back on (``""``)."""
    key = normalize_source(source)
    parts = PurePosixPath(key).parts
    if not key or len(parts) < 2 or ".." in parts or not parts[0].startswith("@"):
        raise ManualFileError(f"Invalid mod XML path: {source!r}")
    path = _source_exclusions_path(manual_dir)
    states = _set_state(_states_to_change(path, normalize_source), key, state)
    _save_states(path, states)
    return states


def set_source_excluded(source: str, excluded: bool, *, manual_dir: Path) -> set[str]:
    """Add/remove one mod XML from the source exclusion list. Returns the set."""
    set_source_state(source, STATE_EXCLUDED if excluded else STATE_ON, manual_dir=manual_dir)
    return load_source_exclusions(manual_dir=manual_dir)


def is_source_excluded(source: str, *, manual_dir: Path) -> bool:
    return normalize_source(source) in load_source_exclusions(manual_dir=manual_dir)


def resolve_mod_value(value: str, mod_ids: dict[str, str]) -> str:
    """Which installed mod a stored ``mod`` value names, or ``""`` for none.

    Accepts three spellings, because a user pre-configuring XML for a mod that
    isn't installed yet has only one of them to hand and shouldn't have to guess
    which one zsm wants:

    * the mod dir name — ``@CF``, case-insensitive, the ``@`` optional;
    * its workshop id — ``1559212036``.

    `mod_ids` is ``{mod dir name: workshop id}`` for the installed mods; a value
    that matches nothing in it belongs to a mod this box doesn't have, which is
    a legitimate state (the file waits, flagged, until the mod arrives).
    """
    value = (value or "").strip()
    if not value:
        return ""
    if value.isdigit():
        return next((name for name, wid in mod_ids.items() if wid == value), "")
    wanted = value.lstrip("@").lower()
    return next((name for name in mod_ids if name.lstrip("@").lower() == wanted), "")


def clear_mod_file_states(server_dir: Path, mod_name: str, *, manual_dir: Path) -> int:
    """Turn every one of a mod's XML files back on. Returns how many changed.

    The bulk undo for one mod's per-file Disable/Delete records. It is a
    *separate* action from re-configuring the mod's XML on purpose: a merge that
    dropped these records would undo the user's choices every time a mod is
    installed, toggled or re-configured, which is the whole thing the records
    exist to survive.

    Both lists are cleared — the generated `custom/` names this mod owns (matched
    by `owning_mod`, so `@Trader` doesn't claim `@TraderPlus`'s files) and its own
    source paths.
    """
    custom = load_custom_states(manual_dir=manual_dir)
    sources = load_source_states(manual_dir=manual_dir)
    kept_custom = {
        name: state for name, state in custom.items() if owning_mod(server_dir, name) != mod_name
    }
    prefix = mod_name + "/"
    kept_sources = {s: state for s, state in sources.items() if not s.startswith(prefix)}

    changed = (len(custom) - len(kept_custom)) + (len(sources) - len(kept_sources))
    if len(kept_custom) != len(custom):
        _save_states(_exclusions_path(manual_dir), kept_custom)
    if len(kept_sources) != len(sources):
        _save_states(_source_exclusions_path(manual_dir), kept_sources)
    return changed


def fork_mod_source(server_dir: Path, source: str, *, manual_dir: Path, file_id: str = "") -> dict:
    """Turn one mod's own XML file into an editable manual link.

    The mod's copy is SteamCMD's — the next workshop update overwrites it, and
    every merge re-applies it from there. Forking copies the content into a link
    (the file of record outside the mission) and excludes the original in the
    same step, so the content isn't contributed twice.

    **Created enabled** (2026 Q3), like `fork_custom_file`. It started out
    disabled, from when forking was the first half of editing the mod's file in
    place. But the fork excludes the mod's original in the same step, so a
    disabled copy takes that content *out* of the mission until the admin finds
    a second switch — the same button on a tile and on a table row meant
    opposite things.
    """
    from app.modmgr.mods import kebab_case
    from app.xml.xml_classify import classify_mod_xml

    key = normalize_source(source)
    path = server_dir / Path(*PurePosixPath(key).parts)
    if not path.is_file():
        raise ManualFileError(f"No mod XML at {source!r}.")

    kind = classify_mod_xml(path)
    if kind not in MANUAL_KINDS:
        raise ManualFileError(
            f"{path.name} isn't a kind Zedstead knows how to link — nothing to customize."
        )

    # Before a file is created: the copy and the original both live is the
    # worse half of a fork that stopped in the middle.
    _states_to_change(_source_exclusions_path(manual_dir), normalize_source)

    mod = PurePosixPath(key).parts[0]
    slug = _slugify(f"{kebab_case(mod)}-{path.stem}")
    ids = [file_id] if file_id else [_fork_id(slug), slug]
    content = path.read_text(encoding="utf-8")
    for candidate in dict.fromkeys(ids):
        try:
            link = create_file(
                candidate,
                kind,
                content,
                manual_dir=manual_dir,
                mod=mod,
                note=f"Customized copy of {key}",
                forked_from=key,
            )
        except ManualFileError:
            if candidate == ids[-1]:
                raise
            continue
        set_source_excluded(key, True, manual_dir=manual_dir)
        # After the id is settled: the retry above means an earlier candidate
        # may belong to an unrelated file, whose baseline this would clobber.
        return set_baseline(candidate, key, path, manual_dir=manual_dir)
    raise ManualFileError(f"No usable link name for {source!r}.")


def owning_mod(server_dir: Path, custom_name: str) -> str | None:
    """Which installed mod generated ``custom/<custom_name>``.

    Matched on the ``kebab_case(mod)-`` prefix the copy step writes, longest
    first — ``@Trader`` and ``@TraderPlus`` both prefix-match a TraderPlus file,
    and the shorter one would be the wrong answer.
    """
    from app.modmgr.mods import kebab_case

    best: tuple[int, str] | None = None
    for mod_dir in server_dir.iterdir():
        if not (mod_dir.is_dir() and mod_dir.name.startswith("@")):
            continue
        prefix = kebab_case(mod_dir.name) + "-"
        if custom_name.startswith(prefix) and (best is None or len(prefix) > best[0]):
            best = (len(prefix), mod_dir.name)
    return best[1] if best else None


def mod_file_for_custom(server_dir: Path, custom_name: str, mod: str) -> Path | None:
    """The mod's own XML that generated ``custom/<custom_name>``, or None.

    `custom_name` is `kebab_case(mod)-<the mod file's own name>`, so the mod is
    already known (`owning_mod`) and only the filename has to be found again.
    Matched by name while walking rather than through `rglob(filename)`: a mod
    filename may contain `[` or `*`, which `rglob` would read as a pattern.

    Deliberately not `build_mod_xml_report()` — that parses every XML of every
    installed mod, which is far too much work to answer "where did this one file
    come from".
    """
    from app.modmgr.mods import kebab_case

    prefix = kebab_case(mod) + "-" if mod else ""
    if not prefix or not custom_name.startswith(prefix):
        return None
    filename = custom_name[len(prefix):]
    mod_dir = server_dir / mod
    if not filename or not mod_dir.is_dir():
        return None
    from app.xml.xml_economy import custom_file_name

    return next(
        (p for p in mod_dir.rglob("*.xml") if custom_file_name(mod, p.name) == custom_name and p.is_file()),
        None,
    )


def fork_custom_file(
    server_dir: Path,
    mission_name: str,
    custom_name: str,
    *,
    manual_dir: Path,
    file_id: str = "",
    source_path: Path | None = None,
) -> dict:
    """Turn a generated custom/ file into a manual XML file the admin owns.

    The generated original is excluded in the same step: leaving both live would
    register two CE files declaring the same classnames, which DayZ resolves as
    last-one-wins plus a log full of duplicate warnings.

    **Created enabled** (2026 Q3). It started out disabled, when forking was
    the first half of "I want to change this" and the generated file could still
    be edited in place. Generated files are read-only now, so this is the whole
    act of taking a file over — and leaving it disabled would silently drop that
    file's content out of the mission until the admin noticed a second switch.
    The caller applies it (`apply_manual_file`), so the swap is invisible to the
    running economy.

    `source_path` is the mod's own copy of the file, used when the generated one
    isn't there to read: a disabled mod's files are never copied into custom/,
    and an already-excluded file was deleted from it. Without the fallback,
    "Set as manual" would be dead exactly where it is most wanted.
    """
    path = server_dir / "mpmissions" / mission_name / "custom" / custom_name
    if Path(custom_name).name != custom_name:
        raise ManualFileError(f"No generated file named {custom_name!r}.")
    if not path.is_file():
        if not (source_path and source_path.is_file()):
            raise ManualFileError(f"No generated file named {custom_name!r}.")
        path = source_path

    from app.xml.xml_classify import classify_mod_xml

    kind = classify_mod_xml(path)
    if kind not in CE_KINDS:
        raise ManualFileError(
            f"{custom_name} isn't a central-economy file — nothing to fork."
        )

    # Slugify first: the copy step prefixes `kebab_case(mod)-` onto the mod's own
    # filename verbatim, so the repeat is only visible once both are slugs
    # (`traderplus-` + `TraderPlus_Types` -> `traderplus-traderplus-types`).
    # Checked before a file is created, as in `fork_mod_source`.
    _states_to_change(_exclusions_path(manual_dir), str)

    slug = _slugify(Path(custom_name).stem)
    ids = [file_id] if file_id else [_fork_id(slug), slug]
    content = path.read_text(encoding="utf-8")
    mod = owning_mod(server_dir, custom_name) or ""
    for candidate in dict.fromkeys(ids):  # dedupe, keep order
        try:
            link = create_file(
                candidate,
                kind,
                content,
                manual_dir=manual_dir,
                mod=mod,
                note=f"Customized copy of custom/{custom_name}",
                forked_from=custom_name,
            )
        except ManualFileError:
            # The shortened id may already be taken by an unrelated link; fall
            # back to the full stem before giving up.
            if candidate == ids[-1]:
                raise
            continue
        set_excluded(custom_name, True, manual_dir=manual_dir)
        # The baseline is the MOD's file, not the custom/ copy: the drift being
        # tracked is what the mod ships, and custom/ is regenerated from it.
        return _baseline_from_mod_file(
            candidate, server_dir, source_path or mod_file_for_custom(server_dir, custom_name, mod),
            manual_dir=manual_dir,
        )
    raise ManualFileError(f"No usable link name for {custom_name!r}.")


def _baseline_from_mod_file(
    file_id: str, server_dir: Path, mod_file: Path | None, *, manual_dir: Path,
) -> dict:
    """Baseline *file_id* against *mod_file*, if it can be named relative to the
    server dir. A fork whose origin can't be located keeps working — it just
    reports `unknown` instead of tracking drift."""
    link = get_file(file_id, manual_dir=manual_dir)
    if mod_file is None or not mod_file.is_file():
        return link
    try:
        key = normalize_source(mod_file.resolve().relative_to(server_dir.resolve()))
    except ValueError:
        return link
    return set_baseline(file_id, key, mod_file, manual_dir=manual_dir)


# ---------------------------------------------------------------------------
# Upstream baseline — what the mod's file looked like when it was taken over
# ---------------------------------------------------------------------------
#
# Taking a mod's file over has one real cost: that file stops tracking the mod.
# The mod keeps updating, its XML keeps changing, and none of it reaches the
# mission any more — silently, because nothing else in the app looks at a file
# it no longer applies. So a fork records what it copied (a byte copy beside the
# manual file, plus its hash and the time), and the drift is reported.
#
# The copy is kept as well as the hash because a hash can only answer "changed?"
# — the useful question is *what* changed, and that needs both sides.

UPSTREAM_CURRENT = "current"    # the mod's file still matches the baseline
UPSTREAM_CHANGED = "changed"    # the mod shipped something new
UPSTREAM_MISSING = "missing"    # the mod (or the file) is gone
UPSTREAM_UNKNOWN = "unknown"    # no baseline — forked before this existed


def baseline_path(file_id: str, *, manual_dir: Path) -> Path:
    """Where the forked-from copy of the mod's file lives."""
    if not is_safe_file_id(file_id):
        raise ManualFileError(f"Invalid manual XML file id: {file_id!r}")
    return manual_dir / f"{file_id}.source.xml"


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def set_baseline(file_id: str, source: str, source_file: Path, *, manual_dir: Path) -> dict:
    """Record *source_file* as what this manual file was taken from.

    Copies the mod's file beside ours and stores `source` / `source_sha256` /
    `source_captured_at` on the record. Called **after** the record exists, not
    as part of creating it: a fork retries with a second id when the first is
    taken, and writing the copy before knowing which id won would overwrite an
    unrelated file's baseline.
    """
    rows = load_files(manual_dir=manual_dir)
    row = next((r for r in rows if r.get("id") == file_id), None)
    if row is None:
        raise ManualFileError(f"No such file: {file_id!r}")

    dest = baseline_path(file_id, manual_dir=manual_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_file, dest)
    row.update(
        source=normalize_source(source),
        source_sha256=_sha256(source_file),
        source_captured_at=_now_iso(),
    )
    save_files(rows, manual_dir=manual_dir)
    return row


def clear_baseline(file_id: str, *, manual_dir: Path) -> None:
    """Forget what this manual file was taken from: the inverse of `set_baseline`.

    For content that no longer comes from the mod file named in ``source``,
    such as TraderPlus_types.xml downloaded from GitHub after the mod stopped
    shipping it. Without this the Mod XML page reports the old source as
    missing and offers to port a change from a file that is gone.
    """
    rows = load_files(manual_dir=manual_dir)
    row = next((r for r in rows if r.get("id") == file_id), None)
    if row is None:
        raise ManualFileError(f"No such file: {file_id!r}")
    for key in ("source", "source_sha256", "source_captured_at"):
        row.pop(key, None)
    save_files(rows, manual_dir=manual_dir)
    baseline_path(file_id, manual_dir=manual_dir).unlink(missing_ok=True)


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def upstream_status(link: dict, server_dir: Path) -> str:
    """Has the mod changed this file since it was taken over?"""
    from app.xml.xml_report import resolve_mod_source

    source = link.get("source") or ""
    if not source or not link.get("source_sha256"):
        return UPSTREAM_UNKNOWN
    path = resolve_mod_source(server_dir, source)
    if path is None:
        return UPSTREAM_MISSING
    return UPSTREAM_CURRENT if _sha256(path) == link["source_sha256"] else UPSTREAM_CHANGED


def upstream_info(link: dict, server_dir: Path, *, manual_dir: Path, diff: bool = False) -> dict:
    """`{status, source, captured_at, diff}` for one manual file.

    `diff` is the mod's own before/after, never a comparison with the admin's
    edits: the question this answers is "what did the mod change, so I can
    decide whether to port it". Both sides are normalized the way
    `config_versions.try_normalize_xml` normalizes a version diff, so a mod that
    only reformatted its file doesn't read as a change.
    """
    from app.shared.config_versions import try_normalize_xml
    from app.xml.xml_report import resolve_mod_source

    status = upstream_status(link, server_dir)
    out = {
        "status": status,
        "source": link.get("source") or "",
        "captured_at": link.get("source_captured_at") or "",
        "diff": "",
    }
    if not diff or status != UPSTREAM_CHANGED:
        return out

    baseline = baseline_path(link["id"], manual_dir=manual_dir)
    path = resolve_mod_source(server_dir, out["source"])
    if path is None or not baseline.is_file():
        return out

    before, _ = try_normalize_xml(baseline.read_text(encoding="utf-8-sig"))
    after, _ = try_normalize_xml(path.read_text(encoding="utf-8-sig"))
    out["diff"] = "".join(
        unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"{out['source']} (when you took it over)",
            tofile=f"{out['source']} (now)",
        )
    )
    return out


def ack_upstream(file_id: str, server_dir: Path, *, manual_dir: Path) -> dict:
    """"I've seen it, I'm keeping mine" — re-baseline, touch nothing else.

    The admin's file is deliberately not read: acknowledging is a statement
    about the *mod's* version, and re-capturing is what stops the same change
    being reported forever.
    """
    return _rebaseline(file_id, server_dir, manual_dir=manual_dir)


def adopt_upstream(file_id: str, server_dir: Path, *, manual_dir: Path) -> dict:
    """Replace the admin's file with the mod's current version, and re-baseline.

    Destructive — it discards whatever the admin changed — so the manual file is
    snapshotted first and shows up in Analysis → Change history like every other
    overwrite in the app.
    """
    from app.shared.config_versions import snapshot
    from app.xml.xml_report import resolve_mod_source

    link = get_file(file_id, manual_dir=manual_dir)
    if link is None:
        raise ManualFileError(f"No such file: {file_id!r}")
    path = resolve_mod_source(server_dir, link.get("source") or "")
    if path is None:
        raise ManualFileError("The mod's file is no longer there to copy.")

    content = path.read_text(encoding="utf-8")
    own = file_content_path(file_id, manual_dir=manual_dir)
    if own.is_file():
        try:
            snapshot(own, trigger="upstream_adopt")
        except Exception:
            pass  # never block the write on versioning
    update_file(file_id, manual_dir=manual_dir, content=content)
    return _rebaseline(file_id, server_dir, manual_dir=manual_dir)


def _rebaseline(file_id: str, server_dir: Path, *, manual_dir: Path) -> dict:
    from app.xml.xml_report import resolve_mod_source

    link = get_file(file_id, manual_dir=manual_dir)
    if link is None:
        raise ManualFileError(f"No such file: {file_id!r}")
    source = link.get("source") or ""
    path = resolve_mod_source(server_dir, source)
    if path is None:
        raise ManualFileError("The mod's file is no longer there to compare against.")
    link = set_baseline(file_id, source, path, manual_dir=manual_dir)
    return upstream_info(link, server_dir, manual_dir=manual_dir)


# ---------------------------------------------------------------------------
# Moving a mission file's own content into a manual XML file
# ---------------------------------------------------------------------------


def _capture_groups(root: ET.Element, cfg) -> list[tuple[ET.Element, list[ET.Element]]]:
    """(container, elements the admin owns) pairs, at the depth this file merges.

    ``MOD_XML_CHILD_TAGS`` names the elements a merge *targets*, and the mode
    says how: in ``children`` mode they are appended to the root, so the
    admin's are the root's own unowned children of those tags; in ``sections``
    mode they are the *containers* (``<usageflags>`` & co) that the mod's
    entries are appended **into**, so the markers live one level down and the
    section elements themselves must stay put.

    Filtering by tag is not cosmetic: `insert_data_from_mods` only ever
    re-splices those tags, so capturing anything else would move it out of the
    mission into a file that never puts it back.
    """
    tags = {t.lower() for t in cfg["MOD_XML_CHILD_TAGS"]}

    def owned(parent: ET.Element, keep_tags: bool) -> list[ET.Element]:
        return [
            child
            for child in unowned_children(parent)
            if not keep_tags or child.tag.lower() in tags
        ]

    if cfg["MERGE_MODE"] == MODE_SECTIONS:
        return [
            (section, owned(section, keep_tags=False))
            for section in root
            if isinstance(section.tag, str) and section.tag.lower() in tags
        ]
    return [(root, owned(root, keep_tags=True))]


def capture_unowned(
    server_dir: Path,
    mission_name: str,
    target: str,
    *,
    manual_dir: Path,
    file_id: str = "",
    note: str = "",
    stock: Path | None = None,
) -> dict:
    """**Move** a mission file's own entries into a manual XML file.

    Since 2026 Q3 nothing about this is needed to *keep* an edit — a merge
    leaves the file alone. It buys the two things the mission file cannot:
    the content outlives a map switch or a server reinstall (the whole mission
    folder is replaced), and it is re-applied **after** every mod, so it wins
    where DayZ takes the last definition.

    A move, for the same reason `capture_event_positions` is one: a copy would
    leave the entries in the file *and* re-apply them from the manual file, so
    everything captured would be defined twice.

    Mod-owned entries are never touched — they are re-merged from the mod on
    every merge, so taking them would double them and leave the mod's block
    referring to content that had moved.

    **Bohemia's own entries are not the admin's either.** *stock* is the
    mission's stock copy of this file (``mission_stock.stock_copy``): an
    entry identical to one there stays. On a stock cfglimitsdefinition.xml
    the move took all 33 definitions as "your own", and deleting that manual
    file later emptied the mission file.
    """
    from app.xml.xml_utils import load_xml, one_version_per_file, save_xml

    kind = kind_for_target(target)
    if kind == MERGE_KIND:
        raise ManualFileError(
            f"{target} isn't one of the mission files Zedstead merges mod XML into, "
            "so there is nothing to separate. Add a merge for it instead."
        )
    path = mission_file_path(server_dir, mission_name, target)
    if path is None or not path.is_file():
        raise ManualFileError(f"This mission has no {target}.")

    cfg = ALL_MERGE_CONFIGS[kind]
    # preserve_comments is mandatory: the START/END MOD markers ARE the record
    # of what belongs to a mod, and without them everything reads as unowned.
    tree = load_xml(path, preserve_comments=True)
    root = tree.getroot()
    stock_entries = _stock_entries(stock)
    groups = [
        (parent, [k for k in kids if _canon(k) not in stock_entries])
        for parent, kids in _capture_groups(root, cfg)
    ]
    groups = [(parent, kids) for parent, kids in groups if kids]
    if not groups:
        raise ManualFileError(
            f"Nothing of your own in {target} — every entry in it comes from a mod, "
            "from a manual XML file you already have, or is the game's own."
        )

    captured = sum(len(kids) for _parent, kids in groups)
    content = _capture_document(root.tag, cfg, groups)

    file_id = file_id or _slugify(Path(target).stem)
    # Created before the mission file is touched, so a rejected id (already
    # taken, unsafe) leaves the mission exactly as it was.
    link = create_file(
        file_id,
        kind,
        content,
        manual_dir=manual_dir,
        terrain=terrain_from_mission(mission_name) or "",
        note=note or f"Moved {captured} entr{'y' if captured == 1 else 'ies'} out of {target}",
    )

    for parent, kids in groups:
        for child in kids:
            parent.remove(child)
    # One version, the file before the move: not the copy in between, whose
    # entries were gone and not back yet (a version of
    # cfglimitsdefinition.xml with its four lists empty).
    with one_version_per_file():
        save_xml(tree, path)
        apply_manual_file(server_dir, mission_name, file_id, manual_dir=manual_dir)
    return link


def _canon(el: ET.Element) -> str:
    """*el* serialized with its whitespace and comments dropped, to compare
    an entry with the stock file's regardless of indentation."""
    clone = copy.deepcopy(el)
    for node in clone.iter():
        if isinstance(node.tag, str):
            node.text = (node.text or "").strip() or None
        node.tail = None
    for parent in list(clone.iter()):
        for child in [c for c in parent if not isinstance(c.tag, str)]:
            parent.remove(child)
    return ET.tostring(clone, encoding="unicode")


def _stock_entries(stock: Path | None) -> set[str]:
    """Every element of the stock copy *stock*, as `_canon` writes it."""
    if stock is None or not stock.is_file():
        return set()
    from app.xml.xml_utils import load_xml

    try:
        root = load_xml(stock).getroot()
    except Exception:  # noqa: BLE001 - an unreadable stock copy protects nothing
        return set()
    return {_canon(el) for el in root.iter() if el is not root and isinstance(el.tag, str)}


def _capture_document(root_tag: str, cfg, groups: list[tuple[ET.Element, list]]) -> str:
    """Serialize the captured elements as a document of the target's own shape."""
    out = ET.Element(root_tag)
    if cfg["MERGE_MODE"] == MODE_SECTIONS:
        for parent, kids in groups:
            section = ET.SubElement(out, parent.tag)
            section.extend(kids)
    else:
        out.extend(groups[0][1])

    tree = ET.ElementTree(out)
    ET.indent(tree, space="\t")
    return XML_DECLARATION + "\n" + ET.tostring(out, encoding="unicode") + "\n"


def capture_event_positions(
    server_dir: Path,
    mission_name: str,
    event: str,
    *,
    manual_dir: Path,
    file_id: str = "",
    note: str = "",
) -> dict:
    """**Move** one event's own spawn positions into a manual XML file.

    The workflow the Visual Map Editor offers: positions dragged onto the map go
    straight into the mission's cfgeventspawns.xml, where they now survive a
    merge on their own. Converting them buys the two things the mission file
    cannot give — they outlive a map switch or a server reinstall, and they are
    re-applied *after* every mod, so they win where DayZ takes the last
    definition.

    A move, not a copy (2026 Q3). While a merge restored cfgeventspawns.xml
    from its `.backup`, leaving the originals behind was harmless — the next
    merge deleted them. It no longer does, so a copy would leave the same
    positions in the file twice, once as the admin's own content and once inside
    the `@user:<id>` block, and the event would spawn at each point twice as
    often. The captured elements are therefore removed from the mission file and
    the new file is applied in their place; the file's content is unchanged, it
    just belongs to a different owner.

    Only positions outside a mod's START/END block are taken — a mod's own
    positions are re-merged from the mod anyway, and taking them would double
    them on every spawn. That is checked at **both** levels, because a mod's
    positions can be owned in two different shapes: the merge appends the mod's
    whole `<event>` at the root inside markers, so an event that exists *only*
    because a mod merged it has no markers inside it at all and used to read as
    entirely hand-placed.
    """
    from app.xml.eventspawns_xml import get_eventspawns_path, parse_eventspawns_file
    from app.xml.xml_utils import one_version_per_file, save_xml

    mission_dir = server_dir / "mpmissions" / mission_name
    path = get_eventspawns_path(mission_dir)
    if path is None:
        raise ManualFileError("This mission has no cfgeventspawns.xml.")

    parsed = parse_eventspawns_file(path)
    # A name can appear more than once — the mission's own entry plus one per
    # mod that merged positions for it. Only the ones no mod owns are candidates.
    mine = set(unowned_children(parsed.tree.getroot()))
    entry = next((e for e in parsed.entries if e.name == event and e.element in mine), None)
    if entry is None:
        if any(e.name == event for e in parsed.entries):
            raise ManualFileError(
                f"Every position for {event} comes from a mod. They are re-applied "
                "from the mod on each merge, so there is nothing here to take over."
            )
        raise ManualFileError(f"No event named {event!r} in cfgeventspawns.xml.")

    owned = _unowned_positions(entry)
    if not owned:
        raise ManualFileError(
            f"{event} has no hand-placed positions — every position it has comes "
            f"from a mod or a manual link that is already saved."
        )

    # Copy the attribute text verbatim rather than re-formatting the parsed
    # floats: the mission's own coordinates stay byte-identical, so a captured
    # link diffs cleanly against what the map editor wrote.
    body = "".join(
        '\n\t\t<pos x="{x}" z="{z}" a="{a}"/>'.format(
            x=p.element.get("x", p.x), z=p.element.get("z", p.z), a=p.element.get("a", p.a),
        )
        for p in owned
    )
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<eventposdef>\n'
        f'\t<event name="{event}">{body}\n\t</event>\n</eventposdef>\n'
    )

    file_id = file_id or _slugify(event)
    # Created first: a duplicate id raises here, before the mission file has
    # been touched, so a rejected capture changes nothing.
    link = create_file(
        file_id,
        "cfgeventspawns",
        content,
        manual_dir=manual_dir,
        terrain=terrain_from_mission(mission_name) or "",
        note=note or f"Captured {len(owned)} position(s) for {event}",
    )

    for pos in owned:
        entry.element.remove(pos.element)
    # An <event> with nothing left in it (not even a mod's markers) would show up
    # beside the one the manual file merges back in, as a second, empty row with
    # the same name — and `_find_entry` in the map editor takes the first match.
    if len(entry.element) == 0:
        parsed.tree.getroot().remove(entry.element)
    # One version, the file before the move: not the copy in between, with the
    # positions gone and the block not back yet.
    with one_version_per_file():
        save_xml(parsed.tree, path)
        # Straight back into the mission, inside the file's own @user:<id>
        # block, so the move is invisible on the map: same positions, different
        # owner.
        apply_direct_files(server_dir, mission_name, manual_dir=manual_dir, only_file=file_id)
    return link


def file_holds_event(
    path: Path,
    event: str,
    expected: list[tuple[str | None, str | None, str | None]],
) -> bool:
    """True when the cfgeventspawns file at *path* holds an ``<event>`` named
    *event* whose positions are *expected* (``(x, z, a)`` attribute text).

    How the map editor checks that a mod file is the one an event in the
    mission came from, before it copies that file into a manual file."""
    from app.xml.eventspawns_xml import entry_from_element, position_attrs

    if not validate_xml_file(path):
        return False
    tree = load_xml(path, preserve_comments=True)
    children = _collect_children(
        tree.getroot(), ALL_MERGE_CONFIGS["cfgeventspawns"]["MOD_XML_CHILD_TAGS"],
    )
    return any(
        child.get("name", "") == event
        and position_attrs(entry_from_element(child)) == list(expected)
        for child in children
    )


def edit_merged_event(
    server_dir: Path,
    mission_name: str,
    file_id: str,
    block_index: int,
    event: str,
    expected: list[tuple[str | None, str | None, str | None]],
    edit: Callable,
    *,
    manual_dir: Path,
    snapshot_once: Callable[[Path], None] | None = None,
):
    """Change the positions of an event a manual file merges into
    cfgeventspawns.xml, **in the manual file**, then apply that file again.

    The inverse of `capture_event_positions`. A position inside a file's
    ``@user:<id>`` block is re-spliced from the file on every merge, so a move
    written only into the mission went back at the next re-configure. Writing
    the file and re-applying it keeps the two the same.

    *block_index* is the event's place among the elements of the file's block,
    which is the order `_collect_children` takes them out of the file.
    *expected* is each of the mission's positions as ``(x, z, a)`` attribute
    text: the file's must match it, or the file was changed since it was last
    applied and an index would point at the wrong position. *edit* gets the
    file's `EventSpawnEntry` and does the change; its return value is returned.

    *snapshot_once* is the map editor's one version per file per session; with
    it, neither file gets a version per drag.
    """
    from app.xml.eventspawns_xml import entry_from_element, get_eventspawns_path, position_attrs
    from app.xml.xml_utils import one_version_per_file, save_xml

    out_of_step = (
        f"The manual XML file {file_id!r} no longer matches the mission. "
        "Re-apply it on Configs → Custom config, then try again."
    )
    link = get_file(file_id, manual_dir=manual_dir)
    if link is None:
        raise ManualFileError(
            f"These positions come from the manual XML file {file_id!r}, which no "
            "longer exists. Re-apply all mod XML to take them out of the mission."
        )
    if link.get("kind") != "cfgeventspawns":
        raise ManualFileError(out_of_step)
    path = file_content_path(file_id, manual_dir=manual_dir)
    if not validate_xml_file(path):
        raise ManualFileError(
            f"The manual XML file {file_id!r} has no single root element, so the "
            "map editor cannot change it. Edit it on Configs → Custom config."
        )
    tree = load_xml(path, preserve_comments=True)
    children = _collect_children(
        tree.getroot(), ALL_MERGE_CONFIGS["cfgeventspawns"]["MOD_XML_CHILD_TAGS"],
    )
    target = children[block_index] if 0 <= block_index < len(children) else None
    if target is None or target.get("name", "") != event:
        raise ManualFileError(out_of_step)
    entry = entry_from_element(target)
    if position_attrs(entry) != list(expected):
        raise ManualFileError(out_of_step)

    result = edit(entry)
    if snapshot_once is not None:
        snapshot_once(path)
        save_xml(tree, path, snapshot=False)
    else:
        save_xml(tree, path)

    mission_path = get_eventspawns_path(server_dir / "mpmissions" / mission_name)
    kept: tuple[Path, ...] = ()
    if snapshot_once is not None and mission_path is not None:
        snapshot_once(mission_path)
        kept = (mission_path,)
    with one_version_per_file(already_kept=kept):
        failed = apply_direct_files(
            server_dir, mission_name, manual_dir=manual_dir, only_file=file_id,
        )
    if failed:
        raise ManualFileError(
            f"The manual XML file {file_id!r} was saved but could not be applied "
            "to the mission. Re-apply it on Configs → Custom config."
        )
    return result


def unowned_children(parent: ET.Element) -> Iterator[ET.Element]:
    """*parent*'s element children that no mod (or link) put there.

    Anything between a ``<!-- START MOD: … -->`` and its ``END MOD`` comment
    belongs to whoever merged it and is re-applied from the source on every
    merge — copying it into a link would double it on every spawn. Requires a
    tree parsed with ``preserve_comments=True``, or the markers aren't there to
    see and everything looks unowned.
    """
    for owner, child in children_with_owner(parent):
        if owner is None:
            yield child


def children_with_owner(parent: ET.Element) -> Iterator[tuple[str | None, ET.Element]]:
    """*parent*'s element children, each with the mod (or ``@user:<id>`` link)
    whose ``START MOD`` block holds it — ``None`` for the file's own content.

    The one START/END state machine; `unowned_children` is this filtered to
    ``None``. Same ``preserve_comments=True`` requirement.
    """
    owner: str | None = None
    for child in parent:
        if not isinstance(child.tag, str):  # comment / PI
            text = (child.text or "").strip()
            if text.startswith("START MOD:"):
                owner = text[len("START MOD:"):].strip() or "?"
            elif text.startswith("END MOD:"):
                owner = None
            continue
        yield owner, child


def owner_map(root: ET.Element) -> dict[ET.Element, str]:
    """Every element under *root* that sits in a ``START MOD`` block, mapped to
    that block's owner (a mod, or ``@user:<id>`` for a manual file).

    A block's owner is inherited down the tree: a ``<pos>`` inside an ``<event>``
    that a manual file merged belongs to that file too. Same
    ``preserve_comments=True`` requirement as `children_with_owner`. The table
    editors tag their rows with it, so a manual file's rows can be locked and
    filtered (plans/92).
    """
    out: dict[ET.Element, str] = {}

    def walk(parent: ET.Element, inherited: str | None) -> None:
        for owner, child in children_with_owner(parent):
            here = owner or inherited
            if here:
                out[child] = here
            walk(child, here)

    walk(root, None)
    return out


def manual_owner_id(owner: str | None) -> str | None:
    """The manual file id an owner names (``@user:<id>``), or None for a mod or
    for no owner. The inverse of `marker_name`."""
    if owner and owner.startswith(MANUAL_MOD_PREFIX):
        return owner[len(MANUAL_MOD_PREFIX):] or None
    return None


def _unowned_positions(entry) -> list:
    """An event's positions that aren't inside someone's START/END MOD block."""
    by_element = {p.element: p for p in entry.positions}
    return [by_element[c] for c in unowned_children(entry.element) if c in by_element]


# ---------------------------------------------------------------------------
# Sampling a real element out of a merge target
# ---------------------------------------------------------------------------
#
# The starter templates only cover files whose shape is worth shipping. For
# everything else the honest starter is the admin's own file: copy one element
# out of it and let them edit that. Nothing is guessed, and it works for a
# mission XML zsm has never heard of.


@dataclass(frozen=True)
class MergeSample:
    """One element copied out of a merge target, ready to seed a link."""

    target: str               # echoed back, mission-relative
    root_tag: str             # the file's root element ("" when unreadable)
    content: str              # "" when there was nothing safe to copy
    skipped_mod_blocks: int
    reason: str               # why `content` is empty; "" when it isn't


# A free-form merge may target any mission XML, including a modded db/types.xml
# that runs to tens of megabytes. Parsing that on a form interaction is not
# worth one starter button, so past this size the sampler declines and says so.
SAMPLE_MAX_BYTES = 2_000_000
# Enough to show the shape without pasting someone's whole event list.
SAMPLE_MAX_CHILDREN = 4


def sample_merge_element(
    server_dir: Path,
    mission_name: str,
    target: str,
    *,
    max_bytes: int = SAMPLE_MAX_BYTES,
    max_children: int = SAMPLE_MAX_CHILDREN,
) -> MergeSample | None:
    """Copy the first element of *target* that no mod owns, as link content.

    Returns None only for an unsafe target (the caller turns that into a 400).
    Every other outcome is a `MergeSample`, because the file's ``root_tag`` is
    worth having even when there is nothing to copy — it is what lets the UI
    offer a correct empty starter for a file zsm ships no template for.
    """
    path = mission_file_path(server_dir, mission_name, target)
    if path is None:
        return None
    if not path.is_file():
        return MergeSample(target, "", "", 0, "That file isn't part of this mission.")
    if path.stat().st_size > max_bytes:
        return MergeSample(
            target, "", "", 0,
            f"{path.name} is too large to copy from here — open it in its own editor instead.",
        )

    try:
        # preserve_comments is required, not a nicety: without the START/END MOD
        # markers every mod's merged element looks like the user's own, and
        # copying one into a link would double it on every spawn.
        root = load_xml(path, preserve_comments=True).getroot()
    except Exception:
        return MergeSample(target, "", "", 0, f"{path.name} is not valid XML right now.")

    skipped = sum(
        1 for node in root
        if not isinstance(node.tag, str) and (node.text or "").strip().startswith("START MOD:")
    )
    source = next(iter(unowned_children(root)), None)
    if source is None:
        return MergeSample(
            target, root.tag, "", skipped,
            "Everything in that file was added by a mod or a link — nothing of yours to copy.",
        )

    sample = _without_mod_blocks(source)
    trimmed = False
    if len(sample) > max_children:
        for child in list(sample)[max_children:]:
            sample.remove(child)
        trimmed = True

    wrapper = ET.Element(root.tag)
    wrapper.append(ET.Comment(f" Copied from {target}. Edit it, then save. "))
    wrapper.append(sample)
    if trimmed:
        wrapper.append(ET.Comment(" Trimmed for brevity — add as many as you need. "))

    content = format_xml_text(
        f"{XML_DECLARATION}\n{ET.tostring(wrapper, encoding='unicode')}"
    ).text
    return MergeSample(target, root.tag, content, skipped, "")


def _without_mod_blocks(elem: ET.Element) -> ET.Element:
    """Deep copy of *elem* with every mod-owned child dropped, at every level.

    Recursive on purpose: sampling an ``<event>`` out of cfgeventspawns.xml
    must not drag along the ``<pos>`` entries a mod merged into that same
    event. Comments go too — the markers are exactly what must not be copied,
    and the formatter re-indents whatever is left.
    """
    out = ET.Element(elem.tag, dict(elem.attrib))
    out.text = elem.text
    for child in unowned_children(elem):
        out.append(_without_mod_blocks(child))
    return out


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64]
    return slug or "event"


def _fork_id(slug: str) -> str:
    """Drop an immediately repeated first segment from a custom/ file's slug.

    custom/ names are `kebab_case(mod)-<the mod's own filename>`, so a mod whose
    file is already named after itself doubles: @TraderPlus's TraderPlus_Types.xml
    becomes `traderplus-traderplus-types`. Matched on the repeat itself, not on
    the mod name — stripping the mod prefix would turn `bikes-types` into the
    meaningless id `types`.
    """
    parts = slug.split("-")
    if len(parts) > 2 and parts[0] == parts[1]:
        return "-".join(parts[1:])
    return slug


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


def applies_to(link: dict, terrain: str | None) -> bool:
    """Enabled, and either map-agnostic or scoped to this terrain.

    `file_matches_terrain` does the terrain comparison so a link scoped to
    `enoch` accepts the same spellings a mod's `_Livonia.xml` file would.
    """
    if not link.get("enabled", True):
        return False
    scope = (link.get("terrain") or "").strip()
    if not scope:
        return True
    return file_matches_terrain(f"link_{scope}.xml", terrain)


def mod_matches(stored: str, mod_name: str) -> bool:
    """Does a stored ``mod`` value name *mod_name*?

    The value may be the mod dir name (``@CF``, case-insensitive, ``@``
    optional) or its workshop id, so the comparison can't be `==`. The id case
    is resolved through the installed-mod map when the caller has one.
    """
    stored = (stored or "").strip()
    if not stored or not mod_name:
        return False
    if stored.isdigit():
        return False  # ids are resolved by the caller via resolve_mod_value
    return stored.lstrip("@").lower() == mod_name.lstrip("@").lower()


def mod_is_off(stored: str, disabled_mods: Iterable[str] | None) -> bool:
    """Does a stored ``mod`` value name a mod that is turned off?

    A manual file follows its mod: while the mod is off, the file stays out of
    the mission. Before, `custom/user-traderplus-types.xml` stayed registered
    with @TraderPlus off, and DayZ logged every class as unknown (browser test
    2026-11-17, Q1). A mod that is not installed is not off, so a file
    prepared for it still applies. A Workshop ID is resolved through the IDs
    zsm knows (`modlist.known_ids`).
    """
    stored = (stored or "").strip()
    if not stored or not disabled_mods:
        return False
    if stored.isdigit():
        from app.modmgr.modlist import known_ids

        ids = known_ids()
        return any(ids.get(name) == stored for name in disabled_mods)
    return any(mod_matches(stored, name) for name in disabled_mods)


def _active_files(
    terrain: str | None,
    kinds: Iterable[str],
    only_mod: str | None = None,
    only_file: str | None = None,
    *,
    manual_dir: Path,
    disabled_mods: Iterable[str] | None = None,
) -> list[dict]:
    """The manual files to put into the mission: on, for this map, with
    content on disk, and not owned by a mod that is off (`mod_is_off`)."""
    wanted = set(kinds)
    return [
        link
        for link in load_files(manual_dir=manual_dir)
        if link.get("kind") in wanted
        and applies_to(link, terrain)
        and not mod_is_off(link.get("mod", ""), disabled_mods)
        and (only_mod is None or mod_matches(link.get("mod", ""), only_mod))
        and (only_file is None or link.get("id") == only_file)
        and file_content_path(link["id"], manual_dir=manual_dir).is_file()
    ]


def copy_manual_ce_files(
    custom_folder: Path,
    *,
    manual_dir: Path,
    terrain: str | None = None,
    only_file: str | None = None,
    disabled_mods: Iterable[str] | None = None,
    log: LogFn | None = None,
) -> list[tuple[str, str, str]]:
    """Copy CE-kind links into custom/, returning (filename, ce_type, name).

    Same tuple shape `xml_economy.find_and_copy_mods_xml` returns, so the caller
    registers mod files and links through one `_update_cfgeconomycore` call and
    the types-first ordering keeps holding.

    `only_file` copies exactly one — the CE half of `apply_manual_file`.
    """
    from app.xml.xml_economy import _wrap_with_root_if_needed

    results: list[tuple[str, str, str]] = []
    custom_folder.mkdir(parents=True, exist_ok=True)

    for link in _active_files(
        terrain, CE_KINDS, only_file=only_file, manual_dir=manual_dir, disabled_mods=disabled_mods,
    ):
        file_id = link["id"]
        dest = custom_folder / custom_file_name(file_id)
        copy_file(file_content_path(file_id, manual_dir=manual_dir), dest)
        _wrap_with_root_if_needed(dest, root_tag_for(link["kind"]))

        if not validate_xml_file(dest):
            dest.unlink()
            if log:
                log(f"Manual link {file_id}: skipped, {dest.name} is not valid XML.")
            continue

        results.append((dest.name, link["kind"], marker_name(file_id)))
        if log:
            log(f"Linked manual XML {file_id} as {link['kind']} ({dest.name}).")
    return results


def apply_manual_file(
    server_dir: Path,
    mission_name: str,
    file_id: str,
    *,
    manual_dir: Path,
    log: LogFn | None = None,
) -> bool:
    """Put one manual file into the mission, without running a whole merge.

    The scoped counterpart to "apply everything": both halves already know how to
    do one file — `copy_manual_ce_files` / `apply_direct_files` take `only_file`
    — and the CE half's missing piece is `xml_economy.register_custom_file`, the
    inverse of the `unregister_custom_file` every removal path calls.

    Used where a file has to go live the moment it is created and a full
    `merge_economy_core` would be far too big a hammer: taking over a generated
    file ("Set as manual") has to leave the mission serving the same content it
    served a second earlier.

    Returns False when nothing was applied — no record, or the file is disabled
    or scoped to another map.
    """
    from app.xml.xml_economy import register_custom_file

    terrain = terrain_from_mission(mission_name)
    # Checked up front for both halves: `apply_direct_files` reports failures,
    # not "there was nothing to do", so an inactive file would read as applied.
    active = _active_files(terrain, MANUAL_KINDS, only_file=file_id, manual_dir=manual_dir)
    if not active:
        return False

    if active[0].get("kind") in CE_KINDS:
        mission_dir = server_dir / "mpmissions" / mission_name
        copied = copy_manual_ce_files(
            mission_dir / "custom",
            manual_dir=manual_dir,
            terrain=terrain,
            only_file=file_id,
            log=log,
        )
        for name, ce_type, _mod in copied:
            register_custom_file(mission_dir, name, ce_type)
        return bool(copied)

    return not apply_direct_files(
        server_dir, mission_name, manual_dir=manual_dir, only_file=file_id, log=log,
    )


def apply_direct_files(
    server_dir: Path,
    mission_name: str,
    *,
    manual_dir: Path,
    only_mod: str | None = None,
    only_file: str | None = None,
    disabled_mods: Iterable[str] | None = None,
    log: LogFn | None = None,
) -> list[str]:
    """Splice direct-kind links into their mission files. Returns failed ids.

    Each link's own block is stripped first, so re-running is a no-op rather
    than a duplicate — the same trick `merge_mod_direct_configs` uses, and the
    reason this never restores from `.backup` (that would drop every mod).

    One write per file. A file that gets blocks back is stripped in memory and
    saved once with them in, so it is never on disk half merged, Versions keeps
    the file as it was, and an apply that ends where it started writes nothing
    (`save_xml` skips an identical document). Stripping on disk first wrote
    every file twice per apply and kept the stripped copy as a version.

    `only_file` applies exactly one, for the paths that just created it and want
    it live in the mission without re-splicing everything else.
    """
    from app.xml.xml_utils import save_xml

    terrain = terrain_from_mission(mission_name)
    failed: list[str] = []
    active = _active_files(
        terrain, DIRECT_KINDS, only_mod=only_mod, only_file=only_file, manual_dir=manual_dir,
        disabled_mods=disabled_mods,
    )

    # Every block of every file in scope goes, active or not. A file that is
    # off (disabled, or scoped to another map) must leave the mission too, and
    # a free-form merge's block even outlived a full merge, whose strip covers
    # only the merge configs. One that then fails to apply is stripped as well:
    # a stale block from a previous run must not outlive the file's state.
    markers = {
        marker_name(link["id"])
        for link in load_files(manual_dir=manual_dir)
        if link.get("kind") in DIRECT_KINDS
        and (only_mod is None or mod_matches(link.get("mod", ""), only_mod))
        and (only_file is None or link.get("id") == only_file)
    }

    # Each target parsed once without those blocks (None: not XML), and the
    # links it gets back, in the order they come back in.
    trees: dict[Path, ET.ElementTree | None] = {}
    by_target: dict[Path, list[dict]] = {}
    for link in active:
        file_id = link["id"]
        free_form = link["kind"] == MERGE_KIND
        cfg = None if free_form else ALL_MERGE_CONFIGS[link["kind"]]
        target = (
            link_target_path(server_dir, mission_name, link)
            if free_form
            else mission_target_path(server_dir, mission_name, cfg)
        )

        if target is None or not target.is_file():
            name = target.name if target is not None else link.get("target", "?")
            if log:
                log(
                    f"Manual link {file_id}: {name} is not part of this "
                    f"mission, nothing to merge into."
                )
            # A free-form merge names its target explicitly, so a missing one is
            # always an error; the merge configs have their own OPTIONAL rule.
            if free_form or not cfg.get("OPTIONAL"):
                failed.append(file_id)
            continue
        if target not in trees:
            trees[target] = _parse_without_blocks(target, markers)
        if trees[target] is None:
            if log:
                log(f"Manual link {file_id}: {target.name} is not valid XML.")
            failed.append(file_id)
            continue
        by_target.setdefault(target, []).append(link)

    # Every other file is stripped on disk; the targets below, in their write.
    remove_mod_xml_blocks(server_dir, mission_name, markers, skip=list(by_target))

    for target, links in by_target.items():
        tree = trees[target]
        ensure_backup(target)
        for link in links:
            file_id = link["id"]
            source = file_content_path(file_id, manual_dir=manual_dir)
            wrapped = _wrapped_copy(source, root_tag_for(link["kind"]))
            try:
                if link["kind"] == MERGE_KIND:
                    added = _merge_free_form(
                        tree, wrapped, marker_name(file_id), target_name=target.name, log=log,
                    )
                else:
                    cfg = ALL_MERGE_CONFIGS[link["kind"]]
                    added = splice_mod_files(
                        tree,
                        [wrapped],
                        cfg,
                        cfg.get("ELEMENTS_TO_REMOVE", []),
                        target_name=target.name,
                        mod_name=marker_name(file_id),
                        log=log,
                    )
            finally:
                if wrapped != source:
                    wrapped.unlink(missing_ok=True)

            if added == 0 and log:
                log(f"Manual link {file_id} contributed nothing to {target.name}.")
        save_xml(tree, target)
    return failed


def _parse_without_blocks(path: Path, markers: set[str]) -> ET.ElementTree | None:
    """*path* parsed with its comments and without *markers*' START/END MOD
    blocks, or None when that is not XML: what stripping the file and then
    loading it gave, without writing the stripped copy."""
    try:
        text = path.read_text(encoding="utf-8-sig")
        return parse_xml_text(strip_mod_blocks_text(text, only=markers)[0], preserve_comments=True)
    except (ET.ParseError, OSError, UnicodeDecodeError):
        return None


def mission_file_path(server_dir: Path, mission_name: str, target: str) -> Path | None:
    """Absolute path of a mission-relative target, or None if it isn't safe."""
    if not is_safe_target(target):
        return None
    return server_dir / "mpmissions" / mission_name / Path(*PurePosixPath(target).parts)


def link_target_path(server_dir: Path, mission_name: str, link: dict) -> Path | None:
    """Absolute path of a free-form merge's target, or None if it isn't safe.

    Re-validated on every apply, not just at create time: the stored record is a
    plain JSON file the user can edit.
    """
    return mission_file_path(server_dir, mission_name, link.get("target", ""))


def _merge_free_form(
    tree: ET.ElementTree,
    source: Path,
    marker: str,
    *,
    target_name: str,
    log: LogFn | None = None,
) -> int:
    """Splice a fragment into an arbitrary mission XML, parsed as *tree*.
    Returns elements added.

    The merge configs know which child tags belong where; a free-form merge does
    not, so the rule is the simplest one that is always right: every element
    under the fragment's root is appended to the mission file's root, between
    this link's START/END markers. A fragment that *is* a single element (no
    element children) is appended whole.

    *tree* is parsed with its comments for the same reason every other merge
    needs them — other mods' markers in the target must survive the round-trip
    or their content becomes unremovable.
    """
    root = tree.getroot()

    fragment = load_xml(source).getroot()
    children = [child for child in fragment if isinstance(child.tag, str)]
    if not children:
        children = [fragment]

    root.append(ET.Comment(f" START MOD: {marker} "))
    for child in children:
        root.append(child)
    root.append(ET.Comment(f" END MOD: {marker} "))

    if log:
        log(f"Merged {len(children)} element(s) from {marker} into {target_name}.")
    return len(children)


def _wrapped_copy(source: Path, root_tag: str) -> Path:
    """Return a parseable copy of *source*, wrapping a bare fragment if needed.

    A mod's file is wrapped in place (`_wrap_with_root_if_needed`); a link's
    file is the user's own, so it is copied to a sibling temp file instead of
    being rewritten under them.
    """
    from app.xml.xml_economy import _wrap_with_root_if_needed

    if validate_xml_file(source):
        return source
    tmp = source.with_suffix(".wrapped.xml")
    shutil.copy(source, tmp)
    _wrap_with_root_if_needed(tmp, root_tag)
    return tmp


def remove_link_blocks(server_dir: Path, mission_name: str, file_id: str) -> None:
    """Strip one link's START/END MOD blocks from every mission XML."""
    remove_mod_xml_blocks(server_dir, mission_name, marker_name(file_id))


def follow_mod_state(
    server_dir: Path,
    mission_name: str,
    mod_name: str,
    *,
    enable: bool,
    manual_dir: Path,
    log: LogFn | None = None,
) -> list[str]:
    """Take the CE half of *mod_name*'s manual files out of the mission, or
    put it back. Returns the file ids it changed.

    The direct half needs no call: `merge_all_direct_configs` with the new
    `disabled_mods` strips and re-applies it (`mod_is_off`). The CE half lives
    in custom/ and cfgeconomycore.xml, which only a full merge rewrites, so a
    mod toggle does it here (browser test 2026-11-17, Q1).
    """
    mission_dir = server_dir / "mpmissions" / mission_name
    changed: list[str] = []
    for link in load_files(manual_dir=manual_dir):
        if link.get("kind") not in CE_KINDS or not mod_is_off(link.get("mod", ""), {mod_name}):
            continue
        file_id = link["id"]
        if enable:
            if apply_manual_file(server_dir, mission_name, file_id, manual_dir=manual_dir, log=log):
                changed.append(file_id)
            continue
        _remove_ce_copy(mission_dir, file_id)
        changed.append(file_id)
        if log:
            log(f"Manual XML {file_id}: off while {mod_name} is off.")
    return changed


def remove_file_artifacts(server_dir: Path, mission_name: str, file_id: str) -> None:
    """Undo everything a link applied: merged blocks, custom/ copy, <ce> entry."""
    remove_link_blocks(server_dir, mission_name, file_id)
    _remove_ce_copy(server_dir / "mpmissions" / mission_name, file_id)


def _remove_ce_copy(mission_dir: Path, file_id: str) -> None:
    """Delete a link's custom/ copy and its cfgeconomycore registration."""
    from app.xml.xml_economy import unregister_custom_file

    custom_file = mission_dir / "custom" / custom_file_name(file_id)
    if custom_file.is_file():
        custom_file.unlink()
    unregister_custom_file(mission_dir, custom_file_name(file_id))
