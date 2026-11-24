"""Write a JSON document back over the text it was read from, changing only
what changed.

Bohemia lays its mission JSON out by hand (tabs, CRLF, `"Pos": [ x, y, z ]`,
a stray `{ \\t`), and any serializer reflows all of it: one edited value in
cfgundergroundtriggers.json or cfgEffectArea.json read as a whole-file diff,
and CRLF became LF. Here the old text is parsed with the position of every
value, the new document is compared with it, and only the values that differ
are replaced in the text:

- a scalar that is equal to the file's (``1`` for ``1.0`` included) keeps its
  literal; a changed one is written in the file's own number style (a whole
  number stays whole where the file wrote it so);
- an entry added to or removed from a list or object is inserted or cut on
  its own, at the file's indent and line ending, and everything around it
  stays as it was;
- only a container whose keys were reordered, or whose type changed, is
  written fresh, and then only that container.

A save with no edits therefore gives back the file byte for byte.
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any

from app.shared.json_utils import (
    SourceFloat,
    _auto_snapshot,
    atomic_write_text,
    dumps_json,
    dumps_json_over,
)

_WS = " \t\r\n"
_NUMBER = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?")
_STRING = re.compile(r'"(?:[^"\\]|\\.)*"', re.S)
_WORDS = {"true": True, "false": False, "null": None}


class _Node:
    """One value of the old text: its span and, for a container, its parts.

    An object's ``items`` are ``(key, key_start, node)``; an array's are nodes.
    """

    __slots__ = ("kind", "start", "end", "items", "value")

    def __init__(self, kind: str, start: int) -> None:
        self.kind = kind
        self.start = start
        self.end = start
        self.items: list = []
        self.value: Any = None


def _skip(text: str, i: int) -> int:
    while i < len(text) and text[i] in _WS:
        i += 1
    return i


def _parse(text: str, i: int) -> tuple[_Node, int]:
    i = _skip(text, i)
    if i >= len(text):
        raise ValueError("unexpected end of JSON")
    c = text[i]
    if c in "{[":
        node = _Node("object" if c == "{" else "array", i)
        close = "}" if c == "{" else "]"
        i = _skip(text, i + 1)
        if i < len(text) and text[i] == close:
            node.end = i + 1
            return node, i + 1
        while True:
            if node.kind == "object":
                m = _STRING.match(text, i)
                if not m:
                    raise ValueError(f"expected a key at {i}")
                key_start = i
                i = _skip(text, m.end())
                if i >= len(text) or text[i] != ":":
                    raise ValueError(f"expected ':' at {i}")
                child, i = _parse(text, i + 1)
                node.items.append((json.loads(m.group()), key_start, child))
            else:
                child, i = _parse(text, i)
                node.items.append(child)
            i = _skip(text, i)
            if i < len(text) and text[i] == ",":
                i = _skip(text, i + 1)
                continue
            if i < len(text) and text[i] == close:
                node.end = i + 1
                return node, i + 1
            raise ValueError(f"expected ',' or '{close}' at {i}")
    node = _Node("scalar", i)
    m = _STRING.match(text, i) if c == '"' else _NUMBER.match(text, i)
    if m:
        node.end = m.end()
        node.value = json.loads(m.group(), parse_float=SourceFloat)
        return node, node.end
    for word, value in _WORDS.items():
        if text.startswith(word, i):
            node.end = i + len(word)
            node.value = value
            return node, node.end
    raise ValueError(f"unexpected {c!r} at {i}")


def _same(old: Any, new: Any) -> bool:
    if isinstance(old, bool) or isinstance(new, bool) or old is None or new is None:
        return type(old) is type(new) and old == new
    if isinstance(old, (int, float)) and isinstance(new, (int, float)):
        return old == new
    return type(old) is type(new) and old == new


class _Patcher:
    def __init__(self, text: str, inline_scalar_lists: bool) -> None:
        self.text = text
        self.inline = inline_scalar_lists
        # The ending most of the file's lines have: TraderPlus' own IDs file
        # ships with LF lines and one CRLF at the end, and an entry added to it
        # took CRLF.
        self.nl = "\r\n" if 2 * text.count("\r\n") > text.count("\n") else "\n"
        self.unit = _indent_unit(text)

    def value(self, node: _Node) -> Any:
        return json.loads(self.text[node.start:node.end], parse_float=SourceFloat)

    def line_indent(self, pos: int) -> str:
        line_start = self.text.rfind("\n", 0, pos) + 1
        end = line_start
        while end < pos and self.text[end] in " \t":
            end += 1
        return self.text[line_start:end]

    def fresh(self, value: Any, prev: Any, base: str) -> str:
        """*value* serialized at indent *base*, in the file's unit and newline."""
        lines = dumps_json(
            value, indent=4, inline_scalar_lists=self.inline, previous=prev
        ).split("\n")
        out = [lines[0]]
        for line in lines[1:]:
            stripped = line.lstrip(" ")
            out.append(base + self.unit * ((len(line) - len(stripped)) // 4) + stripped)
        return self.nl.join(out)

    def render(self, node: _Node, new: Any) -> str:
        old_text = self.text[node.start:node.end]
        if node.kind == "scalar":
            old = node.value
            if _same(old, new):
                return old_text
            if (
                isinstance(old, int) and not isinstance(old, bool)
                and isinstance(new, float) and new.is_integer()
            ):
                new = int(new)
            return dumps_json(new, previous=old)
        if node.kind == "object" and isinstance(new, dict):
            return old_text if not new and not node.items else self._object(node, new)
        if node.kind == "array" and isinstance(new, list):
            return old_text if not new and not node.items else self._array(node, new)
        return self.fresh(new, self.value(node), self.line_indent(node.start))

    def _join(self, node: _Node, starts: list[int], ends: list[int], pieces: list) -> str:
        """The container's text with *pieces* between its brackets.

        A piece is ``(old_index or None, text)``. Two old entries that were
        neighbours keep the separator between them; any other pair takes the
        file's first separator.
        """
        text = self.text
        if not starts:
            raise _Fresh
        if len(starts) > 1:
            default_sep = text[ends[0]:starts[1]]
        elif text[node.start + 1:starts[0]].strip(" \t") == "":
            default_sep = ", "
        else:
            default_sep = "," + self.nl + self.line_indent(starts[0])
        out = [text[node.start:starts[0]]]
        for n, (index, piece) in enumerate(pieces):
            if n:
                prev_index = pieces[n - 1][0]
                if prev_index is not None and index == prev_index + 1:
                    out.append(text[ends[prev_index]:starts[index]])
                else:
                    out.append(default_sep)
            out.append(piece)
        out.append(text[ends[-1]:node.end])
        return "".join(out)

    def _object(self, node: _Node, new: dict) -> str:
        members = {key: (key_start, child) for key, key_start, child in node.items}
        old_keys = [key for key, _, _ in node.items]
        kept = [key for key in new if key in members]
        if kept != [key for key in old_keys if key in new] or len(members) != len(old_keys):
            return self.fresh(new, self.value(node), self.line_indent(node.start))
        if not new:
            return "{}"
        if not node.items:
            return self.fresh(new, None, self.line_indent(node.start))
        index = {key: n for n, key in enumerate(old_keys)}
        base = self.line_indent(node.items[0][1])
        pieces = []
        for key, value in new.items():
            if key in members:
                key_start, child = members[key]
                pieces.append((
                    index[key],
                    self.text[key_start:child.start] + self.render(child, value),
                ))
            else:
                pieces.append((
                    None,
                    json.dumps(key, ensure_ascii=False) + ": " + self.fresh(value, None, base),
                ))
        starts = [key_start for _, key_start, _ in node.items]
        ends = [child.end for _, _, child in node.items]
        return self._join(node, starts, ends, pieces)

    def _array(self, node: _Node, new: list) -> str:
        items = node.items
        if not new:
            return "[]"
        if not items:
            return self.fresh(new, None, self.line_indent(node.start))
        if len(items) == len(new):
            pieces = [(n, self.render(child, value)) for n, (child, value) in enumerate(zip(items, new))]
        else:
            old_values = [self.value(child) for child in items]
            matcher = difflib.SequenceMatcher(
                None, [_key(v) for v in old_values], [_key(v) for v in new], autojunk=False
            )
            base = self.line_indent(items[0].start)
            pieces = []
            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag == "equal":
                    pieces += [(i, self.text[items[i].start:items[i].end]) for i in range(i1, i2)]
                    continue
                paired = min(i2 - i1, j2 - j1) if tag == "replace" else 0
                pieces += [(i1 + k, self.render(items[i1 + k], new[j1 + k])) for k in range(paired)]
                pieces += [(None, self.fresh(new[j], None, base)) for j in range(j1 + paired, j2)]
        return self._join(node, [c.start for c in items], [c.end for c in items], pieces)


class _Fresh(Exception):
    pass


def _key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _indent_unit(text: str) -> str:
    """A tab when the file indents with tabs, else its narrowest run of spaces."""
    widths = []
    for line in text.splitlines():
        stripped = line.lstrip(" \t")
        if not stripped or len(stripped) == len(line):
            continue
        lead = line[: len(line) - len(stripped)]
        if lead[0] == "\t":
            return "\t"
        widths.append(len(lead) - len(lead.lstrip(" ")))
    return " " * min(widths) if widths else " " * 4


def dumps_json_patched(text: str, data: Any, *, inline_scalar_lists: bool = False) -> str:
    """*data* written over *text*, the JSON it was read from (module docstring).

    Raises ValueError when *text* is not JSON this reader follows.
    """
    bom = "﻿" if text.startswith("﻿") else ""
    body = text[len(bom):]
    root, end = _parse(body, 0)
    if body[end:].strip(_WS):
        raise ValueError("text after the JSON document")
    patcher = _Patcher(body, inline_scalar_lists)
    try:
        middle = patcher.render(root, data)
    except _Fresh:
        middle = patcher.fresh(data, patcher.value(root), "")
    return bom + body[:root.start] + middle + body[root.end:]


def dumps_json_over_text(path: Path, data: Any, *, inline_scalar_lists: bool = False) -> str:
    """What to write over *path*: :func:`dumps_json_patched` of the file as it
    is now, or :func:`~app.shared.json_utils.dumps_json_over` when the file
    is missing or is not JSON this reader follows."""
    try:
        text = path.read_bytes().decode("utf-8")
        return dumps_json_patched(text, data, inline_scalar_lists=inline_scalar_lists)
    except (OSError, ValueError):
        return dumps_json_over(path, data, inline_scalar_lists=inline_scalar_lists)


def write_json_patched(path: Path, data: Any, *, inline_scalar_lists: bool = False) -> None:
    """Atomically write :func:`dumps_json_over_text`, line endings verbatim.
    Takes no snapshot; the caller does that."""
    atomic_write_text(
        path, dumps_json_over_text(path, data, inline_scalar_lists=inline_scalar_lists), newline=""
    )


def save_json_patched(path: Path, data: Any, *, inline_scalar_lists: bool = False) -> None:
    """Save *data* over *path* the way every editor of a file someone else
    lays out does: a version snapshot, then :func:`write_json_patched`.
    Nothing is written, and no snapshot taken, when the text would not
    change."""
    text = dumps_json_over_text(path, data, inline_scalar_lists=inline_scalar_lists)
    try:
        if path.read_bytes() == text.encode("utf-8"):
            return
    except OSError:
        pass
    _auto_snapshot(path)
    atomic_write_text(path, text, newline="")
