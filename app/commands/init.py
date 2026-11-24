"""zsm init — interactive setup wizard."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import click
from InquirerPy import inquirer
from rich.console import Console
from rich.panel import Panel

from app.shared.config import DayzConfig, detect_workshop_content_dir
from app.ops import site_ops
from app.server.instances import save_new_config
from app.server.service_control import server_dir_problem
from app.shared.platform import IS_WINDOWS
from app.shared.steam import (
    install_steamcmd,
    steam_login,
    steam_username_problem,
    update_dayz_server,
)
from app.shared.cli_ui import confirm, print_success, print_warning, select

console = Console()

STEAMCMD_INSTALL_URL = (
    "https://github.com/modcommunity/how-to-download-and-run-steamcmd"
    "?tab=readme-ov-file#linux"
)

_STEAMCMD_BINARY = "steamcmd.exe" if IS_WINDOWS else "steamcmd.sh"
_SERVER_BINARY = "DayZServer_x64.exe" if IS_WINDOWS else "DayZServer"

if IS_WINDOWS:
    _SEARCH_PATHS = ["C:/", str(Path.home())]
else:
    _SEARCH_PATHS = ["/home", "/opt", "/usr/local", "/srv", "/root"]

_ENTER_MANUALLY = "Enter path manually..."

_FIND_MAX_DEPTH = 6
_FIND_TIMEOUT = 10


def _find_file(filename: str, timeout: int = _FIND_TIMEOUT) -> list[str]:
    """Search common filesystem locations for a file. Returns list of absolute paths.

    Uses pathlib BFS with max_depth and timeout — works on both Linux and Windows.
    """
    search_roots = [Path(p) for p in _SEARCH_PATHS if Path(p).is_dir()]
    if not search_roots:
        return []

    results: list[str] = []
    start = time.monotonic()

    for root in search_roots:
        if time.monotonic() - start > timeout:
            break
        try:
            _walk_for_file(root, filename, 0, _FIND_MAX_DEPTH, results, start, timeout)
        except (PermissionError, OSError):
            continue

    return results


def _walk_for_file(
    directory: Path,
    filename: str,
    depth: int,
    max_depth: int,
    results: list[str],
    start: float,
    timeout: int,
) -> None:
    """BFS-style recursive file search with depth and time limits."""
    if depth > max_depth or time.monotonic() - start > timeout:
        return

    try:
        entries = list(directory.iterdir())
    except (PermissionError, OSError):
        return

    dirs: list[Path] = []
    for entry in entries:
        if entry.is_file() and entry.name == filename:
            results.append(str(entry))
        elif entry.is_dir() and not entry.is_symlink():
            dirs.append(entry)

    for d in dirs:
        if time.monotonic() - start > timeout:
            return
        _walk_for_file(d, filename, depth + 1, max_depth, results, start, timeout)


def _select_or_enter_path(
    found_dirs: list[str],
    prompt_select: str,
    prompt_manual: str,
    default: str = "",
) -> str:
    """Show select menu if dirs found, otherwise text input. Returns chosen path."""
    if found_dirs:
        choices = found_dirs + [_ENTER_MANUALLY]
        choice = select(prompt_select, choices)
        if choice != _ENTER_MANUALLY:
            return choice

    return inquirer.text(
        message=prompt_manual,
        default=default,
        validate=lambda val: len(val.strip()) > 0,
        invalid_message="Path is required.",
    ).execute().strip()


# ---------------------------------------------------------------------------
# Setup phases
# ---------------------------------------------------------------------------

def _setup_steamcmd(existing: DayzConfig | None) -> str:
    """Step 1: Find or install SteamCMD. Returns steamcmd_dir path."""
    console.print("[bold]Step 1: SteamCMD[/bold]")

    has_steamcmd = inquirer.confirm(
        message="Is SteamCMD already installed?", default=True
    ).execute()

    if not has_steamcmd:
        console.print()
        install_choice = select(
            "How would you like to set up SteamCMD?",
            ["Install automatically", "Install manually (I'll do it myself)"],
        )

        if install_choice == "Install automatically":
            default_path = str(Path.home() / "steamcmd")
            install_path_str = inquirer.text(
                message="Install directory for SteamCMD:",
                default=default_path,
                validate=lambda val: len(val.strip()) > 0,
                invalid_message="Path is required.",
            ).execute().strip()
            install_path = Path(install_path_str).expanduser()

            if install_steamcmd(install_path):
                return str(install_path)

            console.print("[yellow]Auto-install failed. You can set the path manually and install later.[/yellow]")
            return inquirer.text(
                message="Path where SteamCMD will be installed:",
                default=str(install_path),
                validate=lambda val: len(val.strip()) > 0,
                invalid_message="SteamCMD directory is required.",
            ).execute().strip()

        # Manual install path
        console.print()
        console.print(f"[bold]Installation guide:[/bold] {STEAMCMD_INSTALL_URL}")
        console.print()
        proceed = inquirer.confirm(
            message="Continue setup anyway? (you can set the path now and install later)",
            default=False,
        ).execute()
        if not proceed:
            console.print("[dim]Setup cancelled. Install SteamCMD and re-run 'zsm init'.[/dim]")
            raise SystemExit(0)
        return inquirer.text(
            message="Path where SteamCMD will be installed:",
            default=str(existing.steamcmd_dir) if existing else "",
            validate=lambda val: len(val.strip()) > 0,
            invalid_message="SteamCMD directory is required.",
        ).execute().strip()

    # SteamCMD already installed — find it
    console.print(f"[dim]Searching for {_STEAMCMD_BINARY}...[/dim]")
    found = _find_file(_STEAMCMD_BINARY)
    found_dirs = list(dict.fromkeys(str(Path(p).parent) for p in found))

    if found_dirs:
        console.print(f"[green]Found {len(found_dirs)} SteamCMD installation(s).[/green]")
    else:
        console.print(f"[yellow]No {_STEAMCMD_BINARY} found on filesystem.[/yellow]")

    return _select_or_enter_path(
        found_dirs,
        prompt_select="Select SteamCMD directory:",
        prompt_manual=f"Path to steamcmd directory (where {_STEAMCMD_BINARY} lives):",
        default=str(existing.steamcmd_dir) if existing else "",
    )


def _setup_server(existing: DayzConfig | None) -> tuple[str, bool]:
    """Step 2: Find or set DayZ server directory. Returns (server_dir, has_server)."""
    console.print()
    console.print("[bold]Step 2: DayZ Server[/bold]")

    has_server = inquirer.confirm(
        message="Is the DayZ server already installed?", default=True
    ).execute()

    if has_server:
        console.print(f"[dim]Searching for {_SERVER_BINARY}...[/dim]")
        found = _find_file(_SERVER_BINARY)
        found_dirs = list(dict.fromkeys(str(Path(p).parent) for p in found))

        if found_dirs:
            console.print(f"[green]Found {len(found_dirs)} DayZ server installation(s).[/green]")
        else:
            console.print(f"[yellow]No {_SERVER_BINARY} binary found on filesystem.[/yellow]")

        server_dir = _select_or_enter_path(
            found_dirs,
            prompt_select="Select DayZ server directory:",
            prompt_manual="Path to your DayZ server directory:",
            default=str(existing.server_dir) if existing else "",
        )
        return server_dir, True

    default_server = str(Path.home() / "dayz-server")
    location_choice = select(
        "Where should the server be installed?",
        [f"Default location ({default_server})", "Custom location"],
    )
    if location_choice.startswith("Default"):
        server_dir = default_server
    else:
        server_dir = inquirer.text(
            message="Desired installation directory for DayZ server:",
            default=str(existing.server_dir) if existing else "",
            validate=lambda val: len(val.strip()) > 0,
            invalid_message="Server directory is required.",
        ).execute().strip()
    console.print(f"[dim]The server will be installed at {server_dir} after login.[/dim]")
    return server_dir, False


def _migrate_env_file(server_dir: str, existing: DayzConfig | None) -> tuple[DayzConfig | None, str]:
    """Step 3: Import settings from .env file if present. Returns (migrated_config, steam_username_default)."""
    steam_username_default = existing.steam_username if existing else ""
    migrated = None

    env_path = Path(server_dir) / ".env"
    if env_path.exists():
        console.print(f"[green]Found .env at {env_path}[/green]")
        migrate = inquirer.confirm(
            message="Import settings from existing .env file?", default=True
        ).execute()
        if migrate:
            migrated = DayzConfig.from_env_file(env_path)
            steam_username_default = migrated.steam_username
            console.print("[green]Imported values from .env[/green]")

    return migrated, steam_username_default


# What to type when the wizard ends without a DayZ server: a failed Steam login
# or a "no" to the download. `zsm steam-login` only reports a cached login
# that already works, so the two commands are right in both cases.
NEXT_WITHOUT_SERVER = (
    "Log in with: zsm steam-login   then download the server with: zsm setup"
)


def _prompt_steam_username(default: str) -> str:
    """Step 3: the Steam account, asked again until it is one that can
    download DayZ. An anonymous login never can: SteamCMD logs it in and then
    refuses the server install with "No subscription"."""
    while True:
        username = inquirer.text(
            message="Steam username:",
            default=default,
            validate=lambda val: len(val.strip()) > 0,
            invalid_message="Steam username is required.",
        ).execute().strip()
        problem = steam_username_problem(username)
        if not problem:
            return username
        print_warning(problem)
        default = ""


def _perform_steam_login(config: DayzConfig) -> bool:
    """Step 4: Log in to Steam to cache credentials. Returns True on success."""
    if not config.steam_username or not config.steamcmd_path.is_file():
        return False

    console.print()
    console.print("[bold]Step 4: Steam Login[/bold]")
    console.print("[dim]Logging in to cache credentials for future use.[/dim]")
    try:
        steam_login(config)
        print_success("Steam login successful.")
        return True
    except subprocess.CalledProcessError:
        # SteamCMD's own line above says why ("ERROR (Invalid Password)") and
        # steam_login has said that it failed. The exception's text is the
        # command and its exit status, which told a newcomer nothing.
        pass
    except Exception as e:
        console.print(f"[yellow]Steam login failed: {e}[/yellow]")
    console.print("[dim]You can log in later with: zsm steam-login[/dim]")
    return False


def _offer_server_install(config: DayzConfig, login_ok: bool) -> None:
    """Step 5: Optionally install DayZ server if not yet installed."""
    console.print()
    console.print("[bold]Step 5: Install DayZ Server[/bold]")
    if not login_ok:
        # Skipping this step without a word left "Setup complete!" on a machine
        # with no DayZ server and no hint at `zsm setup` (first-hour test,
        # 2026 Q4).
        print_warning("Skipped: the DayZ server cannot be downloaded without the Steam login.")
        console.print(f"[dim]{NEXT_WITHOUT_SERVER}[/dim]")
        return

    install_now = inquirer.confirm(
        message=f"Install DayZ server now to {config.server_dir_path}?",
        default=True,
    ).execute()
    if install_now:
        try:
            update_dayz_server(config)
        except Exception as e:
            console.print(f"[yellow]Server installation failed: {e}[/yellow]")
            console.print("[dim]You can install later with: zsm setup[/dim]")
            return
        from app.commands.setup import apply_query_port

        apply_query_port(config)

        # Windows: show prerequisites and generate bat files
        if IS_WINDOWS:
            console.print()
            console.print(Panel(
                "[bold]Windows Prerequisites[/bold]\n\n"
                "The DayZ server requires the following runtimes:\n\n"
                "1. [cyan]Microsoft Visual C++ Redistributable[/cyan]\n"
                "   https://aka.ms/vc14/vc_redist.x64.exe\n\n"
                "2. [cyan]DirectX End-User Runtimes (June 2010)[/cyan]\n"
                "   https://www.microsoft.com/en-us/download/details.aspx?id=8109",
                title="Required Dependencies",
                expand=False,
            ))

            from app.server.service_control import START_BAT_NAME, STOP_BAT_NAME, write_windows_scripts

            console.print()
            console.print("[bold]Generating start/stop scripts...[/bold]")
            write_windows_scripts(config)
            console.print(f"[green]Saved {START_BAT_NAME} and {STOP_BAT_NAME} to {config.server_dir}[/green]")
    else:
        console.print("[dim]Skipped. You can install later with: zsm setup[/dim]")


def _offer_site(config: DayzConfig) -> None:
    """Step 6: the two zedstead.com opt-ins (plans/57 B6), ping first, both
    defaulting to no.

    Only the wizard asks. `zsm setup`, `zsm container-init` and
    `--non-interactive` never reach this, so turning one on is always a human
    act. Saves the config only when a switch was turned on.
    """
    console.print()
    console.print("[bold]Step 6: zedstead.com (optional)[/bold]")
    ping = inquirer.confirm(
        message="Send zedstead.com an anonymous install count once a day (random ID, Zedstead "
        "version, OS, Docker yes/no)? Changeable later in Settings.",
        default=False,
    ).execute()
    listing = inquirer.confirm(
        message="List this server publicly on zedstead.com (its IP, game port and query "
        "port become public)? Changeable later in Settings.",
        default=False,
    ).execute()
    if not (ping or listing):
        return
    site_ops.set_switch(config, "site_ping", ping)
    site_ops.set_switch(config, "site_listing", listing)
    config.save()
    console.print("[dim]`zsm listing status` shows exactly what is sent.[/dim]")


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------

@click.command()
@click.pass_context
def init(ctx):
    """Interactive setup wizard for zsm."""
    console.print(Panel("[bold]Zedstead Setup Wizard[/bold]", expand=False))
    console.print()

    existing = DayzConfig.load_or_none()
    if existing:
        console.print(f"[yellow]Existing config found at {existing.paths.config_file}[/yellow]")
        overwrite = inquirer.confirm(
            message="Overwrite existing configuration?", default=False
        ).execute()
        if not overwrite:
            console.print("[dim]Setup cancelled.[/dim]")
            return

    # Step 1: SteamCMD
    steamcmd_dir = _setup_steamcmd(existing)

    # Step 2: DayZ Server directory
    server_dir, has_server = _setup_server(existing)
    while problem := server_dir_problem(server_dir):
        print_warning(problem)
        server_dir, has_server = _setup_server(existing)

    # Workshop content location
    console.print()
    config_tmp = DayzConfig(steamcmd_dir=steamcmd_dir)
    workshop_content_dir = ""
    detected = detect_workshop_content_dir()
    if detected is not None:
        # A desktop Steam install already has Workshop content somewhere other
        # than the clean-server ~/Steam default. Pin it so the mod manager
        # finds it.
        console.print(
            f"[dim]Detected existing Steam Workshop content at {detected}.[/dim]"
        )
        if inquirer.confirm(
            message="Use this Workshop content directory?", default=True
        ).execute():
            workshop_content_dir = str(detected)
        else:
            workshop_content_dir = inquirer.text(
                message="Workshop content directory:",
                default=str(detected),
            ).execute().strip()
    else:
        console.print(
            f"[dim]Workshop content (mods) will be downloaded to "
            f"{config_tmp.workshop_dir} regardless of server/SteamCMD location.[/dim]"
        )

    # Step 3: .env migration
    migrated, steam_username_default = _migrate_env_file(server_dir, existing)

    # Step 4: Steam username
    console.print()
    console.print("[bold]Step 3: Steam Account[/bold]")
    steam_username = _prompt_steam_username(steam_username_default)

    # Build and save config
    if migrated:
        config = migrated
        config.server_dir = server_dir
        config.steamcmd_dir = steamcmd_dir
        config.steam_username = steam_username
        config.workshop_content_dir = workshop_content_dir
    else:
        config = DayzConfig(
            server_dir=server_dir,
            steamcmd_dir=steamcmd_dir,
            steam_username=steam_username,
            workshop_content_dir=workshop_content_dir,
        )

    # Validate steamcmd path (warn only)
    if not config.steamcmd_path.is_file():
        console.print(
            f"[yellow]Warning: {config.steamcmd_path.name} not found at {config.steamcmd_path}[/yellow]"
        )
        if has_server:
            proceed = inquirer.confirm(
                message="Save config anyway?", default=True
            ).execute()
            if not proceed:
                console.print("[dim]Setup cancelled.[/dim]")
                return

    if existing:
        # Overwriting keeps the instance, or the legacy file of an install the
        # migration has not moved yet.
        config.instance_id = existing.instance_id
        config.save()
    else:
        save_new_config(config)
    console.print()
    console.print(f"[green]Config saved to {config.paths.config_file}[/green]")

    # Step 4: Steam login
    login_ok = _perform_steam_login(config)

    # Step 5: Optionally install server, or adopt the one that is there
    if not has_server:
        _offer_server_install(config, login_ok)
    else:
        _offer_adopt(config)

    # Step 6: zedstead.com opt-ins
    _offer_site(config)

    _say_done(config)


def _offer_adopt(config: DayzConfig) -> None:
    """D4 of plans/78: a server zsm did not install is read, shown, and
    adopted on one answer. Skipped when the mod list already holds mods (an
    init over a config zsm already runs) or the directory holds nothing to
    adopt."""
    from rich.markup import escape

    from app.commands.adopt import apply_with_prompt, print_report
    from app.modmgr import modlist
    from app.ops import adopt

    try:
        if modlist.installed(config):
            return
        report = adopt.scan(config)
    except (OSError, modlist.ModListError) as exc:
        print_warning(f"Could not read the server to adopt it: {escape(str(exc))}")
        return
    if report.launch is None and not report.mods:
        return
    console.print()
    console.print("[bold]Your server[/bold]")
    print_report(report, None)
    if apply_with_prompt(config, report, ni=False):
        console.print("[dim]Checks and fixes for it: zsm adopt[/dim]")


def _say_done(config: DayzConfig) -> None:
    """The wizard's last lines. "Setup complete!" only when there is a DayZ
    server: after a failed login or a "no" to the download there is none, and
    the last thing on screen has to say what gets one."""
    from app.commands.doctor import server_is_installed

    console.print()
    if server_is_installed(config):
        console.print("[bold green]Setup complete![/bold green]")
    else:
        console.print("[bold yellow]Setup saved. The DayZ server is not installed yet.[/bold yellow]")
        console.print(NEXT_WITHOUT_SERVER)
    console.print("Run [bold]zsm[/bold] to open the web UI, or [bold]zsm --help[/bold] for the CLI.")
    console.print()


def prompt_init_if_missing(ctx: click.Context) -> bool:
    """Run the setup wizard if no config exists yet.

    Returns True if a config exists after the call (either was already there
    or the wizard created one), False if the user declined.
    """
    if DayzConfig.load_or_none() is not None:
        return True
    non_interactive = ctx.obj.get("non_interactive", False) if ctx.obj else False
    if not confirm(
        "No configuration found. Would you like to run the setup wizard?",
        non_interactive=non_interactive,
    ):
        return False
    ctx.invoke(init)
    return DayzConfig.load_or_none() is not None
