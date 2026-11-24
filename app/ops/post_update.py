"""After an update: did the server come back, and if not, which mods stopped it (plans/73).

A DayZ update cannot be held back. Steam has no "never update" setting for
players, so a server left on the old build rejects everyone who launched the
game to join it ("Bad version, server rejected connection"). The server has to
take the update, and on a big one many mods stop working until their authors
publish a fix. What zsm can do is notice when the server did not come back,
name the mods behind it, and offer to set just those aside.

This module is the only place that decides what happened after an update. The
three update paths (`update_cycle.run_update_cycle`, the interactive
`zsm run`, `POST /api/update/run`) call it and none re-implements it.

- **`snapshot` / `game_updated` / `mods_updated`**: what the update changed,
  from the installed build id and each mod's Workshop `time_updated`.
- **`watch_boot`**: waits for the first log the server writes after a start,
  and returns as soon as it reaches "Player connect enabled" (joinable), the
  server crashes, or the timeout passes. Measured in step 0: a mod whose script
  does not compile crashes the server 4 s after start (exit 139) and leaves a
  `crash_<date>.log` and a `.mdmp`, so a crash is seen at once and the timeout
  only matters for a server that hangs while loading. A crash log alone is no
  crash while the process runs: DayZ writes one for every script error it
  survives (`_crash_evidence`).
- **`suspects`**: the mods the failed boot's script errors point at. An
  Enfusion script error names its file under the PBO's ``prefix``, never the
  ``@folder`` (step 0), so each path is matched against the prefixes of the
  mods the run loaded, longest first. A path no mod claims is reported as it
  is, never guessed into a mod.
- **`check_boot` / `start_and_check`**: the above in the order the update
  paths need it. The result is saved per instance (`config.paths.post_update`)
  so `zsm mods triage` and the Update page can show it later, and a failed
  boot sends one Discord message.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable

from app.shared.config import DayzConfig
from app.modmgr import modlist

JOINABLE = "joinable"
CRASHED = "crashed"
NOT_JOINABLE = "not_joinable"

DEFAULT_BOOT_TIMEOUT = 600
_MIN_BOOT_TIMEOUT = 30
_POLL = 3.0
# A server process that is not there yet just after a start is not a crash:
# on Windows the .bat takes a moment to launch DayZServer_x64.exe.
_START_GRACE = 10.0

Emit = Callable[[str], None]


@dataclass
class UpdateSnapshot:
    """What was installed before an update."""

    taken_at: float
    build: str
    # Enabled mod -> the Workshop time_updated of its installed copy (0 = unknown).
    mods: dict[str, int] = field(default_factory=dict)


@dataclass
class BootOutcome:
    status: str  # JOINABLE | CRASHED | NOT_JOINABLE
    seconds: float
    rpt: Path | None = None
    detail: str = ""


def boot_timeout(config: DayzConfig) -> int:
    """`config.post_update_boot_timeout`, never below 30 s."""
    try:
        value = int(getattr(config, "post_update_boot_timeout", DEFAULT_BOOT_TIMEOUT))
    except (TypeError, ValueError):
        value = DEFAULT_BOOT_TIMEOUT
    return max(_MIN_BOOT_TIMEOUT, value)


def installed_mod_times(config: DayzConfig) -> dict[str, int]:
    """Every mod in the mod list -> the Workshop `time_updated` of its installed
    copy, from SteamCMD's workshop manifest (0 when it does not say)."""
    from app.modmgr.mod_ops import get_mod_info_list
    from app.parsers.acf_parser import get_installed_timestamps, parse_acf_file

    try:
        installed = get_installed_timestamps(parse_acf_file(config.workshop_acf_path))
    except (OSError, ValueError):
        installed = {}
    return {m.name: installed.get(m.workshop_id, 0) for m in get_mod_info_list(config)}


def _enabled_mod_times(config: DayzConfig) -> dict[str, int]:
    disabled = modlist.disabled(config)
    return {m: ts for m, ts in installed_mod_times(config).items() if m not in disabled}


def snapshot(config: DayzConfig) -> UpdateSnapshot:
    """Take before an update runs; `game_updated` and `mods_updated` compare
    against it afterwards."""
    from app.ops.mission_stock import installed_buildid

    return UpdateSnapshot(
        taken_at=time.time(), build=installed_buildid(config), mods=_enabled_mod_times(config),
    )


def game_updated(before: UpdateSnapshot, config: DayzConfig) -> bool:
    """Did the update change the installed DayZ build? A first install is not
    an update: there was no build before it."""
    from app.ops.mission_stock import installed_buildid

    after = installed_buildid(config)
    return bool(before.build and after and after != before.build)


def mods_updated(before: UpdateSnapshot, config: DayzConfig) -> list[str]:
    """Enabled mods whose installed copy changed (or is new) since *before*."""
    return sorted(m for m, ts in _enabled_mod_times(config).items() if before.mods.get(m) != ts)


# --- watching a boot --------------------------------------------------------


def _boot_rpt(config: DayzConfig, since: float) -> Path | None:
    """The first log written after *since*: the oldest .RPT modified since then.

    The oldest, because under ``Restart=always`` a crashing server writes one
    log per attempt, and the first attempt is the one this boot is about.
    """
    from app.ops import log_analysis

    after = []
    for path in log_analysis.find_rpt_logs(config):
        try:
            if path.stat().st_mtime >= since:
                after.append(path)
        except OSError:
            continue
    return after[-1] if after else None


def crash_files(config: DayzConfig, since: float) -> list[Path]:
    """The ``crash_*.log`` and ``.mdmp`` files the engine wrote since *since*,
    oldest first."""
    from app.ops import log_analysis

    directory = log_analysis.profiles_dir(config)
    found = []
    try:
        entries = list(directory.iterdir())
    except OSError:
        return []
    for f in entries:
        name = f.name.lower()
        if not (name.endswith(".mdmp") or (name.startswith("crash_") and name.endswith(".log"))):
            continue
        try:
            stamp = f.stat().st_mtime
        except OSError:
            continue
        if stamp >= since:
            found.append((stamp, f))
    return [f for _, f in sorted(found)]


def _crash_evidence(config: DayzConfig, since: float, elapsed: float) -> str:
    """Why the server is known to have crashed since *since*, or "".

    A minidump is a crash. A crash log is one only once the process is gone:
    DayZ also writes a crash log for a script error it survives. The first
    boot on 1.30 of a world that an earlier boot without mods had saved
    logged 22,201 "Virtual Machine Exception" entries, all saved items it
    could not read, then took players, and this used to call it a crash.
    """
    from app.server import service_control

    files = crash_files(config, since)
    dumps = [f for f in files if f.suffix.lower() == ".mdmp"]
    if dumps:
        return f"The server crashed ({dumps[0].name})."
    stats = service_control.restart_stats(config)
    if stats.get("limit_hit"):
        return "The server kept crashing and systemd stopped restarting it."
    if stats.get("looping") or stats.get("count", 0) > 0:
        return "The server exited and systemd is restarting it."
    # A crash log proves the engine ran, so its absence needs no start grace.
    if (files or elapsed >= _START_GRACE) and not service_control.is_server_process_alive(config):
        return f"The server crashed ({files[0].name})." if files else "The server process exited."
    return ""


async def watch_boot(
    config: DayzConfig,
    since: float,
    *,
    emit: Emit | None = None,
    timeout: float | None = None,
    poll: float = _POLL,
) -> BootOutcome:
    """Wait for the server started at *since* to become joinable.

    Returns as soon as its log reaches "Player connect enabled" (JOINABLE), the
    server crashes (CRASHED: a minidump, a unit in auto-restart, or no process
    left, with or without a crash log), or *timeout* passes (NOT_JOINABLE; default
    `boot_timeout(config)`). The log is read through `log_analysis`, a few
    lines at a time.
    """
    from app.ops import log_analysis

    say = emit or (lambda _line: None)
    limit = boot_timeout(config) if timeout is None else timeout
    start = time.monotonic()
    rpt: Path | None = None
    offset = 0
    say(f"Waiting for the server to become joinable (up to {limit:.0f}s)...")
    while True:
        elapsed = time.monotonic() - start
        if rpt is None:
            rpt = await asyncio.to_thread(_boot_rpt, config, since)
        if rpt is not None:
            ready, offset = await asyncio.to_thread(log_analysis.find_ready, rpt, offset)
            if ready:
                say(f"The server is joinable ({elapsed:.0f}s after start).")
                return BootOutcome(JOINABLE, elapsed, rpt, "The server became joinable.")
        crash = await asyncio.to_thread(_crash_evidence, config, since, elapsed)
        if crash:
            if rpt is None:
                rpt = await asyncio.to_thread(_boot_rpt, config, since)
            say(crash)
            return BootOutcome(CRASHED, elapsed, rpt, crash)
        if elapsed >= limit:
            detail = f"The server was not joinable {limit:.0f}s after start."
            say(detail)
            return BootOutcome(NOT_JOINABLE, elapsed, rpt, detail)
        await asyncio.sleep(poll)


# --- naming the mods ----------------------------------------------------------

# The shapes a script error names its file in (measured, plans/73 step 0, and
# a crash log's stack trace from a 1.30 boot):
#   19:40:47 SCRIPT    (E): @"Plan73Broken/Scripts/4_World/plan73_broken.c,5": Unknown type '…'
#   Plan73Broken/Scripts/4_World/plan73_broken.c(5): Unknown type '…'
#   JM/CF/Scripts/4_World/communityframework/modstorage/cf_modstorageobject.c:138 Function OnStoreLoad_CF
# A stack frame of the game's own scripts (`scripts/…`) or of the mission
# (`$CurrentDir:mpmissions/…`) names no mod, so it is no error line here.
_RE_QUOTED_PATH = re.compile(r'@"([^"\n]+?\.c),\d+"')
_RE_COMPILE_PATH = re.compile(r'^\s*(?:\d{1,2}:\d{2}:\d{2}\s+)?([^\s"(@][^"(\n]*?\.c)\(\d+\):')
_RE_STACK_PATH = re.compile(r'^\s*(?!scripts/)([^\s"(@$][^"(\n:]*?\.c):\d+ Function \w')
_RE_MODULE = re.compile(r"(?:Can't|Cannot) compile\s+\"?([^\"\n]+?)\"? script module")
_RE_TIMESTAMP = re.compile(r"^\s*\d{1,2}:\d{2}:\d{2}\s+")

MAX_LINES_PER_MOD = 5
MAX_UNATTRIBUTED = 10


@dataclass
class Suspect:
    mod: str
    # The error lines that name a file of this mod, verbatim, at most MAX_LINES_PER_MOD.
    lines: list[str]
    workshop_id: str = ""
    # The Workshop time_updated of the installed copy, 0 when unknown.
    time_updated: int = 0
    # True when the installed copy is older than the DayZ build installed now:
    # its author has published nothing since this update. None when unknown.
    before_game_update: bool | None = None


@dataclass
class Attribution:
    suspects: list[Suspect] = field(default_factory=list)
    # Error lines that name a file no loaded mod's prefix covers.
    unattributed: list[str] = field(default_factory=list)
    # Script modules that did not compile ("World", "Mission", ...).
    modules: list[str] = field(default_factory=list)


def script_path(line: str) -> str | None:
    """The script file an error line names, or None for any other line."""
    if "(E)" in line:
        m = _RE_QUOTED_PATH.search(line)
        if m:
            return m.group(1)
    m = _RE_COMPILE_PATH.match(line) or _RE_STACK_PATH.match(line)
    return m.group(1) if m else None


def _scan(path: Path, modules: list[str]) -> list[tuple[str, str]]:
    """``(script path, line)`` for every error line in *path* that names a
    file; appends the modules that did not compile to *modules*."""
    from app.ops.log_analysis import MAX_LINES

    hits: list[tuple[str, str]] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for lineno, raw in enumerate(f, 1):
                if lineno > MAX_LINES:
                    break
                if "compile" in raw and (m := _RE_MODULE.search(raw)) and m.group(1) not in modules:
                    modules.append(m.group(1))
                if ".c" not in raw:
                    continue
                found = script_path(raw)
                if found:
                    hits.append((found, raw.rstrip()))
    except OSError:
        pass
    return hits


def owner(path: str, prefixes: dict[str, list[str]]) -> str | None:
    """The mod whose PBO prefix covers script *path*, longest prefix first;
    None when none does."""
    norm = path.replace("\\", "/").strip("/").lower()
    best: tuple[int, str] | None = None
    for mod, values in prefixes.items():
        for prefix in values:
            p = prefix.lower()
            if p and (norm == p or norm.startswith(p + "/")) and (best is None or len(p) > best[0]):
                best = (len(p), mod)
    return best[1] if best else None


def suspects(config: DayzConfig, rpt: Path | None, *, since: float) -> Attribution:
    """The mods the boot started at *since* failed on, from its log *rpt* and
    the first crash log written since then (which carries the cause in a few
    lines)."""
    from app.modmgr.mod_conflict import mod_pbo_prefixes
    from app.modmgr.mod_ops import get_mod_info_list
    from app.modmgr import modlist
    from app.ops import log_analysis
    from app.ops.mission_stock import installed_build_time

    result = Attribution()
    sources = [rpt] if rpt is not None else []
    sources += [f for f in crash_files(config, since) if f.suffix.lower() == ".log"][:1]
    hits: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for source in sources:
        for path, line in _scan(source, result.modules):
            key = (path.lower(), _RE_TIMESTAMP.sub("", line).strip())
            if key not in seen:
                seen.add(key)
                hits.append((path, line))
    if not hits:
        return result

    loaded = (log_analysis.launched_mod_names(rpt) if rpt is not None else None) or modlist.active(config)
    prefixes = mod_pbo_prefixes(config.server_dir_path, loaded)
    by_mod: dict[str, list[str]] = {}
    for path, line in hits:
        mod = owner(path, prefixes)
        if mod is None:
            if len(result.unattributed) < MAX_UNATTRIBUTED:
                result.unattributed.append(line)
        else:
            by_mod.setdefault(mod, []).append(line)

    ids = {m.name: m.workshop_id for m in get_mod_info_list(config)}
    times = installed_mod_times(config)
    build_time = installed_build_time(config)
    for mod, lines in by_mod.items():
        ts = times.get(mod, 0)
        result.suspects.append(Suspect(
            mod=mod,
            lines=lines[:MAX_LINES_PER_MOD],
            workshop_id=ids.get(mod, ""),
            time_updated=ts,
            before_game_update=(ts < build_time) if ts and build_time else None,
        ))
    return result


# --- the report -----------------------------------------------------------------


def load_report(config: DayzConfig) -> dict | None:
    """The last boot after an update, as `check_boot` saved it, or None."""
    try:
        data = json.loads(config.paths.post_update.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_report(config: DayzConfig, report: dict) -> None:
    from app.shared.json_utils import atomic_write_text

    atomic_write_text(config.paths.post_update, json.dumps(report, indent=2) + "\n")


def clear_report(config: DayzConfig) -> bool:
    try:
        config.paths.post_update.unlink()
    except FileNotFoundError:
        return False
    return True


def failed(report: dict | None) -> bool:
    """Did the boot *report* describes fail (crash, or never joinable)?"""
    return bool(report) and report.get("outcome") in (CRASHED, NOT_JOINABLE)


def occasion(report: dict) -> str:
    """"after the DayZ update (build A → B)", "after a mod update (@X)" or
    "after the update" (plans/73 D2: a mod-only update gets the same triage)."""
    if report.get("trigger") == "game":
        return f"after the DayZ update (build {report.get('build_before')} → {report.get('build_after')})"
    mods = report.get("mods_updated") or []
    if report.get("trigger") == "mods" and mods:
        names = ", ".join(mods[:3]) + (f" and {len(mods) - 3} more" if len(mods) > 3 else "")
        return f"after a mod update ({names})"
    return "after the update"


def _date(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "unknown"


# Saved items one boot could not read before the report warns, counted by
# log_analysis's storage rules. A mod update that changes what an item saves
# drops a few on its first boot, as expected. The first 1.30 boot of a world
# that an earlier boot without mods had saved dropped 21,527, took players,
# and only a false crash alarm said anything (release item 24).
WORLD_DAMAGE_ITEMS = 100
_UNREADABLE_RULES = ("storage_scripted_vars", "storage_items_failed")


def unreadable_items(rpt: Path | None) -> int:
    """How many saved items the boot that wrote *rpt* could not read.

    The larger of the log analyzer's two counts, since the item loader's
    "N failed" and the "Scripted variables corrupted" lines can name the
    same items.
    """
    from app.ops.log_analysis import analyze_rpt

    if rpt is None:
        return 0
    try:
        findings = analyze_rpt(rpt).get("findings", [])
    except OSError:
        return 0
    return max(
        sum(f.get("count", 0) for f in findings if f.get("id") == rule)
        for rule in _UNREADABLE_RULES
    )


def damage_lines(report: dict) -> list[str]:
    """The world-damage warning of a boot *report*, or nothing."""
    if not report.get("world_damage"):
        return []
    lines = [
        f"Warning: the server could not read {report.get('unreadable_items', 0)} saved items on "
        "this boot (bases, vehicles, gear). They leave the world at its next save."
    ]
    backup = report.get("backup")
    if backup:
        lines.append(
            f"To undo it, stop the server and restore the newest backup, {backup['name']} "
            f"({backup.get('timestamp', 'unknown time')})."
        )
    else:
        lines.append("No backup of this mission exists to restore.")
    return lines


def summary_lines(report: dict) -> list[str]:
    """The report in plain lines, markup-free (job logs render them raw)."""
    lines: list[str] = []
    if report.get("trigger") == "game":
        lines.append(f"DayZ updated: build {report.get('build_before')} → {report.get('build_after')}.")
    if report.get("outcome") == JOINABLE:
        lines.append(f"The server came back and is joinable ({report.get('seconds', 0)}s after start).")
        return lines + damage_lines(report)
    lines.append(f"The server did not come back {occasion(report)}. {report.get('detail', '')}".rstrip())
    for suspect in report.get("suspects") or []:
        note = ""
        if suspect.get("before_game_update"):
            note = f" (installed copy from {_date(suspect.get('time_updated', 0))}, older than this DayZ build)"
        lines.append(f"Suspect: {suspect['mod']}{note}")
        lines.extend(f"    {line}" for line in suspect.get("lines", []))
    if report.get("unattributed"):
        lines.append("Errors no installed mod's files match:")
        lines.extend(f"    {line}" for line in report["unattributed"])
    if not report.get("suspects") and not report.get("unattributed"):
        where = f" ({report['rpt']})" if report.get("rpt") else ""
        lines.append(f"The server log names no script file{where}; see Analysis → Server logs.")
    return lines + damage_lines(report)


def _build_report(config: DayzConfig, before: UpdateSnapshot, outcome: BootOutcome, since: float) -> dict:
    from app.ops.mission_stock import installed_buildid

    build_after = installed_buildid(config)
    changed = mods_updated(before, config)
    game = bool(before.build and build_after and build_after != before.build)
    report = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "started": before.taken_at,
        "since": since,
        "trigger": "game" if game else "mods" if changed else "",
        "build_before": before.build,
        "build_after": build_after,
        "mods_updated": changed,
        "outcome": outcome.status,
        "seconds": round(outcome.seconds),
        "detail": outcome.detail,
        "rpt": outcome.rpt.name if outcome.rpt else None,
        "suspects": [],
        "unattributed": [],
        "modules": [],
        "set_aside": [],
        "recheck": None,
    }
    if outcome.status != JOINABLE:
        found = suspects(config, outcome.rpt, since=since)
        report["suspects"] = [asdict(s) for s in found.suspects]
        report["unattributed"] = found.unattributed
        report["modules"] = found.modules
    lost = unreadable_items(outcome.rpt)
    report["unreadable_items"] = lost
    report["world_damage"] = lost >= WORLD_DAMAGE_ITEMS
    report["backup"] = latest_backup(config, before.taken_at) if report["world_damage"] else None
    return report


def _notify_failed(config: DayzConfig, report: dict) -> None:
    from app.monitoring.discord_hooks import notify_boot_failed

    lines = [line for s in report.get("suspects", []) for line in s.get("lines", [])[:2]]
    lines += report.get("unattributed", [])[:3]
    try:
        notify_boot_failed(
            config,
            occasion=occasion(report),
            detail=report.get("detail", ""),
            suspects=[s["mod"] for s in report.get("suspects", [])],
            lines=lines,
        )
    except Exception:  # noqa: BLE001 - a notification never fails the update
        pass


def _notify_damage(config: DayzConfig, report: dict) -> None:
    from app.monitoring.discord_hooks import notify_world_damage

    try:
        notify_world_damage(
            config,
            occasion=occasion(report),
            items=report.get("unreadable_items", 0),
            backup=(report.get("backup") or {}).get("name", ""),
        )
    except Exception:  # noqa: BLE001 - a notification never fails the update
        pass


async def check_boot(
    config: DayzConfig,
    before: UpdateSnapshot,
    *,
    since: float,
    emit: Emit | None = None,
    timeout: float | None = None,
) -> dict:
    """Watch the boot started at *since*, name the suspects if it failed, save
    the report, and send one Discord message for a failed boot or for one
    that could not read many saved items."""
    say = emit or (lambda _line: None)
    outcome = await watch_boot(config, since, emit=say, timeout=timeout)
    report = await asyncio.to_thread(_build_report, config, before, outcome, since)
    await asyncio.to_thread(save_report, config, report)
    for line in summary_lines(report):
        say(line)
    if failed(report):
        await asyncio.to_thread(_notify_failed, config, report)
    elif report.get("world_damage"):
        await asyncio.to_thread(_notify_damage, config, report)
    return report


async def start_and_check(
    config: DayzConfig,
    before: UpdateSnapshot,
    *,
    emit: Emit | None = None,
    timeout: float | None = None,
) -> dict:
    """Start the server, then `check_boot`. A start that fails is returned as
    ``{"outcome": "not_started", "detail": ...}`` and not saved: it says
    nothing about the mods."""
    from app.server import service_control

    say = emit or (lambda _line: None)
    say("Starting the server...")
    since = time.time()
    start = await asyncio.to_thread(service_control.start_server, config, anyway=True)
    if not start.ok:
        say(f"Could not start the server: {start.message}")
        return {"outcome": "not_started", "detail": start.message}
    return await check_boot(config, before, since=since, emit=say, timeout=timeout)


# --- the offer (never applied without the admin's yes) --------------------------

# Measured in plans/73 step 0: one boot of the test container's server without
# 23 of its 25 mods could not load 1,334 saved objects, and the next boot no
# longer had them in storage at all.
PERSISTENCE_WARNING = (
    "A server that boots without a mod deletes that mod's saved objects as soon as "
    "it saves: bases built from its parts, its vehicles, the items in its containers. "
    "On a test server, one boot without 23 mods lost 1,334 objects for good. A backup "
    "taken before you set them aside is the only way back."
)
BACKUP_NOTE = "before setting mods aside after an update"


def offerable(report: dict | None) -> list[str]:
    """The suspects of the last failed boot (the re-check's, after one) that
    have not been set aside yet."""
    if not report:
        return []
    names: list[str] = []
    recheck = report.get("recheck") or {}
    for source in (report, recheck if failed(recheck) else {}):
        for suspect in source.get("suspects") or []:
            if suspect["mod"] not in names:
                names.append(suspect["mod"])
    done = set(report.get("set_aside") or [])
    return [n for n in names if n not in done]


def dependents(config: DayzConfig, mods: list[str]) -> list[str]:
    """Enabled mods that need one of *mods*, directly or through another one.
    Disabling a framework takes them down too, so the offer names them and
    sets them aside together or not at all."""
    from app.modmgr.mod_conflict import get_mod_dependencies
    from app.modmgr import modlist

    active = modlist.active(config)
    # Every provider: the frameworks an update breaks first are CF and
    # Expansion, and the load order's graph skips their addon names.
    graph = get_mod_dependencies(
        active, config.server_dir_path, modlist.workshop_ids(config),
        every_provider=True,
    )
    down = set(mods)
    grew = True
    while grew:
        grew = False
        for mod, needs in graph.items():
            if mod not in down and needs & down:
                down.add(mod)
                grew = True
    return [m for m in active if m in down and m not in mods]


def latest_backup(config: DayzConfig, since: float) -> dict | None:
    """The newest backup of the current mission, and whether it was taken at or
    after *since* (the start of the update: storage has not changed since)."""
    from app.modmgr.mods import get_current_mission
    from app.ops import backup_ops

    backups = backup_ops.list_backups(config, get_current_mission(config.server_cfg_path))
    if not backups:
        return None
    newest = backups[0]
    try:
        taken = datetime.fromisoformat(newest.metadata.timestamp).timestamp()
    except ValueError:
        taken = 0.0
    return {
        "name": newest.zip_path.name,
        "timestamp": newest.metadata.timestamp,
        "fresh": bool(since) and taken >= since,
    }


def offer(config: DayzConfig, report: dict | None, mods: list[str] | None = None) -> dict:
    """What setting *mods* aside would do (default: everything `offerable`),
    for a surface to show before it asks. Only enabled mods are kept."""
    from app.modmgr import modlist

    active = set(modlist.active(config))
    names = [m for m in (offerable(report) if mods is None else mods) if m in active]
    return {
        "mods": names,
        "dependents": dependents(config, names) if names else [],
        "backup": latest_backup(config, (report or {}).get("started", 0)),
        "warning": PERSISTENCE_WARNING,
    }


async def set_aside(
    config: DayzConfig,
    mods: list[str],
    *,
    mutate: Callable[[Callable[[DayzConfig], object]], Awaitable[DayzConfig]],
    regenerate: Callable[[DayzConfig], Awaitable[object]],
    emit: Emit | None = None,
    backup_first: bool = True,
) -> dict:
    """Disable *mods* and every enabled mod that needs them, record why in
    ``update_triage``, restart the server and watch that boot. Once: zsm never
    goes on to disable another mod by itself.

    Only for after the admin said yes. *mutate* is the surface's config write
    (the CLI saves, the web goes through ``WebState.mutate_config``) and
    *regenerate* its unit rebuild, the same two its Mods toggle uses.
    """
    from app.modmgr.mods import apply_mod_state_to_mission_xml, mark_disabled
    from app.ops import backup_ops
    from app.ops.mission_stock import installed_buildid
    from app.server import service_control

    say = emit or (lambda _line: None)
    report = await asyncio.to_thread(load_report, config) or {}
    plan = await asyncio.to_thread(offer, config, report, mods)
    targets = plan["mods"] + plan["dependents"]
    if not targets:
        raise ValueError("None of those mods is enabled.")
    if backup_first:
        say("Taking a backup before setting mods aside...")
        await asyncio.to_thread(
            backup_ops.create_backup, config, backup_ops.BackupContents(), BACKUP_NOTE, say,
        )

    build = report.get("build_after") or await asyncio.to_thread(installed_buildid, config)
    times = await asyncio.to_thread(installed_mod_times, config)
    at = datetime.now().isoformat(timespec="seconds")

    def _apply(cfg: DayzConfig) -> None:
        for mod in targets:
            mark_disabled(cfg, mod)
            cfg.update_triage[mod] = {"build": build, "time_updated": times.get(mod, 0), "at": at}

    config = await mutate(_apply)
    for mod in targets:
        await asyncio.to_thread(apply_mod_state_to_mission_xml, config, mod, enable=False)
        say(f"Set aside {mod}.")
    await regenerate(config)

    say("Restarting the server without them...")
    # Stopped before `since` is taken, as every other boot check does: a server
    # that is still up writes its last lines while it stops, and its log would
    # then be the oldest one since, the one `watch_boot` takes for the new boot.
    restart = await asyncio.to_thread(service_control.stop_server, config)
    since = time.time()
    if restart.ok:
        restart = await asyncio.to_thread(service_control.start_server, config, anyway=True)
    if restart.ok:
        outcome = await watch_boot(config, since, emit=say)
        recheck: dict = {
            "outcome": outcome.status,
            "seconds": round(outcome.seconds),
            "detail": outcome.detail,
            "rpt": outcome.rpt.name if outcome.rpt else None,
        }
        if outcome.status != JOINABLE:
            found = await asyncio.to_thread(suspects, config, outcome.rpt, since=since)
            recheck["suspects"] = [asdict(s) for s in found.suspects]
            recheck["unattributed"] = found.unattributed
    else:
        say(f"Could not restart the server: {restart.message}")
        recheck = {"outcome": "not_started", "detail": restart.message}

    report["set_aside"] = list(dict.fromkeys([*(report.get("set_aside") or []), *targets]))
    report["recheck"] = recheck
    await asyncio.to_thread(save_report, config, report)
    if recheck["outcome"] == JOINABLE:
        say("The server is joinable without them.")
    elif failed(recheck):
        say("The server still did not come back. Zedstead does not set more mods aside on its own.")
        for suspect in recheck.get("suspects", []):
            say(f"Suspect: {suspect['mod']}")
    return report


# --- bringing them back (notify only, plans/73 D1) ------------------------------


def _remote_times(config: DayzConfig) -> dict[str, int]:
    """Workshop id -> remote time_updated, from the last saved update check."""
    from app.modmgr.workshop_api import load_detected_updates

    try:
        return {u.mod_id: u.remote_updated for u in load_detected_updates(config.paths.mod_updates)}
    except Exception:  # noqa: BLE001 - an unreadable check is no check
        return {}


def triage_entries(config: DayzConfig, remote: dict[str, int] | None = None) -> list[dict]:
    """The mods set aside after an update, and whether their author has
    published since: a newer `time_updated` on the installed copy (a later
    download) or in the Workshop check (*remote*, default the last saved one).
    ``updated_at`` is that newer time, 0 when there is none or the time at
    which the mod was set aside is not known."""
    if not config.update_triage:
        return []
    from app.modmgr.mod_ops import get_mod_info_list

    ids = {m.name: m.workshop_id for m in get_mod_info_list(config)}
    local = installed_mod_times(config)
    if remote is None:
        remote = _remote_times(config)
    disabled = modlist.disabled(config)
    out = []
    for mod, entry in config.update_triage.items():
        wid = ids.get(mod, "")
        try:
            recorded = int(entry.get("time_updated", 0) or 0)
        except (TypeError, ValueError):
            recorded = 0
        newest = max(local.get(mod, 0), remote.get(wid, 0) if wid else 0)
        out.append({
            "mod": mod,
            "workshop_id": wid,
            "build": str(entry.get("build", "")),
            "at": str(entry.get("at", "")),
            "time_updated": recorded,
            "updated_at": newest if recorded and newest > recorded else 0,
            "notified": int(entry.get("notified", 0) or 0),
            "disabled": mod in disabled,
        })
    return out


def newly_updated(config: DayzConfig, remote: dict[str, int] | None = None) -> list[dict]:
    """`triage_entries` whose author has published since and that nobody has
    been told about yet (``notified`` below that time). The caller says so and
    records ``notified`` with `mark_notified`."""
    return [e for e in triage_entries(config, remote) if e["updated_at"] and e["notified"] < e["updated_at"]]


def mark_notified(cfg: DayzConfig, entries: list[dict]) -> None:
    """Record that *entries* were announced, so each update is said once.
    Changes *cfg* in place and does not save (a `mutate` callback)."""
    for e in entries:
        if e["mod"] in cfg.update_triage:
            cfg.update_triage[e["mod"]]["notified"] = e["updated_at"]


def triage_update_line(entry: dict) -> str:
    """"@X has updated since DayZ build N; enable it again?" (D1: an offer)."""
    return f"{entry['mod']} has updated since DayZ build {entry['build'] or '?'}; enable it again?"
