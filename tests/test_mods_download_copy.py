"""Smoke tests for `zsm mods download` and `zsm mods copy`."""

from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from app.commands.mods import mods_group


def test_download_invokes_helper():
    runner = CliRunner()
    with patch("app.commands.mods._download_mods") as mock_dl, \
         patch("app.commands.mods.DayzConfig.load") as mock_load:
        result = runner.invoke(mods_group, ["download"])
    assert result.exit_code == 0, result.output
    mock_dl.assert_called_once_with(mock_load.return_value)


def test_copy_invokes_helper():
    runner = CliRunner()
    with patch("app.commands.mods._copy_mods_to_server") as mock_copy, \
         patch("app.commands.mods.DayzConfig.load") as mock_load:
        result = runner.invoke(mods_group, ["copy"])
    assert result.exit_code == 0, result.output
    mock_copy.assert_called_once_with(mock_load.return_value)


def test_remove_requires_at_least_one_name():
    runner = CliRunner()
    result = runner.invoke(mods_group, ["remove"])
    assert result.exit_code != 0
    assert "Missing argument" in result.output or "Usage" in result.output


def test_remove_skips_unknown_names():
    """`mods remove @Unknown` should print a skip warning and not call uninstall."""
    runner = CliRunner()
    with patch("app.modmgr.modlist.installed", return_value=["@CF", "@TraderPlus"]), \
         patch("app.commands.mods.uninstall_mods") as mock_uninstall, \
         patch("app.commands.mods.confirm", return_value=True), \
         patch("app.commands.mods.DayzConfig.load"):
        result = runner.invoke(
            mods_group, ["remove", "@DoesNotExist"], obj={"non_interactive": True},
        )
    assert result.exit_code == 0, result.output
    assert "not in the mod list" in result.output
    mock_uninstall.assert_not_called()
