"""Expansion AI's own patrols: read-only checks over its settings (plans/64).

Expansion AI reads ``<mission>/expansion/settings/AIPatrolSettings.json`` at
boot. When it dislikes a patrol it skips it, swaps a value or falls back to a
default, and says so only in a log, so "my patrol never spawns" or "they spawn
as civilians" is the usual symptom. Every check here restates one of
Expansion's own load rules, in its terms.

The rules were read from Expansion's scripts (``salutesh/DayZ-Expansion-Scripts``,
branch ``release`` at ``1660f7a``) and its wiki (``afcf54a``), and measured
against the file a real boot wrote (plans/64 "Step 0 results"). Those scripts
carry a no-derivatives licence: the rules are summarised here in our own words,
nothing is copied, and the value lists are the names an admin types.

**A file as Expansion writes it gives a passing row.** Errors and warnings are
for what Expansion skips, ignores or silently changes; a choice is a notice.
Expansion's own defaults use ``RANDOM`` as a faction, an empty loadout, and
object patrols whose waypoints are relative to their building, so none of
those may ever be reported.

This module never writes.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from app.ops import ai_refs
from app.ops.fix_hints import (
    CONFIG_EXPANSION_AI,
    CONFIG_EXPANSION_AI_PATROL_LIST,
    CONFIG_EXPANSION_AI_PATROLS,
    MAP_EDITOR_AI_PATROLS,
)
from app.ops.log_analysis import _synthetic as finding
from app.ops.log_analysis import profiles_dir
from app.ops.mod_files import display_path, doctor_verdict, installed_mod_name, json_number, read_json
from app.ops.zone_mods import current_terrain, points_off_map, switched_off

MOD_NAME = "@DayZ-Expansion-AI"
WORKSHOP_ID = "2792982069"
LABEL = "Expansion AI"

PATROL_FILE = "AIPatrolSettings.json"
AI_SETTINGS_FILE = "AISettings.json"
MISSION_SETTINGS = ("expansion", "settings")
PROFILE_SETTINGS = ("ExpansionMod", "Settings")
LOOT_DROPS = ("ExpansionMod", "AI", "LootDrops")

# The patrol settings version of the measured build. A lower one is converted
# and saved again on the next boot.
SETTINGS_VERSION = 32

# AISettings.json's accuracy when it names none (the last fallback).
AI_SETTINGS_ACCURACY = (0.35, 0.95)

# Load balancing: the category a patrol gets when it names none, and the one an
# unknown name is counted under.
OBJECT_CATEGORY = "ObjectPatrol"
PATROL_CATEGORY = "Patrol"
GLOBAL = "Global"

# The values Expansion's wiki documents. Formation is not checked: other mods
# add formations, as they add factions.
BEHAVIOURS = frozenset({
    "HALT", "ONCE", "LOOP", "ALTERNATE", "LOOP_OR_ALTERNATE", "HALT_OR_ALTERNATE",
    "HALT_OR_LOOP", "ROAMING", "ROAMING_LOCAL",
})
SPEEDS = frozenset({"STATIC", "WALK", "JOG", "SPRINT", "RANDOM", "RANDOM_NONSTATIC"})
STANCES = frozenset({"STANDING", "CROUCHED", "PRONE"})
INTERPOLATIONS = frozenset({"", "CatmullRom", "NaturalCubic", "UniformCubic"})
LOOTING = frozenset({
    "WEAPONS_FIREARMS", "WEAPONS_LAUNCHERS", "WEAPONS_MELEE", "WEAPONS", "BANDAGES",
    "CLOTHING_ARMBAND", "CLOTHING_BACK_LARGE", "CLOTHING_BACK_MEDIUM", "CLOTHING_BACK_SMALL",
    "CLOTHING_BACK", "CLOTHING_BODY", "CLOTHING_EYEWEAR", "CLOTHING_FEET", "CLOTHING_GLOVES",
    "CLOTHING_HEADGEAR", "CLOTHING_HIPS", "CLOTHING_LEGS", "CLOTHING_MASK", "CLOTHING_MELEE",
    "CLOTHING_SHOULDER", "CLOTHING_VEST", "CLOTHING", "CLOTHING_IDENTICAL", "CLOTHING_SIMILAR",
    "FOOD", "UPGRADE", "DEFAULT", "ALL", "NONE",
})
_ENUM_KEYS = (
    ("Behaviour", BEHAVIOURS),
    ("Speed", SPEEDS),
    ("UnderThreatSpeed", SPEEDS),
    ("DefaultStance", STANCES),
)

_LINKS = [{"label": "AI Patrols", "to": CONFIG_EXPANSION_AI_PATROLS}]
_TABLE = "Patrol list"
_MAP_LINK = {"label": "Map editor", "to": MAP_EDITOR_AI_PATROLS}
# LoadBalancingCategories is a map of lists, which only the Raw tab edits.
_RAW_LINKS = [{"label": "AI Patrols (Raw)", "to": CONFIG_EXPANSION_AI_PATROLS + "?tab=Raw"}]
_AI_SETTINGS_LINKS = [{"label": "Expansion AI", "to": CONFIG_EXPANSION_AI}]

_num = json_number


def installed_name(config) -> str | None:
    """The mod-list entry that is Expansion AI, found by workshop ID. None when
    it is not installed or is disabled."""
    return installed_mod_name(config, MOD_NAME, WORKSHOP_ID)


def patrol_path(config) -> Path | None:
    """The mission's patrol file, or None when no mission folder resolves."""
    from app.xml.xml_utils import get_mission_dir

    mission = get_mission_dir(config)
    return mission.joinpath(*MISSION_SETTINGS, PATROL_FILE) if mission else None


def ai_settings_path(config) -> Path:
    return profiles_dir(config).joinpath(*PROFILE_SETTINGS, AI_SETTINGS_FILE)


def loot_drops_dir(config) -> Path:
    return profiles_dir(config).joinpath(*LOOT_DROPS)


# ── Reading patrols ──────────────────────────────────────────────────────


def _patrols(doc: dict) -> list[tuple[int, dict]]:
    items = doc.get("Patrols")
    if not isinstance(items, list):
        return []
    return [(i, p) for i, p in enumerate(items) if isinstance(p, dict)]


def is_object_patrol(patrol: dict) -> bool:
    """A patrol is an object patrol when it names a building class, whatever
    its waypoints. Its waypoints are then relative to that building."""
    value = patrol.get("ObjectClassName")
    return isinstance(value, str) and bool(value)


def _label(index: int, patrol: dict) -> str:
    if is_object_patrol(patrol):
        return f"Object patrol {patrol['ObjectClassName']}"
    name = patrol.get("Name")
    return f"Patrol {name!r}" if isinstance(name, str) and name else f"Patrol #{index + 1}"


def _waypoints(patrol: dict) -> list[tuple[float, float]]:
    """Each waypoint's map position, (x, z); a malformed one is skipped."""
    out = []
    for wp in patrol.get("Waypoints") or []:
        if isinstance(wp, (list, tuple)) and len(wp) >= 3:
            x, z = _num(wp[0]), _num(wp[2])
            if x is not None and z is not None:
                out.append((x, z))
    return out


def _persists(patrol: dict) -> bool:
    return bool(_num(patrol.get("Persist"), 0.0)) or patrol.get("Persist") is True


# ── Checks ───────────────────────────────────────────────────────────────


def _file_findings(config, path: Path | None, doc, err) -> list[dict]:
    if path is None:
        return [finding(
            "expansion_ai_patrols_missing", "notice",
            f"{PATROL_FILE} cannot be looked for",
            "No mission folder: serverDZ.cfg names no mission Zedstead can find",
            "The patrol file lives in the mission folder.",
            "Set the mission in serverDZ.cfg (Maps page), then look again.",
        )]
    if err is not None:
        return [finding(
            "expansion_ai_patrols_unreadable", "error",
            f"{PATROL_FILE} cannot be read",
            f"{display_path(config, path)}: {err}",
            "When the file does not load, Expansion runs its own built-in default "
            "patrols for that boot and leaves the file as it is. None of the patrols "
            "in it run.",
            "Fix the syntax at that position, or delete the file so Expansion writes "
            "its defaults on the next boot.",
            _LINKS,
        )]
    if doc is None:
        return [finding(
            "expansion_ai_patrols_missing", "notice",
            f"{PATROL_FILE} does not exist yet",
            display_path(config, path),
            "Expansion writes it with its default patrols on the first boot that finds "
            "it missing.",
            "Start the server once.",
        )]
    out = switched_off(
        doc, "Enabled", "expansion_ai_patrols_off",
        "Expansion AI patrols are switched off",
        "No patrol starts while the switch is off.", PATROL_FILE,
    )
    for f in out:
        f["links"] = list(_LINKS)
    version = _num(doc.get("m_Version"))
    if version is not None and version < SETTINGS_VERSION:
        out.append(finding(
            "expansion_ai_version_upgrade", "notice",
            f"{PATROL_FILE} is from an older Expansion",
            f"m_Version {version:g}; this build writes {SETTINGS_VERSION}",
            "Expansion converts an older file and saves it again on the next boot. "
            "Its wiki asks you never to change m_Version yourself.",
            "Nothing to do: start the server once and the file is brought up to date.",
            _LINKS,
        ))
    return out


def _patrol_findings(config, doc: dict, factions: set[str], ai_doc: dict | None) -> list[dict]:
    loadouts = ai_refs.read_loadouts(config)
    loot_dir = loot_drops_dir(config)
    terrain = current_terrain(config)
    categories = doc.get("LoadBalancingCategories")
    categories = categories if isinstance(categories, dict) else None

    # File-level values a patrol inherits (B17, B18).
    file_min_dist, file_max_dist = _num(doc.get("MinDistRadius")), _num(doc.get("MaxDistRadius"))
    ai_doc = ai_doc or {}
    ai_acc = (
        _num(ai_doc.get("AccuracyMin"), AI_SETTINGS_ACCURACY[0]),
        _num(ai_doc.get("AccuracyMax"), AI_SETTINGS_ACCURACY[1]),
    )
    file_acc = tuple(
        v if v is not None and v > 0 else fallback
        for v, fallback in zip((_num(doc.get("AccuracyMin")), _num(doc.get("AccuracyMax"))), ai_acc)
    )

    # Each hit is (the patrol's label, the line the finding shows).
    Hits = list[tuple[str, str]]
    unknown_faction: Hits = []
    unknown_loadout: Hits = []
    fallback_category: Hits = []
    no_ai: Hits = []
    no_waypoints: Hits = []
    off_map_points: list[tuple[str, list[tuple[float, float]]]] = []
    at_origin: list[str] = []
    never: Hits = []
    swapped: Hits = []
    accuracy: Hits = []
    persist_object: Hits = []
    persist_name: Hits = []
    lootdrop: Hits = []
    unknown_value: Hits = []
    persistent_names: set[str] = set()
    names: dict[str, str] = {}  # label -> Name, for the table's ?q=

    for i, p in _patrols(doc):
        label = _label(i, p)
        on_object = is_object_patrol(p)

        if isinstance(p.get("Name"), str) and p["Name"]:
            names[label] = p["Name"]

        if "Faction" in p and (why := ai_refs.faction_problem(p["Faction"], factions, random_ok=True)):
            unknown_faction.append((label, f"{label}: {why}"))
        if "Loadout" in p and (why := ai_refs.loadout_problem(
                p["Loadout"], loadouts, extension_optional=True, empty_ok=True)):
            unknown_loadout.append((label, f"{label}: {why}"))

        if categories is not None:
            named = p.get("LoadBalancingCategory")
            category = named if isinstance(named, str) and named else (
                OBJECT_CATEGORY if on_object else PATROL_CATEGORY)
            if category not in categories:
                fallback_category.append((label, f"{label}: {category}"))

        if _num(p.get("NumberOfAI")) == 0:
            no_ai.append((label, label))

        if not on_object:
            points = _waypoints(p)
            if not p.get("Waypoints"):
                no_waypoints.append((label, label))
            elif points:
                off_map_points.append((label, points))
                if any(x == 0 and z == 0 for x, z in points):
                    at_origin.append(label)

        if _num(p.get("Chance")) == 0:
            never.append((label, label))

        own_min, own_max = _num(p.get("MinDistRadius")), _num(p.get("MaxDistRadius"))
        # A negative minimum, or a maximum of 0 or less, means "the file's value".
        lo = own_min if own_min is not None and own_min >= 0 else file_min_dist
        hi = own_max if own_max is not None and own_max > 0 else file_max_dist
        if lo is not None and hi is not None and lo > hi:
            swapped.append((label, f"{label}: {lo:g} > {hi:g}"))

        # 0 or less means "the file's value", whose own 0 or less means AISettings'.
        own_acc = (_num(p.get("AccuracyMin")), _num(p.get("AccuracyMax")))
        acc_lo, acc_hi = (
            own if own is not None and own > 0 else fallback for own, fallback in zip(own_acc, file_acc)
        )
        if acc_lo is not None and acc_hi is not None and (acc_hi > 1 or acc_lo > 1 or acc_lo > acc_hi):
            accuracy.append((label, f"{label}: {acc_lo:g}–{acc_hi:g}"))

        if _persists(p):
            if on_object:
                persist_object.append((label, label))
            else:
                name = p.get("Name")
                if not isinstance(name, str) or not name:
                    persist_name.append((label, f"{label}: no Name"))
                elif name in persistent_names:
                    persist_name.append((label, f"{label}: Name used by an earlier persistent patrol"))
                else:
                    persistent_names.add(name)

        drop = p.get("LootDropOnDeath")
        if isinstance(drop, str) and drop:
            file = drop if drop.endswith(".json") else drop + ".json"
            if not (loot_dir / file).is_file():
                lootdrop.append((label, f"{label}: {drop}"))

        for key, allowed in _ENUM_KEYS:
            value = p.get(key)
            if isinstance(value, str) and value and value not in allowed:
                unknown_value.append((label, f"{label}: {key} {value}"))
        value = p.get("WaypointInterpolation")
        if isinstance(value, str) and value not in INTERPOLATIONS:
            unknown_value.append((label, f"{label}: WaypointInterpolation {value}"))
        value = p.get("LootingBehaviour")
        if isinstance(value, str):
            bad = [t for t in (t.strip() for t in value.split("|")) if t and t not in LOOTING]
            if bad:
                unknown_value.append((label, f"{label}: LootingBehaviour {', '.join(bad)}"))

    out: list[dict] = []

    def table_link(hits: Hits) -> dict:
        # One named patrol: the table filtered to it. Anything else: the table.
        named = {names.get(label) for label, _ in hits}
        to = CONFIG_EXPANSION_AI_PATROL_LIST
        if len(named) == 1 and (name := named.pop()):
            to += "?q=" + quote(name)
        return {"label": _TABLE, "to": to}

    def add(fid, severity, title, hits: Hits, why, how, *extra):
        if hits:
            links = [table_link(hits), *extra]
            out.append(finding(fid, severity, title, "; ".join(text for _, text in hits), why, how,
                               links, count=len(hits)))

    add("expansion_ai_unknown_faction", "warning",
        "Patrols with a faction Expansion does not know", unknown_faction,
        "Expansion looks a faction up by its exact name. An unknown name is logged as "
        "'Invalid faction name' and the patrol spawns as Civilian. RANDOM is fine: "
        "Expansion picks a registered faction for it. Installed: " + ", ".join(sorted(factions)) + ".",
        "Use one of the installed faction names, spelled exactly, or RANDOM.")
    add("expansion_ai_unknown_loadout", "warning",
        "Patrols with a loadout that is not in ExpansionMod/Loadouts", unknown_loadout,
        "Expansion adds .json to the name and loads that file. When it is missing it "
        "logs 'Unknown loadout requested' and the AI spawn with no loadout. An empty "
        "Loadout is fine: the faction's own default is used.",
        "Name a file in ExpansionMod/Loadouts (with or without .json), or leave Loadout empty.")
    fallback_why = (
        "A patrol is counted under its LoadBalancingCategory. A name that is not in "
        "LoadBalancingCategories is counted under Global instead"
        + (", so only Global's limits apply to it." if categories and GLOBAL in categories
           else ", and this file has no Global category, so nothing limits it.")
        + " With no category named, an object patrol uses ObjectPatrol and any other Patrol."
    )
    add("expansion_ai_category_fallback", "warning",
        "Patrols in a load-balancing category the file does not have", fallback_category,
        fallback_why,
        "Add the category to LoadBalancingCategories (on the Raw tab), or name one that exists.",
        *_RAW_LINKS)
    add("expansion_ai_number_zero", "warning",
        "Patrols with NumberOfAI 0", no_ai,
        "Expansion logs an error and does not set the patrol up. A negative number "
        "spawns a random 1 to that many; NumberOfAIMax above 0 sets a range.",
        "Set NumberOfAI to 1 or more.")
    add("expansion_ai_no_waypoints", "warning",
        "Patrols with no waypoints", no_waypoints,
        "A patrol that is not tied to a building needs at least one waypoint to "
        "spawn at. Expansion logs 'has no waypoints' and skips it.",
        "Add a waypoint on the map editor's AI patrols layer, or set ObjectClassName "
        "to tie the patrol to a building.", _MAP_LINK)
    outside, size = points_off_map(off_map_points, terrain)
    add("expansion_ai_waypoint_off_map", "warning",
        "Patrol waypoints off the map or at 0, 0",
        [(label, f"{label}: at 0, 0") for label in at_origin]
        + [(label, f"{label}: beyond the edge") for label in outside if label not in at_origin],
        "A waypoint at the map's corner is almost always one that was never set"
        + (f", and the map is {size:g} m square, so the AI cannot walk beyond it." if size else ".")
        + " An object patrol is never listed: its waypoints are relative to its building.",
        "Move the waypoint to where the patrol should walk, on the map editor's AI patrols layer.",
        _MAP_LINK)
    add("expansion_ai_never_spawns", "notice",
        "Patrols with Chance 0", never,
        "A patrol with Chance 0 never spawns.",
        "Set Chance above 0 when you want it.")
    add("expansion_ai_radius_swapped", "warning",
        "Patrols whose spawn distances are the wrong way round", swapped,
        "A patrol spawns only while a player is between MinDistRadius and "
        "MaxDistRadius from it (-1 uses the file's own values). With the minimum "
        "above the maximum, Expansion logs an error and swaps them.",
        "Swap the two values so MinDistRadius is the smaller.")
    add("expansion_ai_accuracy", "warning",
        "Patrols with an accuracy outside 0 to 1, or a minimum above the maximum", accuracy,
        "Expansion's wiki documents accuracy as a value from 0 to 1. A patrol's -1 "
        "uses the file's value, and the file's -1 uses AISettings.json's.",
        "Keep AccuracyMin at or below AccuracyMax, both between 0 and 1.")
    add("expansion_ai_persist_object", "notice",
        "Object patrols with Persist on", persist_object,
        "Object patrols are never persisted: Expansion logs an error and switches "
        "Persist off for them.",
        "Set Persist to 0 on these patrols.")
    add("expansion_ai_persist_name", "warning",
        "Persistent patrols without a name of their own", persist_name,
        "A persistent patrol is saved under its Name. An empty name, or one an earlier "
        "persistent patrol already uses, is logged as an error and the patrol is not "
        "persisted.",
        "Give each persistent patrol its own Name.")
    add("expansion_ai_lootdrop_missing", "warning",
        "Patrols whose LootDropOnDeath file does not exist", lootdrop,
        "Expansion loads the file from ExpansionMod/AI/LootDrops (adding .json when "
        "the name has none). When it cannot, it logs \"Couldn't load LootDropOnDeath\".",
        "Name a file in ExpansionMod/AI/LootDrops, or leave LootDropOnDeath empty.")
    add("expansion_ai_unknown_value", "warning",
        "Patrol settings with a value Expansion does not document", unknown_value,
        "Behaviour, Speed, UnderThreatSpeed, DefaultStance, WaypointInterpolation and "
        "each LootingBehaviour part take one of the names Expansion's wiki lists. An "
        "unknown looting part or interpolation is logged as an error and ignored.",
        "Use one of the documented names, spelled exactly.", *_RAW_LINKS)
    return out


# ── Load balancing ──────────────────────────────────────────────────────
#
# Per category, the first entry whose MinPlayers..MaxPlayers holds the online
# count applies; MaxPlayers 0 or less has no upper bound, and no matching entry
# means no limit from that category. Ranges are worked out as intervals of
# player counts, so an unbounded one needs no cap.

_INF = float("inf")


def _player_range(entry) -> tuple[float, float] | None:
    """The counts *entry* matches, or None when it can match none."""
    if not isinstance(entry, dict):
        return None
    lo, hi = _num(entry.get("MinPlayers")), _num(entry.get("MaxPlayers"))
    if lo is None or hi is None:
        return None
    hi = _INF if hi <= 0 else hi
    return (lo, hi) if lo <= hi else None


def _merge(ranges) -> list[tuple[float, float]]:
    """Whole-number ranges joined where they touch (0-10 and 11-20 are one)."""
    merged: list[list[float]] = []
    for lo, hi in sorted(ranges):
        if merged and lo <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return [(lo, hi) for lo, hi in merged]


def _uncovered(lo: float, hi: float, covered) -> list[tuple[float, float]]:
    """The parts of lo..hi that no range in *covered* (merged) holds."""
    if lo > hi:
        return []
    gaps, start = [], lo
    for c_lo, c_hi in covered:
        if c_hi < start:
            continue
        if c_lo > hi:
            break
        if c_lo > start:
            gaps.append((start, min(c_lo - 1, hi)))
        if c_hi >= hi:
            return gaps
        start = c_hi + 1
    gaps.append((start, hi))
    return gaps


def _counts(ranges) -> str:
    return ", ".join(
        f"{lo:g} and up" if hi == _INF else f"{lo:g}" if lo == hi else f"{lo:g}–{hi:g}"
        for lo, hi in ranges
    )


def _max_players(config) -> int | None:
    from app.parsers.servercfg import read_cfg_value

    try:
        value = read_cfg_value(config.server_cfg_path, "maxPlayers")
        return int(value) if value is not None else None
    except (OSError, ValueError):
        return None


def _category_findings(doc: dict, max_players: int | None) -> list[dict]:
    categories = doc.get("LoadBalancingCategories")
    if not isinstance(categories, dict):
        return []
    never: list[str] = []
    shadowed: list[str] = []
    gaps: list[str] = []
    none_allowed: list[str] = []
    global_none = False

    for name, entries in categories.items():
        entries = entries if isinstance(entries, list) else []
        earlier: list[tuple[float, float]] = []
        for i, entry in enumerate(entries):
            where = f"{name} #{i + 1}"
            rng = _player_range(entry)
            if rng is None:
                lo, hi = (_num(entry.get(k)) for k in ("MinPlayers", "MaxPlayers")) if isinstance(
                    entry, dict) else (None, None)
                if lo is not None and hi is not None:
                    never.append(f"{where}: MinPlayers {lo:g} above MaxPlayers {hi:g}")
                continue
            merged = _merge(earlier)
            if not _uncovered(*rng, merged):
                shadowed.append(f"{where}: {_counts([rng])} players")
            elif max_players is not None and _num(entry.get("MaxPatrols")) == 0:
                applies = _uncovered(max(rng[0], 0), min(rng[1], max_players), merged)
                if applies:
                    none_allowed.append(f"{name}: {_counts(applies)} players")
                    global_none = global_none or name == GLOBAL
            earlier.append(rng)
        if max_players is not None:
            missing = _uncovered(0, max_players, _merge(earlier))
            if missing:
                gaps.append(f"{name}: {_counts(missing)} players")

    out: list[dict] = []

    def add(fid, title, items, why, how):
        if items:
            out.append(finding(fid, "notice", title, "; ".join(items), why, how, _RAW_LINKS, count=len(items)))

    add("expansion_ai_category_never_applies",
        "Load-balancing entries that match no player count", never,
        "An entry applies while the online count is between its MinPlayers and "
        "MaxPlayers. With the minimum above the maximum it never does.",
        "Swap the two, or set MaxPlayers to 0 or less for no upper bound.")
    add("expansion_ai_category_shadowed",
        "Load-balancing entries an earlier entry always beats", shadowed,
        "Expansion uses the first entry of a category that matches the online count. "
        "Earlier entries already cover every count this one does, so it never applies.",
        "Move the entry up, or narrow the entries above it.")
    add("expansion_ai_category_gap",
        "Player counts a load-balancing category has no entry for", gaps,
        f"With no matching entry, the category sets no limit at those counts "
        f"(the server has {max_players} slots)." if max_players is not None else "",
        "Add an entry for those counts if the category should limit them.")
    add("expansion_ai_category_none_allowed",
        "Player counts at which a load-balancing category allows no patrols", none_allowed,
        "MaxPatrols 0 allows no patrol of that category at those counts"
        + (". Global counts every patrol, so at Global's counts every patrol despawns."
           if global_none else ".")
        + " Expansion's own default does this for Global above 50 players.",
        "Raise MaxPatrols if patrols should run then, or leave it if that is the point.")
    return out


def _player_faction_findings(ai_doc: dict | None, factions: set[str]) -> list[dict]:
    entries = (ai_doc or {}).get("PlayerFactions")
    if not isinstance(entries, list):
        return []
    unknown = [why for entry in entries if (why := ai_refs.faction_problem(entry, factions, random_ok=True))]
    if not unknown:
        return []
    return [finding(
        "expansion_ai_player_faction_unknown", "warning",
        "Player factions Expansion does not know",
        "; ".join(unknown),
        "AISettings.json gives each player who connects a random faction from "
        "PlayerFactions. An unknown name is logged as 'Invalid faction name' and that "
        "player gets no faction. Installed: " + ", ".join(sorted(factions)) + ".",
        "Use installed faction names, spelled exactly, or RANDOM.",
        _AI_SETTINGS_LINKS, count=len(unknown),
    )]


# ── The report ──────────────────────────────────────────────────────────


def build_report(config) -> dict | None:
    """Everything zsm can tell about Expansion AI's own patrols, or None when
    the mod is not installed (or is disabled)."""
    name = installed_name(config)
    if name is None:
        return None

    path = patrol_path(config)
    ai_path = ai_settings_path(config)
    factions = ai_refs.installed_factions(config)
    doc, err = read_json(path) if path is not None else (None, None)
    ai_doc, _ai_err = read_json(ai_path)

    findings = _file_findings(config, path, doc, err)
    patrols = _patrols(doc) if doc is not None else []
    if doc is not None:
        findings.extend(_patrol_findings(config, doc, factions, ai_doc))
        findings.extend(_category_findings(doc, _max_players(config)))
    findings.extend(_player_faction_findings(ai_doc, factions))
    categories = doc.get("LoadBalancingCategories") if doc is not None else None

    return {
        "mod": name,
        "file": display_path(config, path) if path is not None else None,
        "files": {
            "patrols": bool(path and path.is_file()),
            "ai_settings": ai_path.is_file(),
        },
        "factions": sorted(factions),
        "patrols": len(patrols),
        "object_patrols": sum(1 for _, p in patrols if is_object_patrol(p)),
        "categories": sorted(categories) if isinstance(categories, dict) else [],
        "findings": findings,
    }


def doctor_row(config) -> tuple[str, bool, str] | None:
    """The Doctor's one-line verdict, or None when the mod is not installed."""
    report = build_report(config)
    if report is None:
        return None
    n, on_objects = report["patrols"], report["object_patrols"]
    parts = []
    if n:
        parts.append(f"{n} patrol{'' if n == 1 else 's'}" + (f", {on_objects} on objects" if on_objects else ""))
    return doctor_verdict(LABEL, report["findings"], parts)
