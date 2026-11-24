"""Tests for app/ops/firewall_revert.py and firewall_ops.build_revert_commands
(plans/34 B).

**No test here may run a real systemd-run, systemctl, sudo, ufw or
firewall-cmd, or wait for a real timer.** The autouse guard below turns any
real subprocess call into a failure; every test injects its own fakes.
"""

from __future__ import annotations

import subprocess

import pytest

from app.ops import firewall_ops, firewall_revert
from app.ops.firewall_revert import (
    ALREADY_GONE,
    COULD_NOT_TELL,
    DISARMED,
    STILL_PENDING,
    TIMER,
    arm_revert_timer,
    disarm_revert_timer,
    revert_timer_available,
)
from app.shared.config import DayzConfig


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture(autouse=True)
def _no_real_processes(monkeypatch):
    def refuse(argv, *_a, **_k):
        raise AssertionError(f"test ran a real process: {' '.join(argv)}")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(firewall_ops.subprocess, "run", refuse)
    monkeypatch.setattr(firewall_revert, "sudo_prefix", lambda: ["sudo"])


class _FakeScope:
    """Stands in for a system SystemdScope: `states` is what successive
    `show` reads return, in order."""

    def __init__(self, *states: str):
        self.states = list(states)
        self.queries = 0

    def query(self, *args, timeout=5.0):  # noqa: ARG002
        self.queries += 1
        return self.states.pop(0) if self.states else ""

    def systemctl_argv(self, *args, sudo=True):  # noqa: ARG002
        return ["sudo", "systemctl", *args]


ACTIVE = "LoadState=loaded\nActiveState=active\n"
GONE = "LoadState=not-found\nActiveState=inactive\n"
INACTIVE = "LoadState=loaded\nActiveState=inactive\n"


def _which(name):
    return {"ufw": "/usr/sbin/ufw", "firewall-cmd": "/usr/bin/firewall-cmd",
            "systemd-run": "/usr/bin/systemd-run"}.get(name)


# ── build_revert_commands ────────────────────────────────────────────────


def test_ufw_reverts_by_disabling(tmp_path):
    cfg = DayzConfig(server_dir=str(tmp_path))
    assert firewall_ops.build_revert_commands(cfg, backend="ufw") == [["ufw", "disable"]]


def test_firewalld_reverts_the_zone_it_resolved_at_arm_time(tmp_path, monkeypatch):
    monkeypatch.setattr(firewall_ops, "_run", lambda argv, timeout=10: (0, "drop-me\n", ""))
    cfg = DayzConfig(server_dir=str(tmp_path))
    assert firewall_ops.build_revert_commands(cfg, backend="firewalld") == [
        ["firewall-cmd", "--permanent", "--set-target=default", "--zone=drop-me"],
        ["firewall-cmd", "--reload"],
    ]


@pytest.mark.parametrize("backend", ["ufw", "firewalld"])
def test_undo_hint_and_the_revert_argv_say_the_same_thing(tmp_path, monkeypatch, backend):
    # Two statements of one fact: the hint is what a locked-out operator types,
    # the argv is what the timer runs. They must not drift apart.
    monkeypatch.setattr(firewall_ops, "_run", lambda argv, timeout=10: (0, "<zone>\n", ""))
    cfg = DayzConfig(server_dir=str(tmp_path))
    assert set(firewall_ops.UNDO_HINT) == {"ufw", "firewalld"}
    for cmd in firewall_ops.build_revert_commands(cfg, backend=backend):
        assert " ".join(cmd) in firewall_ops.UNDO_HINT[backend]


# ── arm ──────────────────────────────────────────────────────────────────


def test_arming_a_ufw_revert_builds_one_timer_running_one_command():
    calls = []

    def run(argv, **_k):
        calls.append(argv)
        return _Proc(0)

    assert arm_revert_timer([["ufw", "disable"]], seconds=120, run=run, which=_which).ok
    (argv,) = calls
    assert argv[:2] == ["sudo", "systemd-run"]
    assert "--unit=zsm-firewall-revert" in argv
    assert "--on-active=120" in argv
    assert argv[-2:] == ["/usr/sbin/ufw", "disable"]


def test_arming_a_firewalld_revert_runs_both_commands_in_one_unit():
    calls = []

    def run(argv, **_k):
        calls.append(argv)
        return _Proc(0)

    arm_revert_timer(
        [["firewall-cmd", "--permanent", "--set-target=default", "--zone=public"],
         ["firewall-cmd", "--reload"]],
        run=run,
        which=_which,
    )
    (argv,) = calls
    assert argv[-3:-1] == ["/bin/sh", "-c"]
    assert argv[-1] == (
        "/usr/bin/firewall-cmd --permanent --set-target=default --zone=public"
        " && /usr/bin/firewall-cmd --reload"
    )


def test_a_taken_unit_name_is_reported_as_a_stale_timer_not_retried():
    calls = []

    def run(argv, **_k):
        calls.append(argv)
        return _Proc(1, stderr=f"Failed to start transient timer unit: Unit {TIMER} already exists.")

    result = arm_revert_timer([["ufw", "disable"]], run=run, which=_which)
    assert not result.ok and result.stale
    assert "sudo systemctl stop zsm-firewall-revert.timer" in result.reason
    assert len(calls) == 1  # never retried under a different name


def test_a_failed_arm_carries_the_reason():
    def run(argv, **_k):
        raise FileNotFoundError("systemd-run")

    result = arm_revert_timer([["ufw", "disable"]], run=run, which=_which)
    assert not result.ok and not result.stale
    assert "could not run systemd-run" in result.reason


# ── disarm: four answers, and only one of them is success ────────────────


def _stop_recorder():
    calls = []

    def run(argv, **_k):
        calls.append(argv)
        return _Proc(0)

    return calls, run


def test_disarm_verifies_the_timer_is_gone():
    calls, run = _stop_recorder()
    assert disarm_revert_timer(scope=_FakeScope(ACTIVE, GONE), run=run) == DISARMED
    assert calls == [["sudo", "systemctl", "stop", TIMER]]


def test_a_timer_that_survives_the_stop_is_still_pending():
    _calls, run = _stop_recorder()
    assert disarm_revert_timer(scope=_FakeScope(ACTIVE, ACTIVE), run=run) == STILL_PENDING


def test_a_verify_that_could_not_read_is_not_success():
    _calls, run = _stop_recorder()
    assert disarm_revert_timer(scope=_FakeScope(ACTIVE, ""), run=run) == COULD_NOT_TELL


@pytest.mark.parametrize("before", [GONE, INACTIVE])
def test_a_timer_already_gone_before_the_stop_is_not_called_disarmed(before):
    # It fired (the revert ran) or someone removed it — the firewall may not be
    # what --apply left, so "disarmed" would be the lie that matters most.
    calls, run = _stop_recorder()
    assert disarm_revert_timer(scope=_FakeScope(before), run=run) == ALREADY_GONE
    assert calls == []


def test_an_unreadable_first_look_still_stops_and_verifies():
    calls, run = _stop_recorder()
    assert disarm_revert_timer(scope=_FakeScope("", GONE), run=run) == DISARMED
    assert calls


# ── availability — read-only, arms nothing ───────────────────────────────


def test_no_systemd_run_means_no_net(monkeypatch):
    monkeypatch.setattr(firewall_revert, "IS_WINDOWS", False)
    result = revert_timer_available(scope=_FakeScope(), which=lambda _n: None)
    assert not result.ok and "systemd-run" in result.reason


def test_a_pending_timer_from_an_earlier_run_is_named(monkeypatch, tmp_path):
    monkeypatch.setattr(firewall_revert, "IS_WINDOWS", False)
    monkeypatch.setattr(firewall_revert, "_SYSTEMD_BOOTED", tmp_path)
    result = revert_timer_available(scope=_FakeScope(ACTIVE), which=_which)
    assert not result.ok
    assert "earlier run" in result.reason
    assert "sudo systemctl stop zsm-firewall-revert.timer" in result.reason


def test_a_clean_systemd_host_can_arm(monkeypatch, tmp_path):
    monkeypatch.setattr(firewall_revert, "IS_WINDOWS", False)
    monkeypatch.setattr(firewall_revert, "_SYSTEMD_BOOTED", tmp_path)
    assert revert_timer_available(scope=_FakeScope(GONE), which=_which).ok


def test_a_host_not_booted_with_systemd_cannot(monkeypatch, tmp_path):
    monkeypatch.setattr(firewall_revert, "IS_WINDOWS", False)
    monkeypatch.setattr(firewall_revert, "_SYSTEMD_BOOTED", tmp_path / "absent")
    assert not revert_timer_available(scope=_FakeScope(), which=_which).ok


def test_the_timer_scope_is_system_whatever_the_config_says():
    assert firewall_revert._scope().user_mode is False


def test_firewall_revert_does_not_import_firewall_ops():
    # The dependency runs one way: firewall_ops imports firewall_revert.
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(firewall_revert))
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert not any(name and "firewall_ops" in name for name in imported)
