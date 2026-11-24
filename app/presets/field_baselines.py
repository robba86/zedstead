"""Per-field baselines for scaling stock mission files without drift.

Shared by Quick Tweaks (``<mission>/.zsm-tweaks.json``) and the preset
engine (``<mission>/.zsm-presets.json``, plans/50 C). Each keeps its own
sidecar file in the same shape::

    {"version": 1, "fields": {<file key>: {<entry name>: {<field>: {"base", "written"}}}}}

The baseline lives in a sidecar, never in a ``.backup``: a ``.backup`` holds
the file as it was when it was first backed up, so a baseline read from there
ignores every edit made since, and restoring ``types.xml`` from its ``.backup``
throws away every Types-page edit made since it was taken. Records are kept per
entry field, so overlapping writers share one baseline and the last apply wins.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any, Callable


def load_baselines(mission_dir: Path, sidecar: str) -> dict:
    """The ``fields`` map of the sidecar; empty when missing or unreadable.

    An unreadable sidecar re-baselines from disk, like .animal_tweaks.json.
    """
    try:
        data = json.loads((mission_dir / sidecar).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    fields = data.get("fields") if isinstance(data, dict) and data.get("version") == 1 else None
    return fields if isinstance(fields, dict) else {}


def save_baselines(mission_dir: Path, fields: dict, sidecar: str) -> None:
    """Write the sidecar, or delete it once nothing is tweaked any more.

    Empty records are dropped rather than stored: a reset leaves them behind,
    and a sidecar of empty maps would claim a baseline it doesn't have.
    """
    from app.shared.json_utils import atomic_write_text

    kept: dict = {}
    for file_key, entries in fields.items():
        live = {name: rec for name, rec in entries.items() if rec}
        if live:
            kept[file_key] = live

    path = mission_dir / sidecar
    if not kept:
        path.unlink(missing_ok=True)
        return
    atomic_write_text(path, json.dumps({"version": 1, "fields": kept}, indent=2) + "\n")


def scale_field(records: dict, key: str, on_disk: int, multiplier: float) -> int:
    """Scale one field from its baseline and record ``{base, written}`` under *key*.

    The recorded base holds only while the file still has what the last tweak
    wrote. Any other value on disk (a hand edit, a preset, the first tweak)
    becomes the new base — so repeats never compound, and Normal (1x) means the
    value before the first tweak or after the last outside edit.
    """
    rec = records.get(key)
    if isinstance(rec, dict) and rec.get("written") == on_disk and isinstance(rec.get("base"), int):
        base = rec["base"]
    else:
        base = on_disk
    # Round half up: monotonic, so min <= nominal <= max still holds after scaling.
    new = max(0, math.floor(base * multiplier + 0.5))
    records[key] = {"base": base, "written": new}
    return new


def reset_field(records: dict, key: str, on_disk: int) -> int | None:
    """The baseline to put back for one field, or None to leave the value alone.

    Dropping the record is the reset: with no record, the next tweak baselines
    from disk. A field that no longer holds what the tweak wrote was superseded
    by a hand edit or a preset, and that value *is* the default now — so the
    record goes and the value stays.
    """
    rec = records.pop(key, None)
    if isinstance(rec, dict) and rec.get("written") == on_disk and isinstance(rec.get("base"), int):
        return rec["base"]
    return None


def int_value(text: str | None) -> int | None:
    try:
        return int(text.strip()) if text else None
    except ValueError:
        return None  # a malformed value is left alone, not overwritten with 0


def file_records(fields: dict, file_key: str) -> dict:
    """A private copy of one file's records, committed only once its save succeeds."""
    records = fields.get(file_key)
    return copy.deepcopy(records) if isinstance(records, dict) else {}


def entry_records(records: dict, name: str) -> dict:
    entry = records.get(name)
    if not isinstance(entry, dict):
        entry = records[name] = {}
    return entry


# What to write into one field: the records for the entry, the field key, and the
# value on disk. Returns the new value, or None to leave the field alone.
Compute = Callable[[dict, str, int], int | None]


def write_elements(
    entry: Any, records: dict, names: tuple[str, ...],
    setter: Callable[[Any, str, int], None], compute: Compute,
) -> bool:
    """Recompute the numeric child elements *names* of an entry that has them."""
    changed = False
    for name in names:
        elem = entry.element.find(name)
        current = int_value(elem.text if elem is not None else None)
        if current is None:
            continue
        new = compute(records, name, current)
        if new is not None:
            setter(entry, name, new)
            changed = True
    return changed


def prune_only(mission_dir: Path, fields: dict, file_key: str, records: dict, sidecar: str) -> list[str]:
    """No value changed, so no file is reported — but a reset may still have
    dropped stale records, and those have to leave the sidecar."""
    if records != fields.get(file_key):
        fields[file_key] = records
        save_baselines(mission_dir, fields, sidecar)
    return []
