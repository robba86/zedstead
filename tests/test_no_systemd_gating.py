"""Gating of systemd-only actions when systemd isn't the init (e.g. container).

On a real systemd host these gates are no-ops (has_systemd() is True), so host
behaviour is unchanged; these tests force has_systemd() False to prove the
container path skips/aborts cleanly instead of erroring on missing systemctl.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.commands import service as service_cmd
from app.server import scheduler, service_control


def test_systemctl_skips_when_no_systemd(monkeypatch):
    """service_control.systemctl returns a failed ServiceResult without
    invoking subprocess when no systemd."""
    monkeypatch.setattr(service_control, "has_systemd", lambda: False)
    with patch("app.server.service_control.subprocess.run") as mock_run:
        result = service_control.systemctl("start")
    assert result.ok is False
    mock_run.assert_not_called()


def test_cli_systemctl_wrapper_skips_when_no_systemd(monkeypatch):
    """The CLI _systemctl wrapper delegates to service_control and returns
    False without invoking subprocess when no systemd."""
    monkeypatch.setattr(service_control, "has_systemd", lambda: False)
    with patch("app.server.service_control.subprocess.run") as mock_run:
        assert service_cmd._systemctl("start") is False
    mock_run.assert_not_called()


async def test_install_timers_raises_without_systemd(monkeypatch, sample_config):
    """install_timers_async aborts with a clear RuntimeError (caught by the UI)."""
    monkeypatch.setattr(scheduler, "IS_WINDOWS", False)
    monkeypatch.setattr(scheduler, "has_systemd", lambda: False)
    with pytest.raises(RuntimeError, match="systemd is not available"):
        await scheduler.install_timers_async(sample_config)


async def test_remove_timers_noop_without_systemd(monkeypatch, sample_config):
    """remove_timers_async returns [] (nothing was ever installed)."""
    monkeypatch.setattr(scheduler, "IS_WINDOWS", False)
    monkeypatch.setattr(scheduler, "has_systemd", lambda: False)
    assert await scheduler.remove_timers_async(sample_config) == []
