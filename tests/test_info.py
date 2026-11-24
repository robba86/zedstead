"""Tests for app/commands/info.py (zsm info)."""

from __future__ import annotations

from click.testing import CliRunner

from app.commands.info import info


def test_info_ends_after_its_tables_without_waiting_for_input(sample_config, tmp_path):
    # It ended with a "← Go Back" menu from the deleted CLI menus, which hung
    # any run without a terminal (a pipe, cron) until someone pressed a key.
    sample_config.workshop_content_dir = str(tmp_path / "workshop")  # not the host's cache
    sample_config.save()
    result = CliRunner().invoke(info, [])
    assert result.exit_code == 0, result.output
    assert "Configuration" in result.output
    assert "Go Back" not in result.output
