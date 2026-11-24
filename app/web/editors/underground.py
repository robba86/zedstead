"""Underground triggers — ``<mission>/cfgundergroundtriggers.json`` (TODO H4).

This file had **no surface anywhere in the app** until 2026 Q3 — zero
references outside ``dayz-docs/`` — while shipping 30 triggers on Sakhal and 8
on Livonia. Chernarus ships an empty stub, which is why it stayed invisible.

It is positional data (each trigger a box at a world position with a size and
orientation, each breadcrumb a point with a radius), so the Visual Map Editor
is where it is moved and resized (``leaflet/useUndergroundLayer.js``, and
``MAP_EDITOR_LINKS`` sends this editor there). This editor is the whole file:
the map edits the triggers the terrain ships and adds none.

It was raw text until plans/76 step 2b; the Basic tab is now a form whose
top-level lists (``Triggers``, each with its ``Breadcrumbs``) are a tree
(``editors/_nested.py``) and whose top-level scalars, if a file ever has any,
are fields. **Every key is kept, always**: Sakhal's triggers carry 12 keys
and Livonia's 7, with ``Comment``, ``CustomSpawn``, ``ParentNetworkId`` and
``UseLinePointFade``
appearing on Sakhal alone. The tree never adds or drops a key, a save patches
the file it re-reads, and an entry that was moved or removed is fitted to the
entry it came from, not to its new neighbour (``_nested._template``).

**The type is shown, never set.** ``underground_state.trigger_type`` derives
it (breadcrumbs make a trigger transitional; without them it is outer at eye
accommodation 1 and inner otherwise), and the form lists it per trigger as a
read-only field, so the map, this form and anything later cannot disagree.

The file is written the way the map layer writes it: only the values the save
changed are replaced in the file's text (``app.shared.json_patch``), so
Bohemia's tabs, CRLF and ``"Position": [ x, y, z ]`` stay and a save with no
edits writes nothing. A trigger the save changed is checked against the wiki
first (``underground_state.check_trigger``). ``save_json_patched`` snapshots
first, so Analysis -> Change history is the undo. Not ``restorable``: zsm only backs
up the files a mod merge writes into, and no merge touches this one.
"""

from __future__ import annotations

from pathlib import Path

from app.maps import registry
from app.shared.config import DayzConfig
from app.shared.json_patch import save_json_patched
from app.shared.json_utils import load_json, load_json_preserving
from app.spawn_editor.underground_state import UNDERGROUND_FILE, check_trigger, trigger_type
from app.web.editors import EditorSpec, register
from app.web.editors._nested import apply_nested
from app.web.editors._paths import mission_dir
from app.web.editors._raw import raw_targets_from
from app.web.editors.expansion import _coerce, _scalar_field
from app.web.editors.text_fallback import _mission_file_ci

EDITOR_ID = "undergroundtriggers"
TYPE_LABELS = {"outer": "Outer", "inner": "Inner", "transitional": "Transitional"}

_find = _mission_file_ci(UNDERGROUND_FILE)


#: What the engine reads as decimals (`dayz-docs/wiki/pages/underground-areas-configuration.wiki`:
#: positions, rotations and sizes are vectors, eye accommodation a 0..1 weight,
#: a breadcrumb's radius metres; `LightLerp` is Sakhal's 0..1 blend). JSON
#: writes a whole float as ``1``, so without this list ``0.5`` was refused
#: there. `UseRaycast`, `UseLinePointFade` and `ParentNetworkId` stay whole.
FLOAT_KEYS = frozenset(
    {"Position", "Orientation", "Size", "EyeAccommodation", "InterpolationSpeed", "Radius", "LightLerp"}
)


#: Bohemia's own ambient sound, on all eight Livonia triggers (CE 1.29).
DEFAULT_SOUND_SET = "Underground_SoundSet"


def _templates(path: Path) -> dict:
    """What "Add" puts into an empty list (``nested_templates``).

    Without it an empty ``Triggers`` (Chernarus's stub) read as a list of
    names, and a typed name was refused on save as "A trigger must be an
    object". A new trigger carries Livonia's keys, its ambient sound
    included, since the form never adds a key later. It starts at the
    middle of the mission's terrain, not in the map's corner; its height is
    0 until it is typed in, as the map keeps a height when it moves a box.
    """
    middle = registry.map_size(registry.terrain_from_mission(path.parent.name)) / 2
    return {
        "Triggers": {
            "Position": [middle, 0.0, middle],
            "Orientation": [0.0, 0.0, 0.0],
            "Size": [10.0, 5.0, 10.0],
            "EyeAccommodation": 1.0,
            "AmbientSoundSet": DEFAULT_SOUND_SET,
            "InterpolationSpeed": 1.0,
            "Breadcrumbs": [],
        },
        "Breadcrumbs": {"Position": [middle, 0.0, middle], "EyeAccommodation": 1.0, "UseRaycast": 0, "Radius": -1.0},
    }


def _path(config: DayzConfig) -> Path | None:
    m_dir = mission_dir(config)
    return _find(m_dir) if m_dir else None


def _read(path: Path, *, preserving: bool = False) -> dict:
    data = (load_json_preserving if preserving else load_json)(path)
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} is not a JSON object. Correct it on the Raw tab.")
    return data


def _nested_keys(data: dict) -> set[str]:
    """Every list and object: the tree shows them, and only them may be sent."""
    return {key for key, value in data.items() if isinstance(value, (list, dict))}


def _type_section(data: dict) -> dict | None:
    triggers = data.get("Triggers")
    if not isinstance(triggers, list) or not triggers:
        return None
    fields = []
    for index, entry in enumerate(triggers):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("Comment") or "").strip() or f"Trigger {index + 1}"
        fields.append({
            "key": f"type:{index}",
            "label": name,
            "type": "str",
            "value": TYPE_LABELS[trigger_type(entry)],
            "readonly": True,
            "help": (
                "Derived, not stored: any breadcrumbs make a trigger transitional; "
                "without them it is outer at eye accommodation 1 and inner otherwise."
            ),
        })
    return {"title": "Trigger types", "fields": fields} if fields else None


def _load(config: DayzConfig) -> dict:
    path = _path(config)
    if path is None:
        return {"kind": "form", "sections": []}
    data = _read(path)
    sections = []
    fields = [
        field
        for key, value in data.items()
        if not isinstance(value, (list, dict)) and (field := _scalar_field(key, value))
    ]
    if fields:
        sections.append({"title": "Underground Triggers", "fields": fields})
    types = _type_section(data)
    if types:
        sections.append(types)
    nested = {key: data[key] for key in data if key in _nested_keys(data)}
    return {"kind": "form", "sections": sections, "nested": nested, "nested_templates": _templates(path)}


def _save(config: DayzConfig, values: dict) -> None:
    path = _path(config)
    if path is None:
        raise RuntimeError(f"{UNDERGROUND_FILE} not found for the current mission")
    data = _read(path, preserving=True)
    for key, raw in (values or {}).items():
        # Only a scalar the file has: never a new key, never a derived type.
        if key not in data or isinstance(data[key], (list, dict)):
            continue
        try:
            data[key] = _coerce(data[key], raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key}: {exc}") from exc
    before = data.get("Triggers")
    before = list(before) if isinstance(before, list) else []
    apply_nested(data, values, _nested_keys(data), floats=FLOAT_KEYS, templates=_templates(path))
    triggers = data.get("Triggers")
    for number, entry in enumerate(triggers if isinstance(triggers, list) else [], start=1):
        # Only what the save changed: a file written by hand keeps saving.
        if entry in before:
            continue
        try:
            check_trigger(entry)
        except ValueError as exc:
            raise ValueError(f"Trigger {number}: {exc}") from exc
    save_json_patched(path, data, inline_scalar_lists=True)


register(
    EditorSpec(
        id=EDITOR_ID,
        title="Underground Triggers",
        group="Mission",
        kind="form",
        exists=lambda config: _path(config) is not None,
        load=_load,
        save=_save,
        raw_targets=raw_targets_from((_path, "json")),
    )
)
