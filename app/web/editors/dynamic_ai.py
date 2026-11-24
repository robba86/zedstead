"""Dynamic AI (DayZ-Dynamic-AI-Addon) settings editors (plans/47 C, decision D2).

A dedicated editor rather than the generic Expansion adapter. The file's real
content is four lists of objects (``Group``, ``Point``, ``Location``,
``Audio``), which the generic adapter would send to the Raw tab. So there is
one grouped form for the top-level settings and a table for each list, plus one
for the notification file's entries.

Rules, each of them load-bearing:

- **A save patches.** It re-reads the file and changes only what the editor
  shows. Unknown keys, positions (the map layers own those) and anything the
  next mod release adds stay exactly as they were.
- **Whole numbers stay whole.** A decimal in a field DayZ loads as an int is
  what the mod's author diagnosed as stopping a server from starting, so the
  save is refused with the field named, on the Basic tab and on the Raw tab
  (``raw_validator``).
- **``Version`` is not a field.** The mod compares it on load and switches
  itself off for any value it does not know. It stays on the Raw tab.
- **``SpatialPlayerSettings.json`` is never written or offered.** It is the
  mod's own player database, and the author says to leave it alone.
- Faction and loadout pickers are fed by the install
  (``mod_conflict.registered_factions``, ``ExpansionMod/Loadouts``). A value
  outside the list is kept and stays visible, never swapped for the first option.

Every rule comes from ``app/ops/dynamic_ai.py``, which reads the same files for
the Doctor; this module only edits them. The form and table machinery is
shared with the other pre-configured mods (``_mod_json.py``).
"""

from __future__ import annotations

import json
from pathlib import Path

from app.ops import ai_refs
from app.ops import dynamic_ai as ops
from app.shared.config import DayzConfig
from app.shared.json_utils import load_json
from app.web.editors import EditorSpec, register
from app.web.editors._mod_json import (
    F,
    Field,
    Form,
    Table,
    form_load,
    form_save,
    table_load,
    table_save,
)
from app.web.editors._raw import raw_targets_from

GROUP = "Dynamic AI"
FMT = ops.V21

# A closed list's meanings show under each value in the picker (F's dict form).
HUNT_MODES = {
    "0": "No waypoints: they stand still.",
    "1": "Hunt the player.",
    "2": "Go where the player was.",
    "3": "Idle.",
    "4": "Run a little, then stand.",
    "5": "Wander on waypoints.",
    "6": "Follow at a distance.",
    "7": "",  # valid, but no source says what it does
    "8": "",
}
LOOTABLE = {"0": "Nothing.", "1": "Everything.", "2": "A random part.", "3": "Only the leader's."}

_HUNT_HELP = (
    "How spawned AI behave. 1 hunt the player, 2 go where the player was, 3 idle, "
    "4 run a little then stand, 5 wander on waypoints, 6 follow at a distance. "
    "0 gives them no waypoints, so they stand still. Only 0 to 8 are valid."
)
_LOOT_HELP = "What the AI drop when killed: 0 nothing, 1 everything, 2 a random part, 3 only the leader's."
_FACTION_HELP = "An Expansion AI faction. The list is what the installed mods register."
_MESSAGE_TYPES = {
    "0": "Log only.",
    "1": "A chat line.",
    "2": "A chat line.",
    "3": "A notification.",
    "4": "A notification.",
    "5": "Only if the player carries a GPS receiver.",
}
_LOCATION_MODES = {
    "0": "Off.",
    "1": "Spawn on the ground below each spawn position.",
    "2": "Spawn exactly at it, height included (for snipers on towers).",
}
_LOADOUT_HELP = "A file in ExpansionMod/Loadouts, exact name with .json. A group whose file is missing is skipped."


# ── The settings form ───────────────────────────────────────────────────

SECTIONS: tuple[tuple[str, tuple[Field, ...]], ...] = (
    ("Timers (minutes)", (
        F("Spatial_MinTimer", "float",
          "Shortest wait between spawn checks, in minutes. The mod raises anything under 5 to 5. "
          "A negative value is its debug switch: debug lines in the log, markers on the map for "
          "players with Expansion Navigation, and the value used without its sign.", unit="minutes"),
        F("Spatial_MaxTimer", "float",
          "Longest wait between spawn checks. Below the minimum it is set equal to it.", unit="minutes"),
        F("EngageTimer", "float", "Engagement time in minutes. The mod enforces at least 5.", unit="minutes"),
        F("CleanupTimer", "float",
          "Minutes before spawned AI are cleaned up. Below EngageTimer the mod uses EngageTimer + 1.", unit="minutes"),
    )),
    ("Distances (metres)", (
        F("MinDistance", "int", "Closest a group spawns to the player. The mod raises anything under 120 to 120.", unit="meters"),
        F("MaxDistance", "int", "Farthest a group spawns. Below MinDistance it becomes MinDistance + 20.", unit="meters"),
        F("MinimumPlayerDistance", "int", unit="meters"),
    )),
    ("Who gets AI", (
        F("MaxAI", "int",
          "How many spawned sets may exist across the whole mod, not AI per group. Under 1 the mod "
          "switches itself off. Every AI costs server frames; players have been kicked on hardware "
          "that could not keep up."),
        F("PlayerChecks", "int",
          "0 or more: AI spawn only on party leaders. Negative: on anyone. The most common reason "
          "for \"AI never spawn on me\" is a value of 0 or more."),
        F("MaxSoloPlayers", "int"),
        F("GroupDifficulty", "int"),
        F("HuntMode", "int", _HUNT_HELP, HUNT_MODES),
    )),
    ("Points, locations and audio", (
        F("Points_Enabled", "int",
          "0 off. 1: inside a point, its own spawns replace the group spawns. 2: AI spawn only inside "
          "points and locations. A point marked Safe is a no-spawn zone.", {
            "0": "Off.",
            "1": "Inside a point, its own spawns replace the group spawns.",
            "2": "AI spawn only inside points and locations.",
        }),
        F("Locations_Enabled", "int",
          "0 off. 1: spawn on the ground below each spawn position. 2: spawn exactly at it, height "
          "included (for snipers on towers). \"They spawn under the tower\" and \"they fall from the "
          "sky\" are this setting and the Y coordinate.", _LOCATION_MODES),
        F("Audio_Enabled", "int", "Sound-triggered locations. 0 off, 1 and 2 as for locations.", _LOCATION_MODES),
    )),
    ("Active hours", (
        F("ActiveHoursEnabled", "int",
          "0 off. 1: only between the start and stop times below. 2: groups are picked by time of "
          "day through the notification entries. 3: by player age. In 2 and 3 a group no "
          "notification entry names never spawns.", {
            "0": "Off.",
            "1": "Only between the start and stop times below.",
            "2": "Groups are picked by time of day through the notification entries.",
            "3": "Groups are picked by player age.",
        }),
        F("ActiveStartTime", "float", "Hour of day, 0 to 24."),
        F("ActiveStopTime", "float", "Hour of day, 0 to 24."),
        F("MinimumAge", "int", "A player's age before AI spawn on them. A negative value uses playtime (beta)."),
    )),
    ("Messages", (
        F("MessageType", "int",
          "0 log only. 1 or 2 a chat line. 3 or 4 a notification. 5 only if the player carries a GPS receiver.",
          _MESSAGE_TYPES),
        F("MessageTitle", "str"),
        F("MessageText", "str"),
    )),
    ("Checks to skip", tuple(
        F(key, "bool", f"On: the mod skips this check ({what}) when deciding whether to spawn.")
        for key, what in (
            ("Spatial_InVehicle", "the player is in a vehicle"),
            ("Spatial_IsBleeding", "the player is bleeding"),
            ("Spatial_IsRestrained", "the player is restrained"),
            ("Spatial_IsUnconscious", "the player is unconscious"),
            ("Spatial_IsInSafeZone", "the player is in an Expansion safe zone"),
            ("Spatial_TPSafeZone", "the player is in a TraderPlus safe zone"),
            ("Spatial_InOwnTerritory", "the player is in their own Expansion territory"),
        )
    )),
    ("Placeholders", (
        F("TargetBone", "str", "A placeholder, per the mod's author."),
        F("LootWhitelist", "list", "A placeholder, per the mod's author."),
    )),
)

#: Not settings: the version is the mod's (refused on save, kept on Raw), and
#: the four lists have their own tables.
NOT_ON_FORM = frozenset({FMT.version_key, *ops.SPAWN_LISTS})


# ── Paths ───────────────────────────────────────────────────────────────


def _settings_path(config: DayzConfig) -> Path:
    return ops.settings_dir(config, FMT) / FMT.settings


def _notes_path(config: DayzConfig) -> Path:
    return ops.settings_dir(config, FMT) / FMT.notifications


# ── The tables ──────────────────────────────────────────────────────────

_COUNTS = (
    F("Spatial_MinCount", "int", "Fewest AI in the set. Under 0, or above the maximum, the entry is skipped."),
    F("Spatial_MaxCount", "int", "Most AI in the set. Under 1 the entry is skipped."),
)
_TAIL = (
    F("Spatial_Lootable", "int", _LOOT_HELP, LOOTABLE),
    F("Spatial_Chance", "float", share=True),
    F("Spatial_MinAccuracy", "float", share=True),
)


_POSITIONS_NOTE = (
    "Positions are not in this table. They stay as they are when you save, and a "
    "duplicated entry starts at its original's position."
)


def _T(id, title, section, columns, *, notifications=False, add_row=True, template=None) -> Table:
    """A Dynamic AI table over the settings file, or the notification file."""
    return Table(
        id=id,
        title=title,
        mod="Dynamic AI",
        path=_notes_path if notifications else _settings_path,
        section=section,
        columns=columns,
        add_row=add_row,
        template=template or {},
        name_key="Spatial_Name",
        # Entries that carry positions are only ever duplicated: a blank row
        # would land at the map origin, and D3 settled "new entries by duplicate".
        warnings=() if add_row else (_POSITIONS_NOTE,),
    )


TABLES: tuple[Table, ...] = (
    _T("dynai-groups", "Dynamic AI Groups", "Group", (
        F("Spatial_Name", "str", "Also the key the notification entries match on."),
        F("Spatial_Faction", "str", _FACTION_HELP, "factions"),
        F("Spatial_Loadout", "str", _LOADOUT_HELP, "loadouts"),
        *_COUNTS,
        F("Spatial_Weight", "float", "How likely this group is picked against the others."),
        *_TAIL,
        F("Spatial_MaxAccuracy", "float", share=True),
        F("Spatial_UnlimitedReload", "bool"),
    ), template={
        "Spatial_MinCount": 1, "Spatial_MaxCount": 3, "Spatial_Weight": 100.0,
        "Spatial_Loadout": "", "Spatial_Faction": "", "Spatial_Name": "",
        "Spatial_Lootable": 1, "Spatial_Chance": 0.5, "Spatial_MinAccuracy": 0.25,
        "Spatial_MaxAccuracy": 0.66, "Spatial_UnlimitedReload": 0,
    }),
    _T("dynai-points", "Dynamic AI Points", "Point", (
        F("Spatial_Name", "str"),
        F("Spatial_Safe", "bool", "On: a no-spawn zone."),
        F("Spatial_Radius", "float", "Metres. Under 0 the mod uses 100.", unit="meters"),
        F("Spatial_ZoneLoadout", "list", "Loadout files for this point."),
        *_COUNTS,
        F("Spatial_HuntMode", "int", _HUNT_HELP + " Out of range becomes 3.", HUNT_MODES),
        F("Spatial_Faction", "str", _FACTION_HELP, "factions"),
        *_TAIL,
        F("Spatial_MaxAccuracy", "float", share=True),
        F("Spatial_UnlimitedReload", "bool"),
    ), add_row=False),
    _T("dynai-locations", "Dynamic AI Locations", "Location", (
        F("Spatial_Name", "str"),
        F("Spatial_TriggerRadius", "float", "Metres around the trigger position.", unit="meters"),
        F("Spatial_ZoneLoadout", "str", _LOADOUT_HELP + " The mod does not check this one.", "loadouts"),
        *_COUNTS,
        F("Spatial_HuntMode", "int", _HUNT_HELP, HUNT_MODES),
        F("Spatial_Faction", "str", _FACTION_HELP, "factions"),
        *_TAIL,
        F("Spatial_MaxAccuracy", "float", share=True),
        F("Spatial_Timer", "float", "Minutes.", unit="minutes"),
        F("Spatial_SpawnMode", "int", "0 or 1; anything else becomes 0.", ["0", "1"]),
        F("Spatial_UnlimitedReload", "bool"),
    ), add_row=False),
    _T("dynai-audio", "Dynamic AI Audio", "Audio", (
        F("Spatial_Name", "str"),
        F("Spatial_TriggerRadius", "float", "Metres around the trigger position.", unit="meters"),
        F("Spatial_ZoneLoadout", "str", _LOADOUT_HELP + " The mod does not check this one.", "loadouts"),
        *_COUNTS,
        F("Spatial_HuntMode", "int", _HUNT_HELP, HUNT_MODES),
        F("Spatial_Faction", "str", _FACTION_HELP, "factions"),
        *_TAIL,
        F("Spatial_Timer", "float", "Minutes.", unit="minutes"),
        F("Spatial_Sensitivity", "float"),
        F("Spatial_SpawnMode", "int", "0 or 1; anything else becomes 0.", ["0", "1"]),
        F("Spatial_UnlimitedReload", "bool"),
    ), add_row=False),
    _T("dynai-notifications", "Dynamic AI Notifications", "notification", (
        F("Spatial_Name", "str",
          "The group this entry covers, matched exactly. It decides which groups can spawn when "
          "ActiveHoursEnabled is 2 or 3.", "groups"),
        F("StartTime", "float", "Hour of day."),
        F("StopTime", "float", "Hour of day."),
        F("AgeTime", "float"),
        F("MessageType", "int", "0 log only, 1-2 chat, 3-4 notification, 5 GPS receiver only.",
          _MESSAGE_TYPES),
        F("MessageTitle", "str"),
        F("MessageText", "list", "The message lines."),
    ), notifications=True, template={
        "Spatial_Name": "", "StartTime": 0.0, "StopTime": 24.0, "AgeTime": 0.0,
        "MessageType": 1, "MessageTitle": "", "MessageText": [],
    }),
)


# ── Pickers ─────────────────────────────────────────────────────────────


def _install_choices(config: DayzConfig) -> dict[str, list[str]]:
    # The mod resolves factions through Expansion, which takes RANDOM (plans/64 D2).
    choices = ai_refs.install_choices(config, loadout_stems=False, random=True)
    groups: list[str] = []
    settings = _settings_path(config)
    if settings.is_file():
        try:
            doc = load_json(settings)
            groups = sorted({
                g.get("Spatial_Name") for g in doc.get("Group") or []
                if isinstance(g, dict) and g.get("Spatial_Name")
            })
        except (OSError, ValueError, AttributeError):
            groups = []
    return {**choices, "groups": groups}


# ── The form ────────────────────────────────────────────────────────────


FORM = Form(mod="Dynamic AI", path=_settings_path, sections=SECTIONS, not_on_form=NOT_ON_FORM)


def _form_load(config: DayzConfig) -> dict:
    # What is *not* on the form (Version, the four lists) is said in the page
    # intro (webui/src/pageHelp.js), not squeezed into a section title.
    return form_load(config, FORM, _install_choices(config))


def _form_save(config: DayzConfig, values: dict) -> None:
    form_save(config, FORM, values)


# ── The Raw tab ─────────────────────────────────────────────────────────


def raw_validator(content: str) -> None:
    """Refuse a Raw save the mod would choke on: a decimal in a whole-number
    field. JSON syntax is checked by the Raw tab itself."""
    doc = json.loads(content)
    if not isinstance(doc, dict):
        raise ValueError("The file must be a JSON object.")
    bad = ops._decimals_in_whole_numbers(doc, FMT)
    if bad:
        raise ValueError(
            "DayZ loads these as whole numbers, and a decimal there has stopped servers from "
            "starting: " + ", ".join(bad[:5]) + (f" (+{len(bad) - 5} more)" if len(bad) > 5 else "")
        )


def _notes_validator(content: str) -> None:
    doc = json.loads(content)
    if not isinstance(doc, dict):
        raise ValueError("The file must be a JSON object.")
    bad = [
        f"notification[{i}].MessageType = {n.get('MessageType')!r}"
        for i, n in enumerate(doc.get("notification") or [])
        if isinstance(n, dict) and isinstance(n.get("MessageType"), float)
    ]
    if bad:
        raise ValueError("DayZ loads these as whole numbers: " + ", ".join(bad))


def _settings_targets(config: DayzConfig):
    targets = raw_targets_from((_settings_path, "json"))(config)
    for t in targets:
        t.validate = raw_validator
    return targets


def _notes_targets(config: DayzConfig):
    targets = raw_targets_from((_notes_path, "json"))(config)
    for t in targets:
        t.validate = _notes_validator
    return targets


# ── Registration ────────────────────────────────────────────────────────

register(
    EditorSpec(
        id="dynai-settings",
        title="Dynamic AI Settings",
        group=GROUP,
        kind="form",
        exists=lambda config: _settings_path(config).is_file(),
        load=_form_load,
        save=_form_save,
        raw_targets=_settings_targets,
    )
)


def _register_table(table: Table) -> None:
    register(
        EditorSpec(
            id=table.id,
            title=table.title,
            group=GROUP,
            kind="table",
            exists=lambda config: table.path(config).is_file(),
            load=lambda config: table_load(config, table, _install_choices(config)),
            save=lambda config, rows: table_save(config, table, rows),
            raw_targets=_notes_targets if table.path is _notes_path else _settings_targets,
        )
    )


for _table in TABLES:
    _register_table(_table)
