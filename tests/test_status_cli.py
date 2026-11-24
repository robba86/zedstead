"""Tests for `zsm status` — live .RPT tail."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from app.commands.status import status_cmd
from app.shared.config import DayzConfig


def test_status_missing_profiles_dir_exits(tmp_path: Path):
    cfg = DayzConfig(server_dir=str(tmp_path), profiles_path="profiles")
    runner = CliRunner()
    with patch("app.commands.status.DayzConfig.load", return_value=cfg):
        result = runner.invoke(status_cmd, [])
    assert result.exit_code == 1
    assert "Profiles directory not found" in result.output


def test_status_tail_writes_existing_lines_then_exits_on_ctrl_c(tmp_path: Path):
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    rpt = profiles / "session.RPT"
    rpt.write_text("first line\nsecond line\n", encoding="utf-8")

    cfg = DayzConfig(server_dir=str(tmp_path), profiles_path="profiles")
    runner = CliRunner()

    # Make the tail loop raise KeyboardInterrupt the first time it sleeps.
    with patch("app.commands.status.DayzConfig.load", return_value=cfg), \
         patch("app.commands.status.time.sleep", side_effect=KeyboardInterrupt):
        result = runner.invoke(status_cmd, [])

    assert result.exit_code == 0, result.output
    assert "first line" in result.output
    assert "second line" in result.output


def test_status_waits_for_rpt_when_none_present(tmp_path: Path):
    """No .RPT yet — should print the waiting message and exit cleanly on Ctrl+C."""
    profiles = tmp_path / "profiles"
    profiles.mkdir()

    cfg = DayzConfig(server_dir=str(tmp_path), profiles_path="profiles")
    runner = CliRunner()

    with patch("app.commands.status.DayzConfig.load", return_value=cfg), \
         patch("app.commands.status.time.sleep", side_effect=KeyboardInterrupt):
        result = runner.invoke(status_cmd, [])

    assert result.exit_code == 0, result.output
    assert "Waiting for the server" in result.output
