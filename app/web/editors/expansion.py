"""DayZ-Expansion's generated settings files.

Expansion writes **30 JSON files on its first boot** and reads them on every
one after that: 21 into ``<profiles>/ExpansionMod/Settings/`` (server-wide) and
9 into ``<mission>/expansion/settings/`` (per mission). Until that boot they do
not exist — which is why `exists` is per file and the Configs page's Expansion
tab says so rather than showing 30 empty forms.

**One generic adapter, no hand-written field list.** Every form is built from
the file's own JSON, so a key Expansion adds in its next release appears by
itself instead of being silently dropped on the next save. Three rules make
that safe:

- **A save patches, it never replaces.** `_save` re-reads the file, writes back
  only the keys the form sent *and the file already has*, and leaves everything
  else — including every nested list — exactly as it was. A stale browser tab
  cannot delete `Patrols`.
- **Only top-level scalars and scalar lists become fields.** Nested objects and
  lists of objects (`Patrols`, `RoamingLocations`, `Containers`, `ItemRarity`)
  are Expansion's real content; they travel as the payload's ``nested`` and the
  form renders each as a tree under the fields (plans/76 step 2,
  `editors/_nested.py`, whose rules a save of them keeps). A list of names or of numbers is a ``"list"`` field,
  edited item by item and sent as a real array (plans/76 step 1), never a
  comma-joined string — a class name may contain anything. A mixed list stays
  JSON text. An empty list is a list of names unless `EMPTY_OBJECT_LISTS`
  says its entries are objects; then it is a tree with a template to add from.
- **Flags stay integers.** Expansion writes its booleans as `0`/`1`, so they
  render as number fields and are written back as ints. Guessing "bool" from a
  0/1 int would rewrite a genuine count of 1 as `true`.

Every one of the 30 is a form. `AILocationSettings` and `SocialMediaSettings`
were raw text until plans/76 step 2b: their content is all lists of objects
(``RoamingLocations``, ``NoGoAreas``, the news feed and links), which a form
could not show before the tree. The Raw tab still edits the whole file.

**Out of scope here, deliberately**: the 151 catalog files under
``<profiles>/ExpansionMod/`` (`Market/` 67, `Traders/` 17, `Loadouts/` 16,
`Quests/` 51). Those are a catalog editor, not a settings form. Expansion's own
wiki lists them among the "settings" as *Market Traders* / *Market Categories*;
they are folders of catalog data, and that is why they are not here.

**The table is 30 because a real boot wrote 30** (2026 Q3, checked against
both the wiki index and the mods themselves). A byte scan of the installed PBOs
references **33** ``*Settings.json`` names: the 30 here plus ``DebugSettings``
(Core), ``QuestPatrolSettings`` (Quests) and ``eAISettings`` (AI), none of which
a full 21-mod boot produced. They are left out rather than added as cards that
would read "missing" forever — which folder each would land in is not something
the scan answers, and guessing one is how an editor ends up pointing at a path
that never exists. If a server is ever seen with one, add it with its measured
location. The reverse case is in the same scan: the wiki documents a mission-side
``Personal Storage Settings``, and **no PBO in this build references it**, so a
row for it would have been a permanent dead card.
"""

from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass
from pathlib import Path

from app.shared.config import DayzConfig
from app.shared.json_patch import save_json_patched
from app.shared.json_utils import load_json
from app.web.editors import EditorSpec, register
from app.web.editors._mod_json import coerce_list, list_field, list_item_type
from app.web.editors._nested import apply_nested, is_nested
from app.web.editors._paths import mission_dir
from app.web.editors._raw import raw_targets_from

GROUP = "Expansion"
PROFILE_DIR = ("ExpansionMod", "Settings")
MISSION_DIR = ("expansion", "settings")

#: How many not-shown keys the section heading names before it stops counting.
_MAX_NAMED = 3

#: Expansion's own schema version. It is not a setting: Expansion compares it on
#: boot and migrates (rewrites) the file when it differs, so a stray edit in a
#: number box would hand the whole file back to defaults. It stays fully visible
#: and editable on the Raw tab, and the section heading says where it went —
#: dropping a key from a form without saying so is the thing this file's nested
#: handling exists to avoid.
_NOT_A_SETTING = frozenset({"m_Version"})


#: A vector as Expansion writes it: ``[x, y, z]``, decimals.
_VECTOR = [0.0, 0.0, 0.0]

#: The lists of objects a stock file writes empty, per file, each with what a
#: new entry looks like (plans/76). An empty list says nothing about its
#: entries, so without this it was a list of names, and a typed name was a
#: string where Expansion reads an object. Each template is the class
#: Expansion's script serialises into that list (DayZ-Expansion-Scripts,
#: read in 2026 Q4): every key it writes, and a neutral value per type, or the
#: class's own default where it sets one. Booleans are 0/1, as Expansion
#: writes them. The template is sent as the payload's ``nested_templates`` and
#: the save fits a new entry to it (`_nested.conform`), so ``5`` typed in an
#: int is written as ``5``. A list the file has entries in is fitted to those,
#: as before; a list of names (``BlacklistedWords``, ``Admins``, ...) is not
#: here and stays a list field.
EMPTY_OBJECT_LISTS: dict[str, dict[str, dict]] = {
    "AILocationSettings.json": {
        # ExpansionAINoGoAreaConfig (AI/.../locations/expansionailocationsettings.c)
        "NoGoAreas": {"Name": "", "Position": _VECTOR, "Radius": 0.0, "Height": 0.0},
    },
    "BaseBuildingSettings.json": {
        # ExpansionBuildNoBuildZone; IsWhitelist defaults to true in its constructor
        "Zones": {
            "Name": "",
            "Center": _VECTOR,
            "Radius": 0.0,
            "Items": [],
            "IsWhitelist": 1,
            "CustomMessage": "",
        },
    },
    "MapSettings.json": {
        # ExpansionMarkerData (Core/.../Markers/ExpansionMarkerData.c), the
        # list's declared type. Its constructor sets m_Persist and m_Visibility
        # EXPANSION_MARKER_VIS_WORLD | EXPANSION_MARKER_VIS_MAP (2 | 4).
        "ServerMarkers": {
            "m_UID": "",
            "m_Visibility": 6,
            "m_Is3D": 0,
            "m_Text": "",
            "m_IconName": "",
            "m_Color": 0,
            "m_Position": _VECTOR,
            "m_Locked": 0,
            "m_Persist": 1,
        },
    },
    "MarketSettings.json": {
        # ExpansionMarketNetworkCategory. Expansion clears this list on every
        # load and never saves it (ExpansionMarketSettings.OnLoad/OnSave), so
        # an entry here does nothing; the template only keeps one from being
        # written as a name.
        "NetworkCategories": {
            "DisplayName": "",
            "Icon": "",
            "Color": 0,
            "CategoryID": 0,
            "IsExchange": 0,
        },
        # ExpansionMarketSpawnPosition (not V1's Direction: the current class
        # writes Orientation)
        "LandSpawnPositions": {"Position": _VECTOR, "Orientation": _VECTOR},
        "AirSpawnPositions": {"Position": _VECTOR, "Orientation": _VECTOR},
        "WaterSpawnPositions": {"Position": _VECTOR, "Orientation": _VECTOR},
        "TrainSpawnPositions": {"Position": _VECTOR, "Orientation": _VECTOR},
    },
    "SafeZoneSettings.json": {
        # ExpansionSafeZoneCircle, ExpansionSafeZonePolygon, ExpansionSafeZoneCylinder
        # (Core/.../Settings/Safezone/). A polygon starts with one corner;
        # Copy on it adds the next.
        "CircleZones": {"Center": _VECTOR, "Radius": 0.0},
        "PolygonZones": {"Positions": [_VECTOR]},
        "CylinderZones": {"Center": _VECTOR, "Radius": 0.0, "Height": 0.0},
    },
    "SpawnSettings.json": {
        # ExpansionSpawnLocation (SpawnSelection/.../Spawning/ExpansionSpawnLocation.c);
        # TerritoryID is NonSerialized. One position to start, Copy adds more.
        "SpawnLocations": {"Name": "", "Positions": [_VECTOR], "UseCooldown": 0},
    },
}


@dataclass(frozen=True)
class ExpansionEditor:
    id: str
    title: str
    filename: str
    #: "profile" = <profiles>/ExpansionMod/Settings, "mission" = <mission>/expansion/settings
    where: str


SETTINGS: tuple[ExpansionEditor, ...] = (
    # --- <profiles>/ExpansionMod/Settings — server-wide -------------------
    ExpansionEditor("expansion-core", "Expansion Core", "CoreSettings.json", "profile"),
    ExpansionEditor("expansion-general", "Expansion General", "GeneralSettings.json", "profile"),
    ExpansionEditor("expansion-ai", "Expansion AI", "AISettings.json", "profile"),
    ExpansionEditor("expansion-airdrop", "Expansion Airdrop", "AirdropSettings.json", "profile"),
    ExpansionEditor("expansion-book", "Expansion Book", "BookSettings.json", "profile"),
    ExpansionEditor("expansion-chat", "Expansion Chat", "ChatSettings.json", "profile"),
    ExpansionEditor(
        "expansion-damagesystem", "Expansion Damage System", "DamageSystemSettings.json", "profile"
    ),
    ExpansionEditor("expansion-garage", "Expansion Garage", "GarageSettings.json", "profile"),
    ExpansionEditor("expansion-logs", "Expansion Logs", "LogsSettings.json", "profile"),
    ExpansionEditor("expansion-mission", "Expansion Missions", "MissionSettings.json", "profile"),
    ExpansionEditor(
        "expansion-monitoring", "Expansion Monitoring", "MonitoringSettings.json", "profile"
    ),
    ExpansionEditor("expansion-nametags", "Expansion Name Tags", "NameTagsSettings.json", "profile"),
    ExpansionEditor(
        "expansion-notification", "Expansion Notifications", "NotificationSettings.json", "profile"
    ),
    ExpansionEditor(
        "expansion-notificationscheduler",
        "Expansion Notification Scheduler",
        "NotificationSchedulerSettings.json",
        "profile",
    ),
    ExpansionEditor("expansion-party", "Expansion Party", "PartySettings.json", "profile"),
    ExpansionEditor(
        "expansion-playerlist", "Expansion Player List", "PlayerListSettings.json", "profile"
    ),
    ExpansionEditor("expansion-quest", "Expansion Quests", "QuestSettings.json", "profile"),
    ExpansionEditor("expansion-raid", "Expansion Raid", "RaidSettings.json", "profile"),
    ExpansionEditor(
        "expansion-socialmedia", "Expansion Social Media", "SocialMediaSettings.json", "profile"
    ),
    ExpansionEditor("expansion-territory", "Expansion Territory", "TerritorySettings.json", "profile"),
    ExpansionEditor("expansion-vehicle", "Expansion Vehicles", "VehicleSettings.json", "profile"),
    # --- <mission>/expansion/settings — per mission -----------------------
    ExpansionEditor(
        "expansion-ai-locations", "Expansion AI Locations", "AILocationSettings.json", "mission"
    ),
    ExpansionEditor(
        "expansion-ai-patrols", "Expansion AI Patrols", "AIPatrolSettings.json", "mission"
    ),
    ExpansionEditor(
        "expansion-basebuilding",
        "Expansion Base Building",
        "BaseBuildingSettings.json",
        "mission",
    ),
    ExpansionEditor("expansion-hardline", "Expansion Hardline", "HardlineSettings.json", "mission"),
    ExpansionEditor("expansion-map", "Expansion Map", "MapSettings.json", "mission"),
    ExpansionEditor("expansion-market", "Expansion Market", "MarketSettings.json", "mission"),
    ExpansionEditor(
        "expansion-p2pmarket", "Expansion P2P Market", "P2PMarketSettings.json", "mission"
    ),
    ExpansionEditor("expansion-safezone", "Expansion Safe Zones", "SafeZoneSettings.json", "mission"),
    ExpansionEditor("expansion-spawn", "Expansion Spawn", "SpawnSettings.json", "mission"),
)


def settings_path(config: DayzConfig, entry: ExpansionEditor) -> Path | None:
    """Where *entry*'s file lives, or None when the mission isn't resolvable."""
    if entry.where == "profile":
        return config.server_dir_path.joinpath(config.profiles_path, *PROFILE_DIR, entry.filename)
    m_dir = mission_dir(config)
    return m_dir.joinpath(*MISSION_DIR, entry.filename) if m_dir else None


#: How a few top-level number keys are drawn, by key name, whichever file
#: holds them: a 0/1 switch (`widget: "flag"`, saved as 0 or 1), a picker
#: whose options say what each number does, a 0.0-1.0 share as a percentage
#: slider (`min`/`max`, webui RangeField), or a unit in the box.
#: The picker meanings are the maintainer's notes on Expansion's values.
_FIELD_HINTS: dict[str, dict] = {
    **dict.fromkeys(("ForceExactCEItemLifetime", "GravecrossDeleteBody", "BuildingInteriors"), {"widget": "flag"}),
    "EnableLamps": {"choices": ["0", "1", "2", "3"], "choice_help": {
        "0": "Disabled: street lights are off.",
        "1": "Generators: unused and broken, the generators would need a fix to work.",
        "2": "Always on: street lights give light, but some stay off on purpose.",
        "3": "Always on everywhere: every light is forced on.",
    }},
    "BuildingIvys": {"choices": ["0", "1", "2"], "choice_help": {
        "0": "No custom ivies are added to the map.",
        "1": "Custom ivies only at set places (the default, mainly for ChernarusPlus).",
        "2": "Custom ivies on all buildings across the map.",
    }},
    # Only AISettings.json's own values: in AIPatrolSettings.json, 0 or below
    # means "take AISettings.json's", which a 0-100 % slider would hide.
    **dict.fromkeys(("AccuracyMin", "AccuracyMax"), {"min": 0.0, "max": 1.0, "file": "AISettings.json"}),
    "MaxFlankingDistance": {"unit": "meters"},
}


def _hinted(field: dict, filename: str) -> dict:
    """*field* of *filename* with its `_FIELD_HINTS` entry, when the entry
    names no other file and the value fits it: a switch only for a 0 or 1
    int, a picker only for an int, a share only for a number."""
    hint = dict(_FIELD_HINTS.get(field["key"]) or {})
    kind, value = field["type"], field.get("value")
    if not hint or kind not in ("int", "float") or hint.pop("file", filename) != filename:
        return field
    if hint.get("widget") == "flag" and not (kind == "int" and value in (0, 1)):
        return field
    if "choices" in hint and kind != "int":
        return field
    return {**field, **hint}


def _scalar_field(key: str, value) -> dict | None:
    """One form field for a top-level key, or None when it isn't flat.

    The label is the key verbatim: these names are what Expansion's own wiki
    and every support thread use. The form shows a label that is only its key
    as words and puts the key in the tooltip (webui/src/fieldLabel.js), so the
    name stays one hover away for a lookup.
    """
    if isinstance(value, bool):
        return {"key": key, "label": key, "type": "bool", "value": value}
    if isinstance(value, int):
        return {"key": key, "label": key, "type": "int", "value": value}
    if isinstance(value, float):
        return {"key": key, "label": key, "type": "float", "value": value}
    if isinstance(value, str):
        return {"key": key, "label": key, "type": "str", "value": value}
    item = list_item_type(value)
    if item is not None:
        return {**list_field(key, key, value, item), "label": key}
    if isinstance(value, list) and all(
        isinstance(item, (str, int, float, bool)) for item in value
    ):
        # A mixed list (names and numbers, booleans): no item type to edit
        # it item by item, so it stays JSON text.
        return {
            "key": key,
            "label": key,
            "type": "str",
            "value": json.dumps(value),
            "help": "A JSON list — keep the brackets and quotes.",
        }
    return None


def _number(raw) -> float:
    """A form value as a finite number, or a ValueError worded for the admin.

    Python's own wording reached the toast verbatim ("could not convert string
    to float: ''"), and `inf` got through: an int field raised OverflowError
    with no field name on it, and a float field wrote `Infinity`, which is not
    JSON at all — Expansion could no longer read its own file.
    """
    text = "" if raw is None else str(raw).strip()
    if not text:
        raise ValueError("needs a number — it can't be left empty")
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not math.isfinite(value):
        raise ValueError(f"{text!r} is not a number")
    return value


def _coerce(current, raw):
    """Fit the form's string back onto the type the file already uses."""
    if isinstance(current, bool):
        return bool(raw) and str(raw).lower() not in ("0", "false", "")
    if isinstance(current, int):
        # int() cut 4.5 to 4 without a word; the file holds a whole number here.
        value = _number(raw)
        if value != int(value):
            raise ValueError(f"{raw!r} is not a whole number, and the file holds one here")
        return int(value)
    if isinstance(current, float):
        return _number(raw)
    if isinstance(current, list) and list_item_type(current) is not None:
        return coerce_list(raw, list_item_type(current), current)
    if isinstance(current, list):
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            parsed = None
        if not isinstance(parsed, list):
            raise ValueError("expected a JSON list, e.g. [\"a\", \"b\"]")
        return parsed
    return "" if raw is None else str(raw)


def _raw_only_note(not_shown: list[str]) -> str:
    """One line naming what the form is *not* showing, or "" when it shows all.

    A form that quietly omits `Patrols` from AIPatrolSettings.json reads as a
    complete view of the file, which it is very much not. It used to be glued
    onto the section heading ("Expansion AI — m_Version is on the Raw tab");
    it is the section's note now, so the heading stays the file's name.
    """
    if not not_shown:
        return ""
    shown = ", ".join(not_shown[:_MAX_NAMED])
    if len(not_shown) > _MAX_NAMED:
        shown += f" and {len(not_shown) - _MAX_NAMED} more"
    verb = "is" if len(not_shown) == 1 else "are"
    return f"{shown} {verb} on the Raw tab."


def _empty_object_lists(entry: ExpansionEditor, data: dict) -> dict[str, dict]:
    """{key: template} for each list of objects *data* has empty."""
    return {
        key: copy.deepcopy(template)
        for key, template in EMPTY_OBJECT_LISTS.get(entry.filename, {}).items()
        if data.get(key) == []
    }


def _is_tree(key: str, value, empty: dict) -> bool:
    """Whether a top-level key is shown as a tree rather than a field."""
    if key in _NOT_A_SETTING:
        return False
    return key in empty or (_scalar_field(key, value) is None and is_nested(value))


def _nested_keys(data: dict, empty: dict) -> set[str]:
    """The keys a form shows as a tree, and so the only ones a save may send."""
    return {key for key, value in data.items() if _is_tree(key, value, empty)}


def _register(entry: ExpansionEditor) -> None:
    def _path(config: DayzConfig) -> Path | None:
        return settings_path(config, entry)

    def _existing(config: DayzConfig) -> Path | None:
        path = _path(config)
        return path if path is not None and path.is_file() else None

    def _exists(config: DayzConfig) -> bool:
        return _existing(config) is not None

    def _load_form(config: DayzConfig) -> dict:
        path = _existing(config)
        if path is None:
            return {"kind": "form", "sections": []}
        data = load_json(path)
        if not isinstance(data, dict):
            raise RuntimeError(f"{entry.filename} is not a JSON object.")
        fields: list[dict] = []
        not_shown: list[str] = []
        nested: dict = {}
        empty = _empty_object_lists(entry, data)
        for key, value in data.items():
            if _is_tree(key, value, empty):
                nested[key] = value
                continue
            field = None if key in _NOT_A_SETTING else _scalar_field(key, value)
            if field is not None:
                fields.append(_hinted(field, entry.filename))
            else:
                not_shown.append(key)
        return {
            "kind": "form",
            "sections": [{"title": entry.title, "note": _raw_only_note(not_shown), "fields": fields}],
            "nested": nested,
            "nested_templates": empty,
        }

    def _save_form(config: DayzConfig, values: dict) -> None:
        path = _existing(config)
        if path is None:
            from app.ops.expansion_ops import missing_settings_message

            raise RuntimeError(missing_settings_message(config, entry.filename))
        data = load_json(path)
        if not isinstance(data, dict):
            raise RuntimeError(f"{entry.filename} is not a JSON object.")
        empty = _empty_object_lists(entry, data)
        tree = _nested_keys(data, empty)
        for key, raw in (values or {}).items():
            # Never invent a key: a form can only edit what the file has, so a
            # stale tab or a typo can't grow the file a setting Expansion
            # ignores (or, worse, mis-parses). `_NOT_A_SETTING` is refused on
            # the way in as well — the form never offers it, so a value for it
            # can only come from a hand-made request. A tree key is edited
            # only through `$nested`: as a field value, an empty list of
            # objects would take names.
            if key not in data or key in _NOT_A_SETTING or key in tree:
                continue
            try:
                data[key] = _coerce(data[key], raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key}: {exc}") from exc
        apply_nested(data, values, tree, templates=empty)
        save_json_patched(path, data)

    register(
        EditorSpec(
            id=entry.id,
            title=entry.title,
            group=GROUP,
            kind="form",
            exists=_exists,
            load=_load_form,
            save=_save_form,
            # The Raw tab is where the nested content is edited, so it points at
            # the file whether or not it exists yet — `raw_targets_from` drops a
            # target only when the path can't be resolved at all.
            raw_targets=raw_targets_from((_path, "json")),
        )
    )


for _entry in SETTINGS:
    _register(_entry)
