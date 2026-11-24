"""Economy core XML handler — adapted from config_cfgeconomycore.py.

Copies mod XML files of the central-economy kinds (`xml_classify.CE_TYPES`)
into the mission's custom/ folder with kebab-case names, then registers them in
cfgeconomycore.xml via `<ce folder="custom">` `<file type=...>` entries.

Two rules the rest of the module rests on:

* the mod's file decides its `type=` by **content**, never by filename — see
  `ce_bucket`;
* only `AUTO_LINK_KINDS` is linked off a mod dir. `messages` is a valid CE type
  and fully supported for manual XML files, but a mod's copy is never linked
  automatically.
"""

from __future__ import annotations

import glob
import logging
import re
import shutil
from pathlib import Path
from typing import Callable, NamedTuple
from xml.dom import minidom

from defusedxml.minidom import parse as _safe_minidom_parse

from rich.console import Console

from app.modmgr.mods import EXCLUDED_MOD_KEYWORDS, kebab_case
from app.shared.json_utils import atomic_write_text, copy_file
from app.shared.zip_utils import is_bare_name
from app.xml.xml_classify import (
    AUTO_LINK_KINDS,
    CE_TYPE_ORDER,
    KIND_MESSAGES,
    ce_root_tag,
    classify_mod_file,
    terrain_from_mission,
)
from app.xml.xml_configs import CE_FILE_CONFIGS
from app.xml.xml_merge import ensure_backup, iter_mod_xml_files
from app.xml.xml_text import check_xml_text, format_xml_text, mend_comment_dashes, strip_declaration
from app.xml.xml_utils import validate_xml_file, write_keeping_line_endings

console = Console()
_log = logging.getLogger(__name__)


def ce_entry_order(entry: tuple) -> tuple:
    """Sort key of one (file name, CE type) registration in the custom block.

    By type first (`CE_TYPE_ORDER`), then the manual files after every mod's,
    since DayZ applies the last definition and a manual file must win."""
    from app.xml.xml_manual import CUSTOM_FILE_PREFIX

    name, ce_type = entry[0], entry[1]
    return CE_TYPE_ORDER.get(ce_type, 9), name.startswith(CUSTOM_FILE_PREFIX), name


def _wrap_with_root_if_needed(source_file: Path, root_tag: str) -> None:
    """Give *source_file* a ``<root_tag>`` root when it hasn't got one.

    Mods ship rootless fragments as often as whole files, and only a rooted
    document is loadable. The file's own ``<?xml …?>`` is dropped first: an XML
    document may carry exactly one declaration, at the very start, so leaving it
    in place put a second one *inside* the wrapper — the copy then failed
    `validate_xml_file` and was silently discarded by the caller. That accident
    was, until 2026 Q3, the only thing stopping unrecognised mod XML from
    being registered as ``type="types"``.

    Known wart: the "has a root already" test is a substring check, so a file
    whose root carries attributes (``<types version="1">``) is wrapped again.
    Left alone — no mod in the wild writes one, and the fix is a parse per file.
    """
    content = source_file.read_text(encoding="utf-8")
    if f"<{root_tag}>" in content:
        return
    body = strip_declaration(content).strip()
    atomic_write_text(
        source_file,
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f"<{root_tag}>\n{body}\n</{root_tag}>",
    )


def _mend_copy(dest_path: Path) -> int:
    """Make the comments in a copied mod file legal XML; how many changed
    (`mend_comment_dashes`). The mod's own file is never touched."""
    text = dest_path.read_text(encoding="utf-8-sig")
    mended, changed = mend_comment_dashes(text)
    if changed:
        atomic_write_text(dest_path, mended)
    return changed


def _clear_custom_folder(mission_dir: Path) -> Path:
    custom_folder = mission_dir / "custom"
    if custom_folder.exists():
        shutil.rmtree(custom_folder)
    custom_folder.mkdir()
    return custom_folder


def _filename_bucket(xml_file: Path) -> tuple[str, str] | None:
    """Legacy filename-based bucketing — the fallback for files with no single
    root element (bare fragments, which only become valid XML once wrapped).
    Returns (ce_type, root_tag).

    Reached from `ce_bucket` **only** in that case. It covers three kinds, not
    six: see the note on `CE_FILE_CONFIGS`.
    """
    name = xml_file.name.lower()
    for ce_cfg in CE_FILE_CONFIGS:
        if ce_cfg["filename_contains"] and ce_cfg["filename_contains"] not in name:
            continue
        if any(ep.lower() in name for ep in ce_cfg["excluded_file_parts"]):
            continue
        return ce_cfg["ce_type"], ce_cfg["root_tag"]
    return None


def ce_bucket(
    xml_file: Path,
    *,
    log: Callable[[str], None] | None = None,
    label: str = "",
    not_ce: list[str] | None = None,
) -> tuple[str, str] | None:
    """`(ce_type, root_tag)` for a mod XML, or None to skip it.

    Content wins: a file is linked as what it *is*, not what it's named, so an
    `<events>` payload in `MyMod.xml` is still linked as events and a
    `<lists>`/`<eventposdef>` file is never mislinked as types.

    Public because `xml_report` calls the identical predicate — a second,
    lookalike copy is how the page came to report files as "Ignored" that the
    merge was linking (and vice versa).

    The filename fallback is reached **only** for a well-formed fragment with no
    root element. A file that parses with a root the central economy doesn't
    know is not a CE file, and running it through the `types` catch-all is what
    registered mods' private XML as types.

    `log` gets the two *actionable*, rare skips (a messages file, unparseable
    XML). The ordinary "parses fine, just isn't a CE file" case appends its
    label to `not_ce` instead — a mod dir is rglobbed whole, so a modpack has
    hundreds of those and one line each would bury the job log; the caller
    reports a count.
    """
    kind, root_tag = classify_mod_file(xml_file)
    if kind in AUTO_LINK_KINDS:
        return kind, ce_root_tag(kind)
    if kind == KIND_MESSAGES:
        # Understood, but never linked off a mod dir: mods ship example mission
        # files as documentation, and auto-linking one would broadcast its
        # sample messages to real players. The admin opts in with "Set as
        # manual" on the Mod XML page.
        if log:
            log(
                f"Recognised {label} as messages — not linked automatically. "
                "Use 'Set as manual' on the Custom config tab to apply it."
            )
        return None
    if kind is not None:
        return None  # recognised, but merged directly rather than CE-linked
    if root_tag is not None:
        if not_ce is not None:
            not_ce.append(f"{label} (<{root_tag}>)")
        return None
    try:
        text = xml_file.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        # Not UTF-8, or not a file: one mod's file must not stop the merge.
        if log:
            log(f"Skipped {label}: not a UTF-8 text file.")
        return None
    problem = check_xml_text(text, fragment=True)
    if problem is not None and check_xml_text(mend_comment_dashes(text)[0], fragment=True) is not None:
        if log:
            where = f" at line {problem.line}" if problem.line else ""
            log(f"Skipped {label}: not valid XML{where} ({problem.hint or problem.message}).")
        return None
    # A file broken only by "--" in a comment is linked; the copy is mended
    # (`_mend_copy`).
    return _filename_bucket(xml_file)


def custom_file_name(mod_name: str, file_name: str) -> str:
    """The ``custom/`` name a mod's XML file is copied to:
    ``kebab_case(mod)-<the file's own name>``, with a run of dashes in the
    file's name made one. A disabled mod's names go into a comment in
    cfgeconomycore.xml, where "--" is invalid XML. Every reader that maps a
    custom/ name back to its mod file uses this (`xml_manual.mod_file_for_custom`,
    `xml_report`)."""
    return f"{kebab_case(mod_name)}-{re.sub(r'-{2,}', '-', file_name)}"


def find_and_copy_mods_xml(
    server_dir: Path,
    custom_folder: Path,
    *,
    manual_dir: Path,
    disabled_mods: set[str] | None = None,
    mods_only: set[str] | None = None,
    terrain: str | None = None,
    log: Callable[[str], None] | None = None,
) -> list[tuple[str, str, str]]:
    """Find mod XMLs, copy to custom/, return (dest_filename, ce_type, mod_name) triples.

    `mods_only`, if given, restricts iteration to the named mods (e.g.
    `{"@TraderPlus"}`) — used by per-mod reset paths that share this machinery.
    `terrain` drops other maps' variants of map-specific files.

    One residual gap, deliberate: a **rootless** globals/economy/messages
    fragment still falls into `_filename_bucket`'s catch-all and is linked as
    types. There is no content to key on, and filename buckets for those kinds
    would be worse than the gap (`cfgeconomycore.xml` contains "economy"). Use a
    manual XML file, where the kind is chosen explicitly.
    """
    from app.xml.xml_manual import load_exclusions, load_source_exclusions, normalize_source

    results: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    not_ce: list[str] = []
    # User-disabled generated files. Enforced here rather than at registration
    # time because custom/ is rebuilt from scratch every merge: deleting the
    # file is undone on the next run, skipping the copy is not.
    excluded = load_exclusions(manual_dir=manual_dir)
    # The same switch keyed by the *mod's* file instead of the generated one —
    # the only form available for a file that was never registered (so has no
    # tile), and the one the Mod XML table's Disable writes.
    excluded_sources = load_source_exclusions(manual_dir=manual_dir)

    for mod_name, xml_file in iter_mod_xml_files(
        server_dir,
        EXCLUDED_MOD_KEYWORDS,
        [],
        disabled_mods=disabled_mods,
        mods_only=mods_only,
        terrain=terrain,
    ):
        label = f"{mod_name}/{xml_file.name}"
        bucket = ce_bucket(xml_file, log=log, label=label, not_ce=not_ce)
        if bucket is None:
            continue
        ce_type, root_tag = bucket

        if normalize_source(xml_file.relative_to(server_dir)) in excluded_sources:
            if log:
                log(f"Skipped {label}: disabled by you.")
            continue

        dest_name = custom_file_name(mod_name, xml_file.name)
        if dest_name in seen:
            continue
        seen.add(dest_name)

        if dest_name in excluded:
            if log:
                log(f"Skipped {label}: {dest_name} is disabled.")
            continue

        dest_path = custom_folder / dest_name
        copy_file(xml_file, dest_path)
        mended = _mend_copy(dest_path)
        _wrap_with_root_if_needed(dest_path, root_tag)

        if validate_xml_file(dest_path):
            results.append((dest_name, ce_type, mod_name))
            if log:
                note = (
                    f" Rewrote {mended} comment(s) with '--' inside, which XML does not allow."
                    if mended else ""
                )
                log(f"Linked {label} as {ce_type} ({dest_name}).{note}")
        else:
            # Was a silent unlink. A file that survived classification but not
            # the copy is exactly the one an admin needs told about.
            dest_path.unlink()
            if log:
                log(f"Skipped {label}: the wrapped copy isn't valid XML.")

    if not_ce and log:
        log(
            f"Skipped {len(not_ce)} mod XML file(s) that aren't central-economy files: "
            + ", ".join(not_ce[:10])
            + (f" and {len(not_ce) - 10} more" if len(not_ce) > 10 else "")
        )

    results.sort(key=ce_entry_order)
    return results


def _custom_ce_elements(economycore) -> list:
    """Every `<ce folder="custom">` element under *economycore*.

    Plural on purpose: a file written by an older zsm (or hand-edited) holds
    one `<ce>` per registered file, and every path here has to read those and
    collapse them into the single grouped element `_rebuild_custom_ce` writes.
    """
    return [
        ce for ce in economycore.getElementsByTagName("ce")
        if ce.getAttribute("folder") == "custom"
    ]


def _rebuild_custom_ce(dom, economycore, entries: list[tuple[str, str]]) -> None:
    """Replace every `<ce folder="custom">` with ONE holding *entries*.

    DayZ loads a file with a `<ce>` per registered XML fine, but it makes
    cfgeconomycore.xml unreadable once a few mods are installed — every mod
    contributes 3 near-identical five-line blocks. `<file>` children of one
    rootclass are equivalent, so they are grouped.

    `<!-- DISABLED @Mod: ... -->` wrappers are comment nodes, not elements, so
    they are invisible here and survive the rewrite untouched.

    The rebuilt block goes back **where the old one was**, and is only appended
    when there wasn't one. DayZ applies the last definition of a classname, so
    appending unconditionally quietly demoted an admin's own
    `<ce folder="myoverrides">` below every mod's files on each merge — the
    documented "one folder overrides vanilla, another appends mod files" layout
    stopped working with no visible change to the file's contents.
    """
    existing = _custom_ce_elements(economycore)
    # The node the block sat in front of. Walk past siblings that are themselves
    # about to be removed: a legacy file holds one <ce> per registered file, so
    # the next sibling is usually another custom block.
    removing = {id(node) for node in existing}
    anchor = existing[0].nextSibling if existing else None
    while anchor is not None and id(anchor) in removing:
        anchor = anchor.nextSibling
    for ce in existing:
        economycore.removeChild(ce)
    if not entries:
        return
    ce = dom.createElement("ce")
    ce.setAttribute("folder", "custom")
    for filename, ce_type in entries:
        file_elem = dom.createElement("file")
        file_elem.setAttribute("name", filename)
        file_elem.setAttribute("type", ce_type)
        ce.appendChild(file_elem)
    if anchor is not None:
        economycore.insertBefore(ce, anchor)
    else:
        economycore.appendChild(ce)


def _save_economycore_dom(xml_path: Path, dom) -> None:
    """Serialize *dom* re-indented, falling back to the raw text.

    minidom keeps the whitespace text nodes of whatever it parsed, so removing
    an element leaves a hole and appending one puts it flush against the last
    tag; `format_xml_text` re-indents the whole file and keeps the DISABLED
    comments. The file keeps its line endings (`write_keeping_line_endings`).

    `encoding="UTF-8"` is passed for the declaration alone — a bare
    `dom.toxml()` writes `<?xml version="1.0" ?>`, dropping the encoding off
    every mission file it touches.
    """
    text = dom.toxml(encoding="UTF-8").decode("utf-8")
    result = format_xml_text(text)
    write_keeping_line_endings(xml_path, result.text if result.ok else text)


def _update_cfgeconomycore(
    mission_dir: Path,
    mod_xml_files: list[tuple[str, str, str]],
    log: Callable[[str], None] | None = None,
) -> None:
    xml_path = mission_dir / "cfgeconomycore.xml"
    if xml_path.exists():
        dom = _safe_minidom_parse(str(xml_path))
    else:
        dom = minidom.Document()
        dom.appendChild(dom.createElement("economycore"))

    # Every sibling function here guards this; this one used to index blindly
    # and take the whole merge job down with an IndexError on a file that parses
    # but has some other root. The file is left exactly as the admin wrote it —
    # replacing their root would be worse than reporting the problem.
    roots = dom.getElementsByTagName("economycore")
    if not roots:
        message = (
            "cfgeconomycore.xml has no <economycore> root — no custom/ files were "
            "registered. Fix the file (or restore its .backup) and merge again."
        )
        if log:
            log(message)
        console.print(f"[red]{message}[/red]")
        return
    economycore = roots[0]
    _rebuild_custom_ce(
        dom, economycore, [(name, ce_type) for name, ce_type, _mod in mod_xml_files],
    )

    _save_economycore_dom(xml_path, dom)
    console.print(f"[green]Updated cfgeconomycore.xml[/green]")


def _existing_custom_entries(mission_dir: Path) -> list[tuple[str, str, str]]:
    """Read the current `<ce folder="custom">` registrations back off disk.

    Returns the same (dest_filename, ce_type, mod_name) shape
    `find_and_copy_mods_xml` produces, with an empty mod name (unused by
    `_update_cfgeconomycore`). Entries whose custom/ file no longer exists are
    dropped, so a stale registration isn't carried forward. Commented-out
    `<!-- DISABLED @Mod: ... -->` blocks are comment nodes, not elements, so
    they're invisible here — and survive a rewrite untouched.
    """
    xml_path = mission_dir / "cfgeconomycore.xml"
    if not xml_path.exists():
        return []

    custom_folder = mission_dir / "custom"
    dom = _safe_minidom_parse(str(xml_path))
    entries: list[tuple[str, str, str]] = []
    for ce in dom.getElementsByTagName("ce"):
        if ce.getAttribute("folder") != "custom":
            continue
        for file_elem in ce.getElementsByTagName("file"):
            name = file_elem.getAttribute("name")
            if name and (custom_folder / name).exists():
                entries.append((name, file_elem.getAttribute("type"), ""))
    return entries


def ce_file_path(mission_dir: Path, folder: str, name: str) -> Path | None:
    """``mission_dir / folder / name`` for a cfgeconomycore.xml registration,
    or None when that would leave the mission folder.

    Both values come from a file a map author or a mod can ship (plans/68
    CON-8). Every part between ``/`` or ``\\`` has to be a plain name
    (`zip_utils.is_bare_name`), so ``..``, an absolute path and a drive are
    refused; a nested folder (``a/b``) and an empty or ``.`` part are kept.
    The check is on the text, so a symlink the admin made inside the mission
    still works.
    """
    if folder[:1] in ("/", "\\") or name[:1] in ("/", "\\"):
        return None
    parts = [p for p in re.split(r"[/\\]", f"{folder}/{name}") if p not in ("", ".")]
    if not parts or not all(is_bare_name(p) for p in parts):
        return None
    return mission_dir.joinpath(*parts)


_REPORTED_OUTSIDE: set[tuple[str, str, str]] = set()


def log_outside_mission(mission_dir: Path, folder: str, name: str) -> None:
    """Say, once per process, that a registration leaves the mission and is skipped."""
    key = (str(mission_dir), folder, name)
    if key in _REPORTED_OUTSIDE:
        return
    _REPORTED_OUTSIDE.add(key)
    _log.warning(
        "%s/cfgeconomycore.xml registers folder=%r name=%r, which is outside the "
        "mission folder; Zedstead does not read it",
        mission_dir.name, folder, name,
    )


def registered_custom_entries(mission_dir: Path) -> list[tuple[str, str]]:
    """(filename, ce_type) for every live `<ce folder="custom">` registration.

    The public reader over `_existing_custom_entries` — for callers that want to
    know what the mission's economy actually loads out of custom/ (the map
    editor asks, so it can tell a mod-defined event from an unknown one). A
    name that would leave the mission folder (`ce_file_path`) is left out with
    a log line, since its callers read, and the spawn test writes, the file.
    """
    out: list[tuple[str, str]] = []
    for name, ce_type, _mod in _existing_custom_entries(mission_dir):
        if ce_file_path(mission_dir, "custom", name) is None:
            log_outside_mission(mission_dir, "custom", name)
            continue
        out.append((name, ce_type))
    return out


def merge_mod_economy_entries(
    server_dir: Path,
    mission_name: str,
    mod_name: str,
    *,
    manual_dir: Path,
    log: Callable[[str], None] | None = None,
) -> list[tuple[str, str, str]]:
    """Re-link a single mod's CE XMLs, leaving every other mod's alone.

    The scoped counterpart to `merge_economy_core`, which wipes the whole
    custom/ folder and rewrites every registration. Here only this mod's
    custom/ files and `<ce>` entries are dropped and rebuilt; the other mods'
    entries are read back off disk and re-registered alongside the new ones so
    the global types-first ordering still holds.

    Returns the (dest_filename, ce_type, mod_name) triples for this mod.
    """
    mission_dir = server_dir / "mpmissions" / mission_name

    # Backup only — restoring would drop every other mod's registration.
    ensure_backup(mission_dir / "cfgeconomycore.xml")

    remove_mod_economy_entries(server_dir, mission_name, mod_name)
    remove_disabled_economy_entries(server_dir, mission_name, mod_name)

    custom_folder = mission_dir / "custom"
    custom_folder.mkdir(parents=True, exist_ok=True)
    copied_files = find_and_copy_mods_xml(
        server_dir,
        custom_folder,
        manual_dir=manual_dir,
        mods_only={mod_name},
        terrain=terrain_from_mission(mission_name),
        log=log,
    )

    # Read *after* the removals, so this is exactly the other mods' entries.
    kept = _existing_custom_entries(mission_dir)
    merged = sorted(
        kept + copied_files, key=ce_entry_order,
    )
    _update_cfgeconomycore(mission_dir, merged, log)
    return copied_files


def _mod_custom_names(server_dir: Path, mod_name: str) -> Callable[[str], bool]:
    """Whether a custom/ file name is *mod_name*'s: it starts with the mod's
    ``kebab_case`` prefix and not with the longer prefix of another installed
    mod, as `xml_manual.owning_mod` decides (``@Trader``'s ``trader-`` also
    starts ``trader-plus-types.xml``, which is ``@Trader_Plus``'s)."""
    prefix = kebab_case(mod_name) + "-"
    longer = [
        other for other in (
            kebab_case(d.name) + "-" for d in server_dir.iterdir()
            if d.is_dir() and d.name.startswith("@") and d.name != mod_name
        )
        if len(other) > len(prefix) and other.startswith(prefix)
    ] if server_dir.is_dir() else []
    return lambda name: name.startswith(prefix) and not any(name.startswith(o) for o in longer)


def remove_mod_economy_entries(server_dir: Path, mission_name: str, mod_name: str) -> None:
    """Remove a mod's custom XML files and cfgeconomycore.xml <ce> entries."""
    mission_dir = server_dir / "mpmissions" / mission_name
    custom_folder = mission_dir / "custom"
    prefix = kebab_case(mod_name) + "-"
    is_mods = _mod_custom_names(server_dir, mod_name)

    # Delete custom/{kebab-prefix}-*.xml files
    if custom_folder.is_dir():
        for xml_file in custom_folder.glob(f"{glob.escape(prefix)}*.xml"):
            if not is_mods(xml_file.name):
                continue
            xml_file.unlink()
            console.print(f"[green]Removed {xml_file.name}[/green]")

    # Remove matching <ce> entries from cfgeconomycore.xml
    xml_path = mission_dir / "cfgeconomycore.xml"
    if not xml_path.exists():
        return

    dom = _safe_minidom_parse(str(xml_path))
    economycore = dom.getElementsByTagName("economycore")
    if not economycore:
        return

    root = economycore[0]
    for ce in _custom_ce_elements(root):
        # Per <file>, not per <ce>: the entries are grouped into one rootclass,
        # so dropping the element would take every other mod's with it.
        for file_elem in list(ce.getElementsByTagName("file")):
            name = file_elem.getAttribute("name")
            if is_mods(name):
                ce.removeChild(file_elem)
                console.print(f"[green]Removed economy entry for {name}[/green]")
        if not ce.getElementsByTagName("file"):
            root.removeChild(ce)

    _save_economycore_dom(xml_path, dom)


def unregister_custom_file(mission_dir: Path, custom_name: str) -> bool:
    """Drop one `<ce folder="custom">` registration by filename.

    The by-filename counterpart to `remove_mod_economy_entries` (which works by
    mod prefix) — used when deleting a manual XML link, whose custom/ file is
    named after the link rather than a mod.
    """
    xml_path = mission_dir / "cfgeconomycore.xml"
    if not xml_path.exists():
        return False

    dom = _safe_minidom_parse(str(xml_path))
    economycore = dom.getElementsByTagName("economycore")
    if not economycore:
        return False

    root = economycore[0]
    removed = False
    for ce in _custom_ce_elements(root):
        for file_elem in list(ce.getElementsByTagName("file")):
            if file_elem.getAttribute("name") == custom_name:
                ce.removeChild(file_elem)
                removed = True
        if not ce.getElementsByTagName("file"):
            root.removeChild(ce)

    if removed:
        _save_economycore_dom(xml_path, dom)
    return removed


def _live_custom_entries(economycore) -> list[tuple[str, str]]:
    """(filename, type) for every registered — i.e. not commented out — file."""
    return [
        (f.getAttribute("name"), f.getAttribute("type"))
        for ce in _custom_ce_elements(economycore)
        for f in ce.getElementsByTagName("file")
    ]


def register_custom_file(mission_dir: Path, custom_name: str, ce_type: str) -> bool:
    """Add one `<ce folder="custom">` `<file>` registration. True if it changed.

    The inverse of `unregister_custom_file`, and the only scoped way to put a
    single generated file into cfgeconomycore.xml — `merge_economy_core` rewrites
    the whole block and `merge_mod_economy_entries` is keyed on a mod, so neither
    can register one manual XML file on its own. Used when a file is taken over
    from a mod ("Set as manual"), where the mission must keep serving the same
    content across the swap.

    Goes through `_rebuild_custom_ce` like every other writer, so the block stays
    grouped, keeps its position, and stays in `CE_TYPE_ORDER`. Re-registering an
    existing name updates its type rather than duplicating it.
    """
    xml_path = mission_dir / "cfgeconomycore.xml"
    if not xml_path.exists():
        return False

    dom = _safe_minidom_parse(str(xml_path))
    roots = dom.getElementsByTagName("economycore")
    if not roots:
        return False
    root = roots[0]

    entries = [e for e in _live_custom_entries(root) if e[0] != custom_name]
    entries.append((custom_name, ce_type))
    entries.sort(key=ce_entry_order)
    if entries == _live_custom_entries(root):
        return False

    _rebuild_custom_ce(dom, root, entries)
    _save_economycore_dom(xml_path, dom)
    return True


# The `<file>` inside a `<!-- DISABLED @Mod: ... -->` wrapper.
_DISABLED_FILE_RE = re.compile(r'<file\s+name="([^"]+)"\s+type="([^"]+)"')

# What follows the mod name in a wrapper: `: <ce folder="custom"`.
_CUSTOM_CE_OPEN_RE = re.compile(r'\s*<ce\s+folder="custom"')


class _Wrapper(NamedTuple):
    """One `<!-- DISABLED @Mod: <ce folder="custom">…</ce> -->` comment."""

    mod: str
    start: int  # index of its "<!--"
    end: int  # index just past its "-->"


def _disabled_wrappers(text: str) -> list[_Wrapper]:
    """Every DISABLED wrapper in *text*, in order, in one pass.

    A wrapper is a comment whose text is ``DISABLED``, whitespace, the mod
    name, then ``:`` and ``<ce folder="custom"``, and which ends with
    ``</ce>`` and whitespace before its ``-->``. The mod name runs to the first
    such ``:`` (so a name with a space or a ``:`` reads whole), trimmed, and
    the comment ends at its first ``-->``, as XML says.

    Openers are found with str.find, and the next ``-->`` and the next ``:``
    before a custom ``<ce`` are reused until a later opener passes them, so an
    opener with no close costs nothing extra. The two regexes this replaced
    re-read the rest of the file from every such opener, and their time grew
    with the square or the cube of the number of openers (plans/68, the CON-7
    follow-up). `remove_disabled_economy_entries` reads through this too, so
    the mods it removes are the mods `disabled_wrapper_mods` lists.
    """
    wrappers: list[_Wrapper] = []
    size = len(text)
    close = tail = -1  # the next "-->", and where the whitespace before it starts
    colon_from, colon, ce_end = 0, -1, -1  # first ": <ce folder=\"custom\"" at or after colon_from
    i = text.find("<!--")
    while i >= 0:
        if close < i + 4:
            close = text.find("-->", i + 4)
            if close < 0:
                break  # no "-->" from here on, so no comment can close
            tail = close
            while tail > i + 4 and text[tail - 1].isspace():
                tail -= 1
        k = i + 4
        while k < close and text[k].isspace():
            k += 1
        if text.startswith("DISABLED", k, close):
            name_at = k + 8
            while name_at < close and text[name_at].isspace():
                name_at += 1
            if name_at > k + 8 and name_at < close:
                q = name_at + 1
                if not colon_from <= q <= colon:
                    colon_from, colon = q, size
                    c = text.find(":", q)
                    while c >= 0:
                        m = _CUSTOM_CE_OPEN_RE.match(text, c + 1)
                        if m:
                            colon, ce_end = c, m.end()
                            break
                        c = text.find(":", c + 1)
                if colon < close and ce_end <= tail - 5 and text.startswith("</ce>", tail - 5):
                    wrappers.append(_Wrapper(text[name_at:colon].rstrip(), i, close + 3))
                    i = text.find("<!--", close + 3)
                    continue
        i = text.find("<!--", i + 4)
    return wrappers


def disabled_wrapper_mods(mission_dir: Path) -> list[str]:
    """Mods with parked `<!-- DISABLED @Mod: ... -->` entries, first seen first."""
    xml_path = mission_dir / "cfgeconomycore.xml"
    if not xml_path.is_file():
        return []
    text = xml_path.read_text(encoding="utf-8")
    return list(dict.fromkeys(w.mod for w in _disabled_wrappers(text)))


def disable_mod_economy_entries(
    server_dir: Path, mission_name: str, mod_name: str,
) -> int:
    """Park a mod's custom/ registrations in <!-- DISABLED @Mod: ... --> comments.

    The registrations themselves are `<file>` children of the single grouped
    `<ce folder="custom">`, but the parked copy keeps the standalone-`<ce>`
    payload the wrapper has always carried — `enable_mod_economy_entries`,
    `remove_disabled_economy_entries` and `xml_report._CE_ENTRY_RE` all match
    that shape, and a file written by an older zsm still re-enables.

    Returns the number of entries commented out.
    """
    mission_dir = server_dir / "mpmissions" / mission_name
    xml_path = mission_dir / "cfgeconomycore.xml"
    if not xml_path.exists():
        return 0

    is_mods = _mod_custom_names(server_dir, mod_name)
    dom = _safe_minidom_parse(str(xml_path))
    economycore = dom.getElementsByTagName("economycore")
    if not economycore:
        return 0
    root = economycore[0]

    entries = _live_custom_entries(root)
    parked = [e for e in entries if is_mods(e[0])]
    if not parked:
        # Idempotent: an already-disabled mod has no live entry left. Its
        # wrappers are comments, so there is nothing here to re-wrap — nesting
        # <!-- ... --> would corrupt the file (comments can't contain "--").
        return 0

    _rebuild_custom_ce(dom, root, [e for e in entries if not is_mods(e[0])])
    for name, ce_type in parked:
        root.appendChild(dom.createComment(
            f' DISABLED {mod_name}: <ce folder="custom">'
            f'<file name="{name}" type="{ce_type}"/></ce> '
        ))

    _save_economycore_dom(xml_path, dom)
    return len(parked)


def enable_mod_economy_entries(
    server_dir: Path, mission_name: str, mod_name: str,
) -> int:
    """Restore a mod's disabled entries into the grouped <ce folder="custom">.

    Returns the number of entries re-enabled.
    """
    mission_dir = server_dir / "mpmissions" / mission_name
    xml_path = mission_dir / "cfgeconomycore.xml"
    if not xml_path.exists():
        return 0

    dom = _safe_minidom_parse(str(xml_path))
    economycore = dom.getElementsByTagName("economycore")
    if not economycore:
        return 0
    root = economycore[0]

    wrapper = re.compile(r'^\s*DISABLED\s+' + re.escape(mod_name) + r'\s*:\s*', re.DOTALL)
    restored: list[tuple[str, str]] = []
    for node in list(root.childNodes):
        if node.nodeType != node.COMMENT_NODE:
            continue
        match = wrapper.match(node.data)
        if not match:
            continue
        found = _DISABLED_FILE_RE.findall(node.data[match.end():])
        if not found:
            continue
        restored.extend(found)
        root.removeChild(node)

    if not restored:
        return 0

    merged = _live_custom_entries(root) + restored
    merged.sort(key=ce_entry_order)
    _rebuild_custom_ce(dom, root, merged)

    _save_economycore_dom(xml_path, dom)
    return len(restored)


def remove_disabled_economy_entries(
    server_dir: Path, mission_name: str, mod_name: str,
) -> int:
    """Remove commented-out (disabled) CE entries for a mod.

    Returns the number of entries removed.
    """
    mission_dir = server_dir / "mpmissions" / mission_name
    xml_path = mission_dir / "cfgeconomycore.xml"
    if not xml_path.exists():
        return 0
    return sum(_remove_wrappers(xml_path, lambda mod: mod == mod_name).values())


def _remove_wrappers(xml_path: Path, drop: Callable[[str], bool]) -> dict[str, int]:
    """Remove every wrapper whose mod *drop* accepts, in one read and one write.

    Returns how many went per mod, first seen first. Each wrapper goes with the
    whitespace right before it, as the regex this replaced removed it
    (`_disabled_wrappers` says why it is not a regex).
    """
    text = xml_path.read_text(encoding="utf-8")
    parts: list[str] = []
    pos = 0
    removed: dict[str, int] = {}
    for wrapper in _disabled_wrappers(text):
        if not drop(wrapper.mod):
            continue
        start = wrapper.start
        while start > pos and text[start - 1].isspace():
            start -= 1
        parts.append(text[pos:start])
        pos = wrapper.end
        removed[wrapper.mod] = removed.get(wrapper.mod, 0) + 1
    if removed:
        parts.append(text[pos:])
        write_keeping_line_endings(xml_path, "".join(parts))
    return removed


def _prune_stale_disabled_entries(
    server_dir: Path,
    mission_name: str,
    disabled_mods: set[str],
    log: Callable[[str], None] | None = None,
) -> int:
    """Drop DISABLED wrappers whose mod isn't disabled any more. Returns the count.

    A wrapper is only meaningful while its mod is off — it is where that mod's
    `<ce>` registrations wait for `enable_mod_economy_entries` to unwrap them.
    Once the mod is enabled again (or uninstalled while disabled, which drops it
    from `disabled_mods` too) the wrapper is dead weight that nothing will ever
    read, and a later disable would park a *second* copy beside it.

    The full merge's `.backup` restore used to clear these as a side effect;
    since `merge_economy_core` stopped restoring (2026 Q3) this is what keeps
    them from accumulating. Wrappers are comment nodes, so `_rebuild_custom_ce`
    cannot see them.

    One read, one scan and one write for every stale mod together: a file
    holding thousands of distinct stale names took one of each per name, and
    the merge waited for all of them (plans/68 2P-C2).
    """
    xml_path = server_dir / "mpmissions" / mission_name / "cfgeconomycore.xml"
    if not xml_path.is_file():
        return 0
    removed = _remove_wrappers(xml_path, lambda mod: mod not in disabled_mods)
    if log:
        for mod_name, count in removed.items():
            log(
                f"{mod_name} is no longer disabled — removed {count} parked "
                f"cfgeconomycore entr{'y' if count == 1 else 'ies'}."
            )
    return sum(removed.values())


def _restore_legacy_backups(mission_dir: Path) -> None:
    """One-time migration: restore events.xml / cfgspawnabletypes.xml from
    backups left by the old direct-merge approach, then remove the backups."""
    legacy_files = [
        mission_dir / "db" / "events.xml",
        mission_dir / "cfgspawnabletypes.xml",
    ]
    for path in legacy_files:
        backup = path.with_suffix(path.suffix + ".backup")
        if backup.exists():
            copy_file(backup, path)
            backup.unlink()
            console.print(f"[dim]Restored {path.name} from legacy merge backup.[/dim]")


def merge_economy_core(
    server_dir: Path,
    mission_name: str,
    *,
    manual_dir: Path,
    disabled_mods: set[str] | None = None,
    log: Callable[[str], None] | None = None,
) -> list[tuple[str, str, str]]:
    """Run the full cfgeconomycore merge pipeline.

    Returns list of (dest_filename, ce_type, mod_name) for each linked file.

    The file is backed up but **never restored** (2026 Q3): `_rebuild_custom_ce`
    already replaces the whole `<ce folder="custom">` block in place, so a
    restore added nothing except deleting the admin's own rootclasses,
    `<defaults>` edits and comments. `disabled_mods` is what tells the parked
    `<!-- DISABLED @Mod: ... -->` wrappers apart from stale ones — pass it, or
    the pruning is skipped rather than guessed at.
    """
    mission_dir = server_dir / "mpmissions" / mission_name

    from app.xml.xml_manual import copy_manual_ce_files

    _restore_legacy_backups(mission_dir)
    ensure_backup(mission_dir / "cfgeconomycore.xml")
    if disabled_mods is not None:
        _prune_stale_disabled_entries(server_dir, mission_name, disabled_mods, log)
    custom_folder = _clear_custom_folder(mission_dir)
    terrain = terrain_from_mission(mission_name)
    copied_files = find_and_copy_mods_xml(
        server_dir,
        custom_folder,
        manual_dir=manual_dir,
        disabled_mods=disabled_mods,
        terrain=terrain,
        log=log,
    )
    # The custom/ wipe above takes the manual links' copies with it, so they are
    # re-copied here and registered in the same rewrite as the mods' files —
    # that keeps CE_TYPE_ORDER's types-first ordering across both sources.
    copied_files.extend(
        copy_manual_ce_files(
            custom_folder, manual_dir=manual_dir, terrain=terrain,
            disabled_mods=disabled_mods, log=log,
        )
    )
    copied_files.sort(key=ce_entry_order)
    _update_cfgeconomycore(mission_dir, copied_files, log)

    if copied_files:
        console.print(f"[dim]Linked {len(copied_files)} mod XML file(s) to cfgeconomycore.[/dim]")

    return copied_files
