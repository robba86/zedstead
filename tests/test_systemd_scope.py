"""Tests for the systemd scope abstraction (app/shared/systemd_scope.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.shared import systemd_scope as ss
from app.shared.config import DayzConfig


@pytest.fixture(autouse=True)
def _no_scope_env(monkeypatch):
    """This module drives the real resolver, so a ZSM_SERVICE_SCOPE inherited
    from the host (or the Docker image) would outrank every rule under test and
    make the results depend on where the suite runs. Tests that want it set it
    explicitly."""
    monkeypatch.delenv(ss.SERVICE_SCOPE_ENV, raising=False)


# --- resolve_service_scope ------------------------------------------------

def test_env_scope_wins_over_config_and_heuristics(monkeypatch, tmp_path):
    """$ZSM_SERVICE_SCOPE outranks everything, including an explicit config.

    The Docker image sets it to "system": that container runs systemd as PID 1
    and cannot run user-scope units at all, so a stale `service_scope: "user"`
    in a bind-mounted config.json must not route Start/Stop through
    `systemctl --user`. It also has to hold when ZSM_WRITE_CONFIG=0 skips
    container-init, which is what pins the config value.
    """
    monkeypatch.setattr(ss, "IS_ROOT", False)
    monkeypatch.setattr(ss, "SYSTEM_UNIT_DIR", tmp_path)  # no unit file
    monkeypatch.setenv(ss.SERVICE_SCOPE_ENV, "system")

    # Would otherwise be "user" by both rule 1 (explicit) and rule 4 (default).
    assert ss.resolve_service_scope(DayzConfig(service_scope="user")) == "system"
    assert ss.resolve_service_scope(DayzConfig()) == "system"


def test_env_scope_user_is_honored(monkeypatch, tmp_path):
    """The override is not a system-only backdoor — it declares either scope."""
    monkeypatch.setattr(ss, "IS_ROOT", True)  # would force system
    monkeypatch.setattr(ss, "SYSTEM_UNIT_DIR", tmp_path)
    (tmp_path / ss.SERVICE_NAME).write_text("[Unit]\n")  # would force system
    monkeypatch.setenv(ss.SERVICE_SCOPE_ENV, "user")
    assert ss.resolve_service_scope(DayzConfig()) == "user"


@pytest.mark.parametrize("value", ["", "  ", "bogus", "System!"])
def test_env_scope_invalid_falls_through(monkeypatch, tmp_path, value):
    """A junk value must not win, and must not crash — fall back to the rules."""
    monkeypatch.setattr(ss, "IS_ROOT", False)
    monkeypatch.setattr(ss, "SYSTEM_UNIT_DIR", tmp_path)  # no unit file
    monkeypatch.setenv(ss.SERVICE_SCOPE_ENV, value)
    assert ss.resolve_service_scope(DayzConfig()) == "user"


def test_env_scope_is_case_and_space_insensitive(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "IS_ROOT", False)
    monkeypatch.setattr(ss, "SYSTEM_UNIT_DIR", tmp_path)
    monkeypatch.setenv(ss.SERVICE_SCOPE_ENV, "  SYSTEM ")
    assert ss.resolve_service_scope(DayzConfig()) == "system"


def test_env_scope_does_not_read_config(monkeypatch, tmp_path):
    """With the env set, resolution must not touch config.json at all.

    Hot paths (the web status poll) call this repeatedly; falling back to a
    lazy DayzConfig.load_or_none() per call would be a per-poll file read.
    """
    monkeypatch.setenv(ss.SERVICE_SCOPE_ENV, "system")

    def _boom():
        raise AssertionError("config must not be loaded when the env declares scope")

    monkeypatch.setattr("app.shared.config.DayzConfig.load_or_none", staticmethod(_boom))
    assert ss.resolve_service_scope() == "system"


def test_explicit_scope_wins_over_everything(monkeypatch, tmp_path):
    # Even as root with a system unit present, explicit "user" wins.
    monkeypatch.setattr(ss, "IS_ROOT", True)
    monkeypatch.setattr(ss, "SYSTEM_UNIT_DIR", tmp_path)
    (tmp_path / ss.SERVICE_NAME).write_text("[Unit]\n")
    cfg = DayzConfig(service_scope="user")
    assert ss.resolve_service_scope(cfg) == "user"

    cfg2 = DayzConfig(service_scope="system")
    monkeypatch.setattr(ss, "IS_ROOT", False)
    assert ss.resolve_service_scope(cfg2) == "system"


def test_root_resolves_system(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "IS_ROOT", True)
    monkeypatch.setattr(ss, "SYSTEM_UNIT_DIR", tmp_path)  # no unit file
    assert ss.resolve_service_scope(DayzConfig()) == "system"


def test_existing_system_unit_resolves_system(monkeypatch, tmp_path):
    from app.server.unit_guard import ZSM_UNIT_MARKER

    monkeypatch.setattr(ss, "IS_ROOT", False)
    monkeypatch.setattr(ss, "SYSTEM_UNIT_DIR", tmp_path)
    (tmp_path / ss.SERVICE_NAME).write_text(f"[Unit]\n{ZSM_UNIT_MARKER}\n")
    assert ss.resolve_service_scope(DayzConfig()) == "system"


def test_a_system_unit_zsm_did_not_write_decides_nothing(monkeypatch, tmp_path):
    """plans/78 D2: the admin's own unit of that name is not an install of zsm's."""
    monkeypatch.setattr(ss, "IS_ROOT", False)
    monkeypatch.setattr(ss, "SYSTEM_UNIT_DIR", tmp_path)
    (tmp_path / ss.SERVICE_NAME).write_text("[Unit]\n")
    assert ss.resolve_service_scope(DayzConfig()) == "user"


def test_default_resolves_user(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "IS_ROOT", False)
    monkeypatch.setattr(ss, "SYSTEM_UNIT_DIR", tmp_path)  # no unit file
    assert ss.resolve_service_scope(DayzConfig()) == "user"


def test_unknown_scope_string_falls_through_to_auto(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "IS_ROOT", False)
    monkeypatch.setattr(ss, "SYSTEM_UNIT_DIR", tmp_path)
    assert ss.resolve_service_scope(DayzConfig(service_scope="bogus")) == "user"


# --- systemctl_argv -------------------------------------------------------

def test_systemctl_argv_user_mode():
    scope = ss.SystemdScope(user_mode=True)
    assert scope.systemctl_argv("restart", ss.SERVICE_NAME) == [
        "systemctl",
        "--user",
        "restart",
        ss.SERVICE_NAME,
    ]
    # user mode never adds sudo even when sudo=True
    assert scope.systemctl_argv("is-active", sudo=True)[:2] == ["systemctl", "--user"]


def test_systemctl_argv_system_mode(monkeypatch):
    monkeypatch.setattr(ss, "sudo_prefix", lambda: ["sudo"])
    scope = ss.SystemdScope(user_mode=False)
    assert scope.systemctl_argv("restart", ss.SERVICE_NAME) == [
        "sudo",
        "systemctl",
        "restart",
        ss.SERVICE_NAME,
    ]
    # sudo=False drops the prefix (read-only queries)
    assert scope.systemctl_argv("show") == ["sudo", "systemctl", "show"]
    assert scope.systemctl_argv("show", sudo=False) == ["systemctl", "show"]


# --- unit_dir -------------------------------------------------------------

def test_unit_dir_user_uses_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    scope = ss.SystemdScope(user_mode=True)
    assert scope.unit_dir == tmp_path / "systemd" / "user"


def test_unit_dir_system():
    scope = ss.SystemdScope(user_mode=False)
    assert scope.unit_dir == ss.SYSTEM_UNIT_DIR


# --- subprocess_env -------------------------------------------------------

def test_subprocess_env_injects_xdg_runtime_when_missing(monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(ss.os, "getuid", lambda: 1234, raising=False)
    env = ss.SystemdScope(user_mode=True).subprocess_env()
    assert env is not None
    assert env["XDG_RUNTIME_DIR"] == "/run/user/1234"


def test_subprocess_env_none_when_xdg_runtime_present(monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/9999")
    assert ss.SystemdScope(user_mode=True).subprocess_env() is None


def test_subprocess_env_none_in_system_mode():
    assert ss.SystemdScope(user_mode=False).subprocess_env() is None


# --- needs_sudo -----------------------------------------------------------

def test_needs_sudo_user_mode_false():
    assert ss.SystemdScope(user_mode=True).needs_sudo is False


def test_needs_sudo_system_mode_delegates(monkeypatch):
    monkeypatch.setattr(ss, "needs_sudo_auth", lambda: True)
    assert ss.SystemdScope(user_mode=False).needs_sudo is True
    monkeypatch.setattr(ss, "needs_sudo_auth", lambda: False)
    assert ss.SystemdScope(user_mode=False).needs_sudo is False


# --- query ----------------------------------------------------------------
#
# Regression cover for the Docker "Service already stopped" bug: the image was
# built with --no-install-recommends and the D-Bus system bus is only a
# *Recommends* of systemd, so the container had none. Root systemctl still
# worked (via /run/systemd/private) but every non-root state read failed with
# empty output, which upstream is indistinguishable from "unit is inactive" —
# the app called a live server stopped.


class _Proc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode


def _record_runs(monkeypatch, *results):
    """Patch subprocess.run to return ``results`` in order; return the argv log."""
    seen: list[list[str]] = []
    queue = list(results)

    def _run(cmd, *_a, **_k):
        seen.append(cmd)
        return queue.pop(0) if queue else _Proc()

    monkeypatch.setattr(ss.subprocess, "run", _run)
    return seen


def test_query_returns_stdout_without_sudo(monkeypatch):
    seen = _record_runs(monkeypatch, _Proc("ActiveState=active\n"))
    scope = ss.SystemdScope(user_mode=False)
    assert scope.query("show", "u", "--property=ActiveState") == "ActiveState=active\n"
    assert len(seen) == 1, "must not retry when the unprivileged query worked"
    assert seen[0][0] != "sudo"


def test_query_retries_with_sudo_n_when_bus_unreachable(monkeypatch):
    """No system bus: unprivileged systemctl exits non-zero with no stdout."""
    monkeypatch.setattr(ss, "sudo_prefix", lambda: ["sudo"])
    seen = _record_runs(
        monkeypatch,
        _Proc("", returncode=1),  # Failed to connect to bus
        _Proc("ActiveState=active\n"),
    )
    scope = ss.SystemdScope(user_mode=False)
    assert scope.query("show", "u", "--property=ActiveState") == "ActiveState=active\n"
    assert len(seen) == 2
    # -n is mandatory: these run on the tty-less web poll path, where a bare
    # `sudo` password prompt would hang the request.
    assert seen[1][:3] == ["sudo", "-n", "systemctl"]


def test_query_does_not_retry_on_nonzero_with_output(monkeypatch):
    """`is-enabled` exits non-zero while printing `disabled` — a real answer."""
    monkeypatch.setattr(ss, "sudo_prefix", lambda: ["sudo"])
    seen = _record_runs(monkeypatch, _Proc("disabled\n", returncode=1))
    scope = ss.SystemdScope(user_mode=False)
    assert scope.query("is-enabled", "u").strip() == "disabled"
    assert len(seen) == 1


def test_query_user_mode_never_sudos(monkeypatch):
    monkeypatch.setattr(ss, "sudo_prefix", lambda: ["sudo"])
    seen = _record_runs(monkeypatch, _Proc("", returncode=1))
    scope = ss.SystemdScope(user_mode=True)
    assert scope.query("show", "u") == ""
    assert len(seen) == 1
    assert seen[0][:2] == ["systemctl", "--user"]


def test_query_no_retry_when_already_root(monkeypatch):
    """Root reaches the manager directly; sudo would add nothing."""
    monkeypatch.setattr(ss, "sudo_prefix", lambda: [])
    seen = _record_runs(monkeypatch, _Proc("", returncode=1))
    assert ss.SystemdScope(user_mode=False).query("show", "u") == ""
    assert len(seen) == 1


def test_query_survives_missing_systemctl(monkeypatch):
    def _boom(*_a, **_k):
        raise FileNotFoundError()

    monkeypatch.setattr(ss.subprocess, "run", _boom)
    assert ss.SystemdScope(user_mode=False).query("show", "u") == ""


# --- linger_enabled -------------------------------------------------------

def test_linger_enabled(monkeypatch, tmp_path):
    linger = tmp_path / "linger"
    linger.mkdir()
    monkeypatch.setattr(ss, "LINGER_DIR", linger)
    monkeypatch.setattr(ss, "_current_user", lambda: "bob")
    assert ss.linger_enabled() is False
    assert ss.linger_enabled("bob") is False
    (linger / "bob").write_text("")
    assert ss.linger_enabled("bob") is True
    assert ss.linger_enabled() is True  # uses _current_user()


# --- privileged_ops_available --------------------------------------------

def test_privileged_ops_user_bus_up(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "IS_ROOT", False)
    monkeypatch.setattr(ss, "SYSTEM_UNIT_DIR", tmp_path)  # → user scope
    monkeypatch.setattr(ss, "user_bus_available", lambda: True)
    ok, reason = ss.privileged_ops_available(DayzConfig())
    assert ok is True
    assert reason == ""


def test_privileged_ops_user_bus_down(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "IS_ROOT", False)
    monkeypatch.setattr(ss, "SYSTEM_UNIT_DIR", tmp_path)
    monkeypatch.setattr(ss, "user_bus_available", lambda: False)
    monkeypatch.setattr(ss, "_current_user", lambda: "bob")
    ok, reason = ss.privileged_ops_available(DayzConfig())
    assert ok is False
    assert "enable-linger bob" in reason


def test_privileged_ops_system_sudo_ok(monkeypatch):
    monkeypatch.setattr(ss, "sudo_available_noninteractive", lambda: True)
    ok, reason = ss.privileged_ops_available(DayzConfig(service_scope="system"))
    assert ok is True
    assert reason == ""


def test_privileged_ops_system_sudo_missing(monkeypatch):
    monkeypatch.setattr(ss, "sudo_available_noninteractive", lambda: False)
    ok, reason = ss.privileged_ops_available(DayzConfig(service_scope="system"))
    assert ok is False
    assert "passwordless sudo" in reason
