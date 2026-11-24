"""Nested objects and lists in a JSON form (plans/76 step 2a).

A form shows a file's flat settings as fields. What it cannot show that way
(Expansion's ``Patrols``, a mod's loot presets) used to be Raw-only. Such a
key now travels beside the fields as ``nested``: ``{key: value}``, the value
as the file has it, and the form renders it as a tree (``JsonTree.jsx``).

A save sends ``"$nested": {key: edited value}`` and ``"$nested_base": {key:
value as loaded}`` among the form's values. The rules are the ones every JSON
editor here keeps:

- **The file is re-read and only the sent keys change.** A key whose current
  value differs from the base the page loaded is refused, so a stale tab
  cannot overwrite (or delete entries from) a list someone changed since.
- **The edited value is fitted to the shape the file already has**
  (`conform`): a whole number stays one and a decimal there is refused with
  its path named, a float stays a float, a 0/1 flag stays an int, a string
  stays a string, and a value left as it was is kept as the very object the
  file had (so ``SourceFloat`` keeps its text). A list entry is fitted to the
  old entry it came from (`_template`: a moved entry is not fitted to the one
  that used to sit at its position), a new one to an entry with its keys. An
  object never gains or loses a key here.
- **An empty list has nothing to fit a new entry to, so it borrows one**
  (`_item_template`): the editor's own template for that key (Expansion's
  empty lists of objects, ``templates``), else the first entry of the same key
  in a sibling entry that has some (a patrol's ``Units`` from another
  patrol's), but only one with exactly the new entry's keys. A typed ``5`` in
  an int key is then written as ``5``, not ``"5"``. With neither, the entry is
  written as sent (`_plain`), as before.
- **Only keys the form hands out are accepted** (`allowed`); a hand-made
  request for any other key is ignored.
"""

from __future__ import annotations

import copy
import math

NESTED = "$nested"
NESTED_BASE = "$nested_base"


def is_nested(value) -> bool:
    """A value a flat field cannot show: an object, or a list holding one."""
    if isinstance(value, dict):
        return True
    if isinstance(value, list):
        return any(isinstance(item, (dict, list)) for item in value)
    return False


def _number(raw, where: str) -> float:
    if isinstance(raw, bool):
        raise ValueError(f"{where}: expected a number")
    text = "" if raw is None else str(raw).strip()
    if not text:
        raise ValueError(f"{where}: needs a number and can't be left empty")
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not math.isfinite(value):
        raise ValueError(f"{where}: {text!r} is not a number")
    return value


def _same(a, b) -> bool:
    """JSON equality that tells 1 from true and 1 from "1"."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if type(a) is not type(b) and not (isinstance(a, str) and isinstance(b, str)):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def _plain(new, where: str):
    """A value with nothing in the file to fit it to (an entry of an empty list)."""
    if isinstance(new, float) and not math.isfinite(new):
        raise ValueError(f"{where}: not a number")
    if isinstance(new, dict):
        return {k: _plain(v, f"{where}.{k}") for k, v in new.items()}
    if isinstance(new, list):
        return [_plain(v, f"{where}[{i + 1}]") for i, v in enumerate(new)]
    return new


def _shape(value):
    """A hashable form of a JSON value, equal wherever `_same` says equal."""
    if isinstance(value, bool):
        return ("b", value)
    if isinstance(value, (int, float)):
        return ("n", float(value))
    if isinstance(value, str):
        return ("s", str(value))
    if isinstance(value, dict):
        return ("d", frozenset((k, _shape(v)) for k, v in value.items()))
    if isinstance(value, list):
        return ("l", tuple(_shape(v) for v in value))
    return ("z", repr(value))


def _template(item, old: list, i: int, index: dict):
    """The entry of *old* that list item *i* is fitted to.

    The same position, unless the entries moved: a removed, copied or
    reordered entry shifts every one after it, and the entries of one list
    need not share a key set (Sakhal's underground triggers carry keys
    Livonia's lack, and a breadcrumb may or may not have a ``Radius``).
    Fitted by position, a shifted object would be refused for a key its
    neighbour lacks, or take a key it never had from that neighbour. So an
    object is fitted to the old entry it equals (it was only moved), else to
    the one at its position or the first one with exactly its keys; *index*
    caches the old entries by `_shape` for the first of those.
    """
    here = old[i] if i < len(old) else old[-1]
    if not isinstance(item, dict) or (i < len(old) and _same(item, here)):
        return here
    if not index:
        for n, entry in enumerate(old):
            index.setdefault(_shape(entry), n)
    hit = index.get(_shape(item))
    if hit is not None:
        return old[hit]
    if isinstance(here, dict) and here.keys() == item.keys():
        return here
    for entry in old:
        if isinstance(entry, dict) and entry.keys() == item.keys():
            return entry
    return here


def _decimal_keys(old: list) -> frozenset:
    """Keys that hold a decimal in at least one object of *old*.

    JSON writes a float that happens to be whole as ``1``, so one entry's int
    says nothing when its siblings hold ``0.2`` under the same key (Bohemia's
    underground ``EyeAccommodation``: ``0``, ``0.2`` and ``1`` in one list).
    Such a key takes a decimal in every entry; the whole-number rule stays
    for a key no entry ever writes with a fraction (Expansion's 0/1 flags).
    """
    return frozenset(
        k
        for entry in old
        if isinstance(entry, dict)
        for k, v in entry.items()
        if isinstance(v, float)
    )


def _borrowed(old: list) -> dict:
    """{key: first entry} of each list some object of *old* holds entries in.

    What an empty list under the same key in a sibling object is fitted to.
    """
    out: dict = {}
    for entry in old:
        if isinstance(entry, dict):
            for k, v in entry.items():
                if k not in out and isinstance(v, list) and v:
                    out[k] = v[0]
    return out


def _item_template(item, explicit, borrowed):
    """What *item*, an entry of a list the file has empty, is fitted to.

    *explicit* (the editor's template) may name keys the entry lacks; they
    are filled from it. A *borrowed* sibling entry is used only when it has
    exactly the entry's shape, since its values are someone else's.
    """
    if explicit is not None:
        return copy.deepcopy(explicit)
    if borrowed is None:
        return None
    if isinstance(item, dict):
        ok = isinstance(borrowed, dict) and borrowed.keys() == item.keys()
    elif isinstance(item, list):
        ok = isinstance(borrowed, list)
    else:
        ok = not isinstance(borrowed, (dict, list))
    return copy.deepcopy(borrowed) if ok else None


def conform(
    new,
    old,
    where: str,
    decimal: bool = False,
    decimal_keys: frozenset = frozenset(),
    floats: frozenset = frozenset(),
    templates: dict | None = None,
    item_template=None,
    borrowed=None,
    siblings: dict | None = None,
):
    """*new* fitted to the shape of *old*; see the module docstring.

    *decimal*: this value may take a fraction although the file wrote it whole
    (`_decimal_keys`). *decimal_keys* passes that down to an object's keys.
    *floats*: key names the game reads as decimals at any depth (the editor's
    own list, from Bohemia's wiki), so ``1`` in the file still takes ``0.5``;
    a list under such a key (``Position``) passes it to its numbers.
    *templates*: {key: new entry} for lists the file may have empty, at any
    depth; *item_template* is the one for this list itself, and *borrowed*
    a sibling's first entry for it (`_item_template`). *siblings*: for an
    object in a list, `_borrowed` of that list.
    """
    if _same(new, old):
        return old
    if isinstance(old, bool):
        if isinstance(new, bool):
            return new
        return str(new).strip().lower() in ("1", "true", "on", "yes")
    if isinstance(old, int):
        value = _number(new, where)
        if decimal and value != int(value):
            return value
        if value != int(value):
            raise ValueError(
                f"{where}: {new!r} is not a whole number, and the file holds one here "
                "(the Raw tab takes any number)"
            )
        return int(value)
    if isinstance(old, float):
        value = _number(new, where)
        return old if value == old else value
    if isinstance(old, str):
        if isinstance(new, (dict, list)):
            raise ValueError(f"{where}: expected text")
        return "" if new is None else str(new)
    if isinstance(old, list):
        if not isinstance(new, list):
            raise ValueError(f"{where}: expected a list")
        out = []
        index: dict = {}
        decimals = _decimal_keys(old)
        shared = _borrowed(old)
        for i, item in enumerate(new):
            at = f"{where}[{i + 1}]"
            if old:
                out.append(
                    conform(
                        item,
                        _template(item, old, i, index),
                        at,
                        decimal=decimal,
                        decimal_keys=decimals,
                        floats=floats,
                        templates=templates,
                        siblings=shared,
                    )
                )
                continue
            template = _item_template(item, item_template, borrowed)
            if template is None:
                out.append(_plain(item, at))
            else:
                out.append(conform(item, template, at, decimal=decimal, floats=floats, templates=templates))
        return out
    if isinstance(old, dict):
        if not isinstance(new, dict):
            raise ValueError(f"{where}: expected an object")
        extra = [k for k in new if k not in old]
        if extra:
            raise ValueError(f"{where}: has no key {extra[0]!r}")
        templates = templates or {}
        siblings = siblings or {}
        return {
            k: (
                conform(
                    new[k],
                    v,
                    f"{where}.{k}",
                    decimal=k in decimal_keys or k in floats,
                    floats=floats,
                    templates=templates,
                    item_template=templates.get(k),
                    borrowed=siblings.get(k),
                )
                if k in new
                else v
            )
            for k, v in old.items()
        }
    # null in the file: anything single goes, as written.
    if isinstance(new, (dict, list)):
        raise ValueError(f"{where}: expected a single value")
    return _plain(new, where)


def nested_of(doc: dict, keys) -> dict:
    """The ``nested`` part of a form payload: *keys* that *doc* has."""
    return {k: doc[k] for k in keys if k in doc}


def apply_nested(
    doc: dict,
    values: dict,
    allowed,
    floats: frozenset = frozenset(),
    templates: dict | None = None,
) -> None:
    """Write the form's ``$nested`` edits into *doc* (in place).

    *floats*: the key names this file's readers take as decimals (`conform`).
    *templates*: {key: new entry} for a list the file may have empty (the
    payload's ``nested_templates``).
    """
    templates = templates or {}
    edits = (values or {}).get(NESTED) or {}
    bases = (values or {}).get(NESTED_BASE) or {}
    if not isinstance(edits, dict) or not isinstance(bases, dict):
        raise ValueError("Nested values must be sent as objects.")
    for key, new in edits.items():
        if key not in allowed or key not in doc:
            continue
        if key not in bases or not _same(bases[key], doc[key]):
            raise ValueError(
                f"{key} changed in the file since this page loaded it. Reload the page and "
                "make the change again."
            )
        doc[key] = conform(
            new,
            doc[key],
            key,
            decimal=key in floats,
            floats=floats,
            templates=templates,
            item_template=templates.get(key),
        )
