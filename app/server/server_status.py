"""Cross-platform server uptime, next-restart, and runtime metrics."""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.shared.config import DayzConfig

from app.shared.platform import IS_WINDOWS
from app.xml.xml_utils import load_xml

# On Windows the start time, memory and CPU come from one PowerShell call that
# the status tick's three readers share. Before, each reader started its own
# process (two PowerShell and a wmic) every 5 s, which on a slow machine
# competed with the browser for the CPU (2026 Q4, Windows 11 VM).
# `-NoProfile` skips the user's profile script, and InvariantCulture keeps the
# decimal point a point on a Swedish or German Windows.
_WIN_PROCESS_SCRIPT = (
    "$p = Get-Process DayZServer_x64 -ErrorAction SilentlyContinue | Select-Object -First 1; "
    "if ($p) { [string]::Format([cultureinfo]::InvariantCulture, '{0}|{1}|{2}', "
    "$p.StartTime.ToString('s'), $p.WorkingSet64, $p.TotalProcessorTime.TotalSeconds) }"
)
# One status tick calls the three readers within milliseconds, so a snapshot
# younger than this is the same tick's.
_WIN_SNAPSHOT_TTL = 2.0
_win_snapshot: dict = {"at": None, "value": None}
# The CPU seconds and the clock at the previous reading, for a percentage over
# the time between two ticks.
_win_cpu_last: dict = {"cpu": None, "at": None}


def _windows_process_snapshot() -> dict | None:
    """``{start, working_set, cpu_seconds}`` for the server process, or None."""
    import time

    now = time.monotonic()
    if _win_snapshot["at"] is not None and now - _win_snapshot["at"] < _WIN_SNAPSHOT_TTL:
        return _win_snapshot["value"]
    value = None
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _WIN_PROCESS_SCRIPT],
            capture_output=True, text=True, timeout=5,
        )
        parts = result.stdout.strip().split("|")
        if result.returncode == 0 and len(parts) == 3:
            value = {
                "start": datetime.fromisoformat(parts[0]),
                "working_set": int(parts[1]),
                "cpu_seconds": float(parts[2]),
            }
    except (OSError, subprocess.SubprocessError, ValueError):
        value = None
    _win_snapshot["at"] = now
    _win_snapshot["value"] = value
    return value


def _format_kb(kb: int) -> str:
    if kb >= 1048576:
        return f"{kb / 1048576:.1f} GB"
    return f"{kb // 1024} MB"


def get_server_start_time(config: DayzConfig | None = None) -> datetime | None:
    """Return the server process start time, or None on failure."""
    from app.shared.platform import IS_WINDOWS

    try:
        if IS_WINDOWS:
            snap = _windows_process_snapshot()
            return snap["start"] if snap else None
        else:
            from app.shared.systemd_scope import get_scope, server_unit

            scope = get_scope(config)
            out = scope.query(
                "show", server_unit(config), "--property=ActiveState,ActiveEnterTimestamp",
            )
            props = dict(
                line.split("=", 1) for line in out.splitlines() if "=" in line
            )
            # A stopped or failed unit keeps its last ActiveEnterTimestamp, so
            # without this the dashboard's uptime kept counting on a dead
            # server (test pass 1, F1). "reloading" is still up.
            if props.get("ActiveState", "").strip() not in ("active", "reloading"):
                return None
            # "Mon 2026-10-19 10:00:00 CEST", in this machine's own zone.
            # The zone is dropped, never parsed: strptime's %Z knows only UTC
            # and this machine's names, and systemd writes "-03" for a zone
            # with no abbreviation, so a server in Sao Paulo had no uptime.
            ts_str = " ".join(props.get("ActiveEnterTimestamp", "").split()[:3])
            if ts_str:
                return datetime.strptime(ts_str, "%a %Y-%m-%d %H:%M:%S")
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return None


def get_server_uptime(config: DayzConfig | None = None) -> timedelta | None:
    """Return how long the server has been running, or None."""
    start = get_server_start_time(config)
    if start is None:
        return None
    return datetime.now() - start


def get_shutdown_deadline(config: DayzConfig) -> int | None:
    """Return the shutdown deadline (minutes) from messages.xml, or None."""
    try:
        from app.modmgr.mods import get_current_mission
        import xml.etree.ElementTree as ET

        mission = get_current_mission(config.server_cfg_path)
        if not mission:
            return None

        msg_path = config.server_dir_path / "mpmissions" / mission / "db" / "messages.xml"
        if not msg_path.is_file():
            return None

        tree = load_xml(msg_path)
        for msg in tree.findall(".//message"):
            shutdown_el = msg.find("shutdown")
            if shutdown_el is not None and (shutdown_el.text or "").strip() == "1":
                deadline_el = msg.find("deadline")
                if deadline_el is not None:
                    val = int((deadline_el.text or "0").strip())
                    if val > 0:
                        return val
    except (OSError, ValueError, ET.ParseError):
        pass  # a file that cannot be read or parsed sets no deadline
    return None


def get_steam_query_port(config: DayzConfig) -> int:
    """Return the steam query port from serverDZ.cfg, fallback to 27016."""
    try:
        from app.parsers.servercfg import read_cfg_value

        raw = read_cfg_value(config.server_cfg_path, "steamQueryPort")
        if raw is not None:
            return int(raw)
    except (OSError, ValueError):
        pass
    return 27016


def get_next_restart(config: DayzConfig, uptime: timedelta | None) -> tuple[int, int] | None:
    """``(minutes until the next restart, minutes in its cycle)``, or None.

    The one answer for the restart warnings (``WebNotifier._get_remaining_and_deadline``),
    the Next Restart tile (``collect_status``) and the Discord status post. The
    tile read only messages.xml and showed "—" beside an enabled restart task,
    the bug the warnings had before TODO C3b.

    A scheduled ``restart`` or ``update`` task wins (``scheduler.next_restart_occurrence``):
    its timer is what restarts the server on a zsm install. messages.xml's
    shutdown ``<deadline>`` is the fallback, for a server that restarts itself
    on the engine's countdown. That countdown starts with the server, so it
    needs *uptime* and has no answer for a stopped one. The caller passes the
    uptime it already measured, since measuring it is a ``systemctl`` call.
    """
    from app.server.scheduler import cron_now, next_restart_occurrence

    now = cron_now()  # one clock for both halves of the subtraction
    occurrence = next_restart_occurrence(config, now=now)
    if occurrence is not None:
        when, cycle = occurrence
        return max(0, int((when - now).total_seconds() / 60)), cycle
    if uptime is None:
        return None
    deadline = get_shutdown_deadline(config)
    if deadline is None:
        return None
    elapsed_min = uptime.total_seconds() / 60
    return int(deadline - elapsed_min % deadline), deadline


def format_uptime(delta: timedelta) -> str:
    """Format a timedelta as 'Xh YYm'."""
    total_seconds = int(delta.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    return f"{hours}h {minutes:02d}m"


def format_next_restart(minutes_left: int) -> str:
    """Minutes until the next restart as 'in Xh YYm'."""
    hours, minutes = divmod(minutes_left, 60)
    if hours > 0:
        return f"in {hours}h {minutes:02d}m"
    return f"in {minutes}m"


# ---------------------------------------------------------------------------
# Runtime metrics (read from .RPT files / process info)
# ---------------------------------------------------------------------------


def read_server_version(profiles_dir: Path) -> str | None:
    """Read the server version from the newest .RPT file header."""
    try:
        rpt_files = sorted(
            profiles_dir.glob("*.RPT"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        if not rpt_files:
            return None
        with open(rpt_files[0], "r", errors="ignore") as f:
            for i, line in enumerate(f):
                if i > 20:
                    break
                if line.startswith("Version "):
                    return line.strip().split("Version ", 1)[1]
    except OSError:
        pass
    return None


def read_server_memory(
    _profiles_dir: Path | None = None, config: DayzConfig | None = None
) -> str | None:
    """Read DayZ server memory usage from the process directly."""
    try:
        if IS_WINDOWS:
            snap = _windows_process_snapshot()
            return _format_kb(snap["working_set"] // 1024) if snap else None
        else:
            from app.shared.systemd_scope import get_scope, server_unit

            scope = get_scope(config)
            pid_line = scope.query(
                "show", server_unit(config), "--property=MainPID"
            ).strip()
            if "=" in pid_line:
                pid = pid_line.split("=", 1)[1].strip()
                if pid and pid != "0":
                    result = subprocess.run(
                        ["ps", "-p", pid, "-o", "rss", "--no-headers"],
                        capture_output=True, text=True, timeout=5,
                    )
                    val = result.stdout.strip()
                    if val:
                        kb = int(val)
                        if kb >= 1048576:
                            return f"{kb / 1048576:.1f} GB"
                        return f"{kb // 1024} MB"
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return None


def read_server_cpu(config: DayzConfig | None = None) -> str | None:
    """Read the DayZ server process CPU usage."""
    try:
        if IS_WINDOWS:
            return _windows_cpu_percent()
        else:
            from app.shared.systemd_scope import get_scope, server_unit

            scope = get_scope(config)
            pid_line = scope.query(
                "show", server_unit(config), "--property=MainPID"
            ).strip()
            if "=" in pid_line:
                pid = pid_line.split("=", 1)[1].strip()
                if pid and pid != "0":
                    result = subprocess.run(
                        ["ps", "-p", pid, "-o", "pcpu", "--no-headers"],
                        capture_output=True, text=True, timeout=5,
                    )
                    val = result.stdout.strip()
                    if val:
                        import os
                        cores = os.cpu_count() or 1
                        pct = float(val) / cores
                        return f"{pct:.0f}%"
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return None


def _windows_cpu_percent() -> str | None:
    """The server's CPU use since the previous reading, as a share of all cores.

    The first reading has no previous one, so it is the average since the
    process started. wmic, which this used before, is not on a current
    Windows 11 at all.
    """
    import os
    import time

    snap = _windows_process_snapshot()
    if snap is None:
        _win_cpu_last.update(cpu=None, at=None)
        return None
    now = time.monotonic()
    cores = os.cpu_count() or 1
    last_cpu, last_at = _win_cpu_last["cpu"], _win_cpu_last["at"]
    if last_cpu is not None and last_at is not None and now > last_at and snap["cpu_seconds"] >= last_cpu:
        used, wall = snap["cpu_seconds"] - last_cpu, now - last_at
    else:
        used = snap["cpu_seconds"]
        wall = (datetime.now() - snap["start"]).total_seconds()
    _win_cpu_last.update(cpu=snap["cpu_seconds"], at=now)
    if wall <= 0:
        return None
    return f"{min(100.0, used / wall / cores * 100):.0f}%"


def read_server_fps(profiles_dir: Path) -> str | None:
    """Read the latest FPS value from the newest .RPT file in profiles."""
    try:
        rpt_files = sorted(
            profiles_dir.glob("*.RPT"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        if not rpt_files:
            return None
        # Read last 8KB to find the most recent FPS line
        rpt = rpt_files[0]
        size = rpt.stat().st_size
        with open(rpt, "r", errors="ignore") as f:
            if size > 8192:
                f.seek(size - 8192)
                f.readline()  # discard partial line
            fps_val = None
            for line in f:
                if "Average server FPS: " in line:
                    try:
                        fps_val = line.split("Average server FPS: ", 1)[1].split()[0]
                    except (IndexError, ValueError):
                        pass
            if fps_val:
                # "2419.00" → "2419"
                try:
                    return str(int(float(fps_val)))
                except ValueError:
                    return fps_val
    except OSError:
        pass
    return None
