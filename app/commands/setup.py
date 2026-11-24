"""zsm setup — non-interactive bootstrap for headless / automated installs.

The interactive wizard is `zsm init`. This command does the same install work
without any prompts, reading paths from the existing config, so it can run in a
container or CI. It is idempotent: anything already present is left alone.
"""

from __future__ import annotations

import click
from rich.console import Console

from app.commands.context import load_context
from app.shared.platform import IS_WINDOWS
from app.shared.steam import check_steam_login, update_dayz_server
from app.shared.cli_ui import (
    print_error,
    print_header,
    print_step,
    print_success,
    print_warning,
)

console = Console()


@click.command()
@click.pass_context
def setup(ctx):
    """Non-interactive bootstrap: install the server.

    Idempotent. Creates the server directory, then installs the DayZ server
    via SteamCMD when a Steam token is cached and the server isn't installed
    yet; otherwise it skips the install and tells you to run `zsm
    steam-login` first.
    """
    config, _ni = load_context(ctx)
    print_header("zsm setup")

    server_dir = config.server_dir_path
    server_dir.mkdir(parents=True, exist_ok=True)

    # Install the server — only if a Steam token is cached and it's missing.
    binary = "DayZServer_x64.exe" if IS_WINDOWS else "DayZServer"
    if (server_dir / binary).exists():
        print_success("DayZ server already installed.")
        apply_query_port(config)
        return

    if not check_steam_login(config):
        print_warning(
            "No cached Steam login — skipping server install. "
            "Run 'zsm steam-login' once, then 'zsm setup' again "
            "(or it runs automatically on the next start)."
        )
        return

    print_step(1, 1, "Installing DayZ server via SteamCMD")
    try:
        update_dayz_server(config)
        print_success("DayZ server installed.")
    except Exception as e:
        print_error(f"Server install failed: {e}")
    else:
        apply_query_port(config)


def apply_query_port(config) -> None:
    """The query port an instance was given lives in its serverDZ.cfg, which
    exists only once the server is installed (plans/44 step 5). Every CLI
    command that downloads the server calls it after a download that worked:
    without it a second instance binds 27016 beside main's."""
    from app.shared.container import sync_query_port

    for key, value in sync_query_port(config).items():
        print_success(f"serverDZ.cfg {key} = {value}")
