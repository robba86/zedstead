"""Shared service file generation/installation logic.

Used by the CLI commands, the update pipeline and the web routers (mods,
service, update, maps, cleanup).
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from rich.markup import escape

from app.server import unit_guard
from app.shared.config import DayzConfig
from app.shared.platform import IS_WINDOWS, has_systemd, sudo_prefix


def generate_service_files(config: DayzConfig) -> None:
    """Generate and install service files (sync, for CLI and blocking contexts).

    On Linux: renders .service file, copies to systemd dir, reloads daemon.
    On Windows: renders start/stop .bat files.
    """
    from app.commands.service import _render_service, SYSTEMD_DIR
    from app.server.service_control import write_windows_scripts
    from app.shared.systemd_scope import server_unit

    server_dir = Path(config.server_dir)
    unit = server_unit(config)  # this instance's (plans/44 step 6)
    if not server_dir.is_dir():
        # It writes into the server dir first, so with none the admin read
        # "[Errno 2] No such file or directory".
        from app.server.service_control import ServerNotInstalled, server_not_installed

        raise ServerNotInstalled(server_not_installed(config))

    if IS_WINDOWS:
        write_windows_scripts(config)
    else:
        from app.shared.systemd_scope import get_scope

        content = _render_service(config)
        service_path = server_dir / unit
        service_path.write_text(content, encoding="utf-8")
        if not has_systemd():
            # No systemd (container / non-systemd host): the unit file is written
            # to the server dir but installing it system-wide is meaningless.
            return
        # A unit zsm did not write, or one edited by hand, is left alone
        # (plans/78 D2); UnitNotOurs carries the sentence that says so.
        unit_guard.guard(config, content)
        scope = get_scope(config)
        if scope.user_mode:
            scope.unit_dir.mkdir(parents=True, exist_ok=True)
            (scope.unit_dir / unit).write_text(content, encoding="utf-8")
            subprocess.run(
                scope.systemctl_argv("daemon-reload"),
                check=True,
                env=scope.subprocess_env(),
            )
        else:
            dest = SYSTEMD_DIR / unit
            subprocess.run([*sudo_prefix(), "cp", str(service_path), str(dest)], check=True)
            subprocess.run([*sudo_prefix(), "systemctl", "daemon-reload"], check=True)
        unit_guard.record(config, content)


async def generate_service_files_async(runner, config: DayzConfig) -> bool:
    """Generate and install service files (async, through a job *runner*).

    Returns True on success, False on failure.
    """
    from app.commands.service import _render_service, SYSTEMD_DIR
    from app.server.service_control import write_windows_scripts
    from app.shared.systemd_scope import server_unit

    server_dir = Path(config.server_dir)
    unit = server_unit(config)  # this instance's (plans/44 step 6)
    if not server_dir.is_dir():
        # It writes into the server dir first, so with none the admin read
        # "[Errno 2] No such file or directory".
        from app.server.service_control import server_not_installed

        runner.write(f"[red]{server_not_installed(config)}[/red]")
        return False

    if IS_WINDOWS:
        write_windows_scripts(config)
        runner.write("[green]\u2713 start-server.bat and stop-server.bat regenerated.[/green]")
        return True
    else:
        content = _render_service(config)
        output_path = server_dir / unit
        output_path.write_text(content, encoding="utf-8")
        runner.write("[green]\u2713 Service file regenerated.[/green]")

        if not has_systemd():
            # No systemd (container / non-systemd host): skip system-wide install.
            runner.write(
                "[yellow]systemd not detected \u2014 skipping service install "
                "(unit written to server dir).[/yellow]"
            )
            return True

        from app.shared.systemd_scope import get_scope

        refused = unit_guard.check(config, content)
        if not refused.may_replace:
            # Not a failure: leaving the admin's unit alone is the point
            # (plans/78 D2), and a failed finalize would roll an update back.
            runner.write(f"[yellow]{escape(refused.refusal(config))}[/yellow]")
            return True

        runner.write("[dim]Installing service...[/dim]")
        scope = get_scope(config)
        if scope.user_mode:
            scope.unit_dir.mkdir(parents=True, exist_ok=True)
            (scope.unit_dir / unit).write_text(content, encoding="utf-8")
            # Recorded as soon as it is written, or the next install reads
            # Zedstead's own unit as a hand edit and refuses to replace it.
            unit_guard.record(config, content)
            # Not through the runner, which passes no env: a headless panel
            # (`nohup zsm web`, a timer) can lack XDG_RUNTIME_DIR, and then
            # systemctl --user finds no bus. A failed reload leaves systemd on
            # the old unit, so it is a failure, never "reloaded".
            reload = await asyncio.to_thread(
                subprocess.run,
                scope.systemctl_argv("daemon-reload"),
                capture_output=True,
                text=True,
                env=scope.subprocess_env(),
            )
            if reload.returncode != 0:
                detail = (reload.stderr or reload.stdout or "").strip()
                runner.write(
                    "[red]\u2717 Service file written, but systemctl --user "
                    f"daemon-reload failed: {escape(detail)}[/red]"
                )
                return False
            runner.write("[green]\u2713 Service installed and daemon reloaded.[/green]")
            return True
        dest = SYSTEMD_DIR / unit
        rc = await runner.run_command([*sudo_prefix(), "cp", str(output_path), str(dest)])
        if rc == 0:
            await runner.run_command([*sudo_prefix(), "systemctl", "daemon-reload"])
            unit_guard.record(config, content)
            runner.write("[green]\u2713 Service installed and daemon reloaded.[/green]")
            return True
        else:
            runner.write("[red]\u2717 Service install failed.[/red]")
            return False
