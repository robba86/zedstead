"""zsm service — generate, install, and manage the server service."""

from __future__ import annotations

import subprocess
from pathlib import Path

import click
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from app.shared.config import ConfigNotFound, DayzConfig
from app.commands.context import _sudo_note, load_context
from app.server import unit_guard
from app.server.service_control import (
    NO_SYSTEMD_MESSAGE,
    SERVICE_NAME,  # re-exported for log_analyzer
    START_BAT_NAME,
    STOP_BAT_NAME,
    SYSTEMD_DIR,
    ServiceUserError,
    _get_template_replacements,
    _kill_watchdog_command,
    _render_service,
    _render_start_bat,
    _render_stop_bat,
    _windows_launch_start_bat,
    build_registry_order,
    is_server_running,
    systemctl,
    unit_is_stale,
    windows_server_status,
    windows_stop_server,
    write_windows_scripts,
)
from app.shared.platform import IS_WINDOWS, has_systemd, sudo_prefix
from app.shared.systemd_scope import zsm_command, get_scope, server_unit
from app.shared.cli_ui import confirm, print_error, print_success, print_warning

console = Console()


def _ensure_sudo_cli(config: DayzConfig | None = None) -> bool:
    """Validate sudo up front. Returns True if credentials are cached or accepted.

    Returns True immediately in **user** scope (no sudo is ever used), or when
    sudo isn't needed in system scope: on Windows, when already root, or when
    sudo isn't installed. Otherwise tries non-interactive `sudo -n true`,
    falling back to interactive `sudo -v` (prompts on the tty). On auth failure
    or cancellation, returns False so callers can abort before making changes.
    """
    if not get_scope(config).needs_sudo:
        return True
    try:
        if subprocess.run(
            ["sudo", "-n", "true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode == 0:
            return True
        return subprocess.run(["sudo", "-v"]).returncode == 0
    except FileNotFoundError:
        print_error("sudo not found.")
        return False


def _install_unit_from(config: DayzConfig, source: Path) -> bool:
    """Install the rendered unit file at ``source`` into the resolved scope's
    unit dir and daemon-reload. User scope writes directly (no sudo); system
    scope ``sudo cp``s into /etc. Prints on failure; returns True on success.
    """
    scope = get_scope(config)
    dest = scope.unit_dir / server_unit(config)
    try:
        content = source.read_text(encoding="utf-8")
        if scope.user_mode:
            scope.unit_dir.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
        else:
            subprocess.run([*sudo_prefix(), "cp", str(source), str(dest)], check=True)
    except (OSError, subprocess.CalledProcessError) as e:
        print_error(f"Failed to install service: {e}")
        return False
    unit_guard.record(config, content)
    return _systemctl("daemon-reload", unit=False, config=config)


def _may_replace_unit(config: DayzConfig, content: str, *, ni: bool, replace: bool) -> bool:
    """Ask the unit guard before *content* replaces the installed unit
    (plans/78 D2). A unit zsm did not write is replaced only with
    ``--replace``; a zsm unit edited by hand shows the difference and is
    replaced only on an answer typed at the prompt, never on ``-y``."""
    check = unit_guard.check(config, content)
    if check.may_replace or (replace and check.state == unit_guard.FOREIGN):
        return True
    if check.state == unit_guard.EDITED and not ni:
        console.print(Syntax(unit_guard.diff(check, content), "diff", theme="monokai"))
        if confirm(
            f"{check.path} was edited by hand since Zedstead installed it. "
            "Replace it with what Zedstead renders now?",
            default=False,
        ):
            return True
    print_error(check.refusal(config))
    return False


def _no_systemd_msg() -> None:
    """Tell the user systemd isn't available (e.g. container / non-systemd host)."""
    print_warning(NO_SYSTEMD_MESSAGE)


def _instance_config() -> DayzConfig | None:
    """The config of the instance this command acts on, for the commands
    that need nothing from it but which unit is theirs (plans/44 step 6).
    None with no config at all: `dayz-server.service`, as before."""
    return DayzConfig.load_or_none()


def _systemctl(
    action: str,
    *,
    sudo: bool = True,
    check: bool = True,
    unit: bool = True,
    config: DayzConfig | None = None,
) -> bool:
    """CLI wrapper around service_control.systemctl: prints on failure."""
    result = systemctl(action, sudo=sudo, check=check, unit=unit, config=config)
    if not result.ok and result.message:
        if result.message == NO_SYSTEMD_MESSAGE:
            print_warning(result.message)
        else:
            print_error(result.message)
    return result.ok


def _windows_stop_server() -> bool:
    """CLI wrapper around service_control.windows_stop_server: prints on failure."""
    result = windows_stop_server()
    if not result.ok and result.message:
        print_error(result.message)
    return result.ok


def ensure_server_stopped(config: DayzConfig) -> bool:
    """Check if the server is running and prompt the user to stop it.

    Returns True if it's safe to proceed (server not running or successfully stopped).
    """
    if not is_server_running(config):
        return True
    if not IS_WINDOWS:
        print_warning("DayZ Server is currently running. Stop it before continuing.")
        return False
    print_warning("DayZ Server is currently running. Files may be locked.")
    if not confirm("Stop the server before continuing?", default=True):
        print_warning("Aborted — the server must be stopped to modify files on Windows.")
        return False
    return _windows_stop_server()


def _windows_server_status() -> None:
    """Show DayZ server process status on Windows."""
    result = windows_server_status()
    if result.ok:
        console.print(result.message)
    else:
        print_error(result.message)


# ---------------------------------------------------------------------------
# Click commands
# ---------------------------------------------------------------------------

@click.group()
@click.pass_context
def service(ctx):
    """Manage the DayZ server service."""
    pass


@service.command()
@click.pass_context
def generate(ctx):
    """Generate and preview the service/start script."""
    config, ni = load_context(ctx)

    if IS_WINDOWS:
        start_content = _render_start_bat(config)
        stop_content = _render_stop_bat(config)

        console.print()
        console.print("[bold]start-server.bat:[/bold]")
        console.print(Syntax(start_content, "batch", theme="monokai", line_numbers=True))
        console.print()
        console.print("[bold]stop-server.bat:[/bold]")
        console.print(Syntax(stop_content, "batch", theme="monokai", line_numbers=True))
        console.print()

        if confirm(f"Save to {config.server_dir}?", default=True, non_interactive=ni):
            write_windows_scripts(config)
            print_success(f"Scripts saved to {config.server_dir}")
    else:
        try:
            content = _render_service(config)
        except ServiceUserError as e:
            print_error(str(e))
            return

        console.print()
        console.print(Syntax(content, "ini", theme="monokai", line_numbers=True))
        console.print()

        output_path = Path(config.server_dir) / server_unit(config)
        if confirm(f"Save to {output_path}?", default=True, non_interactive=ni):
            output_path.write_text(content, encoding="utf-8")
            print_success(f"Service file saved to {output_path}")
            console.print("[dim]Install with: zsm service install[/dim]")


@service.command()
@click.option("--replace", is_flag=True, default=False,
              help="Replace an installed unit Zedstead did not write.")
@click.pass_context
def install(ctx, replace):
    """Install the service file to systemd and reload daemon."""
    if IS_WINDOWS:
        print_warning("Service install is not available on Windows. Use 'zsm service generate' instead.")
        return

    if not has_systemd():
        _no_systemd_msg()
        return

    config, ni = load_context(ctx)

    source = Path(config.server_dir) / server_unit(config)
    if not source.exists():
        print_error(f"No service file found at {source}. Run 'zsm service generate' first.")
        return

    scope = get_scope(config)
    dest = scope.unit_dir / server_unit(config)
    if unit_is_stale(config, source):
        # Refused, not re-rendered: `generate` saves this file for the admin to
        # read before installing, so it may hold a hand edit that a silent
        # re-render would throw away. Measured in 2026 Q3: a server folder
        # rsynced from the Docker box carried a system-scope unit for another
        # path, installed verbatim into user scope, and could never start.
        print_error(
            f"{source} is not what Zedstead renders for this machine now: it was "
            "rendered elsewhere or before a settings change, or edited by hand."
        )
        console.print(
            "[dim]Run 'zsm service generate' to re-render it (this replaces the file), "
            f"then install again. To install a hand-edited unit as it is, copy it to "
            f"{dest} and daemon-reload yourself.[/dim]"
        )
        return
    if not _may_replace_unit(config, source.read_text(encoding="utf-8"), ni=ni, replace=replace):
        return
    sudo_note = " (requires sudo)" if scope.needs_sudo else ""
    if confirm(f"Install {source} to {dest}?{sudo_note}", default=True, non_interactive=ni):
        if not _ensure_sudo_cli(config):
            print_error("Aborted — sudo authentication required.")
            return
        if _install_unit_from(config, source):
            print_success(f"Service installed to {dest} and daemon reloaded.")
            # Installing does not enable: nothing said so, and after a reboot
            # the panel's service was up and the game server was not
            # (first-hour test, 2026 Q4).
            console.print(
                f"[dim]Start it at boot too: {zsm_command(config, 'service enable')}[/dim]"
            )
            if scope.user_mode:
                console.print(
                    f"[dim]Manage with: systemctl --user start|status {server_unit(config)}[/dim]"
                )


@service.command()
@click.pass_context
def status(ctx):
    """Show server status."""
    if IS_WINDOWS:
        _windows_server_status()
    else:
        config = _instance_config()
        scope = get_scope(config)
        _systemctl("status", sudo=False, check=False, config=config)
        if scope.user_mode:
            console.print(
                f"[dim]Logs: journalctl --user -u {server_unit(config)} -f[/dim]"
            )


@service.command()
@click.option(
    "--anyway", is_flag=True,
    help="Start although a runtime the server needs looks missing (Windows).",
)
@click.pass_context
def start(ctx, anyway):
    """Start the DayZ server."""
    from app.ops.changeset_ops import take_start_snapshot
    from app.server.windows_runtime import runtime_problem

    from app.server.service_control import start_refusal

    config, ni = load_context(ctx)
    # The web's words, and --anyway does not pass them: no server or no unit
    # cannot start (systemctl answers "exit status 5").
    refusal = start_refusal(config)
    if refusal:
        print_error(refusal)
        ctx.exit(1)
    problem = None if anyway else runtime_problem(config)
    if problem:
        print_error(f"{problem} To start it anyway, run {zsm_command(config, 'start --anyway')}.")
        ctx.exit(1)
    take_start_snapshot(config)
    if IS_WINDOWS:
        launched = _windows_launch_start_bat(config)
        if not launched.ok:
            print_error(launched.message)
            return
        print_success("Server start script executed.")
    else:
        if _systemctl("start", config=config):
            print_success("Service started.")


@service.command()
@click.pass_context
def stop(ctx):
    """Stop the DayZ server."""
    if IS_WINDOWS:
        if _windows_stop_server():
            print_success("Server stopped.")
    else:
        from app.server.service_control import clear_stop_failure, crash_looping, restarts_this_run

        config = _instance_config()
        if not is_server_running(config) and not crash_looping(config):
            print_success("Service already stopped.")
            return
        restarts = restarts_this_run(config)
        if _systemctl("stop", config=config):
            clear_stop_failure(config, restarts)
            print_success("Service stopped.")


@service.command()
@click.option(
    "--if-running", is_flag=True,
    help="Restart only a server that is running; leave a stopped one stopped.",
)
@click.pass_context
def restart(ctx, if_running):
    """Restart the DayZ server."""
    from app.ops.changeset_ops import take_start_snapshot

    config, ni = load_context(ctx)
    # The Windows restart task runs this with --if-running, as the Linux one
    # skips a server the admin stopped.
    if if_running and not is_server_running(config):
        print_success("The server is not running, so there is nothing to restart.")
        return
    take_start_snapshot(config)
    if IS_WINDOWS:
        _windows_stop_server()
        launched = _windows_launch_start_bat(config)
        if not launched.ok:
            print_error(launched.message)
            return
        print_success("Server restarted.")
    else:
        if _systemctl("restart", config=config):
            print_success("Service restarted.")


@service.command()
@click.pass_context
def remove(ctx):
    """Remove the DayZ server systemd service."""
    if IS_WINDOWS:
        print_warning("Service remove is not available on Windows.")
        return

    if not has_systemd():
        _no_systemd_msg()
        return

    from app.server.service_control import remove_service

    config, ni = load_context(ctx)
    scope = get_scope(config)
    dest = scope.unit_dir / server_unit(config)

    if not dest.exists():
        print_warning("No service file found at " + str(dest))
        return

    # `-y` is "skip all confirmations"; asked, a bare Enter keeps the service.
    # (confirm() returns its default when non-interactive, which made `-y` a
    # silent no here until plans/44 step 6 tripped over it.)
    if not ni and not confirm("This will stop and remove the systemd service. Continue?"):
        return

    if not _ensure_sudo_cli(config):
        print_error("Aborted — sudo authentication required.")
        return

    console.print("[bold]Stopping, disabling, and removing service...[/bold]")
    result = remove_service(config)
    if result.ok:
        print_success("Service removed successfully.")
    else:
        print_error(result.message or "Failed to remove service.")


@service.command(name="migrate-scope")
@click.argument("target", type=click.Choice(["user", "system"]))
@click.pass_context
def migrate_scope(ctx, target):
    """Migrate the DayZ systemd service to USER scope (no sudo). Linux only.

    Removes the old system unit + timers (the last time sudo is used), flips
    service_scope to 'user', and reinstalls everything under
    ~/.config/systemd/user. `migrate-scope system` is intentionally a no-op with
    guidance — to go back, run `zsm settings set service_scope system` then
    `zsm service update`.
    """
    import asyncio
    import dataclasses

    from app.server import scheduler
    from app.server.service_control import is_server_running, remove_service, stop_server
    from app.server.service_ops import generate_service_files
    from app.shared.systemd_scope import (
        SYSTEM_UNIT_DIR,
        _current_user,
        linger_enabled,
    )

    if IS_WINDOWS:
        print_warning("Scope migration is Linux/systemd only.")
        return
    if not has_systemd():
        _no_systemd_msg()
        return

    config, ni = load_context(ctx)

    if target == "system":
        print_warning(
            "migrate-scope system is not needed. To switch back to system scope run:\n"
            "  zsm settings set service_scope system\n"
            "  zsm service update"
        )
        return

    if config.service_scope == "user":
        print_warning("Already configured for user scope. Nothing to migrate.")
    else:
        # Removal phase runs against the *system* unit regardless of the config's
        # current (unset/auto) value.
        system_cfg = dataclasses.replace(config, service_scope="system")

        if is_server_running(system_cfg):
            if not confirm(
                "The server is running. Stop it to migrate?", default=True, non_interactive=ni
            ):
                print_warning("Aborted — stop the server before migrating.")
                return
            stop_server(system_cfg)

        if not _ensure_sudo_cli(system_cfg):
            print_error("Aborted — sudo authentication required to remove the system unit.")
            return

        if (SYSTEM_UNIT_DIR / server_unit(config)).exists():
            console.print("[bold]Removing system service unit...[/bold]")
            remove_service(system_cfg)
        console.print("[bold]Removing system timers...[/bold]")
        try:
            asyncio.run(scheduler.remove_timers_async(system_cfg))
        except Exception as e:  # noqa: BLE001 - best-effort cleanup
            print_warning(f"Timer cleanup issue (continuing): {e}")

        config.service_scope = "user"
        config.save()
        print_success("service_scope set to 'user'.")

        console.print("[bold]Installing user service unit...[/bold]")
        try:
            generate_service_files(config)
        except Exception as e:  # noqa: BLE001
            print_error(f"User service install failed: {e}")
            return
        if [t for t in scheduler.load_tasks(config) if t.enabled]:
            console.print("[bold]Installing user timers...[/bold]")
            try:
                asyncio.run(scheduler.install_timers_async(config))
            except Exception as e:  # noqa: BLE001
                print_warning(f"Timer install issue: {e}")

    user = _current_user()
    if not linger_enabled(user):
        print_warning(
            "Lingering is disabled — the service won't start at boot or survive logout. "
            f"Run: zsm service enable-linger  (or sudo loginctl enable-linger {user})"
        )
    else:
        print_success("Lingering is enabled.")

    console.print()
    # Stop with zsm: a raw `systemctl stop` leaves DayZ's exit 255 as
    # "failed", and the restart task restarts a failed server.
    console.print("[bold green]Migration complete.[/bold green] Manage the service with:")
    console.print(f"  [dim]{zsm_command(config, 'start|stop|restart')}[/dim]")
    console.print(f"  [dim]systemctl --user status {server_unit(config)}[/dim]")
    console.print(f"  [dim]journalctl --user -u {server_unit(config)} -f[/dim]")


@service.command(name="enable-linger")
@click.pass_context
def enable_linger(ctx):
    """Enable systemd user lingering (user-scope services run at boot / after logout).

    Required for user-scope services (``service_scope=user``) so the per-user
    systemd manager — and therefore the DayZ server + its timers — starts at
    boot and survives logout. A one-time root action; polkit sometimes allows
    it without a password, so this tries unprivileged first, then ``sudo``.
    """
    if IS_WINDOWS:
        print_warning("Lingering is a systemd concept — not applicable on Windows.")
        return

    from app.shared.systemd_scope import _current_user, linger_enabled

    user = _current_user()
    if linger_enabled(user):
        print_success(f"Lingering already enabled for {user}.")
        return

    attempts = [["loginctl", "enable-linger", user]]
    if sudo_prefix():
        attempts.append([*sudo_prefix(), "loginctl", "enable-linger", user])
    for cmd in attempts:
        try:
            if subprocess.run(cmd).returncode == 0:
                print_success(f"Lingering enabled for {user}  (`{' '.join(cmd)}`).")
                return
        except FileNotFoundError:
            print_error("loginctl not found. Is systemd available?")
            return
    print_error(
        f"Failed to enable lingering. Run manually: sudo loginctl enable-linger {user}"
    )


@service.command()
@click.option("--apply", "apply_", is_flag=True,
              help="Write the rules. Without this, only report what would change.")
@click.option("--dry-run", is_flag=True, default=False,
              help="Print the exact commands and exit (the default when --apply is absent).")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt (for scripts).")
@click.pass_context
def firewall(ctx, apply_, dry_run, yes):
    """Report — or with --apply, set — a deny-by-default firewall that opens
    exactly the DayZ server's UDP ports.

    Never opens the RCon port or the web UI port — put that behind TLS and a
    reverse proxy instead. With --apply, the
    live SSH port is detected and allowed *before* the default policy flips;
    if it cannot be determined while sshd is running, the command refuses.
    """
    from app.ops.firewall_ops import (
        apply_rules,
        build_apply_commands,
        detect_backend,
        firewall_status,
        format_commands,
        no_backend_advice,
        NO_BACKEND_SUMMARY,
        rcon_collision,
        required_ports,
        ssh_detect,
        ssh_source_is_confident,
        WEAK_SOURCE_NOTE,
    )

    if IS_WINDOWS:
        print_warning("Firewall management is Linux-only (ufw/firewalld). "
                      "Open the DayZ UDP ports in Windows Firewall manually.")
        return

    try:
        config, ni = load_context(ctx)
    except ConfigNotFound:
        # Runs before `zsm init` on a fresh VPS, like bootstrap-vps (which
        # hands over to this) and user-data.yaml's firewall line. The config is
        # only read for the ports a server would use, and the defaults answer
        # that. Under sudo the missing config is root's, not the caller's, so
        # defaults there would stand in for the caller's real ports: re-raise.
        if _sudo_note():
            raise
        config, ni = DayzConfig(), ctx.obj.get("non_interactive", False)
        print_warning("No config yet (`zsm init` has not run), so these are the default "
                      "ports. If the server will use other ports, run `zsm init` first "
                      "and re-run this.")
    # --dry-run is the documented posture: it is what happens without --apply,
    # and passing both means report only. Writing rules is never the default.
    if dry_run:
        apply_ = False
    backend = detect_backend()
    status = firewall_status(config)

    console.print()
    if backend is None:
        print_warning(NO_BACKEND_SUMMARY)
        console.print(f"[dim]  {no_backend_advice()}[/dim]")
    else:
        state = {True: "active", False: "inactive", None: "unknown (needs root to read)"}[status.active]
        console.print(f"[bold]Firewall:[/bold] {backend} — {state}")
        # Without this the screen read as self-contradictory on the command the
        # tool tells users to run first: "firewalld — active" followed by five
        # `unknown`s and no reason anywhere. `zone_conflict` is the explanation
        # doctor already had; it is set *in addition to* `error`, and the table
        # below still branches on `error`, so nothing about what this command
        # trusts has changed.
        if status.zone_conflict:
            console.print(f"  [yellow]{status.zone_conflict}.[/yellow]")
            console.print(
                "  [dim]Check which zone holds the server's interface: "
                "firewall-cmd --get-active-zones[/dim]"
            )

    table = Table(title="Ports the DayZ server needs (UDP)", show_header=True)
    table.add_column("Port")
    table.add_column("Proto")
    table.add_column("Purpose")
    table.add_column("Allowed now?")
    clash = rcon_collision(config)
    for spec in required_ports(config):
        if status.active is not True or status.error:
            allowed = "unknown"
        else:
            allowed = "yes" if spec.port in status.open_udp else "no"
        label = f"{spec.label} + RCon!" if clash and spec.port == clash.port else spec.label
        table.add_row(str(spec.port), spec.proto, label, allowed)
    console.print(table)

    console.print("[dim]Not opened on purpose: the web app port (put it behind TLS + a "
                  "reverse proxy) — and the RCon port, which is not a port players "
                  "need.[/dim]")
    if clash is not None:
        print_warning(
            f"RCon port {clash.port} is also this server's {clash.label} port, so one allow "
            f"rule covers both — and allowing it for players publishes the RCon listener. "
            f"Measured on a running server: the DayZ process binds RCon to 0.0.0.0, not to "
            f"loopback.\n"
            f"  Fix before applying: set  RConIP 127.0.0.1  in beserver_x64.cfg (BattlEye "
            f"tab in the web UI), then restart the server.\n"
            f"  Confirm with:  ss -ulnp | grep {clash.port}   — it should show 127.0.0.1."
        )

    detection = ssh_detect()
    ports, source = detection.ports, detection.source
    if source == "unknown":
        print_warning("SSH is running but its port could not be determined — "
                      "--apply will refuse rather than risk locking you out.")
    else:
        # A config-file source is intent, not observation — say so rather than
        # presenting every detection method as equally solid (plans/27, D3).
        note = "" if ssh_source_is_confident(source) else f", {WEAK_SOURCE_NOTE.get(source, '')}"
        console.print(f"[bold]SSH:[/bold] port(s) {', '.join(str(p) for p in ports)} "
                      f"(detected via {source}{note}) — allowed first, before the deny "
                      f"policy, and re-checked in the firewall's own table before it flips")
    # Detection is a union of four sources, so a source that could not answer
    # means the port list may be short — which is the lockout. Say so instead of
    # letting it show up only as a refusal later. See plans/29 (D7-D9).
    for reason in detection.incomplete:
        console.print(f"[dim]  incomplete: {reason}[/dim]")

    console.print()
    if backend is None:
        # It used to fall back to `backend or "ufw"` and print nine `sudo ufw …`
        # commands under a header correctly saying ufw is not installed, so a
        # user pasting them got `command not found` nine times. There is no
        # honest command list to print for a backend that is not there.
        console.print("[dim]No commands to show — Zedstead writes rules for ufw or "
                      "firewalld, and neither is installed.[/dim]")
    elif not ports:
        # Same reasoning one branch up, and it took a real box to notice: with an
        # empty union this printed `--add-port=22/tcp` — the one assumption
        # `ssh_detect()` refuses to make — on a host whose sshd was on 2222, four
        # lines below a warning saying the port could not be determined. `--apply`
        # refuses here, so nothing zsm *runs* was ever wrong; the danger was that
        # the four commands are runnable by hand, and the refusal tells the
        # operator to do exactly that. Copying them denies the live port. That is
        # the F19 lockout relocated to the screen, where the plan-27 gate has no
        # reach. Found in the 2026 Q3 VPS test (F29).
        console.print("[dim]No commands to show — the SSH port is not known, and an "
                      "allow rule for the wrong port would lock you out. Find it with "
                      "`sudo ss -tlnp | grep sshd`, allow that port by hand, then "
                      "re-run.[/dim]")
    else:
        console.print("[bold]Commands:[/bold]")
        for line in format_commands(build_apply_commands(config, backend=backend, ssh=ports)):
            console.print(f"  [dim]{line}[/dim]")
    console.print()

    if not apply_:
        if backend is not None and ports:
            console.print("[dim]Nothing was changed. Re-run with --apply to write these rules.[/dim]")
        return

    def _confirm(_lines: list[str]) -> bool:
        return confirm("Apply these rules?", default=False, non_interactive=ni)

    code = apply_rules(
        config,
        assume_yes=yes,
        log=lambda line: console.print(line, markup=False),
        confirm=None if yes else _confirm,
        interactive=not ni,
    )
    if code == 0:
        print_success("Firewall rules applied.")
    else:
        ctx.exit(code)


@service.command()
@click.pass_context
def enable(ctx):
    """Enable the DayZ server service to start at boot."""
    if IS_WINDOWS:
        print_warning("Enable at boot is not available on Windows.")
        return
    if _systemctl("enable", config=_instance_config()):
        print_success("Service enabled at boot.")


@service.command()
@click.pass_context
def disable(ctx):
    """Disable the DayZ server service from starting at boot."""
    if IS_WINDOWS:
        print_warning("Disable at boot is not available on Windows.")
        return
    if _systemctl("disable", config=_instance_config()):
        print_success("Service disabled at boot.")


@service.group(name="web")
def web_service():
    """Run the web app as a service, so it outlives your SSH session."""


@web_service.command(name="install")
@click.pass_context
def web_install(ctx):
    """Install zsm-web.service, enable it at boot and start the app."""
    from app.server.panel_unit import (
        PANEL_PORT,
        install_panel_unit,
        panel_bind_host,
        panel_commands,
        panel_port_taken,
        panel_unit_path,
    )
    from app.shared.systemd_scope import linger_enabled
    from app.web.users import has_no_login

    if IS_WINDOWS:
        print_warning("The app service is systemd only. On Windows, run 'zsm web' in a terminal.")
        return
    if not has_systemd():
        _no_systemd_msg()
        return

    config, ni = load_context(ctx)
    if has_no_login(config):
        # `zsm web` prompts for one exactly then (the same helper), and a
        # service has no terminal to answer: it would exit, restart and exit
        # again every five seconds. Readable accounts are a login without the
        # single password, so they do not refuse.
        print_error("No web UI password is set, and a service cannot answer the prompt for one.")
        console.print("[dim]Run 'zsm web --set-password' first, then install again.[/dim]")
        ctx.exit(1)
    if panel_port_taken(config):
        print_error(
            f"Something is already listening on port {PANEL_PORT}, probably a "
            "'zsm web' you started by hand (in tmux, say)."
        )
        console.print("[dim]Stop it (Ctrl+C in its terminal), then install again.[/dim]")
        ctx.exit(1)

    scope = get_scope(config)
    dest = panel_unit_path(config)
    sudo_note = " (requires sudo)" if scope.needs_sudo else ""
    if not confirm(f"Install {dest} and start the app?{sudo_note}", default=True, non_interactive=ni):
        return
    if not _ensure_sudo_cli(config):
        print_error("Aborted — sudo authentication required.")
        ctx.exit(1)
    result = install_panel_unit(config)
    if not result.ok:
        print_error(result.message)
        ctx.exit(1)

    host = panel_bind_host(config)
    restart_cmd, logs_cmd = panel_commands(config)
    print_success(f"App service installed to {dest}, enabled at boot, listening on {host}:{PANEL_PORT}.")
    if host == "127.0.0.1":
        console.print(
            "[dim]Loopback only: a service has no SSH session, so the auto-detect that "
            "binds 'zsm web' to 0.0.0.0 over SSH does not apply. Reach it through your "
            "reverse proxy or an SSH tunnel, or run 'zsm settings set web_bind_host "
            f"0.0.0.0' and then '{restart_cmd}'.[/dim]"
        )
    if scope.user_mode and not linger_enabled():
        print_warning(
            "Lingering is disabled, so the app still stops when you log out. "
            "Run: zsm service enable-linger"
        )
    console.print(f"[dim]Restart: {restart_cmd}   Logs: {logs_cmd}[/dim]")


@web_service.command(name="remove")
@click.pass_context
def web_remove(ctx):
    """Stop the app and remove zsm-web.service."""
    from app.server.panel_unit import panel_unit_path, remove_panel_unit

    if IS_WINDOWS:
        print_warning("The app service is systemd only.")
        return
    if not has_systemd():
        _no_systemd_msg()
        return

    config, ni = load_context(ctx)
    dest = panel_unit_path(config)
    if not dest.exists():
        print_warning(f"No app service found at {dest}")
        return
    if not confirm("This stops the app and removes its service. Continue?", default=True, non_interactive=ni):
        return
    if not _ensure_sudo_cli(config):
        print_error("Aborted — sudo authentication required.")
        ctx.exit(1)
    result = remove_panel_unit(config)
    if not result.ok:
        print_error(result.message or "Failed to remove the app service.")
        ctx.exit(1)
    print_success("App service removed. Start the app by hand with 'zsm web'.")


@service.command(name="install-timers")
@click.pass_context
def install_timers_cmd(ctx):
    """Install systemd timers for all enabled scheduled tasks (headless).

    Writes/enables a .timer + .service pair per enabled task in the unit
    directory of the service scope (user or system), and removes the units of
    tasks that are disabled or gone. The headless equivalent of the Scheduler
    page's Install button — used by the container bootstrap so schedules are
    (re)installed from config on every boot and survive a container recreate.
    """
    if IS_WINDOWS:
        print_warning("Timer install via systemd is Linux-only.")
        return
    if not has_systemd():
        _no_systemd_msg()
        return

    import asyncio

    from app.server.scheduler import install_timers_async, load_tasks

    config, ni = load_context(ctx)
    if not [t for t in load_tasks(config) if t.enabled]:
        console.print("[dim]No enabled scheduled tasks — nothing to install.[/dim]")
        return
    if not _ensure_sudo_cli(config):
        print_error("Aborted — sudo authentication required.")
        return
    try:
        installed = asyncio.run(install_timers_async(config))
    except Exception as e:
        print_error(f"Failed to install timers: {e}")
        return
    print_success(f"Installed {len(installed)} timer(s): {', '.join(installed)}")


@service.command(name="remove-timers")
@click.pass_context
def remove_timers_cmd(ctx):
    """Stop and remove this instance's scheduled-task timers (Linux).

    The tasks stay in config.json; `install-timers` puts the timers back.
    Only this instance's units are touched (plans/44 step 6).
    """
    if IS_WINDOWS:
        print_warning("Timer removal via systemd is Linux-only.")
        return
    if not has_systemd():
        _no_systemd_msg()
        return

    import asyncio

    from app.server.scheduler import remove_timers_async

    config, ni = load_context(ctx)
    if not _ensure_sudo_cli(config):
        print_error("Aborted — sudo authentication required.")
        return
    try:
        removed = asyncio.run(remove_timers_async(config))
    except Exception as e:
        print_error(f"Failed to remove timers: {e}")
        return
    if removed:
        print_success(f"Removed {len(removed)} timer(s): {', '.join(removed)}")
    else:
        console.print("[dim]No timers installed — nothing to remove.[/dim]")


@service.command(name="update")
@click.pass_context
def service_update(ctx):
    """Stop server, regenerate service/scripts, and start."""
    config, ni = load_context(ctx)

    if not confirm("This will stop the server, update the service, and restart. Continue?", default=True, non_interactive=ni):
        return

    if IS_WINDOWS:
        console.print("[bold]Stopping server...[/bold]")
        _windows_stop_server()

        console.print("[bold]Regenerating start/stop scripts...[/bold]")
        write_windows_scripts(config)

        console.print("[bold]Starting server...[/bold]")
        launched = _windows_launch_start_bat(config)
        if not launched.ok:
            print_error(launched.message)
            return
        print_success("Server updated and restarted.")
    else:
        if not has_systemd():
            _no_systemd_msg()
            return

        try:
            rendered = _render_service(config)
        except ServiceUserError:
            rendered = None  # reported below, after the stop, as before
        if rendered is not None and not _may_replace_unit(config, rendered, ni=ni, replace=False):
            return

        if not _ensure_sudo_cli(config):
            print_error("Aborted — sudo authentication required.")
            return

        from app.server.service_control import clear_stop_failure, restarts_this_run

        console.print("[bold]Stopping service...[/bold]")
        restarts = restarts_this_run(config)
        if _systemctl("stop", check=False, config=config):
            # A server left stopped below must stay stopped at the next restart task.
            clear_stop_failure(config, restarts)

        console.print("[bold]Regenerating service file...[/bold]")
        try:
            content = _render_service(config)
        except ServiceUserError as e:
            print_error(str(e))
            print_warning("Service left stopped — fix the above and re-run 'zsm service update'.")
            return
        output_path = Path(config.server_dir) / server_unit(config)
        output_path.write_text(content, encoding="utf-8")

        console.print("[bold]Installing service...[/bold]")
        if not _install_unit_from(config, output_path):
            return

        console.print("[bold]Starting service...[/bold]")
        from app.ops.changeset_ops import take_start_snapshot

        take_start_snapshot(config)
        if _systemctl("start", config=config):
            print_success("Service updated and restarted.")
