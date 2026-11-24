"""Shared XML/config file utilities — backup, restore, load, save, mission dir."""

from __future__ import annotations

import re
import shutil
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from app.shared.json_utils import atomic_write_text, copy_file
from app.xml.xml_text import parse_xml_text


# The deepest tree a merge will write. `save_xml`'s `_indent` and ET.tostring
# recurse once per level, so a mod file nested near Python's recursion limit
# parses fine and then raises RecursionError half way through a merge (plans/68
# 2P-C3). DayZ's own mission files are five levels deep at most.
MAX_XML_DEPTH = 200


def validate_xml_file(path: str | Path) -> bool:
    """Return True if the file exists and contains valid XML.

    A file that is not UTF-8, or cannot be read at all, is not valid XML here:
    mod files are someone else's, and one of them must not stop a merge.
    """
    return load_valid_xml(path) is not None


def load_valid_xml(path: str | Path) -> ET.ElementTree | None:
    """The parsed file, or None for a file `validate_xml_file` calls invalid."""
    try:
        return load_xml(path)
    except (ET.ParseError, OSError, UnicodeDecodeError):
        return None


def xml_depth(elem: ET.Element) -> int:
    """How many levels *elem*'s tree has, *elem* itself being one.

    Counted with a list rather than by recursion, so it answers for the very
    trees `save_xml` cannot write.
    """
    deepest = 0
    pending = [(elem, 1)]
    while pending:
        node, level = pending.pop()
        deepest = max(deepest, level)
        pending.extend((child, level + 1) for child in node)
    return deepest


def subelement_in_order(parent: ET.Element, tag: str, order: list[str]) -> ET.Element:
    """A new *tag* child of *parent*, where Bohemia's files put it: after the
    last child whose tag comes no later than *tag* in *order* (first when there
    is none), at the end for a tag *order* does not know. ``ET.SubElement``
    appends, which put a first ``<lifetime>`` after a type's ``<value>``s.
    The same rule as `app.web.editors._xml_nested.after`."""
    index = len(parent)
    if tag in order:
        rank = order.index(tag)
        index = 0
        for i, child in enumerate(parent):
            if isinstance(child.tag, str) and child.tag in order and order.index(child.tag) <= rank:
                index = i + 1
    elem = ET.Element(tag)
    parent.insert(index, elem)
    return elem


def load_xml(path: str | Path, *, preserve_comments: bool = False) -> ET.ElementTree:
    """Parse an XML file, stripping any content before the XML declaration.

    Handles files that have comments, BOM, or whitespace before ``<?xml ...?>``.

    ``preserve_comments`` keeps ``<!-- ... -->`` nodes in the tree so a
    parse/save round-trip doesn't drop them. Required for mission files that
    mods splice into with ``<!-- START MOD: @X -->`` / ``<!-- END MOD: @X -->``
    markers — ``xml_merge.remove_mod_xml_blocks`` matches those markers as
    text, so losing them makes merged mod content unremovable.
    """
    return parse_xml_text(
        Path(path).read_text(encoding="utf-8-sig"), preserve_comments=preserve_comments,
    )


def backup_file(path: Path) -> Path:
    """Create a .backup copy of a config file. Returns the backup path."""
    backup_path = path.with_suffix(path.suffix + ".backup")
    if path.is_file():
        shutil.copy2(path, backup_path)
    return backup_path


def keep_first_backup(path: Path) -> Path:
    """:func:`backup_file`, create-only: an existing ``.backup`` stays as it
    is, like every mission file's (``xml_merge.ensure_backup``). The map
    editor saves on every edit, and overwriting kept no file as it was."""
    backup_path = path.with_suffix(path.suffix + ".backup")
    return backup_path if backup_path.exists() else backup_file(path)


def restore_file(path: Path, shipped: Path | None = None) -> bool:
    """Restore a config file. Returns True if restored.

    *shipped* is the game's own copy of the file for the installed version
    (`mission_stock.shipped_copy`), and wins when there is one. The `.backup`
    is the fallback, for a file Zedstead keeps no stock copy of (a community
    mission): it is written once, so for a stock file it can hold an older game
    version's file (1.29 on 1.30, browser test 2026-11-17, F10).

    Snapshots the current contents first. This was harmless while every merge
    restored these files anyway; since merges stopped doing that (2026 Q3) an
    admin's edits accumulate in the live file, and *Restore from backup* is the
    one button that throws all of them away at once. The version is the undo.
    """
    source = shipped if shipped is not None else path.with_suffix(path.suffix + ".backup")
    if not source.is_file():
        return False
    snapshot_before_write(path, trigger="restore_backup")
    copy_file(source, path)
    return True


# What XML 1.0 cannot hold (its `Char` production): the C0 controls other than
# tab, LF and CR, lone surrogates, U+FFFE and U+FFFF. ElementTree writes them
# as they are, and the file then no longer parses (plans/68 A-2).
_NOT_XML_CHAR = re.compile("[^\t\n\r\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")


class XmlCharError(ValueError):
    """A tree ``save_xml`` refuses to write. Routes answer it with a 400."""


def _where_bad_char(root: ET.Element) -> str:
    """The element or attribute that holds the first character
    ``_NOT_XML_CHAR`` matches, as a reader would look for it."""
    for elem in root.iter():
        if isinstance(elem.tag, str):
            name = f"<{elem.tag}>"
        else:
            name = "a comment" if elem.tag is ET.Comment else "a processing instruction"
        for attr, value in elem.attrib.items():
            if _NOT_XML_CHAR.search(attr) or _NOT_XML_CHAR.search(value):
                return f'the "{attr}" attribute of {name}'
        if _NOT_XML_CHAR.search(name) or _NOT_XML_CHAR.search(elem.text or ""):
            return name
        if _NOT_XML_CHAR.search(elem.tail or ""):
            return f"the text after {name}"
    return "the file"


# The declaration ElementTree writes, kept for a file that does not exist yet.
_NEW_FILE_DECLARATION = "<?xml version='1.0' encoding='utf-8'?>\n"

_DECLARATION = re.compile(r"<\?xml\s.*?\?>", re.S)
# What may stand between the declaration and the root element.
_PROLOG_ITEM = re.compile(r"\s+|<!--.*?-->|<\?.*?\?>|<!DOCTYPE[^>]*>", re.S)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_INDENTED_TAG = re.compile(r"^([ \t]+)<", re.M)
# An empty-element tag without its closing `/>` (and the space before it).
_TIGHT_EMPTY = re.compile(r"(<[^\s/!?<>](?:[^<>]*?[^\s/<>])?)/>")
_SPACED_EMPTY = re.compile(r"(<[^\s/!?<>](?:[^<>]*?[^\s/<>])?)\s+/>")
_EMPTY_PAIR =re.compile(r"<([^\s/!?<>]+)(?:\s[^<>]*)?></\1>")


@dataclass(frozen=True)
class _FileStyle:
    """How a file on disk is laid out, so a save can write it the same way."""

    prolog: str        # declaration and anything up to the root element, verbatim
    epilog: str        # what follows the root element (space, comments), verbatim
    indent: str | None  # one level; None when no line is indented
    tight_empty: bool  # mostly `<a/>` rather than ElementTree's `<a />`
    tight_tags: frozenset[str]   # each `<a …` the file closes as `<a …/>`
    spaced_tags: frozenset[str]  # and as `<a … />`
    empty_pairs: bool  # `<a></a>` (cfgignorelist.xml) rather than either
    crlf: bool
    body: str = ""     # the root element as the file has it (LF), when nothing precedes the declaration


def _mostly_crlf(text: str) -> bool:
    return text.count("\r\n") > text.count("\n") // 2


def write_keeping_line_endings(path: Path, text: str) -> None:
    """Atomically write LF *text* to *path* with the line endings the file
    there has, the way `save_xml` keeps them, for a writer that serializes
    the whole file itself (an XML parser reads CRLF as LF). A file that
    cannot be read is written as a new one."""
    try:
        crlf = _mostly_crlf(path.read_bytes().decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError):
        atomic_write_text(path, text)
        return
    if crlf:
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    atomic_write_text(path, text, newline="")


def _file_style(path: Path) -> _FileStyle | None:
    """The layout of the file at *path*, or None when there is none to copy.

    Read the way `load_xml` reads: anything before the declaration is dropped
    (that is what "re-save to fix" in `XML_ERROR_HINTS` promises), and so is a
    byte order mark.
    """
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig")  # line endings untranslated
    except (OSError, UnicodeDecodeError):
        return None
    crlf = _mostly_crlf(text)
    text = text.replace("\r\n", "\n")
    decl = _DECLARATION.search(text)
    start = decl.start() if decl else 0
    pos = decl.end() if decl else 0
    while (m := _PROLOG_ITEM.match(text, pos)) and m.end() > pos:
        pos = m.end()
    if not re.match(r"<[^\s!?/<>]", text[pos:pos + 2]):
        return None  # not a document this can make sense of; write as new
    body = _COMMENT.sub("", text[pos:])
    widths = [m.group(1) for m in _INDENTED_TAG.finditer(body)]
    tabs = sum(1 for w in widths if w.startswith("\t"))
    spaces = [len(w) for w in widths if w.startswith(" ") and w.strip(" ") == ""]
    if tabs and tabs >= len(spaces):
        indent = "\t"
    elif spaces:
        indent = " " * min(spaces)
    else:
        indent = None
    tight_tags = frozenset(_TIGHT_EMPTY.findall(body))
    spaced_tags = frozenset(_SPACED_EMPTY.findall(body))
    tight = len(re.findall(r"[^\s]/>", body))
    spaced = len(re.findall(r"\s/>", body))
    pairs = len(_EMPTY_PAIR.findall(body))
    root_end = _root_end(text)
    return _FileStyle(
        prolog=text[start:pos],
        epilog=text[root_end:],
        indent=indent,
        tight_empty=tight > spaced,
        tight_tags=tight_tags,
        spaced_tags=spaced_tags,
        empty_pairs=pairs > tight + spaced,
        crlf=crlf,
        body=text[pos:root_end] if start == 0 and not raw.startswith(b"\xef\xbb\xbf") else "",
    )


def _root_end(text: str) -> int:
    """Where the root element of *text* ends: what follows it can only be
    space, comments and processing instructions, none of which is in the
    tree, so a save puts them back from the file. A comment holds no "--"
    and so no "<!--", and an instruction no "?>", so the last opener before
    each closer starts it."""
    end = len(text.rstrip())
    while True:
        if text.endswith("-->", 0, end):
            start = text.rfind("<!--", 0, end - 3)
        elif text.endswith("?>", 0, end):
            start = text.rfind("<?", 0, end - 2)
        else:
            return end
        if start < 0:
            return end
        end = len(text[:start].rstrip())


_SPACE = re.compile(r"\s+")


def _same_document(old: str, new: str) -> bool:
    """*old* and *new* are one document, written differently only where
    ElementTree cannot copy the file (`chance= "0.03"`, `&apos;`): equal once
    canonicalised (C14N, comments included). The whitespace-blind comparison
    first keeps the cost off a save that did change something."""
    if not old or _SPACE.sub("", old) != _SPACE.sub("", new):
        return False
    try:
        return ET.canonicalize(old, with_comments=True) == ET.canonicalize(new, with_comments=True)
    except ET.ParseError:
        return False


def _keep_layout(ws: str | None, want: str) -> str:
    """*ws* when it already puts the next tag where *want* would (blank lines
    and trailing spaces included), else *ws* with its last line replaced by
    *want*'s, so a trailing tab after the tag before stays (Bohemia's
    cfgignorelist.xml ends `Wreck_Mi8` with one); *want* when *ws* has no
    line break."""
    if ws and not ws.strip() and "\n" in ws and want.startswith("\n"):
        return ws[:ws.rindex("\n")] + want
    return want


def _on_one_line(elem: ET.Element, kids: list[ET.Element]) -> bool:
    """*elem* written on one line with its children (Bohemia's
    `<cargo chance="0.20"> <item name="…" /> </cargo>`): its text and every
    child's tail are whitespace with no line break (the last child's may be
    none, `<item … /></cargo>`). A child added in the middle has no tail
    yet, so the element is laid out again; one added at the end joins the
    line."""
    def inline(ws: str | None) -> bool:
        return ws is not None and not ws.strip() and "\n" not in ws

    if not inline(elem.text) or not kids:
        return False
    return all(inline(k.tail) for k in kids[:-1]) and (kids[-1].tail is None or inline(kids[-1].tail))


def _width(line: str) -> int:
    """How far an indent (a line break and what follows it) reaches, a tab
    counted as four spaces: Bohemia mixes the two in one file (Livonia's
    cfgeventspawns.xml: events at four spaces, their positions at two tabs)."""
    return len(line[line.rindex("\n") + 1:].expandtabs(4))


def _deeper(ws: str | None, outer: str) -> str | None:
    """The last line of layout whitespace *ws* when it puts the next tag
    deeper than *outer*, else None."""
    if not ws or ws.strip() or "\n" not in ws:
        return None
    line = ws[ws.rindex("\n"):]
    return line if _width(line) > _width(outer) else None


def _inner(elem: ET.Element, kids: list[ET.Element], outer: str, space: str) -> str:
    """The indent of *elem*'s children: the one the file already gives them
    when that is deeper than the element's own (cfgrandompresets.xml indents
    items two tabs under their <cargo>), else one *space* deeper."""
    for ws in [elem.text, *(k.tail for k in kids[:-1])]:
        line = _deeper(ws, outer)
        if line is not None:
            return line
    return outer + space


def _layout_before(ws: str | None, nxt: ET.Element, want: str, outer: str) -> str:
    """The whitespace before *nxt*, a child, as the file has it when it puts
    the tag on a line of its own at any indent but its parent's (Chernarus'
    cfgeventgroups.xml has children at column 0), or a comment at column 0 or
    on the line of the tag before; else as `_keep_layout`. A line at the
    parent's own indent is the tail that closed the parent before something
    was appended after it, and is replaced."""
    if ws is not None and not ws.strip():
        if "\n" in ws and _width(ws) != _width(outer):
            return ws
        if nxt.tag is ET.Comment and ("\n" not in ws or ws.endswith("\n")):
            return ws
    return _keep_layout(ws, want)


def _indent(root: ET.Element, space: str) -> None:
    """`ET.indent`, except that whitespace already right is left as it is.

    ElementTree's version overwrites every bit of layout whitespace, so a blank
    line between two elements went on every save. Text that is not whitespace
    is never touched, as there. Whitespace the file already has before a child
    stays when it puts the child deeper than its parent or precedes a comment
    (`_layout_before`), so an untouched file is written back byte for byte;
    a new child takes its siblings' indent (`_inner`), and an element written
    on one line stays on it (`_on_one_line`).
    """

    def children(elem: ET.Element, outer: str) -> None:
        kids = list(elem)
        if _on_one_line(elem, kids):
            for child in kids:
                if len(child):
                    children(child, outer)
            return
        inner = _inner(elem, kids, outer, space)
        if kids and (not elem.text or not elem.text.strip()):
            elem.text = _layout_before(elem.text, kids[0], inner, outer)
        for i, child in enumerate(kids):
            if len(child):
                children(child, inner)
            if child.tail and child.tail.strip():
                continue
            after = kids[i + 1] if i + 1 < len(kids) else None
            if after is None:
                # The closing tag: kept at the file's own indent as long as
                # that is not deeper than the element's (a tail that used to
                # separate two children is).
                tail = child.tail
                keep = tail and not tail.strip() and "\n" in tail and _width(tail) <= _width(outer)
                child.tail = tail if keep else _keep_layout(tail, outer)
            else:
                child.tail = _layout_before(child.tail, after, inner, outer)

    if len(root):
        children(root, "\n")


def _empty_tags_as_in(body: str, style: _FileStyle) -> str:
    """ElementTree's `<a />` written the way the file wrote that tag.

    Bohemia's files mix `<a/>` and `<a />` (cfgeconomycore.xml's `<default>`s
    against its `<rootclass>`es), so a tag the file already holds keeps its own
    form, an edited one takes the form of the file's other tags of that element
    name, and a name the file has none of takes the file's majority. Comments
    are left alone; ` />` cannot occur in text or an attribute value, where
    ElementTree writes `>` as `&gt;`.
    """
    def name_of(tag: str) -> str:
        return tag[1:].split(None, 1)[0]

    tight_names = Counter(name_of(t) for t in style.tight_tags)
    spaced_names = Counter(name_of(t) for t in style.spaced_tags)

    def one(m: re.Match) -> str:
        tag = m.group(1)
        if tag in style.tight_tags:
            tight = True
        elif tag in style.spaced_tags:
            tight = False
        elif tight_names[name_of(tag)] != spaced_names[name_of(tag)]:
            tight = tight_names[name_of(tag)] > spaced_names[name_of(tag)]
        else:
            tight = style.tight_empty
        return tag + ("/>" if tight else " />")

    parts = _COMMENT.split(body)
    comments = _COMMENT.findall(body)
    out = [_SPACED_EMPTY.sub(one, parts[0])]
    for comment, part in zip(comments, parts[1:]):
        out += [comment, _SPACED_EMPTY.sub(one, part)]
    return "".join(out)


def save_xml(
    tree: ET.ElementTree, path: Path, indent: str | None = None, *, snapshot: bool = True,
) -> None:
    """Write an ElementTree to disk, laid out the way the file already was.

    When *path* exists its declaration and whatever precedes the root element
    (comments included) are kept byte for byte, and so are its indent unit
    (tabs, or the width of its spaces), its empty-element style (`<a/>` or
    `<a />`), its line endings and its trailing whitespace. Layout whitespace
    that already sits right is not rewritten, so an edit changes only the lines
    of what it edited (2026 Q4: a two-value edit of Bohemia's events.xml
    used to rewrite all 2,400 lines, from 4 spaces to tabs). *indent* is the
    unit for a file that has none to copy (a new file, or one on one line);
    tabs by default. A new file gets ElementTree's declaration.

    Automatically creates a versioned snapshot before overwriting, unless
    *snapshot* is False: the map editor takes its own, once per session
    (``app/spawn_editor/versioning.py``), since it writes on every drag. The write
    is atomic (temp file + os.replace): a crash mid-write must not truncate a
    live mission XML, since every XML editor and the spawn editor route through
    here.

    Raises ``XmlCharError``, before the snapshot and the write, when the text
    holds a character XML cannot (a vertical tab typed into an editor, say):
    the file would no longer parse. A parsed tree never holds one.
    """
    style = _file_style(path)
    unit = (style.indent if style else None) or indent or "\t"
    _indent(tree.getroot(), unit)
    body = ET.tostring(
        tree.getroot(), encoding="unicode",
        short_empty_elements=not (style and style.empty_pairs),
    )
    bad =_NOT_XML_CHAR.search(body)
    if bad:
        raise XmlCharError(
            f"{_where_bad_char(tree.getroot())} contains the character "
            f"U+{ord(bad.group()):04X}, which an XML file cannot hold."
        )
    if style is not None and _same_document(style.body, body):
        # Nothing changed: the file keeps its bytes and gets no version.
        return
    if style is None:
        text = _NEW_FILE_DECLARATION + body + "\n"
        newline = None
    else:
        body = _empty_tags_as_in(body, style)
        text = style.prolog + body + style.epilog
        if style.crlf:
            text = text.replace("\n", "\r\n")
        newline = ""
    if snapshot:
        snapshot_before_write(path)
    atomic_write_text(path, text, newline=newline)


# The files a `one_version_per_file` scope has already kept; None outside one.
_kept_this_scope: ContextVar[set[Path] | None] = ContextVar("_kept_this_scope", default=None)


@contextmanager
def one_version_per_file(already_kept: Iterable[Path] = ()) -> Iterator[None]:
    """Inside, a file gets one version: what it held before the first write.

    A merge writes a mission file twice, once with every mod block stripped and
    once with the mods spliced back. Snapshotting both kept the stripped half
    as a version, a file with every mod block gone that nobody ever had
    (2026 Q4). A scope inside another one shares the outer one's files.

    *already_kept* are files the caller has versioned its own way and that get
    none here: the map editor takes one version per file per session, and a
    drag that re-applies a manual file must not add one per drag.
    """
    if _kept_this_scope.get() is not None:
        yield
        return
    token = _kept_this_scope.set({Path(p).resolve() for p in already_kept})
    try:
        yield
    finally:
        _kept_this_scope.reset(token)


def snapshot_before_write(path: Path, trigger: str = "auto_save") -> None:
    """Create a versioned snapshot if the file exists and has changed.

    Call it before rewriting a mission file outside `save_xml`. Inside a
    `one_version_per_file` scope only the first call per file snapshots.
    """
    if not path.is_file():
        return
    kept = _kept_this_scope.get()
    if kept is not None:
        key = path.resolve()
        if key in kept:
            return
        kept.add(key)
    try:
        from app.shared.config_versions import snapshot
        snapshot(path, trigger=trigger)
    except Exception:
        pass  # never block a save because of versioning


def get_mission_dir(config) -> Path | None:
    """Resolve the active mission directory from config.

    Uses the mission template from serverDZ.cfg to find the
    mpmissions/<template> directory.
    """
    from app.modmgr.mods import get_current_mission

    mission = get_current_mission(config.server_cfg_path)
    if not mission:
        return None
    mission_dir = config.server_dir_path / "mpmissions" / mission
    return mission_dir if mission_dir.is_dir() else None
