"""Spawn test — put N of a mod's class next to the first player, then undo it.

A test is a *session*: starting one copies the mission's persistence folder
(``storage_<instanceId>``, `servercfg.storage_folder_name`) and every file it is
about to change into ``<config.paths.spawn_test_dir>/<timestamp>/``, then makes the
change; ending one puts all of it back. Only one test is active at a time, and
``active.json`` exists exactly while there is something to restore.

Two rules here are load-bearing:

* **Restore replaces the storage folder wholesale.** ``backup_ops.restore_backup``
  extracts files over the directory but never deletes one, so a
  ``data/dynamic_*.bin`` the server wrote during the test would survive it — and
  the spawned objects with it.
* **The ``init.c`` block is global functions only, plus one call line inside
  ``main()``.** Every stock mission already overrides ``CustomMission`` methods
  (``CreateCharacter``, ``StartingEquipSetup``; the Halloween ones also
  ``OnClientReadyEvent``), so a second override would be a script compile error
  and the server would not start.

A variant of the first mode, **at a location**, writes the same block with a
map position: the items appear there and the first player who joins is moved
to them.

Package B adds a second mode, **"Only X"**: instead of writing ``init.c`` it
rewrites the loot economy so the class under test is the only thing that
spawns, and empties the storage folder so the world starts fresh. Its restore is a
file copy rather than a surgical strip, which is why the two modes share the
session machinery and split at the ends.

Plan: ``plans/done/37-mod-spawn-test.md`` (Packages A and B).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.maps.registry import terrain_from_mission
from app.modmgr.mods import breaks_a_line
from app.parsers.servercfg import is_storage_folder, storage_folder_name
from app.presets import preset_engine
from app.shared.config import DayzConfig
from app.shared.json_utils import copy_file, notify_written
from app.shared.seed_data import MAP_SIZES

Log = Callable[[str], None] | None

INT32_MAX = 2**31 - 1

# The count above which the page warns, per RAM tier: under MIN_RAM_MB, then
# under TIER_VANILLA_MB, TIER_MODDED_LIGHT_MB, TIER_MODDED_HEAVY_MB, and above.
# Unmeasured starting values, not benchmarks — tune them after testing on real
# hardware. A warning only: nothing refuses a count for being high.
SPAWN_WARN_ABOVE = (10, 25, 50, 100, 200)

# "Only X" scopes (Package B): the value is what ``active.json`` records, the
# label is what a human reads in a log line or the pipeline reminder.
ONLY_X_SCOPES = {
    "loot": "loot only",
    "loot_vehicles": "loot + vehicles",
    "everything": "everything",
}

# Every stock event that spawns a vehicle is named Vehicle*, on all three maps.
# A prefix rather than a keyword search because Sakhal's StaticBoatFishing /
# StaticBoatMilitary are static wrecks, not vehicle events (plan 35's
# EventTarget note) — and "boat" would drag them in.
VEHICLE_EVENT_PREFIX = "Vehicle"

# Quick tweaks keep their pre-tweak values in these sidecars. They are
# snapshotted with the files they describe: restoring db/types.xml without the
# baseline that records what its values *were* leaves the two disagreeing, and
# the next tweak re-baselines off the test's zeros.
TWEAK_BASELINES = Path(".zsm-tweaks.json")
# plans/37 writes this as <mission>/.animal_tweaks.json; the real path is
# under env/ (app/presets/quick_tweaks.py:479).
ANIMAL_TWEAKS = Path("env") / ".animal_tweaks.json"
# The preset engine's multiplier baselines (plans/50 C), for the same reason.
PRESET_BASELINES = Path(preset_engine.PRESET_BASELINES)

MARKER_FILE = "zsm-spawn-test.done"
BLOCK_START = '// START Zedstead SPAWN TEST - added by zsm, removed by "End test & restore"'
BLOCK_END = "// END Zedstead SPAWN TEST"
CALL_TAG = "// Zedstead SPAWN TEST"
CALL_LINE = (
    "\tGetGame().GetCallQueue(CALL_CATEGORY_GAMEPLAY)"
    f".CallLater(ZsmSpawnTestTick, 5000, true); {CALL_TAG}"
)

_MAIN_RE = re.compile(r"\bvoid\s+main\s*\(\s*\)\s*\{")
_CLASSNAME_RE = re.compile(r"[A-Za-z0-9_]+")
# What a line has to start with to open or close the block (BLOCK_START and
# BLOCK_END without what follows on their lines).
_START_MARK = "// START Zedstead SPAWN TEST"
_END_MARK = "// END Zedstead SPAWN TEST"
_CALL_RE =re.compile(r"\r?\n[^\r\n]*" + re.escape(CALL_TAG) + r"[^\r\n]*")

# Read and written with surrogateescape so an init.c that is not valid UTF-8
# (a Latin-1 comment) still round-trips byte for byte. newline="" keeps CRLF.
_TEXT = {"encoding": "utf-8", "errors": "surrogateescape", "newline": ""}


class SpawnTestError(Exception):
    """A refusal. ``status`` is the HTTP status the web surface answers with."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.message = message
        self.status = status


# ── State ───────────────────────────────────────────────────────────────


def _root(config: DayzConfig) -> Path:
    return config.paths.spawn_test_dir


def _active_path(config: DayzConfig) -> Path:
    return _root(config) / "active.json"


def active_test(config: DayzConfig) -> dict | None:
    """The active test's state, or None. An unreadable state file reads as None
    here (callers only report it); ``start_*`` and ``end_test`` refuse on it."""
    try:
        data = json.loads(_active_path(config).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def active_test_warning(config: DayzConfig) -> str | None:
    """The reminder printed where a forgotten test would reach players.

    An "Only X" test earns a second sentence: every XML merge wipes and
    regenerates ``custom/`` (``app/xml/CLAUDE.md``), so an install, a mod
    toggle or a ``zsm run`` during the test silently un-zeroes the mod items
    it zeroed. That is a warning, never a refusal — an update run is also how
    a test server gets its mods in the first place.
    """
    state = active_test(config)
    if state is None:
        return None
    detail = f"{state.get('count')} x {state.get('classname')}"
    only_x = state.get("mode") == "only_x"
    if only_x:
        scope = state.get("scope")
        detail += f", Only X, {ONLY_X_SCOPES.get(scope, scope)}"
    elif isinstance(state.get("location"), dict):
        detail += f", at {location_text(state['location'])}"
    text = (
        f"Spawn test active for {state.get('mod')} ({detail}) — end it on the mod page "
        "before opening the server to players."
    )
    if only_x:
        text += " An XML merge during the test undoes it for mod items."
    return text


def _read_active_or_refuse(config: DayzConfig) -> dict:
    path = _active_path(config)
    if not path.exists():
        raise SpawnTestError("No spawn test is active.")
    state = active_test(config)
    if state is None:
        raise SpawnTestError(f"The spawn test state file can't be read: {path}")
    return state


def _write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _write_text_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".zsm-tmp")
    with open(tmp, "w", **_TEXT) as f:
        f.write(text)
    os.replace(tmp, path)
    notify_written(path)


def _read_text(path: Path) -> str:
    with open(path, "r", **_TEXT) as f:
        return f.read()


def _new_session_dir(config: DayzConfig) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    candidate = _root(config) / stamp
    n = 1
    while candidate.exists():
        n += 1
        candidate = _root(config) / f"{stamp}-{n}"
    candidate.mkdir(parents=True)
    return candidate


def _prune_old_sessions(config: DayzConfig, keep: Path) -> None:
    for entry in _root(config).iterdir():
        if entry.is_dir() and entry != keep:
            shutil.rmtree(entry, ignore_errors=True)


def _marker_path(config: DayzConfig) -> Path:
    return config.server_dir_path / config.profiles_path / MARKER_FILE


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_file(session: Path, mission_dir: Path, path: Path) -> None:
    """Copy one mission file into ``<session>/files/<path relative to the mission>``."""
    dest = session / "files" / path.relative_to(mission_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)


def _session_copies(session: Path) -> list[tuple[Path, Path]]:
    """(copy, mission-relative path) for every file in the session's files/ tree."""
    root = session / "files"
    if not root.is_dir():
        return []
    return [
        (path, path.relative_to(root))
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def _replace_dir(target: Path, source: Path) -> None:
    """Replace ``target`` wholesale with a copy of ``source``.

    Copying *over* the directory would leave behind anything the server wrote
    during the test (the ``restore_backup`` trap in this module's docstring),
    so the copy is staged beside it and swapped in.
    """
    staging = target.with_name(target.name + ".zsm-restore")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(source, staging, symlinks=True)
    if target.exists():
        shutil.rmtree(target)
    os.replace(staging, target)


def _empty_dir(path: Path) -> None:
    for entry in path.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def _refuse_if_active(config: DayzConfig) -> None:
    if not _active_path(config).exists():
        return
    state = active_test(config)
    if state is None:
        raise SpawnTestError(f"The spawn test state file can't be read: {_active_path(config)}")
    raise SpawnTestError(
        f"A spawn test is already active for {state.get('mod')} ({state.get('classname')}). "
        "End it first."
    )


# ── Inputs ──────────────────────────────────────────────────────────────


def mod_classes(config: DayzConfig, mod_name: str) -> list[str]:
    """The mod's item classes, de-duplicated and sorted case-insensitively.

    CfgVehicles holds most items, but a weapon is in CfgWeapons and a magazine
    or ammo box in CfgMagazines (`TYPE_CLASS_SECTIONS`, as types.xml names
    them), so a weapons-only mod has classes too.
    """
    from app.modmgr.mod_conflict import TYPE_CLASS_SECTIONS, extract_mod_classnames_cached

    classes = extract_mod_classnames_cached(
        config.server_dir_path, [mod_name], TYPE_CLASS_SECTIONS, prune=False
    ).get(mod_name, [])
    seen: dict[str, str] = {}
    for cls in classes:
        seen.setdefault(cls.lower(), cls)
    return sorted(seen.values(), key=str.lower)


def class_counts(config: DayzConfig) -> dict[str, int]:
    """How many item classes each installed mod adds (`mod_classes`), 0 for none.

    The Map Editor's Items tab lists only mods above 0. The first call parses
    every mod's PBOs, later ones read the classname cache (by PBO mtime), so a
    mod updated to add items counts again. Raises `ModListError` for an
    unreadable mod list.
    """
    from app.modmgr import modlist
    from app.modmgr.mod_conflict import TYPE_CLASS_SECTIONS, extract_mod_classnames_cached

    names = modlist.installed(config)
    by_mod = extract_mod_classnames_cached(config.server_dir_path, names, TYPE_CLASS_SECTIONS, prune=False)
    return {name: len({c.lower() for c in by_mod.get(name, [])}) for name in names}


def spawn_count_warning() -> tuple[int | None, str]:
    """(count above which to warn, why). ``None`` when this machine's RAM can't
    be read — no threshold is better than a guessed one."""
    from app.ops import vps_readiness

    measured, _swap = vps_readiness.mem_mb()
    if measured is None:
        return None, "could not read this machine's RAM"
    advertised = vps_readiness.advertised_ram_mb(measured)
    limits = vps_readiness.thresholds_or_defaults()
    bounds = (
        limits["MIN_RAM_MB"],
        limits["TIER_VANILLA_MB"],
        limits["TIER_MODDED_LIGHT_MB"],
        limits["TIER_MODDED_HEAVY_MB"],
    )
    gb = [b // 1024 for b in bounds]
    if advertised < bounds[0]:
        return SPAWN_WARN_ABOVE[0], f"under {gb[0]} GB RAM"
    for i in range(1, len(bounds)):
        if advertised < bounds[i]:
            return SPAWN_WARN_ABOVE[i], f"{gb[i - 1]}–{gb[i]} GB RAM"
    return SPAWN_WARN_ABOVE[-1], f"{gb[-1]} GB RAM or more"


def count_warning_text(count: int, warn_above: int | None, reason: str) -> str | None:
    """The "at your own risk" sentence, or None when there is nothing to warn about.
    ``webui/src/pages/ModPage.jsx`` renders the same wording — keep them in step."""
    if warn_above is None or count <= warn_above:
        return None
    return (
        f"{count} is above the {warn_above} suggested for this server ({reason}). "
        "Spawning this many can lag or crash the server — you do this at your own risk."
    )


def _validate_count(count: object) -> int:
    if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= INT32_MAX:
        raise SpawnTestError("Count must be a whole number, 0 or more.", status=422)
    return count


def _validate_classname(config: DayzConfig, mod_name: str, classname: str) -> str:
    """The class's spelling as the mod declares it."""
    if not _CLASSNAME_RE.fullmatch(classname or ""):
        raise SpawnTestError(f"{classname} is not a class in {mod_name}.", status=422)
    for cls in mod_classes(config, mod_name):
        if cls.lower() == classname.lower():
            return cls
    raise SpawnTestError(f"{classname} is not a class in {mod_name}.", status=422)


def _mission_dir(config: DayzConfig) -> tuple[str, Path]:
    from app.modmgr.mods import get_current_mission

    mission = get_current_mission(config.server_cfg_path)
    mission_dir = config.server_dir_path / "mpmissions" / mission if mission else None
    if not mission or not mission_dir.is_dir():
        raise SpawnTestError("Cannot determine the current mission.")
    return mission, mission_dir


# What a session calls its copy of the storage folder, whatever the server's
# own folder is named.
STORAGE_COPY = "storage_1"


def _storage_of(state: dict, mission_dir: Path) -> Path:
    """The folder a test copied, by the name its state recorded at the start.
    A test started before the name was recorded copied ``storage_1``."""
    name = str(state.get("storage_name") or "")
    return mission_dir / (name if is_storage_folder(name) else "storage_1")


# ── init.c ──────────────────────────────────────────────────────────────


def _block_lines(classname: str, count: int, at: tuple[float, float] | None = None) -> list[str]:
    """The Enforce Script block, as lines.

    **Measured against a real 1.29 server in 2026 Q3** (plan 37 step A9, first
    attempt), which refused the first version with three errors and
    ``Script module failed to compile: 'mission init script'`` — a server that
    then runs but that no client can join (the loading screen stops at ~60%):

        init.c,26: Unknown operator '%'
        init.c,28: Incompatible parameter 'pos'
        init.c,29: Cannot convert 'float' to 'vector' for argument '1' to method 'CreateObjectEx'

    So: **Enforce has no ``%`` operator** — the grid walks `col`/`row` counters
    instead — and the two follow-on errors were that broken expression degrading
    `pos`'s type. The position is built with ``Vector(x, y, z)`` now rather than
    by assigning components, which keeps the whole statement in one expression.
    Line 27 (``pos[2] = origin[2] + (i / perRow) * 6;``) was **not** among the
    errors, so integer division and vector-index assignment are both fine —
    don't "fix" those next time.

    What that run did prove works: a **global** function passed to
    ``CallLater``/``Remove``, ``array<Man>`` + ``GetPlayers``, ``IsAlive``,
    ``string.Format`` and the file helpers — none of them were flagged.

    ``at`` is a map position (x, z): the grid starts there instead of at the
    player, and the player is moved to it first so they land beside the items.
    ``SetPosition`` on a player is what admin tools teleport with; this block's
    use of it has not been run on a server yet.
    """
    per_row = max(1, math.ceil(math.sqrt(count)))
    if at is None:
        origin = ["\tvector origin = player.GetPosition();"]
    else:
        origin = [
            f"\tfloat ox = {at[0]:.1f};",
            f"\tfloat oz = {at[1]:.1f};",
            "\tvector origin = Vector(ox, GetGame().SurfaceY(ox, oz), oz);",
            "\tplayer.SetPosition(origin);",
        ]
    return [
        BLOCK_START,
        "void ZsmSpawnTestTick()",
        "{",
        f'\tstring marker = "$profile:{MARKER_FILE}";',
        "\tif (FileExist(marker))",
        "\t{",
        "\t\tGetGame().GetCallQueue(CALL_CATEGORY_GAMEPLAY).Remove(ZsmSpawnTestTick);",
        "\t\treturn;",
        "\t}",
        "\tarray<Man> players = new array<Man>;",
        "\tGetGame().GetPlayers(players);",
        "\tif (players.Count() == 0)",
        "\t\treturn;",
        "\tMan player = players[0];",
        "\tif (!player || !player.IsAlive())",
        "\t\treturn;",
        "",
        f'\tstring cls = "{classname}";',
        f"\tint count = {count};",
        f"\tint perRow = {per_row};",
        *origin,
        "\tint created = 0;",
        "\tint col = 0;",
        "\tint row = 0;",
        "\tfor (int i = 0; i < count; i++)",
        "\t{",
        "\t\tfloat px = origin[0] + 6 + col * 6;",
        "\t\tfloat pz = origin[2] + row * 6;",
        "\t\tvector pos = Vector(px, GetGame().SurfaceY(px, pz), pz);",
        "\t\tif (GetGame().CreateObjectEx(cls, pos, ECE_PLACE_ON_SURFACE))",
        "\t\t\tcreated++;",
        "\t\tcol++;",
        "\t\tif (col >= perRow)",
        "\t\t{",
        "\t\t\tcol = 0;",
        "\t\t\trow++;",
        "\t\t}",
        "\t}",
        '\tPrint(string.Format("[zsm spawn test] created %1 of %2 %3", created, count, cls));',
        "",
        "\tFileHandle f = OpenFile(marker, FileMode.WRITE);",
        "\tif (f)",
        "\t{",
        "\t\tFPrintln(f, cls);",
        "\t\tCloseFile(f);",
        "\t}",
        "\tGetGame().GetCallQueue(CALL_CATEGORY_GAMEPLAY).Remove(ZsmSpawnTestTick);",
        "}",
        BLOCK_END,
    ]


def _check_injectable(text: str) -> re.Match:
    if "Zedstead SPAWN TEST" in text:
        raise SpawnTestError(
            "init.c still contains a spawn-test block from an earlier test. Remove it by hand "
            "(between the START/END Zedstead SPAWN TEST lines) and try again."
        )
    match = _MAIN_RE.search(text)
    if not match:
        raise SpawnTestError("init.c has no void main() to hook into — the test was not started.")
    # The call goes on a line of its own after the brace; code sharing that line
    # would end up after the call's trailing comment, and strip could not give
    # the file back byte for byte.
    rest = text[match.end():].split("\n", 1)[0]
    if rest.strip():
        raise SpawnTestError(
            "init.c's void main() has code on the same line as its opening brace. "
            "Move that code to the next line and try again."
        )
    return match


def inject_spawn_test_block(
    text: str, classname: str, count: int, at: tuple[float, float] | None = None
) -> str:
    """``text`` with the spawn-test block before ``void main()`` and the call as
    the first line of its body. ``strip_spawn_test_block`` undoes it exactly."""
    match = _check_injectable(text)
    nl = "\r\n" if "\r\n" in text else "\n"
    block = nl.join(_block_lines(classname, count, at)) + nl
    line_start = text.rfind("\n", 0, match.start()) + 1
    brace_end = match.end()
    return (
        text[:line_start]
        + block
        + text[line_start:brace_end]
        + nl
        + CALL_LINE
        + text[brace_end:]
    )


def strip_spawn_test_block(text: str) -> str:
    """Remove the fenced block and the call line (with the newline before it)."""
    return _CALL_RE.sub("", _strip_blocks(text))


def _line_start_find(text: str, mark: str, start: int) -> int:
    """The first index from *start* where a line begins with *mark*, or -1."""
    i = text.find(mark, start)
    while i > 0 and text[i - 1] != "\n":
        i = text.find(mark, i + 1)
    return i


def _strip_blocks(text: str) -> str:
    """*text* without its fenced blocks, in one pass.

    A block runs from a line starting with ``_START_MARK``, through that
    line's ``\\n``, to the first later line starting with ``_END_MARK``, and
    takes that line with its ``\\n`` or ``\\r\\n``. A START with no END after it
    is left alone, and so is everything after it, since no later START can
    have one either. The lazy regex this replaced searched the rest of the
    file again from every such START: 4000 of them took 4.2 s (plans/68 B25).
    """
    size = len(text)
    parts: list[str] = []
    pos = 0
    start = _line_start_find(text, _START_MARK, 0)
    while start >= 0:
        newline = text.find("\n", start + len(_START_MARK))
        if newline < 0:
            break
        end = _line_start_find(text, _END_MARK, newline + 1)
        if end < 0:
            break
        stop = end + len(_END_MARK)
        while stop < size and text[stop] not in "\r\n":
            stop += 1
        if text.startswith("\r\n", stop):
            stop += 2
        elif text.startswith("\n", stop):
            stop += 1
        parts.append(text[pos:start])
        pos = stop
        start = _line_start_find(text, _START_MARK, stop)
    parts.append(text[pos:])
    return "".join(parts)


# ── "Only X" (Package B) ────────────────────────────────────────────────


@dataclass
class _OnlyXPlan:
    """What an "Only X" start is about to write, worked out before it writes it.

    The trees are already edited in memory; ``saves`` holds the callables that
    put them on disk. Planning first is what makes the "nothing to test"
    refusal free of side effects.
    """

    saves: list[tuple[Path, Callable[[], None]]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    types_hits: int = 0
    event_hits: int = 0


def _event_files(mission_dir: Path) -> list[Path]:
    """``db/events.xml`` plus every ``custom/`` file registered as events.

    A mod's events fragment is never merged into ``db/events.xml`` — it is
    copied to ``custom/`` and registered in cfgeconomycore.xml, which DayZ
    loads alongside it. An "Only X" that read only ``db/events.xml`` would
    leave every modded event spawning and the "only" would be a lie.
    """
    from app.xml import events_xml, xml_economy

    found: list[Path] = []
    stock = events_xml.get_events_path(mission_dir)
    if stock:
        found.append(stock)
    for name, ce_type in xml_economy.registered_custom_entries(mission_dir):
        if ce_type != "events":
            continue
        path = mission_dir / "custom" / name
        if path.is_file():
            found.append(path)
    return found


def _position_notes(mission_dir: Path, event_names: list[str], count: int) -> list[str]:
    """Why an event that is set to spawn N may still produce fewer than N.

    An event spawns at the positions cfgeventspawns.xml gives it, so a nominal
    of 50 against 12 positions is 12 — which reads as "the test failed" unless
    it is said out loud before the server starts.
    """
    from app.xml import eventspawns_xml

    if not event_names:
        return []
    path = eventspawns_xml.get_eventspawns_path(mission_dir)
    if path is None:
        return [
            "There is no cfgeventspawns.xml, so the events that spawn it have no "
            "positions to use."
        ]
    parsed = eventspawns_xml.parse_eventspawns_file(path)
    positions = {entry.name.lower(): len(entry.positions) for entry in parsed.entries}
    notes: list[str] = []
    for name in event_names:
        found = positions.get(name.lower(), 0)
        if not found:
            notes.append(
                f"{name} has no positions in cfgeventspawns.xml, so nothing will spawn from it."
            )
        elif found < count:
            notes.append(f"{name} has only {found} spawn positions — at most {found} can appear.")
    return notes


def _plan_only_x(mission_dir: Path, classname: str, count: int, scope: str) -> _OnlyXPlan:
    """Zero the economy except ``classname``, in memory.

    Matching is case-insensitive on both sides: DayZ resolves class names that
    way, and a mod whose types.xml spells the class differently from its
    config.cpp would otherwise be zeroed along with everything else — a test
    that proves the mod is broken when it is the comparison that is.
    """
    from app.xml import events_xml, types_xml

    lower = classname.lower()
    plan = _OnlyXPlan()

    for path in types_xml.scan_types_files(mission_dir):
        parsed = types_xml.parse_types_file(path, preserve_comments=True)
        changed = False
        for entry in parsed.entries:
            hit = entry.name.lower() == lower
            if hit:
                plan.types_hits += 1
            target = count if hit else 0
            # lifetime, restock, usage, value and flags are left alone: the
            # question is whether the mod's own entry spawns, not whether a
            # rewritten one does.
            if (entry.nominal, entry.min) != (target, target):
                types_xml.set_numeric_field(entry, "nominal", target)
                types_xml.set_numeric_field(entry, "min", target)
                changed = True
        if changed:
            plan.saves.append(
                (path, lambda parsed=parsed: types_xml.save_types_files([parsed]))
            )

    if scope == "loot":
        return plan

    spawning: list[str] = []
    for path in _event_files(mission_dir):
        parsed = events_xml.parse_events_file(path, preserve_comments=True)
        changed = False
        for entry in parsed.entries:
            spawns = [c for c in entry.children if (c.type or "").lower() == lower]
            if spawns:
                plan.event_hits += len(spawns)
                spawning.append(entry.name)
                for numeric in ("nominal", "min", "max"):
                    events_xml.set_event_numeric(entry, numeric, count)
                events_xml.set_event_active(entry, True)
                for child in entry.children:
                    hit = (child.type or "").lower() == lower
                    events_xml.set_child_values(
                        child, 0, count if hit else 0, child.lootmin, child.lootmax
                    )
                changed = True
            elif scope == "everything" or entry.name.startswith(VEHICLE_EVENT_PREFIX):
                # Zero counts rather than active=0: a smaller, cleaner diff,
                # and the restore is a file copy either way.
                if (entry.nominal, entry.min, entry.max) != (0, 0, 0):
                    for numeric in ("nominal", "min", "max"):
                        events_xml.set_event_numeric(entry, numeric, 0)
                    changed = True
        if changed:
            plan.saves.append((path, lambda parsed=parsed: events_xml.save_events_file(parsed)))

    plan.notes.extend(_position_notes(mission_dir, spawning, count))
    return plan


def _nothing_to_test(classname: str, scope: str) -> str:
    events = "" if scope == "loot" else ", and no event spawns it"
    return (
        f'{classname} has no types.xml entry{events} — the economy can\'t spawn it. '
        'Use "Spawn next to me" instead.'
    )


# ── Start / end ─────────────────────────────────────────────────────────


def start_near_player(
    config: DayzConfig,
    mod_name: str,
    classname: str,
    count: object,
    log: Log = None,
) -> dict:
    """Arm a test: ``count`` x ``classname`` next to the first player who joins.

    The caller refuses while the server runs — this module has no view of that.
    """
    return _start_init_c(config, mod_name, classname, count, None, log)


def start_at_location(
    config: DayzConfig,
    mod_name: str,
    classname: str,
    count: object,
    x: object,
    z: object,
    name: str | None = None,
    log: Log = None,
) -> dict:
    """Arm a test: ``count`` x ``classname`` at map position (x, z), and the
    first player who joins moved there. ``name`` is the location's label, for
    the state and the log only.
    """
    _, mission_dir = _mission_dir(config)
    location = _validate_location(mission_dir.name, x, z, name)
    return _start_init_c(config, mod_name, classname, count, location, log)


def mission_locations(config: DayzConfig) -> tuple[list[dict], int | None]:
    """The current map's named places (name, x, z), sorted by name, and its edge
    length in metres (None for a map zsm has no size for)."""
    from app.maps.locations import load_locations

    try:
        _, mission_dir = _mission_dir(config)
    except SpawnTestError:
        return [], None
    terrain = terrain_from_mission(mission_dir.name)
    places = sorted(load_locations(terrain, config.server_dir_path), key=lambda loc: loc.name.lower())
    return (
        [{"name": loc.name, "x": loc.x, "z": loc.z} for loc in places],
        MAP_SIZES.get(terrain),
    )


def _validate_location(mission: str, x: object, z: object, name: str | None) -> dict:
    size = MAP_SIZES.get(terrain_from_mission(mission))
    coords = []
    for axis, value in (("X", x), ("Z", z)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise SpawnTestError(f"{axis} must be a number.", status=422)
        # A map with no known size is only held to "not below 0".
        if value < 0 or (size and value > size):
            limit = f"0 to {size}" if size else "0 or more"
            raise SpawnTestError(f"{axis} {value:g} is off the map ({limit}).", status=422)
        coords.append(round(float(value), 1))
    label = (name or "").strip()
    # The name is shown and logged, never written into init.c.
    if breaks_a_line(label) or len(label) > 100:
        raise SpawnTestError("The location name must be one short line.", status=422)
    return {"name": label or None, "x": coords[0], "z": coords[1]}


def location_text(location: dict) -> str:
    """How a log line or the CLI names a test's location."""
    where = f"{location.get('x'):g}, {location.get('z'):g}"
    return f"{location['name']} ({where})" if location.get("name") else where


def _start_init_c(
    config: DayzConfig,
    mod_name: str,
    classname: str,
    count: object,
    location: dict | None,
    log: Log,
) -> dict:
    """The init.c modes: next to the first player, or at ``location``."""
    _refuse_if_active(config)
    count = _validate_count(count)
    classname = _validate_classname(config, mod_name, classname)
    mission, mission_dir = _mission_dir(config)
    init_c = mission_dir / "init.c"
    if not init_c.is_file():
        raise SpawnTestError(f"{mission}/init.c not found.")
    original = _read_text(init_c)
    at = (location["x"], location["z"]) if location else None
    new_text = inject_spawn_test_block(original, classname, count, at)

    storage = mission_dir / storage_folder_name(config.server_cfg_path)
    storage_existed = storage.is_dir()
    session = _new_session_dir(config)
    wrote_init = False
    try:
        if storage_existed:
            shutil.copytree(storage, session / STORAGE_COPY, symlinks=True)
        (session / "files").mkdir()
        shutil.copy2(init_c, session / "files" / "init.c")

        _marker_path(config).unlink(missing_ok=True)
        _write_text_atomic(init_c, new_text)
        wrote_init = True

        state = {
            "mode": "at_location" if location else "near_player",
            "scope": None,
            "mod": mod_name,
            "classname": classname,
            "count": count,
            "mission": mission,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "session_dir": str(session),
            "storage_existed": storage_existed,
            "storage_name": storage.name,
            "written": {},
        }
        if location:
            state["location"] = location
        _write_json_atomic(_active_path(config), state)
    except OSError as exc:
        if wrote_init:
            copy_file(session / "files" / "init.c", init_c)
        shutil.rmtree(session, ignore_errors=True)
        raise SpawnTestError(f"Could not start the spawn test: {exc}", status=500) from exc

    _prune_old_sessions(config, keep=session)
    if log:
        if location:
            log(
                f"Spawn test armed: {count} x {classname} will appear at "
                f"{location_text(location)}, and the first player who joins after the next "
                "server start is moved there."
            )
        else:
            log(
                f"Spawn test armed: {count} x {classname} will appear next to the first player "
                "who joins after the next server start."
            )
        log(
            'The server log line "[zsm spawn test] created N of M" says how many the engine '
            "could create; 0 means the class can't be spawned."
        )
    return state


def start_only_x(
    config: DayzConfig,
    mod_name: str,
    classname: str,
    count: object,
    scope: str,
    log: Log = None,
) -> dict:
    """Make ``classname`` the only thing the loot economy spawns.

    ``init.c`` is not touched. Instead every ``<type>`` in the mission goes to
    nominal/min 0 except this one, which goes to ``count``; the event scopes do
    the same to ``<event>`` counts. The storage folder is then emptied, because
    loot already lying on the map counts toward the nominals it came from and
    the "only X" world never appears while it does.

    The caller refuses while the server runs — this module has no view of that.
    """
    _refuse_if_active(config)
    if scope not in ONLY_X_SCOPES:
        raise SpawnTestError(
            f"Unknown spawn test scope: {scope!r}. Use one of: {', '.join(ONLY_X_SCOPES)}.",
            status=422,
        )
    count = _validate_count(count)
    classname = _validate_classname(config, mod_name, classname)
    mission, mission_dir = _mission_dir(config)

    # Planned in memory, so this refusal has written nothing.
    plan = _plan_only_x(mission_dir, classname, count, scope)
    if not plan.types_hits and not plan.event_hits:
        raise SpawnTestError(_nothing_to_test(classname, scope), status=422)

    snapshot = [path for path, _save in plan.saves]
    for sidecar in (mission_dir / TWEAK_BASELINES, mission_dir / ANIMAL_TWEAKS, mission_dir / PRESET_BASELINES):
        if sidecar.is_file():
            snapshot.append(sidecar)

    storage = mission_dir / storage_folder_name(config.server_cfg_path)
    storage_existed = storage.is_dir()
    session = _new_session_dir(config)
    written: dict[str, str] = {}
    try:
        if storage_existed:
            shutil.copytree(storage, session / STORAGE_COPY, symlinks=True)
        (session / "files").mkdir(exist_ok=True)
        for path in snapshot:
            _snapshot_file(session, mission_dir, path)

        for path, save in plan.saves:
            save()
            written[str(path)] = _sha256(path)
        if storage_existed:
            _empty_dir(storage)

        state = {
            "mode": "only_x",
            "scope": scope,
            "mod": mod_name,
            "classname": classname,
            "count": count,
            "mission": mission,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "session_dir": str(session),
            "storage_existed": storage_existed,
            "storage_name": storage.name,
            "written": written,
        }
        _write_json_atomic(_active_path(config), state)
    except OSError as exc:
        for copy_path, rel in _session_copies(session):
            copy_file(copy_path, mission_dir / rel)
        if storage_existed and (session / STORAGE_COPY).is_dir():
            _replace_dir(storage, session / STORAGE_COPY)
        shutil.rmtree(session, ignore_errors=True)
        raise SpawnTestError(f"Could not start the spawn test: {exc}", status=500) from exc

    _prune_old_sessions(config, keep=session)
    if log:
        log(
            f"Only X armed: {classname} set to {count} in the loot economy, everything else "
            f"in scope ({ONLY_X_SCOPES[scope]}) set to 0 across {len(plan.saves)} files."
        )
        if storage_existed:
            log(
                f"{storage.name} emptied for the test — the world starts fresh; "
                "End test & restore puts it back."
            )
        for note in plan.notes:
            log(note)
        log(
            "Items spawn where the map allows them, not next to you — start the server and "
            "give the central economy time to place them."
        )
    return state


def end_test(config: DayzConfig, log: Log = None) -> dict:
    """Put ``init.c`` and the storage folder back as they were when the test
    started.

    The caller refuses while the server runs: a running server rewrites its
    storage and would undo the restore on shutdown.
    """
    state = _read_active_or_refuse(config)
    session = Path(state.get("session_dir") or "")
    mission_dir = config.server_dir_path / "mpmissions" / str(state.get("mission") or "")
    storage_copy = session / STORAGE_COPY
    storage_existed = bool(state.get("storage_existed"))
    only_x = state.get("mode") == "only_x"
    init_copy = session / "files" / "init.c"

    # Refuse before touching anything: a half-restore is worse than none.
    if not state.get("mission") or not mission_dir.is_dir():
        raise SpawnTestError(f"The test's mission folder is gone: {mission_dir}")
    if storage_existed and not storage_copy.is_dir():
        raise SpawnTestError(
            f"The copy of {_storage_of(state, mission_dir).name} at {storage_copy} is missing, "
            "so nothing was restored."
        )
    if only_x:
        # The directory, not its contents: `start_only_x` always creates it, and
        # a test whose economy already matched what it asked for legitimately
        # wrote nothing. Its absence means the session itself is gone.
        if not (session / "files").is_dir():
            raise SpawnTestError(
                f"The file copies at {session / 'files'} are missing, so nothing was restored."
            )
    elif not init_copy.is_file():
        raise SpawnTestError(f"The init.c copy at {init_copy} is missing, so nothing was restored.")

    restored: list[str] = []
    drifted: list[str] = []

    if only_x:
        restored, drifted = _restore_only_x(state, session, mission_dir, log)
        return _finish_end(config, state, mission_dir, storage_copy, storage_existed,
                           restored, drifted, log)

    init_c = mission_dir / "init.c"
    before = _read_text(init_copy)
    if init_c.is_file():
        live = _read_text(init_c)
        stripped = strip_spawn_test_block(live)
        if stripped == before:
            if live != before:
                copy_file(init_copy, init_c)
        else:
            _write_text_atomic(init_c, stripped)
            drifted.append("init.c")
            if log:
                log(
                    "init.c was edited during the test; kept your edits and removed only the "
                    f"spawn-test block. The pre-test copy is in {init_copy}."
                )
    else:
        copy_file(init_copy, init_c)
        if log:
            log("init.c was missing; put the pre-test copy back.")
    restored.append("init.c")
    return _finish_end(config, state, mission_dir, storage_copy, storage_existed,
                       restored, drifted, log)


def _restore_only_x(
    state: dict, session: Path, mission_dir: Path, log: Log
) -> tuple[list[str], list[str]]:
    """Put every snapshotted file back, keeping a copy of anything that moved.

    Drift is measured against the sha256 recorded **after** the test's own
    write, so an edit made during the test is distinguishable from the test's
    own change. The edit is never discarded silently — it goes to the
    session's ``drifted/`` beside the pre-test copy, and the pre-test file wins
    on disk, because a half-zeroed economy is nobody's intent.
    """
    written = state.get("written") or {}
    restored: list[str] = []
    drifted: list[str] = []
    for copy_path, rel in _session_copies(session):
        live = mission_dir / rel
        expected = written.get(str(live))
        if expected and live.is_file() and _sha256(live) != expected:
            keep = session / "drifted" / rel
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(live, keep)
            drifted.append(rel.as_posix())
            if log:
                log(
                    f"{rel.as_posix()} was edited during the test; the pre-test version is "
                    f"back and your edit is kept at {keep}."
                )
        copy_file(copy_path, live)
        restored.append(rel.as_posix())
    return restored, drifted


def _finish_end(
    config: DayzConfig,
    state: dict,
    mission_dir: Path,
    storage_copy: Path,
    storage_existed: bool,
    restored: list[str],
    drifted: list[str],
    log: Log,
) -> dict:
    """The half of ``end_test`` both modes share: storage, marker, state file."""
    storage = _storage_of(state, mission_dir)
    if storage_existed:
        _replace_dir(storage, storage_copy)
        storage_result = "restored"
    else:
        if storage.exists():
            shutil.rmtree(storage)
        storage_result = "removed"
    if log:
        log(
            f"{storage.name} restored to the copy taken when the test started."
            if storage_result == "restored"
            else f"{storage.name} removed — there was none before the test."
        )

    _marker_path(config).unlink(missing_ok=True)
    _active_path(config).unlink()
    if log:
        log(f"Spawn test for {state.get('mod')} ({state.get('classname')}) ended.")
    return {"restored_files": restored, "storage": storage_result, "drifted": drifted}
