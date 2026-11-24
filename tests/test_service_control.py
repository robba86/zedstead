"""Tests for app/server/service_control.py — pure service-lifecycle primitives."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from app.server import service_control
from tests.conftest import set_test_flags


class _FakeCompletedProcess:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode


def _raise_file_not_found(*_a, **_k):
    raise FileNotFoundError()


# ---------------------------------------------------------------------------
# build_registry_order
# ---------------------------------------------------------------------------


def test_build_registry_order_no_custom_returns_registry(sample_config):
    order = service_control.build_registry_order(sample_config)
    assert isinstance(order, list)


def test_build_registry_order_custom_mods_come_first(sample_config):
    set_test_flags(sample_config, pinned=["MyCustomMod"])
    order = service_control.build_registry_order(sample_config)
    assert order[0] == "MyCustomMod"
    assert order.count("MyCustomMod") == 1


# ---------------------------------------------------------------------------
# is_server_running
# ---------------------------------------------------------------------------


def _fake_unit_state(active: str, sub: str):
    """Stub `systemctl show --property=ActiveState --property=SubState` output."""

    def _run(*_a, **_k):
        return _FakeCompletedProcess(f"ActiveState={active}\nSubState={sub}\n")

    return _run


def test_is_server_running_linux_active(monkeypatch):
    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    monkeypatch.setattr(
        service_control.subprocess, "run", _fake_unit_state("active", "running")
    )
    assert service_control.is_server_running() is True


def test_is_server_running_linux_inactive(monkeypatch):
    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    monkeypatch.setattr(
        service_control.subprocess, "run", _fake_unit_state("inactive", "dead")
    )
    assert service_control.is_server_running() is False


def test_is_server_running_activating_startup_is_running(monkeypatch):
    """A unit genuinely starting up still counts as running."""
    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    monkeypatch.setattr(
        service_control.subprocess, "run", _fake_unit_state("activating", "start")
    )
    assert service_control.is_server_running() is True


def test_is_server_running_activating_auto_restart_is_not_running(monkeypatch):
    """A crash-looping unit (Restart=always backoff) has no live server.

    `systemctl is-active` reports "activating" here, which used to be read as
    running — so a server failing every RestartSec showed as up.
    """
    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    monkeypatch.setattr(
        service_control.subprocess, "run", _fake_unit_state("activating", "auto-restart")
    )
    assert service_control.is_server_running() is False


def test_is_server_running_queries_substate(monkeypatch):
    """Guards the argv: SubState is what separates start from auto-restart."""
    seen: list[list[str]] = []

    def _run(cmd, *_a, **_k):
        seen.append(cmd)
        return _FakeCompletedProcess("ActiveState=active\nSubState=running\n")

    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    monkeypatch.setattr(service_control.subprocess, "run", _run)
    service_control.is_server_running()
    assert seen, "systemctl was never invoked"
    assert "show" in seen[0]
    assert "--property=SubState" in seen[0]
    assert "--property=ActiveState" in seen[0]


def test_is_server_running_windows_detected(monkeypatch):
    monkeypatch.setattr(service_control, "IS_WINDOWS", True)
    monkeypatch.setattr(
        service_control.subprocess,
        "run",
        lambda *a, **k: _FakeCompletedProcess("DayZServer_x64.exe  123"),
    )
    assert service_control.is_server_running() is True


def test_is_server_running_missing_binary_is_false(monkeypatch):
    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    monkeypatch.setattr(service_control.subprocess, "run", _raise_file_not_found)
    assert service_control.is_server_running() is False


# ---------------------------------------------------------------------------
# is_dayz_process_running
# ---------------------------------------------------------------------------


class _FakeProc:
    def __init__(self, returncode: int = 0, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout


def test_is_dayz_process_running_linux_found(monkeypatch):
    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    monkeypatch.setattr(service_control.subprocess, "run", lambda *a, **k: _FakeProc(returncode=0))
    assert service_control.is_dayz_process_running() is True


def test_is_dayz_process_running_matches_cmdline_not_comm(monkeypatch):
    """Guards the argv, not just the return code.

    The Enfusion engine renames its process to ``enfMain``, so the old
    ``pgrep -x DayZServer`` (which matches /proc/<pid>/comm) never matched a
    running server. Detection must go through ``pgrep -f`` against the full
    command line. Every earlier test stubbed subprocess.run without looking at
    argv, which is exactly why that bug survived.
    """
    seen: list[list[str]] = []

    def _run(cmd, *_a, **_k):
        seen.append(cmd)
        return _FakeProc(returncode=0)

    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    monkeypatch.setattr(service_control.subprocess, "run", _run)
    service_control.is_dayz_process_running()

    assert seen, "pgrep was never invoked"
    assert seen[0][0] == "pgrep"
    assert "-f" in seen[0], "must match the command line, not comm"
    assert "-x" not in seen[0], "comm matching can never see a renamed engine process"


def test_dayz_process_pattern_semantics():
    """The pattern hits a real server argv but not a log tail holding the name."""
    import re

    # Python has no [[:space:]]; translate for an equivalent check of intent.
    pattern = re.compile(
        service_control.DAYZ_PROCESS_PATTERN.replace("[[:space:]]", r"\s")
    )
    assert pattern.search("./DayZServer -config=serverDZ.cfg -port=2302")
    assert pattern.search("/home/zsm/server/DayZServer_x64 -config=serverDZ.cfg")
    assert pattern.search("DayZServer")
    # Must not fire on processes that merely mention the binary name.
    assert not pattern.search("tail -f /home/zsm/server/DayZServer.RPT")
    assert not pattern.search("vim DayZServer.cfg")


def test_with_a_config_only_a_server_in_its_dir_counts(monkeypatch, tmp_path):
    """Another instance's server is not this one's (plans/44)."""
    from types import SimpleNamespace

    mine, theirs = tmp_path / "main", tmp_path / "pvp"
    mine.mkdir(), theirs.mkdir()
    cwds = {"101": theirs}
    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    monkeypatch.setattr(service_control.subprocess, "run",
                        lambda *a, **k: _FakeProc(0, "\n".join(cwds) + "\n"))
    monkeypatch.setattr(service_control, "_process_cwd", lambda pid: cwds[pid])
    config = SimpleNamespace(server_dir_path=mine)

    assert service_control.is_dayz_process_running() is True
    assert service_control.is_dayz_process_running(config) is False
    cwds["102"] = mine
    assert service_control.is_dayz_process_running(config) is True


def test_an_unreadable_process_dir_still_counts(monkeypatch, tmp_path):
    def unreadable(pid):
        if pid == "7":
            raise FileNotFoundError(pid)  # exited since pgrep
        raise PermissionError(pid)

    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    monkeypatch.setattr(service_control.subprocess, "run", lambda *a, **k: _FakeProc(0, "7\n"))
    monkeypatch.setattr(service_control, "_process_cwd", unreadable)
    from types import SimpleNamespace
    config = SimpleNamespace(server_dir_path=tmp_path)

    assert service_control.is_dayz_process_running(config) is False, "a gone process is nobody's"
    monkeypatch.setattr(service_control.subprocess, "run", lambda *a, **k: _FakeProc(0, "8\n"))
    assert service_control.is_dayz_process_running(config) is True


def test_the_real_proc_cwd_is_read(tmp_path):
    import os
    assert service_control._process_cwd(str(os.getpid())) == Path.cwd().resolve()


def test_is_dayz_process_running_linux_not_found(monkeypatch):
    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    monkeypatch.setattr(service_control.subprocess, "run", lambda *a, **k: _FakeProc(returncode=1))
    assert service_control.is_dayz_process_running() is False


def test_is_dayz_process_running_missing_pgrep(monkeypatch):
    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    monkeypatch.setattr(service_control.subprocess, "run", _raise_file_not_found)
    assert service_control.is_dayz_process_running() is False


def test_is_dayz_process_running_windows_delegates_to_tasklist(monkeypatch):
    monkeypatch.setattr(service_control, "IS_WINDOWS", True)
    monkeypatch.setattr(
        service_control.subprocess,
        "run",
        lambda *a, **k: _FakeCompletedProcess("DayZServer_x64.exe 123"),
    )
    assert service_control.is_dayz_process_running() is True


# ---------------------------------------------------------------------------
# systemctl
# ---------------------------------------------------------------------------


def test_systemctl_skips_when_no_systemd(monkeypatch):
    monkeypatch.setattr(service_control, "has_systemd", lambda: False)
    calls = []
    monkeypatch.setattr(service_control.subprocess, "run", lambda *a, **k: calls.append(a))
    result = service_control.systemctl("start")
    assert result == service_control.ServiceResult(False, service_control.NO_SYSTEMD_MESSAGE)
    assert calls == []


def test_systemctl_success(monkeypatch):
    monkeypatch.setattr(service_control, "has_systemd", lambda: True)
    monkeypatch.setattr(service_control, "sudo_prefix", lambda: [])
    monkeypatch.setattr(service_control.subprocess, "run", lambda cmd, **k: _FakeCompletedProcess())
    result = service_control.systemctl("start")
    assert result.ok is True


def test_systemctl_called_process_error(monkeypatch):
    monkeypatch.setattr(service_control, "has_systemd", lambda: True)
    monkeypatch.setattr(service_control, "sudo_prefix", lambda: [])

    def raise_cpe(cmd, **k):
        raise service_control.subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(service_control.subprocess, "run", raise_cpe)
    result = service_control.systemctl("start")
    assert result.ok is False
    assert "Failed to start service" in result.message


def test_systemctl_missing_binary(monkeypatch):
    monkeypatch.setattr(service_control, "has_systemd", lambda: True)
    monkeypatch.setattr(service_control, "sudo_prefix", lambda: [])
    monkeypatch.setattr(service_control.subprocess, "run", _raise_file_not_found)
    result = service_control.systemctl("start")
    assert result.ok is False
    assert "systemctl not found" in result.message


# ---------------------------------------------------------------------------
# windows_stop_server
# ---------------------------------------------------------------------------


class _Ran:
    """What ``subprocess.run`` hands back, as far as these tests read it."""

    def __init__(self, stdout=""):
        self.stdout = stdout
        self.returncode = 0


def test_windows_stop_server_already_stopped(monkeypatch):
    monkeypatch.setattr(service_control, "is_server_running", lambda: False)
    calls = []
    monkeypatch.setattr(service_control.subprocess, "run", lambda cmd, **k: calls.append(cmd) or _Ran())
    result = service_control.windows_stop_server()
    assert result.ok is True
    assert result.message == "DayZ Server is not running."
    assert not any(cmd[0] == "taskkill" for cmd in calls)


def test_a_stop_ends_the_watchdog_even_when_the_server_is_down(monkeypatch):
    """A crash loop, or the ten seconds between two restarts: the server is
    not running at that moment, and the stop used to return before it ended
    the watchdog, which then started the server again."""
    order = []
    monkeypatch.setattr(service_control, "is_server_running", lambda: order.append("asked") or False)

    def _run(cmd, **k):
        order.append(cmd[0])
        return _Ran("stopped\n" if cmd[0] == "powershell" else "")

    monkeypatch.setattr(service_control.subprocess, "run", _run)

    result = service_control.windows_stop_server()

    assert order == ["powershell", "asked"]
    assert result.ok is True
    assert result.message == service_control.WATCHDOG_STOPPED


def test_the_watchdog_kill_counts_what_it_ended(monkeypatch):
    monkeypatch.setattr(
        service_control.subprocess, "run", lambda cmd, **k: _Ran("stopped\nstopped\n")
    )
    assert service_control._kill_watchdog() == 2
    monkeypatch.setattr(service_control.subprocess, "run", lambda cmd, **k: _Ran(""))
    assert service_control._kill_watchdog() == 0
    monkeypatch.setattr(service_control.subprocess, "run", _raise_file_not_found)
    assert service_control._kill_watchdog() == 0
    assert "'stopped'" in service_control._kill_watchdog_command()[-1]


def test_windows_stop_server_graceful_success(monkeypatch):
    running = [True, False]
    monkeypatch.setattr(service_control, "is_server_running", lambda: running.pop(0))
    monkeypatch.setattr(service_control.time, "sleep", lambda s: None)
    run_calls = []
    monkeypatch.setattr(service_control.subprocess, "run", lambda cmd, **k: run_calls.append(cmd))

    result = service_control.windows_stop_server()

    assert result.ok is True
    assert any(cmd and cmd[0] == "taskkill" and "/F" not in cmd for cmd in run_calls)


def test_windows_stop_server_force_kills_after_timeout(monkeypatch):
    monkeypatch.setattr(service_control, "is_server_running", lambda: True)
    monkeypatch.setattr(service_control.time, "sleep", lambda s: None)
    run_calls = []
    monkeypatch.setattr(service_control.subprocess, "run", lambda cmd, **k: run_calls.append(cmd))

    result = service_control.windows_stop_server()

    assert result.ok is True
    assert any("/F" in cmd for cmd in run_calls)


def test_windows_stop_server_missing_taskkill(monkeypatch):
    monkeypatch.setattr(service_control, "is_server_running", lambda: True)
    monkeypatch.setattr(service_control.subprocess, "run", _raise_file_not_found)
    result = service_control.windows_stop_server()
    assert result.ok is False
    assert "taskkill" in result.message


# ---------------------------------------------------------------------------
# windows_server_status
# ---------------------------------------------------------------------------


def test_windows_server_status_returns_stdout(monkeypatch):
    monkeypatch.setattr(
        service_control.subprocess,
        "run",
        lambda *a, **k: _FakeCompletedProcess("Image Name\nDayZServer_x64.exe  123"),
    )
    result = service_control.windows_server_status()
    assert result.ok is True
    assert "DayZServer_x64.exe" in result.message


def test_windows_server_status_missing_tasklist(monkeypatch):
    monkeypatch.setattr(service_control.subprocess, "run", _raise_file_not_found)
    result = service_control.windows_server_status()
    assert result.ok is False
    assert "tasklist" in result.message


# ---------------------------------------------------------------------------
# start_server / stop_server / restart_server (cross-platform dispatch)
# ---------------------------------------------------------------------------


def test_start_server_linux_delegates_to_systemctl(monkeypatch, sample_config):
    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    monkeypatch.setattr(service_control, "systemctl", lambda action, **k: service_control.ServiceResult(action == "start"))
    result = service_control.start_server(sample_config)
    assert result.ok is True


def test_a_windows_start_writes_the_scripts_when_there_are_none(monkeypatch, sample_config):
    """Only the wizard's install step wrote them, so a server installed from
    the panel or by `zsm setup` answered "No start script found"."""
    monkeypatch.setattr(service_control, "IS_WINDOWS", True)
    server = Path(sample_config.server_dir)
    run_calls = []
    monkeypatch.setattr(service_control.subprocess, "run", lambda cmd, **k: run_calls.append(cmd))
    assert not (server / service_control.START_BAT_NAME).exists()

    result = service_control.start_server(sample_config)

    assert result.ok is True, result.message
    assert "DayZServer_x64.exe" in (server / service_control.START_BAT_NAME).read_text(encoding="utf-8")
    assert "taskkill" in (server / service_control.STOP_BAT_NAME).read_text(encoding="utf-8")
    assert run_calls == [["cmd", "/c", "start", "/min", "", service_control.START_BAT_NAME]]


def test_a_windows_start_keeps_the_script_that_is_there(monkeypatch, sample_config):
    monkeypatch.setattr(service_control, "IS_WINDOWS", True)
    bat_path = Path(sample_config.server_dir) / service_control.START_BAT_NAME
    bat_path.write_text("echo mine", encoding="utf-8")
    monkeypatch.setattr(service_control.subprocess, "run", lambda cmd, **k: None)

    assert service_control.start_server(sample_config).ok is True
    assert bat_path.read_text(encoding="utf-8") == "echo mine"


def test_a_windows_start_with_a_refused_launch_command_starts_nothing(monkeypatch, sample_config):
    monkeypatch.setattr(service_control, "IS_WINDOWS", True)
    cfg = dataclasses.replace(sample_config, server_args="-doLogs & calc")
    run_calls = []
    monkeypatch.setattr(service_control.subprocess, "run", lambda cmd, **k: run_calls.append(cmd))

    result = service_control.start_server(cfg)

    assert result.ok is False
    assert "Launch flags" in result.message
    assert run_calls == []
    assert not (Path(cfg.server_dir) / service_control.START_BAT_NAME).exists()
    assert not (Path(cfg.server_dir) / service_control.STOP_BAT_NAME).exists()


def test_a_windows_start_without_a_server_folder_says_so(monkeypatch, sample_config, tmp_path):
    monkeypatch.setattr(service_control, "IS_WINDOWS", True)
    cfg = dataclasses.replace(sample_config, server_dir=str(tmp_path / "nowhere"))
    monkeypatch.setattr(service_control.subprocess, "run", lambda cmd, **k: pytest.fail("started"))

    result = service_control._windows_launch_start_bat(cfg)

    assert result.ok is False
    assert "nowhere" in result.message


def _missing_runtime(monkeypatch, problem="DayZServer_x64.exe cannot start on this computer: X.dll is missing."):
    from app.server import windows_runtime

    monkeypatch.setattr(windows_runtime, "runtime_problem", lambda config: problem)
    monkeypatch.setattr(service_control.time, "sleep", lambda s: None)
    return problem


def test_a_start_that_cannot_work_says_why_and_ends_the_loop(monkeypatch, sample_config):
    """With a runtime DLL missing the server exits at once and the watchdog
    restarted it every ten seconds, with nothing on screen saying why."""
    monkeypatch.setattr(service_control, "IS_WINDOWS", True)
    problem = _missing_runtime(monkeypatch)
    monkeypatch.setattr(service_control, "is_server_running", lambda: False)
    run_calls = []
    monkeypatch.setattr(service_control.subprocess, "run", lambda cmd, **k: run_calls.append(cmd) or _Ran())

    result = service_control.start_server(sample_config, anyway=True)

    assert result.ok is False
    assert result.message == problem
    assert [cmd[0] for cmd in run_calls] == ["cmd", "powershell"]


def test_a_start_with_a_runtime_missing_is_refused_before_it_launches(monkeypatch, sample_config):
    """A soft gate: refused with the runtime's sentence, nothing launched and
    no start snapshot taken, unless the caller says anyway."""
    monkeypatch.setattr(service_control, "IS_WINDOWS", True)
    problem = _missing_runtime(monkeypatch)
    monkeypatch.setattr("app.ops.changeset_ops.take_start_snapshot", lambda config: pytest.fail("snapshot"))
    monkeypatch.setattr(service_control.subprocess, "run", lambda cmd, **k: pytest.fail("launched"))

    result = service_control.start_server(sample_config)

    assert result.ok is False
    assert result.message == problem


def test_a_server_that_runs_is_never_refused_on_the_dll_check_alone(monkeypatch, sample_config):
    monkeypatch.setattr(service_control, "IS_WINDOWS", True)
    _missing_runtime(monkeypatch)
    monkeypatch.setattr(service_control, "is_server_running", lambda: True)
    run_calls = []
    monkeypatch.setattr(service_control.subprocess, "run", lambda cmd, **k: run_calls.append(cmd) or _Ran())

    result = service_control.start_server(sample_config, anyway=True)

    assert result.ok is True
    assert [cmd[0] for cmd in run_calls] == ["cmd"]


def test_a_start_with_every_runtime_does_not_wait(monkeypatch, sample_config):
    from app.server import windows_runtime

    monkeypatch.setattr(service_control, "IS_WINDOWS", True)
    monkeypatch.setattr(windows_runtime, "runtime_problem", lambda config: None)
    monkeypatch.setattr(service_control.time, "sleep", lambda s: pytest.fail("waited"))
    monkeypatch.setattr(service_control, "is_server_running", lambda: pytest.fail("asked"))
    monkeypatch.setattr(service_control.subprocess, "run", lambda cmd, **k: None)

    assert service_control.start_server(sample_config).ok is True


def test_start_server_windows_launches_bat(monkeypatch, sample_config):
    monkeypatch.setattr(service_control, "IS_WINDOWS", True)
    bat_path = Path(sample_config.server_dir) / service_control.START_BAT_NAME
    bat_path.write_text("echo hi", encoding="utf-8")
    run_calls = []
    monkeypatch.setattr(service_control.subprocess, "run", lambda cmd, **k: run_calls.append(cmd))

    result = service_control.start_server(sample_config)

    assert result.ok is True
    assert run_calls and run_calls[0][0] == "cmd"


def test_the_bat_is_started_by_name_so_the_server_dir_never_reaches_cmd(
    monkeypatch, sample_config, tmp_path
):
    # cmd parses its own command line, and list2cmdline quotes only an argument
    # holding whitespace, so the server dir must not be on it at all.
    monkeypatch.setattr(service_control, "IS_WINDOWS", True)
    server = tmp_path / "a&b"
    server.mkdir()
    (server / service_control.START_BAT_NAME).write_text("echo hi", encoding="utf-8")
    cfg = dataclasses.replace(sample_config, server_dir=str(server))
    calls = []
    monkeypatch.setattr(
        service_control.subprocess, "run", lambda cmd, **k: calls.append((cmd, k))
    )
    assert service_control._windows_launch_start_bat(cfg).ok is True
    cmd, kwargs = calls[0]
    assert cmd == ["cmd", "/c", "start", "/min", "", service_control.START_BAT_NAME]
    assert not any(str(server) in arg for arg in cmd)
    assert kwargs["cwd"] == str(server)


def test_stop_server_linux_already_stopped(monkeypatch, sample_config):
    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    monkeypatch.setattr(service_control, "is_server_running", lambda config=None: False)
    monkeypatch.setattr(service_control, "systemd_unit_state", lambda config=None: ("inactive", "dead"))
    result = service_control.stop_server(sample_config)
    assert result.ok is True
    assert "already stopped" in result.message.lower()


def test_stop_server_linux_running_delegates_to_systemctl(monkeypatch, sample_config):
    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    monkeypatch.setattr(service_control, "is_server_running", lambda config=None: True)
    monkeypatch.setattr(service_control, "systemctl", lambda action, **k: service_control.ServiceResult(True))
    result = service_control.stop_server(sample_config)
    assert result.ok is True


def test_stop_server_windows_delegates_to_windows_stop_server(monkeypatch, sample_config):
    monkeypatch.setattr(service_control, "IS_WINDOWS", True)
    monkeypatch.setattr(
        service_control, "windows_stop_server", lambda: service_control.ServiceResult(True, "stopped")
    )
    result = service_control.stop_server(sample_config)
    assert result.ok is True
    assert result.message == "stopped"


def test_restart_server_linux_delegates_to_systemctl(monkeypatch, sample_config):
    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    monkeypatch.setattr(service_control, "systemctl", lambda action, **k: service_control.ServiceResult(action == "restart"))
    result = service_control.restart_server(sample_config)
    assert result.ok is True


def test_restart_server_windows_stops_then_launches(monkeypatch, sample_config):
    monkeypatch.setattr(service_control, "IS_WINDOWS", True)
    stop_called = []
    monkeypatch.setattr(
        service_control, "windows_stop_server", lambda: stop_called.append(True) or service_control.ServiceResult(True)
    )
    bat_path = Path(sample_config.server_dir) / service_control.START_BAT_NAME
    bat_path.write_text("echo hi", encoding="utf-8")
    monkeypatch.setattr(service_control.subprocess, "run", lambda cmd, **k: None)

    result = service_control.restart_server(sample_config)

    assert stop_called == [True]
    assert result.ok is True


# ---------------------------------------------------------------------------
# User-scope behavior (service_scope="user") — no sudo, --user, ~/.config unit
# ---------------------------------------------------------------------------

def _user_cfg(sample_config, tmp_path, monkeypatch):
    """A user-scope config whose unit dir is a monkeypatched tmp XDG path."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")  # skip env injection
    return dataclasses.replace(sample_config, service_scope="user")


def test_to_user_unit_strips_user_and_swaps_target():
    system_unit = (
        "[Unit]\nDescription=x\nAfter=network-online.target\n\n"
        "[Service]\nUser=alice\nExecStart=/bin/true\nRestart=always\n\n"
        "[Install]\nWantedBy=multi-user.target\n"
    )
    out = service_control._to_user_unit(system_unit)
    assert "User=alice" not in out
    assert "WantedBy=default.target" in out
    assert "WantedBy=multi-user.target" not in out
    assert "After=network-online.target" in out  # ordering line kept


def test_template_user_resolves_to_real_account_without_USER_env(
    monkeypatch, sample_config
):
    """`User=` must name an account that exists, even with no $USER in the env.

    `docker compose exec` leaves $USER unset. The old fallback chain
    (`os.environ.get("USER", os.environ.get("USERNAME", "dayz"))`) then stamped
    the literal "dayz" into the unit — an account that exists in no container —
    and systemd failed the service at runtime with 217/USER, crash-looping under
    Restart=always. Resolution must fall back to a passwd lookup, not a guess.
    """
    import getpass

    monkeypatch.delenv("USER", raising=False)
    monkeypatch.delenv("USERNAME", raising=False)
    monkeypatch.delenv("LOGNAME", raising=False)
    monkeypatch.delenv("LNAME", raising=False)

    user = service_control._get_template_replacements(sample_config)["user"]
    assert user != "dayz", "hardcoded fallback names a nonexistent account"
    assert user
    assert user == getpass.getuser()


def test_template_user_ignores_empty_USER_env(monkeypatch, sample_config):
    """A set-but-empty $USER must not render a bare `User=` (also 217/USER)."""
    import getpass

    monkeypatch.setenv("USER", "")
    monkeypatch.delenv("USERNAME", raising=False)
    user = service_control._get_template_replacements(sample_config)["user"]
    assert user, "empty $USER must fall through, not render `User=`"
    assert user == getpass.getuser()


def test_render_service_rejects_nonexistent_user(monkeypatch, sample_config):
    """A User= that resolves to nothing must fail here, not at runtime.

    systemd reports this as 217/USER with Restart=always crash-looping and no
    log from the service itself, so generate time is the only place it can be
    reported usefully.
    """
    cfg = dataclasses.replace(sample_config, service_scope="system")
    monkeypatch.setattr(service_control, "_current_user", lambda: "definitely-no-such-user")
    with pytest.raises(service_control.ServiceUserError, match="217/USER"):
        service_control._render_service(cfg)


def test_render_service_rejects_empty_user(monkeypatch, sample_config):
    cfg = dataclasses.replace(sample_config, service_scope="system")
    monkeypatch.setattr(service_control, "_current_user", lambda: "")
    with pytest.raises(service_control.ServiceUserError):
        service_control._render_service(cfg)


def test_render_service_user_scope_skips_user_validation(
    monkeypatch, sample_config, tmp_path
):
    """User units drop User= altogether, so an unresolvable name is irrelevant."""
    cfg = _user_cfg(sample_config, tmp_path, monkeypatch)
    monkeypatch.setattr(service_control, "_current_user", lambda: "definitely-no-such-user")
    out = service_control._render_service(cfg)
    assert "User=" not in out


def test_render_service_user_mode_applies_rewrite(monkeypatch, sample_config, tmp_path):
    cfg = _user_cfg(sample_config, tmp_path, monkeypatch)
    monkeypatch.setattr(
        service_control, "_render_template",
        lambda *a, **k: "[Service]\nUser=bob\n[Install]\nWantedBy=multi-user.target\n",
    )
    out = service_control._render_service(cfg)
    assert "User=bob" not in out
    assert "WantedBy=default.target" in out


def test_render_service_omits_mod_flag_without_mods(monkeypatch, sample_config):
    """An empty -mod= is malformed to the engine — the unit must not carry it."""
    cfg = dataclasses.replace(sample_config, service_scope="system")
    monkeypatch.setattr(service_control, "_get_mod_string", lambda config: "")
    monkeypatch.setattr(service_control, "_validate_unit_user", lambda user: None)
    out = service_control._render_service(cfg)
    assert "-mod" not in out


def test_render_service_includes_mod_flag_with_mods(monkeypatch, sample_config):
    cfg = dataclasses.replace(sample_config, service_scope="system")
    monkeypatch.setattr(service_control, "_get_mod_string", lambda config: "@CF;@COT")
    monkeypatch.setattr(service_control, "_validate_unit_user", lambda user: None)
    out = service_control._render_service(cfg)
    assert '"-mod=@CF;@COT"' in out


# --- server-side mods: -serverMod= (plans/47 A) ---


def _server_side_config(monkeypatch, sample_config, server_args="", server_side=()):
    cfg = dataclasses.replace(
        sample_config,
        service_scope="system",
        server_args=server_args,
    )
    set_test_flags(cfg, server_only=list(server_side))
    monkeypatch.setattr(
        service_control, "_ordered_active_mods",
        lambda config: ["@CF", "@DayZ-Expansion-AI", "@DayZ-Dynamic-AI-Addon"],
    )
    monkeypatch.setattr(service_control, "_validate_unit_user", lambda user: None)
    return cfg


def test_a_server_side_mod_moves_from_mod_to_server_mod(monkeypatch, sample_config):
    cfg = _server_side_config(
        monkeypatch, sample_config, server_side=["@DayZ-Dynamic-AI-Addon"]
    )
    r = service_control._get_template_replacements(cfg)
    assert r["mod_arg"] == '"-mod=@CF;@DayZ-Expansion-AI" "-serverMod=@DayZ-Dynamic-AI-Addon"'
    argv = service_control.build_launch_argv(cfg)
    assert "-mod=@CF;@DayZ-Expansion-AI" in argv
    assert "-serverMod=@DayZ-Dynamic-AI-Addon" in argv
    out = service_control._render_service(cfg)
    assert '"-serverMod=@DayZ-Dynamic-AI-Addon"' in out


def test_nothing_server_side_leaves_the_launch_flags_byte_identical(monkeypatch, sample_config):
    # An install that never uses the feature must render the ExecStart it had,
    # a hand-typed -serverMod= included, where the admin put it.
    typed = "-port=2302 -serverMod=@Mine -dologs"
    cfg = _server_side_config(monkeypatch, sample_config, server_args=typed)
    r = service_control._get_template_replacements(cfg)
    assert r["server_args"] == typed
    assert r["mod_arg"] == '"-mod=@CF;@DayZ-Expansion-AI;@DayZ-Dynamic-AI-Addon"'
    assert "server_mod_string" in r and r["server_mod_string"] == ""


def test_a_typed_server_mod_flag_is_merged_into_one(monkeypatch, sample_config):
    cfg = _server_side_config(
        monkeypatch, sample_config,
        server_args='-port=2302 "-servermod=@Typed;@DayZ-Dynamic-AI-Addon" -dologs',
        server_side=["@DayZ-Dynamic-AI-Addon"],
    )
    r = service_control._get_template_replacements(cfg)
    assert r["server_mod_string"] == "@DayZ-Dynamic-AI-Addon;@Typed"
    assert r["server_args"] == "-port=2302 -dologs"
    argv = service_control.build_launch_argv(cfg)
    assert [a for a in argv if a.lower().startswith("-servermod=")] == [
        "-serverMod=@DayZ-Dynamic-AI-Addon;@Typed"
    ]


def test_only_server_side_mods_drops_the_mod_flag(monkeypatch, sample_config):
    cfg = _server_side_config(
        monkeypatch, sample_config,
        server_side=["@CF", "@DayZ-Expansion-AI", "@DayZ-Dynamic-AI-Addon"],
    )
    r = service_control._get_template_replacements(cfg)
    assert r["mod_arg"] == '"-serverMod=@CF;@DayZ-Expansion-AI;@DayZ-Dynamic-AI-Addon"'
    assert not any(a.startswith("-mod=") for a in service_control.build_launch_argv(cfg))


def test_a_server_side_mod_that_is_not_active_is_left_out(monkeypatch, sample_config):
    # Disabled (or no longer in mods.txt) means _ordered_active_mods omits it;
    # the leftover mark must not bring it back through -serverMod=.
    cfg = _server_side_config(monkeypatch, sample_config, server_side=["@Gone"])
    r = service_control._get_template_replacements(cfg)
    assert r["server_mod_string"] == ""
    assert "-serverMod" not in r["mod_arg"]


def test_the_run_command_view_labels_server_side_mods(monkeypatch, sample_config):
    cfg = _server_side_config(
        monkeypatch, sample_config, server_side=["@DayZ-Dynamic-AI-Addon"]
    )
    parts = service_control.build_launch_command_info(cfg)["parts"]
    part = next(p for p in parts if p.get("flag") == "-serverMod")
    assert part["mods"] == ["@DayZ-Dynamic-AI-Addon"]
    assert part["origin"] == "mods"


# --- launch-command safety: nothing reaches bash/cmd as syntax ---


def test_a_normal_unit_renders_the_expected_execstart(monkeypatch, sample_config):
    """The guard must not change a normal install's unit — byte for byte. The
    three path arguments are each one double-quoted word, like -mod=."""
    cfg = _server_side_config(
        monkeypatch, sample_config,
        server_args="-adminLog -netLog -freezeCheck -cpuCount=2 -doLogs",
    )
    out = service_control._render_service(cfg)
    profiles = str(cfg.server_dir_path / "profiles")
    battleye = str(cfg.server_dir_path / "battleye")
    expected = (
        "ExecStart=/bin/bash -c 'exec ./DayZServer \"-config=serverDZ.cfg\" "
        '"-mod=@CF;@DayZ-Expansion-AI;@DayZ-Dynamic-AI-Addon" '
        f'"-profiles={profiles}" "-BEpath={battleye}" '
        "-adminLog -netLog -freezeCheck -cpuCount=2 -doLogs -port=2302'"
    )
    assert expected in out


@pytest.mark.parametrize("server_dir", ["/srv/dayz server", "/srv/dayz (copy)"])
def test_a_server_dir_with_a_space_or_parens_is_one_quoted_word(
    monkeypatch, sample_config, server_dir
):
    cfg = _server_side_config(monkeypatch, sample_config)
    cfg = dataclasses.replace(cfg, server_dir=server_dir)
    out = service_control._render_service(cfg)
    assert f'"-profiles={server_dir}/profiles" "-BEpath={server_dir}/battleye"' in out
    # The unit's command line, as bash will split it, keeps each path whole.
    import shlex as _shlex

    line = next(ln for ln in out.splitlines() if ln.startswith("ExecStart="))
    inner = _shlex.split(line.removeprefix("ExecStart="))[2]  # bash -c '<this>'
    words = _shlex.split(inner)
    assert f"-profiles={server_dir}/profiles" in words
    assert f"-BEpath={server_dir}/battleye" in words


@pytest.mark.parametrize(
    "server_dir",
    [r"C:\DayZ Server", r"C:\Program Files (x86)\DayZServer"],
)
def test_windows_bat_quotes_a_path_with_a_space_or_parens(
    monkeypatch, sample_config, server_dir
):
    monkeypatch.setattr(service_control, "IS_WINDOWS", True)
    monkeypatch.setattr(service_control, "_ordered_active_mods", lambda config: ["@CF"])
    cfg = dataclasses.replace(
        sample_config,
        server_dir=server_dir,
        config_path=server_dir + r"\serverDZ.cfg",
    )
    out = service_control._render_start_bat(cfg)
    # The joined paths as the renderer builds them (this host's Path type).
    profiles = str(cfg.server_dir_path / cfg.profiles_path)
    battleye = str(cfg.server_dir_path / cfg.battleye_path)
    assert f'cd /d "{server_dir}"' in out
    assert f'"-config={server_dir}\\serverDZ.cfg"' in out
    assert f'"-profiles={profiles}" "-BEpath={battleye}"' in out


def _windows(monkeypatch):
    """Render as on Windows, joining paths the way a WindowsPath does."""
    from pathlib import PureWindowsPath

    from app.shared.config import DayzConfig

    monkeypatch.setattr(service_control, "IS_WINDOWS", True)
    monkeypatch.setattr(service_control, "_ordered_active_mods", lambda config: ["@CF"])
    monkeypatch.setattr(
        DayzConfig, "server_dir_path", property(lambda self: PureWindowsPath(self.server_dir))
    )


def _launch_line(out: str) -> str:
    return next(ln for ln in out.splitlines() if ln.startswith("start /wait"))


def test_windows_drops_a_trailing_backslash_from_each_path_argument(monkeypatch, sample_config):
    # The server's argv parser reads \" as a literal quote, so a path ending in a
    # backslash would run into the next argument.
    _windows(monkeypatch)
    cfg = dataclasses.replace(
        sample_config,
        server_dir=r"C:\DayZ",
        config_path="C:\\DayZ\\serverDZ.cfg\\",
        profiles_path="profiles\\",
        battleye_path="C:\\DayZ\\battleye\\\\",
    )
    line = _launch_line(service_control._render_start_bat(cfg))
    assert '\\"' not in line
    assert r'"-config=C:\DayZ\serverDZ.cfg"' in line
    assert r'"-profiles=C:\DayZ\profiles" "-BEpath=C:\DayZ\battleye"' in line


@pytest.mark.parametrize(
    "field, value, flag",
    [
        ("config_path", "C:\\", "-config"),
        ("config_path", "C:", "-config"),
        ("config_path", "\\", "-config"),
        ("profiles_path", "D:\\", "-profiles"),
        ("battleye_path", "\\", "-BEpath"),
    ],
)
def test_windows_refuses_a_drive_root_as_a_path_argument(
    monkeypatch, sample_config, field, value, flag
):
    # C:\ has no trailing backslash to drop safely: C: is the drive's current dir.
    _windows(monkeypatch)
    cfg = dataclasses.replace(sample_config, server_dir=r"C:\DayZ", **{field: value})
    with pytest.raises(service_control.LaunchCommandError, match=f"{flag}.*drive root"):
        service_control._render_start_bat(cfg)


def test_posix_renders_a_drive_like_path_unchanged(monkeypatch, sample_config):
    cfg = _server_side_config(monkeypatch, sample_config)
    cfg = dataclasses.replace(cfg, config_path="C:")
    out = service_control._render_service(cfg)
    assert '"-config=C:"' in out


def test_a_quote_in_a_path_argument_is_still_refused(monkeypatch, sample_config):
    # Quoted words keep the looser set, which still refuses a closing quote.
    cfg = _server_side_config(monkeypatch, sample_config)
    cfg = dataclasses.replace(cfg, profiles_path='/srv/pro"files')
    with pytest.raises(service_control.LaunchCommandError, match="-profiles"):
        service_control._render_service(cfg)


def test_the_preview_shows_a_path_with_a_space_as_one_word(monkeypatch, sample_config):
    cfg = _server_side_config(monkeypatch, sample_config)
    normal = service_control.build_launch_command_info(cfg)["command"]
    assert '"' not in normal  # a normal install's preview is unchanged
    spaced = dataclasses.replace(cfg, server_dir="/srv/dayz server")
    command = service_control.build_launch_command_info(spaced)["command"]
    assert '"-profiles=/srv/dayz server/profiles"' in command
    assert "-profiles=/srv/dayz server/profiles" in service_control.build_launch_argv(spaced)


def test_a_mod_name_with_a_space_and_parens_still_renders(monkeypatch, sample_config):
    # A Workshop folder name may hold a space or parentheses; zsm wraps mod
    # names in double quotes, so they were fine before and must stay fine.
    cfg = dataclasses.replace(sample_config, service_scope="system")
    monkeypatch.setattr(
        service_control, "_ordered_active_mods", lambda config: ["@Auto Run (Toggle)"]
    )
    monkeypatch.setattr(service_control, "_validate_unit_user", lambda user: None)
    out = service_control._render_service(cfg)
    assert '"-mod=@Auto Run (Toggle)"' in out


@pytest.mark.parametrize(
    "field, value",
    [
        ("config_path", "serverDZ$HOME.cfg"),
        ("profiles_path", "profiles$(id)"),
        ("battleye_path", "battleye`id`"),
        ("server_args", "-doLogs | tee /tmp/x"),
        ("server_args", "-doLogs && reboot"),
    ],
)
def test_a_shell_metachar_in_a_launch_value_is_refused(
    monkeypatch, sample_config, field, value
):
    cfg = _server_side_config(monkeypatch, sample_config)
    cfg = dataclasses.replace(cfg, **{field: value})
    with pytest.raises(service_control.LaunchCommandError):
        service_control._render_service(cfg)


def test_the_refusal_names_the_field(monkeypatch, sample_config):
    cfg = _server_side_config(monkeypatch, sample_config)
    cfg = dataclasses.replace(cfg, config_path="serverDZ$HOME.cfg")
    with pytest.raises(service_control.LaunchCommandError, match="-config"):
        service_control._render_service(cfg)


def test_a_mod_name_that_breaks_out_of_its_quotes_is_refused(monkeypatch, sample_config):
    cfg = dataclasses.replace(sample_config, service_scope="system")
    monkeypatch.setattr(
        service_control, "_ordered_active_mods", lambda config: ['@Bad"; reboot; "']
    )
    monkeypatch.setattr(service_control, "_validate_unit_user", lambda user: None)
    with pytest.raises(service_control.LaunchCommandError):
        service_control._render_service(cfg)


def test_windows_allows_backslash_paths_but_refuses_cmd_metachars(monkeypatch, sample_config):
    monkeypatch.setattr(service_control, "IS_WINDOWS", True)
    monkeypatch.setattr(service_control, "_ordered_active_mods", lambda config: ["@CF"])
    ok = dataclasses.replace(sample_config, config_path=r"C:\dayz\serverDZ.cfg")
    out = service_control._render_start_bat(ok)
    assert r"C:\dayz\serverDZ.cfg" in out  # backslash is a path separator on Windows
    # Inside the argument's double quotes cmd's & | < > ^ ( ) are literal; % and
    # a closing quote are not.
    for value in ("serverDZ%PATH%.cfg", 'serverDZ".cfg'):
        bad = dataclasses.replace(sample_config, config_path=value)
        with pytest.raises(service_control.LaunchCommandError):
            service_control._render_start_bat(bad)


@pytest.mark.parametrize(
    "value",
    ["/srv/dayz\nUnexpected=1", "/srv/dayz\rUnexpected=1", "/srv/%h/dayz", "/srv/da'yz"],
)
def test_a_server_dir_that_would_reach_the_unit_as_syntax_is_refused(
    monkeypatch, sample_config, value
):
    # server_dir is a WorkingDirectory= line of its own and is joined into
    # -profiles=/-BEpath=, so a line break, a % specifier or a quote in it must
    # never be written into the unit.
    cfg = _server_side_config(monkeypatch, sample_config)
    cfg = dataclasses.replace(cfg, server_dir=value)
    with pytest.raises(service_control.LaunchCommandError, match="Server directory"):
        service_control._render_service(cfg)


@pytest.mark.parametrize("sep", ["\x0b", "\x0c", "\x1c", "\x85", "\u2028", "\u2029"])
def test_a_line_separator_in_a_launch_value_never_starts_a_unit_line(
    monkeypatch, sample_config, sep
):
    # The user unit is rebuilt through str.splitlines, which splits at all of
    # these, so the text after one would become a directive of its own.
    cfg = _server_side_config(monkeypatch, sample_config)
    cfg = dataclasses.replace(
        cfg, service_scope="user", server_dir=f"/srv/dayz{sep}ExecStartPre=/bin/true"
    )
    with pytest.raises(service_control.LaunchCommandError, match="Server directory"):
        service_control._render_service(cfg)
    assert service_control.server_dir_problem(cfg.server_dir)
    args = dataclasses.replace(cfg, server_dir="/srv/dayz", server_args=f"-doLogs{sep}-x")
    with pytest.raises(service_control.LaunchCommandError, match="Launch flags"):
        service_control._render_service(args)


@pytest.mark.parametrize("flags", ["-doLogs #-adminLog", "#x", "-doLogs ~/x", "-a\t~"])
def test_a_launch_flag_bash_reads_at_a_word_start_is_refused(monkeypatch, sample_config, flags):
    # "#" commented out the rest of the command line (-port= with it) and "~"
    # became the home directory, while the Run Command view showed them as typed.
    cfg = _server_side_config(monkeypatch, sample_config, server_args=flags)
    with pytest.raises(service_control.LaunchCommandError, match="Launch flags has a word starting"):
        service_control._render_service(cfg)
    assert service_control.launch_field_problem(None, "server_args", flags)


def test_a_hash_or_tilde_inside_a_word_still_renders(monkeypatch, sample_config):
    cfg = _server_side_config(monkeypatch, sample_config, server_args="-name=a#b -x=c~d")
    assert "-name=a#b -x=c~d" in service_control._render_service(cfg)
    assert service_control.launch_field_problem(None, "config_path", "~cfg/#a.cfg") is None
    monkeypatch.setattr(service_control, "IS_WINDOWS", True)
    assert service_control.launch_field_problem(None, "server_args", "#x ~y") is None


def test_a_tab_in_the_launch_flags_still_renders(monkeypatch, sample_config):
    cfg = _server_side_config(monkeypatch, sample_config, server_args="-doLogs\t-adminLog")
    assert "-doLogs\t-adminLog" in service_control._render_service(cfg)


def test_a_server_dir_with_a_space_still_renders(monkeypatch, sample_config):
    cfg = _server_side_config(monkeypatch, sample_config)
    cfg = dataclasses.replace(cfg, server_dir="/srv/dayz server")
    out = service_control._render_service(cfg)
    assert "WorkingDirectory=/srv/dayz server\n" in out


def test_a_shell_character_inside_a_quoted_path_is_part_of_the_word(
    monkeypatch, sample_config
):
    # The path arguments are double-quoted words, so ; & | ( ) inside one are
    # passed to the server as part of the path, not read by bash.
    import shlex as _shlex

    cfg = _server_side_config(monkeypatch, sample_config)
    cfg = dataclasses.replace(cfg, server_dir="/srv/a;b&c", profiles_path="profiles")
    out = service_control._render_service(cfg)
    line = next(ln for ln in out.splitlines() if ln.startswith("ExecStart="))
    words = _shlex.split(_shlex.split(line.removeprefix("ExecStart="))[2])
    assert "-profiles=/srv/a;b&c/profiles" in words


@pytest.mark.parametrize("port", ["2302\nUnexpected=1", "abc", "0", "70000", True])
def test_a_port_that_is_not_a_port_is_refused(monkeypatch, sample_config, port):
    # A bundle import sets server_port without the Run Command form's int().
    cfg = _server_side_config(monkeypatch, sample_config)
    cfg = dataclasses.replace(cfg, server_port=port)
    with pytest.raises(service_control.LaunchCommandError, match="-port"):
        service_control._render_service(cfg)


def test_a_port_written_as_digits_still_renders(monkeypatch, sample_config):
    cfg = _server_side_config(monkeypatch, sample_config)
    cfg = dataclasses.replace(cfg, server_port="2402")
    assert "-port=2402'" in service_control._render_service(cfg)


def test_windows_server_dir_is_checked_for_the_bat(monkeypatch, sample_config):
    monkeypatch.setattr(service_control, "IS_WINDOWS", True)
    monkeypatch.setattr(service_control, "_ordered_active_mods", lambda config: ["@CF"])
    ok = dataclasses.replace(sample_config, server_dir=r"C:\dayz\server")
    assert 'cd /d "C:\\dayz\\server"' in service_control._render_start_bat(ok)
    bad = dataclasses.replace(sample_config, server_dir='C:\\dayz"\\server')
    with pytest.raises(service_control.LaunchCommandError, match="Server directory"):
        service_control._render_start_bat(bad)


def test_render_service_system_mode_unchanged(monkeypatch, sample_config):
    cfg = dataclasses.replace(sample_config, service_scope="system")
    monkeypatch.setattr(
        service_control, "_render_template",
        lambda *a, **k: "[Service]\nUser=bob\n[Install]\nWantedBy=multi-user.target\n",
    )
    out = service_control._render_service(cfg)
    assert "User=bob" in out
    assert "WantedBy=multi-user.target" in out


def test_systemctl_user_mode_no_sudo(monkeypatch, sample_config, tmp_path):
    cfg = _user_cfg(sample_config, tmp_path, monkeypatch)
    monkeypatch.setattr(service_control, "has_systemd", lambda: True)
    captured = {}
    monkeypatch.setattr(
        service_control.subprocess, "run",
        lambda cmd, **k: captured.setdefault("cmd", cmd) or _FakeCompletedProcess(),
    )
    result = service_control.systemctl("restart", config=cfg)
    assert result.ok is True
    assert captured["cmd"] == ["systemctl", "--user", "restart", service_control.SERVICE_NAME]
    assert "sudo" not in captured["cmd"]


def test_is_server_running_user_mode_uses_user_flag(monkeypatch, sample_config, tmp_path):
    cfg = _user_cfg(sample_config, tmp_path, monkeypatch)
    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    captured = {}

    def fake_run(cmd, **k):
        captured["cmd"] = cmd
        return _FakeCompletedProcess("ActiveState=active\nSubState=running\n")

    monkeypatch.setattr(service_control.subprocess, "run", fake_run)
    assert service_control.is_server_running(cfg) is True
    assert captured["cmd"][:2] == ["systemctl", "--user"]
    assert "sudo" not in captured["cmd"]


def test_service_setup_state_user_mode_reads_user_unit_dir(monkeypatch, sample_config, tmp_path):
    cfg = _user_cfg(sample_config, tmp_path, monkeypatch)
    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    monkeypatch.setattr(service_control, "has_systemd", lambda: True)
    unit_dir = tmp_path / "xdg" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    (unit_dir / service_control.SERVICE_NAME).write_text("[Unit]\n")
    monkeypatch.setattr(
        service_control.subprocess, "run",
        lambda cmd, **k: _FakeCompletedProcess("enabled\n"),
    )
    state = service_control.service_setup_state(cfg)
    assert state["installed"] is True
    assert state["enabled_at_boot"] is True
    # `stale` is the third key since plans/38 A3: the unit written here is a
    # stub, so it cannot match what the settings would render.
    assert state["stale"] is True


def test_remove_service_user_mode_unlinks_no_sudo(monkeypatch, sample_config, tmp_path):
    cfg = _user_cfg(sample_config, tmp_path, monkeypatch)
    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    monkeypatch.setattr(service_control, "has_systemd", lambda: True)
    unit_dir = tmp_path / "xdg" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    unit = unit_dir / service_control.SERVICE_NAME
    unit.write_text("[Unit]\n")
    cmds = []
    monkeypatch.setattr(
        service_control.subprocess, "run",
        lambda cmd, **k: cmds.append(cmd) or _FakeCompletedProcess(),
    )
    result = service_control.remove_service(cfg)
    assert result.ok is True
    assert not unit.exists()  # direct unlink, not `sudo rm`
    assert all("rm" not in c and "sudo" not in c for c in cmds)
    # stop, disable, daemon-reload all went through --user
    assert all(c[:2] == ["systemctl", "--user"] for c in cmds)


# ---------------------------------------------------------------------------
# The start snapshot (test pass 1, F51: only the web Start button took one)
# ---------------------------------------------------------------------------

def _record_start(monkeypatch, snapshot=None):
    calls: list[str] = []
    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    monkeypatch.setattr(service_control, "clear_start_limit", lambda config=None: None)
    monkeypatch.setattr(
        service_control,
        "systemctl",
        lambda action, **kw: calls.append(f"systemctl {action}")
        or service_control.ServiceResult(True, ""),
    )
    monkeypatch.setattr(
        "app.ops.changeset_ops.take_snapshot",
        snapshot or (lambda config: calls.append("snapshot")),
    )
    return calls


@pytest.mark.parametrize("fn,action", [("start_server", "start"), ("restart_server", "restart")])
def test_a_start_takes_the_snapshot_first(monkeypatch, sample_config, fn, action):
    calls = _record_start(monkeypatch)
    assert getattr(service_control, fn)(sample_config).ok
    assert calls == ["snapshot", f"systemctl {action}"]


def test_a_failing_snapshot_never_blocks_a_start(monkeypatch, sample_config):
    def broken(config):
        raise OSError("disk full")

    calls = _record_start(monkeypatch, snapshot=broken)
    assert service_control.start_server(sample_config).ok
    assert calls == ["systemctl start"]


def test_a_start_with_no_server_installed_names_install_server(monkeypatch, sample_config):
    """Start on exp answered systemctl's exit status 5."""
    monkeypatch.setattr(
        service_control, "unit_runtime_state", lambda config=None: pytest.fail("asked systemd")
    )
    refusal = service_control.start_refusal(sample_config)
    assert str(sample_config.server_dir_path) in refusal and "Install server" in refusal


@pytest.mark.parametrize("load_state,refused", [("not-found", True), ("loaded", False), (None, False)])
def test_a_start_is_refused_only_when_systemd_has_no_unit(monkeypatch, sample_config, load_state, refused):
    from app.shared.platform import IS_WINDOWS

    (sample_config.server_dir_path / ("DayZServer_x64.exe" if IS_WINDOWS else "DayZServer")).write_text("")
    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    monkeypatch.setattr(service_control, "has_systemd", lambda: True)
    state = {} if load_state is None else {"LoadState": load_state}
    monkeypatch.setattr(service_control, "unit_runtime_state", lambda config=None: state)
    refusal = service_control.start_refusal(sample_config)
    if refused:
        assert "Install service" in refusal and "service update" in refusal
    else:
        assert refusal is None


def test_writing_the_unit_with_no_server_dir_says_install_server(sample_config, tmp_path):
    """Doctor's Install service answered "[Errno 2]"."""
    from app.server.service_ops import generate_service_files

    cfg = dataclasses.replace(sample_config, server_dir=str(tmp_path / "server-exp"))
    with pytest.raises(service_control.ServerNotInstalled, match="Install server"):
        generate_service_files(cfg)
    assert not (tmp_path / "server-exp").exists()
