"""AppContext — wired to click.Context.obj for all commands."""

from __future__ import annotations

import os
from dataclasses import dataclass

import click

from app.shared.cli_ui import print_error, print_warning
from app.shared.config import ConfigNotFound, DayzConfig
from app.shared.platform import IS_WINDOWS


@dataclass
class AppContext:
    config: DayzConfig
    non_interactive: bool = False
    verbose: bool = False


def _sudo_note() -> str | None:
    """Explain a missing config that is only missing because of sudo.

    `sudo zsm ...` runs as root with root's HOME, so the config dir is
    /root/.config/zsm and the caller's own config is invisible.
    Nothing in zsm wants to be launched as root — the systemd, firewall and
    package paths escalate per command — so this is always the wrong way round.
    """
    if IS_WINDOWS or not os.environ.get("SUDO_USER"):
        return None
    return (
        f"You are running under sudo, so that path is root's — "
        f"{os.environ['SUDO_USER']}'s config was not read. Re-run without sudo; "
        f"Zedstead asks for sudo itself for the steps that need root."
    )


def report_missing_config(exc: ConfigNotFound) -> None:
    """Render an unconfigured install as an error, not a traceback.

    Not having run `zsm init` yet is the ordinary state of a fresh box, and it
    is the state every subcommand meets first. `app/__main__.py` calls this from
    the root group so it covers all of them — including the ~38 commands that
    call `DayzConfig.load()` directly rather than through `load_context`.
    """
    print_error(str(exc))
    note = _sudo_note()
    if note:
        print_warning(note)


def load_context(ctx: click.Context) -> tuple[DayzConfig, bool]:
    """Load config from disk and extract the non_interactive flag from ctx.obj."""
    return DayzConfig.load(), ctx.obj.get("non_interactive", False)
