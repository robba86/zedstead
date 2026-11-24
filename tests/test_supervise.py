"""Tests for the headless `zsm supervise` server supervisor + scheduler."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

from app.commands.supervise import Supervisor, _build_server_argv
from app.server.scheduler import ScheduledTask


def test_build_argv_matches_template():
    """argv is assembled from the shared template replacements (same as systemd)."""
    repl = {
        "config_path": "serverDZ.cfg",
        "mod_string": "@CF;@COT",
        "profiles_path": "/srv/profiles",
        "battleye_path": "/srv/battleye",
        "server_args": "-adminLog -cpuCount=2",
        "server_port": "2302",
    }
    # argv is now assembled by the shared service_control.build_launch_argv
    # (supervise._build_server_argv delegates to it) — patch it there.
    with patch("app.server.service_control._get_template_replacements", return_value=repl):
        argv = _build_server_argv(MagicMock())
    assert argv == [
        "./DayZServer",
        "-config=serverDZ.cfg",
        "-mod=@CF;@COT",
        "-profiles=/srv/profiles",
        "-BEpath=/srv/battleye",
        "-adminLog",
        "-cpuCount=2",
        "-port=2302",
    ]


async def test_fire_restart_task_calls_restart(sample_config):
    sup = Supervisor(sample_config)
    sup._restart_server = AsyncMock()
    task = ScheduledTask(id="r1", task_type="restart", cron="0 * * * *")
    await sup._fire_task(task)
    sup._restart_server.assert_awaited_once()


async def test_fire_backup_task_spawns_cli(sample_config):
    """A backup task runs `python -m app -y backup create` as a subprocess."""
    sup = Supervisor(sample_config)
    proc = MagicMock()
    proc.wait = AsyncMock(return_value=0)
    task = ScheduledTask(id="b1", task_type="backup", cron="0 4 * * *")
    with patch(
        "app.commands.supervise.asyncio.create_subprocess_exec",
        AsyncMock(return_value=proc),
    ) as mock_exec:
        await sup._fire_task(task)
    args = mock_exec.call_args.args
    assert args[0] == sys.executable
    assert args[1:] == ("-m", "app", "-y", "backup", "create")


async def test_restart_server_sends_lock_and_shutdown(sample_config):
    sup = Supervisor(sample_config)
    rcon = MagicMock()
    rcon.is_connected.return_value = True
    sup._rcon = rcon
    proc = MagicMock()
    proc.returncode = 0
    proc.wait = AsyncMock(return_value=0)
    sup._proc = proc
    with patch(
        "app.commands.supervise.send_lock_and_shutdown", AsyncMock(return_value=True)
    ) as mock_shutdown:
        await sup._restart_server()
    mock_shutdown.assert_awaited_once_with(rcon)


async def test_run_aborts_without_binary(sample_config):
    """No DayZServer in the server dir -> exit 1 before starting anything."""
    rc = await Supervisor(sample_config).run()
    assert rc == 1


def test_request_stop_sets_event(sample_config):
    sup = Supervisor(sample_config)
    assert not sup._stop.is_set()
    sup._request_stop()
    assert sup._stop.is_set()


def _fake_proc(returncode=0):
    proc = MagicMock()
    proc.returncode = returncode
    proc.wait = AsyncMock(return_value=returncode)
    return proc


async def test_fire_wipe_task_stops_runs_and_releases_server(sample_config):
    """A wipe stops supervise's own server first, like an update."""
    sup = Supervisor(sample_config)
    order = []

    async def restart():
        order.append(("stop", sup._may_run.is_set()))

    async def run_cmd(*args, **kwargs):
        order.append(("run", sup._may_run.is_set(), args[1:]))
        return _fake_proc(0)

    sup._restart_server = restart
    task = ScheduledTask(id="w1", task_type="wipe", cron="0 4 * * 0")
    with patch("app.commands.supervise.asyncio.create_subprocess_exec", run_cmd):
        await sup._fire_task(task)
    assert order == [
        ("stop", False),
        ("run", False, ("-m", "app", "-y", "cleanup", "wipe-server", "--restart")),
    ]
    assert sup._may_run.is_set()


async def test_fire_update_task_stops_runs_and_releases_server(sample_config):
    """An update stops supervise's own server, which `run update` cannot see
    without systemd, and keeps it down until the command has finished."""
    sup = Supervisor(sample_config)
    order = []

    async def restart():
        order.append(("stop", sup._may_run.is_set()))

    async def run_cmd(*args, **kwargs):
        order.append(("run", sup._may_run.is_set(), args[1:]))
        return _fake_proc(0)

    sup._restart_server = restart
    task = ScheduledTask(id="u1", task_type="update", cron="0 4 * * *")
    with patch("app.commands.supervise.asyncio.create_subprocess_exec", run_cmd):
        await sup._fire_task(task)
    assert order == [
        ("stop", False),
        ("run", False, ("-m", "app", "-y", "run", "update")),
    ]
    assert sup._may_run.is_set()


async def test_fire_update_task_releases_server_when_command_fails(sample_config):
    sup = Supervisor(sample_config)
    sup._restart_server = AsyncMock()
    task = ScheduledTask(id="u1", task_type="update", cron="0 4 * * *")
    with patch(
        "app.commands.supervise.asyncio.create_subprocess_exec",
        AsyncMock(return_value=_fake_proc(1)),
    ):
        await sup._fire_task(task)
    sup._restart_server.assert_awaited_once()
    assert sup._may_run.is_set()


async def test_fire_backup_task_leaves_server_alone(sample_config):
    sup = Supervisor(sample_config)
    sup._restart_server = AsyncMock()
    task = ScheduledTask(id="b1", task_type="backup", cron="0 4 * * *")
    with patch(
        "app.commands.supervise.asyncio.create_subprocess_exec",
        AsyncMock(return_value=_fake_proc(0)),
    ):
        await sup._fire_task(task)
    sup._restart_server.assert_not_awaited()


async def test_server_loop_waits_while_held(sample_config, monkeypatch):
    """The relaunch after a held stop waits until the task releases it."""
    import asyncio

    sup = Supervisor(sample_config)
    sup._may_run.clear()
    launches = []

    async def launch(*argv, **kwargs):
        launches.append(argv)
        sup._stop.set()
        return _fake_proc(0)

    monkeypatch.setattr("app.commands.supervise._build_server_argv", lambda c: ["./DayZServer"])
    monkeypatch.setattr("app.commands.supervise.take_start_snapshot", lambda c: None)
    monkeypatch.setattr("app.commands.supervise.asyncio.create_subprocess_exec", launch)
    loop_task = asyncio.create_task(sup._run_server_loop())
    await asyncio.sleep(0.05)
    assert launches == []
    sup._may_run.set()
    await asyncio.wait_for(loop_task, timeout=2)
    assert launches == [("./DayZServer",)]


async def test_update_task_gets_restart_warnings(sample_config, monkeypatch):
    """Players are warned before an update the same way as before a restart."""
    import asyncio
    from datetime import datetime, timedelta, timezone

    sample_config.scheduler_enabled = True
    sup = Supervisor(sample_config)
    task = ScheduledTask(id="u1", task_type="update", cron="0 4 * * *")
    monkeypatch.setattr(
        ScheduledTask, "next_run",
        property(lambda self: datetime.now(timezone.utc) + timedelta(minutes=10)),
    )
    monkeypatch.setattr("app.commands.supervise.load_tasks", lambda c: [task])
    warned = []

    async def warn(state, remaining, rcon, config):
        warned.append(remaining)
        sup._stop.set()

    monkeypatch.setattr("app.commands.supervise.run_warning_cycle", warn)
    await asyncio.wait_for(sup._scheduler_loop(), timeout=2)
    assert warned and 9 <= warned[0] <= 10
