"""zsm supervise — headless foreground supervisor for the DayZ server.

Owns the ``DayZServer`` process (launch + relaunch on exit, like the systemd
unit's ``Restart=always`` or the Windows ``start-server.bat`` watchdog) and runs
the scheduler in-process — timed restarts and updates with in-game RCON
warnings, plus the backup / mod_check / wipe tasks. Built for environments without systemd,
primarily the Docker container where this is the container's PID-1 workload.

It reuses the exact same launch command builder as the systemd unit
(`_get_template_replacements`) and the existing RCON / scheduler code, so the
launched server and the warning behaviour match a host install. Purely additive:
no host (Windows / systemd-Linux) behaviour changes.
"""

from __future__ import annotations

import asyncio
import shlex
import signal
import sys
from datetime import datetime, timezone

import click
from rich.console import Console

from app.commands.context import load_context
from app.ops.changeset_ops import take_start_snapshot
from app.rcon.rcon_ops import is_rcon_configured
from app.server.service_control import build_launch_argv
from app.rcon.rcon_scheduler import (
    RestartWarningState,
    run_warning_cycle,
    send_lock_and_shutdown,
)
from app.server.scheduler import RESTART_TASK_TYPES, load_tasks, task_command
from app.shared.platform import IS_WINDOWS

console = Console()

SERVER_BINARY = "DayZServer"
_RELAUNCH_DELAY = 10        # seconds before relaunch after a normal exit (RestartSec)
_RAPID_EXIT_SECS = 30       # exits faster than this are treated as a crash loop
_MAX_BACKOFF = 120
_SCHED_TICK = 20            # scheduler poll interval (seconds)
_GRACE_SECS = 80            # how long to wait for a graceful #shutdown


def _build_server_argv(config) -> list[str]:
    """Build the DayZServer launch argv (shared source: same as the systemd
    unit's ExecStart, so the mod string / paths / args match a host launch)."""
    return build_launch_argv(config)


class Supervisor:
    """Launch + supervise DayZServer and run the scheduler, all in one loop."""

    def __init__(self, config) -> None:
        self.config = config
        self.server_dir = config.server_dir_path
        self._proc: asyncio.subprocess.Process | None = None
        self._stop = asyncio.Event()
        # Cleared while a task that restarts the server runs its command, so
        # the run loop does not relaunch DayZServer underneath it.
        self._may_run = asyncio.Event()
        self._may_run.set()
        self._rcon = None
        self._warn_state = RestartWarningState(
            thresholds=config.restart_warning_minutes,
            template=config.restart_warning_template,
        )

    # -- server process ------------------------------------------------------

    async def _run_server_loop(self) -> None:
        """Launch DayZServer and relaunch on exit until shutdown is requested."""
        backoff = _RELAUNCH_DELAY
        while not self._stop.is_set():
            await self._wait_may_run()
            if self._stop.is_set():
                break
            argv = _build_server_argv(self.config)
            console.print(
                f"[bold green]Launching {SERVER_BINARY}[/] on :{self.config.server_port}"
            )
            started = datetime.now(timezone.utc)
            await asyncio.to_thread(take_start_snapshot, self.config)
            self._proc = await asyncio.create_subprocess_exec(
                *argv, cwd=str(self.server_dir)
            )
            await self._proc.wait()
            if self._stop.is_set():
                break
            ran = (datetime.now(timezone.utc) - started).total_seconds()
            if ran < _RAPID_EXIT_SECS:
                backoff = min(backoff * 2, _MAX_BACKOFF)
                console.print(
                    f"[yellow]{SERVER_BINARY} exited after {ran:.0f}s — "
                    f"relaunching in {backoff}s[/]"
                )
            else:
                backoff = _RELAUNCH_DELAY
                console.print(
                    f"[yellow]{SERVER_BINARY} exited — relaunching in {backoff}s[/]"
                )
            await self._sleep_or_stop(backoff)

    async def _restart_server(self) -> None:
        """Gracefully stop the server (RCON #lock/#shutdown); the run loop relaunches."""
        self._warn_state.reset()
        if self._rcon is not None and self._rcon.is_connected():
            console.print("[bold]Scheduled restart — locking + shutting down server[/]")
            await send_lock_and_shutdown(self._rcon)
        await self._wait_for_exit()

    async def _wait_for_exit(self) -> None:
        """Give the server ``_GRACE_SECS`` to exit, then terminate it."""
        proc = self._proc
        if proc is not None and proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=_GRACE_SECS)
            except asyncio.TimeoutError:
                proc.terminate()
                await proc.wait()

    # -- scheduler -----------------------------------------------------------

    async def _scheduler_loop(self) -> None:
        """Poll scheduled tasks; fire restarts (with warnings) and other tasks."""
        if not self.config.scheduler_enabled:
            return
        next_fire: dict[str, datetime] = {}
        while not self._stop.is_set():
            now = datetime.now(timezone.utc)
            tasks = [t for t in load_tasks(self.config) if t.enabled]
            ids = {t.id for t in tasks}
            for stale in [k for k in next_fire if k not in ids]:
                next_fire.pop(stale, None)
            for t in tasks:
                if t.id not in next_fire and t.next_run is not None:
                    next_fire[t.id] = t.next_run

            # In-game warnings approaching the soonest restart.
            restart_times = [
                next_fire[t.id]
                for t in tasks
                if t.task_type in RESTART_TASK_TYPES and t.id in next_fire
            ]
            if restart_times:
                remaining = max(0, int((min(restart_times) - now).total_seconds() // 60))
                await run_warning_cycle(
                    self._warn_state, remaining, self._rcon, self.config
                )

            # Fire any task whose scheduled time has passed.
            for t in tasks:
                due = next_fire.get(t.id)
                if due is not None and now >= due:
                    await self._fire_task(t)
                    nxt = t.next_run
                    next_fire[t.id] = nxt if nxt is not None else now

            await self._sleep_or_stop(_SCHED_TICK)

    async def _fire_task(self, task) -> None:
        """Run one due scheduled task."""
        if task.task_type == "restart":
            await self._restart_server()
            return
        # task_command, not TASK_COMMANDS: a payload type runs
        # `scheduler run <id>` and reads its text out of config.json, so
        # nothing user-typed is ever split into argv here either.
        command = task_command(task)
        if not command:
            return
        if task.task_type not in RESTART_TASK_TYPES:
            await self._run_task_command(task, command)
            return
        # Without systemd, `is_server_running` cannot see this process's
        # DayZServer, so the command would run against a live server and never
        # start it again. Stop it here and relaunch it whatever the outcome.
        self._may_run.clear()
        try:
            await self._restart_server()
            await self._run_task_command(task, command)
        finally:
            self._may_run.set()

    async def _run_task_command(self, task, command: str) -> None:
        console.print(f"[bold]Scheduled task:[/] {task.task_type} ({command})")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "app", "-y", *shlex.split(command)
        )
        rc = await proc.wait()
        if rc:
            console.print(
                f"[red]Scheduled task {task.task_type} ({command}) failed "
                f"with exit code {rc}[/]"
            )

    # -- lifecycle -----------------------------------------------------------

    async def _wait_may_run(self) -> None:
        """Wait until no task holds the server down, or shutdown is requested."""
        if self._may_run.is_set():
            return
        waits = [asyncio.ensure_future(e.wait()) for e in (self._may_run, self._stop)]
        try:
            await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for w in waits:
                w.cancel()

    async def _sleep_or_stop(self, timeout: float) -> None:
        """Sleep up to ``timeout`` seconds, waking early if shutdown is requested."""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    def _request_stop(self) -> None:
        if not self._stop.is_set():
            console.print("[yellow]Shutdown signal received — stopping server...[/]")
            self._stop.set()

    async def _graceful_shutdown(self) -> None:
        if self._rcon is not None and self._rcon.is_connected():
            try:
                await self._rcon.send_command("#shutdown")
            except Exception:
                pass
        await self._wait_for_exit()
        if self._rcon is not None:
            await self._rcon.stop()

    async def run(self) -> int:
        binary = self.server_dir / SERVER_BINARY
        if not binary.exists():
            console.print(
                f"[red]{SERVER_BINARY} not found in {self.server_dir}.[/]\n"
                "Install it first ('zsm setup' / 'zsm run all', or mount a "
                "prepared server dir)."
            )
            return 1

        if is_rcon_configured(self.config):
            from app.rcon.rcon_manager import RconManager

            self._rcon = RconManager(self.config)
            self._rcon.start()
        else:
            console.print(
                "[dim]RCON not configured — running without in-game restart "
                "warnings.[/]"
            )

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._request_stop)
            except NotImplementedError:
                pass  # e.g. non-main thread

        server_task = asyncio.create_task(self._run_server_loop())
        sched_task = asyncio.create_task(self._scheduler_loop())

        await self._stop.wait()
        await self._graceful_shutdown()

        for task in (server_task, sched_task):
            task.cancel()
        await asyncio.gather(server_task, sched_task, return_exceptions=True)
        return 0


@click.command()
@click.pass_context
def supervise(ctx) -> None:
    """Run the DayZ server in the foreground with relaunch + scheduler (headless).

    The supervised, no-systemd equivalent of the service + scheduler: launches
    and relaunches the server, runs scheduled restarts with in-game RCON
    warnings, and runs backup/mod-check/wipe tasks. Intended for containers.
    """
    if IS_WINDOWS:
        console.print(
            "[red]'zsm supervise' is Linux-only. On Windows use the generated "
            "start-server.bat watchdog.[/]"
        )
        ctx.exit(1)
    config, _ni = load_context(ctx)
    rc = asyncio.run(Supervisor(config).run())
    if rc:
        ctx.exit(rc)
