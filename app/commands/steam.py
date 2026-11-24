"""zsm steam-login — authenticate SteamCMD with the configured account.

Built for the case it is nearly always run in: someone on an SSH session,
first run, with Steam Guard on. Three things go wrong there, and each has an
answer here rather than a note in the docs:

* the Guard prompt arrives with no explanation — so the preamble in
  ``app/shared/steam.py::LOGIN_PREAMBLE_LINES`` is printed first, shared with
  ``zsm init`` because that is the same first run;
* people log in again when they did not need to — so a cached login is
  detected and reported instead of being replaced;
* a failure says only "failed" — so it is classified through
  ``diagnose_login_output``, the same classifier the web has used since
  2026 Q3. Nothing here writes new diagnostics.
"""

from __future__ import annotations

import subprocess

import click
from rich.console import Console

from app.shared.config import DayzConfig
from app.shared.steam import (
    LOGIN_TIMEOUT,
    SteamLoginTimeout,
    check_steam_login,
    probe_login_failure,
    steam_login,
    steam_username_problem,
)
from app.shared.cli_ui import print_error, print_success, print_warning

console = Console()

_FACTOR_HINT = {
    "mobile": (
        "Steam wants a code from the Steam mobile app (the authenticator). "
        "No email is sent for those accounts."
    ),
    "email": "Steam wants the Steam Guard code it emailed for this account.",
}


@click.command(name="steam-login")
@click.option(
    "--username",
    default="",
    help="Steam account to log in as. Saved to the config once the login "
    "succeeds, the same as the web login does.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Log in even if the cached login still works.",
)
@click.option(
    "--timeout",
    type=int,
    default=LOGIN_TIMEOUT,
    show_default=True,
    help="Seconds to wait at SteamCMD's prompt before giving the terminal "
    "back. 0 waits forever.",
)
@click.pass_context
def steam_login_cmd(ctx, username: str, force: bool, timeout: int):
    """Run a Steam login pass via SteamCMD using the configured username."""
    config = DayzConfig.load()

    username = username.strip()
    if username:
        config.steam_username = username
    if not config.steam_username:
        print_error(
            "No Steam username configured. Pass --username, or set it with "
            "'zsm settings set steam_username <name>'."
        )
        ctx.exit(1)
    problem = steam_username_problem(config.steam_username)
    if problem:
        print_error(problem)
        console.print("[dim]Pass it with --username <name>.[/dim]")
        ctx.exit(1)
    if not config.steamcmd_path.is_file():
        print_error(
            f"SteamCMD is not installed at {config.steamcmd_path}. "
            "Run 'zsm setup' to install it."
        )
        ctx.exit(1)

    # The commonest reason to run this is a suspicion, not a known problem.
    # Answering it without an interactive login also means not spending a
    # Steam Guard code to find out. --force is the way past it.
    if not force:
        console.print("[dim]Checking whether the cached login still works...[/dim]")
        if check_steam_login(config):
            print_success(f"Already logged in as {config.steam_username}.")
            console.print("[dim]Use --force to log in again anyway.[/dim]")
            _say_next_step(config)
            return

    try:
        steam_login(config, timeout=timeout or None)
    except SteamLoginTimeout as e:
        print_error(str(e))
        ctx.exit(1)
    except subprocess.CalledProcessError:
        # steam_login has said that it failed, under SteamCMD's own reason. The
        # exception's text is the command and its exit status.
        _explain_failure(config)
        ctx.exit(1)
    except Exception as e:
        print_error(f"Steam login failed: {e}")
        _explain_failure(config)
        ctx.exit(1)

    if username:
        config.save()
    print_success("Steam login successful.")
    _say_next_step(config)


def _say_next_step(config: DayzConfig) -> None:
    """Name `zsm setup` while there is no DayZ server yet: after a login that
    failed inside the wizard, this command is where a newcomer lands, and
    nothing told them how to get the server (first-hour test, 2026 Q4)."""
    from app.commands.doctor import server_is_installed
    from app.shared.systemd_scope import zsm_command

    if not server_is_installed(config):
        console.print(
            f"[dim]The DayZ server is not installed yet. Download it with: "
            f"{zsm_command(config, 'setup')}[/dim]"
        )


def _explain_failure(config: DayzConfig) -> None:
    """Say what is actually wrong, when SteamCMD's own output can say.

    The interactive attempt printed straight to the terminal, so this asks
    again non-interactively and classifies *that* — see `probe_login_failure`.
    It describes the account now rather than that exact attempt, which is why
    the wording below hedges, and why nothing is printed when the classifier
    has no opinion: a confident guess would be worse than the plain failure.
    """
    console.print("[dim]Asking Steam what is wrong with the account...[/dim]")
    try:
        diagnosis = probe_login_failure(config)
    except Exception:  # a diagnosis is a nicety; never let it mask the failure
        return
    if diagnosis.error:
        print_warning(diagnosis.error)
    elif diagnosis.factor:
        print_warning(_FACTOR_HINT[diagnosis.factor])
        console.print("[dim]Run 'zsm steam-login' again with that code ready.[/dim]")


__all__ = ["steam_login_cmd"]
