"""priority_ops — read/write/CRUD for priority.txt.

priority.txt lives at the server install root. It is a semicolon-delimited
list of Steam64 IDs that DayZ lets jump the login queue. The serverDZ.cfg
key ``disablePrioritylist`` toggles whether the engine reads the file.

Format: ``76561198000000001;76561198000000002;76561198000000003``
(dayz-docs/wiki/pages/server-configuration.wiki:202) — no surrounding
whitespace; trailing semicolons are tolerated on read.

An add or a remove **edits the file's lines and never regenerates it**
(2026 Q4), the way whitelist.txt is edited: a ``//`` comment, a token
that is not a Steam64 ID, a blank line, the line endings and the final
newline (or its absence) all stay as they were. The rewrite this replaced
wrote the file back from the IDs it could parse, which dropped every one of
those. The line editor below is shared with ``whitelist_ops``.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.shared.json_utils import NotUtf8Error, atomic_write_text, read_text_to_rewrite


# fullmatch, never match: "$" also matches before a trailing "\n", which put a
# blank line and a second copy of the ID into whitelist.txt (plans/68 LB-8).
# [0-9], not \d, which also matches non-ASCII digits.
_STEAM64_RE = re.compile(r"7656119[0-9]{10}")


# Any run of separators: Bohemia's semicolons, commas, or line breaks and the
# whitespace around them. Written wide on purpose: the cost of tolerating a
# hand-edited variant is nothing, while refusing to read one looks to the
# admin like zsm lost their list.
_SPLIT_RE = re.compile(r"[;,\s]+")


def is_valid_steam64(value: str) -> bool:
    """True iff *value* is a 17-digit Steam64 individual ID (76561190000000000+)."""
    return bool(_STEAM64_RE.fullmatch(value))


def read_raw(path: Path, *, lossy: bool = False) -> str:
    """The file as text with its line endings intact (Bohemia's are CRLF).

    A file that is not UTF-8 (a comment typed in another encoding) raises
    ``NotUtf8Error`` for the callers that write the text back. *lossy* is for
    the ones that only list its IDs: what cannot be read becomes U+FFFD.
    """
    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        if not lossy:
            raise NotUtf8Error(path) from None
        return data.decode("utf-8-sig", errors="replace")


def split_comment(line: str) -> tuple[str, str]:
    """``"ID\t//note"`` → ``("ID\t", "//note")``."""
    i = line.find("//")
    return (line, "") if i < 0 else (line[:i], line[i:])


def tokens(code: str) -> list[str]:
    return [tok for tok in _SPLIT_RE.split(code) if tok]


def listed_ids(path: Path, is_id) -> list[str]:
    """The tokens *is_id* accepts, in file order, outside ``//`` comments.
    Missing file → ``[]``."""
    if not path.is_file():
        return []
    return [
        tok
        for line in read_raw(path, lossy=True).splitlines()
        for tok in tokens(split_comment(line)[0])
        if is_id(tok)
    ]


def id_comments(path: Path, is_id) -> dict[str, str]:
    """The comment after each ID *is_id* accepts, without its ``//``, for
    the IDs whose line has one. Missing file → ``{}``."""
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in read_raw(path, lossy=True).splitlines():
        code, comment = split_comment(line)
        note = comment[2:].strip()
        if not note:
            continue
        for tok in tokens(code):
            if is_id(tok):
                out.setdefault(tok, note)
    return out


def _write_lines(path: Path, text: str) -> None:
    # Atomic: a half-written list drops everyone past the truncation point
    # (a queue skip here, a kick from a whitelist). ``newline=""`` so CRLF
    # stays CRLF on every OS.
    atomic_write_text(path, text, newline="")


def add_id_line(path: Path, new_id: str, *, join_single: bool) -> None:
    """Add *new_id* to a list file by editing one line.

    It extends the last line that already holds ``A;B`` with ``;new_id``.
    With *join_single* it also extends a file's only ID line (priority.txt's
    documented form is one line); otherwise it gets a line of its own. Every
    other byte stays, and a file without a final newline keeps none.
    """
    text = read_raw(path) if path.is_file() else ""
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    id_lines = [i for i, line in enumerate(lines) if tokens(split_comment(line)[0])]
    joined = [i for i in id_lines if ";" in split_comment(lines[i])[0]]
    if not joined and join_single and len(id_lines) == 1:
        joined = id_lines
    if joined:
        i = joined[-1]
        body = lines[i].rstrip("\r\n")
        code, comment = split_comment(body)
        ids = code.rstrip()
        lines[i] = (
            ids.rstrip(";") + ";" + new_id + code[len(ids):] + comment
            + lines[i][len(body):]
        )
        _write_lines(path, "".join(lines))
    elif text and not text.endswith(("\n", "\r")):
        # No final newline: the new last line has none either.
        _write_lines(path, text + eol + new_id)
    else:
        _write_lines(path, text + new_id + eol)


def remove_id_line(path: Path, old_id: str) -> None:
    """Take *old_id* out of a list file by editing its line.

    A line left with no ID goes, comment and all (the comment was that ID's
    label). Every other line is written back byte for byte, and a file
    without a final newline keeps none.
    """
    text = read_raw(path)
    out = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        code, comment = split_comment(body)
        toks = tokens(code)
        if old_id not in toks:
            out.append(line)
            continue
        rest = [tok for tok in toks if tok != old_id]
        if not rest:
            continue
        sep = ";" if ";" in code else " "
        lead = code[: len(code) - len(code.lstrip())]
        trail = code[len(code.rstrip()):]
        out.append(lead + sep.join(rest) + trail + comment + line[len(body):])
    if out and not text.endswith(("\n", "\r")):
        # The last line went: the one before it ends the file now.
        out[-1] = out[-1].rstrip("\r\n")
    _write_lines(path, "".join(out))


def read_priority_list(path: Path) -> list[str]:
    """Parse priority.txt → list of Steam64 IDs in file order.

    Missing or empty file → ``[]``. BOM tolerated. Whitespace around tokens
    and trailing/leading semicolons are stripped, and ``//`` comments are
    skipped. A line break separates IDs as a semicolon does: a file written
    one ID per line read as one invalid token, so it showed no IDs and the
    next add replaced them all with one. Tokens that fail
    ``is_valid_steam64`` are not listed, and stay in the file.
    """
    return listed_ids(path, is_valid_steam64)


def write_priority_list(path: Path, ids: list[str]) -> None:
    """Write *ids* as ``id1;id2;…\\n`` (no trailing semicolon). Creates parent dirs.

    A whole new file: an add or a remove edits lines instead
    (``add_priority_id``, ``remove_priority_id``). Atomic: a crash mid-write
    must not truncate priority.txt. A file that is not UTF-8 is refused
    (``NotUtf8Error``).
    """
    if path.is_file():
        read_text_to_rewrite(path)
    atomic_write_text(path, ";".join(ids) + "\n")


def add_priority_id(path: Path, steam_id: str) -> bool:
    """Add *steam_id* to priority.txt. Returns ``False`` on duplicate.

    Raises ``ValueError`` if *steam_id* is not a valid Steam64 ID.
    """
    if not is_valid_steam64(steam_id):
        raise ValueError(f"Not a valid Steam64 ID: {steam_id!r}")
    if steam_id in read_priority_list(path):
        return False
    add_id_line(path, steam_id, join_single=True)
    return True


def remove_priority_id(path: Path, steam_id: str) -> bool:
    """Remove *steam_id* from priority.txt. Returns ``False`` if not present."""
    if steam_id not in read_priority_list(path):
        return False
    remove_id_line(path, steam_id)
    return True
