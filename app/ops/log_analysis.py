"""RPT log analysis — what the last server run actually says.

The old analyzer (``app/commands/log_analyzer.py``, 2026 Q3) matched four
Arma-era strings (``Cannot open file``, ``Duplicate addon``, ``Addon 'x'
requires``, ``Cannot compile``). A DayZ 1.29 Enfusion RPT contains none of them,
so a run whose central economy had rejected a types.xml entry, skipped an event
spawn and never shut down cleanly still reported "No known errors detected."

What an RPT actually is: ~95% engine chatter that every vanilla server prints on
every boot (missing p3d geometry components, PerfWarnings, absent localisation
strings), wrapped around a thin layer of lines that mean something. This module
is built around that ratio:

1. **Facts** (``run``/``stats``) — the header block, the launch command line,
   which mission and terrain actually loaded, when the server became joinable,
   the economy totals. None of this is a "finding"; it is the context that makes
   findings readable, and it was previously thrown away.
2. **Rules** — specific, high-signal messages with a *why* and a *how*, plus the
   deep link into the view that fixes them.
3. **Catch-alls** — any ``!!! [CE]``, ``SCRIPT (E)`` or ``(E):`` line that no
   rule claimed and no noise pattern excused is reported verbatim, grouped. A
   message Bohemia adds in 1.30 surfaces on its own rather than vanishing,
   which is exactly the failure mode of a fixed pattern list.
4. **Noise** — an explicit, labelled suppression list. Suppressed lines are
   *counted*, never silently dropped, so "8,412 cosmetic engine warnings" stays
   visible and the list can be audited against a real log.
5. **Origin** (``VANILLA_BASELINE``) — a finding that names something *Bohemia's
   own mission files ship* is tagged ``origin="vanilla"``, kept out of the
   error/warning headline and rendered in its own group. A clean install of
   1.29 Chernarus rejects ``ChristmasTree``, rejects
   ``Static_FrozenScientist_DE`` and skips ``VehicleTransitBus`` spawns on every
   single boot; reporting those next to a real fault told a brand-new server's
   admin their server was broken, which is the bug this layer exists to fix.
   Where the installed mission can answer for itself — pointless loot groups,
   orphan event spawns — ``app/ops/mission_baseline.py`` derives the answer and
   it beats the table; that is what reaches community maps (plans/39).
   ``MOD_BASELINE`` does the same for a pre-configured mod's own files
   (``origin="mod"``, plans/48 D6): a class RadZone's own types.xml lists and
   its own build rejects is the mod author's, not the admin's.

Rule of thumb when adding a pattern: if a stock server with no mods prints it on
every boot, it is noise. If it names a file, class or event *the admin controls*,
it is a finding. If it names something *the stock mission itself* ships, it is a
finding with a baseline entry — visible, explained, and not counted against the
admin.

Consumers: ``zsm log-analyzer`` (CLI), ``app/ops/doctor_report.py`` (the Doctor
page's Log Analyzer card) and ``GET /api/analysis/logs*`` (the Analysis page's
Logs tab). All three render the same dict — keep presentation out of here.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.ops.fix_hints import (
    CONFIGS,
    CONFIG_ECONOMYCORE,
    CONFIG_EVENTS,
    CONFIG_EVENTSPAWNS,
    CONFIG_IGNORELIST,
    CONFIG_MESSAGES,
    CONFIG_PLAYERSPAWNPOINTS,
    CONFIG_RANDOMPRESETS,
    CONFIG_SERVERDZ,
    CONFIG_TYPES,
    MAPS,
    MODS_CONFLICTS,
    MODS_INSTALLED,
    MOD_XML,
)
from app.ops.mission_baseline import MISSION, MissionBaseline, owner_label
from app.parsers.servercfg import DESCRIPTION_LIMIT
from app.shared.platform import IS_WINDOWS

# An RPT grows without bound while a server runs; a week-old one can be
# hundreds of MB. Analysis is a page load, not a batch job, so it reads a
# bounded prefix and says so (``truncated``) rather than stalling the request.
MAX_LINES = 400_000
# Past MAX_LINES the analysis jumps to the end and reads at most this many last
# lines (from at most _TAIL_BYTES), so a mod that floods the middle of a log
# cannot hide how the run ended or the errors printed while it stopped.
TAIL_LINES = 20_000
_TAIL_BYTES = 8 * 1024 * 1024
_COUNT_CHUNK = 1024 * 1024
MAX_SAMPLES = 5
MAX_SUBJECTS = 25

SEVERITIES = ("error", "warning", "notice")

#: Who put this in the log. ``vanilla`` findings are things Bohemia's own files
#: make every stock server print — real, explained, but not the admin's doing,
#: so they are excluded from the severity headline and grouped separately.
ORIGIN_YOURS = "yours"
ORIGIN_VANILLA = "vanilla"
#: The same, for a mod: its own shipped files make every server running that
#: build print it. See ``MOD_BASELINE``.
ORIGIN_MOD = "mod"


# ── Rules ─────────────────────────────────────────────────────────────────
#
# ``hint`` is a cheap literal prefilter: ``hint in line`` runs at C speed and
# skips the regex for ~99% of lines. It must be a substring of everything the
# pattern can match — get that wrong and the rule silently stops firing.


@dataclass(frozen=True)
class Rule:
    id: str
    severity: str
    title: str
    hint: str
    pattern: str | None
    why: str
    how: str
    subject: int = 0  # regex group holding the offending name (0 = none)
    value: int = 0  # regex group holding a count the message states itself
    links: tuple[tuple[str, str], ...] = ()
    # Some messages a stock server prints a handful of on every boot, and only
    # a *pile* of them means anything. Below this many occurrences the finding
    # is dropped rather than reported as a permanent false positive.
    min_count: int = 1
    # A literal the engine prints within the next few lines as part of the
    # same message. That line is this finding's, not a second one: the gas-mask
    # load error below is followed by a generic "Virtual Machine Exception".
    absorbs_next: str = ""


#: An unclaimed line repeated this often is a mod flooding the log. It also
#: pushes the end of the run past ``MAX_LINES``, so the analysis is cut short.
FLOOD_MIN_REPEATS = 10_000
#: How many distinct unclaimed lines the flood count tracks, to bound memory.
_FLOOD_MAX_KEYS = 5_000

LOG_FLOOD = Rule(
    id="log_flood",
    severity="warning",
    title="One line repeated many thousands of times",
    hint="",
    pattern=None,
    min_count=FLOOD_MIN_REPEATS,
    why=(
        "A mod printed the same line over and over. The log grows by hundreds of "
        "MB, and the live log in the Activity drawer skips most of it."
    ),
    how=(
        "Find the mod that prints the line, read what it asks for, and fix it or "
        "disable the mod."
    ),
    links=(("Mods", MODS_INSTALLED),),
)


#: How many lines after a hit an ``absorbs_next`` line may follow.
_ABSORB_WINDOW = 4


RULES: tuple[Rule, ...] = (
    # ── Central economy: the mission XML the app itself merges into ──────
    Rule(
        id="ce_type_missing",
        severity="error",
        title="types.xml entry has no such class",
        hint="will be ignored. (Type does not exist",
        pattern=r"Type '([^']+)' will be ignored\. \(Type does not exist",
        subject=1,
        why=(
            "types.xml lists a classname the server cannot find in any loaded "
            "addon. The entry is dropped, so that item never spawns."
        ),
        how=(
            "Either the mod providing it is not loaded, or the entry is a typo "
            "or a leftover from an uninstalled mod. Remove the entry or install "
            "the mod that defines it."
        ),
        links=(("Types editor", CONFIG_TYPES), ("Mods", MODS_INSTALLED)),
    ),
    Rule(
        id="ce_type_not_spawnable",
        severity="warning",
        title="types.xml entry is not spawnable",
        hint="will be ignored. (Not spawnable",
        pattern=r"Type '([^']+)' will be ignored\. \(Not spawnable",
        subject=1,
        why=(
            "The class exists but its config scope is not public, so the central "
            "economy refuses to spawn it. The entry is dropped."
        ),
        how=(
            "Base/helper classes and event-only items belong in events or "
            "spawnabletypes, not in types.xml. Remove the entry."
        ),
        links=(("Types editor", CONFIG_TYPES),),
    ),
    Rule(
        id="ce_missing_event",
        severity="warning",
        title="Event spawn points for an event that does not exist",
        hint="non-existing event",
        pattern=r"Skipping entry for non-existing event '([^']+)'",
        subject=1,
        why=(
            "cfgeventspawns.xml supplies positions for an event that events.xml "
            "does not define, so those positions are ignored."
        ),
        how=(
            "Add the event to events.xml, or drop its block from "
            "cfgeventspawns.xml. A half-removed mod is the usual cause."
        ),
        links=(("Events", CONFIG_EVENTS), ("Event spawns", CONFIG_EVENTSPAWNS)),
    ),
    # The next three were read off stock DayZ 1.30 boots (1.30.164078,
    # 2026 Q4). Before them their lines went to the `ce_other` catch-all.
    Rule(
        id="ce_no_player_spawns",
        severity="error",
        title="No valid spawn points for new players",
        hint="[CE][PlayerSpawnPoints] :: ",
        pattern=r"\[CE\]\[PlayerSpawnPoints\] :: (?:There are no valid groups|NO VALID SPAWNS)",
        absorbs_next="NO VALID SPAWNS",
        why=(
            "The central economy found no spawn group it can use in "
            "cfgplayerspawnpoints.xml and says new characters will spawn at "
            "0,0,0, the corner of the map. It logs this once for each section of "
            "the file that has the problem (fresh, hop, travel)."
        ),
        how=(
            "Check the <group> entries in cfgplayerspawnpoints.xml, then join "
            "with a new character to see where it spawns."
        ),
        links=(("Player spawn points", CONFIG_PLAYERSPAWNPOINTS),),
    ),
    Rule(
        id="ce_event_child_missing",
        severity="warning",
        title="Event child with no such class",
        hint="Unable to create child (",
        pattern=r"\[CE\]\[DE\] \([^)]*\) :: Unable to create child \(([^)]+)\) as the type does not exist",
        subject=1,
        why=(
            "An event in events.xml lists a child class the server cannot find in "
            "any loaded addon, so the event spawns without it."
        ),
        how=(
            "Fix the class name in the event's <children>, or install the mod "
            "that defines it."
        ),
        links=(("Events", CONFIG_EVENTS),),
    ),
    Rule(
        id="ce_vehicles_short",
        severity="notice",
        title="Fewer vehicles than the event asks for",
        hint="Failed to spawn the requested amount",
        pattern=r'\[CE\]\[VehicleRespawner\] \([^)]*\) :: (?:Init|Respawning): "([^"]+)" - Failed to spawn the requested amount',
        subject=1,
        why=(
            "A vehicle event could not place its full number of vehicles within "
            "the attempts it allows, at startup (Init) or on a later boot "
            "(Respawning). The line gives both numbers."
        ),
        how=(
            "Add positions for the event in cfgeventspawns.xml, or lower its "
            "nominal in events.xml."
        ),
        links=(("Events", CONFIG_EVENTS), ("Event spawns", CONFIG_EVENTSPAWNS)),
    ),
    Rule(
        id="ce_xml_parse_errors",
        severity="error",
        title="Errors while parsing mission XML",
        hint="Errors during XML parse",
        pattern=r"(\d+) Errors during XML parse",
        value=1,
        why=(
            "The central economy hit malformed or invalid entries while loading "
            "the mission XML. Whatever failed to parse is simply absent at "
            "runtime."
        ),
        how=(
            "The line after this one names the last group it read, which brackets "
            "the damage. Check the most recently merged mod XML, then re-run the "
            "XML merge."
        ),
        links=(("cfgeconomycore.xml", CONFIG_ECONOMYCORE), ("Configs", CONFIGS)),
    ),
    Rule(
        id="ce_groups_no_points",
        severity="warning",
        title="Loot groups with no spawn points",
        hint="groups have no points",
        pattern=r"(\d+) groups have no points",
        value=1,
        why=(
            "mapgroupproto.xml defines container groups that no building "
            "actually exposes points for, so nothing spawns in them."
        ),
        how=(
            "Usually harmless leftovers from a custom mapgroupproto. Worth "
            "checking if loot is missing from a specific building type."
        ),
        links=(("Configs", CONFIGS),),
    ),
    # Printed beside "groups have no points" by stock Livonia (1) and Sakhal
    # (12), never by stock Chernarus — which is why, until a boot of those two
    # was read (2026 Q3), it only ever reached the report as an unexplained
    # catch-all line blamed on the admin.
    Rule(
        id="ce_groups_wrong_points",
        severity="warning",
        title="Loot groups with invalid spawn points",
        hint="groups have wrong points",
        pattern=r"(\d+) groups have wrong points",
        value=1,
        why=(
            "mapgroupproto.xml gives these container groups points the economy "
            "rejects as it loads them. Those points never spawn loot; the rest "
            "of each group is unaffected."
        ),
        how=(
            "Only worth chasing in a mapgroupproto.xml you edited or one a mod "
            "map ships. The \"[CE][Point] Removing … from <building>\" lines "
            "name the buildings."
        ),
        links=(("Configs", CONFIGS),),
    ),
    # One line per dropped position, naming the building. Every stock map
    # prints some — at boot on Livonia and Sakhal, during the run on Chernarus —
    # and which buildings varies from run to run, so the baseline is the map,
    # not a list of names.
    Rule(
        id="ce_point_removed",
        severity="notice",
        title="Loot positions dropped by the economy",
        hint="[CE][Point] Removing",
        pattern=r"\[CE\]\[Point\] Removing [-\d.]+,\s*[-\d.]+ from (\S+)",
        subject=1,
        why=(
            "The economy dropped a loot position it could not use in the named "
            "building. That one spot never spawns loot; the building's other "
            "positions still do."
        ),
        how=(
            "Only worth a look for a building from a mapgroupproto.xml you "
            "edited or one a mod map ships."
        ),
        links=(("Configs", CONFIGS),),
    ),
    Rule(
        id="ce_type_added_by_event",
        severity="notice",
        title="Event spawns a class no types file defines",
        # Bohemia's spelling, not ours.
        hint="Programatically adding",
        pattern=r'Programatically adding "([^"]+)" to types',
        subject=1,
        why=(
            "A dynamic event spawns this class, but no types file lists it, so "
            "the economy made up a default entry for it at runtime."
        ),
        how=(
            "Add a <type> entry for it in types.xml so its lifetime and flags "
            "are yours rather than the engine's defaults."
        ),
        links=(("Types editor", CONFIG_TYPES), ("Events", CONFIG_EVENTS)),
    ),
    Rule(
        id="ce_messages_missing",
        severity="warning",
        title="Mission has no messages.xml",
        hint="Failed to read messages file",
        # Anchored on the `!!!` copy: the engine prints the line twice.
        pattern=r"!!! \[CE\]\[offlineDB\] :: Failed to read messages file",
        why=(
            "The economy could not read db/messages.xml, so the scheduled "
            "in-game messages it would hold (welcome text, timed broadcasts) are "
            "not sent."
        ),
        how=(
            "Add a db/messages.xml to the mission if you want those messages. "
            "Zedstead's restart warnings go over RCon and do not need it."
        ),
        links=(("Messages", CONFIG_MESSAGES),),
    ),
    Rule(
        id="ce_preset_item_ignored",
        severity="notice",
        title="Preset item the ignore list ignores",
        hint="because it is ignored",
        pattern=r"PRESET LOAD ERROR: Spawnable type '[^']*' could not load preset item '([^']+)' because it is ignored",
        subject=1,
        why=(
            "A preset names an item that cfgignorelist.xml tells the central "
            "economy to ignore, so the preset never spawns it."
        ),
        how=(
            "To have the item spawn, take it off the ignore list. To keep it "
            "out, remove it from the preset in cfgrandompresets.xml."
        ),
        links=(("Ignore list", CONFIG_IGNORELIST), ("Random presets", CONFIG_RANDOMPRESETS)),
    ),
    Rule(
        id="ce_no_shutdown_message",
        severity="warning",
        title="Previous run did not shut down cleanly",
        hint="No Shutdown message present",
        pattern=None,
        why=(
            "The economy database was opened without the shutdown marker the "
            "previous run should have written — that run crashed, was killed, or "
            "the machine went down. Persistence written in its last minutes may "
            "be lost."
        ),
        how=(
            "Stop the server through Zedstead (or systemctl) rather than killing the "
            "process. If it was not stopped by hand, look for a crash dump next "
            "to this log."
        ),
        links=(("Backups", "/backups"),),
    ),
    # ── Persistence / storage ───────────────────────────────────────────
    #
    # Both of these were invisible before 2026 Q3: `!!! Serious stream
    # damage` is not a `!!! [CE]` line so no catch-all claimed it, and the
    # item-loader line has no severity marker at all. In the log that prompted
    # this section the two agree exactly — 20 damaged offsets, then
    # "261 items loaded. (20 failed)" — i.e. twenty persisted objects were
    # silently dropped from the world while the report said the run was fine.
    Rule(
        id="storage_stream_damage",
        severity="warning",
        title="Damaged persistence data skipped while loading",
        hint="Serious stream damage detected during load",
        # No subject: the only thing the line names is a file offset, and
        # twenty hex offsets in the detail line teach nobody anything.
        pattern=None,
        why=(
            "The engine could not read parts of the storage files, so whatever "
            "was persisted at those offsets — built structures, containers, "
            "vehicles — is gone from the world. Each line is one damaged offset."
        ),
        how=(
            "Compare the count with the \"N failed\" on the item-loader line just "
            "below it. If the loss matters, restore the newest backup taken "
            "before this run; if it keeps happening, the server is being killed "
            "mid-save rather than stopped."
        ),
        links=(("Backups", "/backups"),),
    ),
    Rule(
        id="storage_items_failed",
        severity="warning",
        title="Persistence items that failed to load",
        hint="items loaded. (",
        # Only a non-zero failure count is a finding — a stock run prints this
        # line a dozen times with "(0 failed)".
        pattern=r"(\d+) items loaded\. \(([1-9]\d*) failed\)",
        value=2,
        why=(
            "The central economy read a persistence file and had to discard "
            "entries in it. Those objects no longer exist in the world."
        ),
        how=(
            "Usually the same event as the damaged-stream lines above. Restore a "
            "backup if the lost objects matter."
        ),
        links=(("Backups", "/backups"),),
    ),
    Rule(
        id="storage_scripted_vars",
        severity="warning",
        title="Saved items that could not be read",
        hint="Scripted variables corrupted upon",
        # The "!!!" line only: the "Reason:" line of the exception block below
        # it repeats the same words and is the same item.
        pattern=r'!!! Scripted variables corrupted upon "([^"]+)"',
        subject=1,
        absorbs_next="SCRIPT    (E): Virtual Machine Exception",
        why=(
            "An item in persistence was saved with data the loaded mods no longer "
            "expect, so the engine drops it. Each line is one item gone from the "
            "world."
        ),
        how=(
            "Expected once, on the first boot after adding or removing a mod that "
            "changes what this item saves. If it shows again on a later boot, the "
            "storage and the mods disagree for good: check what changed."
        ),
        links=(("Mods", MODS_INSTALLED), ("Backups", "/backups")),
    ),
    # ── Launch / server identity ────────────────────────────────────────
    Rule(
        id="description_too_long",
        severity="warning",
        title="Server description over the 255-character limit",
        # Bohemia's own typo — matching the misspelling is deliberate.
        hint="description exceeeds the limit",
        pattern=None,
        why=(
            "serverDZ.cfg's description is longer than the 255 characters the "
            "protocol allows, so the server browser shows no description at all."
        ),
        how="Shorten `description` in serverDZ.cfg to 255 characters or fewer.",
        links=(("serverDZ.cfg", CONFIG_SERVERDZ),),
    ),
    Rule(
        id="steam_init_failed",
        severity="error",
        title="Steam game-server registration failed",
        hint="SteamGameServer_Init",
        pattern=r"(?:FAIL|ERROR|Failed).*SteamGameServer_Init",
        why=(
            "The server could not register with Steam, so it never appears in "
            "the in-game server browser however healthy it otherwise is."
        ),
        how=(
            "Check the query port is free and reachable (game port + 1 by "
            "default) and that no second server instance holds it."
        ),
        links=(("serverDZ.cfg", "/configs/serverdz?q=steamQueryPort"),),
    ),
    Rule(
        id="battleye_failed",
        severity="error",
        title="BattlEye failed to start",
        hint="BattlEye",
        pattern=r"BattlEye.*(?:[Ff]ailed|[Ee]rror|not (?:be )?(?:found|installed))",
        why=(
            "With BattlEye unavailable the server either refuses connections or "
            "runs unprotected, depending on serverDZ.cfg."
        ),
        how=(
            "Check the -BEpath directory exists and is writable, and that the "
            "BattlEye config file is present in it."
        ),
        links=(("BattlEye config", "/configs/battleye"),),
    ),
    # ── Mods / addons ───────────────────────────────────────────────────
    Rule(
        id="addon_requires",
        severity="error",
        title="Mod dependency missing or loaded too late",
        hint="requires addon",
        pattern=r"Addon '([^']+)' requires addon '([^']+)'",
        subject=2,
        why=(
            "An addon declares a requiredAddon the server did not load before "
            "it. Scripts from the dependent mod may not run."
        ),
        how=(
            "Install the missing mod, or move it earlier in the load order — "
            "@CF and framework mods must come first."
        ),
        links=(("Check mods", MODS_CONFLICTS), ("Mods", MODS_INSTALLED)),
    ),
    Rule(
        id="duplicate_addon",
        severity="warning",
        title="Two mods ship the same addon",
        hint="Duplicate addon",
        pattern=None,
        why=(
            "Two loaded PBOs declare the same addon name; which one wins is "
            "load-order dependent and usually not what you intended."
        ),
        how="Remove one of the two mods, or check for a duplicated mod folder.",
        links=(("Check mods", MODS_CONFLICTS),),
    ),
    Rule(
        id="missing_file",
        severity="error",
        title="Server could not open a file it was told to load",
        hint="Cannot open file",
        pattern=r"Cannot open file '?\"?([^'\"\n]+)",
        subject=1,
        why="A referenced file is absent — commonly a mod PBO that did not copy.",
        how=(
            "Re-run the mod install so the mod folder is copied fresh from the "
            "workshop cache."
        ),
        links=(("Mods", MODS_INSTALLED),),
    ),
    Rule(
        id="missing_mod_model",
        severity="notice",
        title="Missing model referenced outside the vanilla data",
        hint="Cannot open object",
        pattern=r"Cannot open object ([^\s].*?)\s*$",
        subject=1,
        why=(
            "A p3d model outside dz\\ / dta\\ could not be opened. In a modded "
            "server this normally means a mod PBO is incomplete."
        ),
        how=(
            "Re-download the mod that owns the path. Vanilla paths are filtered "
            "out of this finding already."
        ),
        links=(("Mods", MODS_INSTALLED),),
    ),
    Rule(
        id="script_module_failed",
        severity="error",
        title="Script module failed to compile",
        hint="compile",
        pattern=r"(?:Can't|Cannot) compile\s+\"?([^\"\n]+?)\"?(?: script module)?!?\s*$",
        subject=1,
        why=(
            "A script module did not build, so the server starts crippled or not "
            "at all. Nearly always the last mod installed."
        ),
        how=(
            "The lines just above name the file and line number. Remove or "
            "update that mod, then restart."
        ),
        links=(("Mods", MODS_INSTALLED),),
    ),
    Rule(
        id="anim_load_error",
        severity="warning",
        title="Animation config could not be loaded",
        hint="ANIMATION (E)",
        pattern=r"ANIMATION\s+\(E\):\s*Can't load\s+(.*?)\s*$",
        subject=1,
        why=(
            "The engine looked for an animation config it could not read. The "
            "path says whose: one of the base game's other terrains is normal "
            "and harmless, the loaded map or a mod's map is not."
        ),
        how=(
            "If the path names a map you are not running, re-apply the map so "
            "the mission and its addons match."
        ),
        links=(("Maps", MAPS),),
    ),
    Rule(
        id="two_animation_mods",
        severity="error",
        title="Two animation mods are loaded together",
        hint="TWO ANIMATION MODS",
        pattern=r"TWO ANIMATION MODS.*?UNINSTALL\s+(.+?)\s*,",
        subject=1,
        why=(
            "A mod found a second animation mod in the same mod list and says the "
            "server can crash. @Survivor Animations prints this when "
            "@DayZ-Expansion-Animations also loads, and it can repeat the line "
            "about a million times while the server stops."
        ),
        how=(
            "Disable one of the two animation mods. The line names the one the "
            "mod wants gone. Disable the mods that depend on it too."
        ),
        links=(("Mods", MODS_INSTALLED),),
    ),
    Rule(
        id="off_map_object",
        severity="notice",
        title="Objects placed outside the map",
        hint="outside landscape",
        pattern=None,
        min_count=25,
        why=(
            "Something was spawned at coordinates off the terrain. Vanilla "
            "prints a few of these; a large count points at custom spawn "
            "positions with bad coordinates."
        ),
        how=(
            "Check recently edited object/event spawn positions against the map "
            "bounds in the Map Editor."
        ),
        links=(("Map editor", "/map-editor"),),
    ),
    # ── zsm's own spawn test (app/ops/spawn_test_ops.py) ────────────────
    # The block prints one line per test, at `SCRIPT :` — no catch-all sees
    # it, so without these rules the only answer to "did it spawn?" is a grep.
    # The 0-of-N rule is declared first: "first rule wins", and its pattern is
    # what tells the two apart.
    Rule(
        id="spawn_test_none",
        severity="warning",
        title="Spawn test created nothing",
        hint="[zsm spawn test] created 0 of",
        pattern=r"\[zsm spawn test\] created 0 of (\d+) (\S+)",
        subject=2,
        why=(
            "Zedstead's spawn test asked the engine for this class and got nothing "
            "back, so the class cannot be created — usually a base class, a "
            "typo, or a mod that is not loaded."
        ),
        how=(
            "Pick a spawnable class on the mod's page and start the test again. "
            "End the current test first; it stays armed until you do."
        ),
        links=(("Mods", MODS_INSTALLED),),
    ),
    Rule(
        id="spawn_test_ok",
        severity="notice",
        title="Spawn test spawned its objects",
        hint="[zsm spawn test] created",
        pattern=r"\[zsm spawn test\] created ([1-9]\d*) of (\d+) (\S+)",
        subject=3,
        why=(
            "Zedstead's spawn test placed its objects next to the first player who "
            "joined. They are real objects in the world until the test is ended."
        ),
        how=(
            "End the test on the mod's page when you are done — it restores "
            "init.c and the storage folder, which is what removes them."
        ),
        links=(("Mods", MODS_INSTALLED),),
    ),
)

# ── Catch-alls ───────────────────────────────────────────────────────────
#
# Reported verbatim so an unrecognised message is visible rather than absent.
# Order matters: the first that matches a surviving line claims it.

CATCH_ALLS: tuple[Rule, ...] = (
    Rule(
        id="ce_other",
        severity="warning",
        title="Other central-economy complaints",
        hint="!!! [CE]",
        pattern=None,
        why=(
            "The central economy flagged these itself (its own `!!!` prefix) and "
            "no specific rule here recognises them yet."
        ),
        how="Read the lines below — CE messages usually name the file at fault.",
        links=(("Configs", CONFIGS),),
    ),
    Rule(
        id="script_error",
        severity="error",
        title="Script errors",
        hint="SCRIPT",
        pattern=r"SCRIPT\s+\(E\):\s*(.*)$",
        subject=1,
        why="A mod or mission script raised an error the engine logged.",
        how=(
            "The message names the script file and line. Report it to the mod "
            "author, or remove the mod to confirm it is the source."
        ),
        links=(("Mods", MODS_INSTALLED),),
    ),
    Rule(
        id="engine_error",
        severity="notice",
        title="Other engine errors",
        hint="(E):",
        pattern=r"^\s*(?:\d{1,2}:\d{2}:\d{2}\s+)?\s*(\S.*\(E\):\s*.*)$",
        subject=1,
        why="Engine-level errors that no rule here classifies.",
        how="Usually cosmetic on a vanilla server; worth reading if new.",
    ),
)


# ── Noise ────────────────────────────────────────────────────────────────
#
# Every stock DayZ server prints all of this on every boot. Suppressed lines are
# counted per label and reported as a total, never dropped silently.
# Verified against a vanilla 1.29 Chernarus+ RPT (2026 Q3).

NOISE: tuple[tuple[str, str, str | None], ...] = (
    # (label, literal hint, optional regex)
    ("Model geometry warnings", "Warning: No components in", None),
    ("Model geometry warnings", "selection faces are less then 4", None),
    ("Model geometry warnings", "PerfWarning: Way too much components", None),
    ("Model geometry warnings", "Placement slopelandcontact failed", None),
    ("Model geometry warnings", "DamageSystem Warning: Selection", None),
    ("Model geometry warnings", "is missing geometry components", None),
    ("Missing localisation strings", "Localization not present:", None),
    ("Missing localisation strings", "not in localization table", None),
    ("Missing localisation strings", "listed twice in", None),
    ("Config lookup warnings", "Warning Message: No entry", None),
    ("Config lookup warnings", "Trying to access error value", None),
    ("Config lookup warnings", "Warning Message: Size:", None),
    ("Config lookup warnings", "not an array", None),
    ("Config lookup warnings", "Updating base class", None),
    ("Config lookup warnings", "Unrecognized CfgNonAIVehicles simulation", None),
    ("Config lookup warnings", "from CfgActionSounds", None),
    ("Config lookup warnings", "missing in PreloadConfig", None),
    ("Config lookup warnings", "is not a class", None),
    ("Addon loading chatter", "FileSystem: Adding", None),
    ("Animation source warnings", "unknown animation source", None),
    ("Animation source warnings", "Invalid parent bone", None),
    ("Animation source warnings", "Animation source", None),
    ("Entity/proxy warnings", "must be inherited from class", None),
    ("Entity/proxy warnings", "Unknown object class", None),
    ("Entity/proxy warnings", "Load entity type", r"ENTITY\s+: Load entity type"),
    ("Entity/proxy warnings", "Create entity type", r"WORLD\s+: Create entity type"),
    ("Rendering warnings", "Fresnel n must be", None),
    ("Rendering warnings", "Unrecognized texture type", None),
    ("Rendering warnings", "Bad uvSource", None),
    ("Rendering warnings", "contains water texture", None),
    ("Rendering warnings", "SW keep height animation used", None),
    ("Rendering warnings", "Atmosphere not loaded", None),
    ("Rendering warnings", "Unknown hit sound type", None),
    ("Rendering warnings", "Terrain normal texture loaded", None),
    # Two `(E):` lines a stock 1.29 server prints on every boot. Without these
    # they fell through to the `engine_error` catch-all, whose own advice reads
    # "usually cosmetic on a vanilla server" — it already knew, and reported
    # them anyway. Both are scoped so a *mod's* version still surfaces: the
    # material one only for objects under `dz/`, and the animation one only for
    # the bare continuation line, which carries no path at all.
    ("Vanilla asset warnings", "(E): Failed to open file, line 0, column 0", None),
    (
        "Vanilla asset warnings",
        "cannot load material",
        r"MATERIAL\s+\(E\):\s*Object dz[/\\]",
    ),
    # A model shared by entities with different `physLayer` values. The test
    # server printed 539-549 of these on every boot (2026 Q3), and every
    # one named one of Bohemia's own `dz\` models (walls, weapons, ammo): the
    # admin can do nothing about them. A mod's model with the same mismatch
    # still reaches the catch-all, since only a `dz` path matches here.
    (
        "Vanilla asset warnings",
        "physLayer",
        r"PHYSICS\s+\(E\):\s*LODShape:\s*'dz[/\\]",
    ),
    # Two more a stock 1.30 Experimental server prints on boot (1.30.164014,
    # 2026 Q4, plans/66): a dedicated server has no post-process textures,
    # and the base game's IZH18 config names a sound shader it does not ship.
    # Both are scoped to exactly those names, so a mod's own version of either
    # message still reaches the catch-all.
    (
        "Rendering warnings",
        "SMAAEffect",
        r"MATERIAL\s+\(E\):\s*SMAAEffect \w+ texture not found \(system[/\\]",
    ),
    (
        "Vanilla asset warnings",
        "IZH18_silencerHomeMade_SoundSet",
        r"AUDIO\s+\(E\):\s*Sound set 'IZH18_silencerHomeMade_SoundSet' references missing sound shader 'IZH18_Closure_SoundShader'",
    ),
    # A stock 1.30 Sakhal boot (1.30.164078, 2026 Q4): the base game's
    # Sakhal sound config names a shader it does not ship. Scoped to that name.
    (
        "Vanilla asset warnings",
        "WindTree_Small_Leaves_Sakhal_SoundSet",
        r"AUDIO\s+\(E\):\s*Sound set 'WindTree_Small_Leaves_Sakhal_SoundSet' references missing sound shader 'WindTree_Small_Leaves_Sakhal_SoundShader'",
    ),
)

# ``Cannot open object`` fires ``missing_mod_model`` — except for these, which
# vanilla itself asks for and never ships. Kept as data, not a regex special
# case, so the reason for each exemption stays readable.
_VANILLA_MISSING_MODELS = {"bmp.p3d"}
_VANILLA_MODEL_ROOTS = ("dz\\", "dz/", "dta\\", "dta/")


# ── Vanilla baseline ─────────────────────────────────────────────────────
#
# Findings a *stock* mission produces. Unlike NOISE these are real, named and
# worth being able to read — they are just not the admin's doing, so they are
# tagged ``origin="vanilla"``, given their own explanation in place of the
# rule's (whose "remove the entry" advice is wrong for a file Bohemia ships)
# and kept out of the severity headline.
#
# Every entry cites the file in Bohemia's DayZ-Central-Economy repo that ships
# it, so the claim can be rechecked when a mission update lands rather than
# trusted. Checked against commit 9a21bb9 (2026 Q3) and observed in two
# unrelated real 1.29 logs.


@dataclass(frozen=True)
class Baseline:
    """One thing a stock mission makes the server complain about.

    ``subjects`` empty and ``count`` zero means the whole finding is baseline.
    ``terrains`` empty means every map; otherwise only the ones listed, because
    Bohemia does not ship the same entries in every mission (``ChristmasTree``
    is in Chernarus' types.xml and in neither Livonia's nor Sakhal's).
    """

    rule: str
    note: str  # replaces the rule's `how` on the vanilla half
    source: str  # where in the stock files it comes from — so this is checkable
    subjects: tuple[str, ...] = ()
    terrains: tuple[str, ...] = ()
    count: int = 0  # self-counting rules: how many a stock mission prints
    #: For rules whose subject is a path rather than a name — ``(subject,
    #: terrain) -> bool``. Checked after ``subjects``.
    match: Callable[[str, str], bool] | None = None
    #: The game versions the entry was measured on, as prefixes of the log's
    #: ``Version`` line ("1.29" matches 1.29.163709). Empty means every
    #: version, and so does a log with no ``Version`` line: what cannot be
    #: ruled out is not ruled out.
    versions: tuple[str, ...] = ()


#: What a stock 1.30 shutdown logs as SCRIPT (E): Bohemia's own scripts leak one
#: BunkerBroadcastManager, and the summary line counts exactly that one. A second
#: leaked class makes the summary say 2x, so it stays the admin's with the class.
_STOCK_130_LEAK = re.compile(
    r"Leaked 'BunkerBroadcastManager' script instance \(1x\)!|==== Total Leaks \(1x\)! ===="
)


def _is_stock_130_leak(subject: str, terrain: str) -> bool:
    return bool(_STOCK_130_LEAK.fullmatch(subject.strip()))


def _is_vanilla_terrain_anim(subject: str, terrain: str) -> bool:
    """``sakhal/Anims/…`` — base-game data, whichever map is loaded.

    The engine reads the animation configs of every terrain shipped in the game
    data at startup, before any mission loads, and logs the one it cannot read.
    This used to exempt only *other* terrains; a stock Sakhal boot
    (2026 Q3) logs the identical ``sakhal/Anims/cfg/skeletons.anim.xml`` line
    on Sakhal itself. A path naming a *mod's* map is left alone: that is the
    case the rule was written for.
    """
    from app.maps.registry import VANILLA_TERRAINS

    head = subject.replace("\\", "/").lstrip("/").split("/", 1)[0].lower()
    return head in VANILLA_TERRAINS


def _is_stock_resource(subject: str, terrain: str) -> bool:
    """A resource failure for a file under Bohemia's own DZ/ folder."""
    return bool(re.search(r"Failed to open \(/?DZ/", subject, re.IGNORECASE))


def _is_stock_navmesh(subject: str, terrain: str) -> bool:
    """Livonia's navmesh line, which names no part (_is_stock_resource's kin)."""
    return "Transform navmesh part double registration" in subject


VANILLA_BASELINE: tuple[Baseline, ...] = (
    Baseline(
        rule="ce_type_missing",
        subjects=("Static_FrozenScientist_DE",),
        terrains=("chernarusplus", "enoch"),
        source="stock db/types.xml (Chernarus line ~19802, Livonia likewise)",
        note=(
            "Bohemia's own types.xml lists it and the base game does not define "
            "the class, so every stock server rejects it on every boot. Nothing "
            "to fix — removing it only makes the next mission update put it back."
        ),
    ),
    Baseline(
        rule="ce_type_not_spawnable",
        subjects=("ChristmasTree",),
        terrains=("chernarusplus",),
        source="stock db/types.xml line ~5075 + mapgroupproto.xml line ~16660",
        note=(
            "A seasonal helper class Bohemia ships in the stock Chernarus "
            "types.xml with a non-public scope. Rejected on every boot of every "
            "vanilla server; leave it alone."
        ),
    ),
    # The next four were read off a boot of stock Livonia and Sakhal on
    # 1.29.163709 (2026 Q3, plans/39 C), each checked against that map's
    # shipped db/types.xml.
    Baseline(
        rule="ce_type_not_spawnable",
        subjects=("ChristmasTree_Green",),
        terrains=("enoch", "sakhal"),
        source="stock db/types.xml of Livonia and Sakhal; logged by both stock boots",
        note=(
            "The Livonia and Sakhal counterpart of Chernarus' ChristmasTree: a "
            "seasonal class Bohemia's own types.xml lists with a non-public "
            "scope. Rejected on every boot; leave it alone."
        ),
    ),
    Baseline(
        rule="ce_type_not_spawnable",
        subjects=(
            "Land_Wreck_sed02_aban1_Police_DE",
            "Land_Wreck_sed02_aban2_Police_DE",
            "Land_wreck_sed02_aban1_police",
            "Land_wreck_sed02_aban2_police",
        ),
        terrains=("sakhal",),
        source="stock Sakhal db/types.xml; logged by a stock Sakhal boot",
        note=(
            "Police-car wrecks Bohemia's Sakhal types.xml lists as loot, with a "
            "non-public scope. Every stock Sakhal server rejects them."
        ),
    ),
    Baseline(
        rule="ce_type_added_by_event",
        subjects=("Land_wreck_sed02_aban1_police_DE", "Land_wreck_sed02_aban2_police_DE"),
        terrains=("sakhal",),
        source="stock Sakhal db/events.xml children Land_Wreck_sed02_aban{1,2}_"
        "Police_DE, whose types.xml entries are rejected as not spawnable; "
        "logged by a stock Sakhal boot",
        note=(
            "A stock Sakhal event spawns these police-car wrecks, and the "
            "types.xml entries Bohemia ships for them are the ones rejected as "
            "not spawnable, so the engine fills in a default entry. Every stock "
            "Sakhal server logs it."
        ),
    ),
    Baseline(
        rule="ce_type_missing",
        subjects=("WinterMilitaryCoat_Greay",),
        terrains=("enoch",),
        source="stock Livonia db/types.xml; logged by a stock Livonia boot",
        note=(
            "A misspelling in Bohemia's own Livonia types.xml (Greay). Every "
            "stock Livonia server rejects it; the coat itself spawns under its "
            "real name through the entries that spell it right."
        ),
    ),
    Baseline(
        rule="ce_missing_event",
        subjects=("VehicleTransitBus",),
        terrains=("chernarusplus", "enoch"),
        source="stock cfgeventspawns.xml; no matching entry in stock db/events.xml",
        note=(
            "The stock cfgeventspawns.xml carries positions for a bus event the "
            "stock events.xml never defines. Shipped that way — the positions "
            "are simply unused."
        ),
    ),
    # Livonia's 10 was read off a stock boot (2026 Q3); stock Sakhal prints
    # no such line at all. Plans/39 set out to derive this count instead and
    # the derivation failed on both maps — see app/ops/mission_baseline.py.
    # Both counts are 1.29's: a stock 1.30.164014 Chernarus boot prints no such
    # line (2026 Q4, plans/66), so on 1.30 every parse error is the
    # admin's. Livonia on 1.30 is not measured, and is treated the same way.
    Baseline(
        rule="ce_xml_parse_errors",
        terrains=("enoch",),
        versions=("1.29",),
        count=10,
        source="measured on a stock 1.29.163709 Livonia boot",
        note=(
            "Stock Livonia prints exactly this many on every boot — they are in "
            "Bohemia's own mission XML. A number above 10 is the one worth "
            "chasing, and this finding says so when it sees one."
        ),
    ),
    Baseline(
        rule="ce_xml_parse_errors",
        terrains=("chernarusplus",),
        versions=("1.29",),
        count=10,
        source="measured on two unrelated stock 1.29 Chernarus servers (builds "
        "163451 and 163709), both exactly 10",
        note=(
            "Stock Chernarus prints exactly this many on every boot — they are "
            "in Bohemia's own mission XML. A number *above* 10 is the one worth "
            "chasing, and this finding says so when it sees one."
        ),
    ),
    Baseline(
        rule="ce_groups_no_points",
        terrains=("chernarusplus",),
        count=5,
        source="measured on the same two stock 1.29 Chernarus servers",
        note=(
            "Five leftover groups in Bohemia's own mapgroupproto.xml. A higher "
            "number points at a custom mapgroupproto; five is the floor."
        ),
    ),
    # Fallbacks only: with the mission readable, the count is derived from its
    # own mapgroupproto.xml (mission_baseline.pointless_groups) and these are
    # never consulted. Both agree with the derivation, measured in 2026 Q3.
    Baseline(
        rule="ce_groups_no_points",
        terrains=("enoch",),
        count=4,
        source="measured on a stock 1.29.163709 Livonia boot",
        note="Four leftover groups in Bohemia's own Livonia mapgroupproto.xml.",
    ),
    Baseline(
        rule="ce_groups_no_points",
        terrains=("sakhal",),
        count=5,
        source="measured on a stock 1.29.163709 Sakhal boot",
        note="Five leftover groups in Bohemia's own Sakhal mapgroupproto.xml.",
    ),
    Baseline(
        rule="ce_groups_wrong_points",
        terrains=("enoch",),
        count=1,
        source="measured on a stock 1.29.163709 Livonia boot",
        note=(
            "Stock Livonia prints exactly one on every boot, for a group in "
            "Bohemia's own mapgroupproto.xml. More than one is worth a look."
        ),
    ),
    Baseline(
        rule="ce_groups_wrong_points",
        terrains=("sakhal",),
        count=12,
        source="measured on a stock 1.29.163709 Sakhal boot",
        note=(
            "Stock Sakhal prints exactly twelve on every boot, for groups in "
            "Bohemia's own mapgroupproto.xml. More than twelve is worth a look."
        ),
    ),
    Baseline(
        rule="ce_point_removed",
        terrains=("chernarusplus", "enoch", "sakhal", "nasdara"),
        source="stock boots of all four maps; Chernarus logs 1-3 per run, "
        "Livonia 19 and Sakhal 62 at boot on 1.29; on 1.30.164078 Chernarus "
        "logged 7, Livonia 20, Sakhal 94 and Badlands 202",
        note=(
            "Every stock map drops some of its own loot positions, and which "
            "buildings it names changes from run to run. On a stock map this is "
            "Bohemia's data; nothing to fix."
        ),
    ),
    Baseline(
        rule="ce_messages_missing",
        terrains=("sakhal",),
        source="stock Sakhal ships no db/messages.xml (dayz-docs/CLAUDE.md)",
        note=(
            "Bohemia ships Sakhal without a db/messages.xml, so every stock "
            "Sakhal server logs this. Add one only if you want its timed "
            "messages; Zedstead's restart warnings go over RCon and do not need it."
        ),
    ),
    Baseline(
        rule="ce_preset_item_ignored",
        subjects=("Flaregun", "Ammo_Flare", "Ammo_FlareRed", "Ammo_FlareGreen", "Ammo_FlareBlue"),
        terrains=("chernarusplus", "enoch", "sakhal"),
        versions=("1.30",),
        source="stock 1.30 cfgignorelist.xml lists all five while cfgrandompresets.xml "
        "still names them, in all three missions (read from the Experimental "
        "server's files, 2026 Q4); measured on a stock 1.30.164014 Chernarus "
        "boot (Experimental, 2026 Q4)",
        note=(
            "Bohemia's 1.30 missions put the flare gun and its four rounds on the "
            "ignore list and still list them in a random preset, so every stock "
            "1.30 server logs these. Nothing to fix."
        ),
    ),
    Baseline(
        rule="script_error",
        terrains=("chernarusplus",),
        versions=("1.30",),
        match=_is_stock_130_leak,
        source="measured on two stops of a stock 1.30.164014 Chernarus server "
        "(Experimental, 2026 Q4, plans/66): both logged it while destroying "
        "the game, then hung until systemd killed the server 90 s later",
        note=(
            "Bohemia's 1.30 scripts leak this at every shutdown, with no mods "
            "loaded. On Linux the server then waits for a debugger, so stopping "
            "it takes until the service's stop timeout (90 s) and the unit ends "
            "as failed. The world is saved before that. Nothing to fix on your "
            "side."
        ),
    ),
    # DayZ 1.30 stable (1.30.164078), read off a stock boot of each of the four
    # maps in 2026 Q4, each checked against that map's shipped 1.30 files.
    # The Badlands (nasdara) entries are its first stock boot.
    Baseline(
        rule="ce_type_missing",
        subjects=(
            "CeramicPot_Black", "CeramicPot_Gray", "CeramicPot_Natural", "CeramicPot_White",
            "Glock_Tan", "ZmbM_takiCitizenUrbanB_BlueBright",
        ),
        terrains=("chernarusplus", "enoch", "sakhal", "nasdara"),
        versions=("1.30",),
        source="stock 1.30 db/types.xml of all four maps lists them; logged by a "
        "stock 1.30.164078 boot of each map (2026 Q4)",
        note=(
            "Bohemia's 1.30 types.xml lists these and the 1.30 server does not "
            "define the classes, so every stock 1.30 server rejects them on every "
            "boot. Nothing to fix."
        ),
    ),
    Baseline(
        rule="ce_type_missing",
        subjects=("Static_FrozenScientist_DE",),
        terrains=("sakhal", "nasdara"),
        versions=("1.30",),
        source="stock 1.30 db/types.xml of Sakhal and Badlands; logged by their "
        "stock 1.30.164078 boots (2026 Q4)",
        note=(
            "The Chernarus and Livonia entry, which Bohemia's 1.30 types.xml now "
            "carries on Sakhal and Badlands as well. Rejected on every boot; "
            "leave it alone."
        ),
    ),
    Baseline(
        rule="ce_type_missing",
        subjects=(
            "Land_House_1M3_Dam3", "Land_House_1S1_Dam1", "Land_House_1S1_Dam2",
            "Land_House_2M1_Dam3", "Water_Well_N1",
        ),
        terrains=("nasdara",),
        versions=("1.30",),
        source="stock Badlands db/types.xml; logged by a stock 1.30.164078 "
        "Badlands boot (2026 Q4)",
        note=(
            "Building classes Bohemia's Badlands types.xml lists and the server "
            "does not define. Every stock Badlands server rejects them."
        ),
    ),
    Baseline(
        rule="ce_type_not_spawnable",
        subjects=("ChristmasTree",),
        terrains=("enoch", "sakhal", "nasdara"),
        versions=("1.30",),
        source="stock 1.30 db/types.xml of Livonia, Sakhal and Badlands; logged by "
        "their stock 1.30.164078 boots (2026 Q4)",
        note=(
            "Chernarus' seasonal helper class, which Bohemia's 1.30 types.xml now "
            "lists on every map with a non-public scope. Leave it alone."
        ),
    ),
    Baseline(
        rule="ce_type_not_spawnable",
        subjects=(
            "Land_House_1M2_Dam1", "Land_House_1M3_Dam1", "Land_House_1M4_Dam1",
            "Land_House_1M4_Dam3", "Land_House_2M1_Dam1", "Land_House_2M2_Dam1",
            "Land_House_2M2_Dam3", "Land_House_2S1_Dam1", "Land_House_2S1_Dam2",
            "Land_House_2S1_Dam4", "Land_House_3S1_Dam1", "Land_House_3S1_Dam2",
            "Land_House_3S2_Dam1", "Land_House_3S2_Dam3",
        ),
        terrains=("nasdara",),
        versions=("1.30",),
        source="stock Badlands db/types.xml; logged by a stock 1.30.164078 "
        "Badlands boot (2026 Q4)",
        note=(
            "Damaged-house classes Bohemia's Badlands types.xml lists with a "
            "non-public scope. Every stock Badlands server rejects them."
        ),
    ),
    Baseline(
        rule="ce_xml_parse_errors",
        terrains=("nasdara",),
        versions=("1.30",),
        count=3,
        source="measured on a stock 1.30.164078 Badlands boot (2026 Q4)",
        note=(
            "Stock Badlands prints exactly this many on every boot, from "
            "Bohemia's own mission XML. A number above 3 is the one worth chasing."
        ),
    ),
    Baseline(
        rule="ce_groups_no_points",
        terrains=("nasdara",),
        count=18,
        source="measured on a stock 1.30.164078 Badlands boot (2026 Q4); the "
        "derivation from mapgroupproto.xml gives the same 18",
        note="Eighteen leftover groups in Bohemia's own Badlands mapgroupproto.xml.",
    ),
    Baseline(
        rule="ce_groups_wrong_points",
        terrains=("nasdara",),
        versions=("1.30",),
        count=53,
        source="measured on a stock 1.30.164078 Badlands boot (2026 Q4)",
        note=(
            "Stock Badlands prints exactly 53 on every boot, for groups in "
            "Bohemia's own mapgroupproto.xml. More than 53 is worth a look."
        ),
    ),
    Baseline(
        rule="ce_event_child_missing",
        subjects=("ZmbM_takiCitizenUrbanB_BlueBright",),
        terrains=("nasdara",),
        versions=("1.30",),
        source="stock Badlands db/events.xml, event InfectedCity; logged by a "
        "stock 1.30.164078 Badlands boot (2026 Q4)",
        note=(
            "Bohemia's Badlands InfectedCity event names an infected class the "
            "1.30 server does not define. The event spawns the others."
        ),
    ),
    Baseline(
        rule="ce_vehicles_short",
        subjects=(
            "StaticPoliceCar", "VehicleCivilianSedan", "VehicleHatchback02",
            "VehicleOffroadHatchback", "VehicleSedan02",
        ),
        terrains=("nasdara",),
        versions=("1.30",),
        source="logged by two stock 1.30.164078 Badlands boots (2026 Q4): "
        "the first with an empty storage_1 (Init), the second with the world "
        "the first one saved (Respawning)",
        note=(
            "Stock Badlands asks these events for more vehicles than it can "
            "place. Bohemia's data; nothing to fix."
        ),
    ),
    Baseline(
        rule="ce_no_player_spawns",
        terrains=("nasdara",),
        versions=("1.30",),
        source="logged three times by every stock 1.30.164078 Badlands boot; every "
        "group in its stock cfgplayerspawnpoints.xml has lifetime=\"0\". A new "
        "character joining that server (2026 Q4) spawned at 8906.7, 12183.7, "
        "50 m from a <pos> in the file's fresh section, not at 0,0,0",
        note=(
            "Stock Badlands logs this on every boot, yet a new character joining a "
            "stock Badlands server spawned at one of the file's own spawn "
            "positions, not in the corner of the map. Bohemia's file; nothing to "
            "fix. If new players ever do land at the corner, check the <group> "
            "entries in cfgplayerspawnpoints.xml."
        ),
    ),
    Baseline(
        rule="missing_mod_model",
        subjects=("crates_large 1.p3d", "crates_large 2.p3d"),
        terrains=("nasdara",),
        versions=("1.30",),
        source="logged by a stock 1.30.164078 Badlands boot (2026 Q4); no "
        "mission file names them, so the terrain asks for them",
        note="The Badlands terrain asks for two models the game does not ship.",
    ),
    Baseline(
        rule="missing_mod_model",
        subjects=(
            "dayz\\trunk\\data\\dz\\structures_sakhal\\military\\storage\\proxy\\"
            "warhead_storage_door_switch.p3d",
        ),
        terrains=("sakhal",),
        versions=("1.30",),
        source="logged by a stock 1.30.164078 Sakhal boot (2026 Q4)",
        note="A Sakhal model path in Bohemia's own data that the game does not ship.",
    ),
    Baseline(
        rule="ce_no_shutdown_message",
        source="observed on stock 1.29 runs that also logged "
        "'--- Termination successfully completed ---'",
        note=(
            "The offline database prints this whether or not the last run ended "
            "cleanly, including on a first boot where there was no previous run "
            "at all — so on its own it is not evidence of a crash. A crash dump "
            "beside this log is; Zedstead reports one as an error when it finds it, "
            "and promotes this line back to a warning at the same time."
        ),
    ),
    Baseline(
        rule="anim_load_error",
        match=_is_vanilla_terrain_anim,
        source="base-game data ships every terrain's animation configs; stock "
        "Livonia and Sakhal boots both log sakhal/Anims before loading a mission, "
        "and a stock 1.30.164014 Chernarus boot (Experimental, 2026 Q4) logs "
        "nasdara/Anims as well",
        note=(
            "The engine loads the animation configs of every terrain in the game "
            "data at startup, before any mission, and logs the ones it cannot "
            "read — stock Sakhal logs its own. Only a path naming a mod's "
            "terrain is worth acting on."
        ),
    ),
    # A fresh stock 1.30.164289 install, booted on each of the four maps with
    # no mods and no changes (night test, 2026 Q4). The 1.30.164078 stock
    # fixtures print the resource line too.
    Baseline(
        rule="engine_error",
        match=_is_stock_resource,
        source="every stock 1.30 boot of the four maps asks for "
        "DZ/anims/anm/player/reloads/LeeEnfield/w_enfield_states.anm, which the "
        "base game does not ship, and logs this",
        note=(
            "The base game asks for a file under DZ/ that it does not ship. Every "
            "stock server logs this. Nothing to fix. The same line for a mod's "
            "file stays yours."
        ),
    ),
    Baseline(
        rule="engine_error",
        match=_is_stock_navmesh,
        terrains=("enoch",),
        versions=("1.30",),
        source="a stock 1.30.164289 Livonia boot logs it while loading the "
        "underground stairs (Land_Underground_Stairs_Exit); Chernarus, Sakhal "
        "and Badlands do not",
        note="Stock Livonia logs this while it loads its bunker models. Nothing to fix.",
    ),
)

_BASELINES_BY_RULE: dict[str, list[Baseline]] = {}
for _b in VANILLA_BASELINE:
    _BASELINES_BY_RULE.setdefault(_b.rule, []).append(_b)
del _b


def _version_matches(baseline: Baseline, version: str) -> bool:
    if not baseline.versions or not version:
        return True
    return any(version == v or version.startswith(v + ".") for v in baseline.versions)


def _baselines_for(rule_id: str, terrain: str, version: str = "") -> list[Baseline]:
    """Baseline entries that apply to this rule on this terrain and version.

    An unknown terrain (a community map, or a log that never reached "Loading
    landscape") matches only the map-independent entries — guessing that a
    modded mission ships Bohemia's quirks would hide the admin's real ones.
    """
    return [
        b
        for b in _BASELINES_BY_RULE.get(rule_id, ())
        if (not b.terrains or terrain in b.terrains) and _version_matches(b, version)
    ]


# ── Mod baseline (plans/48 D6) ───────────────────────────────────────────
#
# The vanilla baseline's reasoning, applied to a mod zsm knows well enough to
# pre-configure: a class the mod's *own* types.xml lists and the mod's *own*
# build rejects is printed by every server running that build, and the admin
# did nothing to cause it. Each entry names the Workshop build it was measured
# on, because a mod update can fix it, and then the entry is simply wrong.


@dataclass(frozen=True)
class ModBaseline:
    """Findings a loaded mod causes by itself, named by subject.

    Applies only when *mod* was loaded by the run. Where the installed mission
    can say who registers each class, *owners* says who must be listing it:

    - ``"mod"``: the mod's own economy file, alone. The same class in the
      admin's own file stays theirs to read.
    - ``"stock"``: the mission's ``db/types.xml`` alone, for a vanilla class
      the mod stops defining.
    - ``"any"``: no check, for a subject that is not a types entry.

    *first_boot* limits the entry to the run that loaded *mod* for the first
    time: the one whose previous run's command line did not name it. Without a
    previous run to compare against, the entry does not apply, so the finding
    stays the admin's. *every_subject* claims every subject of the hit (with
    *subjects* empty), for a mod that changes what a whole class family saves;
    an entry naming its subjects still wins over it for those names.
    """

    rule: str
    mod: str  # the mod folder as it appears in -mod=
    subjects: tuple[str, ...]
    note: str  # replaces the rule's `how` on the mod half
    source: str  # the build and the log it was measured on
    owners: str = "mod"
    first_boot: bool = False
    every_subject: bool = False


MOD_BASELINE: tuple[ModBaseline, ...] = (
    ModBaseline(
        rule="ce_type_missing",
        mod="@RadZone",
        subjects=("DecontaminationShowerStaticUnlimited",),
        source="@RadZone ServerFiles/types.xml, Workshop build of 2025-09-28; "
        "rejected on a 1.29.163709 boot (plans/48 Step 0)",
        note=(
            "RadZone's own types.xml lists this class, and the Workshop build of "
            "2025-09-28 does not define it, although its Readme names it. Every "
            "server running that build rejects it on every boot, so nothing spawns "
            "under that name; the rest of the file loads. Leave it, or turn the "
            "entry off on the Custom config tab."
        ),
    ),
    ModBaseline(
        rule="ce_type_not_spawnable",
        mod="@RadZone",
        subjects=("NBCBootsBase", "GP5GasMask_Filter"),
        source="@RadZone ServerFiles/types.xml, Workshop build of 2025-09-28; "
        "rejected on a 1.29.163709 boot (plans/48 Step 0)",
        note=(
            "RadZone's own types.xml lists these with a config scope that is not "
            "public, so every server running the 2025-09-28 build refuses them on "
            "every boot. The mod's other items spawn normally; nothing to fix."
        ),
    ),
    ModBaseline(
        rule="ce_type_missing",
        mod="@ToxicZone",
        subjects=("GasMask",),
        owners="stock",
        source="Workshop build of 2022-02-18 with the stock Chernarus db/types.xml; "
        "rejected on 1.29.163709 boots (plans/48 Step 0, ToxicZone)",
        note=(
            "With ToxicZone's 2022-02-18 build loaded, the vanilla GasMask class "
            "no longer exists, while the stock types.xml still lists it. Every boot "
            "drops that one entry and nothing spawns under the name; ToxicZone's "
            "own masks are unaffected. To quiet it, remove GasMask from db/types.xml "
            "in the Types editor, and put it back if you uninstall ToxicZone."
        ),
    ),
    ModBaseline(
        rule="storage_scripted_vars",
        mod="@ToxicZone",
        subjects=("GP5GasMask", "AirborneMask"),
        owners="any",
        source="Workshop build of 2022-02-18; on the first 1.29.163709 boot after "
        "installing it, not on the next (plans/48 Step 0, ToxicZone)",
        note=(
            "ToxicZone changes what these masks save. Masks saved before it was "
            "added (or before it was removed) cannot be read and are dropped, once, "
            "on the next boot; the boot after that is clean. If it shows again on "
            "a later boot, something else changed."
        ),
    ),
    ModBaseline(
        rule="storage_scripted_vars",
        mod="@TraderPlus",
        subjects=(),
        every_subject=True,
        first_boot=True,
        owners="any",
        source="Workshop build with TraderPlus.pbo 25061910 (installed in 2026 Q3); 83 "
        "vehicles, vanilla and Expansion, dropped on the first 1.29.163709 boot with "
        "it and none on the seven boots after (plans/48 D, in game)",
        note=(
            "TraderPlus's car lock changes what every vehicle saves, so on the first "
            "boot with it the engine cannot read vehicles saved before and drops them, "
            "once; the mod's Workshop page warns about this. Everything the engine "
            "dropped on this boot is listed here, so an item that is not a vehicle was "
            "lost for another reason. A backup from before the install, restored "
            "without TraderPlus, brings the vehicles back."
        ),
    ),
)

#: The PBO prefixes that identify a ``MOD_BASELINE`` mod under any folder
#: name. An adopted server's copy may be ``@MyRadZone`` (plans/78 step 9), and
#: by folder name alone its stock rejects read as the admin's. A mod with no
#: entry here is matched by name only; add one only from its real PBOs.
MOD_PREFIXES: dict[str, tuple[str, ...]] = {
    # RadZone.pbo and RadZone_Data.pbo, Workshop 2294323732, read in 2026 Q4.
    "@RadZone": ("RadZone", "RadZone_Data"),
}


def mod_aliases(config) -> dict[str, str]:
    """``analyze_rpt``'s *mod_aliases*: each ``@`` folder on the server whose
    PBOs carry every prefix of a ``MOD_PREFIXES`` mod under another name,
    lowercased, to that mod's name. Reads PBO headers through the prefix
    cache; a folder it cannot read is left out."""
    from app.modmgr.mod_conflict import mod_pbo_prefixes

    known = {name.lower() for name in MOD_PREFIXES}
    try:
        folders = [
            p.name for p in config.server_dir_path.glob("@*")
            if p.is_dir() and p.name.lower() not in known
        ]
        found = mod_pbo_prefixes(config.server_dir_path, folders) if folders else {}
    except OSError:
        return {}
    out: dict[str, str] = {}
    for folder, prefixes in found.items():
        have = {p.lower() for p in prefixes}
        for mod, wanted in MOD_PREFIXES.items():
            if {w.lower() for w in wanted} <= have:
                out[folder.lower()] = mod
    return out


def _aliased_mission(
    mission: MissionBaseline | None, aliases: dict[str, str],
) -> MissionBaseline | None:
    """*mission* with each renamed copy's types owner named as the mod it is."""
    if mission is None or not aliases or mission.type_sources is None:
        return mission
    from dataclasses import replace

    return replace(mission, type_sources={
        k: tuple(aliases.get(o.lower(), o) for o in v) for k, v in mission.type_sources.items()
    })


_MOD_BASELINES_BY_RULE: dict[str, list[ModBaseline]] = {}
for _m in MOD_BASELINE:
    _MOD_BASELINES_BY_RULE.setdefault(_m.rule, []).append(_m)
del _m


def _mod_quirks(
    hit: _Hit,
    mission: MissionBaseline | None,
    loaded_mods: set[str],
    new_mods: set[str] | None = None,
) -> dict[str, ModBaseline]:
    """The hit's subjects a loaded mod's own files explain, each with its entry.

    *new_mods* are the mods this run loaded and the previous run did not; a
    ``first_boot`` entry applies only to those.
    """
    entries = _MOD_BASELINES_BY_RULE.get(hit.rule.id)
    if not entries:
        return {}
    sources = mission.type_sources if mission is not None else None
    out: dict[str, ModBaseline] = {}
    # Entries that name their subjects first, so they keep those names.
    for b in sorted(entries, key=lambda e: e.every_subject):
        if b.mod.lower() not in loaded_mods:
            continue
        if b.first_boot and b.mod.lower() not in (new_mods or set()):
            continue
        # subject_counts, not subjects: the latter stops at MAX_SUBJECTS + 1
        # names, and the finding's count is the sum over the names it gets.
        for name in hit.subject_counts if b.every_subject else b.subjects:
            if name not in hit.subject_counts or name in out:
                continue
            if b.owners != "any" and sources is not None:
                allowed = {MISSION} if b.owners == "stock" else {b.mod}
                if set(sources.get(name, ())) - allowed:
                    continue  # listed somewhere else too: that copy is the admin's
            out[name] = b
    return out


# ── Facts ────────────────────────────────────────────────────────────────

# The engine pads a one-digit hour with a space: " 8:41:33 Player connect
# enabled". A two-digit-only pattern read every boot before 10:00 as never
# joinable and left the time inside each finding's subject (2026 Q3).
_TS = re.compile(r"^\s*(\d{1,2}):(\d{2}):(\d{2})\s")
_TS_PREFIX = re.compile(r"^\s*\d{1,2}:\d{2}:\d{2}\s+")
_RE_VERSION = re.compile(r"^Version\s+(.+?)\s*$")
_RE_EXE_TS = re.compile(r"^Exe timestamp:\s*(.+?)\s*$")
_RE_CUR_TIME = re.compile(r"^Current time:\s*(.+?)\s*$")
_RE_CMDLINE = re.compile(r"^==\s+(\S*DayZServer\S*\s+.*)$")
# 1.29 says "to RV filesystem", 1.30 "to filesystem" (measured in 2026 Q4).
_RE_MISSION = re.compile(r"mpmissions[/\\]([^'\"\s]+)['\"]?\s+to (?:RV )?filesystem under name mission")
_RE_LANDSCAPE = re.compile(r"Loading landscape '([^']+)'")
_RE_STORAGE = re.compile(r"Selected storage directory:\s*(.+?)\s*$")
_RE_HOSTNAME = re.compile(r"Hostname of server:\s*\"(.*)\"")
_RE_BANKS = re.compile(r"Added (\d+) RV banks")
_RE_STEAM_OK = re.compile(r"SUCCESS: SteamGameServer_Init\(([^)]*)\)")
_RE_LOOT = re.compile(r"Nominal:\s*(\d+),\s*Total in Map:\s*(\d+)")
_RE_STORAGE_TOOK = re.compile(r"Storage load took:\s*([\d.]+) seconds")
_RE_LANDSCAPE_TOOK = re.compile(r"Loading landscape took:\s*([\d.]+) ms")
# 1.29: "Load  Events:[62]"; 1.30: "[CE][DynEvent] :: 51 types".
_RE_EVENTS = re.compile(r"Load\s+Events:\[(\d+)\]|\[CE\]\[DynEvent\] :: (\d+) types")
_RE_ENTITIES = re.compile(r"\bentity:\s*(\d+), building:\s*(\d+)")
_RE_HIVE = re.compile(r"\[CE\]\[Hive\] :: Initializing (\w+)")
# The RPT logs the command line unquoted, so a mod folder with a space in its
# name ("@Dabs Framework") ends only where the next " -flag" begins. \S* stopped
# at the space and summed up a 21-mod run as "@CF, @Dabs" (2026 Q3).
_RE_MOD_ARG = re.compile(r"-mod=(.*?)(?=\s+-\w|\s*$)")
# Case-sensitive "-mod=" above can never match inside "-serverMod=" (the
# character before "mod" is "r"), so the two never read each other's list.
_RE_SERVER_MOD_ARG = re.compile(r"-serverMod=(.*?)(?=\s+-\w|\s*$)", re.IGNORECASE)
_RE_PORT_ARG = re.compile(r"-port=(\d+)")
_RE_CPU_ARG = re.compile(r"-cpuCount=(\d+)")
# Mod PBOs live under ``<server>/@Mod/addons/`` — the engine prints the full
# path as it mounts each bank, which is the only place the log states which
# mods the run *really* loaded (``-mod=`` states only what it was asked for).
_RE_MOD_ADDON = re.compile(r"[/\\](@[^/\\]+)[/\\]")

_READY_MARKER = "Player connect enabled"
_CLEAN_SHUTDOWN = "Termination successfully completed"
_DESTROYING = "Destroying game"


@dataclass
class _Hit:
    rule: Rule
    count: int = 0
    stated: int | None = None
    subjects: list[str] = field(default_factory=list)
    #: Per-subject occurrence counts, so a hit can be split into a vanilla half
    #: and an admin half with an honest count on each.
    subject_counts: dict[str, int] = field(default_factory=dict)
    samples: list[dict] = field(default_factory=list)
    first_line: int = 0


def _seconds(match: re.Match | None) -> int | None:
    if not match:
        return None
    h, m, s = (int(g) for g in match.groups())
    return h * 3600 + m * 60 + s


# The engine logs a missing resource as a bare "RESOURCES (E): Failed to open"
# and names the file only on the line before it ("GetResourceObject @"{id}/path"").
# The loop appends that path, so the finding names the file and a file under
# Bohemia's DZ/ can be told from a mod's (_is_stock_resource).
_RE_RESOURCE_REQUEST = re.compile(r'GetResourceObject @"(?:\{[0-9A-Fa-f]+\})?([^"]+)"')
_BARE_RESOURCE_FAILURE = re.compile(r"RESOURCES\s+\(E\):\s*Failed to open\s*$")


def _with_resource_path(line: str, previous: str) -> str:
    """*line* with the file *previous* asked for appended, when *line* is the
    bare resource failure and *previous* names one; else *line* unchanged."""
    if not _BARE_RESOURCE_FAILURE.search(line):
        return line
    m = _RE_RESOURCE_REQUEST.search(previous)
    return f"{line.rstrip()} ({m.group(1)})" if m else line


def _is_vanilla_model(path: str) -> bool:
    p = path.strip().strip("'\"").lower()
    return p in _VANILLA_MISSING_MODELS or p.startswith(_VANILLA_MODEL_ROOTS)


def analyze_rpt(
    path: Path,
    *,
    max_lines: int = MAX_LINES,
    baseline_for: Callable[[str], MissionBaseline | None] | None = None,
    mod_file_exists: Callable[[str], bool] | None = None,
    previous_mods: set[str] | None = None,
    mod_aliases: dict[str, str] | None = None,
) -> dict:
    """Parse one .RPT into ``{log, run, stats, findings, noise, summary}``.

    Pure: no config, no filesystem beyond *path*, so it can be pointed at any
    log file (a copy, a fixture, an older run) and tested directly.

    *baseline_for* maps the mission the log loaded (``run["mission"]``) to what
    that mission's own files make the server complain about
    (``app/ops/mission_baseline.py``). The caller owns the lookup — this
    function never goes looking for a mission — and without one the
    per-terrain ``VANILLA_BASELINE`` table is all there is.

    *mod_file_exists* answers "does this ``@Mod/...`` path exist on disk?" and
    turns the engine's per-mod animation-config probe into noise when the mod
    ships no such file. Without it every probe stays a warning.

    *previous_mods* are the mods the run before this one loaded
    (``launched_mods``), lowercased; a ``first_boot`` entry of
    ``MOD_BASELINE`` needs them. None means "not known".

    *mod_aliases* (``mod_aliases(config)``) names a renamed copy of a
    ``MOD_BASELINE`` mod as that mod, in the run's mods, the previous run's and
    the mission's types owners.
    """
    run: dict = {
        "version": None,
        "exe_timestamp": None,
        "started_at": None,
        "command_line": None,
        "mod_args": [],
        "server_mod_args": [],
        "mod_addons": [],
        "port": None,
        "cpu_count": None,
        "mission": None,
        "terrain": None,
        "storage_dir": None,
        "hostname": None,
        "addons_loaded": None,
        "steam_registered": False,
        "steam_ports": None,
        "hive": None,
        "ready_seconds": None,
        "outcome": "unknown",
        "outcome_detail": "",
    }
    stats: dict = {
        "loot_nominal": None,
        "loot_in_map": None,
        "dynamic_events": None,
        "entities": None,
        "storage_load_seconds": None,
        "landscape_load_ms": None,
    }

    hits: dict[str, _Hit] = {}
    noise_counts: dict[str, int] = {}
    first_ts: int | None = None
    last_ts: int | None = None
    ready_ts: int | None = None
    absorb: tuple[str, int] | None = None  # (literal, last line it may be on)
    previous = ""  # the line before, for _with_resource_path
    repeats: dict[str, list[int]] = {}  # unclaimed text -> [count, first line]
    total = 0
    skipped = {"lines": 0}

    try:
        handle = open(path, "rb")
    except OSError:
        return _empty_report(path)

    with handle as f:
        for lineno, raw in _head_and_tail(f, max_lines, skipped):
            total = lineno
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line.strip():
                continue

            ts = _seconds(_TS.match(line))
            if ts is not None:
                if first_ts is None:
                    first_ts = ts
                last_ts = ts

            line, previous = _with_resource_path(line, previous), line
            _collect_facts(line, run, stats)
            if _READY_MARKER in line and ready_ts is None:
                ready_ts = ts

            if _match_noise(line, noise_counts):
                continue
            if _is_mod_anim_probe(line, mod_file_exists):
                noise_counts[MOD_ANIM_PROBE] = noise_counts.get(MOD_ANIM_PROBE, 0) + 1
                continue
            if absorb is not None and lineno <= absorb[1] and absorb[0] in line:
                absorb = None
                continue
            rule = _match_rules(line, lineno, hits)
            # A line that is itself the follow-up message absorbs nothing: a
            # second "NO VALID SPAWNS" right after a lone one is its own event.
            if rule is not None and rule.absorbs_next and rule.absorbs_next not in line:
                absorb = (rule.absorbs_next, lineno + _ABSORB_WINDOW)
            if rule is None:
                _count_repeat(repeats, _TS_PREFIX.sub("", line).strip(), lineno)

    flood = _flood_hit(repeats)
    if flood is not None:
        hits[LOG_FLOOD.id] = flood

    if first_ts is not None and ready_ts is not None:
        # Midnight rollover: a server started at 23:59 is joinable "before" it
        # started by clock arithmetic alone.
        run["ready_seconds"] = ready_ts - first_ts + (86400 if ready_ts < first_ts else 0)

    run.update(_outcome(path, run, ready_ts is not None))
    mission = None
    if baseline_for is not None and run.get("mission"):
        mission = baseline_for(run["mission"])
    aliases = mod_aliases or {}
    mission = _aliased_mission(mission, aliases)
    loaded = {aliases.get(m.lower(), m).lower() for m in (*run["mod_args"], *run["mod_addons"])}
    if previous_mods is not None:
        previous_mods = {aliases.get(m, m).lower() for m in previous_mods}
    new_mods = loaded - previous_mods if previous_mods is not None else set()
    findings = _sorted_findings(
        hits, run.get("terrain") or "", mission, loaded, new_mods, version=run.get("version") or "",
    )
    return {
        "log": {
            "name": path.name,
            "path": str(path),
            "lines": total,
            "truncated": skipped["lines"] > 0,
            # Lines between the first max_lines and the last TAIL_LINES that
            # were not read (``_head_and_tail``).
            "skipped_lines": skipped["lines"],
            "size_bytes": _size(path),
            "modified": _mtime(path),
        },
        "run": run,
        "stats": stats,
        "findings": findings,
        "noise": {
            "suppressed": sum(noise_counts.values()),
            "categories": [
                {"label": k, "count": v}
                for k, v in sorted(noise_counts.items(), key=lambda kv: -kv[1])
            ],
        },
        "summary": summarise(findings),
    }


#: The central economy's two "this class is dropped" messages.
REJECTION_RULES = ("ce_type_missing", "ce_type_not_spawnable")


def rejected_classes(path: Path, *, max_lines: int = MAX_LINES) -> tuple[dict[str, str], list[str] | None]:
    """The classes one run's central economy dropped, and the mods it loaded.

    ``({classname: rule id}, mod folders from -mod= and -serverMod=)``; the
    mod list is None when the log holds no command line. A narrow pass for a
    caller that must not offer a class the server throws away (plans/48 D);
    the full report is ``analyze_rpt``.
    """
    rules = [r for r in RULES if r.id in REJECTION_RULES]
    rejected: dict[str, str] = {}
    mods: list[str] | None = None
    try:
        handle = open(path, "r", encoding="utf-8", errors="replace")
    except OSError:
        return rejected, mods
    with handle as f:
        for lineno, line in enumerate(f, 1):
            if lineno > max_lines:
                break
            if mods is None:
                mods = _command_line_mods(line)
            for rule in rules:
                if rule.hint in line:
                    m = re.search(rule.pattern, line)
                    if m:
                        rejected.setdefault(m.group(rule.subject).strip(), rule.id)
                    break
    return rejected, mods


def _command_line(line: str) -> str | None:
    """The command, if *line* is the RPT's logged command line, else None."""
    if not line.startswith("== ") or "DayZServer" not in line:
        return None
    m = _RE_CMDLINE.match(line.rstrip("\n"))
    return m.group(1).strip() if m else None


def _command_line_mods(line: str) -> list[str] | None:
    """The mod folders (-mod= then -serverMod=) if *line* is the RPT's logged
    command line, else None."""
    cmd = _command_line(line)
    if cmd is None:
        return None
    launched = logged_mod_args(cmd)
    return launched.get("mod_args", []) + launched.get("server_mod_args", [])


#: The command line sits in an RPT's first few dozen lines.
_COMMAND_LINE_WITHIN = 300


def logged_command_line(path: Path) -> str | None:
    """The command line one run was started with, as its RPT logged it (the
    quotes dropped), or None when the log holds none."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for lineno, line in enumerate(f, 1):
                if lineno > _COMMAND_LINE_WITHIN:
                    break
                cmd = _command_line(line)
                if cmd is not None:
                    return cmd
    except OSError:
        pass
    return None


def launched_mod_names(path: Path) -> list[str] | None:
    """The mod folders one run loaded, as its command line spells them, or
    None when its log holds no command line (a run cut short before it, an
    unreadable file)."""
    cmd = logged_command_line(path)
    if cmd is None:
        return None
    launched = logged_mod_args(cmd)
    return launched.get("mod_args", []) + launched.get("server_mod_args", [])


def launched_mods(path: Path) -> set[str] | None:
    """`launched_mod_names`, lowercased."""
    mods = launched_mod_names(path)
    return None if mods is None else {m.lower() for m in mods}


def find_ready(path: Path, offset: int = 0) -> tuple[bool, int]:
    """Has the run logging to *path* become joinable? Reads from byte *offset*.

    Returns ``(ready, next_offset)``, for a log the server is still writing
    (plans/73's boot watch): each call reads only what was added since the
    last one, and only whole lines are consumed, so a marker split across two
    writes is found on the next call.
    """
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return False, offset
    end = data.rfind(b"\n")
    if end == -1:
        return False, offset
    return _READY_MARKER.encode() in data[: end + 1], offset + end + 1


def summarise(findings: list[dict]) -> dict:
    """Severity counts for the headline, plus how many were the stock mission's
    (``vanilla``) and a pre-configured mod's own (``mod``).

    The severity counts deliberately exclude both: a fresh install reading
    "2 errors, 6 warnings" when every one of them came out of Bohemia's own
    mission folder is the thing this whole layer exists to stop. They are still
    reported — under their own keys, and in the findings list.
    """
    yours = [f for f in findings if f.get("origin", ORIGIN_YOURS) == ORIGIN_YOURS]
    summary = {sev: sum(1 for f in yours if f["severity"] == sev) for sev in SEVERITIES}
    summary["vanilla"] = sum(1 for f in findings if f.get("origin") == ORIGIN_VANILLA)
    summary["mod"] = sum(1 for f in findings if f.get("origin") == ORIGIN_MOD)
    return summary


def _empty_report(path: Path) -> dict:
    return {
        "log": {
            "name": path.name,
            "path": str(path),
            "lines": 0,
            "truncated": False,
            "size_bytes": None,
            "modified": None,
        },
        "run": {"outcome": "unreadable", "outcome_detail": "Log file could not be read."},
        "stats": {},
        "findings": [],
        "noise": {"suppressed": 0, "categories": []},
        "summary": summarise([]),
    }


def _size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _mtime(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        return None


def logged_mod_args(cmd: str) -> dict[str, list[str]]:
    """``mod_args`` / ``server_mod_args`` from a logged command line, where set.

    For the RPT's form only, where the quotes are gone and a name may hold a
    space (``-mod=@CF;@Dabs Framework -BEpath=…``); a unit's or a script's
    command, quoted, is read by `parse_mods` / `parse_server_mods`."""
    mods: dict[str, list[str]] = {}
    for key, pattern in (("mod_args", _RE_MOD_ARG), ("server_mod_args", _RE_SERVER_MOD_ARG)):
        found = pattern.search(cmd)
        if found:
            mods[key] = [
                name for name in (p.strip().strip('"') for p in found.group(1).split(";")) if name
            ]
    return mods


def _collect_facts(line: str, run: dict, stats: dict) -> None:
    """Header/summary values. Cheap membership tests guard every regex."""
    if run["version"] is None and line.startswith("Version "):
        m = _RE_VERSION.match(line)
        if m:
            run["version"] = m.group(1)
    if line.startswith("Exe timestamp:"):
        m = _RE_EXE_TS.match(line)
        if m:
            run["exe_timestamp"] = m.group(1)
    if line.startswith("Current time:"):
        m = _RE_CUR_TIME.match(line)
        if m:
            run["started_at"] = m.group(1)
    if run["command_line"] is None and line.startswith("== ") and "DayZServer" in line:
        m = _RE_CMDLINE.match(line)
        if m:
            cmd = m.group(1).strip()
            run["command_line"] = cmd
            run.update(logged_mod_args(cmd))
            port = _RE_PORT_ARG.search(cmd)
            if port:
                run["port"] = int(port.group(1))
            cpu = _RE_CPU_ARG.search(cmd)
            if cpu:
                run["cpu_count"] = int(cpu.group(1))
    if ".pbo" in line or ".ebo" in line:
        m = _RE_MOD_ADDON.search(line)
        if m and m.group(1) not in run["mod_addons"]:
            run["mod_addons"].append(m.group(1))
    if run["mission"] is None and "under name mission" in line:
        m = _RE_MISSION.search(line)
        if m:
            run["mission"] = m.group(1).rstrip("'\"")
    if run["terrain"] is None and "Loading landscape" in line:
        m = _RE_LANDSCAPE.search(line)
        if m:
            run["terrain"] = m.group(1)
    if run["storage_dir"] is None and "Selected storage directory" in line:
        m = _RE_STORAGE.search(line)
        if m:
            run["storage_dir"] = m.group(1)
    if run["hostname"] is None and "Hostname of server" in line:
        m = _RE_HOSTNAME.search(line)
        if m:
            run["hostname"] = m.group(1)
    if run["addons_loaded"] is None and "RV banks" in line:
        m = _RE_BANKS.search(line)
        if m:
            run["addons_loaded"] = int(m.group(1))
    if not run["steam_registered"] and "SteamGameServer_Init" in line:
        m = _RE_STEAM_OK.search(line)
        if m:
            run["steam_registered"] = True
            run["steam_ports"] = m.group(1)
    if run["hive"] is None and "[CE][Hive] :: Initializing" in line:
        m = _RE_HIVE.search(line)
        if m:
            run["hive"] = m.group(1)

    if stats.get("loot_nominal") is None and "Total in Map" in line:
        m = _RE_LOOT.search(line)
        if m:
            stats["loot_nominal"] = int(m.group(1))
            stats["loot_in_map"] = int(m.group(2))
    if stats.get("storage_load_seconds") is None and "Storage load took" in line:
        m = _RE_STORAGE_TOOK.search(line)
        if m:
            stats["storage_load_seconds"] = float(m.group(1))
    if stats.get("landscape_load_ms") is None and "Loading landscape took" in line:
        m = _RE_LANDSCAPE_TOOK.search(line)
        if m:
            stats["landscape_load_ms"] = round(float(m.group(1)))
    if stats.get("dynamic_events") is None and ("Load  Events:[" in line or "[DynEvent]" in line):
        m = _RE_EVENTS.search(line)
        if m:
            stats["dynamic_events"] = int(m.group(1) or m.group(2))
    if stats.get("entities") is None and "entity:" in line:
        m = _RE_ENTITIES.search(line)
        if m:
            stats["entities"] = int(m.group(1))


# The engine asks every loaded mod for ``@Mod/Anims/cfg/skeletons.anim.xml`` and
# logs ANIMATION (E) for each one that has none: 22 lines on a 22-mod install
# where no mod ships the file (2026 Q3). Only a mod that ships it and still
# fails is worth a warning, and telling the two apart takes the install, which
# is why this is a callback rather than a NOISE entry.
_RE_MOD_ANIM = re.compile(r"ANIMATION\s+\(E\):\s*Can't load\s+(@.*?)\s*$")
MOD_ANIM_PROBE = "Animation config a mod does not ship"


def _is_mod_anim_probe(line: str, file_exists: Callable[[str], bool] | None) -> bool:
    if file_exists is None or "ANIMATION (E)" not in line:
        return False
    m = _RE_MOD_ANIM.search(line)
    return bool(m) and not file_exists(m.group(1))


def _head_and_tail(f, max_lines: int, skipped: dict) -> Iterator[tuple[int, bytes]]:
    """Yield ``(line number, raw line)`` for the first *max_lines* lines of the
    binary file *f*, then for its last ``TAIL_LINES`` lines.

    The lines in between are counted, never parsed, into ``skipped["lines"]``,
    so the tail keeps its true line numbers.
    """
    lineno = 0
    for raw in f:
        lineno += 1
        if lineno > max_lines:
            break
        yield lineno, raw
    else:
        return
    start = f.tell() - len(raw)  # the first line past the head
    size = os.fstat(f.fileno()).st_size
    tail_from = max(start, size - _TAIL_BYTES)
    if tail_from > start:
        # Finish the line tail_from falls inside: one byte back, so a tail
        # that starts on a line boundary drops nothing.
        f.seek(tail_from - 1)
        f.readline()
    else:
        f.seek(start)
    middle = _count_newlines(f, start, f.tell())
    rest = f.readlines()
    tail = rest[-TAIL_LINES:]
    skipped["lines"] = middle + len(rest) - len(tail)
    for offset, raw in enumerate(tail):
        yield max_lines + 1 + skipped["lines"] + offset, raw


def _count_newlines(f, start: int, end: int) -> int:
    """Count the line ends in bytes ``start..end`` of *f*, leaving *f* at *end*."""
    f.seek(start)
    count = 0
    left = end - start
    while left > 0:
        chunk = f.read(min(_COUNT_CHUNK, left))
        if not chunk:
            break
        count += chunk.count(b"\n")
        left -= len(chunk)
    f.seek(end)
    return count


def _count_repeat(repeats: dict[str, list[int]], text: str, lineno: int) -> None:
    """Count one unclaimed line, tracking at most ``_FLOOD_MAX_KEYS`` distinct texts."""
    entry = repeats.get(text)
    if entry is not None:
        entry[0] += 1
    elif len(repeats) < _FLOOD_MAX_KEYS:
        repeats[text] = [1, lineno]


def _flood_hit(repeats: dict[str, list[int]]) -> _Hit | None:
    """The ``log_flood`` hit for every text repeated ``FLOOD_MIN_REPEATS`` times, or None."""
    flooded = sorted(
        ((text, n, first) for text, (n, first) in repeats.items() if n >= FLOOD_MIN_REPEATS),
        key=lambda t: -t[1],
    )
    if not flooded:
        return None
    hit = _Hit(rule=LOG_FLOOD, first_line=min(first for _, _, first in flooded))
    for text, n, first in flooded:
        hit.count += n
        hit.subjects.append(text)
        hit.subject_counts[text] = n
        if len(hit.samples) < MAX_SAMPLES:
            hit.samples.append({"line": first, "text": text})
    return hit


def is_stock_line(
    line: str,
    *,
    terrain: str = "",
    version: str = "",
    mod_file_exists: Callable[[str], bool] | None = None,
    previous: str = "",
) -> bool:
    """True for a line that is not the admin's doing, by this module's own rules.

    The Activity drawer's Key lines mutes these, so a vanilla server's
    harmless ``(E):`` lines do not show as red errors (app/web/rpt.py). Three
    kinds: ``NOISE``; a mod's animation probe for a file it does not ship; and
    a line whose finding the stock tables (``VANILLA_BASELINE``) file under
    *terrain* and *version* as stock. The report itself is unchanged: Doctor
    and Analysis still list that last kind, marked as stock (night test
    2026-11-23: a stock Chernarus boot left eight red lines in Key lines).
    *previous* is the line before, which names the file of a bare resource
    failure (``_with_resource_path``).
    """
    if _match_noise(line, {}) or _is_mod_anim_probe(line, mod_file_exists):
        return True
    line = _with_resource_path(line, previous)
    hits: dict[str, _Hit] = {}
    rule = _match_rules(line, 0, hits)
    if rule is None:
        return False
    findings = _split_by_table(hits[rule.id], terrain, version)
    return all(f["origin"] != ORIGIN_YOURS for f in findings)


def stock_line_checker(config) -> Callable[[str], bool]:
    """``is_stock_line`` bound to this install: the terrain serverDZ.cfg loads,
    the version the newest log names, and the mod files on disk. Call it on
    the lines in their order: it remembers the line before."""
    from app.modmgr.mods import get_current_mission
    from app.server.server_status import read_server_version

    template = get_current_mission(config.server_cfg_path) or ""
    terrain = template.rsplit(".", 1)[-1].lower() if "." in template else ""
    version = read_server_version(config.server_dir_path / config.profiles_path) or ""
    server_dir = config.server_dir_path
    before = [""]

    def check(line: str) -> bool:
        stock = is_stock_line(
            line,
            terrain=terrain,
            version=version,
            mod_file_exists=lambda rel: (server_dir / rel).is_file(),
            previous=before[0],
        )
        before[0] = line
        return stock

    return check


def _match_noise(line: str, counts: dict[str, int]) -> bool:
    for label, hint, pattern in NOISE:
        if hint in line and (pattern is None or re.search(pattern, line)):
            counts[label] = counts.get(label, 0) + 1
            return True
    return False


def _match_rules(line: str, lineno: int, hits: dict[str, _Hit]) -> Rule | None:
    """Record *line* under the first rule it matches, and return that rule."""
    for rule in RULES + CATCH_ALLS:
        if rule.hint and rule.hint not in line:
            continue
        m = re.search(rule.pattern, line) if rule.pattern else None
        if rule.pattern and not m:
            continue
        subject = m.group(rule.subject).strip() if (m and rule.subject) else ""
        if rule.id == "missing_mod_model" and _is_vanilla_model(subject):
            return None  # vanilla asks for models it never shipped; not a finding
        _record(hits, rule, lineno, _TS_PREFIX.sub("", line).strip(), subject, m)
        return rule  # first rule wins — a line is one finding
    return None


def _record(
    hits: dict[str, _Hit],
    rule: Rule,
    lineno: int,
    text: str,
    subject: str,
    m: re.Match | None,
) -> None:
    hit = hits.get(rule.id)
    if hit is None:
        hit = hits[rule.id] = _Hit(rule=rule, first_line=lineno)
    hit.count += 1
    if rule.value and m:
        try:
            hit.stated = int(m.group(rule.value))
        except (ValueError, IndexError):
            pass
    if subject:
        if subject not in hit.subject_counts and len(hit.subjects) < MAX_SUBJECTS + 1:
            hit.subjects.append(subject)
        hit.subject_counts[subject] = hit.subject_counts.get(subject, 0) + 1
    if len(hit.samples) < MAX_SAMPLES:
        hit.samples.append({"line": lineno, "text": text})


def sort_findings(findings: list[dict]) -> list[dict]:
    """Yours first, worst first — the vanilla baseline always sinks to the end."""
    order = {sev: i for i, sev in enumerate(SEVERITIES)}
    findings.sort(
        key=lambda f: (
            f.get("origin", ORIGIN_YOURS) != ORIGIN_YOURS,
            order.get(f["severity"], 9),
            -f["count"],
        )
    )
    return findings


def _sorted_findings(
    hits: dict[str, _Hit],
    terrain: str = "",
    mission: MissionBaseline | None = None,
    loaded_mods: set[str] | None = None,
    new_mods: set[str] | None = None,
    version: str = "",
) -> list[dict]:
    out: list[dict] = []
    for hit in hits.values():
        if hit.count >= hit.rule.min_count:
            out.extend(_split_by_origin(
                hit, terrain, mission, loaded_mods or set(), new_mods, version=version,
            ))
    return sort_findings(out)


def _split_by_origin(
    hit: _Hit,
    terrain: str,
    mission: MissionBaseline | None = None,
    loaded_mods: set[str] | None = None,
    new_mods: set[str] | None = None,
    *,
    version: str = "",
) -> list[dict]:
    """One hit in, one or two findings out — the stock half and the admin half.

    A rule fires on both kinds of line at once: Chernarus' own
    ``Static_FrozenScientist_DE`` and a typo the admin just made land in the
    same ``ce_type_missing`` hit. Splitting on the subject is what keeps the
    typo reportable while the stock entry stops crying wolf.

    Where the installed mission answers the question itself (*mission*), that
    answer wins over the table: it read this install, the table read Chernarus
    in 2026 Q3. Where it cannot — a file it needs is unreadable — the table is
    the fallback, never "nothing is stock".

    A loaded mod's own quirks (``MOD_BASELINE``) come off first, so what is
    left goes through the stock split exactly as before.
    """
    quirks = _mod_quirks(hit, mission, loaded_mods or set(), new_mods)
    if quirks:
        mod_half = _as_mod_findings(hit, quirks)
        rest = [s for s in hit.subjects if s not in quirks]
        if not rest:
            return mod_half
        return mod_half + _split_by_origin(_narrowed(hit, rest), terrain, mission, version=version)
    if mission is not None:
        derived = _split_by_mission(hit, terrain, mission)
        if derived is not None:
            return derived
    findings = _split_by_table(hit, terrain, version)
    if mission is not None and hit.rule.id in _TYPE_RULES:
        for f in findings:
            if f["origin"] == ORIGIN_YOURS:
                _attribute_types(f, hit, mission)
    return findings


def _split_by_table(hit: _Hit, terrain: str, version: str = "") -> list[dict]:
    baselines = _baselines_for(hit.rule.id, terrain, version)
    if not baselines:
        return [_as_finding(hit)]

    whole = next((b for b in baselines if not b.subjects and not b.count and not b.match), None)
    if whole is not None:
        return [_as_finding(hit, origin=ORIGIN_VANILLA, note=whole.note)]

    counted = next((b for b in baselines if b.count), None)
    if counted is not None:
        return [_as_counted_finding(hit, counted, terrain)]

    stock_names = {name for b in baselines for name in b.subjects}
    predicates = [b.match for b in baselines if b.match]
    notes = [b.note for b in baselines]

    def is_stock(name: str) -> bool:
        return name in stock_names or any(p(name, terrain) for p in predicates)

    stock = [s for s in hit.subjects if is_stock(s)]
    mine = [s for s in hit.subjects if not is_stock(s)]
    out = []
    if mine:
        out.append(_as_finding(hit, names=mine))
    if stock:
        out.append(_as_finding(hit, origin=ORIGIN_VANILLA, note=" ".join(notes), names=stock))
    return out or [_as_finding(hit)]


def _narrowed(hit: _Hit, names: list[str]) -> _Hit:
    """The part of *hit* about *names* only — its own count, its own samples."""
    return _Hit(
        rule=hit.rule,
        count=sum(hit.subject_counts.get(n, 1) for n in names),
        stated=hit.stated,
        subjects=list(names),
        subject_counts={n: hit.subject_counts[n] for n in names if n in hit.subject_counts},
        samples=[s for s in hit.samples if any(n in s["text"] for n in names)],
        first_line=hit.first_line,
    )


def _as_mod_findings(hit: _Hit, quirks: dict[str, ModBaseline]) -> list[dict]:
    """One ``origin="mod"`` finding per baseline entry, carrying the mod's name."""
    by_entry: dict[ModBaseline, list[str]] = {}
    for name, entry in quirks.items():
        by_entry.setdefault(entry, []).append(name)
    out = []
    for entry, names in by_entry.items():
        f = _as_finding(hit, origin=ORIGIN_MOD, note=entry.note, names=names)
        f["mod"] = entry.mod
        out.append(f)
    return out


def _as_counted_finding(hit: _Hit, baseline: Baseline, terrain: str) -> dict:
    """A rule that states its own total, against the total a stock mission states.

    At or under the stock number nothing of the admin's is failing. Above it,
    the finding stays theirs but says how much of the count was never theirs —
    "17 reported, 10 of which stock chernarusplus prints anyway".
    """
    from app.maps.registry import display_name

    stated = hit.stated if hit.stated is not None else hit.count
    map_name = display_name(terrain)
    if stated <= baseline.count:
        return _as_finding(
            hit,
            origin=ORIGIN_VANILLA,
            note=baseline.note,
            detail=f"{stated} reported — a stock {map_name} server prints {baseline.count}",
        )
    return _as_finding(
        hit,
        detail=(
            f"{stated} reported — {baseline.count} of them are a stock {map_name}'s "
            f"own, so {stated - baseline.count} came from your XML"
        ),
    )


# ── Derived from the installed mission (plans/39) ────────────────────────

#: Rules whose subject is a types.xml classname — attributed, never derived:
#: whether a class exists or is public is the engine's registry, not a file.
_TYPE_RULES = frozenset({"ce_type_missing", "ce_type_not_spawnable"})


def _names_list(names: list[str] | tuple[str, ...]) -> str:
    shown = list(names[:MAX_SUBJECTS])
    extra = len(names) - len(shown)
    return ", ".join(shown) + (f" (+{extra} more)" if extra > 0 else "")


def _split_by_mission(hit: _Hit, terrain: str, mission: MissionBaseline) -> list[dict] | None:
    """The finding split by what the mission's own files produce, or None when
    this rule has no derivation or the mission could not answer it."""
    rule = hit.rule.id
    if rule == "ce_groups_no_points" and mission.pointless_groups is not None:
        return [_as_derived_count(hit, list(mission.pointless_groups))]
    if rule == "ce_missing_event" and mission.orphan_event_spawns is not None:
        return _split_events(hit, mission)
    return None


def _as_derived_count(hit: _Hit, own: list[str]) -> dict:
    """``N groups have no points`` against the groups the mission itself has."""
    stated = hit.stated if hit.stated is not None else hit.count
    n = len(own)
    if stated <= n:
        return _as_finding(
            hit,
            origin=ORIGIN_VANILLA,
            detail=(
                f"{stated} reported — all {n} are your mission's own mapgroupproto.xml groups"
                if stated == n
                else f"{stated} reported — your mission's mapgroupproto.xml has {n} such groups"
            ),
            note=(
                f"Groups in the mission's own mapgroupproto.xml that no point is "
                f"defined for: {_names_list(own)}. That is how the file is, not "
                f"anything a mod added, and a stock server prints the same. "
                f"Nothing spawns in them; nothing else is affected."
            ),
        )
    return _as_finding(
        hit,
        detail=(
            f"{stated} reported — {n} are your mission's own, {stated - n} more "
            f"than its mapgroupproto.xml explains as it reads now"
        ),
        note=(
            f"{hit.rule.how} The mission's own: {_names_list(own)}. More than "
            f"that means the file changed since this run, or the server read "
            f"a different prototype file."
            if own
            else hit.rule.how
        ),
    )


def _split_events(hit: _Hit, mission: MissionBaseline) -> list[dict]:
    """Event spawns for an undefined event: the mission's own vs a mod block's."""
    stock_set = set(mission.orphan_event_spawns or ())
    by_owner = {
        name: owner for owner, names in mission.mod_orphan_event_spawns.items() for name in names
    }
    stock = [s for s in hit.subjects if s in stock_set]
    mine = [s for s in hit.subjects if s not in stock_set]
    out: list[dict] = []
    if mine:
        parts: list[str] = []
        owners: dict[str, list[str]] = {}
        for name in mine:
            if name in by_owner:
                owners.setdefault(by_owner[name], []).append(name)
        for owner, names in owners.items():
            parts.append(
                f"In cfgeventspawns.xml, {owner_label(owner)} added positions for "
                f"{_names_list(names)}, and no events file defines "
                f"{'it' if len(names) == 1 else 'them'}."
            )
        gone = [n for n in mine if n not in by_owner]
        if gone:
            parts.append(
                f"Not undefined in the mission as it reads now: {_names_list(gone)} — "
                f"fixed or removed since this run."
            )
        out.append(_as_finding(hit, names=mine, note=" ".join([hit.rule.how, *parts])))
    if stock:
        out.append(
            _as_finding(
                hit,
                origin=ORIGIN_VANILLA,
                names=stock,
                note=(
                    "The mission's own cfgeventspawns.xml has positions for "
                    f"{'this event' if len(stock) == 1 else 'these events'}, and no "
                    "events file it loads defines "
                    f"{'it' if len(stock) == 1 else 'them'} — not something a mod "
                    "added. Stock Chernarus and Livonia ship VehicleTransitBus "
                    "this way. The positions are simply unused."
                ),
            )
        )
    return out or [_as_finding(hit)]


def _attribute_types(finding: dict, hit: _Hit, mission: MissionBaseline) -> None:
    """Say who put each rejected class into the economy (plans/39 B).

    Not derivable — whether a class exists or is public lives in the game's
    config — but *where the entry came from* is: a mod's registered custom/
    types file, the mission's own db/types.xml, or neither any more. That is
    the part of the question an admin can act on, and it reaches community
    maps no table ever will.
    """
    sources = mission.type_sources
    names = finding["subjects"]
    if sources is None or not names:
        return
    from_mods: dict[str, list[str]] = {}
    in_mission: list[str] = []
    nowhere: list[str] = []
    for name in names:
        owners = [o for o in sources.get(name, ()) if o != MISSION]
        if owners:
            for o in owners:
                from_mods.setdefault(o, []).append(name)
        elif name in sources:
            in_mission.append(name)
        else:
            nowhere.append(name)

    missing = hit.rule.id == "ce_type_missing"
    parts: list[str] = []
    for owner, mod_names in from_mods.items():
        where = owner_label(owner)
        if missing:
            parts.append(
                f"From {where}: {_names_list(mod_names)} — its types list these, "
                "but no loaded addon defines them: the mod that does is not "
                "loaded, loads too late, or no longer ships the class."
            )
        else:
            if owner.startswith("@user:"):
                fix = "Remove the entry from that file on the Custom config tab."
            elif owner.startswith("@"):
                fix = (
                    "That entry is the mod's; the Custom config tab can turn the file "
                    "off or take it over."
                )
            else:
                fix = "Remove the entry from that file."
            parts.append(
                f"From {where}: {_names_list(mod_names)} — its types list a class "
                f"whose scope is not public. {fix}"
            )
    if in_mission:
        parts.append(
            f"In your mission's db/types.xml: {_names_list(in_mission)} — it came "
            "with the mission or was added in the Types editor. "
            + (
                "Remove the entry, or install the mod that defines the class."
                if missing
                else "Remove the entry; base and helper classes belong in events "
                "or spawnabletypes."
            )
        )
    if nowhere:
        parts.append(
            f"In no types file now: {_names_list(nowhere)} — removed or re-merged "
            "since this run, or a hand edit."
        )
    if parts:
        finding["how"] = " ".join(parts)
    if from_mods and not any(link["to"] == MOD_XML for link in finding["links"]):
        finding["links"].append({"label": "Custom config", "to": MOD_XML})


def _as_finding(
    hit: _Hit,
    *,
    origin: str = ORIGIN_YOURS,
    note: str = "",
    names: list[str] | None = None,
    detail: str | None = None,
) -> dict:
    rule = hit.rule
    if names is None:
        names = hit.subjects
        count = hit.stated if hit.stated is not None else hit.count
        samples = hit.samples
    else:
        # A split half owns only its own lines: its count is the sum of its
        # subjects' occurrences, and it shows only the samples that name one.
        count = sum(hit.subject_counts.get(n, 1) for n in names)
        samples = [s for s in hit.samples if any(n in s["text"] for n in names)]
    subjects = names[:MAX_SUBJECTS]
    extra = len(names) - len(subjects)
    if detail is None:
        if subjects:
            detail = ", ".join(subjects) + (f" (+{extra} more)" if extra > 0 else "")
        elif hit.stated is not None:
            detail = f"{hit.stated} reported by the server"
        else:
            detail = hit.samples[0]["text"] if hit.samples else ""
    return {
        "id": rule.id,
        "origin": origin,
        "severity": rule.severity,
        "title": rule.title,
        "count": count,
        "occurrences": hit.count,
        "detail": detail,
        "subjects": subjects,
        "why": rule.why,
        # The rule's own advice assumes the admin put it there. For a file
        # Bohemia ships, "remove the entry" is wrong, so the baseline note
        # replaces it rather than sitting beside it.
        "how": note or rule.how,
        "links": [{"label": lbl, "to": to} for lbl, to in rule.links],
        "samples": samples,
        "first_line": hit.first_line,
    }


def _outcome(path: Path, run: dict, reached_ready: bool) -> dict:
    """How the run ended, from the tail of the file.

    Only the last 8 KB is read: the shutdown markers are always the final lines,
    and re-reading a 200 MB file to look at its end would be absurd.
    """
    tail = _tail(path, 8192)
    if _CLEAN_SHUTDOWN in tail:
        return {
            "outcome": "clean_shutdown",
            "outcome_detail": "Server shut down cleanly.",
        }
    if _DESTROYING in tail:
        return {
            "outcome": "stopped",
            "outcome_detail": "Server was stopped, but did not log a clean termination.",
        }
    if reached_ready:
        return {
            "outcome": "running_or_cut_short",
            "outcome_detail": (
                "Server finished loading and accepted players. No shutdown was "
                "logged — it is either still running or ended without stopping "
                "cleanly."
            ),
        }
    return {
        "outcome": "startup_incomplete",
        "outcome_detail": (
            "The log never reaches \"Player connect enabled\" — this run never "
            "became joinable."
        ),
    }


def _tail(path: Path, nbytes: int) -> str:
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > nbytes:
                f.seek(size - nbytes)
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


# ── The launch command the service/bat actually installs ─────────────────
#
# Moved here verbatim from ``app/commands/log_analyzer.py`` so the CLI, the
# Doctor report and the Analysis page read the unit file through one
# implementation. The CLI re-exports the old private names.

# Matched by ``doctor_report._annotate_mod_validation`` to attach the "Install
# mods now" button — the two strings must stay identical.
EXECSTART_NO_MODS = "No mods found in ExecStart."


def extract_execstart(service_path: Path) -> str | None:
    """Parse ExecStart= from a systemd unit, joining line continuations."""
    if not service_path.is_file():
        return None

    exec_lines: list[str] = []
    inside_exec = False
    with open(service_path, "r", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if line.startswith("ExecStart="):
                inside_exec = True
                exec_lines.append(line.replace("ExecStart=", ""))
                if not line.endswith("\\"):
                    break
            elif inside_exec:
                exec_lines.append(line)
                if not line.endswith("\\"):
                    break

    if not exec_lines:
        return None
    return " ".join(exec_lines).replace("\\", "").strip()


def extract_start_command_from_bat(bat_path: Path) -> str | None:
    """Parse the DayZServer_x64.exe launch line out of the Windows bat file."""
    if not bat_path.is_file():
        return None
    with open(bat_path, "r", errors="ignore") as f:
        for line in f:
            if "DayZServer_x64.exe" in line:
                return line.strip()
    return None


def parse_mods(exec_command: str) -> list[str]:
    """Extract -mod= from a launch command (three quoting styles in the wild).

    Empties are dropped **after** unquoting, not before. A unit installed before
    8a34696 ("omit -mod= when no mods are active") still carries a literal
    ``"-mod="``; the quoted patterns need a character to match, so the bare
    fallback claimed the closing quote and returned ``['"']`` — one "mod" whose
    name unquotes to the empty string. ``server_dir / ""`` is the server dir,
    which of course exists, so a server running no mods at all was reported as
    "All mod folders exist." on the Doctor page. That is the false all-clear in
    the tile this analyzer replaced.
    """

    def _clean(mod: str) -> str:
        return mod.strip().strip('"').strip("'")

    for pattern in (r'"-mod=([^"]*)"', r"'-mod=([^']*)'", r"-mod=([^\s]*)"):
        match = re.search(pattern, exec_command)
        if match:
            return [name for name in (_clean(m) for m in match.group(1).split(";")) if name]
    return []


def parse_server_mods(exec_command: str) -> list[str]:
    """Extract ``-serverMod=`` from a launch command, the same way
    :func:`parse_mods` reads ``-mod=`` (plans/47 A). Case-insensitive, because a
    hand-typed flag is often ``-servermod=``."""

    def _clean(mod: str) -> str:
        return mod.strip().strip('"').strip("'")

    for pattern in (r'"-serverMod=([^"]*)"', r"'-serverMod=([^']*)'", r"-serverMod=([^\s]*)"):
        match = re.search(pattern, exec_command, re.IGNORECASE)
        if match:
            return [name for name in (_clean(m) for m in match.group(1).split(";")) if name]
    return []


def validate_mods(server_dir: Path, keys_dir: Path, mods: list[str]) -> list[tuple[str, str]]:
    """Duplicates, missing folders, @CF ordering and keys/. [(level, message)]."""
    issues: list[tuple[str, str]] = []

    if not mods:
        issues.append(("warning", EXECSTART_NO_MODS))
        return issues

    seen: set[str] = set()
    for mod in mods:
        if mod in seen:
            issues.append(("warning", f"Duplicate mod: {mod}"))
        seen.add(mod)

    missing = [m for m in mods if not (server_dir / m).exists()]
    if missing:
        for m in missing:
            issues.append(("error", f"Missing mod folder: {m}"))
    else:
        issues.append(("ok", "All mod folders exist."))

    if any("cf" in m.lower() for m in mods) and not mods[0].lower().startswith("@cf"):
        issues.append(("warning", "@CF should be FIRST in the -mod list."))

    if not keys_dir.is_dir():
        issues.append(("warning", "Keys folder missing — mods may fail signature checks."))

    return issues


def installed_launch_command(config) -> str | None:
    """The -mod= bearing command line the installed service/bat would run."""
    server_dir = config.server_dir_path
    if IS_WINDOWS:
        from app.commands.service import START_BAT_NAME

        return extract_start_command_from_bat(server_dir / START_BAT_NAME)

    from app.shared.systemd_scope import get_scope, server_unit

    return extract_execstart(get_scope(config).unit_dir / server_unit(config))


# ── Log discovery ────────────────────────────────────────────────────────


def profiles_dir(config) -> Path:
    """``<server_dir>/<profiles_path>`` — the -profiles target, which is where
    the engine writes .RPT/.mdmp. Uses the configured value; the old analyzer
    hardcoded "profiles" and looked in the wrong place for anyone who changed
    it."""
    return config.server_dir_path / config.profiles_path


def rpt_files_in(directory: Path) -> list[Path]:
    """Every .RPT in *directory*, newest first.

    Case-insensitive: the engine writes ``.RPT``, but a copied or renamed log is
    often ``.rpt`` and a plain ``glob("*.RPT")`` misses those on Linux. Takes a
    directory rather than a config so it can be pointed at an unpacked bundle.
    """
    if not directory.is_dir():
        return []
    try:
        logs = [
            f for f in directory.iterdir() if f.is_file() and f.suffix.lower() == ".rpt"
        ]
    except OSError:
        return []
    # Name breaks an mtime tie so "the newest log" is a stable answer: DayZ
    # stamps the filename with the start time, so the later name is the later
    # run even when the two files' mtimes land in the same second.
    logs.sort(key=lambda f: (f.stat().st_mtime, f.name), reverse=True)
    return logs


def latest_rpt_in(directory: Path) -> Path | None:
    logs = rpt_files_in(directory)
    return logs[0] if logs else None


def find_rpt_logs(config) -> list[Path]:
    return rpt_files_in(profiles_dir(config))


def latest_rpt(config) -> Path | None:
    return latest_rpt_in(profiles_dir(config))


def list_logs(config) -> list[dict]:
    """[{name, size_bytes, modified}] for the Logs tab's file picker."""
    out = []
    for path in find_rpt_logs(config):
        out.append({"name": path.name, "size_bytes": _size(path), "modified": _mtime(path)})
    return out


def resolve_log(config, name: str) -> Path | None:
    """Map a requested file *name* back to a real log.

    Matches against the discovered list rather than joining the name onto the
    profiles path, so ``../../etc/passwd`` is simply not found — same discipline
    as ``cleanup_ops.remove_mod_data_dirs``.
    """
    if not name:
        return latest_rpt(config)
    for path in find_rpt_logs(config):
        if path.name == name:
            return path
    return None


def crash_dumps(config) -> list[dict]:
    """Minidumps the engine leaves behind when it crashes, newest first.

    A crash is the one thing an RPT cannot state about itself: it stops
    mid-sentence. The .mdmp beside it is the evidence.
    """
    directory = profiles_dir(config)
    if not directory.is_dir():
        return []
    try:
        dumps = [
            f for f in directory.iterdir() if f.is_file() and f.suffix.lower() == ".mdmp"
        ]
    except OSError:
        return []
    dumps.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return [
        {"name": d.name, "size_bytes": _size(d), "modified": _mtime(d)} for d in dumps
    ]


# The engine names both files after a moment: an .RPT after the run's start, an
# .mdmp after the crash (`DayZServer_2026-09-23_10-13-47.mdmp`, 74 s into the
# run whose log is `…_10-12-39.RPT`). Both in the box's local time.
_STAMP_IN_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})")


def _stamp(path: Path) -> float | None:
    """When *path*'s run started (an .RPT) or its crash happened (an .mdmp):
    the time in its name, else its mtime, else None."""
    m = _STAMP_IN_NAME.search(path.name)
    if m:
        try:
            return datetime.strptime(
                f"{m.group(1)} {m.group(2)}:{m.group(3)}:{m.group(4)}", "%Y-%m-%d %H:%M:%S"
            ).timestamp()
        except ValueError:
            pass
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def previous_rpt(config, log_path: Path | None) -> Path | None:
    """The log of the run before *log_path*'s, by the start time in its name."""
    this_start = _stamp(log_path) if log_path is not None else None
    if this_start is None:
        return None
    earlier = [
        (t, p) for p in find_rpt_logs(config) if (t := _stamp(p)) is not None and t < this_start
    ]
    return max(earlier)[1] if earlier else None


def previous_run_mods(config, log_path: Path | None) -> set[str] | None:
    """``analyze_rpt``'s *previous_mods* for *log_path*: what the run before it
    loaded, read from that run's own log. A fact about the logs, not about
    today's config, so it holds for an older log too."""
    previous = previous_rpt(config, log_path)
    return launched_mods(previous) if previous is not None else None


def _date_dumps(config, log_path: Path | None, dumps: list[dict]) -> None:
    """Say which run each crash dump belongs to, in place.

    ``later_runs`` counts the logs that started after the crash, and
    ``previous_run`` / ``this_run`` place the dump against *log_path*. A dump
    in the folder is not evidence about the run just before this log: the one
    that made test pass 1 (F49) call a clean shutdown unclean was 13 runs old.
    """
    directory = profiles_dir(config)
    starts = [t for t in (_stamp(p) for p in find_rpt_logs(config)) if t is not None]
    this_start = _stamp(log_path) if log_path is not None else None
    earlier = [t for t in starts if this_start is not None and t < this_start]
    prev_start = max(earlier) if earlier else None
    for d in dumps:
        t = _stamp(directory / d["name"])
        d["later_runs"] = sum(1 for s in starts if t is not None and s > t)
        d["this_run"] = bool(t is not None and this_start is not None and t >= this_start)
        d["previous_run"] = bool(
            t is not None and prev_start is not None and prev_start <= t < this_start
        )


# ── Cross-checks (log facts vs. this install's config) ───────────────────


def _synthetic(
    id: str,
    severity: str,
    title: str,
    detail: str,
    why: str,
    how: str,
    links: list[dict] | None = None,
    count: int = 1,
) -> dict:
    """A finding derived by comparing the log against the config, rather than
    matched in it. Same shape as a rule hit so one renderer handles both."""
    return {
        "id": id,
        # Always the admin's: a cross-check compares the log against *their*
        # install, so there is nothing Bohemia ships for it to be about.
        "origin": ORIGIN_YOURS,
        "severity": severity,
        "title": title,
        "count": count,
        "occurrences": count,
        "detail": detail,
        "subjects": [],
        "why": why,
        "how": how,
        "links": links or [],
        "samples": [],
        "first_line": 0,
    }


def _promote_unclean_shutdown(findings: list[dict]) -> None:
    """Hand "no shutdown message" back to the admin when a crash dump backs it.

    Only a dump from the *previous* run backs it (``_date_dumps``): the line is
    about how that run ended, and an older dump says nothing about it.

    The line alone is baseline — a stock server prints it after a clean
    termination and on a first boot alike, so treating it as proof of a crash
    accused people of something that never happened. A minidump beside the log
    *is* proof, and then the line stops being background noise and becomes the
    economy-side half of the same event.
    """
    for f in findings:
        if f["id"] == "ce_no_shutdown_message":
            f["origin"] = ORIGIN_YOURS
            f["how"] = (
                "There is a crash dump beside this log, so the previous run "
                "really did end badly. Persistence written in its last minutes "
                "may be lost — check the newest backup before players do."
            )


def _classify_description(config, findings: list[dict]) -> None:
    """Give "description over 255 characters" back to Bohemia when it isn't so.

    DayZ prints the line at every start that gets as far as the server
    browser, even with ``description = "";`` and no mods (test pass 1, F50:
    23 of 28 logs, all of the joinable ones). The cause is not known, so the
    finding is decided by what it claims: within the limit, the admin has
    nothing to shorten.
    """
    from app.parsers.servercfg import read_cfg_values

    for f in findings:
        if f["id"] != "description_too_long" or f["origin"] != ORIGIN_YOURS:
            continue
        try:
            value = read_cfg_values(config.server_cfg_path, ["description"]).get("description")
        except OSError:
            return
        if value is None or len(value) > DESCRIPTION_LIMIT:
            return
        f["origin"] = ORIGIN_VANILLA
        f["how"] = (
            "DayZ prints this at every start, even with an empty description. Yours "
            f"is {len(value)} characters, within the limit, so there is nothing to shorten."
        )


def _cross_checks(
    config, run: dict, mods_listed: int, dumps: list[dict], server_side_listed: int = 0
) -> list[dict]:
    """``mods_listed`` counts the mods meant for ``-mod=``; ``server_side_listed``
    the ones marked to load through ``-serverMod=`` instead (plans/47 A)."""
    out: list[dict] = []

    if dumps:
        newest = dumps[0]
        # Only a crash no more than one start ago is news: the latest run, or
        # the one before it. An older dump stays listed, as a notice that says
        # its age: at "error" a dump 13 runs old made every later log read as
        # broken (test pass 1, F49).
        later = newest.get("later_runs")
        recent = later is None or later <= 1
        age = "" if recent else f", {later} runs ago"
        out.append(
            _synthetic(
                "crash_dump",
                "error" if recent else "notice",
                "Crash dump present",
                f"{newest['name']} ({newest['modified']}){age}",
                "The engine wrote a minidump, which it only does when it crashed.",
                "Check the mods installed just before this date. Keep the dump if "
                "you intend to report the crash to a mod author.",
                [{"label": "Cleanup", "to": "/cleanup"}],
                count=len(dumps),
            )
        )

    if run.get("command_line") and not run.get("mod_args"):
        if mods_listed > 0:
            out.append(
                _synthetic(
                    "run_without_mods",
                    "error",
                    "Last run launched with no mods",
                    f"-mod= was empty; the mod list holds {mods_listed} mod(s)"
                    + (" meant for it" if server_side_listed else ""),
                    "The server ran vanilla. Players joining with the mods loaded "
                    "would have been rejected, and modded loot never spawned.",
                    "Re-install the mods so the launch command is rebuilt from the "
                    "load order, then restart the server.",
                    [{"label": "Mods", "to": MODS_INSTALLED}],
                )
            )
        elif not server_side_listed:
            out.append(
                _synthetic(
                    "run_without_mods",
                    "notice",
                    "Last run launched with no mods",
                    "-mod= was empty and no mods are installed",
                    "This run was vanilla — expected if you have not installed any "
                    "mods yet.",
                    "Nothing to do unless you meant to run mods.",
                    [{"label": "Mods", "to": MODS_INSTALLED}],
                )
            )

    configured = _configured_mission(config)
    loaded = run.get("mission")
    if configured and loaded and configured != loaded:
        out.append(
            _synthetic(
                "mission_mismatch",
                "error",
                "Loaded mission is not the configured one",
                f"ran {loaded}, serverDZ.cfg says {configured}",
                "The run used a different mission folder than the one serverDZ.cfg "
                "names now — the map was changed after this run, or the change did "
                "not take.",
                "Re-apply the map so the mission, its XML and the server config "
                "agree, then restart.",
                [{"label": "Maps", "to": MAPS}, {"label": "serverDZ.cfg", "to": CONFIG_SERVERDZ}],
            )
        )

    if run.get("hostname") in {"DayZ Server", "EXAMPLE NAME"}:
        out.append(
            _synthetic(
                "default_hostname",
                "notice",
                "Server is still using the default name",
                run["hostname"],
                "The server browser shows this name to every player.",
                "Set `hostname` in serverDZ.cfg to something of your own.",
                [{"label": "serverDZ.cfg", "to": CONFIG_SERVERDZ}],
            )
        )

    # Only meaningful once the run actually got that far: in a log that never
    # reaches "Player connect enabled", a missing Steam registration is a
    # symptom of the failed boot, which `outcome` already says on its own.
    if (
        run.get("command_line")
        and not run.get("steam_registered")
        and run.get("ready_seconds") is not None
    ):
        out.append(
            _synthetic(
                "steam_not_registered",
                "warning",
                "No successful Steam registration in this log",
                "SteamGameServer_Init success line absent",
                "Without it the server does not appear in the in-game browser, "
                "even though it may be running and joinable by direct connect.",
                "Check the query port is free, then restart and watch this log.",
                [{"label": "serverDZ.cfg", "to": "/configs/serverdz?q=steamQueryPort"}],
            )
        )

    return out


def _mission_lookup(config) -> Callable[[str], MissionBaseline | None]:
    """``run["mission"]`` -> that mission's derived baseline, read from *this*
    install. The mission the log loaded, not the configured one: a log from
    before a map switch is judged against the files it actually ran."""
    from app.ops import mission_baseline

    root = config.server_dir_path / "mpmissions"

    def lookup(mission: str) -> MissionBaseline | None:
        name = next((p for p in reversed(re.split(r"[/\\]", mission)) if p), "")
        if name in ("", ".", ".."):
            return None
        try:
            return mission_baseline.baseline_for(root / name)
        except Exception:
            # A baseline is an improvement to the wording, never a reason for
            # the report itself to fail.
            return None

    return lookup


def _configured_mission(config) -> str | None:
    try:
        from app.modmgr.mods import get_current_mission

        return get_current_mission(config.server_cfg_path) or None
    except Exception:
        return None


# ── The composed report ──────────────────────────────────────────────────


def build_log_report(config, log_path: Path | None = None) -> dict:
    """Everything the Doctor card and the Analysis page's Logs tab render.

    Superset of the dict the old ``doctor_report._log_analysis`` returned:
    ``profiles_found``, ``latest_log``, ``errors``, ``mod_validation`` and
    ``mods_listed`` keep their meaning so existing consumers do not break.

    *log_path* overrides "the newest one". Note that the cross-checks are then
    comparing an older run against today's config, which is what you want for
    "did the map change since this ran?" and misleading for "is there a crash
    dump?" — the web route only offers the whole report for the current log.
    """
    from app.modmgr import modlist

    directory = profiles_dir(config)
    listed = modlist.installed(config)
    mods_listed = len(listed)
    server_side = set(modlist.server_only(config))
    server_side_listed = sum(1 for m in listed if m in server_side)
    log_path = log_path or latest_rpt(config)

    report: dict = {
        "profiles_found": directory.is_dir(),
        "profiles_dir": str(directory),
        "latest_log": log_path.name if log_path else None,
        "logs": list_logs(config),
        "crash_dumps": crash_dumps(config),
        "mods_listed": mods_listed,
        "mod_validation": [],
        "errors": [],
        "findings": [],
        "log": None,
        "run": {},
        "stats": {},
        "noise": {"suppressed": 0, "categories": []},
        "summary": summarise([]),
    }

    if log_path is not None:
        server_dir = config.server_dir_path
        report.update(
            analyze_rpt(
                log_path,
                baseline_for=_mission_lookup(config),
                mod_file_exists=lambda rel: (server_dir / rel).is_file(),
                previous_mods=previous_run_mods(config, log_path),
                mod_aliases=mod_aliases(config),
            )
        )
        report["latest_log"] = log_path.name

    findings = list(report.get("findings") or [])
    _date_dumps(config, log_path, report["crash_dumps"])
    findings.extend(
        _cross_checks(
            config,
            report.get("run") or {},
            mods_listed - server_side_listed,
            report["crash_dumps"],
            server_side_listed,
        )
    )
    if any(d["previous_run"] for d in report["crash_dumps"]):
        _promote_unclean_shutdown(findings)
    _classify_description(config, findings)
    report["findings"] = sort_findings(findings)
    report["summary"] = summarise(findings)
    # Back-compat shape for anything still reading the old error table.
    report["errors"] = [
        {"type": f["title"], "count": f["count"], "fix": f["how"]} for f in findings
    ]

    exec_cmd = installed_launch_command(config)
    report["service_command"] = exec_cmd
    if exec_cmd:
        mods = parse_mods(exec_cmd)
        server_mods = parse_server_mods(exec_cmd)
        report["service_mods"] = mods
        report["service_server_mods"] = server_mods
        # One list, so a server-side mod whose folder is gone is reported the
        # same way, and a mod in both flags reads as the duplicate it is.
        report["mod_validation"] = [
            {"level": level, "message": msg}
            for level, msg in validate_mods(
                config.server_dir_path, config.server_dir_path / "keys", mods + server_mods
            )
        ]
    else:
        report["service_mods"] = []
        report["service_server_mods"] = []

    return report
