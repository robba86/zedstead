"""Form and table editors over a pre-configured mod's JSON files.

Dynamic AI (plans/47 C) built these; RadZone (plans/48 B) needed the same, so
the machinery lives here and each mod's module declares only its fields,
tables, paths and pickers. The rules are the ones plans/47 C settled, and they
hold for every mod that uses this:

- **A save patches.** It re-reads the file and changes only what the editor
  shows. Unknown keys, nested lists a table does not show, and anything the
  next mod release adds stay exactly as they were.
- **Whole numbers stay whole.** An ``int`` field refuses ``4.5`` with the field
  named; ``4.0`` is written as ``4``. DayZ's JSON loader reads these as ints.
- **A key the file does not have is never invented** by a form save.
- **A float is shown as the shortest decimal DayZ reads the same.** Enforce
  floats are 32-bit and the mods write them out as doubles, so ``0.2`` lands in
  the file as ``0.20000000298023224``. The editor shows ``0.2``; a save of a
  field still showing that keeps the file's own digits, and an edited one is
  written as typed. The game loads the identical float either way.
- **A save writes back only what changed** (plans/54 D). The document is read
  with ``load_json_preserving`` and written with ``save_json_patched``: only
  the values that changed are replaced in the file's text, so a save with no
  edits writes nothing, and the mod's layout, line endings and number text
  stay.
- **A table row is the entry it came from.** Its id is its index; a
  ``dup-<n>-…`` row starts as a copy of entry *n*; a blank row starts from the
  table's template, and only where the table allows one (``add_row``).
- **An entry's objects and lists of objects travel as the row's ``nested``**
  (plans/76 step 2b, ``Table.tree``), under a form's rules: a save sends
  ``$nested`` and ``$nested_base`` among the row's values, and
  ``_nested.apply_nested`` refuses a stale base and fits the edit to the file.
- **A path may be None** when the file lives in a mission folder and none
  resolves (plans/64). A load then shows the empty shape and a save refuses.
"""

from __future__ import annotations

import copy
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.shared.config import DayzConfig
from app.shared.json_patch import save_json_patched
from app.shared.json_utils import float32_equal, load_json_preserving
from app.web.editors._nested import apply_nested, is_nested, nested_of


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    #: "list" is a list of scalars, edited item by item (its items are
    #: ``item``); "json" is any JSON value edited as text. No editor uses it
    #: since plans/76 step 2b: a list of objects is a `Form.tree` or
    #: `Table.tree` key now.
    type: str  # "int" | "float" | "bool" | "str" | "list" | "json"
    help: str = ""
    #: A fixed list, or the name of a list the mod's ``choices`` callable fills.
    choices: tuple[str, ...] | str | None = None
    item: str = "str"  # a "list" field's items: "str" | "int" | "float"
    #: What each fixed value means ({value: text}), the line under it in the picker.
    # Not hashed: a dict cannot be, and the field is frozen (hashable) otherwise.
    choice_help: dict[str, str] | None = field(default=None, hash=False)
    #: The number's unit, shown in its box ("seconds", "minutes", "meters"):
    #: webui UnitInput, and a time unit gets FormEditor's unit picker.
    unit: str | None = None
    #: A 0.0-1.0 share, shown as a percentage: a slider in a form (webui
    #: RangeField, from `min`/`max`), ShareInput in a table cell.
    share: bool = False


def F(key, type, help="", choices=None, label=None, item="str", unit=None, share=False) -> Field:
    """A field. *choices* is a list of values, a {value: meaning} dict (the
    meanings show under each value in the picker; "" for a value with no
    known meaning), or a ``Choices`` name. *unit* and *share* say how a
    number is shown (`Field`)."""
    meanings = {k: v for k, v in choices.items() if v} if isinstance(choices, dict) else None
    if isinstance(choices, (list, dict)):
        choices = tuple(choices)
    return Field(key, label or key, type, help, choices, item, meanings, unit, share)


#: name -> options, computed per request from the install (factions, ...).
Choices = dict[str, list[str]]


@dataclass(frozen=True)
class Form:
    """The top-level settings of one file, as titled sections of fields."""

    mod: str  # for "start the server once with <mod> installed"
    path: Callable[[DayzConfig], Path | None]
    sections: tuple[tuple[str, tuple[Field, ...]], ...]
    #: Keys that are not settings: lists with their own table, a version the
    #: mod checks. A key here is never shown and never written by the form.
    not_on_form: frozenset[str] = frozenset()
    #: Keys of `not_on_form` that no table owns (RadZone's ``ListLoots``):
    #: shown under the fields as a tree and saved through `_nested`.
    tree: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Table:
    """One list of objects in a file, one row per entry."""

    id: str
    title: str
    mod: str
    path: Callable[[DayzConfig], Path | None]
    section: str  # key of the list in the document
    columns: tuple[Field, ...]
    #: False: new entries only by duplicating one (a blank row would, e.g.,
    #: land at the map origin).
    add_row: bool = True
    template: dict = field(default_factory=dict)
    name_key: str = ""  # the key that names an entry, for error messages
    warnings: tuple[str, ...] = ()
    #: How an entry reads in the table, when that differs from the file
    #: (ToxicZone shows a moving zone's ``Speed`` without its sign).
    view: Callable[[dict], dict] | None = None
    #: Runs on each entry after the row's values are in, before the write:
    #: values the file must hold but the table does not show (a moving zone's
    #: derived line). A ValueError is reported against the entry's name.
    finish: Callable[[dict], None] | None = None
    #: Keys of each entry shown under its row as a tree and saved through
    #: `_nested` (plans/76 step 2b): a zone's particles and crate positions, a
    #: slot's clothes. `TREE_REST` in it adds any other object or list of
    #: objects the entry holds that no column shows.
    tree: frozenset[str] = frozenset()


#: In `Table.tree`: every nested value of an entry that no column shows.
TREE_REST = "*"


def tree_keys(table: Table, entry: dict) -> list[str]:
    """The keys of *entry* its row carries as ``nested``, in the entry's order."""
    if not table.tree:
        return []
    columns = {f.key for f in table.columns}
    rest = TREE_REST in table.tree
    return [
        key for key, value in entry.items()
        if key not in columns and (key in table.tree or (rest and is_nested(value)))
    ]


# ── Files ───────────────────────────────────────────────────────────────


def readable(path: Path | None) -> bool:
    return path is not None and path.is_file()


def document(path: Path | None, mod: str) -> dict:
    if path is None:
        raise RuntimeError("No mission is selected, so there is no file to save to.")
    if not path.is_file():
        raise RuntimeError(
            f"{path.name} does not exist yet. Start the server once with {mod} "
            "installed and the mod writes it."
        )
    data = load_json_preserving(path)
    if not isinstance(data, dict):
        raise RuntimeError(f"{path.name} is not a JSON object.")
    return data


# ── Values in and out ───────────────────────────────────────────────────


def _number(raw) -> float:
    text = "" if raw is None else str(raw).strip()
    if not text:
        raise ValueError("needs a number and can't be left empty")
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not math.isfinite(value):
        raise ValueError(f"{text!r} is not a number")
    return value


# ── Lists of scalars (plans/76 step 1) ──────────────────────────────────
#
# Shared by every editor that shows a list item by item: these forms and
# tables, Expansion's settings and cfggameplay.json. On the wire a list field
# is ``{"type": "list", "item": ..., "value": [...]}`` with a real array, never
# a comma-joined string (a value may hold a comma) or JSON in a text box.


def list_item_type(value) -> str | None:
    """The item type of a list read from a file: "str", "int" or "float".

    None when the list is not one of scalars of one kind (a list of objects, a
    mix of names and numbers, booleans), which the caller then shows some
    other way. An empty list is "str": nothing says otherwise, and a list of
    names is what an empty one usually is.
    """
    if not isinstance(value, list):
        return None
    if any(isinstance(v, bool) for v in value):
        return None
    if all(isinstance(v, str) for v in value):
        return "str"
    if all(isinstance(v, int) for v in value):
        return "int"
    if all(isinstance(v, (int, float)) for v in value):
        return "float"
    return None


def _same_item(raw, current) -> bool:
    if isinstance(raw, bool) or isinstance(current, bool):
        return False
    if isinstance(raw, (int, float)) and isinstance(current, (int, float)):
        return raw == current
    return str(raw).strip() == str(current)


def coerce_list(raw, item: str, current=None) -> list:
    """The editor's list, each item typed as *item*.

    *raw* is an array, or JSON text from an older page. An item still equal
    to the file's item at the same place is kept exactly as the file had it,
    so ``1.0`` is not rewritten as ``1`` and a number DayZ wrote as an int in
    a list of floats stays one. An item emptied in its box (``""`` or only
    spaces) is dropped, since the browser keeps the box: written, it was a
    ``""`` entry in a list that had none.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    if not isinstance(raw, list):
        raise ValueError('expected a list, e.g. ["a", "b"]')
    old = current if isinstance(current, list) else []
    out: list = []
    for i, value in enumerate(raw):
        if i < len(old) and _same_item(value, old[i]):
            out.append(old[i])
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, dict)) or value is None:
            raise ValueError(f"item {i + 1} is not a single value")
        if item == "str":
            out.append(str(value))
            continue
        try:
            number = _number(value)
        except ValueError as exc:
            raise ValueError(f"item {i + 1}: {exc}") from exc
        if item == "int":
            if number != int(number):
                raise ValueError(
                    f"item {i + 1}: {value!r} is not a whole number, and DayZ loads these as one"
                )
            out.append(int(number))
        else:
            out.append(number)
    return out


def list_field(key: str, label: str, value: list, item: str) -> dict:
    """A form field (or table column, without ``value``) for a list."""
    return {"key": key, "label": label, "type": "list", "item": item, "value": list(value)}


def coerce(f: Field, raw, current):
    """The editor's value, typed the way the mod loads it."""
    if f.type == "int":
        value = _number(raw)
        if value != int(value):
            raise ValueError(f"{raw!r} is not a whole number, and DayZ loads this field as one")
        result = int(value)
    elif f.type == "float":
        result = _number(raw)
        # What the editor showed for the file's value: the field is untouched,
        # and rewriting it would change the file's digits for nothing.
        if isinstance(current, float) and not isinstance(current, bool):
            if result == current or result == float32_display(current):
                return current
    elif f.type == "bool":
        on = raw is True or str(raw).strip().lower() in ("1", "true", "on", "yes")
        # Mods write these as 0/1; keep whichever form the file already has.
        return on if isinstance(current, bool) else int(on)
    elif f.type == "list":
        return coerce_list(raw, f.item, current)
    elif f.type == "json":
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            parsed = None
        if not isinstance(parsed, list):
            raise ValueError('expected a JSON list, e.g. [{"a": 1}]')
        return parsed
    else:
        return "" if raw is None else str(raw)
    if isinstance(f.choices, tuple) and str(result) not in f.choices:
        raise ValueError(f"{result} is not one of {', '.join(f.choices)}")
    return result


def float32_display(value):
    """*value* as the shortest decimal with the same 32-bit float, else as is."""
    if isinstance(value, bool) or not isinstance(value, float) or not math.isfinite(value):
        return value
    for digits in range(1, 10):
        candidate = float(f"{value:.{digits}g}")
        if float32_equal(candidate, value):  # never true beyond float32
            return candidate
    return value


def shown(f: Field, value):
    if f.type == "bool":
        return bool(value)
    if f.type == "list":
        return list(value) if isinstance(value, list) else []
    if f.type == "json":
        return json.dumps(value if isinstance(value, list) else [])
    if value is None:
        return ""
    return float32_display(value) if f.type == "float" else value


def _choices_for(f: Field, install: Choices) -> list[str] | None:
    if f.choices is None:
        return None
    if isinstance(f.choices, tuple):
        return list(f.choices)
    return install.get(f.choices) or None


def describe(f: Field, install: Choices) -> dict:
    out = {"key": f.key, "label": f.label, "type": "str" if f.type == "json" else f.type}
    if f.type == "list":
        out["item"] = f.item
    if f.help:
        out["help"] = f.help
    choices = _choices_for(f, install)
    if choices:
        out["choices"] = choices
        if f.choice_help:
            out["choice_help"] = dict(f.choice_help)
    if f.unit:
        out["unit"] = f.unit
    if f.share:
        out.update(share=True, min=0.0, max=1.0)
    return out


def _kind_of(value) -> str | None:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    return None


# ── The form ────────────────────────────────────────────────────────────


def form_load(config: DayzConfig, form: Form, install: Choices | None = None) -> dict:
    path = form.path(config)
    if not readable(path):
        return {"kind": "form", "sections": []}
    doc = document(path, form.mod)
    install = install or {}
    sections: list[dict] = []
    listed: set[str] = set()
    for title, fields in form.sections:
        rows = []
        for f in fields:
            listed.add(f.key)
            if f.key in doc:
                rows.append({**describe(f, install), "value": shown(f, doc[f.key])})
        if rows:
            sections.append({"title": title, "fields": rows})
    # A key the next release adds still gets a field, in the type the file uses.
    extra = []
    for key, value in doc.items():
        if key in listed or key in form.not_on_form:
            continue
        kind = _kind_of(value)
        if kind:
            extra.append({"key": key, "label": key, "type": kind, "value": value})
    if extra:
        sections.append({"title": "Other settings", "fields": extra})
    body = {"kind": "form", "sections": sections}
    if form.tree:
        body["nested"] = nested_of(doc, sorted(form.tree))
    return body


def _field_for(form: Form, key: str, current) -> Field | None:
    for _title, fields in form.sections:
        for f in fields:
            if f.key == key:
                return f
    kind = _kind_of(current)
    return Field(key, key, kind) if kind else None


def form_save(config: DayzConfig, form: Form, values: dict) -> None:
    path = form.path(config)
    doc = document(path, form.mod)
    for key, raw in (values or {}).items():
        # Never invent a key and never touch what the form does not own.
        if key not in doc or key in form.not_on_form:
            continue
        f = _field_for(form, key, doc[key])
        if f is None:
            continue
        try:
            doc[key] = coerce(f, raw, doc[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key}: {exc}") from exc
    apply_nested(doc, values, form.tree)
    save_json_patched(path, doc)


# ── The tables ──────────────────────────────────────────────────────────


def table_load(config: DayzConfig, table: Table, install: Choices | None = None) -> dict:
    path = table.path(config)
    if not readable(path):
        return {"kind": "table", "columns": [], "rows": []}
    doc = document(path, table.mod)
    columns = [describe(f, install or {}) for f in table.columns]
    picked = {c["key"]: c["choices"] for c in columns if "choices" in c}
    rows = []
    entries = doc.get(table.section)
    for i, entry in enumerate(entries if isinstance(entries, list) else []):
        if not isinstance(entry, dict):
            continue
        seen = table.view(entry) if table.view else entry
        row = {"id": str(i), "values": {f.key: shown(f, seen.get(f.key)) for f in table.columns}}
        if table.tree:
            row["nested"] = nested_of(entry, tree_keys(table, entry))
        if picked:
            row["choices"] = picked
        rows.append(row)
    body = {"kind": "table", "columns": columns, "rows": rows, "duplicate": True, "add_row": table.add_row}
    if table.warnings:
        body["warnings"] = list(table.warnings)
    return body


def source_of(row_id: str) -> str | None:
    """Which existing entry a row is: its own index, or for "dup-<n>-..." the
    entry it was duplicated from. None for a blank new row."""
    if row_id.isdigit():
        return row_id
    if row_id.startswith("dup-"):
        src = row_id.split("-")[1]
        return src if src.isdigit() else None
    return None


def _entry_name(table: Table, entry: dict, n: int) -> str:
    return (entry.get(table.name_key) if table.name_key else None) or f"row {n + 1}"


def table_save(config: DayzConfig, table: Table, rows: list[dict]) -> None:
    path = table.path(config)
    doc = document(path, table.mod)
    old = doc.get(table.section)
    old = old if isinstance(old, list) else []
    new: list = []
    for n, row in enumerate(rows or []):
        row_id = str(row.get("id", ""))
        values = row.get("values", row) or {}
        src = source_of(row_id)
        if src is not None and int(src) < len(old) and isinstance(old[int(src)], dict):
            # Everything the table does not show (positions, nested lists,
            # unknown keys) comes from the entry this row is, or was
            # duplicated from.
            entry = copy.deepcopy(old[int(src)])
        elif table.add_row:
            entry = dict(table.template)
        else:
            raise ValueError(
                f"{table.title} rows can only be duplicated from an existing one "
                "(a new entry needs a position)."
            )
        try:
            # A duplicate's base is its source as loaded, so this checks it too.
            apply_nested(entry, values, tree_keys(table, entry))
        except ValueError as exc:
            raise ValueError(f"{_entry_name(table, entry, n)}: {exc}") from exc
        for f in table.columns:
            if f.key not in values:
                continue
            raw = values[f.key]
            # A cell left as the table showed an absent key (empty, an unticked
            # box) adds nothing: an unticked bool would otherwise invent a 0.
            if f.key not in entry and (raw in ("", None) or raw == shown(f, None)):
                continue
            try:
                entry[f.key] = coerce(f, raw, entry.get(f.key))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{_entry_name(table, entry, n)}, {f.key}: {exc}") from exc
        if table.finish:
            try:
                table.finish(entry)
            except ValueError as exc:
                raise ValueError(f"{_entry_name(table, entry, n)}: {exc}") from exc
        new.append(entry)
    doc[table.section] = new
    save_json_patched(path, doc)


# ── The Raw tab ─────────────────────────────────────────────────────────


def whole_number_validator(find_decimals: Callable[[dict], list[str]]) -> Callable[[str], None]:
    """A ``RawTarget.validate`` refusing a Raw save that puts a decimal where
    the mod loads a whole number. *find_decimals* names the offending keys."""

    def validate(content: str) -> None:
        doc = json.loads(content)
        if not isinstance(doc, dict):
            raise ValueError("The file must be a JSON object.")
        bad = find_decimals(doc)
        if bad:
            raise ValueError(
                "DayZ loads these as whole numbers: "
                + ", ".join(bad[:5])
                + (f" (+{len(bad) - 5} more)" if len(bad) > 5 else "")
            )

    return validate
