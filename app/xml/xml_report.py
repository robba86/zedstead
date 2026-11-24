"""Where did every mod XML end up? — the report behind the web Mod XML view.

Installing a mod scatters its XML fragments in two directions: CE-linkable
files (types/events/spawnabletypes) are copied into ``mpmissions/<mission>/
custom/`` and registered in cfgeconomycore.xml, while everything else is
spliced straight into the matching mission file. Neither destination is
obvious from the UI, and files that were deliberately skipped (another map's
variant, an XML the mod uses for itself) previously vanished without a
trace.

`build_mod_xml_report` walks the same filters the merge uses and reports one
row per mod XML file with the status it ended up in, so the answer to "where
is my mod's events.xml?" is a page rather than a filesystem hunt.
"""

from __future__ import annotations

import re
import stat
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from app.modmgr.mods import EXCLUDED_MOD_KEYWORDS, get_current_mission, mod_is_excluded
from app.xml import xml_classify, xml_economy, xml_merge
from app.xml.xml_classify import CE_KINDS, CE_TYPES, KIND_MESSAGES
from app.xml.xml_configs import ALL_MERGE_CONFIGS
from app.modmgr import modlist
from app.shared.json_utils import read_text_lossy
from app.xml.xml_merge import _mod_markers

if TYPE_CHECKING:
    from app.shared.config import DayzConfig

# Status values (also the sort order used by the frontend).
STATUS_LINKED = "linked"          # copied to custom/ + <ce> entry
STATUS_MERGED = "merged"          # spliced into a mission file
STATUS_DISABLED = "disabled"      # mod is disabled — deliberately not applied
STATUS_EXCLUDED = "excluded"      # this file turned off by the user
STATUS_OTHER_MAP = "other_map"    # variant for a different terrain
STATUS_NO_TARGET = "no_target"    # kind recognised, mission file absent
STATUS_BAD_NAME = "bad_name"      # merge skips it: "--" or a trailing "-" in the folder name
STATUS_INVALID = "invalid"        # not parseable as XML
STATUS_IGNORED = "ignored"        # not a central-economy file
STATUS_DELETED = "deleted"        # this file deleted by the user (permanent)
# A CE file the merge deliberately never links off a mod dir — messages, whose
# example broadcasts would otherwise reach players. Opt in with "Set as manual".
STATUS_NOT_AUTO_LINKED = "not_auto_linked"

# Which card the frontend shows a file in. Derived from the file's *kind*, never
# from its status: the same file has to stay in the same place whether it is
# live, disabled, deleted or not applied yet. Grouping on status is what used to
# make a disabled custom/ file jump from the tile grid into the merged table.
GROUP_LINKED = "linked"    # a central-economy file -> custom/ + cfgeconomycore
GROUP_MERGED = "merged"    # spliced into a mission file
GROUP_SKIPPED = "skipped"  # never applied: not CE, unparseable, or another map

# cfgeconomycore.xml is read as text, not as a tree: a disabled registration
# lives inside an XML comment, which no parser hands back as an entry. Both scans
# below find their openers with str.find and reuse the next close they found, so
# an opener with no close costs nothing extra; the lazy regexes they replaced
# re-scanned to the end of the file from every such opener (plans/68 CON-7).
#
# The `<ce>` header captures the folder rather than hardcoding "custom", so the
# same scan answers both "what did zsm generate" and "what else is registered".
# Pre-existing limitation kept: `folder` must be the `<ce>`'s first and only
# attribute, which is how DayZ and zsm both write it.
_CE_OPEN_RE = re.compile(r'<ce\s+folder="(?P<folder>[^"]+)"\s*>')
_CE_FILE_RE = re.compile(r'<file\s+name="(?P<name>[^"]+)"\s+type="(?P<type>[^"]+)"')

CUSTOM_CE_FOLDER = "custom"


def _disabled_wrappers(text: str) -> list[tuple[int, int, int]]:
    """``(start, body_start, end)`` of every ``<!-- DISABLED <label>: … -->`` comment.

    The label runs from ``DISABLED`` and at least one whitespace to the first
    ``:``, which has to come before the comment's ``-->``; the body is the rest
    of the comment, up to the first ``-->``. The next search starts after it.
    """
    spans: list[tuple[int, int, int]] = []
    size = len(text)
    close = colon = -1
    i = text.find("<!--")
    while i >= 0:
        if close < i + 4:
            close = text.find("-->", i + 4)
            if close < 0:
                break  # no "-->" from here on, so no comment can close
        k = i + 4
        while k < close and text[k].isspace():
            k += 1
        if text.startswith("DISABLED", k, close):
            if colon < k + 8:
                colon = text.find(":", k + 8)
                if colon < 0:
                    colon = size  # none from here on
            label = text[k + 8:colon] if colon < close else ""
            if len(label) >= 2 and label[0].isspace():
                spans.append((i, colon + 1, close + 3))
                i = text.find("<!--", close + 3)
                continue
        i = text.find("<!--", i + 4)
    return spans


def _without_disabled(text: str, wrappers: list[tuple[int, int, int]]) -> str:
    """*text* with every disabled wrapper cut out."""
    parts: list[str] = []
    pos = 0
    for start, _body, end in wrappers:
        parts.append(text[pos:start])
        pos = end
    parts.append(text[pos:])
    return "".join(parts)


def _ce_blocks(text: str) -> list[tuple[str, str]]:
    """``(folder, body)`` of every ``<ce folder="…">`` … ``</ce>`` block, in order."""
    blocks: list[tuple[str, str]] = []
    close = -1
    i = text.find("<ce")
    while i >= 0:
        head = _CE_OPEN_RE.match(text, i)
        if head is None:
            i = text.find("<ce", i + 3)
            continue
        if close < head.end():
            close = text.find("</ce>", head.end())
            if close < 0:
                break  # no "</ce>" from here on, so no block can close
        blocks.append((head.group("folder"), text[head.end():close]))
        i = text.find("<ce", close + 5)
    return blocks


def _ce_entries(mission_dir: Path) -> dict[str, dict]:
    """{custom filename: {"type": ..., "disabled": bool}} from cfgeconomycore.xml.

    `custom/` only — the folder zsm generates. Everything registered under any
    other folder is `_foreign_ce_entries`' job.

    A `<ce folder="custom">` holds *all* the registered files (one `<file>`
    each), so the block is scanned rather than assumed to be a single-file one.
    """
    text = _economycore_text(mission_dir)
    if text is None:
        return {}

    entries: dict[str, dict] = {}
    wrappers = _disabled_wrappers(text)
    for _start, body, end in wrappers:
        for m in _CE_FILE_RE.finditer(text, body, end - 3):
            entries[m.group("name")] = {"type": m.group("type"), "disabled": True}
    # Live entries second and last-wins: a file that is both registered and
    # parked (shouldn't happen) counts as live, same as the old single regex.
    for folder, body in _ce_blocks(_without_disabled(text, wrappers)):
        if folder != CUSTOM_CE_FOLDER:
            continue
        for m in _CE_FILE_RE.finditer(body):
            entries[m.group("name")] = {"type": m.group("type"), "disabled": False}
    return entries


def _economycore_text(mission_dir: Path) -> str | None:
    path = mission_dir / "cfgeconomycore.xml"
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _foreign_ce_entries(mission_dir: Path) -> list[dict]:
    """Every `<ce folder=…>` registration *outside* custom/, read-only.

    DayZ explicitly supports several CE folders (the documented layout is one
    folder overriding vanilla, another appending mod files), and zsm preserves
    them on every rewrite — but nothing in the UI could see them, so a file
    loaded by the server appeared nowhere in the app. Includes the mission's own
    `db` block, which is the point: this is "what else does the economy load".

    zsm never writes these, so there are no actions on them; `known_type` flags
    a `type=` DayZ doesn't accept, which silently breaks the whole economy load.
    """
    text = _economycore_text(mission_dir)
    if text is None:
        return []

    rows: list[dict] = []
    for folder, body in _ce_blocks(_without_disabled(text, _disabled_wrappers(text))):
        if folder == CUSTOM_CE_FOLDER:
            continue
        for m in _CE_FILE_RE.finditer(body):
            name = m.group("name")
            ce_type = m.group("type")
            # A path that leaves the mission is neither stat'ed nor listed.
            path = xml_economy.ce_file_path(mission_dir, folder, name)
            if path is None:
                xml_economy.log_outside_mission(mission_dir, folder, name)
                continue
            size = _file_size(path)
            rows.append(
                {
                    "folder": folder,
                    "name": name,
                    "type": ce_type,
                    "known_type": ce_type in CE_TYPES,
                    "exists": size is not None,
                    "size": size or 0,
                }
            )
    return rows


def _file_size(path: Path) -> int | None:
    """*path*'s size, or None when it is not a file, or no longer one.

    A merge wipes and rebuilds custom/ (`xml_economy._clear_custom_folder`)
    while this report reads it, so a file listed a moment ago can be gone by
    the time it is stat'ed. Checking first and then stat'ing raised for that,
    and GET /api/modxml answered 500.
    """
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_size if stat.S_ISREG(st.st_mode) else None


def _merged_mods(mission_dir: Path) -> dict[str, list[str]]:
    """{mission filename: [mod names with a START MOD block in it]}."""
    out: dict[str, list[str]] = {}
    for cfg in ALL_MERGE_CONFIGS.values():
        rel = cfg["MISSION_FOLDER_TEMPLATE"].split("{mission_folder}")[-1].lstrip("/")
        target = mission_dir / rel / (cfg["FILE_NAME"] + cfg["FILE_EXT"])
        if not target.is_file():
            continue
        # The merge's own marker reader, so a name with a space in it (one a
        # Workshop title gave) is read whole; lossy, since a report must not
        # fail on a map author's Windows-1252 file.
        text = read_text_lossy(target)
        names = [m.name for m in _mod_markers(text) if m.is_start and m.whole]
        if names:
            # dict.fromkeys: de-dupe (a mod can contribute several files) while
            # keeping the order the blocks appear in.
            out[target.name] = list(dict.fromkeys(names))
    return out


def installed_mod_ids(config: DayzConfig) -> dict[str, str]:
    """``{mod dir name: workshop id}`` for what is on this box.

    The id half is best-effort (a mod installed by name may have none cached)
    and only exists so a manual XML file can name its mod by workshop id — the
    one spelling a user pre-configuring for a not-yet-installed mod has.
    """
    from app.modmgr import modlist

    server_dir = config.server_dir_path
    if not server_dir.is_dir():
        return {}
    cached = {
        name if name.startswith("@") else f"@{name}": wid
        for name, wid in modlist.workshop_ids(config).items()
    }
    return {
        p.name: cached.get(p.name, "")
        for p in server_dir.iterdir()
        if p.is_dir() and p.name.startswith("@")
    }


def _target_for_kind(kind: str) -> str | None:
    cfg = ALL_MERGE_CONFIGS.get(kind)
    return (cfg["FILE_NAME"] + cfg["FILE_EXT"]) if cfg else None


def build_mod_xml_report(config: DayzConfig) -> dict:
    """One row per XML file shipped by an installed mod, plus its destination.

    Two lists carry the page:

    * ``files`` — every mod XML, each tagged with the ``group`` (card) it belongs
      to and, for central-economy kinds, the ``custom_name`` it generates. That
      name is the stable join key between the two lists: unlike ``custom_file``
      it is set whatever the file's status, so a disabled or deleted file still
      pairs with its tile instead of surfacing as a second row elsewhere.
    * ``custom`` — the tile list: every central-economy file the mission *could*
      load, keyed by generated name. Files on disk in ``custom/``, plus the ones
      the user turned off, plus the ones no merge has generated yet.
    """
    server_dir = config.server_dir_path
    mission = get_current_mission(config.server_cfg_path)
    if not mission or not server_dir.is_dir():
        return {
            "mission": mission or "",
            "files": [],
            "custom": [],
            "merged": {},
            "manual": [],
            "ce_folders": [],
        }

    mission_dir = server_dir / "mpmissions" / mission
    terrain = xml_classify.terrain_from_mission(mission)
    disabled_mods = modlist.disabled(config)
    ce_entries = _ce_entries(mission_dir)
    merged = _merged_mods(mission_dir)

    from app.xml import xml_manual

    manual_dir = config.paths.custom_xml_dir
    source_states = xml_manual.load_source_states(manual_dir=manual_dir)
    custom_states = xml_manual.load_custom_states(manual_dir=manual_dir)

    files: list[dict] = []
    for mod_dir in sorted(p for p in server_dir.iterdir() if p.is_dir() and p.name.startswith("@")):
        mod_name = mod_dir.name
        if mod_is_excluded(mod_name, EXCLUDED_MOD_KEYWORDS):
            continue
        for xml_file in sorted(mod_dir.rglob("*.xml")):
            if not xml_file.is_file():  # the merge skips a directory named *.xml too
                continue
            source = xml_manual.normalize_source(xml_file.relative_to(server_dir))
            source_state = source_states.get(source, "")
            row = {
                "mod": mod_name,
                "name": xml_file.name,
                "source": source,
                "kind": None,
                "status": STATUS_IGNORED,
                "group": GROUP_SKIPPED,
                "target": None,
                "custom_file": None,
                "custom_name": None,
                # Reported even when another status wins (a disabled mod's file
                # can also be excluded), so the row's button still says "Enable".
                "excluded": bool(source_state),
                "deleted": source_state == xml_manual.STATE_DELETED,
            }
            # The merge's own predicate, not a lookalike: a well-formed *rootless*
            # fragment has no root to classify but IS linked (through the filename
            # fallback), and reporting it "Invalid" while it sat live in custom/ is
            # exactly the divergence this shares `ce_bucket` to prevent. After
            # this, STATUS_INVALID means "not XML even once wrapped" — which is
            # also precisely when the merge refuses it.
            kind, root_tag = xml_classify.classify_mod_file(xml_file)
            if root_tag is None:
                bucket = xml_economy.ce_bucket(xml_file)
                if bucket is None:
                    row["status"] = STATUS_INVALID
                    files.append(row)
                    continue
                kind = bucket[0]
            row["kind"] = kind
            custom_name = xml_economy.custom_file_name(mod_name, xml_file.name)

            if kind is None and custom_name not in ce_entries:
                files.append(row)
                continue

            # Card first, and independent of everything below: a central-economy
            # file belongs to the tile grid even when nothing has generated it
            # yet, and a direct merge belongs to the table even when it is off.
            if kind in CE_KINDS or custom_name in ce_entries:
                row["group"] = GROUP_LINKED
                row["custom_name"] = custom_name
            elif kind is not None:
                row["group"] = GROUP_MERGED
            custom_state = custom_states.get(custom_name, "") if row["custom_name"] else ""

            if mod_name in disabled_mods:
                row["status"] = STATUS_DISABLED
            elif row["deleted"] or custom_state == xml_manual.STATE_DELETED:
                row["status"] = STATUS_DELETED
            elif row["excluded"] or custom_state:
                # Ahead of the destination checks: the endpoints bring the
                # mission in line as they exclude, so there is nothing left to
                # report as linked or merged.
                row["status"] = STATUS_EXCLUDED
            elif not xml_classify.file_matches_terrain(xml_file.name, terrain):
                row["status"] = STATUS_OTHER_MAP
                row["group"] = GROUP_SKIPPED
            elif custom_name in ce_entries:
                row["status"] = STATUS_LINKED
                row["custom_file"] = custom_name
                row["kind"] = kind or ce_entries[custom_name]["type"]
                row["target"] = "cfgeconomycore.xml"
            elif kind == KIND_MESSAGES:
                # The one CE kind the merge never links off a mod dir; the file
                # is understood and forkable, just not applied by itself.
                row["status"] = STATUS_NOT_AUTO_LINKED
                row["target"] = "cfgeconomycore.xml"
            elif kind in CE_KINDS:
                # Classified as CE-linkable but no <ce> entry — the mission's
                # cfgeconomycore hasn't been rebuilt since the mod landed.
                row["status"] = STATUS_NO_TARGET
                row["target"] = "cfgeconomycore.xml"
            else:
                target = _target_for_kind(kind)
                row["target"] = target
                if target and mod_name in merged.get(target, []):
                    row["status"] = STATUS_MERGED
                elif target and not xml_merge.marker_name_ok(mod_name):
                    # The merge skips it (xml_merge.find_and_prepare_mod_files):
                    # the folder name cannot sit in a START/END MOD comment.
                    row["status"] = STATUS_BAD_NAME
                else:
                    row["status"] = STATUS_NO_TARGET
            files.append(row)

    # The mod file each generated name comes from, so a tile can name its origin
    # (and offer to fork it) even when nothing generated the file this time.
    ce_sources = {
        row["custom_name"]: row
        for row in files
        if row["group"] == GROUP_LINKED and row["custom_name"]
    }
    custom = []
    custom_dir = mission_dir / "custom"
    if custom_dir.is_dir():
        for path in sorted(custom_dir.glob("*.xml")):
            size = _file_size(path)
            if size is None:
                continue  # gone since the listing: a missing tile below
            entry = ce_entries.get(path.name, {})
            state = custom_states.get(path.name, "")
            source_row = ce_sources.get(path.name) or {}
            custom.append(
                {
                    "name": path.name,
                    # The <ce> entry is the authority; an unregistered file still
                    # knows its kind from the mod file that produced it.
                    "type": entry.get("type") or source_row.get("kind"),
                    "linked": path.name in ce_entries,
                    "disabled": bool(entry.get("disabled")),
                    "excluded": bool(state),
                    "deleted": state == xml_manual.STATE_DELETED,
                    "missing": False,
                    "mod": xml_manual.owning_mod(server_dir, path.name),
                    "source": source_row.get("source"),
                    "size": size,
                }
            )

    # A file that isn't in custom/ still gets a tile. Three ways that happens:
    # the user turned it off (it was deleted from the mission), the owning mod is
    # disabled (never copied), or no merge has run since the mod landed. All
    # three used to fall through to the merged-files table, which is where the
    # "disabling a file moves it to the other card" bug came from.
    have = {c["name"] for c in custom}
    for name in sorted((set(custom_states) | set(ce_sources)) - have):
        row = ce_sources.get(name) or {}
        state = custom_states.get(name, "")
        custom.append(
            {
                "name": name,
                "type": row.get("kind"),
                "linked": False,
                "disabled": row.get("status") == STATUS_DISABLED,
                "excluded": bool(state),
                "deleted": state == xml_manual.STATE_DELETED,
                "missing": True,
                "mod": row.get("mod") or xml_manual.owning_mod(server_dir, name),
                "source": row.get("source"),
                "size": 0,
            }
        )

    installed = installed_mod_ids(config)
    manual = _file_rows(
        mission_dir, terrain, installed, ce_entries, merged, server_dir, manual_dir=manual_dir,
    )

    return {
        "mission": mission,
        "files": files,
        "custom": custom,
        "merged": merged,
        # "manual", not "files": that key is already the mod XML rows.
        "manual": manual,
        # Registrations under a CE folder zsm doesn't generate. Read-only.
        "ce_folders": _foreign_ce_entries(mission_dir),
    }


def _marker_in_file(mission_dir: Path, target: str, file_id: str) -> bool:
    """Is this link's START marker present in the mission file it targets?"""
    from app.xml import xml_manual

    if not target or not xml_manual.is_safe_target(target):
        return False
    path = mission_dir / Path(*PurePosixPath(target).parts)
    if not path.is_file():
        return False
    return f"START MOD: {xml_manual.marker_name(file_id)}" in read_text_lossy(path)


def _file_rows(
    mission_dir: Path,
    terrain: str | None,
    installed_mods: dict[str, str],
    ce_entries: dict[str, dict],
    merged: dict[str, list[str]],
    server_dir: Path,
    *,
    manual_dir: Path,
) -> list[dict]:
    """One row per manual XML file, with the status it currently has.

    Read off the mission the same way mod rows are — a manual file is "applied"
    when its artefacts are actually in the mission, not when the config says it
    should be, so one added since the last merge shows as pending.

    `installed_mods` is ``{mod dir name: workshop id}``: the stored ``mod`` may
    be either spelling, so the match needs both.
    """
    from app.xml import xml_manual

    rows: list[dict] = []
    for link in xml_manual.load_files(manual_dir=manual_dir):
        file_id = link.get("id", "")
        kind = link.get("kind", "")
        row = {
            "id": file_id,
            "kind": kind,
            # `mod` is what the user typed (a name or a workshop id);
            # `mod_name` is the installed mod it resolves to, so every consumer
            # joins on one spelling instead of re-deriving the match.
            "mod": link.get("mod", ""),
            "mod_name": xml_manual.resolve_mod_value(link.get("mod", ""), installed_mods),
            "terrain": link.get("terrain", ""),
            "note": link.get("note", ""),
            "enabled": bool(link.get("enabled", True)),
            # Set when the link came from forking a generated custom/ file, so
            # the page can pair the greyed-out original with its replacement.
            "forked_from": link.get("forked_from", ""),
            # For a file taken over from a mod: has the mod changed it since?
            # Hash-only here (no diff) — this runs for every row on every load.
            "upstream": xml_manual.upstream_status(link, server_dir),
            "target": None,
            "custom_file": None,
            "status": STATUS_NO_TARGET,
        }

        if kind == xml_manual.MERGE_KIND:
            # A free-form merge names its own target, which _merged_mods (which
            # only walks ALL_MERGE_CONFIGS) never scans — read the file itself.
            row["target"] = link.get("target") or None
            applied = _marker_in_file(mission_dir, link.get("target", ""), file_id)
        elif kind in CE_KINDS:
            row["target"] = "cfgeconomycore.xml"
            custom_name = xml_manual.custom_file_name(file_id)
            row["custom_file"] = custom_name
            applied = custom_name in ce_entries
        else:
            target = _target_for_kind(kind)
            row["target"] = target
            applied = bool(target) and xml_manual.marker_name(file_id) in merged.get(target, [])

        if not row["enabled"]:
            row["status"] = STATUS_DISABLED
        elif not xml_manual.applies_to(link, terrain):
            row["status"] = STATUS_OTHER_MAP
        elif applied:
            row["status"] = STATUS_LINKED if kind in CE_KINDS else STATUS_MERGED
        if row["mod"] and not row["mod_name"]:
            # Kept, not hidden: the content still merges, but it references a
            # mod's classnames that are no longer there (RPT errors at boot).
            row["orphaned"] = True
        rows.append(row)
    return rows


def mod_source_path(config: DayzConfig, source: str) -> Path | None:
    """`resolve_mod_source` for callers that hold a config — the common case."""
    return resolve_mod_source(config.server_dir_path, source)


def resolve_mod_source(server_dir: Path, source: str) -> Path | None:
    """Resolve a report row's ``source`` to the mod XML it names, or None.

    ``source`` is server-dir-relative (``@Mod/.../types.xml``) and arrives back
    from a query string, so it is re-derived rather than trusted: it must be an
    ``.xml`` under a safe ``@mod`` directory, and the resolved path must still
    sit inside the server dir (which also rules out a symlink pointing out of
    it). Read-only by design — SteamCMD overwrites mod dirs on every update.

    Takes the server dir rather than a config so `xml_manual` can use the same
    guard — a stored upstream baseline names its mod file this way too, and it
    is re-read on every status check.
    """
    from app.modmgr.mods import is_safe_mod_dir_name

    if not source or not source.lower().endswith(".xml"):
        return None
    parts = PurePosixPath(source.replace("\\", "/")).parts
    if len(parts) < 2 or not is_safe_mod_dir_name(parts[0]) or ".." in parts:
        return None

    root = server_dir.resolve()
    path = (root / Path(*parts)).resolve()
    if not path.is_file():
        return None
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


def custom_file_path(config: DayzConfig, name: str) -> Path | None:
    """Resolve a `custom/` filename to a path, or None if it isn't one.

    `name` arrives from a URL path segment, so it is rejected unless it's a
    bare filename component that already exists in the folder — never joined
    blindly onto the directory.
    """
    if not name or Path(name).name != name or not name.lower().endswith(".xml"):
        return None
    mission = get_current_mission(config.server_cfg_path)
    if not mission:
        return None
    path = config.server_dir_path / "mpmissions" / mission / "custom" / name
    return path if path.is_file() else None
