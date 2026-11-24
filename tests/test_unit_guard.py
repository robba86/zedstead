"""The unit guard: zsm replaces only a unit it wrote (plans/78 D2, step 4).

Every test runs in user scope under tmp_path with systemctl stubbed, so the
dev box's own units are never read or written (root CLAUDE.md).
"""

from __future__ import annotations

import asyncio
import dataclasses
import subprocess

import pytest
from click.testing import CliRunner

from app.commands import service as service_cmd
from app.commands.service import service as service_group
from app.server import service_ops, unit_guard
from app.server.service_control import _render_service
from app.shared import systemd_scope
from app.shared.systemd_scope import server_unit

FOREIGN = "[Unit]\nDescription=my own DayZ\n\n[Service]\nExecStart=/srv/start.sh\n"


@pytest.fixture
def user_cfg(sample_config, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    return dataclasses.replace(sample_config, service_scope="user")


def _installed(cfg):
    return unit_guard.installed_path(cfg)


def _put(cfg, text):
    path = _installed(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- check -----------------------------------------------------------------------


def test_the_template_carries_the_marker(user_cfg):
    assert unit_guard.is_zsm_unit(_render_service(user_cfg))


def test_states(user_cfg):
    rendered = _render_service(user_cfg)
    assert unit_guard.check(user_cfg, rendered).state == unit_guard.ABSENT

    _put(user_cfg, FOREIGN)
    assert unit_guard.check(user_cfg, rendered).state == unit_guard.FOREIGN

    _put(user_cfg, rendered)
    assert unit_guard.check(user_cfg, rendered).state == unit_guard.OWN


def test_a_zsm_unit_from_before_the_record_is_zsms(user_cfg):
    """No record: the marker decides, so an older install regenerates as before."""
    rendered = _render_service(user_cfg)
    _put(user_cfg, rendered.replace("Restart=", "# a note\nRestart="))
    assert unit_guard.check(user_cfg, rendered).state == unit_guard.OWN


def test_a_settings_change_is_not_an_edit(user_cfg):
    """The unit is what zsm last installed; what zsm renders now differs."""
    old = _render_service(user_cfg)
    _put(user_cfg, old)
    unit_guard.record(user_cfg, old)
    newer = old.replace("RestartSec=", "RestartSec=1")
    assert unit_guard.check(user_cfg, newer).state == unit_guard.OWN


def test_a_hand_edit_after_the_record_is_an_edit(user_cfg):
    rendered = _render_service(user_cfg)
    unit_guard.record(user_cfg, rendered)
    _put(user_cfg, rendered.replace("Restart=", "# a note\nRestart="))
    check = unit_guard.check(user_cfg, rendered)
    assert check.state == unit_guard.EDITED
    assert "+Restart=" not in unit_guard.diff(check, rendered)
    assert "-# a note" in unit_guard.diff(check, rendered)


# --- the shared regenerate --------------------------------------------------------


def test_generate_leaves_a_foreign_unit_alone(user_cfg, monkeypatch):
    monkeypatch.setattr(service_ops, "has_systemd", lambda: True)
    calls = []
    monkeypatch.setattr(service_ops.subprocess, "run", lambda *a, **k: calls.append(a))
    path = _put(user_cfg, FOREIGN)

    with pytest.raises(unit_guard.UnitNotOurs, match="not written by Zedstead"):
        service_ops.generate_service_files(user_cfg)

    assert path.read_text(encoding="utf-8") == FOREIGN
    assert calls == []


def test_generate_records_what_it_installed(user_cfg, monkeypatch):
    monkeypatch.setattr(service_ops, "has_systemd", lambda: True)
    monkeypatch.setattr(service_ops.subprocess, "run", lambda *a, **k: None)

    service_ops.generate_service_files(user_cfg)

    rendered = _installed(user_cfg).read_text(encoding="utf-8")
    assert user_cfg.paths.installed_unit_hash.read_text().strip() == unit_guard._sha(rendered)


class _Runner:
    def __init__(self):
        self.lines = []
        self.commands = []

    def write(self, line):
        self.lines.append(line)

    async def run_command(self, argv):
        self.commands.append(argv)
        return 0


def test_an_update_keeps_a_foreign_unit_and_does_not_fail(user_cfg, monkeypatch):
    """The finalize step: refusing is the point, so it is not a failure that
    would roll the update back."""
    monkeypatch.setattr(service_ops, "has_systemd", lambda: True)
    path = _put(user_cfg, FOREIGN)
    runner = _Runner()

    ok = asyncio.run(service_ops.generate_service_files_async(runner, user_cfg))

    assert ok is True
    assert path.read_text(encoding="utf-8") == FOREIGN
    assert runner.commands == []
    assert any("not written by Zedstead" in line for line in runner.lines)


def _reload_returns(monkeypatch, returncode, stderr=""):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs.get("env")))
        return subprocess.CompletedProcess(argv, returncode, "", stderr)

    monkeypatch.setattr(service_ops, "has_systemd", lambda: True)
    monkeypatch.setattr(service_ops.subprocess, "run", fake_run)
    return calls


def test_an_update_reloads_a_user_unit_with_the_scope_env(user_cfg, monkeypatch):
    """A headless panel can lack XDG_RUNTIME_DIR, and systemctl --user then
    finds no bus: the reload gets it from the scope, as the sync path does."""
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    monkeypatch.setattr(systemd_scope.os, "getuid", lambda: 1234, raising=False)
    calls = _reload_returns(monkeypatch, 0)

    ok = asyncio.run(service_ops.generate_service_files_async(_Runner(), user_cfg))

    assert ok is True
    argv, env = calls[0]
    assert argv[-1] == "daemon-reload"
    assert env["XDG_RUNTIME_DIR"] == "/run/user/1234"


def test_a_failed_user_reload_fails_the_install_and_keeps_the_record(user_cfg, monkeypatch):
    """systemd still runs the old unit, so it is never logged as reloaded. The
    unit is Zedstead's own, so the next install must not take it for a hand edit."""
    _reload_returns(monkeypatch, 1, "Failed to connect to bus")
    runner = _Runner()

    ok = asyncio.run(service_ops.generate_service_files_async(runner, user_cfg))

    assert ok is False
    assert not any("daemon reloaded" in line for line in runner.lines)
    assert any("Failed to connect to bus" in line for line in runner.lines)
    rendered = _installed(user_cfg).read_text(encoding="utf-8")
    assert unit_guard.check(user_cfg, rendered + "# changed\n").state == unit_guard.OWN


# --- zsm service install / update ------------------------------------------------


def _cli(monkeypatch, cfg, *, ni):
    monkeypatch.setattr(service_cmd, "IS_WINDOWS", False)
    monkeypatch.setattr(service_cmd, "has_systemd", lambda: True)
    monkeypatch.setattr(service_cmd, "load_context", lambda ctx: (cfg, ni))
    calls = []
    monkeypatch.setattr(service_cmd, "_systemctl", lambda action, **k: calls.append(action) or True)
    source = cfg.server_dir_path / server_unit(cfg)
    source.write_text(_render_service(cfg), encoding="utf-8")
    return source, calls


def test_install_refuses_a_foreign_unit_even_with_yes(user_cfg, monkeypatch):
    _cli(monkeypatch, user_cfg, ni=True)
    path = _put(user_cfg, FOREIGN)

    result = CliRunner().invoke(service_group, ["install"], obj={})

    assert path.read_text(encoding="utf-8") == FOREIGN
    assert "--replace" in result.output


def test_install_replace_installs_over_a_foreign_unit(user_cfg, monkeypatch):
    source, calls = _cli(monkeypatch, user_cfg, ni=True)
    path = _put(user_cfg, FOREIGN)

    result = CliRunner().invoke(service_group, ["install", "--replace"], obj={})

    assert result.exit_code == 0, result.output
    assert path.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert calls == ["daemon-reload"]


def test_yes_does_not_replace_an_edited_unit(user_cfg, monkeypatch):
    source, calls = _cli(monkeypatch, user_cfg, ni=True)
    unit_guard.record(user_cfg, source.read_text(encoding="utf-8"))
    edited = source.read_text(encoding="utf-8") + "# mine\n"
    path = _put(user_cfg, edited)

    result = CliRunner().invoke(service_group, ["install", "--replace"], obj={})

    assert path.read_text(encoding="utf-8") == edited
    assert calls == []
    assert "edited by hand" in result.output


@pytest.mark.parametrize("answer, replaced", [(True, True), (False, False)])
def test_an_edited_unit_is_replaced_on_an_answer(user_cfg, monkeypatch, answer, replaced):
    source, _calls = _cli(monkeypatch, user_cfg, ni=False)
    unit_guard.record(user_cfg, source.read_text(encoding="utf-8"))
    edited = source.read_text(encoding="utf-8") + "# mine\n"
    path = _put(user_cfg, edited)
    asked = []
    monkeypatch.setattr(service_cmd, "confirm", lambda msg, **k: asked.append(msg) or answer)

    CliRunner().invoke(service_group, ["install"], obj={})

    assert "edited by hand" in asked[0]
    assert (path.read_text(encoding="utf-8") != edited) is replaced


def test_service_update_refuses_before_it_stops_the_server(user_cfg, monkeypatch):
    _source, calls = _cli(monkeypatch, user_cfg, ni=True)
    path = _put(user_cfg, FOREIGN)

    CliRunner().invoke(service_group, ["update"], obj={})

    assert calls == [], "nothing stopped, nothing installed"
    assert path.read_text(encoding="utf-8") == FOREIGN


# --- the web's regenerate -----------------------------------------------------------


def test_web_regenerate_answers_409_and_leaves_the_server_running(user_cfg, monkeypatch):
    from fastapi.testclient import TestClient

    from app.server import service_control
    from app.web.routers import service as service_router
    from app.web.server import create_app

    monkeypatch.setattr(service_router, "IS_WINDOWS", False)
    monkeypatch.setattr(service_router, "has_systemd", lambda: True)
    monkeypatch.setattr(
        systemd_scope, "privileged_ops_available", lambda config=None: (True, "")
    )
    monkeypatch.setattr(service_control, "is_server_running", lambda config=None: True)
    stopped = []
    monkeypatch.setattr(service_control, "stop_server", lambda config=None: stopped.append(1))
    path = _put(user_cfg, FOREIGN)

    from app.web.auth import hash_password

    user_cfg.web_password_hash = hash_password("testpass123")
    user_cfg.save()
    client = TestClient(create_app(user_cfg))
    client.post("/api/auth/login", json={"password": "testpass123"})
    r = client.post("/api/service/regenerate")

    assert r.status_code == 409
    assert "not written by Zedstead" in r.json()["detail"]
    assert stopped == []
    assert path.read_text(encoding="utf-8") == FOREIGN
