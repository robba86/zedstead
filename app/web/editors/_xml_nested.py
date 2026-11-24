"""XML children as a row's ``nested`` part (plans/76 step 3).

A table row of types.xml, events.xml or cfgspawnabletypes.xml has children a
column cannot show: a type's ``<usage>``/``<value>``/``<tag>`` lists and its
``<flags>``, an event's ``<children>``, a spawnable type's attachment and
cargo groups. Each file maps them to the JSON shape `_nested` already edits
(``JsonTree`` in the browser), through a small explicit shape per file (a
`Part` per key), not a generic XML-to-tree:

- an element's attributes are an object (`attrs_of`), numbers typed, in the
  file's attribute order;
- leaf children are an object of their texts (`texts_of`);
- repeated children are a list (`sync_list`), of objects or of names.

A save goes through `apply_parts`: the row's shape is read from the element,
`_nested.apply_nested` refuses a stale base and fits the edit to that shape
(keys never added or dropped, whole numbers stay whole), and each edited key
is written back **into the elements in place**:

- the tree is never rebuilt, so comments (the ``START/END MOD`` markers that
  ``xml_merge`` matches as text, a parked mod) and every sibling keep their
  place; a list's elements go back into the slots the old ones held;
- a value is written only when it changed, compared as a number, in the
  file's own number style (``0.30`` stays two decimals);
- attributes and child elements the shape does not know stay on their
  element: a list entry that was only moved or edited keeps its element, and
  a copied entry starts as a copy of its source's element.
"""

from __future__ import annotations

import copy
import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.web.editors._nested import NESTED, _same, apply_nested

# Attributes that are names even when they look like a number, and ones that
# take a fraction even where the file wrote a whole number (`chance="1"`).
STRING_ATTRS = frozenset({"name", "type", "preset", "user"})
FLOAT_ATTRS = frozenset({"chance"})

_INT = re.compile(r"[+-]?\d+")
_FLOAT = re.compile(r"[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?")


def is_element(node) -> bool:
    """A real element, not a comment or processing instruction."""
    return isinstance(node.tag, str)


def typed(text: str | None, string: bool = False, decimal: bool = False):
    """An attribute or text as the JSON value the tree shows."""
    text = (text or "").strip()
    if string:
        return text
    if _INT.fullmatch(text) and not decimal:
        return int(text)
    if _FLOAT.fullmatch(text):
        value = float(text)
        if math.isfinite(value):
            return value
    return text


def value_text(value, old: str | None = None) -> str:
    """*value* as the file writes it: a decimal in *old*'s number of places
    when that is still the same number, else the shortest form."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        old = (old or "").strip()
        if "." in old and "e" not in old.lower():
            places = len(old.split(".", 1)[1])
            text = f"{value:.{places}f}"
            if float(text) == value:
                return text
        return repr(value)
    return "" if value is None else str(value)


# ---------------------------------------------------------------------------
# Attributes and texts
# ---------------------------------------------------------------------------


def attrs_of(elem: ET.Element | None, defaults: dict | None = None) -> dict:
    """*elem*'s attributes, typed, in file order; a key of *defaults* the
    element lacks follows with its default (written only when changed)."""
    out = {}
    if elem is not None:
        out = {k: typed(v, k in STRING_ATTRS, k in FLOAT_ATTRS) for k, v in elem.attrib.items()}
    for key, default in (defaults or {}).items():
        out.setdefault(key, default)
    return out


def set_attrs(elem: ET.Element, new: dict, defaults: dict | None = None) -> None:
    """Write each attribute of *new* that differs from what *elem* holds."""
    current = attrs_of(elem, defaults)
    for key, value in new.items():
        if key in current and _same(current[key], value):
            continue
        elem.set(key, value_text(value, elem.get(key)))


def _leaves(elem: ET.Element, skip) -> list[ET.Element]:
    """*elem*'s leaf children (no element children), first of each tag."""
    seen: set[str] = set()
    out = []
    for child in elem:
        if not is_element(child) or child.tag in skip or child.tag in seen:
            continue
        if any(is_element(c) for c in child):
            continue
        seen.add(child.tag)
        out.append(child)
    return out


def texts_of(elem: ET.Element, skip=()) -> dict:
    """The texts of *elem*'s leaf children, by tag, in file order."""
    return {c.tag: typed(c.text) for c in _leaves(elem, skip)}


def set_texts(elem: ET.Element, new: dict, skip=()) -> None:
    """Write each leaf text of *new* that differs from the file's."""
    by_tag = {c.tag: c for c in _leaves(elem, skip)}
    for tag, value in new.items():
        child = by_tag.get(tag)
        if child is None or _same(typed(child.text), value):
            continue
        child.text = value_text(value, child.text)


# ---------------------------------------------------------------------------
# Repeated children
# ---------------------------------------------------------------------------


def _identity(entry):
    """What names a list entry, for matching an edited one to its element."""
    if isinstance(entry, dict):
        for key in ("name", "type", "preset", "user"):
            if entry.get(key) not in (None, ""):
                return (key, entry[key])
        return None
    return ("value", entry)


def _match(old: list, new: list) -> tuple[list, list]:
    """For each new entry, the old entry whose element it keeps (or None), and
    the old entry a new element is copied from (or None for a blank one).

    Equal first (it was only moved), then the same name (it was edited), then
    the same position (edited, name included). An entry left over is a new
    one: a copy of its equal or same-named entry, else of its neighbour.
    """
    keep: list = [None] * len(new)
    used: set[int] = set()

    def take(i: int, j: int) -> None:
        keep[i] = j
        used.add(j)

    for i, entry in enumerate(new):
        if i < len(old) and _same(old[i], entry):
            take(i, i)
    for i, entry in enumerate(new):
        if keep[i] is None:
            j = next((j for j, o in enumerate(old) if j not in used and _same(o, entry)), None)
            if j is not None:
                take(i, j)
    for i, entry in enumerate(new):
        ident = _identity(entry)
        if keep[i] is None and ident is not None:
            j = next((j for j, o in enumerate(old) if j not in used and _identity(o) == ident), None)
            if j is not None:
                take(i, j)
    for i in range(len(new)):
        if keep[i] is None and i < len(old) and i not in used:
            take(i, i)
    source: list = [None] * len(new)
    for i, entry in enumerate(new):
        if keep[i] is None and old:
            source[i] = _source(old, entry, i)
    return keep, source


def _source(old: list, entry, i: int) -> int:
    """The old entry a new one is a copy of: the one it equals, else the one
    with its name, else one with exactly its keys, else its neighbour."""
    ident = _identity(entry)
    tests = [
        lambda o: _same(o, entry),
        lambda o: ident is not None and _identity(o) == ident,
        lambda o: isinstance(o, dict) and isinstance(entry, dict) and o.keys() == entry.keys(),
    ]
    for test in tests:
        j = next((j for j, o in enumerate(old) if test(o)), None)
        if j is not None:
            return j
    return min(i, len(old) - 1)


def _place(parent: ET.Element, old: list[ET.Element], new: list[ET.Element], at: Callable[[ET.Element], int]) -> None:
    """Put *new* where *old* were in *parent*: the k-th new element takes the
    k-th old one's slot, extras follow the last slot, and every other child
    (comments included) keeps its place. With no old slot, *at(parent)* says
    where the new elements go."""
    if not old:
        if new:
            index = at(parent)
            for offset, elem in enumerate(new):
                parent.insert(index + offset, elem)
        return
    old_ids = {id(e) for e in old}
    children = list(parent)
    last = max(i for i, c in enumerate(children) if id(c) in old_ids)
    out = []
    k = 0
    for i, child in enumerate(children):
        if id(child) not in old_ids:
            out.append(child)
            continue
        if k < len(new):
            out.append(new[k])
            k += 1
        if i == last:
            out.extend(new[k:])
            k = len(new)
    parent[:] = out


def sync_list(
    parent: ET.Element,
    old_elems: list[ET.Element],
    new_entries: list,
    *,
    read: Callable[[ET.Element], Any],
    write: Callable[[ET.Element, Any], None],
    make: Callable[[Any], ET.Element],
    at: Callable[[ET.Element], int],
) -> None:
    """Bring *parent*'s repeated children *old_elems* in line with
    *new_entries* (see the module docstring)."""
    old = [read(e) for e in old_elems]
    keep, source = _match(old, new_entries)
    elems = []
    for entry, j, s in zip(new_entries, keep, source):
        if j is not None:
            elem = old_elems[j]
        elif s is not None:
            elem = copy.deepcopy(old_elems[s])
        else:
            elem = make(entry)
        write(elem, entry)
        elems.append(elem)
    _place(parent, old_elems, elems, at)


def after(order: list[str], tag: str) -> Callable[[ET.Element], int]:
    """Where a first *tag* child goes: after the last child whose tag comes
    no later than *tag* in *order*, else first; at the end for a tag *order*
    does not know."""
    if tag not in order:
        return lambda parent: len(parent)
    rank = order.index(tag)

    def at(parent: ET.Element) -> int:
        index = 0
        for i, child in enumerate(parent):
            if is_element(child) and child.tag in order and order.index(child.tag) <= rank:
                index = i + 1
        return index

    return at


def name_list(tag: str, order: list[str]) -> Part:
    """``<tag name="…"/>`` children as a list of names. A ``<tag user="…"/>``
    (a cfglimitsdefinitionuser.xml group) or a nameless one is not in the list
    and stays where it is."""

    def elems(parent):
        return [c for c in parent if c.tag == tag and c.get("name") is not None]

    def read(parent):
        return [c.get("name") for c in elems(parent)]

    def write(parent, names):
        names = [str(n).strip() for n in names]
        if any(not n for n in names):
            raise ValueError(f"{tag}: a name can't be left empty")
        sync_list(
            parent, elems(parent), names,
            read=lambda e: e.get("name"),
            write=lambda e, n: e.get("name") == n or e.set("name", n),
            make=lambda n: ET.Element(tag, {"name": n}),
            at=after(order, tag),
        )

    return Part(read, write)


def attrs_part(tag: str, order: list[str], defaults: dict) -> Part:
    """One ``<tag …/>`` child's attributes as an object. A missing element
    shows *defaults* and is created, with all of them, only when one changes."""

    def read(parent):
        return attrs_of(parent.find(tag), defaults)

    def write(parent, new):
        elem = parent.find(tag)
        if elem is None:
            if _same(new, attrs_of(None, defaults)):
                return
            elem = ET.Element(tag)
            parent.insert(after(order, tag)(parent), elem)
            for key, value in new.items():
                elem.set(key, value_text(value))
            return
        set_attrs(elem, new, defaults)

    return Part(read, write)


# ---------------------------------------------------------------------------
# A row
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Part:
    """One key of a row's ``nested``: how it is read from the row's element
    and written back into it."""

    read: Callable[[ET.Element], Any]
    write: Callable[[ET.Element, Any], None]


def nested_of(elem: ET.Element, parts: dict[str, Part]) -> dict:
    """A row's ``nested`` payload."""
    return {key: part.read(elem) for key, part in parts.items()}


def apply_parts(
    elem: ET.Element,
    parts: dict[str, Part],
    values: dict,
    where: str = "",
    floats: frozenset = frozenset(),
) -> bool:
    """Write a row's ``$nested`` edits into *elem* in place. Raises
    ``ValueError`` (nothing written into *elem* yet) for a stale base or a
    value that does not fit; returns whether there was anything to apply.

    *floats*: attribute names the game reads as decimals although the file
    may write one whole (a ``<pos x="10595">``), passed to `apply_nested`."""
    edits = (values or {}).get(NESTED)
    if not edits:
        return False
    doc = nested_of(elem, parts)
    try:
        apply_nested(doc, values, parts, floats=floats)
    except ValueError as e:
        raise ValueError(f"{where}: {e}" if where else str(e)) from None
    for key in edits:
        if key in parts:
            parts[key].write(elem, doc[key])
    return True
