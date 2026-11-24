"""Tests for the non-interactive `zsm setup` bootstrap command."""

from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from app.commands.setup import setup
from tests.conftest import learned_ids, write_test_ids


def _obj():
    return {"non_interactive": True}


def test_skips_install_without_token(sample_config):
    """No Steam token -> server install skipped. Nothing is seeded: the IDs
    zsm ships with are read from code (plans/77)."""
    sample_config.save()
    runner = CliRunner()
    with patch("app.commands.setup.check_steam_login", return_value=False), \
         patch("app.commands.setup.update_dayz_server") as mock_install:
        result = runner.invoke(setup, obj=_obj())

    assert result.exit_code == 0, result.output
    assert not (sample_config.server_dir_path / "mods.txt").exists()
    assert not (sample_config.server_dir_path / "cached-mod-ids.txt").exists()
    mock_install.assert_not_called()
    assert "steam-login" in result.output


def test_learned_ids_are_left_alone(sample_config):
    """The Workshop IDs zsm learned are left untouched."""
    sample_config.save()
    write_test_ids({'@KEEP': '1'})
    runner = CliRunner()
    with patch("app.commands.setup.check_steam_login", return_value=False), \
         patch("app.commands.setup.update_dayz_server"):
        result = runner.invoke(setup, obj=_obj())

    assert result.exit_code == 0, result.output
    assert learned_ids() == {"@KEEP": "1"}


def test_installs_server_when_token_present(sample_config):
    """Token cached + server missing -> update_dayz_server is called."""
    sample_config.save()
    runner = CliRunner()
    with patch("app.commands.setup.check_steam_login", return_value=True), \
         patch("app.commands.setup.update_dayz_server") as mock_install:
        result = runner.invoke(setup, obj=_obj())

    assert result.exit_code == 0, result.output
    mock_install.assert_called_once()


def test_skips_install_when_already_installed(sample_config):
    """Server binary present -> no reinstall even with a token."""
    sample_config.save()
    (sample_config.server_dir_path / "DayZServer").write_text("", encoding="utf-8")
    runner = CliRunner()
    with patch("app.commands.setup.check_steam_login", return_value=True), \
         patch("app.commands.setup.update_dayz_server") as mock_install:
        result = runner.invoke(setup, obj=_obj())

    assert result.exit_code == 0, result.output
    mock_install.assert_not_called()


def test_setup_never_opts_in_to_site(sample_config):
    """Headless paths never opt in (plans/57): turning a zedstead.com switch on is
    always a human act, so a scripted bootstrap leaves both off and no IDs."""
    sample_config.save()
    runner = CliRunner()
    with patch("app.commands.setup.check_steam_login", return_value=False), \
         patch("app.commands.setup.update_dayz_server"):
        result = runner.invoke(setup, obj=_obj())

    assert result.exit_code == 0, result.output
    from app.shared.config import DayzConfig

    saved = DayzConfig.load()
    assert saved.site_ping is False and saved.site_listing is False
    assert saved.site_install_id == "" and saved.site_secret == ""
