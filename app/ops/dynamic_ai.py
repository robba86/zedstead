"""Dynamic AI (DayZ-Dynamic-AI-Addon): read-only checks over its settings (plans/47 B).

The mod reads its JSON files at boot. When it dislikes a value it corrects it,
skips the entry, or switches itself off, and it says so only in Expansion's own
log, and only while that log's ``AIGeneral`` switch is on. Nothing else tells
the admin, so "no AI ever spawns" is the usual symptom. Every check here restates
one of the mod's own load rules, in its terms: what it clamps, what it skips,
what turns it off. The rules were read from the author's source
(``SpatialSettings.c``, ``SpatialAI.c``) and the Workshop build's own files
(plans/47 "Measured after install"). They are summarised here, not copied.

This module never writes. In particular it never touches
``SpatialPlayerSettings.json`` (the mod's own player database) or anything
inside the mod folder: the author's terms forbid editing the mod.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.ops import ai_refs
from app.ops.ai_refs import loadouts_dir  # noqa: F401 - the editors and tests import it from here
from app.ops.fix_hints import MODS_INSTALLED
from app.ops.log_analysis import _synthetic as finding
from app.modmgr import modlist
from app.ops.log_analysis import profiles_dir
from app.ops.mod_files import (
    display_path,
    doctor_verdict,
    installed_mod_name,
    json_number,
    read_json,
)

MOD_NAME = "@DayZ-Dynamic-AI-Addon"
WORKSHOP_ID = "2874589934"
LABEL = "Dynamic AI"

SPAWN_LISTS = ("Group", "Point", "Location", "Audio")


@dataclass(frozen=True)
class SettingsFormat:
    """Where one settings format lives and what its keys are called (plans/47 D6).

    One table per format, so the renamed format already shipped in the mod's
    ``files/next workshop version/`` becomes a second table, not a rewrite.
    """

    name: str
    folder: tuple[str, ...]  # under the -profiles dir
    settings: str
    notifications: str
    players: str  # the mod's own database: never read for checks, never written
    version_key: str
    version: int  # what the running build loads
    # Versions the build upgrades by one step on load. Anything else switches
    # the mod off ("too old to update"), newer numbers included.
    upgrades: tuple[int, ...]
    # Keys DayZ loads as whole numbers. A decimal there ("4.0") is what the
    # author diagnosed as stopping a server from starting.
    whole_numbers: dict[str, tuple[str, ...]]


_SPAWN_INTS = ("Spatial_MinCount", "Spatial_MaxCount", "Spatial_HuntMode", "Spatial_Lootable")

V21 = SettingsFormat(
    name="v21",
    folder=("ExpansionMod", "AI", "Spatial"),
    settings="SpatialSettings.json",
    notifications="Notificationsettings.json",
    players="SpatialPlayerSettings.json",
    version_key="Version",
    version=21,
    upgrades=(19, 20),
    whole_numbers={
        "": (
            "Version", "MinDistance", "MaxDistance", "HuntMode", "Points_Enabled",
            "Locations_Enabled", "Audio_Enabled", "PlayerChecks", "MaxAI",
            "GroupDifficulty", "MinimumPlayerDistance", "MaxSoloPlayers", "MinimumAge",
            "ActiveHoursEnabled", "MessageType",
        ),
        "Group": ("Spatial_MinCount", "Spatial_MaxCount", "Spatial_Lootable"),
        "Point": _SPAWN_INTS,
        "Location": _SPAWN_INTS + ("Spatial_SpawnMode",),
        "Audio": _SPAWN_INTS + ("Spatial_SpawnMode",),
    },
)

FORMATS: tuple[SettingsFormat, ...] = (V21,)

# The mod's own floor values, in minutes and metres.
_MIN_TIMER = 5
_MIN_DISTANCE = 120
_MIN_ENGAGE = 5
_HUNT_MODES = range(0, 9)
_ZONE_MODES = (0, 1, 2)


# ── Locating the install ────────────────────────────────────────────────


def installed_name(config) -> str | None:
    """The active mod-list entry that is Dynamic AI, by workshop ID, so a copy
    installed under another folder name is still found. None when it is not
    installed or is disabled."""
    return installed_mod_name(config, MOD_NAME, WORKSHOP_ID)


def settings_dir(config, fmt: SettingsFormat = V21) -> Path:
    return profiles_dir(config).joinpath(*fmt.folder)


# Shared with the other pre-configured mods (app/ops/mod_files.py).
_display = display_path
_read_json = read_json
_num = json_number


def _entry_name(section: str, index: int, entry: dict) -> str:
    name = entry.get("Spatial_Name") if isinstance(entry, dict) else None
    return f"{section} {name!r}" if name else f"{section} #{index + 1}"


def _entries(doc: dict, section: str) -> list[tuple[int, dict]]:
    items = doc.get(section)
    if not isinstance(items, list):
        return []
    return [(i, e) for i, e in enumerate(items) if isinstance(e, dict)]


# ── Checks ───────────────────────────────────────────────────────────────


def _decimals_in_whole_numbers(doc: dict, fmt: SettingsFormat) -> list[str]:
    bad: list[str] = []
    for section, keys in fmt.whole_numbers.items():
        rows = [(None, doc)] if section == "" else _entries(doc, section)
        for index, row in rows:
            for key in keys:
                value = row.get(key)
                if isinstance(value, float):
                    where = key if index is None else f"{section}[{index}].{key}"
                    bad.append(f"{where} = {value!r}")
    return bad


def _loadout_state(name, loadouts: ai_refs.Loadouts) -> str | None:
    """None when the mod will find *name*; otherwise why it will not. The mod
    looks the name up verbatim: the file name, ``.json`` included."""
    return ai_refs.loadout_problem(name, loadouts)


def _settings_findings(config, doc: dict, fmt: SettingsFormat, factions: set[str]) -> list[dict]:
    out: list[dict] = []
    path = _display(config, settings_dir(config, fmt) / fmt.settings)

    decimals = _decimals_in_whole_numbers(doc, fmt)
    if decimals:
        out.append(finding(
            "dynamic_ai_decimal_in_whole_number", "error",
            "Decimal in a whole-number field",
            f"{path}: " + ", ".join(decimals[:5]) + (f" (+{len(decimals) - 5} more)" if len(decimals) > 5 else ""),
            "DayZ loads these fields as whole numbers. The mod's author has diagnosed "
            "a value like 4.0 in one of them as what stopped a server from starting.",
            "Write the value without the decimal part (4, not 4.0).",
            count=len(decimals),
        ))

    if fmt.version_key not in doc and "m_Version" in doc:
        out.append(finding(
            "dynamic_ai_unknown_format", "warning",
            "Settings file is in a format this build does not read",
            f"{path} has m_Version and no {fmt.version_key}",
            "That is the renamed format shipped in the mod's 'next workshop version' "
            "folder. The installed build reads Version and the Spatial_ key names, so "
            "it treats this file as unfamiliar keys over its own defaults.",
            "Keep the next-format files where that version expects them, and put a "
            "current-format SpatialSettings.json here, or delete it and let the mod "
            "write fresh defaults on its next boot.",
        ))
        return out

    # A missing Version loads as the class default, 20, which is upgraded.
    version = _num(doc.get(fmt.version_key), 20)
    if version != fmt.version and version not in fmt.upgrades:
        out.append(finding(
            "dynamic_ai_version_off", "error",
            "The mod switches itself off: unsupported settings version",
            f"{path}: {fmt.version_key} {version:g} (the build loads {fmt.version}, "
            f"upgrades {', '.join(str(v) for v in fmt.upgrades)})",
            "Any other version makes the mod log 'too old to update' and stop, newer "
            "numbers included. No AI spawn at all.",
            "Set it back to the version the build writes, or delete the file and let "
            "the mod regenerate it on the next boot.",
        ))
        return out
    if version != fmt.version:
        out.append(finding(
            "dynamic_ai_version_upgrade", "notice",
            "Settings will be upgraded on the next boot",
            f"{path}: {fmt.version_key} {version:g}",
            "The mod upgrades this version in place and saves the file. Upgrading from "
            "20 also prints a line telling you to delete Spatial_MaxAccuracy from the "
            "Audio entries. That line is expected.",
            "Nothing to do.",
        ))

    max_ai = _num(doc.get("MaxAI"), 20)
    if max_ai < 1:
        out.append(finding(
            "dynamic_ai_max_ai_off", "error",
            "The mod switches itself off: MaxAI is under 1",
            f"{path}: MaxAI {max_ai:g}",
            "MaxAI counts spawned sets across the whole mod. Under 1, the mod logs "
            "that it is disabling itself and no AI spawn.",
            "Set MaxAI to 1 or more.",
        ))
        return out

    min_timer = _num(doc.get("Spatial_MinTimer"), 20)
    max_timer = _num(doc.get("Spatial_MaxTimer"), 40)
    if min_timer * 60000 < -59999:
        out.append(finding(
            "dynamic_ai_debug_mode", "warning",
            "Debug mode is on (negative Spatial_MinTimer)",
            f"{path}: Spatial_MinTimer {min_timer:g}",
            "A negative minimum timer is the mod's debug switch. It adds debug lines "
            "to the log and puts markers on the map for players with Expansion "
            "Navigation, and uses the absolute value as the timer.",
            f"Set a positive number of minutes ({_MIN_TIMER} or more) unless you are debugging.",
        ))
        effective_min = abs(min_timer)
    elif min_timer < _MIN_TIMER:
        out.append(finding(
            "dynamic_ai_timer_raised", "warning",
            f"Spatial_MinTimer is raised to {_MIN_TIMER} minutes",
            f"{path}: Spatial_MinTimer {min_timer:g}",
            f"Timers are minutes. The mod enforces a {_MIN_TIMER}-minute minimum and "
            "uses that instead.",
            f"Write {_MIN_TIMER} or more so the file says what the mod does.",
        ))
        effective_min = _MIN_TIMER
    else:
        effective_min = min_timer
    if max_timer < effective_min:
        out.append(finding(
            "dynamic_ai_max_timer_raised", "warning",
            "Spatial_MaxTimer is raised to the minimum",
            f"{path}: Spatial_MaxTimer {max_timer:g}, minimum {effective_min:g}",
            "A maximum below the minimum is set equal to it, so every check runs on "
            "the same interval.",
            "Set Spatial_MaxTimer at or above Spatial_MinTimer.",
        ))

    min_distance = _num(doc.get("MinDistance"), 140)
    max_distance = _num(doc.get("MaxDistance"), 220)
    if min_distance < _MIN_DISTANCE:
        out.append(finding(
            "dynamic_ai_min_distance_raised", "warning",
            f"MinDistance is raised to {_MIN_DISTANCE} m",
            f"{path}: MinDistance {min_distance:g}",
            f"The mod will not spawn AI closer than {_MIN_DISTANCE} m and uses that instead.",
            f"Write {_MIN_DISTANCE} or more.",
        ))
        min_distance = _MIN_DISTANCE
    if max_distance < min_distance:
        out.append(finding(
            "dynamic_ai_max_distance_raised", "warning",
            "MaxDistance is raised above MinDistance",
            f"{path}: MaxDistance {max_distance:g}, MinDistance {min_distance:g}",
            "A maximum below the minimum becomes the minimum plus 20 m.",
            "Set MaxDistance above MinDistance.",
        ))

    hunt = _num(doc.get("HuntMode"), 1)
    if int(hunt) not in _HUNT_MODES or hunt != int(hunt):
        out.append(finding(
            "dynamic_ai_hunt_mode_reset", "warning",
            "HuntMode is reset to 1",
            f"{path}: HuntMode {hunt:g}",
            "Only 0 to 8 are valid. Anything else becomes 1 (hunt aggressively).",
            "Pick a mode from 0 to 8.",
        ))
    elif hunt == 0:
        out.append(finding(
            "dynamic_ai_hunt_mode_zero", "notice",
            "HuntMode 0: AI stand still",
            f"{path}: HuntMode 0",
            "Per the mod's author, mode 0 gives the AI no waypoints, so they stay where "
            "they spawn.",
            "Use 1 or more if the AI should move.",
        ))

    points_mode = _num(doc.get("Points_Enabled"), 0)
    for key, noun in (("Points_Enabled", "points"), ("Locations_Enabled", "locations")):
        value = _num(doc.get(key), 0)
        if value not in _ZONE_MODES:
            out.append(finding(
                f"dynamic_ai_{noun}_off", "warning",
                f"{key} is not 0, 1 or 2, so {noun} are switched off",
                f"{path}: {key} {value:g}",
                f"The mod treats any other value as an error and turns {noun} off.",
                "Use 0 (off), 1 or 2.",
            ))
            if key == "Points_Enabled":
                points_mode = 0

    engage = _num(doc.get("EngageTimer"), 5)
    cleanup = _num(doc.get("CleanupTimer"), 6)
    if engage < _MIN_ENGAGE:
        out.append(finding(
            "dynamic_ai_engage_raised", "warning",
            f"EngageTimer is raised to {_MIN_ENGAGE} minutes",
            f"{path}: EngageTimer {engage:g}",
            f"The mod enforces a {_MIN_ENGAGE}-minute minimum.",
            f"Write {_MIN_ENGAGE} or more.",
        ))
        engage = _MIN_ENGAGE
    if cleanup < engage:
        out.append(finding(
            "dynamic_ai_cleanup_raised", "warning",
            "CleanupTimer is raised above EngageTimer",
            f"{path}: CleanupTimer {cleanup:g}, EngageTimer {engage:g}",
            "Cleanup below the engage time becomes the engage time plus 1 minute.",
            "Set CleanupTimer above EngageTimer.",
        ))

    player_checks = _num(doc.get("PlayerChecks"), -5)
    if player_checks >= 0:
        out.append(finding(
            "dynamic_ai_leaders_only", "notice",
            "AI spawn only on group leaders (PlayerChecks 0 or more)",
            f"{path}: PlayerChecks {player_checks:g}",
            "With 0 or more, the mod spawns only on party leaders. A negative value "
            "spawns on anyone. This is the most common reason for 'AI never spawn on me'.",
            "Use a negative value if solo players should get AI too.",
        ))

    out.extend(_spawn_entry_findings(config, doc, path, points_mode, factions))
    return out


def _spawn_entry_findings(config, doc: dict, path: str, points_mode: float, factions: set[str]) -> list[dict]:
    out: list[dict] = []
    loadouts = ai_refs.read_loadouts(config)

    skipped: list[str] = []
    usable = 0
    for i, g in _entries(doc, "Group"):
        name = _entry_name("Group", i, g)
        lo, hi = _num(g.get("Spatial_MinCount"), 0), _num(g.get("Spatial_MaxCount"), 0)
        if lo < 0:
            skipped.append(f"{name}: Spatial_MinCount under 0")
        elif lo > hi:
            skipped.append(f"{name}: Spatial_MinCount above Spatial_MaxCount")
        elif hi < 1:
            skipped.append(f"{name}: Spatial_MaxCount under 1")
        elif (why := _loadout_state(g.get("Spatial_Loadout"), loadouts)) is not None:
            skipped.append(f"{name}: {why}")
        else:
            usable += 1
    groups = len(_entries(doc, "Group"))
    if skipped:
        out.append(finding(
            "dynamic_ai_group_skipped", "warning",
            "Groups the mod skips",
            "; ".join(skipped),
            "The mod drops a group on load when its counts are impossible or its "
            "loadout file is missing, and logs it only in the Expansion log.",
            "Fix the counts, or name a file that exists in ExpansionMod/Loadouts "
            "(exact name, .json included).",
            count=len(skipped),
        ))
    if groups and not usable and points_mode != 2:
        out.append(finding(
            "dynamic_ai_no_usable_group", "error",
            "No usable group: nothing spawns outside points and locations",
            f"{path}: all {groups} group(s) are skipped",
            "Every group is dropped on load, so ordinary spawns around players never happen.",
            "Fix the groups listed above.",
        ))

    if factions:
        unknown: list[str] = []
        for section in SPAWN_LISTS:
            for i, e in _entries(doc, section):
                faction = e.get("Spatial_Faction")
                if isinstance(faction, str) and faction and ai_refs.faction_problem(faction, factions, random_ok=True):
                    unknown.append(f"{_entry_name(section, i, e)}: {faction}")
        if unknown:
            out.append(finding(
                "dynamic_ai_unknown_faction", "warning",
                "Faction names no installed mod registers",
                "; ".join(unknown),
                "Expansion AI resolves a faction by name. Admins have reported a wrong "
                "name as an 'invalid faction' error and as a script error. Installed: "
                + ", ".join(sorted(factions)) + ".",
                "Use one of the installed faction names, spelled exactly, or RANDOM to "
                "let Expansion pick one.",
                count=len(unknown),
            ))

    missing: list[str] = []
    if points_mode in (1, 2):
        for i, p in _entries(doc, "Point"):
            names = p.get("Spatial_ZoneLoadout")
            for n in names if isinstance(names, list) else [names]:
                if (why := _loadout_state(n, loadouts)) is not None:
                    missing.append(f"{_entry_name('Point', i, p)}: {why}")
    for section, key in (("Location", "Locations_Enabled"), ("Audio", "Audio_Enabled")):
        if not _num(doc.get(key), 0):
            continue
        for i, e in _entries(doc, section):
            if (why := _loadout_state(e.get("Spatial_ZoneLoadout"), loadouts)) is not None:
                missing.append(f"{_entry_name(section, i, e)}: {why}")
    if missing:
        out.append(finding(
            "dynamic_ai_zone_loadout_missing", "warning",
            "Zone loadouts that are not there",
            "; ".join(missing),
            "The mod logs a missing point loadout but keeps the name, and it does not "
            "check location or audio loadouts at all, so those spawns use a file that "
            "does not exist.",
            "Name a file that exists in ExpansionMod/Loadouts (exact name, .json included).",
            count=len(missing),
        ))

    if points_mode in (1, 2):
        reset: list[str] = []
        for i, p in _entries(doc, "Point"):
            name = _entry_name("Point", i, p)
            if _num(p.get("Spatial_Radius"), 0) < 0:
                reset.append(f"{name}: Spatial_Radius under 0 becomes 100")
            safe = p.get("Spatial_Safe")
            if safe not in (0, 1, True, False):
                reset.append(f"{name}: Spatial_Safe {safe!r} becomes 1, a no-spawn zone")
            hunt = _num(p.get("Spatial_HuntMode"), 0)
            if int(hunt) not in _HUNT_MODES or hunt != int(hunt):
                reset.append(f"{name}: Spatial_HuntMode {hunt:g} becomes 3")
        if reset:
            out.append(finding(
                "dynamic_ai_point_reset", "warning",
                "Point values the mod replaces",
                "; ".join(reset),
                "The mod replaces out-of-range point values on load.",
                "Write values in range so the file says what the mod does.",
                count=len(reset),
            ))
    return out


def _notification_findings(config, doc: dict, notes: dict | None, fmt: SettingsFormat) -> list[dict]:
    mode = _num(doc.get("ActiveHoursEnabled"), 0)
    if mode not in (2, 3) or notes is None:
        return []
    linked = {
        n.get("Spatial_Name")
        for n in (notes.get("notification") or [])
        if isinstance(n, dict) and n.get("Spatial_Name")
    }
    groups = [(i, g) for i, g in _entries(doc, "Group")]
    orphans = [_entry_name("Group", i, g) for i, g in groups if g.get("Spatial_Name") not in linked]
    if not orphans:
        return []
    path = _display(config, settings_dir(config, fmt) / fmt.notifications)
    how_picked = "time of day" if mode == 2 else "player age"
    if len(orphans) == len(groups):
        return [finding(
            "dynamic_ai_no_group_linked", "error",
            f"No group can be picked (ActiveHoursEnabled {mode:g})",
            f"no Group's Spatial_Name appears in {path}",
            f"In this mode the mod picks a group by {how_picked} through the "
            "notification entries, matched on Spatial_Name. With no match nothing "
            "spawns outside points.",
            "Give a notification entry the exact Spatial_Name of each group it should cover.",
        )]
    return [finding(
        "dynamic_ai_group_not_linked", "warning",
        f"Groups that can never be picked (ActiveHoursEnabled {mode:g})",
        "; ".join(orphans) + f" (not in {path})",
        f"In this mode a group is picked by {how_picked} through the notification "
        "entries, matched on Spatial_Name. A group no entry names never spawns.",
        "Add a notification entry with the group's exact Spatial_Name.",
        count=len(orphans),
    )]


def _ai_log_enabled(config) -> bool | None:
    doc, _err = _read_json(profiles_dir(config) / "ExpansionMod" / "Settings" / "LogsSettings.json")
    if doc is None or "AIGeneral" not in doc:
        return None
    return bool(doc.get("AIGeneral"))


# ── The mod's own account of its last boot (plans/47 D5) ───────────────

_RE_SPATIAL_LINE = re.compile(r"^(\d\d:\d\d:\d\d(?:\.\d+)?)\s+\[(Spatial[^\]]*)\]\s?(.*)$")
_MAX_LOG_LINES = 300
# Printed on every upgrade from settings version 20, which is every first boot:
# the class default is 20 and the load upgrades it.
_EXPECTED = ("delete the Spatial_MaxAccuracy lines",)


def read_last_boot(config) -> dict | None:
    """The ``[Spatial …]`` lines of the newest Expansion log, or None if there is
    no Expansion log at all."""
    logs_dir = profiles_dir(config) / "ExpansionMod" / "Logs"
    try:
        logs = sorted(logs_dir.glob("ExpLog_*.log"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return None
    if not logs:
        return None
    newest = logs[-1]
    lines: list[dict] = []
    try:
        with newest.open(encoding="utf-8", errors="replace") as f:
            for raw in f:
                m = _RE_SPATIAL_LINE.match(raw.rstrip("\r\n"))
                if m:
                    message = m.group(3)
                    lines.append({
                        "time": m.group(1),
                        "tag": m.group(2),
                        "message": message,
                        "expected": any(e in message for e in _EXPECTED),
                    })
    except OSError:
        return None
    messages = [ln["message"] for ln in lines]
    if any("Spatial AI Enabled" in msg for msg in messages):
        enabled: bool | None = True
    elif any("disabling Spatial AI" in msg or "too old to update" in msg for msg in messages):
        enabled = False
    else:
        enabled = None
    return {
        "file": newest.name,
        "path": _display(config, newest),
        "enabled": enabled,
        "first_boot": any("Couldn't find config file" in msg for msg in messages),
        "debug": any("Debug Mode on" in msg for msg in messages),
        "total": len(lines),
        "lines": lines[-_MAX_LOG_LINES:],
    }


# ── The report ──────────────────────────────────────────────────────────


def build_report(config, fmt: SettingsFormat = V21) -> dict | None:
    """Everything zsm can tell about the Dynamic AI install, or None when the
    mod is not installed (or is disabled)."""
    name = installed_name(config)
    if name is None:
        return None
    folder = settings_dir(config, fmt)
    settings_path = folder / fmt.settings
    notes_path = folder / fmt.notifications
    findings: list[dict] = []

    if name not in modlist.server_only(config):
        findings.append(finding(
            "dynamic_ai_client_side", "warning",
            "Loads through -mod=, so players are asked to download it",
            f"{name} is not marked server-side",
            "The mod is server-side only. Its author says it belongs in -serverMod=; "
            "in -mod= every player has to download it to join.",
            "Mark it Server only on the Mods page, or run: zsm mods server-side " + name,
            [{"label": "Mods", "to": MODS_INSTALLED}],
        ))

    factions = ai_refs.installed_factions(config)
    doc, err = _read_json(settings_path)
    if not folder.is_dir():
        findings.append(finding(
            "dynamic_ai_folder_missing", "warning",
            "The mod's settings folder does not exist yet",
            _display(config, folder),
            "The mod creates it and writes default files on its first boot. Several "
            "admins found it never appeared, which the author put down to folder "
            "permissions.",
            "Start the server once. If the folder is still missing, create it yourself; "
            "the mod then writes its files into it.",
        ))
    elif err is not None:
        findings.append(finding(
            "dynamic_ai_settings_unreadable", "error",
            "SpatialSettings.json cannot be read",
            f"{_display(config, settings_path)}: {err}",
            "DayZ cannot load a file that is not valid JSON. What the mod does then is "
            "not documented, and the one broken file its author diagnosed stopped the "
            "server from starting.",
            "Fix the syntax at that position, or delete the file so the mod writes defaults.",
        ))
    elif doc is None:
        findings.append(finding(
            "dynamic_ai_settings_missing", "notice",
            "SpatialSettings.json does not exist yet",
            _display(config, settings_path),
            "The mod writes a default file on its next boot.",
            "Start the server once.",
        ))
    else:
        findings.extend(_settings_findings(config, doc, fmt, factions))
        notes, notes_err = _read_json(notes_path)
        if notes_err is not None:
            findings.append(finding(
                "dynamic_ai_notifications_unreadable", "error",
                "Notificationsettings.json cannot be read",
                f"{_display(config, notes_path)}: {notes_err}",
                "DayZ cannot load a file that is not valid JSON, and the notification "
                "entries decide which groups can spawn when ActiveHoursEnabled is 2 or 3.",
                "Fix the syntax at that position, or delete the file so the mod writes defaults.",
            ))
        else:
            findings.extend(_notification_findings(config, doc, notes, fmt))

    ai_log = _ai_log_enabled(config)
    if ai_log is False:
        findings.append(finding(
            "dynamic_ai_log_off", "notice",
            "Expansion's AIGeneral log is off, so the mod reports nothing",
            _display(config, profiles_dir(config) / "ExpansionMod" / "Settings" / "LogsSettings.json"),
            "Everything the mod corrects or skips on load is written to the Expansion "
            "log only while AIGeneral is on.",
            "Set AIGeneral to 1 while you set the mod up.",
        ))

    last_boot = read_last_boot(config)
    if last_boot and last_boot["enabled"] is False:
        findings.append(finding(
            "dynamic_ai_disabled_last_boot", "error",
            "On its last boot the mod switched itself off",
            last_boot["path"],
            "The mod logged that it was disabling itself, so no AI spawned on that run.",
            "Read its [Spatial Settings] lines in that log; the findings above name the usual causes.",
        ))

    return {
        "mod": name,
        "format": fmt.name,
        "folder": _display(config, folder),
        "files": {
            "settings": settings_path.is_file(),
            "notifications": notes_path.is_file(),
            "players": (folder / fmt.players).is_file(),
        },
        "factions": sorted(factions),
        "ai_log": ai_log,
        "findings": findings,
        "last_boot": last_boot,
    }


def doctor_row(config) -> tuple[str, bool, str] | None:
    """The Doctor's one-line verdict, or None when the mod is not installed.

    Errors and warnings fail the row; notices ride along in the detail so a
    passing row still says e.g. that AI spawn on leaders only.
    """
    report = build_report(config)
    if report is None:
        return None
    boot = report["last_boot"]
    state = [f"last boot: enabled ({boot['file']})"] if boot and boot["enabled"] is True else []
    return doctor_verdict(LABEL, report["findings"], state)
