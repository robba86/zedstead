"""Tests for `zsm scheduler run` — the payload half of TODO C4.

This command is what keeps user-written text out of `ExecStart=`, so what
matters here is that it reads the payload from config, sends it through the
shared RCon helpers, and picks an exit code a systemd timer can be read from.
No test opens an RCon socket.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from app.commands import scheduler as scheduler_cmd
from app.commands.scheduler import scheduler_group
from app.server.scheduler import ScheduledTask


def _invoke(args, config, monkeypatch, *, running=True, rcon=True):
    from app.rcon import rcon_ops
    from app.server import service_control

    monkeypatch.setattr(scheduler_cmd, "load_context", lambda ctx: (config, True))
    monkeypatch.setattr(service_control, "is_server_running", lambda cfg=None: running)
    monkeypatch.setattr(rcon_ops, "is_rcon_configured", lambda cfg: rcon)
    return CliRunner().invoke(scheduler_group, args)


@pytest.fixture()
def cfg(sample_config):
    sample_config.scheduled_tasks = [
        ScheduledTask(
            id="nightly-say",
            task_type="announce",
            cron="0 20 * * *",
            options={"message": "Restart in 15 minutes"},
        ).to_dict(),
        ScheduledTask(
            id="lock-late",
            task_type="rcon",
            cron="0 3 * * *",
            options={"command": "#lock"},
        ).to_dict(),
        ScheduledTask(id="b", task_type="backup", cron="0 4 * * *").to_dict(),
    ]
    return sample_config


def test_announce_goes_through_the_shared_broadcast_helper(cfg, monkeypatch):
    from app.rcon import rcon_ops

    sent: list[str] = []

    async def _send(config, message):
        sent.append(message)
        return ""

    monkeypatch.setattr(rcon_ops, "send_global_message", _send)

    result = _invoke(["run", "nightly-say"], cfg, monkeypatch)
    assert result.exit_code == 0, result.output
    assert sent == ["Restart in 15 minutes"]


def test_rcon_sends_the_command_exactly_as_typed(cfg, monkeypatch):
    from app.rcon import rcon_ops

    sent: list[str] = []

    async def _send(config, command):
        sent.append(command)
        return "locked"

    monkeypatch.setattr(rcon_ops, "rcon_send", _send)

    result = _invoke(["run", "lock-late"], cfg, monkeypatch)
    assert result.exit_code == 0, result.output
    assert sent == ["#lock"]


def test_a_stopped_server_is_not_a_failure(cfg, monkeypatch):
    """A nightly announcement timer must not go red for a server the admin
    stopped on purpose — there is simply nobody to announce to."""
    result = _invoke(["run", "nightly-say"], cfg, monkeypatch, running=False)
    assert result.exit_code == 0
    assert "not running" in result.output


def test_no_rcon_configured_is_a_failure(cfg, monkeypatch):
    """Here the task genuinely cannot do its job, unlike a stopped server."""
    result = _invoke(["run", "nightly-say"], cfg, monkeypatch, rcon=False)
    assert result.exit_code == 1
    assert "not configured" in result.output


def test_an_rcon_error_exits_non_zero(cfg, monkeypatch):
    from app.rcon import rcon_ops

    async def _boom(config, message):
        raise rcon_ops.RconError("RCON did not answer")

    monkeypatch.setattr(rcon_ops, "send_global_message", _boom)

    result = _invoke(["run", "nightly-say"], cfg, monkeypatch)
    assert result.exit_code == 1
    assert "did not answer" in result.output


def test_an_unknown_task_is_an_error(cfg, monkeypatch):
    result = _invoke(["run", "nope"], cfg, monkeypatch)
    assert result.exit_code == 1
    assert "No scheduled task" in result.output


def test_a_task_type_with_no_payload_says_what_its_timer_runs(cfg, monkeypatch):
    result = _invoke(["run", "b"], cfg, monkeypatch)
    assert result.exit_code == 1
    assert "backup create" in result.output


def test_list_shows_the_payload(cfg, monkeypatch):
    result = _invoke(["list"], cfg, monkeypatch)
    assert result.exit_code == 0, result.output
    assert "nightly-say" in result.output
    assert "#lock" in result.output


def test_a_remote_rcon_host_is_sent_to_whatever_this_machine_runs(cfg, monkeypatch):
    """With `rcon_host` on another machine, this box's unit says nothing about
    that server (app/rcon/CLAUDE.md), and every announcement was skipped as
    "not running" because no server runs here."""
    from app.rcon import rcon_ops

    sent: list[str] = []

    async def _send(config, message):
        sent.append(message)
        return ""

    monkeypatch.setattr(rcon_ops, "send_global_message", _send)
    cfg.rcon_host = "203.0.113.7"

    result = _invoke(["run", "nightly-say"], cfg, monkeypatch, running=False)
    assert result.exit_code == 0, result.output
    assert sent == ["Restart in 15 minutes"]


@pytest.mark.parametrize("text", ["Rules: discord.gg/x [/rules]", "Restart in 5 [min]"])
def test_a_payload_is_printed_as_typed(cfg, monkeypatch, text):
    """The payload was printed as Rich markup: a "[/rules]" in it raised
    MarkupError before anything was sent, and "[min]" vanished from the line."""
    from app.rcon import rcon_ops

    sent: list[str] = []

    async def _send(config, message):
        sent.append(message)
        return ""

    monkeypatch.setattr(rcon_ops, "send_global_message", _send)
    cfg.scheduled_tasks[0]["options"]["message"] = text

    result = _invoke(["run", "nightly-say"], cfg, monkeypatch)
    assert result.exit_code == 0, result.output
    assert sent == [text]
    assert text in result.output
