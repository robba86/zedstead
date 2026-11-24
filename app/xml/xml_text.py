"""Parse, describe and indent XML held as a *string*.

The text-level twin of `xml_utils.py`'s file-level helpers, and the single
place in the app where a string of XML is turned into a tree, an error message
or re-indented text. Five callers collapse onto it: `xml_utils.load_xml`,
`config_versions.try_normalize_xml`, `web/editors/_raw.validate_raw`,
`xml_manual._validate` and the `/api/xml/*` endpoints behind the web editors'
Format / Check buttons.

Two rules the whole module exists to keep:

* **Comments survive.** Mission files carry ``<!-- START MOD: @X -->`` markers
  that `xml_merge.remove_mod_xml_blocks` matches as *text* — dropping them makes
  a mod's merged content unremovable. A plain parse/serialize round-trip drops
  every comment, and even ``preserve_comments=True`` drops the ones *outside*
  the root element, so `format_xml_text` parses through a synthetic wrapper.
* **A rootless fragment is legal input.** Manual XML links accept one (people
  copy the elements out of a file, not the file), so both the validator and the
  formatter take a ``fragment`` flag rather than assuming a document.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from io import StringIO

import defusedxml.ElementTree as DET
from defusedxml.common import DefusedXmlException

_XML_DECL_RE = re.compile(r"<\?xml\s")

# The declaration zsm writes on XML it generates itself (starter templates,
# captured event positions, sampled elements). Never forced onto a file the
# user already has one on — see `format_xml_text`.
XML_DECLARATION = '<?xml version="1.0" encoding="UTF-8"?>'

# Leading declaration, captured verbatim so `format_xml_text` can put the
# author's own bytes back (quote style included) instead of ET's canonical one.
_LEADING_DECL_RE = re.compile(r"^﻿?\s*(<\?xml[^>]*\?>)")


def strip_declaration(text: str) -> str:
    """*text* without its leading ``<?xml …?>`` (and the BOM/space around it).

    For code that wraps a fragment in a root element and emits its own
    declaration: an XML document may carry exactly one, at the very start, so
    leaving the original in place puts a second one *inside* the wrapper and
    the result is not well-formed.
    """
    return _LEADING_DECL_RE.sub("", text, count=1).lstrip("﻿").lstrip()

_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)


def mend_comment_dashes(text: str) -> tuple[str, int]:
    """*text* with every comment made legal XML, and how many were changed.

    XML does not allow ``--`` inside a comment or a ``-`` just before its
    ``-->``, but mods ship banner comments like
    ``<!-- -----IRON SIGHTS----- -->`` and the game reads them: @FOX WEAPONS
    2.0 had 9 of its 10 types files skipped as "not valid XML" for it (user
    test in 2026 Q4). Each ``--`` becomes ``- -``; nothing outside a comment
    changes.
    """
    changed = 0

    def mend(m: re.Match) -> str:
        nonlocal changed
        body = m.group(1)
        if "--" not in body and not body.endswith("-"):
            return m.group(0)
        changed += 1
        body = re.sub(r"-(?=-)", "- ", body)
        if body.endswith("-"):
            body += " "
        return f"<!--{body}-->"

    return _COMMENT_RE.sub(mend, text), changed


# Wrapper tag used to parse a rootless fragment. Internal only — it is stripped
# again before anything is returned, and nothing may key on it.
_FRAGMENT_ROOT = "zsm-fragment"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

# Substring of expat's message -> what it means in plain English. Ordered:
# first match wins.
XML_ERROR_HINTS: list[tuple[str, str]] = [
    ("mismatched tag", "closing tag doesn't match opening tag"),
    ("unclosed token", "element or attribute not properly closed"),
    ("no element found", "missing closing tag for root element (or file is empty)"),
    ("not well-formed", "invalid character or syntax error"),
    ("duplicate attribute", "same attribute appears twice on an element"),
    ("junk after document element", "extra content after the root closing tag"),
    ("not at start of entity", "content before <?xml?> declaration (comment or BOM) — re-save to fix"),
    ("unbound prefix", "namespace prefix used without declaration"),
    ("undefined entity", "unknown entity reference (e.g. &foo;)"),
]


def friendly_xml_error(msg: str) -> str:
    """Turn an ET.ParseError message into a short, actionable hint."""
    lower = msg.lower()
    for pattern, hint in XML_ERROR_HINTS:
        if pattern in lower:
            return hint
    # Strip the trailing "line X, column Y" that expat appends
    parts = msg.rsplit(":", 1)
    return parts[0].strip() if len(parts) > 1 else msg


@dataclass(frozen=True)
class XmlProblem:
    """Where an XML string is broken, and what that means."""

    line: int      # 1-based; 0 when the parser gave no position
    column: int    # 1-based
    message: str   # the raw parser text
    hint: str      # friendly_xml_error(message)

    @property
    def summary(self) -> str:
        if not self.line:
            return self.hint or self.message
        return f"Line {self.line}, column {self.column} — {self.hint}"

    def as_dict(self) -> dict:
        return {
            "line": self.line,
            "column": self.column,
            "message": self.message,
            "hint": self.hint,
            "summary": self.summary,
        }


class XmlSyntaxError(ValueError):
    """Malformed XML, carrying the position.

    ``str()`` is the problem's summary on purpose: the web routers turn an
    exception into ``HTTPException(400, detail=str(e))``, so raising this gets
    them "Line 12, column 4 — closing tag doesn't match opening tag" with no
    router change.
    """

    def __init__(self, problem: XmlProblem) -> None:
        super().__init__(problem.summary)
        self.problem = problem


def describe_xml_error(exc: ET.ParseError, *, line_offset: int = 0) -> XmlProblem:
    """Build an `XmlProblem` from a parse error.

    ``line_offset`` is subtracted from the reported line so a fragment parsed
    inside a wrapper still reports the line the user is looking at.
    """
    line, column = getattr(exc, "position", (0, 0))
    return XmlProblem(
        line=max(line - line_offset, 0),
        column=column + 1 if line else 0,  # expat's column is 0-based
        message=str(exc),
        hint=friendly_xml_error(str(exc)),
    )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_xml_text(
    text: str, *, preserve_comments: bool = False, strip_leading_junk: bool = True,
) -> ET.ElementTree:
    """Parse a string of XML, raising `ET.ParseError` on anything malformed.

    ``strip_leading_junk`` drops whatever sits before ``<?xml ...?>`` (BOM, a
    stray comment, whitespace) — on by default because that is what reading a
    real file has always done. Validators pass False so they accept exactly
    what a save accepts.

    ``preserve_comments`` keeps ``<!-- ... -->`` nodes in the tree. Required
    for mission files that mods splice into with START/END MOD markers.
    """
    if strip_leading_junk:
        m = _XML_DECL_RE.search(text)
        if m and m.start() > 0:
            text = text[m.start():]
    parser = (
        DET.DefusedXMLParser(target=ET.TreeBuilder(insert_comments=True))
        if preserve_comments
        else None
    )
    try:
        return DET.parse(StringIO(text), parser=parser)
    except DefusedXmlException as exc:
        # Reject XXE / entity-expansion payloads as invalid XML so every
        # existing ET.ParseError handler treats them the same as malformed
        # input. defusedxml returns standard ElementTree objects otherwise.
        raise ET.ParseError(f"unsafe XML rejected: {exc}") from exc
    except UnicodeEncodeError as exc:
        # Half of a surrogate pair (JSON's "\ud83d") is text the parser cannot
        # encode and no file can hold.
        raise ET.ParseError(
            "the text holds a character that is not valid Unicode (half of a surrogate pair)"
        ) from exc


def _element_children(parent: ET.Element) -> list[ET.Element]:
    """*parent*'s real element children — comments and PIs have a callable tag."""
    return [child for child in parent if isinstance(child.tag, str)]


def _count_elements(parent: ET.Element) -> int:
    return sum(1 for node in parent.iter() if isinstance(node.tag, str)) - 1


# ---------------------------------------------------------------------------
# Checking
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class XmlInfo:
    """What one check learned about a string of XML."""

    ok: bool
    fragment: bool       # True when it only parsed once wrapped
    root_tag: str        # "" for a fragment (or on failure)
    elements: int        # elements, excluding comments
    problem: XmlProblem | None


def inspect_xml_text(content: str, *, fragment: bool = False) -> XmlInfo:
    """Parse *content* and report what it is, or where it is broken.

    With ``fragment=True`` a string that isn't a single-rooted document is
    retried inside a wrapper — the same second chance `xml_manual._validate`
    has always given a manual link's content. The wrapper is newline-separated
    so every line shifts by exactly one and no column shifts, and the offset is
    taken back off the reported position.

    A leading ``<?xml …?>`` is dropped before wrapping. A declaration is only
    legal at the very start of a document, so inside the wrapper it can never be
    valid — and mods (and users pasting out of a file) routinely ship a rootless
    fragment with one, which used to be reported as broken XML.
    """
    try:
        root = parse_xml_text(content, preserve_comments=True, strip_leading_junk=False).getroot()
    except ET.ParseError as exc:
        strict_problem = describe_xml_error(exc)
    else:
        return XmlInfo(True, False, root.tag, _count_elements(root) + 1, None)

    if not fragment:
        return XmlInfo(False, False, "", 0, strict_problem)

    body = strip_declaration(content)
    # Keep reported line numbers pointing at the user's own text: the wrapper
    # adds one line in front, and the stripped declaration took its own lines
    # away, so they are added back. They were subtracted, which put an error
    # two lines too high after a one-line declaration.
    offset = 1 - content[: len(content) - len(body)].count("\n")
    wrapped = f"<{_FRAGMENT_ROOT}>\n{body}\n</{_FRAGMENT_ROOT}>"
    try:
        root = parse_xml_text(wrapped, preserve_comments=True, strip_leading_junk=False).getroot()
    except ET.ParseError as exc:
        return XmlInfo(False, False, "", 0, describe_xml_error(exc, line_offset=offset))
    return XmlInfo(True, True, "", _count_elements(root), None)


def check_xml_text(content: str, *, fragment: bool = False) -> XmlProblem | None:
    """The problem with *content*, or None when it parses."""
    return inspect_xml_text(content, fragment=fragment).problem


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FormatResult:
    text: str                    # the *unchanged* input when `problem` is set
    changed: bool
    problem: XmlProblem | None
    root_tag: str
    elements: int

    @property
    def ok(self) -> bool:
        return self.problem is None


def _is_layout_whitespace(value: str | None, indent: str) -> bool:
    """True for the `"\\n" + indent * n` strings `ET.indent` writes."""
    return bool(value) and not value.strip() and value.startswith("\n") and value.endswith(indent)


def _dedent_one_level(elem: ET.Element, indent: str) -> None:
    """Remove one indent level from the whitespace `ET.indent` just wrote.

    Done on the tree, never on the serialized string: the wrapper's extra level
    lives only in the `text`/`tail` values ET controls, while a multi-line
    comment body or a multi-line attribute value is content. Stripping a
    leading indent off every output *line* would silently eat a tab from the
    second line of every explanatory comment (which is how this was found).

    Mirrors `ET.indent`'s own rule for what it is allowed to overwrite: `text`
    only on an element that has children, `tail` on anything.
    """
    for node in elem.iter():
        if len(node) and _is_layout_whitespace(node.text, indent):
            node.text = node.text[: -len(indent)]
        if _is_layout_whitespace(node.tail, indent):
            node.tail = node.tail[: -len(indent)]


_MIXED_CONTENT = XmlProblem(
    line=0,
    column=0,
    message="text outside an element",
    hint="there is text outside any element — wrap it in a tag or delete it",
)


def format_xml_text(content: str, *, indent: str = "\t") -> FormatResult:
    """Re-indent *content*, keeping its comments and its own XML declaration.

    Works on a document and on a rootless fragment through one code path: the
    content is *always* parsed inside a synthetic wrapper, then the wrapper's
    two lines are dropped and one indent level is stripped off the rest. That
    is not just fragment support — `ET.TreeBuilder(insert_comments=True)` only
    emits a comment while an element is open, so a header comment *above* the
    root element is silently lost by any unwrapped round-trip, which is the
    most natural place for a user to put one.

    The author's ``<?xml ...?>`` line is put back byte for byte (single vs
    double quotes included) and one is never invented — this rewrites a file
    the user is editing, so it must not restyle anything it wasn't asked to.
    (`config_versions.try_normalize_xml` deliberately does the opposite: it
    canonicalizes the declaration, because a quote-style difference must not
    read as a change in a *diff*.)

    CRLF input comes back LF — ElementTree serializes LF only.
    """
    if not content.strip():
        return FormatResult(content, False, None, "", 0)

    decl_match = _LEADING_DECL_RE.match(content)
    declaration = decl_match.group(1) if decl_match else ""
    # The pattern takes a BOM only together with a declaration.
    body = content[decl_match.end():] if decl_match else content.lstrip("\ufeff")

    wrapped = f"<{_FRAGMENT_ROOT}>\n{body}\n</{_FRAGMENT_ROOT}>"
    try:
        root = parse_xml_text(wrapped, preserve_comments=True, strip_leading_junk=False).getroot()
    except ET.ParseError as exc:
        # The wrapper shifted every line by one; the declaration (if any) was
        # sliced off, so add its lines back to point at the real one.
        offset = 1 - content[: decl_match.end()].count("\n") if decl_match else 1
        return FormatResult(content, False, describe_xml_error(exc, line_offset=offset), "", 0)

    children = _element_children(root)
    if not len(root):
        return FormatResult(content, False, None, "", 0)

    # Text that isn't inside any element would survive the parse (as root.text
    # or a child's tail) but not the line surgery below, so refuse rather than
    # eat it.
    if (root.text and root.text.strip()) or any(c.tail and c.tail.strip() for c in root):
        return FormatResult(content, False, _MIXED_CONTENT, "", 0)

    ET.indent(root, space=indent)
    _dedent_one_level(root, indent)
    lines = ET.tostring(root, encoding="unicode").split("\n")
    # After the dedent, the wrapper's own tags are the only thing left on the
    # first and last lines and everything between is already at level 0.
    text = "\n".join(lines[1:-1]).rstrip("\n") + "\n"
    if declaration:
        text = f"{declaration}\n{text}"

    root_tag = children[0].tag if len(children) == 1 else ""
    return FormatResult(text, text != content, None, root_tag, _count_elements(root))
