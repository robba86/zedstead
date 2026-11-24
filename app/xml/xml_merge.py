"""XML merge engine — adapted from module_merge_mods_xml.py.

All functions take explicit server_dir / paths instead of using CWD.
Preserves the exact merge algorithm from the original.
"""

from __future__ import annotations

import shutil
from bisect import bisect_left
from pathlib import Path
from typing import Callable, Collection, Iterator, NamedTuple
from xml.etree import ElementTree as ET

from rich.console import Console

from app.modmgr.mods import mod_is_excluded
from app.shared.json_utils import atomic_write_text, copy_file
from app.xml import xml_classify
from app.xml.xml_utils import (
    MAX_XML_DEPTH,
    load_valid_xml,
    load_xml,
    one_version_per_file,
    save_xml,
    snapshot_before_write,
    validate_xml_file,
    xml_depth,
)

# XmlMergeConfig is a TypedDict — import at runtime is safe (no circular dep)
from app.xml.xml_configs import MODE_SECTIONS, XmlMergeConfig

console = Console()

# Callback used to surface merge progress/warnings where the user can see them
# (the web job log). Defaults to the Rich console for CLI callers.
LogFn = Callable[[str], None]


def _default_log(message: str) -> None:
    console.print(message)


# ---------------------------------------------------------------------------
# Shared helpers (used by xml_economy.py too)
# ---------------------------------------------------------------------------

def ensure_backup(file_path: Path) -> None:
    """Create a .backup of file_path if none exists. Never restores.

    Per-mod merge paths must use this rather than `ensure_backup_or_restore`:
    restoring the pristine mission file would wipe every *other* mod's merged
    blocks, which is exactly what scoping a merge to one mod has to avoid.
    """
    backup_path = file_path.with_suffix(file_path.suffix + ".backup")
    if backup_path.exists():
        return
    try:
        shutil.copy2(file_path, backup_path)
        console.print(f"[dim]Backup created: {backup_path}[/dim]")
    except OSError as e:
        console.print(f"[red]Failed to create backup: {e}[/red]")


def ensure_backup_or_restore(file_path: Path) -> None:
    """Create a .backup of file_path if none exists, otherwise restore from it.

    No merge path uses this any more (2026 Q3): a whole-mission merge now
    creates the backup and strips the mod blocks by name
    (`strip_all_mod_blocks`), because restoring also deleted everything the
    admin had written into the file themselves. Kept as the explicit
    "put this file back the way it shipped" operation — that is what the
    editors' *Restore from backup* button is for, and it is a deliberate act
    there rather than a side effect of merging.
    """
    backup_path = file_path.with_suffix(file_path.suffix + ".backup")
    if not backup_path.exists():
        ensure_backup(file_path)
    else:
        copy_file(backup_path, file_path)
        console.print(f"[dim]Restored {file_path.name} from backup.[/dim]")


def iter_mod_xml_files(
    server_dir: Path,
    excluded_mod_parts: list[str],
    excluded_file_parts: list[str],
    *,
    filename_contains: str = "",
    ext: str = ".xml",
    disabled_mods: set[str] | None = None,
    mods_only: set[str] | None = None,
    terrain: str | None = None,
) -> Iterator[tuple[str, Path]]:
    """Yield (mod_name, file_path) for XML files inside @-prefixed mod dirs.

    `mods_only`, if given, restricts iteration to mods whose dir name is in
    the set (allow-list, applied before the other exclusion filters).

    `terrain`, if given, drops map-specific variants belonging to another map
    (a mod shipping both ``cfgeventspawns_Chernarus.xml`` and
    ``cfgeventspawns_Livonia.xml`` must contribute only the current one).
    """
    for mod_dir in sorted(server_dir.iterdir()):
        if not (mod_dir.is_dir() and mod_dir.name.startswith("@")):
            continue
        if mods_only is not None and mod_dir.name not in mods_only:
            continue
        if mod_is_excluded(mod_dir.name, excluded_mod_parts):
            continue
        if disabled_mods and mod_dir.name in disabled_mods:
            continue
        for xml_file in mod_dir.rglob(f"*{ext}"):
            if not xml_file.is_file():  # a mod may ship a directory named *.xml
                continue
            name = xml_file.name.lower()
            if filename_contains and filename_contains.lower() not in name:
                continue
            if any(ep.lower() in name for ep in excluded_file_parts):
                continue
            if not xml_classify.file_matches_terrain(xml_file.name, terrain):
                continue
            yield mod_dir.name, xml_file


# ---------------------------------------------------------------------------
# Merge-specific functions
# ---------------------------------------------------------------------------

def get_mod_name(path: str | Path) -> str:
    for part in reversed(Path(path).parts):
        if part.startswith("@"):
            return part
    return "UnknownMod"


def marker_name_ok(mod_name: str) -> bool:
    """True when *mod_name* can sit in a ``<!-- START MOD: … -->`` comment.
    XML forbids "--" inside a comment, and a trailing "-" meets the closing
    "-->" (the marker pads the name with a space, but a reader may not). A
    Workshop title never gives such a name (`mod_ops.TITLE_SEPARATORS`), so
    only a local folder the admin named can."""
    return "--" not in mod_name and not mod_name.endswith("-")


def find_and_prepare_mod_files(
    server_dir: Path,
    cfg: XmlMergeConfig,
    *,
    manual_dir: Path,
    disabled_mods: set[str] | None = None,
    mods_only: set[str] | None = None,
    terrain: str | None = None,
    excluded_sources: set[str] | None = None,
    log: LogFn | None = None,
) -> list[tuple[str, Path]]:
    """Return (mod_name, xml_path) pairs for valid mod XML files.

    The filename filters only narrow the candidate set; the final say belongs
    to `xml_classify.classify_mod_xml`, so a file whose *content* is a
    different CE kind than this merge handles is dropped even when its name
    matched (mods reuse names like ``events.xml`` for both economy events and
    spawn-position tables).

    `mods_only`, if given, restricts the scan to the named mods — used by the
    per-mod re-configure path.

    `excluded_sources` are mod XMLs the user turned off (server-dir-relative
    keys); ``None`` means "read the list stored in `manual_dir`", which is what every merge in
    the pipeline wants. A caller passes an explicit set only to preview one more
    exclusion without persisting it (the Mod XML page's one-off Delete).

    A file nested deeper than `MAX_XML_DEPTH` is skipped and named through
    *log*: the merge could not write the mission file with it in, and it
    used to find that out after stripping every mod's block (plans/68 2P-C3).
    """
    from app.xml.xml_manual import load_source_exclusions, normalize_source

    if excluded_sources is None:
        excluded_sources = load_source_exclusions(manual_dir=manual_dir)

    result: list[tuple[str, Path]] = []
    for mod_name, xml_file in iter_mod_xml_files(
        server_dir,
        cfg["EXCLUDED_MOD_KEYWORDS"],
        cfg["EXCLUDED_FILE_PARTS"],
        filename_contains=cfg["MOD_FILENAME_CONTAINS"],
        ext=cfg["FILE_EXT"],
        disabled_mods=disabled_mods,
        mods_only=mods_only,
        terrain=terrain,
    ):
        if not marker_name_ok(mod_name):
            if log:
                log(
                    f"Skipped {mod_name}/{xml_file.name}: a mod folder name with \"--\" or a "
                    f"trailing \"-\" cannot go into the START/END MOD comments. Rename the folder."
                )
            continue
        if excluded_sources and normalize_source(
            xml_file.relative_to(server_dir)
        ) in excluded_sources:
            continue
        tree = load_valid_xml(xml_file)
        if tree is None:
            continue
        kind = xml_classify.classify_mod_xml(xml_file)
        if kind is not None and kind != cfg["CLASSIFY_KIND"]:
            continue
        depth = xml_depth(tree.getroot())
        if depth > MAX_XML_DEPTH:
            if log:
                log(
                    f"Skipped {mod_name}/{xml_file.name}: nested {depth} levels deep, "
                    f"more than the {MAX_XML_DEPTH} a merge can write."
                )
            continue
        result.append((mod_name, xml_file))
    return result


def remove_elements_by_name(root: ET.Element, names_to_remove: list[str]) -> None:
    if not names_to_remove:
        return
    parent_map = {child: parent for parent in root.iter() for child in parent}
    for elem in list(root.iter()):
        if elem.attrib.get("name") in names_to_remove:
            parent = parent_map.get(elem)
            if parent is not None:
                parent.remove(elem)


def _collect_children(mod_root: ET.Element, child_tags: list[str]) -> list[ET.Element]:
    """Elements to splice in, matched by *child* tag rather than root tag.

    Mods wrap the same payload in whatever root they feel like — a spawn table
    turns up under ``<eventposdef>``, ``<events>``, or as a bare ``<event>``.
    Keying on the root tag (the original behaviour) silently merged nothing
    whenever the mod picked a different wrapper, so match on what the elements
    *are* instead.
    """
    tags = {t.lower() for t in child_tags}
    if mod_root.tag.lower() in tags:
        return [mod_root]
    return [child for child in mod_root if isinstance(child.tag, str) and child.tag.lower() in tags]


def _merge_sections(
    file_element: ET.Element, mod_root: ET.Element, section_tags: list[str], mod_name: str
) -> int:
    """Section-shaped merge: append each mod section's children into the
    mission file's same-named section. Returns the number of elements added.

    The START/END MOD comments are written *inside* each section rather than at
    the root, so `remove_mod_xml_blocks` still strips exactly what was added
    when the mod is uninstalled.
    """
    added = 0
    tags = {t.lower() for t in section_tags}
    for mod_section in mod_root:
        if not isinstance(mod_section.tag, str) or mod_section.tag.lower() not in tags:
            continue
        children = list(mod_section)
        if not children:
            continue
        target = file_element.find(mod_section.tag)
        if target is None:
            target = ET.SubElement(file_element, mod_section.tag)
        target.append(ET.Comment(f" START MOD: {mod_name} "))
        for child in children:
            target.append(child)
            added += 1
        target.append(ET.Comment(f" END MOD: {mod_name} "))
    return added


def insert_data_from_mods(
    file_path: str | Path,
    mod_files: list[Path],
    cfg: XmlMergeConfig,
    names_to_remove: list[str],
    *,
    mod_name: str | None = None,
    log: LogFn | None = None,
) -> int:
    """Splice every mod file into *file_path*. Returns elements added.

    `mod_name` overrides the name derived from the file's path, for sources that
    don't live in a `@Mod` directory — manual XML links (`xml_manual.py`) pass
    their `@user:<id>` marker name that way.
    """
    # preserve_comments: the target may already carry other mods' START/END MOD
    # markers (the per-mod re-configure path doesn't restore from backup first).
    # Dropping them on the round-trip would orphan that mod's elements —
    # `remove_mod_xml_blocks` matches the markers as text, so without them the
    # content becomes unremovable.
    tree = load_xml(file_path, preserve_comments=True)
    total = splice_mod_files(
        tree, mod_files, cfg, names_to_remove,
        target_name=Path(file_path).name, mod_name=mod_name, log=log,
    )
    save_xml(tree, file_path)
    console.print(f"[green]Updated {file_path}[/green]")
    return total


def splice_mod_files(
    tree: ET.ElementTree,
    mod_files: list[Path],
    cfg: XmlMergeConfig,
    names_to_remove: list[str],
    *,
    target_name: str,
    mod_name: str | None = None,
    log: LogFn | None = None,
) -> int:
    """`insert_data_from_mods` on a parsed mission file, *target_name*, without
    reading or writing it. Returns elements added.

    For a caller that strips a file's old blocks in memory and splices them back
    in the same write (`xml_manual.apply_direct_files`).
    """
    _log = log or _default_log
    root = tree.getroot()
    file_element = root.find(cfg["FILE_NAME"])

    if file_element is None:
        file_element = root

    remove_elements_by_name(file_element, names_to_remove)

    total = 0
    sections_mode = cfg["MERGE_MODE"] == MODE_SECTIONS

    for mod_path in mod_files:
        mod_tree = load_xml(mod_path)
        mod_root = mod_tree.getroot()
        name = mod_name or get_mod_name(mod_path)

        if sections_mode:
            added = _merge_sections(
                file_element, mod_root, cfg["MOD_XML_CHILD_TAGS"], name,
            )
        else:
            elements = _collect_children(mod_root, cfg["MOD_XML_CHILD_TAGS"])
            file_element.append(ET.Comment(f" START MOD: {name} "))
            for elem in elements:
                file_element.append(elem)
            file_element.append(ET.Comment(f" END MOD: {name} "))
            added = len(elements)

        total += added
        if added == 0:
            # Loud on purpose: a silent no-op here is exactly how mod spawn
            # tables went missing without anything in the logs.
            _log(
                f"Warning: {name}/{mod_path.name} contributed nothing to "
                f"{target_name} (no <{'>/<'.join(cfg['MOD_XML_CHILD_TAGS'])}> "
                f"elements found under <{mod_root.tag}>)."
            )
        else:
            _log(f"Merged {added} element(s) from {name}/{mod_path.name} into {target_name}.")

    return total


def mission_target_path(server_dir: Path, mission_name: str, cfg: XmlMergeConfig) -> Path:
    """Absolute path of the mission file a merge config targets."""
    template = cfg["MISSION_FOLDER_TEMPLATE"].format(mission_folder=mission_name)
    return server_dir / template / (cfg["FILE_NAME"] + cfg["FILE_EXT"])


def merge_xml(
    server_dir: Path,
    mission_name: str,
    cfg: XmlMergeConfig,
    *,
    manual_dir: Path,
    disabled_mods: set[str] | None = None,
    log: LogFn | None = None,
) -> tuple[str, list[tuple[str, Path]]] | None:
    """Run a single XML merge.

    Returns (relative_target_path, mod_files) on success, None on failure.
    An OPTIONAL config whose mission target is simply absent is not a failure —
    it returns a result with no mod files.
    """
    _log = log or _default_log
    file_path = mission_target_path(server_dir, mission_name, cfg)
    terrain = xml_classify.terrain_from_mission(mission_name)

    mod_file_pairs = find_and_prepare_mod_files(
        server_dir, cfg, manual_dir=manual_dir, disabled_mods=disabled_mods, terrain=terrain,
        log=_log,
    )

    if not file_path.exists() and cfg.get("OPTIONAL"):
        if mod_file_pairs:
            _log(
                f"Skipped {file_path.name}: not present in this mission, so "
                f"{len(mod_file_pairs)} mod file(s) could not be merged."
            )
        return str(file_path.relative_to(server_dir)), []

    # Nothing to merge into a file that is missing or that Zedstead cannot parse
    # is not a failure: a community map's mission (Esseker, user test
    # 2026 Q4) ships files DayZ reads and ElementTree does not, and the
    # switch reported "could not apply" for files no mod writes into.
    if not mod_file_pairs and not (file_path.is_file() and validate_xml_file(file_path)):
        if file_path.exists():
            _log(f"Left {file_path.name} as it is: Zedstead cannot read it as XML, and no mod merges into it.")
        return str(file_path.relative_to(server_dir)), []

    # Create-only, then strip every mod's block by name. Restoring from the
    # .backup (what this did until 2026 Q3) also deleted every hand-placed
    # element in the file — map-editor positions, a user's own <usage>, an
    # admin's extra event — because they live outside any START/END MOD block.
    # This is the mechanism `merge_mod_direct_configs` has always used.
    ensure_backup(file_path)
    before = _read_verbatim(file_path) if file_path.is_file() else None
    strip_all_mod_blocks(file_path, log=log)

    mod_paths = [p for _, p in mod_file_pairs]
    names_to_remove = cfg.get("ELEMENTS_TO_REMOVE", [])

    if file_path.exists() and validate_xml_file(file_path):
        try:
            insert_data_from_mods(file_path, mod_paths, cfg, names_to_remove, log=log)
        except RecursionError:
            # The mod files are depth-checked above, so the mission file itself
            # is too deep to write. Put back what it held before the strip.
            if before is not None:
                atomic_write_text(file_path, before, newline="")
            _log(
                f"Error: {file_path.name} is nested too deeply to write, so nothing "
                "was merged into it. It was left as it was."
            )
            return None
        rel_target = str(file_path.relative_to(server_dir))
        return rel_target, mod_file_pairs
    else:
        why = "is missing" if not file_path.exists() else "is not valid XML"
        _log(f"Error: {file_path.name} {why}, so {len(mod_file_pairs)} mod file(s) could not be merged into it.")
        return None


def apply_failed_message(failed: list[str]) -> str:
    """The one sentence for what `merge_all_direct_configs` returns as failed.

    "Applied", not "merged": most of a mod's XML is linked from
    cfgeconomycore.xml and only some files are merged into a mission file, so
    a step named "merge" misnamed what it did (first-hour test, 2026 Q4).
    The per-file lines keep saying Linked or Merged, which is exact there.
    """
    return f"Mod XML could not be applied for: {', '.join(failed)} (target missing or invalid XML)"


@one_version_per_file()
def merge_all_direct_configs(
    server_dir: Path,
    mission_name: str,
    *,
    manual_dir: Path,
    disabled_mods: set[str] | None = None,
    log: LogFn | None = None,
) -> list[str]:
    """Run every direct (non-cfgeconomycore) merge. Returns the failed names.

    Single source for the whole pipeline: `zsm run`, the web install job, and
    the enable/disable toggles all rebuild the same set of mission files. Each
    merge strips every mod's START/END block first, so re-running it after a
    mod's enabled state changed drops that mod's blocks without any per-mod
    surgery — and leaves anything the admin wrote outside those blocks alone.
    """
    from app.xml.xml_configs import ALL_MERGE_CONFIGS
    from app.xml.xml_manual import apply_direct_files

    failed: list[str] = []
    for name, cfg in ALL_MERGE_CONFIGS.items():
        result = merge_xml(
            server_dir, mission_name, cfg,
            manual_dir=manual_dir, disabled_mods=disabled_mods, log=log,
        )
        if result is None:
            failed.append(name)

    # Manual XML files go in last, after every mod's block: the strip above
    # removes their blocks too (their `@user:<id>` marker is a mod name as far
    # as it can tell), and applying them last is what lets one override a mod
    # where DayZ takes the last definition. See app/xml/xml_manual.py.
    # Named apart from the config names above: callers put this list straight
    # into `apply_failed_message`, where a bare link id would read as a
    # mission file that doesn't exist.
    failed.extend(
        f"manual link {file_id}"
        for file_id in apply_direct_files(
            server_dir, mission_name, manual_dir=manual_dir, disabled_mods=disabled_mods, log=log,
        )
    )
    return failed


@one_version_per_file()
def merge_mod_direct_configs(
    server_dir: Path,
    mission_name: str,
    mod_name: str,
    *,
    manual_dir: Path,
    log: LogFn | None = None,
    excluded_sources: set[str] | None = None,
) -> list[str]:
    """Re-apply a single mod's direct merges. Returns the failed config names.

    The scoped counterpart to `merge_all_direct_configs`: it strips this mod's
    START/END blocks and splices its files back in, leaving every other mod's
    blocks byte-identical. That rules out the backup-restore both
    `merge_all_direct_configs` and `merge_xml` rely on (restoring would drop the
    other mods), so the strip is what keeps it idempotent, and the backup is
    created but never restored.

    The caller decides whether the mod should be merged at all — a disabled mod
    passed here would have its XML applied, so callers gate on the enabled state
    (or use `mods.apply_mod_state_to_mission_xml` for state changes).
    """
    from app.xml.xml_configs import ALL_MERGE_CONFIGS

    _log = log or _default_log
    remove_mod_xml_blocks(server_dir, mission_name, mod_name, log=_log)
    terrain = xml_classify.terrain_from_mission(mission_name)

    failed: list[str] = []
    for name, cfg in ALL_MERGE_CONFIGS.items():
        mod_file_pairs = find_and_prepare_mod_files(
            server_dir,
            cfg,
            manual_dir=manual_dir,
            mods_only={mod_name},
            terrain=terrain,
            excluded_sources=excluded_sources,
            log=_log,
        )
        if not mod_file_pairs:
            continue

        file_path = mission_target_path(server_dir, mission_name, cfg)
        if not file_path.exists():
            if cfg.get("OPTIONAL"):
                _log(
                    f"Skipped {file_path.name}: not present in this mission, so "
                    f"{len(mod_file_pairs)} mod file(s) could not be merged."
                )
            else:
                console.print(f"[red]Error: {file_path} does not exist.[/red]")
                failed.append(name)
            continue

        if not validate_xml_file(file_path):
            console.print(f"[red]Error: {file_path} is not valid XML.[/red]")
            failed.append(name)
            continue

        ensure_backup(file_path)
        insert_data_from_mods(
            file_path,
            [p for _, p in mod_file_pairs],
            cfg,
            cfg.get("ELEMENTS_TO_REMOVE", []),
            log=log,
        )

    return failed


class _Marker(NamedTuple):
    """One `<!-- START MOD: name -->` or `<!-- END MOD: name -->` comment."""

    is_start: bool
    name: str
    begin: int  # index of its "<!--"
    end: int  # index just past its "-->"
    whole: bool  # False when another "<!--" comes before its "-->"


_MARKER_WORDS = (("START MOD:", True), ("END MOD:", False))


def _mod_markers(text: str) -> list[_Marker]:
    """Every START MOD / END MOD marker in *text*, in order, in one pass.

    A marker's name is what runs from the word to the first "-->", trimmed,
    which is how the regex this replaced read it. A marker with another "<!--"
    before that "-->" is not whole: its name stops at that "<!--", and it is
    never paired, only reported.
    """
    markers: list[_Marker] = []
    size = len(text)
    close = -1
    i = text.find("<!--")
    while i >= 0:
        nxt = text.find("<!--", i + 1)
        k = i + 4
        while k < size and text[k].isspace():
            k += 1
        for word, is_start in _MARKER_WORDS:
            if not text.startswith(word, k):
                continue
            k += len(word)
            if close < k:
                close = text.find("-->", k)
            if close < 0:
                return markers  # no "-->" from here on, so no marker can close
            whole = nxt < 0 or nxt > close
            name = text[k:close if whole else nxt].strip()
            markers.append(_Marker(is_start, name, i, close + 3, whole))
            break
        i = nxt
    return markers


def _mod_block_spans(
    text: str, markers: list[_Marker], *, only: Collection[str] | None = None,
) -> list[tuple[int, int]]:
    """Where each mod block in *text* begins and ends, its leading whitespace
    included.

    A START pairs with the first END **of the same name** after it, and that
    is load-bearing: with an unterminated `START MOD: @A` in the file, pairing
    by position would take @B's END and delete everything between them. A
    START with no END of its own name pairs with nothing, so a damaged block is
    left in place — failing to strip a block is recoverable, eating half a
    mission file is not. A START inside a block that was paired goes with it.

    With *only*, just those mods' blocks are found.
    """
    ends: dict[str, tuple[list[int], list[int]]] = {}
    for m in markers:
        if not m.is_start and m.whole:
            begins_ends = ends.setdefault(m.name, ([], []))
            begins_ends[0].append(m.begin)
            begins_ends[1].append(m.end)

    spans: list[tuple[int, int]] = []
    pos = 0
    for m in markers:
        if not (m.is_start and m.whole) or m.begin < pos or m.name not in ends:
            continue
        if only is not None and m.name not in only:
            continue
        begins, block_ends = ends[m.name]
        idx = bisect_left(begins, m.end)
        if idx == len(begins):
            continue
        begin = m.begin
        while begin > pos and text[begin - 1].isspace():
            begin -= 1
        spans.append((begin, block_ends[idx]))
        pos = block_ends[idx]
    return spans


def _read_verbatim(path: Path) -> str:
    """*path*'s text with its line endings as they are, for a strip that is
    written back with ``newline=""``: a text-mode read turns CRLF into "\n",
    and the write then left a CRLF mission file LF on every line."""
    return path.read_bytes().decode("utf-8")


def strip_mod_blocks_text(
    text: str, *, only: str | Collection[str] | None = None,
) -> tuple[str, int]:
    """*text* without any mod's block (or only *only*'s, one name or several),
    and how many blocks went.

    One pass over the markers, so the time grows with the file and not with
    the number of markers that do not pair.
    """
    names = {only} if isinstance(only, str) else None if only is None else set(only)
    spans = _mod_block_spans(text, _mod_markers(text), only=names)
    if not spans:
        return text, 0
    parts: list[str] = []
    last = 0
    for begin, end in spans:
        parts.append(text[last:begin])
        last = end
    parts.append(text[last:])
    return "".join(parts), len(spans)


def strip_all_mod_blocks(file_path: Path, *, log: LogFn | None = None) -> int:
    """Remove every mod block from one file. Returns how many were removed.

    What a whole-mission merge does *instead of* restoring from `.backup`: the
    mods are about to be re-spliced so their old blocks have to go, but anything
    the admin put in the file themselves has to stay. The restore deleted both.

    Name-agnostic on purpose — a mod that has since been uninstalled or disabled
    is not in the re-splice set, so its block goes and does not come back. Manual
    XML files are stripped too (their `@user:<id>` marker is just a mod name from
    here) and `xml_manual.apply_direct_files` puts them back last.

    Any `START MOD:` left over afterwards has no matching `END MOD:`; it is
    reported through *log* rather than guessed at.
    """
    if not file_path.is_file():
        return 0

    text = _read_verbatim(file_path)
    new_text, removed = strip_mod_blocks_text(text)
    if removed:
        snapshot_before_write(file_path)
        atomic_write_text(file_path, new_text, newline="")

    orphans = list(dict.fromkeys(m.name for m in _mod_markers(new_text) if m.is_start))
    if orphans and log:
        markers = ", ".join(f'"START MOD: {name}"' for name in orphans)
        subject = (
            f"unterminated {markers} marker — that block"
            if len(orphans) == 1
            else f"unterminated markers {markers} — those blocks"
        )
        log(
            f"{file_path.name}: {subject} can no longer be updated or removed. "
            'Use "Restore from backup" on the file to reset it.'
        )
    return removed


def remove_mod_xml_blocks(
    server_dir: Path,
    mission_name: str,
    mod_name: str | Collection[str],
    *,
    log: LogFn | None = None,
    skip: Collection[Path] = (),
) -> None:
    """Strip <!-- START MOD: @Name -->...<!-- END MOD: @Name --> blocks from mission XMLs.

    *mod_name* is one name or several; a file is written once either way.
    *skip* are files the caller strips itself, in the write that splices the
    blocks back (`xml_manual.apply_direct_files`).

    Every `*.xml` in the mission and its `db/` is read, including files no
    merge writes, so one that cannot be read (a map author's Windows-1252 file,
    a directory named `*.xml`) is skipped with a line on *log* and the rest are
    still cleaned (plans/68 2P-C5), as the merge skips such a mod file.
    """
    names = {mod_name} if isinstance(mod_name, str) else set(mod_name)
    if not names:
        return
    label = ", ".join(sorted(names))
    skipped = {Path(p).resolve() for p in skip}
    _log = log or _default_log
    mission_dir = server_dir / "mpmissions" / mission_name
    # Scan both db/ and top-level mission dirs
    scan_dirs = [mission_dir / "db", mission_dir]

    for scan_dir in scan_dirs:
        if not scan_dir.is_dir():
            continue
        for xml_file in scan_dir.glob("*.xml"):
            if not xml_file.is_file():  # a directory named *.xml
                continue
            if skipped and xml_file.resolve() in skipped:
                continue
            try:
                text = _read_verbatim(xml_file)
            except (OSError, UnicodeDecodeError):
                name = xml_file.relative_to(mission_dir).as_posix()
                _log(f"Skipped {name} while removing {label}: not a UTF-8 text file.")
                continue
            new_text, _removed = strip_mod_blocks_text(text, only=names)
            if new_text != text:
                snapshot_before_write(xml_file)
                atomic_write_text(xml_file, new_text, newline="")
                console.print(f"[green]Cleaned {label} blocks from {xml_file.name}[/green]")
