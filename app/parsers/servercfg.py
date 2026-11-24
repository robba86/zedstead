"""serverDZ.cfg reader/writer — key=value parser preserving file structure."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from app.modmgr.mods import breaks_a_line
from app.shared.json_utils import NotUtf8Error, atomic_write_text, read_text_lossy


# One root value as DayZ reads it. A quoted string is one token up to its
# closing quote, so a `;` inside it is part of the value (a quote inside is
# doubled, `""`, the grammar check_server_cfg reads); a bare value ends at the
# first `;`. The last alternative is the old lenient reading, kept for a line
# with a stray quote. The reader and the writer both use it, so zsm replaces
# exactly the value it shows.
_VALUE = r'(?:"(?P<quoted>(?:[^"]|"")*)"|"?(?P<bare>[^";]*)"?)'

# A string, or a `//` comment and the rest of its line.
_STRING_OR_COMMENT = re.compile(r'"(?:[^"]|"")*"|//.*')
# The same, or a `/* */` comment, to the end of the line when it does not
# close there. Only the mission walk reads these (`_mission_code`).
_STRING_OR_ANY_COMMENT = re.compile(_STRING_OR_COMMENT.pattern + r"|/\*.*?(?:\*/|$)")
_COMMENT_END = re.compile(r".*?\*/")


def _blank(m: re.Match[str]) -> str:
    """A string's content, or a whole comment, as spaces of the same length."""
    text = m.group()
    if text.startswith('"'):
        return '"' + " " * (len(text) - 2) + '"'
    return " " * len(text)


def _mission_code(lines: list[str]) -> Iterator[str]:
    """Each of *lines* with every string's content and every `//` and `/* */`
    comment (across lines too) blanked out, the same length, so a search in
    it finds only what DayZ reads as code: a key named inside another key's
    quoted value or in a comment, or a brace in a hostname, is not structure.
    Each line keeps its length."""
    in_comment = False

    def blank(m: re.Match[str]) -> str:
        nonlocal in_comment
        text = m.group()
        if text.startswith("/*") and (len(text) < 4 or not text.endswith("*/")):
            in_comment = True
        return _blank(m)

    for line in lines:
        head = ""
        if in_comment:
            end = _COMMENT_END.match(line)
            if end is None:
                yield " " * len(line)
                continue
            head, line = " " * end.end(), line[end.end():]
            in_comment = False
        yield head + _STRING_OR_ANY_COMMENT.sub(blank, line)


def _root_lines(lines: list[str]) -> Iterator[tuple[int, str]]:
    """(index, code) of each of *lines* that starts at the root of the file,
    its code as `_mission_code` gives it. A line that opens a block is still
    one; the lines inside it are not."""
    depth = 0
    for i, code in enumerate(_mission_code(lines)):
        if depth == 0:
            yield i, code
        depth += code.count("{") - code.count("}")


def _value_of(match: re.Match[str]) -> str:
    quoted = match.group("quoted")
    return quoted.replace('""', '"') if quoted is not None else match.group("bare")


def _lines_to_rewrite(path: Path) -> tuple[list[str], str]:
    """*path*'s lines with their endings, and the ending the file uses.

    Read without newline translation, so a CRLF file (Bohemia's serverDZ.cfg
    is one) is written back CRLF by `_write_lines`; a byte order mark stays
    the first character and is written back too. A file that is not UTF-8 is
    refused (``NotUtf8Error``), as ``read_text_to_rewrite`` does.
    """
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise NotUtf8Error(path) from None
    lines = text.splitlines(keepends=True)
    first = next((line for line in lines if line.endswith(("\n", "\r"))), "\n")
    eol = "\r\n" if first.endswith("\r\n") else "\r" if first.endswith("\r") else "\n"
    return lines, eol


def _end_last_line(lines: list[str], eol: str) -> None:
    """Give the last line an ending before a line is added after it."""
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += eol


def _write_lines(path: Path, lines: list[str]) -> None:
    atomic_write_text(path, "".join(lines), newline="")


def read_cfg_values(path: Path, keys: list[str]) -> dict[str, str | None]:
    """Read multiple values from a cfg file (root-level keys only)."""
    result: dict[str, str | None] = {k: None for k in keys}
    if not path.exists():
        return result
    # utf-8-sig: a byte order mark (Notepad adds one) hid the first key.
    lines = read_text_lossy(path, "utf-8-sig").splitlines()
    for i, code in _root_lines(lines):
        for key in keys:
            if result[key] is not None:
                continue
            # Found in the code, so a key in a comment is none; the value is
            # read off the line itself at the same place.
            found = _write_pattern(key).match(code)
            real = _write_pattern(key).match(lines[i], found.end("lead")) if found else None
            if real:
                result[key] = _value_of(real)
    return result


def read_cfg_value(path: Path, key: str) -> str | None:
    """Read a single value from a cfg file. Returns None if not found."""
    return read_cfg_values(path, [key])[key]


# The value Bohemia's serverDZ.cfg ships.
DEFAULT_INSTANCE_ID = 1
_WHOLE_NUMBER = re.compile(r"[+-]?\d+")
_STORAGE_FOLDER = re.compile(r"storage_-?\d+")


def read_instance_id(path: Path) -> int:
    """serverDZ.cfg's ``instanceId``.

    `DEFAULT_INSTANCE_ID` when the key is missing or not a whole number: the
    server refuses to start with such a cfg ("instanceId parameter is
    mandatory and must be valid 32-bit integer"), so nothing is being saved
    under another name.
    """
    raw = (read_cfg_value(path, "instanceId") or "").strip()
    return int(raw) if _WHOLE_NUMBER.fullmatch(raw) else DEFAULT_INSTANCE_ID


def storage_folder_name(path: Path) -> str:
    """The folder inside a mission that holds its persistence:
    ``storage_<instanceId>``.

    Measured on 1.29 (Linux, 2026 Q4) from the RPT's "Selected storage
    directory" line: 7 selects ``storage_7``, 0 ``storage_0`` and -1
    ``storage_-1``. Anything that reads, copies, empties or deletes a
    mission's persistence names the folder through this, never as a literal.
    """
    return f"storage_{read_instance_id(path)}"


def is_storage_folder(name: str) -> bool:
    """Whether *name* is a persistence folder under any ``instanceId``: the
    one in use, or one an earlier id left behind."""
    return _STORAGE_FOLDER.fullmatch(name) is not None


@lru_cache(maxsize=64)
def _write_pattern(key: str) -> re.Pattern[str]:
    # \b before the key so a short key can't match the suffix of a longer one
    # (e.g. "password" matching inside "adminPassword" under IGNORECASE +
    # unanchored search — that clobbered the admin password line). The trailing
    # \s*= already guards the prefix side. Searched in a line's code
    # (`_root_lines`), so it never finds the key inside another key's quoted
    # value or a comment.
    return re.compile(
        rf'(?P<lead>\s*)\b{re.escape(key)}(?P<eq>\s*=\s*){_VALUE}(?P<end>\s*;)',
        re.IGNORECASE,
    )


def _replace_or_append(path: Path, key: str, val_str: str) -> None:
    """Replace a key's value in-place (root level only), or append before class Missions."""
    pattern = _write_pattern(key)
    lines, eol = _lines_to_rewrite(path)
    found = False
    for i, code in _root_lines(lines):
        line = lines[i]
        match = pattern.search(code)
        if match:
            # Spliced in by the match's span, so the value goes in literally:
            # a re string repl would run val_str through re's escape
            # machinery, so a value with a backslash (e.g. a password) raises
            # "bad escape", and a "\g<0>" / "\1" token would be expanded as a
            # group reference — corrupting the line. The lead, `=` and `;`
            # parts are code, which _mission_code leaves as it found them.
            lines[i] = (
                line[: match.start()]
                + f"{match['lead']}{key}{match['eq']}{val_str}{match['end']}"
                + line[match.end():]
            )
            found = True
            break

    if not found:
        _insert_root_line(lines, f"{key} = {val_str};{eol}", eol)

    _write_lines(path, lines)


def _insert_root_line(lines: list[str], new_line: str, eol: str) -> None:
    """Add *new_line* before `class Missions`, so the key stays at the root
    level, or at the end of a file that has no such class."""
    for i, line in enumerate(lines):
        if re.match(r'\s*class\s+Missions\b', line, re.IGNORECASE):
            lines.insert(i, new_line)
            return
    _end_last_line(lines, eol)
    lines.append(new_line)


class CfgValueError(ValueError):
    """A value ``write_cfg_value`` refuses. Routes answer it with a 400; the
    message names the key or field and never quotes the value, which may be
    a password."""


def cfg_value_problem(value: str, *, quoted: bool = False) -> str | None:
    """Why *value* cannot be one serverDZ.cfg value, or ``None``.

    A value is the rest of one line. A character ``str.splitlines`` breaks at
    (``mods.breaks_a_line``: a newline, a vertical tab, NEL, U+2028 and the
    rest) would start a key of its own, or make zsm read the line differently
    from the server (plans/68 LB-4). A tab is whitespace to DayZ's parser and
    stays allowed. A *quoted* value also holds no double quote, which would end
    the string early (plans/68 LB-6). A bare value ends at the first `;`, so it
    holds neither a `;` nor a double quote (plans/68 A-3).
    """
    for ch in value:
        if ch != "\t" and breaks_a_line(ch):
            shown = {"\n": "a newline", "\r": "a carriage return"}.get(ch, f"the character U+{ord(ch):04X}")
            return f"contains {shown}, which would break its line in serverDZ.cfg"
        if quoted and ch == '"':
            return 'contains a double quote ("), which would end the quoted value in serverDZ.cfg'
        if not quoted and ch in '";':
            shown = "a semicolon (;)" if ch == ";" else 'a double quote (")'
            return f"contains {shown}, which would end the value early in serverDZ.cfg"
    return None


# The root keys Bohemia's serverDZ.cfg writes as a quoted string
# (dayz-docs/wiki/pages/server-configuration.wiki, both blocks). Every other
# root key there is a number or a boolean and is written bare. `motd[]` is an
# array, which write_cfg_value does not write, and `template` sits inside
# `class Missions` (set_mission_template quotes it).
QUOTED_KEYS = frozenset(
    {"hostname", "description", "password", "passwordAdmin", "serverTime", "timeStampFormat", "logFile"}
)
_QUOTED_KEYS_LOWER = frozenset(key.lower() for key in QUOTED_KEYS)


def quoted_like_stock(key: str) -> bool:
    """Whether Bohemia's own serverDZ.cfg writes *key* as a quoted string."""
    return key.lower() in _QUOTED_KEYS_LOWER


# server-configuration.wiki:17: the description is "max length 255 characters".
DESCRIPTION_LIMIT = 255

# server-configuration.wiki:38-39: "SystemTime", or a time "in
# "YYYY/MM/DD/HH/MM" format, e.g "2015/4/8/17/23"".
SYSTEM_TIME = "SystemTime"
_SERVER_TIME = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})/(\d{1,2})/(\d{1,2})")


def is_server_time(value: str) -> bool:
    """Whether *value* is a ``serverTime`` the wiki describes: ``SystemTime``
    or a real date and time written YYYY/MM/DD/HH/MM."""
    if value == SYSTEM_TIME:
        return True
    match = _SERVER_TIME.fullmatch(value)
    if match is None:
        return False
    try:
        datetime(*(int(part) for part in match.groups()))
    except ValueError:
        return False
    return True


def write_cfg_value(path: Path, key: str, value: str, *, quoted: bool | None = None) -> None:
    """Replace a key's value in-place, preserving file structure.

    If the key doesn't exist, it is appended before the last closing brace or at EOF.
    Raises ``CfgValueError`` for a value ``cfg_value_problem`` refuses, before
    anything is written; every writer of a root-level key comes through here.
    *quoted* left out writes the key as Bohemia's file does
    (``quoted_like_stock``): a string in quotes, a number or a boolean bare.
    """
    value = str(value)
    if quoted is None:
        quoted = quoted_like_stock(key)
    problem = cfg_value_problem(value, quoted=quoted)
    if problem:
        raise CfgValueError(f"{key} {problem}.")
    if not path.exists():
        return
    val_str = f'"{value}"' if quoted else value
    _replace_or_append(path, key, val_str)


# A root array, `key[] = { "a", "b" };`, which can span lines. In a line's
# code (`_mission_code`) a string is its two quotes with spaces between.
_CODE_STRING = re.compile(r'"[^"]*"')


def _array_pattern(key: str) -> re.Pattern[str]:
    return re.compile(rf"(?P<lead>\s*)\b{re.escape(key)}\s*\[\s*\]\s*=\s*\{{", re.IGNORECASE)


def _find_array(lines: list[str], key: str) -> tuple[int, int, int, int] | None:
    """Where the root-level array *key*[] sits in *lines*: (first line, column
    of the key, last line, column after its `;`), or None when the file has
    none or never closes it. Braces are counted in the code only, so a brace
    inside a message or a comment is text."""
    code = list(_mission_code(lines))
    pattern = _array_pattern(key)
    for i, line_code in _root_lines(lines):
        match = pattern.search(line_code)
        if match is None:
            continue
        depth, col = 1, match.end()
        for j in range(i, len(code)):
            text = code[j]
            while col < len(text):
                ch = text[col]
                col += 1
                depth += (ch == "{") - (ch == "}")
                if depth == 0:
                    semicolon = re.match(r"[ \t]*;", text[col:])
                    return i, match.end("lead"), j, col + (semicolon.end() if semicolon else 0)
            col = 0
        return None
    return None


def read_cfg_list(path: Path, key: str) -> list[str] | None:
    """The strings of the root-level array *key*[] (``motd[]``), or None when
    the file or the array is not there."""
    if not path.exists():
        return None
    lines = read_text_lossy(path, "utf-8-sig").splitlines(keepends=True)
    found = _find_array(lines, key)
    if found is None:
        return None
    first, start, last, end = found
    code = list(_mission_code(lines))
    items = []
    for k in range(first, last + 1):
        low = start if k == first else 0
        high = end if k == last else len(lines[k])
        for m in _CODE_STRING.finditer(code[k], low, high):
            items.append(lines[k][m.start() + 1 : m.end() - 1].replace('""', '"'))
    return items


def write_cfg_list(path: Path, key: str, items: list[str]) -> None:
    """Write the root-level array *key*[] as one line of quoted strings, in
    place of the one the file has, or before `class Missions`. Each item is
    refused the way a quoted value is (``cfg_value_problem``), before anything
    is written."""
    for item in items:
        problem = cfg_value_problem(item, quoted=True)
        if problem:
            raise CfgValueError(f"A line of {key}[] {problem}.")
    if not path.exists():
        return
    body = ", ".join(f'"{item}"' for item in items)
    text = f"{key}[] = {{ {body} }};" if items else f"{key}[] = {{}};"
    lines, eol = _lines_to_rewrite(path)
    found = _find_array(lines, key)
    if found is None:
        _insert_root_line(lines, text + eol, eol)
    else:
        first, start, last, end = found
        lines[first : last + 1] = [lines[first][:start] + text + lines[last][end:]]
    _write_lines(path, lines)


#: A serverDZ.cfg key that replaced an older one, ``{new key: old key}``.
#: DayZ 1.30 brought thirdPersonView (0 off, 1 on, 2 only in a vehicle) in place
#: of disable3rdPerson, and Bohemia's 1.30 serverDZ.cfg ships only the new key.
#: The wiki does not say how the two combine, so a write of the new key takes
#: the old one out of the file (replaced_keys).
REPLACED_CFG_KEYS: dict[str, str] = {"thirdPersonView": "disable3rdPerson"}
#: The DayZ version that replaced each old key, for the form's "Before 1.30" pill.
REPLACED_IN: dict[str, str] = {"disable3rdPerson": "1.30"}


def replaced_keys(written: Iterable[str]) -> set[str]:
    """The old keys that a write of *written* takes out of serverDZ.cfg
    (REPLACED_CFG_KEYS). A caller drops them from its own writes, so a body
    with both keys never writes the old one back."""
    return {REPLACED_CFG_KEYS[key] for key in written if key in REPLACED_CFG_KEYS}


def remove_cfg_key(path: Path, key: str) -> None:
    """Remove a root-level key's line from the cfg file entirely."""
    if not path.exists():
        return
    pattern = _write_pattern(key)
    lines, _eol = _lines_to_rewrite(path)
    drop = {i for i, code in _root_lines(lines) if pattern.search(code)}
    _write_lines(path, [line for i, line in enumerate(lines) if i not in drop])


def get_available_missions(server_dir: Path) -> list[str]:
    """List subdirectories in mpmissions/."""
    mp_dir = server_dir / "mpmissions"
    if not mp_dir.is_dir():
        return []
    return sorted(
        d.name for d in mp_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )


# What reading the mission walks through on a line's code: a class name, a
# brace, or the `template` key.
_MISSION_CODE = re.compile(
    r"\bclass\s+(?P<cls>\w+)|(?P<open>\{)|(?P<close>\})|\btemplate\s*=", re.IGNORECASE
)
_TEMPLATE_VALUE = re.compile(rf"template\s*=\s*(?P<value>{_VALUE})\s*;", re.IGNORECASE)


@dataclass
class _MissionSpot:
    """Where serverDZ.cfg's mission is, by line index into the lines walked."""

    #: The line and match of the ``template`` DayZ loads, if there is one.
    template: tuple[int, re.Match[str]] | None = None
    #: (line, column) of the brace closing ``class DayZ`` in ``class Missions``.
    dayz_close: tuple[int, int] | None = None
    #: (line, column) of the brace closing the root ``class Missions``.
    missions_close: tuple[int, int] | None = None


def _find_mission(lines: list[str]) -> _MissionSpot:
    """Walk *lines* as DayZ reads ``class Missions { class DayZ { template } }``.

    Only that key counts: a root key whose value says "template"
    (``hostname = "Best template server";``, which sits above class Missions
    in Bohemia's file), a line commented out with ``//`` or ``/* */``, or a
    ``template`` anywhere else is not the mission. The walk stops at the
    first template inside ``Missions >> DayZ``; until then it notes where
    those two classes close.
    """
    spot = _MissionSpot()
    blocks: list[str] = []  # the class each open brace belongs to, "" for none
    pending = ""
    for i, (line, code) in enumerate(zip(lines, _mission_code(lines))):
        for m in _MISSION_CODE.finditer(code):
            if m["cls"]:
                pending = m["cls"].lower()
            elif m["open"]:
                blocks.append(pending)
                pending = ""
            elif m["close"]:
                if blocks == ["missions", "dayz"] and spot.dayz_close is None:
                    spot.dayz_close = (i, m.start())
                elif blocks == ["missions"] and spot.missions_close is None:
                    spot.missions_close = (i, m.start())
                if blocks:
                    blocks.pop()
            elif blocks == ["missions", "dayz"]:
                value = _TEMPLATE_VALUE.match(line, m.start())
                if value:
                    spot.template = (i, value)
                    return spot
    return spot


def _template_of(text: str) -> str | None:
    """The mission *text* names (`_find_mission`), or None when it names
    none or an empty one."""
    spot = _find_mission(text.splitlines())
    if spot.template is None:
        return None
    return _value_of(spot.template[1]).strip() or None


def read_mission_template(path: Path) -> str | None:
    """The ``template`` of ``class Missions { class DayZ { … }; };``, as DayZ
    reads it (`_find_mission`), or None."""
    if not path.exists():
        return None
    return _template_of(read_text_lossy(path))


def set_mission_template(path: Path, template: str) -> None:
    """Make *template* the mission serverDZ.cfg loads: the ``template`` inside
    ``class Missions { class DayZ { … } }``, found by the same walk as
    `read_mission_template`, so the value written is the value read back.

    The value is written quoted, as Bohemia's file has it, whether the old
    one was quoted or bare. A ``template`` anywhere else (at the root, in
    another class, in a comment or inside a string) is left alone: DayZ does
    not read it. When the file has no such template, the missing part is
    added, so a map switch never reports a mission it did not set: the line
    into an existing ``class DayZ``, a ``class DayZ`` into an existing
    ``class Missions``, or Bohemia's whole block at the end of the file. A
    missing file is left missing, as before.
    """
    if not path.exists():
        return
    lines, eol = _lines_to_rewrite(path)
    spot = _find_mission(lines)
    quoted = f'"{template}"'
    # Spliced in by position, never through re.sub, so a backslash or a
    # "\g<0>" / "\1" in the name goes in literally (see _replace_or_append).
    if spot.template is not None:
        i, value = spot.template
        start, end = value.span("value")
        lines[i] = lines[i][:start] + quoted + lines[i][end:]
    elif spot.dayz_close is not None:
        _insert_before_brace(lines, spot.dayz_close, f"template = {quoted};", eol)
    elif spot.missions_close is not None:
        _insert_before_brace(lines, spot.missions_close, f"class DayZ {{ template = {quoted}; }};", eol)
    else:
        _end_last_line(lines, eol)
        block = ["class Missions", "{", "\tclass DayZ", "\t{", f"\t\ttemplate = {quoted};", "\t};", "};"]
        lines.extend(line + eol for line in block)
    _write_lines(path, lines)


def _insert_before_brace(lines: list[str], at: tuple[int, int], statement: str, eol: str) -> None:
    """Put *statement* inside the block whose closing brace is at *at*: on a
    line of its own, one indent deeper, when the brace starts its line; else
    on the brace's line, before it."""
    i, col = at
    line = lines[i]
    before = line[:col]
    if before.strip():
        lines[i] = f"{before.rstrip()} {statement} {line[col:]}"
    else:
        deeper = before + (before or "\t")
        lines[i] = f"{deeper}{statement}{eol}{line}"


# ---------------------------------------------------------------------------
# Structure check (plans/51 C)
# ---------------------------------------------------------------------------
#
# A Raw save or an Upload replaces the live serverDZ.cfg, and until this ran
# nothing looked at a `.cfg` at all: test pass 2 uploaded `garbage garbage`
# and got a 200 (N12). This is the grammar the wiki's own serverDZ.cfg uses
# (dayz-docs/wiki/pages/server-configuration.wiki): `key = value;`,
# `key[] = { … };` arrays (which may span lines and nest), nested
# `class Name { … };`, `//` and `/* */` comments. Deliberately lenient where
# the engine is: a value may be several bare words (`hostname = My Server;`),
# a string escapes a quote by doubling it, `#` lines are skipped. Strict only
# where a mistake changes meaning: every statement ends in `;`, braces
# balance, and the file still names a mission where DayZ reads it
# (`_find_mission`). Required keys are not enforced, by design.

_CFG_TOKEN = re.compile(
    r"""
    (?P<ws>[ \t\r\f\v]+)
  | (?P<nl>\n)
  | (?P<line_comment>//[^\n]*)
  | (?P<block_comment>/\*.*?\*/)
  | (?P<open_comment>/\*)
  | (?P<string>"(?:[^"\n]|"")*")
  | (?P<number>[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<ident>[A-Za-z_][A-Za-z0-9_.]*)
  | (?P<op>\+=|[=;{}\[\],:])
  | (?P<other>[^\s"=;{}\[\],]+)
    """,
    re.VERBOSE | re.DOTALL,
)


class _CfgError(ValueError):
    pass


def _cfg_tokens(text: str) -> list[tuple[str, str, int]]:
    """(kind, text, line) for everything but whitespace and comments."""
    tokens: list[tuple[str, str, int]] = []
    line, pos = 1, 0
    at_line_start = True
    while pos < len(text):
        if at_line_start and text[pos] == "#":
            end = text.find("\n", pos)  # a preprocessor line; not ours to judge
            pos = len(text) if end < 0 else end
            continue
        m = _CFG_TOKEN.match(text, pos)
        if m is None:  # only an unterminated string gets here
            raise _CfgError(f"Line {line}: this string is never closed.")
        kind, value = m.lastgroup, m.group()
        if kind == "open_comment":
            raise _CfgError(f"Line {line}: this comment is never closed.")
        if kind not in ("ws", "nl", "line_comment", "block_comment"):
            tokens.append((kind, value, line))
        if kind != "ws":
            at_line_start = kind == "nl"
        line += value.count("\n")
        pos = m.end()
    return tokens


class _CfgParser:
    def __init__(self, tokens: list[tuple[str, str, int]]) -> None:
        self.tokens = tokens
        self.i = 0

    def _peek(self) -> tuple[str, str, int] | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def _line(self) -> int:
        tok = self._peek() or (self.tokens[-1] if self.tokens else None)
        return tok[2] if tok else 1

    def _expect(self, text: str, after: str) -> None:
        tok = self._peek()
        if tok is None or tok[1] != text:
            found = f"'{tok[1]}'" if tok else "the end of the file"
            raise _CfgError(f"Line {self._line()}: expected '{text}' {after}, found {found}.")
        self.i += 1

    def body(self, depth: int) -> dict[str, object]:
        """Statements up to the closing brace (or the end, at depth 0)."""
        found: dict[str, object] = {}
        while True:
            tok = self._peek()
            if tok is None:
                if depth:
                    raise _CfgError(f"Line {self._line()}: a class or array is never closed with '}}'.")
                return found
            if tok[1] == "}":
                if not depth:
                    raise _CfgError(f"Line {tok[2]}: '}}' closes nothing.")
                return found
            if tok[1] == ";":  # a stray empty statement is harmless
                self.i += 1
                continue
            if tok[0] != "ident":
                raise _CfgError(f"Line {tok[2]}: expected a setting name, found '{tok[1]}'.")
            self.i += 1
            if tok[1].lower() == "class":
                name = self._peek()
                if name is None or name[0] != "ident":
                    raise _CfgError(f"Line {tok[2]}: 'class' needs a name.")
                self.i += 1
                if self._peek() and self._peek()[1] == ":":  # class A : B
                    self.i += 2
                if self._peek() and self._peek()[1] == ";":  # forward declaration
                    self.i += 1
                    continue
                self._expect("{", f"after class {name[1]}")
                found[name[1].lower()] = self.body(depth + 1)
                self._expect("}", f"to close class {name[1]}")
                self._expect(";", f"after the '}}' of class {name[1]}")
            elif self._peek() and self._peek()[1] == "[":
                self.i += 1
                self._expect("]", f"after {tok[1]}[")
                op = self._peek()
                if op is None or op[1] not in ("=", "+="):
                    raise _CfgError(f"Line {self._line()}: expected '=' after {tok[1]}[].")
                self.i += 1
                self._expect("{", f"to open the array {tok[1]}[]")
                self._array(tok[1])
                self._expect(";", f"after the array {tok[1]}[]")
            else:
                self._expect("=", f"after '{tok[1]}'")
                found[tok[1].lower()] = self._value(tok[1], tok[2])
        return found

    def _value(self, key: str, key_line: int) -> str:
        parts: list[str] = []
        while True:
            tok = self._peek()
            if tok is None:
                raise _CfgError(f"Line {key_line}: {key} has no ';' at the end.")
            if tok[1] == ";":
                self.i += 1
                if not parts:
                    raise _CfgError(f"Line {tok[2]}: {key} has no value.")
                return " ".join(parts)
            if tok[1] in ("=", "{", "}", "[", "]"):
                # Usually the ';' is missing and the next statement ran on.
                if tok[2] != key_line:
                    raise _CfgError(f"Line {key_line}: {key} has no ';' at the end.")
                raise _CfgError(
                    f"Line {tok[2]}: expected ';' after the value of {key}, found '{tok[1]}'."
                )
            parts.append(tok[1][1:-1].replace('""', '"') if tok[0] == "string" else tok[1])
            self.i += 1

    def _array(self, key: str) -> None:
        """Items up to and including the closing brace; nested arrays allowed."""
        while True:
            tok = self._peek()
            if tok is None:
                raise _CfgError(f"Line {self._line()}: the array {key}[] is never closed with '}}'.")
            self.i += 1
            if tok[1] == "}":
                return
            if tok[1] == "{":
                self._array(key)
            elif tok[1] in ("=", ";", "[", "]", "+="):
                raise _CfgError(f"Line {tok[2]}: '{tok[1]}' inside the array {key}[].")


def check_server_cfg(text: str) -> str | None:
    """What is structurally wrong with a serverDZ.cfg, or ``None`` when fine.

    The message names the line. See the section comment above for how strict
    this is and why.
    """
    text = text.lstrip("\ufeff")
    try:
        _CfgParser(_cfg_tokens(text)).body(0)
    except _CfgError as exc:
        return str(exc)
    # The mission as read_mission_template reads it, so the check refuses
    # exactly the files a map switch and the status would find no mission in.
    if _template_of(text) is None:
        return (
            "No mission is set. serverDZ.cfg must keep "
            'class Missions { class DayZ { template = "<mission>"; }; }; '
            "— the server does not start without it."
        )
    return None


# Field help text (shared with the web UI, which strips the Rich
# console markup; see app/web/editors/_help.py).
# The unit of a serverDZ.cfg number, shown in the web form's box beside the
# value (webui UnitInput.jsx). Taken from FIELD_DESCRIPTIONS below.
FIELD_UNITS: dict[str, str] = {
    **dict.fromkeys(
        ("respawnTime", "motdInterval", "logAverageFps", "logMemory", "logPlayers", "networkObjectBatchLogSlow"),
        "seconds",
    ),
    **dict.fromkeys(
        ("networkRangeClose", "networkRangeNear", "networkRangeFar", "networkRangeDistantEffect",
         "defaultVisibility", "defaultObjectViewDistance"),
        "meters",
    ),
    **dict.fromkeys(("pingWarning", "pingCritical", "MaxPing"), "ms"),
}

FIELD_DESCRIPTIONS: dict[str, str] = {
    "hostname": "Server name displayed in the server browser\n\n[bold]hostname[/bold]",
    "description": "Server description shown to users in the client server browser. Max 255 characters\n\n[bold]description[/bold]",
    "password": "Password required to connect to the server. Leave empty for no password\n\n[bold]password[/bold]",
    "passwordAdmin": "Password to become a server admin\n\n[bold]passwordAdmin[/bold]",
    "enableWhitelist": "Enable/disable whitelist\n\n[bold]enableWhitelist[/bold]\n0 = disabled, 1 = enabled",
    "maxPlayers": "Maximum number of players\n\n[bold]maxPlayers[/bold] [dim]example: 60[/dim]",
    "verifySignatures": "Verifies .pbos against .bisign files. Only 2 is supported\n\n[bold]verifySignatures[/bold] [dim]example: 2[/dim]\n",
    "forceSameBuild": "When enabled, only clients with the same .exe revision can connect\n\n[bold]forceSameBuild[/bold]",
    "disableVoN": "Enable/disable voice over network\n\nSwitch ON = VoN enabled (cfg sense inverted)\n\n[bold]disableVoN[/bold]\n0 = VoN enabled, 1 = VoN disabled",
    "vonCodecQuality": "Voice over network codec quality, higher is better. 0-30: the serverDZ.cfg DayZ 1.29 ships says 0-30, Bohemia's wiki says 0-20\n\n[bold]vonCodecQuality[/bold] [dim]example: 20[/dim]",
    "disable3rdPerson": "Toggles 3rd person view\n\nSwitch ON = 3rd person enabled (cfg sense inverted)\n\n[bold]disable3rdPerson[/bold]\n0 = enabled, 1 = disabled",
    "thirdPersonView": "3rd person view, added in DayZ 1.30. 0 = off, 1 = on, 2 = only while in a vehicle\n\nBohemia's 1.30 serverDZ.cfg ships it (set to 1) in place of disable3rdPerson. A server older than 1.30 ignores it. Bohemia has not documented how it combines with disable3rdPerson\n\n[bold]thirdPersonView[/bold]",
    "disableCrosshair": "Toggles the crosshair\n\nSwitch ON = crosshair enabled (cfg sense inverted)\n\n[bold]disableCrosshair[/bold]\n0 = enabled, 1 = disabled",
    "serverTime": "Initial in-game time. 'SystemTime' for local machine time, or 'YYYY/MM/DD/HH/MM' format\n\n[bold]serverTime[/bold]",
    "serverTimeAcceleration": "Time multiplier (0.1-64). Set to 24 = full day in one hour\n\n[bold]serverTimeAcceleration[/bold] [dim]example: 1[/dim]",
    "serverNightTimeAcceleration": "Night time multiplier (0.1-64), also multiplied by [bold]Time acceleration[/bold] (serverTimeAcceleration)\n\n[bold]serverNightTimeAcceleration[/bold] [dim]example: 1[/dim]",
    "serverTimePersistent": "Save server time to storage. Next start uses saved time\n\n[bold]serverTimePersistent[/bold] [dim]example: 0[/dim]",
    "loginQueueConcurrentPlayers": "Players processed concurrently during login. Prevents performance drop\n\n[bold]loginQueueConcurrentPlayers[/bold] [dim]example: 5[/dim]",
    "loginQueueMaxPlayers": "Maximum players in login queue\n\n[bold]loginQueueMaxPlayers[/bold] [dim]example: 500[/dim]",
    "instanceId": "Server instance ID. The world is saved in storage_<this number> inside the mission folder, so a new number starts an empty world and leaves the old one in its folder\n\n[bold]instanceId[/bold] [dim]example: 1[/dim]",
    "storageAutoFix": "Check and replace corrupted persistence files with empty ones\n\n[bold]storageAutoFix[/bold] [dim]example: 1[/dim]",
    "respawnTime": "Respawn delay in seconds before player can get a new character\n\n[bold]respawnTime[/bold] [dim]example: 5[/dim]",
    "motd": "Messages shown in the in-game chat, one per line\n\n[bold]motd[/bold]",
    "motdInterval": "Time in seconds between the messages of the day in the in-game chat\n\n[bold]motdInterval[/bold] [dim]example: 1[/dim]",
    "logAverageFps": "How often, in seconds, the server writes its average FPS to the .RPT (absent or 0 = never). Zedstead's FPS history reads that line. Needs [bold]-doLogs[/bold] in [@click=screen.open_run_command][bold underline]Run command[/bold underline][/]\n\n[bold]logAverageFps[/bold] [dim]default: not set[/dim]",
    "logMemory": "Log server memory usage (seconds interval). Needs [bold]-doLogs[/bold] in [@click=screen.open_run_command][bold underline]Run command[/bold underline][/]\n\n[bold]logMemory[/bold] [dim]example: 1[/dim]",
    "logPlayers": "Log connected player count (seconds interval). Needs [bold]-doLogs[/bold] in [@click=screen.open_run_command][bold underline]Run command[/bold underline][/]\n\n[bold]logPlayers[/bold] [dim]example: 1[/dim]",
    "logFile": "File the server console log is saved to, in the folder with the other server logs\n\n[bold]logFile[/bold] [dim]example: server_console.log[/dim]",
    # The four adminLog keys are read only by the game's scripts, through
    # ServerConfigGetInt, which "Returns 0 if not found" (Bohemia's
    # DayZ-Script-Diff, 1.30.164289: scripts/3_game/global/game.c:1138 and
    # scripts/4_world/plugins/pluginbase/pluginadminlog.c:44-47). So 0 is the default.
    "adminLogPlayerHitsOnly": "1 = log player hits only, 0 = log all hits including animals/infected\n\n[bold]adminLogPlayerHitsOnly[/bold] [dim]default: 0[/dim]",
    "adminLogPlacement": "Log placement actions (traps, tents)\n\n[bold]adminLogPlacement[/bold] [dim]default: 0[/dim]",
    "adminLogBuildActions": "Log basebuilding actions (build, dismantle, destroy)\n\n[bold]adminLogBuildActions[/bold] [dim]default: 0[/dim]",
    "adminLogPlayerList": "Log periodic player list with position every 5 minutes\n\n[bold]adminLogPlayerList[/bold] [dim]default: 0[/dim]",
    # Default 0, not the 1 in the wiki's example file: the game reads it through
    # ServerConfigGetInt, which returns 0 for a missing key (Bohemia's
    # DayZ-Script-Diff 1.30.164289, scripts/5_mission/mission/missionserver.c:164).
    "enableDebugMonitor": "Show debug info window on screen\n\n[bold]enableDebugMonitor[/bold] [dim]default: 0[/dim]",
    "steamQueryPort": "Steam A2S query port. Used by server browser visibility and doctor checks\n\n[bold]steamQueryPort[/bold] [dim]example: 27016[/dim]",
    "allowFilePatching": "Allow clients with -filePatching launch parameter\n\n[bold]allowFilePatching[/bold] [dim]example: 1[/dim]",
    "simulatedPlayersBatch": "Players simulated per frame for performance\n\n[bold]simulatedPlayersBatch[/bold] [dim]example: 20[/dim]",
    "multithreadedReplication": "Multi-threaded replication. Workers derived from dayzSettings.xml\n\n[bold]multithreadedReplication[/bold] [dim]example: 1[/dim]",
    "speedhackDetection": "Speedhack detection strictness. 1 = strict, 10 = lenient\n\n[bold]speedhackDetection[/bold] [dim]example: 1[/dim]",
    "networkRangeClose": "Network bubble for close objects with items (meters)\n\n[bold]networkRangeClose[/bold] [dim]default: 20[/dim]",
    "networkRangeNear": "Network bubble for near inventory items (meters)\n\n[bold]networkRangeNear[/bold] [dim]default: 150[/dim]",
    "networkRangeFar": "Network bubble for far objects (meters)\n\n[bold]networkRangeFar[/bold] [dim]default: 1000[/dim]",
    "networkRangeDistantEffect": "Network bubble for effects/sounds (meters)\n\n[bold]networkRangeDistantEffect[/bold] [dim]default: 4000[/dim]",
    "networkObjectBatchSendCreate": "Maximum objects sent to clients for creation per server frame\n\n[bold]networkObjectBatchSendCreate[/bold] [dim]example: 10[/dim]",
    "networkObjectBatchSendDelete": "Maximum objects sent to clients for deletion per server frame\n\n[bold]networkObjectBatchSendDelete[/bold] [dim]example: 10[/dim]",
    "networkObjectBatchCompute": "Objects in the create/destroy lists checked per server frame\n\n[bold]networkObjectBatchCompute[/bold] [dim]example: 1000[/dim]",
    "networkObjectBatchLogSlow": "Longest time, in seconds, a network bubble may take to go through its objects before the server logs it to the console\n\n[bold]networkObjectBatchLogSlow[/bold] [dim]example: 5[/dim]",
    "networkObjectBatchEnforceBandwidthLimits": "Limit how fast objects are created for a client, from that connection's bandwidth statistics\n\n[bold]networkObjectBatchEnforceBandwidthLimits[/bold] [dim]example: 1[/dim]",
    "networkObjectBatchUseEstimatedBandwidth": "How a connection's bandwidth use is found. 0 = the data actually sent since the last server frame, 1 = a crude estimate\n\n[bold]networkObjectBatchUseEstimatedBandwidth[/bold] [dim]example: 0[/dim]",
    "networkObjectBatchUseDynamicMaximumBandwidth": "1 = the bandwidth limit is a fraction of the most that can be sent, which changes with demand. 0 = the limit is a hard number\n\n[bold]networkObjectBatchUseDynamicMaximumBandwidth[/bold] [dim]example: 1[/dim]",
    "networkObjectBatchBandwidthLimit": "The bandwidth limit itself: a fraction from 0 to 1 when [bold]Use dynamic maximum bandwidth[/bold] is on, a value of 1 or more when it is off\n\n[bold]networkObjectBatchBandwidthLimit[/bold] [dim]example: 0.8[/dim]",
    "enableCfgGameplayFile": "Read the mission's cfggameplay.json. Without it the whole file is ignored and every gameplay setting is the game's default\n\n[bold]enableCfgGameplayFile[/bold] [dim]default: 0[/dim]",
    "defaultVisibility": "Highest terrain render distance on server (meters)\n\n[bold]defaultVisibility[/bold] [dim]example: 1375[/dim]",
    "defaultObjectViewDistance": "Highest object render distance on server (meters)\n\n[bold]defaultObjectViewDistance[/bold] [dim]example: 1375[/dim]",
    "lightingConfig": "0 = brighter night, 1 = darker night, 2 = Sakhal lighting. A terrain mod can bring its own value: Namalsk's cfgs set 223 (Regular) and 222 (Hardcore)\n\n[bold]lightingConfig[/bold] [dim]default: 0[/dim]",
    # Default 0, not the 1 in the wiki's example file: the game reads it through
    # ServerConfigGetInt, which returns 0 for a missing key (Bohemia's
    # DayZ-Script-Diff 1.30.164289, scripts/5_mission/mission/missionserver.c:470).
    "disablePersonalLight": "Disable personal light for all clients\n\nSwitch ON = personal light enabled (cfg sense inverted)\n\n[bold]disablePersonalLight[/bold] [dim]default: 0[/dim]",
    "disableBaseDamage": "Disable damage to fences and watchtowers\n\nSwitch ON = base damage enabled (cfg sense inverted)\n\n[bold]disableBaseDamage[/bold] [dim]default: 0[/dim]",
    "disableContainerDamage": "Disable damage to tents, barrels, crates, seachests\n\nSwitch ON = container damage enabled (cfg sense inverted)\n\n[bold]disableContainerDamage[/bold] [dim]default: 0[/dim]",
    "disableRespawnDialog": "Disable respawn dialog, spawn as random\n\nSwitch ON = respawn dialog enabled (cfg sense inverted)\n\n[bold]disableRespawnDialog[/bold] [dim]default: 0[/dim]",
    "pingWarning": "Ping value (ms) for yellow ping warning\n\n[bold]pingWarning[/bold] [dim]example: 200[/dim]",
    "pingCritical": "Ping value (ms) for red ping warning\n\n[bold]pingCritical[/bold] [dim]example: 250[/dim]",
    "MaxPing": "Ping value (ms) to kick player\n\n[bold]MaxPing[/bold] [dim]example: 300[/dim]",
    "serverFpsWarning": "Server FPS value for warning. Minimum: 11\n\n[bold]serverFpsWarning[/bold] [dim]example: 15[/dim]",
    "disableBanlist": "ban.txt\n\nSwitch ON = ban list used (cfg sense inverted)\n\n[bold]disableBanlist[/bold] [dim]default: false[/dim]",
    "disablePrioritylist": "priority.txt\n\nSwitch ON = priority list used (cfg sense inverted)\n\n[bold]disablePrioritylist[/bold] [dim]default: false[/dim]",
    "disableMultiAccountMitigation": "Disable multi-account mitigation on consoles\n\nSwitch ON = multi-account mitigation enabled (cfg sense inverted)\n\n[bold]disableMultiAccountMitigation[/bold] [dim]default: false[/dim]",
    "guaranteedUpdates": "Communication protocol used with the game server. Bohemia: use only 1\n\n[bold]guaranteedUpdates[/bold] [dim]example: 1[/dim]",
    "timeStampFormat": "Timestamp format in .rpt file: 'Full' or 'Short'\n\n[bold]timeStampFormat[/bold] [dim]example: Short[/dim]",
    "shotValidation": "Enable shot validation\n\n[bold]shotValidation[/bold] [dim]example: 1[/dim]",
    "clientPort": "Force client connection port\n\n[bold]clientPort[/bold] [dim]example: 2304[/dim]",
    "BattlEye": "Enable BattlEye anti-cheat\n\n[bold]BattlEye[/bold] [dim]example: 1[/dim]",
    "template": "Mission template folder name used by the server\n\nHeads-up: You can change this value but use the [bold]Change Map[/bold] feature instead\n\n[bold]template[/bold]",
}
