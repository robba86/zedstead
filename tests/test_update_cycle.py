"""Tests for app/ops/update_cycle.py — the unattended update (TODO C3).

Nothing here runs SteamCMD, systemctl or RCon: `service_control` and
`pipeline.run_pipeline` are patched, which is the point — what is under test
is the *order* (stop, back up, update, roll back, start) and the decisions
around it, not the pipeline it wraps.
"""

from __future__ import annotations

import asyncio

import pytest

from app.ops import update_cycle
from app.ops.pipeline import PipelineStep, StepResult
from app.ops.update_cycle import PlayerGate, run_update_cycle
from tests.conftest import disabled_names, set_test_flags


class _Runner:
    async def run_command(self, cmd):  # pragma: no cover - never reached
        return 0

    def write(self, text):  # pragma: no cover - never reached
        pass


@pytest.fixture(autouse=True)
def boot_check(monkeypatch):
    """The boot check after the start (plans/73). Patched so no test waits on a
    real server; records what it was asked."""
    from app.ops import post_update

    seen: dict = {"calls": [], "report": {"outcome": post_update.JOINABLE}}

    async def _check(config, before, *, since, emit=None, timeout=None):
        seen["calls"].append({"before": before, "since": since})
        return seen["report"]

    monkeypatch.setattr(post_update, "snapshot", lambda config: post_update.UpdateSnapshot(1.0, "100"))
    monkeypatch.setattr(post_update, "check_boot", _check)
    return seen


@pytest.fixture()
def cfg(sample_config):
    sample_config.update_max_players = 0
    sample_config.auto_backup_before_restart = False
    return sample_config


@pytest.fixture()
def service(monkeypatch):
    """Records what the cycle did to the server, in order."""
    from app.server import service_control

    calls: list[str] = []
    state = {"running": True, "stop_ok": True, "start_ok": True}

    def _is_running(config=None):
        return state["running"]

    def _stop(config):
        calls.append("stop")
        if state["stop_ok"]:
            state["running"] = False
        return service_control.ServiceResult(state["stop_ok"], "" if state["stop_ok"] else "boom")

    def _start(config, anyway=False):
        assert anyway, "an automatic start must not stop at the runtime check"
        calls.append("start")
        state["running"] = state["start_ok"]
        return service_control.ServiceResult(
            state["start_ok"], "" if state["start_ok"] else "no unit"
        )

    monkeypatch.setattr(service_control, "is_server_running", _is_running)
    monkeypatch.setattr(service_control, "stop_server", _stop)
    monkeypatch.setattr(service_control, "start_server", _start)
    return {"calls": calls, "state": state}


def _fake_pipeline(monkeypatch, statuses, *, undoable=("copy_mods",), undo_fails=()):
    """Patch run_pipeline to report *statuses* and record a rollback. The
    undos named in *undo_fails* come back as failed."""
    from app.ops import pipeline

    rolled: list[str] = []

    async def _run(steps, ctx):
        results = [
            StepResult(key, key, status, 0.0, None if status != "failed" else "boom")
            for key, status in statuses.items()
        ]
        ctx.results.extend(results)
        done = [PipelineStep(k, k, _run, undo=lambda _c: None) for k in undoable]
        return results, done

    async def _rollback(steps, ctx):
        rolled.extend(s.key for s in steps)
        return [s.label for s in steps if s.key in undo_fails]

    monkeypatch.setattr(pipeline, "run_pipeline", _run)
    monkeypatch.setattr(pipeline, "rollback_pipeline", _rollback)
    return rolled


# ---------------------------------------------------------------------------
# The player gate
# ---------------------------------------------------------------------------


async def test_players_online_defer_the_update(cfg, service, monkeypatch):
    from app.rcon import rcon_ops

    cfg.rcon_password = "x"
    cfg.rcon_port = 2305
    monkeypatch.setattr(
        rcon_ops, "get_rcon_players", lambda config: _players(2)
    )
    lines: list[str] = []

    result = await run_update_cycle(cfg, emit=lines.append, runner=_Runner())

    assert result.ran is False
    assert "2 player(s) online" in result.deferred_reason
    # And crucially, it never touched the server.
    assert service["calls"] == []


async def _players(n):
    return [object()] * n


async def test_a_quiet_server_is_updated(cfg, service, monkeypatch):
    from app.rcon import rcon_ops

    cfg.rcon_password = "x"
    monkeypatch.setattr(rcon_ops, "get_rcon_players", lambda config: _players(0))
    _fake_pipeline(monkeypatch, {"copy_mods": "success"})

    result = await run_update_cycle(cfg, emit=lambda _l: None, runner=_Runner())

    assert result.ran is True
    assert service["calls"] == ["stop", "start"]


async def test_no_rcon_configured_proceeds_rather_than_deferring_forever(
    cfg, service, monkeypatch
):
    """Deferring every time would make the scheduled task a silent no-op."""
    cfg.rcon_password = ""
    gate = await update_cycle.check_players(cfg)
    assert gate.proceed is True
    assert "not configured" in gate.reason


async def test_a_stopped_server_has_nobody_online(cfg, service, monkeypatch):
    cfg.rcon_password = "x"
    service["state"]["running"] = False
    gate = await update_cycle.check_players(cfg)
    assert gate.proceed is True
    assert gate.players == 0


async def test_an_unreadable_player_list_defers(cfg, service, monkeypatch):
    """RCon configured, server up, query failed — zsm cannot tell, so it stops."""
    from app.rcon import rcon_ops

    cfg.rcon_password = "x"

    async def _boom(config):
        raise rcon_ops.RconError("connection refused")

    monkeypatch.setattr(rcon_ops, "get_rcon_players", _boom)

    gate = await update_cycle.check_players(cfg)
    assert gate.proceed is False
    assert "could not be read" in gate.reason
    assert gate.players is None


async def test_force_skips_the_gate_but_not_the_stop(cfg, service, monkeypatch):
    from app.rcon import rcon_ops

    cfg.rcon_password = "x"
    monkeypatch.setattr(rcon_ops, "get_rcon_players", lambda config: _players(30))
    _fake_pipeline(monkeypatch, {"copy_mods": "success"})

    result = await run_update_cycle(cfg, emit=lambda _l: None, runner=_Runner(), force=True)

    assert result.ran is True
    assert service["calls"] == ["stop", "start"]


async def test_the_limit_is_a_limit_not_a_boolean(cfg, service, monkeypatch):
    from app.rcon import rcon_ops

    cfg.rcon_password = "x"
    cfg.update_max_players = 3
    monkeypatch.setattr(rcon_ops, "get_rcon_players", lambda config: _players(3))
    assert (await update_cycle.check_players(cfg)).proceed is True
    monkeypatch.setattr(rcon_ops, "get_rcon_players", lambda config: _players(4))
    assert (await update_cycle.check_players(cfg)).proceed is False


async def test_minus_one_disables_the_check_entirely(cfg, service, monkeypatch):
    cfg.rcon_password = "x"
    cfg.update_max_players = -1
    gate = await update_cycle.check_players(cfg)
    assert gate.proceed is True
    assert "disabled" in gate.reason


# ---------------------------------------------------------------------------
# Stop -> update -> start
# ---------------------------------------------------------------------------


async def test_a_server_that_was_already_stopped_is_left_stopped(cfg, service, monkeypatch):
    service["state"]["running"] = False
    _fake_pipeline(monkeypatch, {"copy_mods": "success"})

    result = await run_update_cycle(cfg, emit=lambda _l: None, runner=_Runner())

    assert result.ran is True
    assert result.was_running is False
    assert service["calls"] == []  # neither stopped nor started


async def test_a_failed_stop_aborts_before_the_pipeline(cfg, service, monkeypatch):
    service["state"]["stop_ok"] = False
    ran = {"pipeline": False}

    from app.ops import pipeline

    async def _run(steps, ctx):
        ran["pipeline"] = True
        return [], []

    monkeypatch.setattr(pipeline, "run_pipeline", _run)

    result = await run_update_cycle(cfg, emit=lambda _l: None, runner=_Runner())

    assert result.ran is False
    assert ran["pipeline"] is False
    assert service["calls"] == ["stop"]


async def test_a_stop_that_never_takes_effect_aborts(cfg, service, monkeypatch):
    """`systemctl stop` returning ok is not proof the process is gone."""
    from app.server import service_control

    monkeypatch.setattr(service_control, "is_server_running", lambda config=None: True)
    monkeypatch.setattr(update_cycle, "STOP_TIMEOUT", 0.05)
    monkeypatch.setattr(update_cycle, "_STOP_POLL", 0.01)

    result = await run_update_cycle(cfg, emit=lambda _l: None, runner=_Runner())

    assert result.ran is False
    assert "did not stop" in result.deferred_reason


async def test_a_failed_pipeline_rolls_back_and_still_starts_the_server(
    cfg, service, monkeypatch
):
    rolled = _fake_pipeline(
        monkeypatch, {"copy_mods": "success", "merge_xml": "failed"}, undoable=("copy_mods",)
    )

    result = await run_update_cycle(cfg, emit=lambda _l: None, runner=_Runner())

    assert result.rolled_back is True
    assert rolled == ["copy_mods"]
    # The whole point: the server comes back, on the mods it had.
    assert service["calls"] == ["stop", "start"]
    assert result.server_started is True



async def test_a_failed_undo_is_an_error_not_a_quiet_rollback(cfg, service, monkeypatch):
    """A rollback whose undo raised can leave the server with no mods, so the
    cycle reports it as an error."""
    _fake_pipeline(
        monkeypatch, {"copy_mods": "success", "merge_xml": "failed"},
        undoable=("copy_mods",), undo_fails=("copy_mods",),
    )

    result = await run_update_cycle(cfg, emit=lambda _l: None, runner=_Runner())

    assert result.rolled_back is True
    assert result.error == "Rollback failed for: copy_mods"
    # The old mods may not be back, and a boot without a mod deletes its
    # saved objects, so the server stays stopped.
    assert service["calls"] == ["stop"]
    assert result.server_started is False

async def test_the_server_comes_back_even_when_the_pipeline_raises(
    cfg, service, monkeypatch
):
    """A crash must not leave the box offline until somebody notices."""
    from app.ops import pipeline

    async def _boom(steps, ctx):
        raise RuntimeError("pipeline exploded")

    monkeypatch.setattr(pipeline, "run_pipeline", _boom)

    with pytest.raises(RuntimeError):
        await run_update_cycle(cfg, emit=lambda _l: None, runner=_Runner())

    assert service["calls"] == ["stop", "start"]


async def test_a_dry_run_touches_nothing(cfg, service, monkeypatch):
    _fake_pipeline(monkeypatch, {"copy_mods": "dry-run"})

    result = await run_update_cycle(
        cfg, emit=lambda _l: None, runner=_Runner(), dry_run=True
    )

    assert result.ran is True
    assert service["calls"] == []


async def test_a_pre_update_backup_is_taken_when_enabled(cfg, service, monkeypatch):
    from app.ops import backup_ops

    cfg.auto_backup_before_restart = True
    made: list[str] = []
    monkeypatch.setattr(
        backup_ops,
        "create_backup",
        lambda config, contents, note, log=None: made.append(note) or None,
    )
    _fake_pipeline(monkeypatch, {"copy_mods": "success"})

    await run_update_cycle(cfg, emit=lambda _l: None, runner=_Runner())

    assert made == ["before scheduled update"]


async def test_a_failed_pre_update_backup_does_not_block_the_update(
    cfg, service, monkeypatch
):
    """A fresh install has no storage_1 yet; that must not block its first update."""
    from app.ops import backup_ops

    cfg.auto_backup_before_restart = True

    def _boom(*_a, **_k):
        raise OSError("no storage_1")

    monkeypatch.setattr(backup_ops, "create_backup", _boom)
    _fake_pipeline(monkeypatch, {"copy_mods": "success"})

    result = await run_update_cycle(cfg, emit=lambda _l: None, runner=_Runner())

    assert result.ran is True
    assert service["calls"] == ["stop", "start"]


async def test_a_server_that_does_not_come_back_is_reported(cfg, service, monkeypatch):
    service["state"]["start_ok"] = False
    _fake_pipeline(monkeypatch, {"copy_mods": "success"})

    result = await run_update_cycle(cfg, emit=lambda _l: None, runner=_Runner())

    assert result.server_started is False
    assert result.error == "no unit"


# ---------------------------------------------------------------------------
# The scheduler knows about it
# ---------------------------------------------------------------------------


def test_update_is_a_task_type_that_warns_players():
    from app.server.scheduler import RESTART_TASK_TYPES, TASK_COMMANDS, TASK_TYPE_LABELS

    assert TASK_COMMANDS["update"] == "run update"
    assert TASK_TYPE_LABELS["update"]
    # Without this the task stops the server with nobody warned — the exact
    # bug C3b removed for `restart`.
    assert "update" in RESTART_TASK_TYPES


def test_wipe_is_a_task_type_that_warns_players_and_restarts_the_server():
    """The scheduled wipe stops the server, so players are warned first, and
    its unit runs the wipe that stops and starts the server around it."""
    from app.server.scheduler import RESTART_TASK_TYPES, TASK_COMMANDS

    assert TASK_COMMANDS["wipe"] == "cleanup wipe-server --restart"
    assert "wipe" in RESTART_TASK_TYPES


def test_every_server_stopping_task_type_warns():
    """A guard against adding a stopping task and forgetting the warning half."""
    from app.server.scheduler import RESTART_TASK_TYPES, TASK_COMMANDS

    assert RESTART_TASK_TYPES <= set(TASK_COMMANDS)


# ---------------------------------------------------------------------------
# The boot after the update (plans/73)
# ---------------------------------------------------------------------------


async def test_the_boot_is_checked_after_the_server_starts(cfg, service, monkeypatch, boot_check):
    cfg.rcon_password = ""
    _fake_pipeline(monkeypatch, {"copy_mods": "success"})

    result = await run_update_cycle(cfg, emit=lambda _l: None, runner=_Runner())

    assert service["calls"] == ["stop", "start"]
    assert len(boot_check["calls"]) == 1
    assert boot_check["calls"][0]["before"].build == "100"
    assert result.boot == {"outcome": "joinable"}


async def test_a_server_left_stopped_has_no_boot_to_check(cfg, service, monkeypatch, boot_check):
    service["state"]["running"] = False
    _fake_pipeline(monkeypatch, {"copy_mods": "success"})

    result = await run_update_cycle(cfg, emit=lambda _l: None, runner=_Runner())

    assert boot_check["calls"] == []
    assert result.boot is None


async def test_a_server_that_did_not_start_has_no_boot_to_check(cfg, service, monkeypatch, boot_check):
    service["state"]["start_ok"] = False
    _fake_pipeline(monkeypatch, {"copy_mods": "success"})

    await run_update_cycle(cfg, emit=lambda _l: None, runner=_Runner())

    assert boot_check["calls"] == []


async def test_the_scheduled_path_reports_a_failed_boot_and_disables_nothing(
    cfg, service, monkeypatch, boot_check
):
    """STOP condition 3 of plans/73: no unattended path disables a mod."""
    from app.ops import post_update

    async def _never(*_a, **_k):
        raise AssertionError("an unattended update must never set a mod aside")

    monkeypatch.setattr(post_update, "set_aside", _never)
    boot_check["report"] = {"outcome": post_update.CRASHED, "suspects": [{"mod": "@Broken", "lines": []}]}
    set_test_flags(cfg, disabled=[])
    _fake_pipeline(monkeypatch, {"copy_mods": "success"})

    result = await run_update_cycle(cfg, emit=lambda _l: None, runner=_Runner())

    assert post_update.failed(result.boot)
    assert disabled_names(cfg) == []
