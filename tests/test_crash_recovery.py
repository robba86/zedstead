"""Tests for the crash-loop brake and its readout (TODO C6).

Nothing here runs systemctl: `scope.query` and `systemctl` are patched in
`app.server.service_control`, the module that calls them. The autouse fixture
in conftest pins the scope to `system`, which is what the argv assertions
below expect.
"""

from __future__ import annotations

import pytest

from app.server import service_control
from app.shared import systemd_scope


def _show(**props) -> str:
    return "\n".join(f"{k}={v}" for k, v in props.items())


@pytest.fixture()
def unit(monkeypatch):
    """Answer `systemctl show` from a dict the test controls."""
    state = {"out": ""}

    monkeypatch.setattr(service_control, "has_systemd", lambda: True)
    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    # Patched on the class: `get_scope` builds the instance, so there is no
    # binding in service_control to patch instead.
    monkeypatch.setattr(
        systemd_scope.SystemdScope, "query", lambda self, *a: state["out"]
    )
    return state


# ---------------------------------------------------------------------------
# reading the watchdog
# ---------------------------------------------------------------------------


def test_a_healthy_unit_reports_no_restarts(unit, sample_config):
    unit["out"] = _show(ActiveState="active", SubState="running", NRestarts="0", Result="success")
    stats = service_control.restart_stats(sample_config)
    assert stats["count"] == 0
    assert stats["looping"] is False
    assert stats["limit_hit"] is False


def test_a_crash_loop_is_visible_as_one(unit, sample_config):
    """`running: false` alone cannot tell a stopped server from one crashing
    every thirty seconds — the panel showed the same grey box for both."""
    unit["out"] = _show(
        ActiveState="activating", SubState="auto-restart", NRestarts="7", Result="exit-code"
    )
    stats = service_control.restart_stats(sample_config)
    assert stats["looping"] is True
    assert stats["count"] == 7
    # The same state is_server_running already refuses to call running.
    assert service_control.is_server_running(sample_config) is False


def test_systemd_giving_up_is_its_own_finding(unit, sample_config):
    """start-limit-hit means the server stays down until a human starts it,
    which is the whole point of the brake and must be said out loud."""
    unit["out"] = _show(
        ActiveState="failed", SubState="failed", NRestarts="3", Result="start-limit-hit"
    )
    stats = service_control.restart_stats(sample_config)
    assert stats["limit_hit"] is True
    assert stats["looping"] is False


def test_an_automatic_restart_refused_by_the_limit_is_a_give_up(unit, sample_config):
    """What systemd 255 really leaves behind when the brake stops a crash
    loop — copied from `systemctl show` in the test container, 2026 Q3
    (plans/42 stage 7.3). `Result` stays `signal`; only `start-limit-hit` was
    being checked, so the panel and doctor stayed silent for a server that
    was down for good."""
    unit["out"] = _show(
        ActiveState="failed", SubState="failed", NRestarts="3", Result="signal",
        RestartUSec="10s",
        ExecMainExitTimestampMonotonic="241771084546",
        InactiveEnterTimestampMonotonic="241781118328",
    )
    assert service_control.restart_stats(sample_config)["limit_hit"] is True


def test_a_plain_stop_that_ends_failed_is_not_a_give_up(unit, sample_config):
    """DayZ exits non-zero on SIGTERM, so `systemctl stop` leaves the unit
    `failed` too — at the exit itself, not RestartSec later. Same container,
    same day: 168 µs."""
    unit["out"] = _show(
        ActiveState="failed", SubState="failed", NRestarts="3", Result="exit-code",
        RestartUSec="30s",
        ExecMainExitTimestampMonotonic="241906843406",
        InactiveEnterTimestampMonotonic="241906843574",
    )
    assert service_control.restart_stats(sample_config)["limit_hit"] is False


def test_an_unknown_unit_reports_nothing_rather_than_zero(unit, sample_config):
    """No systemd answer is not the same as "no restarts", and a page that
    renders 0 for it would be inventing reassurance."""
    unit["out"] = ""
    assert service_control.restart_stats(sample_config) == {}


def test_a_non_numeric_restart_count_does_not_raise(unit, sample_config):
    unit["out"] = _show(ActiveState="active", SubState="running", NRestarts="", Result="success")
    assert service_control.restart_stats(sample_config)["count"] == 0


def test_windows_has_no_counter_to_report(monkeypatch, sample_config):
    """The .bat watchdog keeps no count; inventing one from log scraping is a
    different feature, not this one."""
    monkeypatch.setattr(service_control, "IS_WINDOWS", True)
    assert service_control.restart_stats(sample_config) == {}


def test_one_show_call_answers_both_questions(unit, sample_config, monkeypatch):
    """systemd_unit_state and restart_stats read the same properties; asking
    twice would double the subprocesses on a 5s status poll."""
    calls: list[tuple] = []

    def counting_query(self, *args):
        calls.append(args)
        return _show(ActiveState="active", SubState="running", NRestarts="0", Result="success")

    monkeypatch.setattr(systemd_scope.SystemdScope, "query", counting_query)

    service_control.restart_stats(sample_config)
    assert len(calls) == 1
    props = [a for a in calls[0] if a.startswith("--property=")]
    assert "--property=NRestarts" in props
    assert "--property=ActiveState" in props


# ---------------------------------------------------------------------------
# the brake itself
# ---------------------------------------------------------------------------


def test_the_unit_carries_the_start_limit_in_the_unit_section(sample_config):
    """StartLimitIntervalSec/StartLimitBurst are [Unit] keys on systemd >= 229.
    In [Service] they are silently ignored, which is a brake that isn't one."""
    text = service_control._render_service(sample_config)
    unit_section = text.split("[Service]")[0]
    assert "StartLimitBurst=3" in unit_section
    assert "StartLimitIntervalSec=300" in unit_section


def test_a_burst_of_zero_disables_the_limit_the_way_systemd_spells_it(sample_config):
    """`StartLimitBurst=0` on its own does not disable anything — the interval
    is what systemd reads as "no limit"."""
    sample_config.restart_limit_burst = 0
    text = service_control._render_service(sample_config)
    assert "StartLimitIntervalSec=0" in text


def test_the_limit_follows_the_config(sample_config):
    sample_config.restart_limit_burst = 9
    sample_config.restart_limit_interval_seconds = 60
    text = service_control._render_service(sample_config)
    assert "StartLimitBurst=9" in text
    assert "StartLimitIntervalSec=60" in text


def test_a_start_clears_a_tripped_limit_first(monkeypatch, sample_config):
    """A unit systemd gave up on refuses to start with "start request repeated
    too quickly" — from the panel that reads as a broken Start button, with the
    real reason only in the journal."""
    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    monkeypatch.setattr(service_control, "has_systemd", lambda: True)
    actions: list[str] = []

    def fake_systemctl(action, **kwargs):
        actions.append(action)
        return service_control.ServiceResult(True)

    monkeypatch.setattr(service_control, "systemctl", fake_systemctl)

    service_control.start_server(sample_config)
    assert actions == ["reset-failed", "start"]

    actions.clear()
    service_control.restart_server(sample_config)
    assert actions == ["reset-failed", "restart"]


def _stop_with(monkeypatch, sample_config, *, restarts: int, stop_ok: bool = True) -> list[str]:
    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    monkeypatch.setattr(service_control, "has_systemd", lambda: True)
    monkeypatch.setattr(service_control, "is_server_running", lambda config=None: True)
    monkeypatch.setattr(service_control, "restarts_this_run", lambda config=None: restarts)
    actions: list[str] = []
    monkeypatch.setattr(
        service_control,
        "systemctl",
        lambda action, **kw: actions.append(action)
        or service_control.ServiceResult(stop_ok or action != "stop"),
    )
    service_control.stop_server(sample_config)
    return actions


def test_stopping_a_loop_resets_nothing(monkeypatch, sample_config):
    """Stop is not a start; clearing the counter there would hide a loop that
    someone stopped to look at."""
    assert _stop_with(monkeypatch, sample_config, restarts=3) == ["stop"]


def test_a_planned_stop_leaves_the_unit_inactive_not_failed(monkeypatch, sample_config):
    """DayZ 1.30 exits 255 on SIGTERM, so a stop left the unit `failed`, and
    the restart task restarts a failed unit."""
    assert _stop_with(monkeypatch, sample_config, restarts=0) == ["stop", "reset-failed"]


def test_a_stop_that_failed_clears_nothing(monkeypatch, sample_config):
    assert _stop_with(monkeypatch, sample_config, restarts=0, stop_ok=False) == ["stop"]


def test_a_stop_between_two_crash_restarts_stops_the_unit(monkeypatch, sample_config):
    """activating/auto-restart is not running, so Stop said "already stopped"
    and systemd started the crashing server again."""
    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    monkeypatch.setattr(service_control, "is_server_running", lambda config=None: False)
    monkeypatch.setattr(
        service_control, "systemd_unit_state", lambda config=None: ("activating", "auto-restart")
    )
    monkeypatch.setattr(service_control, "restarts_this_run", lambda config=None: 3)
    actions: list[str] = []
    monkeypatch.setattr(
        service_control, "systemctl",
        lambda action, **kw: actions.append(action) or service_control.ServiceResult(True),
    )

    assert service_control.stop_server(sample_config).ok
    assert actions == ["stop"]


def test_the_cli_stop_between_two_crash_restarts_stops_the_unit(monkeypatch, sample_config):
    from click.testing import CliRunner

    from app.commands import service as service_cmd

    monkeypatch.setattr(service_cmd, "IS_WINDOWS", False)
    monkeypatch.setattr(service_cmd, "_instance_config", lambda: sample_config)
    monkeypatch.setattr(service_cmd, "is_server_running", lambda config=None: False)
    monkeypatch.setattr(
        service_control, "systemd_unit_state", lambda config=None: ("activating", "auto-restart")
    )
    monkeypatch.setattr(service_control, "restarts_this_run", lambda config=None: 3)
    actions: list[str] = []
    monkeypatch.setattr(service_cmd, "_systemctl", lambda action, **kw: actions.append(action) or True)

    result = CliRunner().invoke(service_cmd.service, ["stop"])

    assert result.exit_code == 0, result.output
    assert actions == ["stop"]
    assert "already stopped" not in result.output


def test_clear_start_limit_is_a_no_op_without_systemd(monkeypatch, sample_config):
    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    monkeypatch.setattr(service_control, "has_systemd", lambda: False)
    called: list[str] = []
    monkeypatch.setattr(service_control, "systemctl", lambda *a, **k: called.append(a))

    service_control.clear_start_limit(sample_config)
    assert called == []


# ---------------------------------------------------------------------------
# the surfaces
# ---------------------------------------------------------------------------


def test_the_status_payload_carries_the_watchdog(unit, sample_config):
    from app.web.status_poller import collect_status

    unit["out"] = _show(
        ActiveState="activating", SubState="auto-restart", NRestarts="4", Result="exit-code"
    )
    data = collect_status(sample_config)
    assert data["restarts"]["looping"] is True
    assert data["restarts"]["count"] == 4


def test_doctor_stays_quiet_when_there_is_nothing_to_report(unit, sample_config):
    from app.commands import doctor

    unit["out"] = _show(ActiveState="active", SubState="running", NRestarts="0", Result="success")
    assert doctor._check_crash_loop(sample_config) is None


def test_doctor_names_a_loop_and_a_give_up_differently(unit, sample_config):
    from app.commands import doctor

    unit["out"] = _show(
        ActiveState="activating", SubState="auto-restart", NRestarts="5", Result="exit-code"
    )
    label, ok, detail = doctor._check_crash_loop(sample_config)
    assert label == "Crash recovery"
    assert ok is False
    assert "5" in detail

    unit["out"] = _show(
        ActiveState="failed", SubState="failed", NRestarts="3", Result="start-limit-hit"
    )
    _, ok, detail = doctor._check_crash_loop(sample_config)
    assert ok is False
    assert "stays down" in detail


def test_a_past_restart_on_a_running_server_is_information_not_a_warning(unit, sample_config):
    from app.commands import doctor

    unit["out"] = _show(ActiveState="active", SubState="running", NRestarts="2", Result="success")
    label, ok, detail = doctor._check_crash_loop(sample_config)
    assert ok is True
    assert "2" in detail


def test_both_settings_are_reachable_from_the_settings_surface():
    from app.shared.settings_registry import _BY_KEY

    for key in ("restart_limit_burst", "restart_limit_interval_seconds"):
        assert key in _BY_KEY, key
        assert _BY_KEY[key].section == "Crash Recovery"


def test_the_status_payload_says_whether_rcon_is_remote(unit, sample_config):
    """plans/53 A: the Players page greys out Broadcast/Console on a stopped
    server, which is only right when RCon is this machine's."""
    from app.web.status_poller import collect_status

    unit["out"] = _show(ActiveState="inactive", SubState="dead", NRestarts="0", Result="success")
    assert collect_status(sample_config)["rcon_remote"] is False
    sample_config.rcon_host = "203.0.113.7"
    assert collect_status(sample_config)["rcon_remote"] is True


def test_the_reset_before_a_start_prints_nothing(monkeypatch, sample_config):
    """A unit that has never been started is "not loaded", and systemd says so
    on stderr: "Failed to reset failed state of unit ...". The first Start on a
    fresh install printed that in the panel's terminal (first-hour test, Docker).
    Every other systemctl call keeps its output."""
    import subprocess

    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    monkeypatch.setattr(service_control, "has_systemd", lambda: True)
    runs: list[tuple[list[str], dict]] = []

    def _run(cmd, **kwargs):
        runs.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 1)

    monkeypatch.setattr(service_control.subprocess, "run", _run)

    service_control.clear_start_limit(sample_config)
    service_control.systemctl("start", config=sample_config)

    (reset_cmd, reset_kwargs), (start_cmd, start_kwargs) = runs
    assert "reset-failed" in reset_cmd and "start" in start_cmd
    assert reset_kwargs["stdout"] is subprocess.DEVNULL and reset_kwargs["stderr"] is subprocess.DEVNULL
    assert "stdout" not in start_kwargs and "stderr" not in start_kwargs
