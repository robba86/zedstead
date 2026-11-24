"""Tests for service-file generation, esp. the systemd-presence gate."""

from __future__ import annotations

import dataclasses
from unittest.mock import patch

from app.server import service_ops
from app.commands.service import SERVICE_NAME


def test_skips_systemd_install_when_no_systemd(sample_config, monkeypatch):
    """No systemd: write the unit to the server dir but don't try to install it."""
    monkeypatch.setattr(service_ops, "has_systemd", lambda: False)
    with patch("app.commands.service._render_service", return_value="[Unit]\n"), \
         patch("app.server.service_ops.subprocess.run") as mock_run:
        service_ops.generate_service_files(sample_config)

    mock_run.assert_not_called()
    assert (sample_config.server_dir_path / SERVICE_NAME).exists()


def test_installs_unit_when_systemd_present(sample_config, monkeypatch):
    """systemd present: cp the unit + daemon-reload (two subprocess calls)."""
    monkeypatch.setattr(service_ops, "has_systemd", lambda: True)
    with patch("app.commands.service._render_service", return_value="[Unit]\n"), \
         patch("app.server.service_ops.subprocess.run") as mock_run:
        service_ops.generate_service_files(sample_config)

    assert mock_run.call_count == 2  # sudo cp + systemctl daemon-reload


def test_installs_user_unit_writes_directly_no_cp(sample_config, monkeypatch, tmp_path):
    """User scope: write the unit into the XDG user dir + one `--user
    daemon-reload`, no `sudo cp`."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    monkeypatch.setattr(service_ops, "has_systemd", lambda: True)
    cfg = dataclasses.replace(sample_config, service_scope="user")

    cmds = []
    with patch("app.commands.service._render_service", return_value="[Unit]\n"), \
         patch("app.server.service_ops.subprocess.run",
               side_effect=lambda cmd, **k: cmds.append(cmd)):
        service_ops.generate_service_files(cfg)

    unit = tmp_path / "xdg" / "systemd" / "user" / SERVICE_NAME
    assert unit.exists() and unit.read_text() == "[Unit]\n"
    assert len(cmds) == 1  # only daemon-reload
    assert cmds[0] == ["systemctl", "--user", "daemon-reload"]
