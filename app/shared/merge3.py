"""Three-way merge of one stock mission file, whatever its kind (plans/71).

`merge3` is the one entry point: XML goes to `app.xml.xml_merge3`, JSON is
merged here by key path, and anything else (``init.c``) line by line, the way
``patch`` would. Every kind returns the same `Merge3Result`; ``fallback`` says
why a file could not be merged, and the caller then keeps the whole-file choice
from plans/67.

Quick tweaks and presets (D4): a value both sides changed that a tweak or
preset wrote is scaled again from DayZ's new value, with the multiplier the
sidecar implies (``written / base``), instead of either side winning.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher

BOTH_CHANGED = "both_changed"
DAYZ_REMOVED = "dayz_removed"
YOU_REMOVED = "you_removed"

# The files whose tweak and preset sidecars record values (`field_baselines`).
SIDECAR_FILES = ("db/types.xml", "db/events.xml")

# Arrays of objects matched by an identity key instead of compared whole.
_JSON_ID_KEYS = ("AreaName", "name", "Name")

_MISSING = object()


@dataclass
class Merge3Result:
    text: str | None = None
    rows: list[dict] = field(default_factory=list)
    dayz: int = 0
    yours: int = 0
    tweaks: int = 0
    # sidecar file name -> entry -> field -> {"base", "written"}
    sidecar_updates: dict = field(default_factory=dict)
    fallback: str | None = None


def merge3(
    rel: str, base: bytes, mine: bytes, theirs: bytes, *,
    prefer: str = "mine", sidecars: dict[str, dict] | None = None,
) -> Merge3Result:
    """Merge the admin's version of *rel* (mission-relative, "dayzOffline.x/db/types.xml")
    into DayZ's new file against the old stock *base*.

    *sidecars* maps a sidecar file name to that file's records
    (``{entry: {field: {"base", "written"}}}``) for D4.
    """
    from app.xml.xml_merge3 import Unmergeable, merge_xml3

    texts = [_decode(data) for data in (base, mine, theirs)]
    suffix = rel.rsplit(".", 1)[-1].lower()
    try:
        if suffix == "xml":
            resolver, updates, count = _tweak_resolver(rel, sidecars or {})
            merged = merge_xml3(*texts, filename=rel, prefer=prefer, resolver=resolver)
            return Merge3Result(
                text=merged.text, rows=[asdict(row) for row in merged.rows],
                dayz=merged.dayz, yours=merged.yours, tweaks=count[0], sidecar_updates=updates,
            )
        if suffix == "json":
            return _merge_json(*texts, prefer=prefer)
        text, yours, dayz = _text3(*texts)
        return Merge3Result(text=text, yours=yours, dayz=dayz)
    except (Unmergeable, _Overlap, ValueError) as exc:
        return Merge3Result(fallback=str(exc) or type(exc).__name__)


def _decode(data: bytes) -> str:
    return data.decode("utf-8-sig", errors="surrogateescape")


# --- quick tweaks and presets (D4) ----------------------------------------------


def _tweak_resolver(rel: str, sidecars: dict[str, dict]):
    """A resolver for `merge_xml3`, the sidecar updates it makes, and a count."""
    from app.presets.field_baselines import scale_field

    updates: dict = {}
    count = [0]
    if not sidecars or not any(rel.endswith("/" + key) for key in SIDECAR_FILES):
        return None, updates, count

    def resolve(keys, target, base, mine, theirs):
        if len(keys) != 2 or target != ["text"] or keys[0][0] != "attr" or keys[1][0] != "tag":
            return None
        entry, name = keys[0][3], keys[1][1]
        numbers = [_int(base), _int(mine), _int(theirs)]
        if None in numbers or numbers[0] <= 0:
            return None
        b, m, t = numbers
        for sidecar, records in sidecars.items():
            rec = (records.get(entry) or {}).get(name)
            if isinstance(rec, dict) and rec.get("base") == b and rec.get("written") == m:
                field_records = {name: dict(rec)}
                new = scale_field(field_records, name, t, m / b)
                updates.setdefault(sidecar, {}).setdefault(entry, {})[name] = field_records[name]
                count[0] += 1
                return str(new)
        return None

    return resolve, updates, count


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


# --- JSON -------------------------------------------------------------------------


def _merge_json(base: str, mine: str, theirs: str, *, prefer: str) -> Merge3Result:
    try:
        docs = [json.loads(text) for text in (base, mine, theirs)]
    except ValueError as exc:
        raise ValueError(f"a version is not valid JSON: {exc}") from exc
    merger = _JsonMerger(prefer)
    value = merger.value(*docs, keys=[])
    text = json.dumps(value, indent=4, ensure_ascii=False) + "\n"
    return Merge3Result(text=text, rows=merger.rows, dayz=merger.dayz, yours=merger.yours)


class _JsonMerger:
    def __init__(self, prefer: str):
        self.prefer = prefer
        self.rows: list[dict] = []
        self.dayz = 0
        self.yours = 0

    def value(self, b, m, t, *, keys):
        if all(isinstance(v, dict) for v in (b, m, t)):
            return self._object(b, m, t, keys)
        id_key = _id_key(b, m, t)
        if id_key:
            return self._keyed_array(b, m, t, id_key, keys)
        return self._leaf(b, m, t, keys)

    def _leaf(self, b, m, t, keys):
        if m == b:
            if t != b:
                self.dayz += 1
            return t
        if t == b:
            self.yours += 1
            return m
        if m == t:
            return m
        self._row(keys, b, m, t, BOTH_CHANGED, self.prefer)
        return m if self.prefer == "mine" else t

    def _object(self, b, m, t, keys):
        out = {}
        for key in dict.fromkeys([*t, *m, *b]):
            merged = self._member(b.get(key, _MISSING), m.get(key, _MISSING), t.get(key, _MISSING), [*keys, ["key", key]])
            if merged is not _MISSING:
                out[key] = merged
        return out

    def _keyed_array(self, b, m, t, id_key, keys):
        maps = [{item[id_key]: item for item in arr} for arr in (b, m, t)]
        order = list(dict.fromkeys([*maps[2], *maps[1]]))
        out = []
        for ident in dict.fromkeys([*order, *maps[0]]):
            merged = self._member(*(mp.get(ident, _MISSING) for mp in maps), [*keys, ["item", id_key, ident]])
            if merged is not _MISSING:
                out.append(merged)
        return out

    def _member(self, b, m, t, keys):
        if b is _MISSING:
            if m is _MISSING:
                if t is not _MISSING:
                    self.dayz += 1
                return t
            if t is _MISSING or t == m:
                self.yours += t is _MISSING
                return m
            self._row(keys, None, m, t, BOTH_CHANGED, self.prefer)
            return m if self.prefer == "mine" else t
        if m is _MISSING and t is _MISSING:
            return _MISSING
        if m is _MISSING:
            if t == b:
                self.yours += 1
                return _MISSING
            self._row(keys, b, None, t, YOU_REMOVED, self.prefer)
            return _MISSING if self.prefer == "mine" else t
        if t is _MISSING:
            if m == b:
                self.dayz += 1
                return _MISSING
            self._row(keys, b, m, None, DAYZ_REMOVED, "dayz")
            return _MISSING
        return self.value(b, m, t, keys=keys)

    def _row(self, keys, b, m, t, kind, live):
        self.rows.append({
            "entry": _json_label(keys[0]) if keys else "(file)",
            "field": "/".join(_json_label(k) for k in keys[1:]) or "(value)",
            "base": _json_shown(b), "mine": _json_shown(m), "theirs": _json_shown(t),
            "kind": kind, "live": live, "keys": keys, "target": ["value"],
        })


def _id_key(*values) -> str | None:
    """The identity key every object of these three arrays carries uniquely."""
    if not all(isinstance(v, list) and v and all(isinstance(i, dict) for i in v) for v in values):
        return None
    for key in _JSON_ID_KEYS:
        if all(all(key in item for item in arr) and len({json.dumps(item[key]) for item in arr}) == len(arr)
               and all(isinstance(item[key], str) for item in arr) for arr in values):
            return key
    return None


def _json_label(key: list) -> str:
    return str(key[1]) if key[0] == "key" else f"{key[1]}={key[2]}"


def _json_shown(value) -> str | None:
    """The value as JSON text, so applying a side later can read it back exactly."""
    return None if value is None else json.dumps(value, ensure_ascii=False)


def find_json(doc, keys: list):
    """(parent, slot) of the value *keys* names in *doc*, or (None, None).
    A missing last step returns its parent, so a removed value can be put back."""
    parent, slot = None, None
    node = doc
    for i, key in enumerate(keys):
        last = i == len(keys) - 1
        if key[0] == "key" and isinstance(node, dict):
            parent, slot = node, key[1]
            if key[1] not in node:
                return (parent, slot) if last else (None, None)
            node = node[key[1]]
        elif key[0] == "item" and isinstance(node, list):
            index = next((j for j, item in enumerate(node) if isinstance(item, dict) and item.get(key[1]) == key[2]), None)
            parent, slot = node, index
            if index is None:
                return (parent, None) if last else (None, None)
            node = node[index]
        else:
            return None, None
    return parent, slot


def json_value_at(doc, keys: list) -> str | None:
    """A row's value in a single document, as the row shows it; None when gone."""
    parent, slot = find_json(doc, keys)
    if parent is None or slot is None or (isinstance(parent, dict) and slot not in parent):
        return None
    return _json_shown(parent[slot])


# --- text --------------------------------------------------------------------------


class _Overlap(Exception):
    pass


def merge_text3(base: str, mine: str, theirs: str) -> str:
    """Line-level three-way merge. Edits that touch or overlap differently are
    refused (`_Overlap`), never guessed."""
    return _text3(base, mine, theirs)[0]


def _text3(base: str, mine: str, theirs: str) -> tuple[str, int, int]:
    """`merge_text3`, plus how many edits came from each side."""
    b, m, t = (text.splitlines(keepends=True) for text in (base, mine, theirs))
    mine_hunks, their_hunks = _hunks(b, m), _hunks(b, t)
    hunks = sorted(mine_hunks + their_hunks, key=lambda h: (h[0], h[1]))
    merged: list[tuple[int, int, list[str]]] = []
    for hunk in hunks:
        if merged and _touch(merged[-1], hunk):
            if merged[-1] == hunk:
                continue
            raise _Overlap("overlapping edits")
        merged.append(hunk)
    out: list[str] = []
    pos = 0
    for start, end, lines in merged:
        out.extend(b[pos:start])
        out.extend(lines)
        pos = end
    out.extend(b[pos:])
    shared = [h for h in mine_hunks if h in their_hunks]
    return "".join(out), len(mine_hunks) - len(shared), len(their_hunks) - len(shared)


def _hunks(base: list[str], other: list[str]) -> list[tuple[int, int, list[str]]]:
    matcher = SequenceMatcher(None, base, other, autojunk=False)
    return [(i1, i2, other[j1:j2]) for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != "equal"]


def _touch(a: tuple, b: tuple) -> bool:
    """Whether two hunks, *a* starting first, share or border base lines."""
    return b[0] <= a[1]
