"""Tests for app/server/panel_unit.py — the panel's own unit (TODO A9).

No test runs a real systemctl: every one is answered by a stub.
"""

from __future__ import annotations

import dataclasses
import socket
import sys

import pytest

from app.server import panel_unit, service_control
from app.server.panel_unit import PANEL_SERVICE_NAME


def _user_scope(monkeypatch, sample_config, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    cfg = dataclasses.replace(sample_config, service_scope="user")
    return cfg, tmp_path / "xdg" / "systemd" / "user" / PANEL_SERVICE_NAME


# --- rendering ------------------------------------------------------------------


def test_user_unit_runs_this_interpreter_with_no_user_line(sample_config):
    cfg = dataclasses.replace(sample_config, service_scope="user")

    text = panel_unit.render_panel_unit(cfg)

    assert f'ExecStart="{sys.executable}" -m app web\n' in text
    assert "User=" not in text
    assert "WantedBy=default.target" in text
    assert "multi-user.target" not in text
    assert "{{" not in text


def test_the_unit_starts_from_a_directory_python_can_import_from(sample_config):
    """The first install in the test container crash-looped: its
    WorkingDirectory was the app package, where app/xml/ hides the standard
    library's xml (`No module named 'xml.dom'`)."""
    import subprocess

    lines = panel_unit.render_panel_unit(sample_config).splitlines()
    workdir = next(ln.split("=", 1)[1] for ln in lines if ln.startswith("WorkingDirectory="))

    run = subprocess.run(
        [sys.executable, "-m", "app", "--version"],
        cwd=workdir, capture_output=True, text=True, timeout=120,
    )
    assert run.returncode == 0, run.stderr


def test_system_unit_runs_as_the_invoking_user(monkeypatch, sample_config):
    monkeypatch.setattr(panel_unit, "_current_user", lambda: "root")

    text = panel_unit.render_panel_unit(sample_config)

    assert "User=root\n" in text
    assert "WantedBy=multi-user.target" in text


def test_a_system_unit_for_a_missing_account_is_refused(monkeypatch, sample_config):
    monkeypatch.setattr(panel_unit, "_current_user", lambda: "no-such-user-zsm")

    with pytest.raises(service_control.ServiceUserError):
        panel_unit.render_panel_unit(sample_config)


def _exec_start_words(text: str) -> tuple[list[str], str]:
    """``ExecStart=``'s words and ``WorkingDirectory=`` the way systemd reads
    them: ``%%`` is a literal ``%``, and ExecStart splits on whitespace outside
    double quotes (shlex, which agrees for a value with no quote or backslash)."""
    import shlex

    lines = dict(ln.split("=", 1) for ln in text.splitlines() if "=" in ln)
    return (
        shlex.split(lines["ExecStart"].replace("%%", "%")),
        lines["WorkingDirectory"].replace("%%", "%"),
    )


def test_an_install_path_with_a_space_or_percent_stays_one_word(monkeypatch, sample_config, tmp_path):
    """plans/68 B10: the interpreter's path is one ExecStart word whatever it
    holds, and ``%`` is escaped for systemd's specifiers."""
    python = "/opt/My Tools/100%/venv/bin/python"
    workdir = tmp_path / "site packages %h"
    monkeypatch.setattr(panel_unit, "_python_exe", lambda: python)
    monkeypatch.setattr(panel_unit, "_working_dir", lambda: workdir)
    cfg = dataclasses.replace(sample_config, service_scope="user")

    text = panel_unit.render_panel_unit(cfg)

    assert 'ExecStart="/opt/My Tools/100%%/venv/bin/python" -m app web\n' in text
    words, cwd = _exec_start_words(text)
    assert words == [python, "-m", "app", "web"]
    assert cwd == str(workdir)
    assert "%h" not in text.replace("%%", "")


@pytest.mark.parametrize("bad", ['/opt/a"b/python', "/opt/a\\b/python", "/opt/a\nb/python"])
def test_a_path_systemd_cannot_hold_is_refused_and_install_writes_nothing(
    monkeypatch, sample_config, tmp_path, bad
):
    cfg, dest = _user_scope(monkeypatch, sample_config, tmp_path)
    monkeypatch.setattr(panel_unit, "_python_exe", lambda: bad)
    monkeypatch.setattr(
        panel_unit, "systemctl",
        lambda *a, **k: pytest.fail("systemctl must not run for a refused unit"),
    )

    with pytest.raises(service_control.ServiceUserError):
        panel_unit.render_panel_unit(cfg)
    result = panel_unit.install_panel_unit(cfg)

    assert not result.ok
    assert "Python interpreter" in result.message
    assert not dest.exists()


@pytest.mark.parametrize("sep", ["\x0b", "\x0c", "\x85", " ", " "])
@pytest.mark.parametrize("which", ["_python_exe", "_working_dir"])
def test_a_path_with_any_line_separator_is_refused(monkeypatch, sample_config, tmp_path, sep, which):
    """plans/68 LB-7: the user unit is rebuilt through ``str.splitlines``, so
    NEL, U+2028 or U+2029 in a path started a unit line of its own (the
    sweep's probe rendered an ``ExecStartPre=`` from the install path)."""
    cfg, _ = _user_scope(monkeypatch, sample_config, tmp_path)
    bad = f"/opt/zsm{sep}ExecStartPre=/bin/sh -c id"
    monkeypatch.setattr(panel_unit, which, lambda: bad if which == "_python_exe" else tmp_path / bad[1:])

    with pytest.raises(service_control.ServiceUserError):
        panel_unit.render_panel_unit(cfg)


def test_a_tab_in_a_path_is_only_whitespace(monkeypatch, sample_config, tmp_path):
    cfg, _ = _user_scope(monkeypatch, sample_config, tmp_path)
    python = "/opt/My\tTools/bin/python"
    monkeypatch.setattr(panel_unit, "_python_exe", lambda: python)

    text = panel_unit.render_panel_unit(cfg)

    assert _exec_start_words(text)[0] == [python, "-m", "app", "web"]
    assert not any(ln.startswith("ExecStartPre") for ln in text.splitlines())


# --- where it listens -----------------------------------------------------------


def test_the_unit_binds_loopback_even_when_installed_over_ssh(monkeypatch, sample_config):
    """`zsm web` in an SSH shell binds 0.0.0.0; the unit has no SSH session."""
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.2 51000 10.0.0.1 22")
    monkeypatch.setattr("app.shared.platform.IS_DOCKER", False)

    assert panel_unit.panel_bind_host(sample_config) == "127.0.0.1"
    chosen = dataclasses.replace(sample_config, web_bind_host="0.0.0.0")
    assert panel_unit.panel_bind_host(chosen) == "0.0.0.0"


def test_the_port_check_sees_a_panel_started_by_hand(monkeypatch, sample_config):
    monkeypatch.setattr(panel_unit, "panel_state", lambda config: {})
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        monkeypatch.setattr(panel_unit, "PANEL_PORT", listener.getsockname()[1])
        assert panel_unit.panel_port_taken(sample_config) is True


def test_the_port_check_ignores_the_units_own_panel(monkeypatch, sample_config):
    monkeypatch.setattr(panel_unit, "panel_state", lambda config: {"active": "active"})
    monkeypatch.setattr(
        socket, "create_connection", lambda *a, **k: pytest.fail("must not probe")
    )
    assert panel_unit.panel_port_taken(sample_config) is False


# --- install / remove -----------------------------------------------------------


def test_install_writes_enables_and_restarts_the_panel_unit(monkeypatch, sample_config, tmp_path):
    cfg, dest = _user_scope(monkeypatch, sample_config, tmp_path)
    calls = []
    monkeypatch.setattr(
        panel_unit,
        "systemctl",
        lambda action, **k: calls.append((action, k.get("unit", True), k["name"]))
        or service_control.ServiceResult(True),
    )

    result = panel_unit.install_panel_unit(cfg)

    assert result.ok, result.message
    assert dest.read_text(encoding="utf-8") == panel_unit.render_panel_unit(cfg)
    assert calls == [
        ("daemon-reload", False, PANEL_SERVICE_NAME),
        ("enable", True, PANEL_SERVICE_NAME),
        ("restart", True, PANEL_SERVICE_NAME),
    ]


def test_install_stops_at_the_first_systemctl_failure(monkeypatch, sample_config, tmp_path):
    cfg, _dest = _user_scope(monkeypatch, sample_config, tmp_path)
    calls = []

    def fake(action, **k):
        calls.append(action)
        return service_control.ServiceResult(action != "enable", f"Failed to {action}")

    monkeypatch.setattr(panel_unit, "systemctl", fake)

    result = panel_unit.install_panel_unit(cfg)

    assert not result.ok and result.message == "Failed to enable"
    assert calls == ["daemon-reload", "enable"]


def test_remove_acts_on_the_panel_unit_not_the_server(monkeypatch, sample_config, tmp_path):
    cfg, dest = _user_scope(monkeypatch, sample_config, tmp_path)
    dest.parent.mkdir(parents=True)
    dest.write_text("[Unit]\n", encoding="utf-8")
    server_unit = dest.parent / service_control.SERVICE_NAME
    server_unit.write_text("[Unit]\n", encoding="utf-8")
    monkeypatch.setattr(service_control, "has_systemd", lambda: True)
    calls = []
    monkeypatch.setattr(
        service_control,
        "systemctl",
        lambda action, **k: calls.append((action, k.get("name")))
        or service_control.ServiceResult(True),
    )

    result = panel_unit.remove_panel_unit(cfg)

    assert result.ok
    assert not dest.exists()
    assert server_unit.exists()
    assert ("stop", PANEL_SERVICE_NAME) in calls
    assert ("disable", PANEL_SERVICE_NAME) in calls


def test_commands_follow_the_scope(sample_config):
    user = dataclasses.replace(sample_config, service_scope="user")

    assert panel_unit.panel_commands(user) == (
        "systemctl --user restart zsm-web",
        "journalctl --user -u zsm-web -f",
    )
    assert panel_unit.panel_commands(sample_config) == (
        "systemctl restart zsm-web",
        "journalctl -u zsm-web -f",
    )
