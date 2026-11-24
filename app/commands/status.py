"""zsm status — live tail of the active DayZ server's .RPT log.

Cross-platform Python tail loop: opens the newest `.RPT` under
`<server_dir>/<profiles_path>/`, prints each new line as the server writes
it, and re-opens the file when the server rotates the log (start of a new
session creates a new `.RPT`). Ctrl+C ends the tail cleanly.
"""

from __future__ import annotations

import time
from pathlib import Path

import click
from rich.console import Console

from app.modmgr.mod_conflict import newest_rpt
from app.shared.config import DayzConfig
from app.shared.cli_ui import print_error, print_warning

console = Console()

_POLL_INTERVAL = 0.5  # seconds between read attempts when at EOF
_ROTATE_CHECK_EVERY = 6  # poll cycles between rotation checks (~3 s)


def _profiles_dir(config: DayzConfig) -> Path:
    return config.server_dir_path / config.profiles_path


def _wait_for_rpt(profiles_dir: Path) -> Path | None:
    """Block until a .RPT exists or Ctrl+C. Returns the path or None on Ctrl+C."""
    console.print("[dim]Waiting for the server to start writing a .RPT log…[/dim]")
    while True:
        rpt = newest_rpt(profiles_dir)
        if rpt is not None:
            return rpt
        try:
            time.sleep(_POLL_INTERVAL * 4)
        except KeyboardInterrupt:
            return None


def _tail_file(path: Path, profiles_dir: Path) -> Path | None:
    """Tail `path` until it rotates (a newer .RPT appears). Returns the new
    path to switch to, or None on Ctrl+C / unrecoverable error."""
    console.print(f"[dim]Tailing {path}[/dim]")
    try:
        fh = path.open("r", encoding="utf-8", errors="replace")
    except OSError as e:
        print_error(f"Could not open {path}: {e}")
        return None

    cycles = 0
    try:
        with fh:
            for line in fh:
                console.out(line.rstrip("\n"))
            while True:
                line = fh.readline()
                if line:
                    console.out(line.rstrip("\n"))
                    continue
                time.sleep(_POLL_INTERVAL)
                cycles += 1
                if cycles >= _ROTATE_CHECK_EVERY:
                    cycles = 0
                    newer = newest_rpt(profiles_dir)
                    if newer is not None and newer != path:
                        console.print(f"[dim]Log rotated → {newer.name}[/dim]")
                        return newer
    except KeyboardInterrupt:
        return None


@click.command(name="status")
@click.pass_context
def status_cmd(ctx):
    """Live tail of the server's newest .RPT log. Ctrl+C to exit."""
    try:
        config = DayzConfig.load()
    except FileNotFoundError as e:
        print_error(str(e))
        ctx.exit(1)

    profiles_dir = _profiles_dir(config)
    if not profiles_dir.is_dir():
        print_warning(f"Profiles directory not found: {profiles_dir}")
        ctx.exit(1)

    current = newest_rpt(profiles_dir) or _wait_for_rpt(profiles_dir)
    if current is None:
        return

    while current is not None:
        current = _tail_file(current, profiles_dir)
