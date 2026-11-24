"""Tests for `zsm settings` get/set/list."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from app.commands.settings import settings_group
from app.shared.config import DayzConfig


def _fresh_config() -> DayzConfig:
    return DayzConfig(
        server_dir="/srv/dayz",
        steamcmd_dir="/opt/steamcmd",
        steam_username="alice",
        server_args="-adminlog -netlog -freezecheck -cpuCount=4",
    )


def test_list_groups_by_section():
    runner = CliRunner()
    with patch("app.commands.settings.DayzConfig.load", return_value=_fresh_config()):
        result = runner.invoke(settings_group, ["list"])
    assert result.exit_code == 0, result.output
    for section in ("Base", "Backups", "Features", "Restart Warnings", "Discord"):
        assert section in result.output


def test_list_filters_by_section():
    runner = CliRunner()
    with patch("app.commands.settings.DayzConfig.load", return_value=_fresh_config()):
        result = runner.invoke(settings_group, ["list", "--section", "Base"])
    assert result.exit_code == 0, result.output
    assert "Base" in result.output
    assert "Discord" not in result.output


def test_list_unknown_section_errors():
    runner = CliRunner()
    with patch("app.commands.settings.DayzConfig.load", return_value=_fresh_config()):
        result = runner.invoke(settings_group, ["list", "--section", "Nope"])
    assert result.exit_code != 0
    assert "No section" in result.output


def test_get_unknown_key_errors():
    runner = CliRunner()
    with patch("app.commands.settings.DayzConfig.load", return_value=_fresh_config()):
        result = runner.invoke(settings_group, ["get", "no_such_key"])
    assert result.exit_code != 0
    assert "Unknown setting" in result.output


def test_get_string_value():
    runner = CliRunner()
    with patch("app.commands.settings.DayzConfig.load", return_value=_fresh_config()):
        result = runner.invoke(settings_group, ["get", "steam_username"])
    assert result.exit_code == 0, result.output
    assert "alice" in result.output


def test_set_string_persists():
    cfg = _fresh_config()
    cfg.save = MagicMock()
    runner = CliRunner()
    with patch("app.commands.settings.DayzConfig.load", return_value=cfg):
        result = runner.invoke(settings_group, ["set", "steam_username", "bob"])
    assert result.exit_code == 0, result.output
    assert cfg.steam_username == "bob"
    cfg.save.assert_called_once()


def test_set_bool_accepts_true_false():
    cfg = _fresh_config()
    cfg.save = MagicMock()
    runner = CliRunner()
    with patch("app.commands.settings.DayzConfig.load", return_value=cfg):
        result = runner.invoke(settings_group, ["set", "discord_hook_chat", "true"])
    assert result.exit_code == 0, result.output
    assert cfg.discord_hook_chat is True

    cfg.save.reset_mock()
    with patch("app.commands.settings.DayzConfig.load", return_value=cfg):
        result = runner.invoke(settings_group, ["set", "discord_hook_chat", "no"])
    assert result.exit_code == 0, result.output
    assert cfg.discord_hook_chat is False


def test_set_service_scope_accepts_valid_choice():
    cfg = _fresh_config()
    cfg.save = MagicMock()
    runner = CliRunner()
    with patch("app.commands.settings.DayzConfig.load", return_value=cfg):
        result = runner.invoke(settings_group, ["set", "service_scope", "user"])
    assert result.exit_code == 0, result.output
    assert cfg.service_scope == "user"
    cfg.save.assert_called_once()


def test_set_service_scope_rejects_invalid_choice():
    cfg = _fresh_config()
    cfg.save = MagicMock()
    runner = CliRunner()
    with patch("app.commands.settings.DayzConfig.load", return_value=cfg):
        result = runner.invoke(settings_group, ["set", "service_scope", "bogus"])
    assert result.exit_code != 0
    cfg.save.assert_not_called()


def test_set_bool_rejects_garbage():
    cfg = _fresh_config()
    cfg.save = MagicMock()
    runner = CliRunner()
    with patch("app.commands.settings.DayzConfig.load", return_value=cfg):
        result = runner.invoke(settings_group, ["set", "discord_hook_chat", "maybe"])
    assert result.exit_code != 0
    cfg.save.assert_not_called()


def test_set_int_validates():
    cfg = _fresh_config()
    cfg.save = MagicMock()
    runner = CliRunner()
    with patch("app.commands.settings.DayzConfig.load", return_value=cfg):
        result = runner.invoke(settings_group, ["set", "backup_max_count", "ten"])
    assert result.exit_code != 0
    cfg.save.assert_not_called()


def test_set_int_list_parses_csv():
    cfg = _fresh_config()
    cfg.save = MagicMock()
    runner = CliRunner()
    with patch("app.commands.settings.DayzConfig.load", return_value=cfg):
        result = runner.invoke(settings_group, ["set", "restart_warning_minutes", "30,15,5,1"])
    assert result.exit_code == 0, result.output
    assert cfg.restart_warning_minutes == [30, 15, 5, 1]


def test_set_unknown_key_errors():
    cfg = _fresh_config()
    cfg.save = MagicMock()
    runner = CliRunner()
    with patch("app.commands.settings.DayzConfig.load", return_value=cfg):
        result = runner.invoke(settings_group, ["set", "server_args", "--", "-adminlog"])
    assert result.exit_code != 0
    cfg.save.assert_not_called()


# --- values that parse but break something later (plans/51 A) -------------

# (key, a value that must be refused, a value that must be accepted). Each
# refused value was saved before plans/51 and broke something only later:
# see the web UI test findings in plans/, N6-N11 and pass 1 F62.
_REFUSED_AND_ACCEPTED = [
    ("metrics_interval", "0", "60"),                     # N7: sleep(0) busy loop
    ("mod_update_check_interval", "1", "0"),             # N8: 0 = off stays valid
    ("mod_update_check_interval", "-5", "3600"),
    ("service_restart_seconds", "-10", "30"),            # N10: the unit's 1-3600
    ("service_restart_seconds", "3601", "3600"),
    ("restart_limit_burst", "-1", "0"),                  # 0 = never give up (documented)
    ("restart_limit_interval_seconds", "-5", "0"),
    ("restart_lock_at_minutes", "-3", "0"),
    ("restart_warning_minutes", "-5,0,999999", "30,15,5,1"),
    ("restart_warning_minutes", "0", "1440"),
    ("restart_warning_template", "Restart in {mins}", "Restart in {minutes}!"),   # N9
    ("restart_warning_template", "Restart in {minutes", "{{Server}} restarts in {minutes}"),
    ("backup_max_count", "-3", "0"),                     # F62: 0 = keep every backup
    ("update_max_players", "-2", "-1"),                  # -1 = never check (documented)
    ("log_cleanup_days", "-1", "0"),
    ("log_cleanup_keep", "-1", "0"),
    ("web_bind_host", "not a host!", ""),                # N6
    ("web_bind_host", " 0.0.0.0", "0.0.0.0"),
    ("web_bind_host", "999.1.1.1", "::"),
    ("rcon_host", "999.1.1.1", "dayz.example.com"),      # N11
    ("rcon_host", "not a host!", ""),                    # empty falls back to 127.0.0.1
    ("server_dir", "relative/path", "/srv/dayz"),        # N11
    ("server_dir", "/srv/da'yz", "/srv/dayz (copy)"),    # the launch command's quoted set
    ("server_dir", "/srv/dayz\nUnexpected=1", "/srv/dayz server"),
    ("server_dir", "/srv/%h/dayz", "/srv/a;b&c"),
    ("steamcmd_dir", "steamcmd", "~/steamcmd"),          # ~ is expanded on load
    ("backup_dir", "backups", ""),
    ("web_tls_cert", "cert.pem", "/etc/ssl/zsm.pem"),
    ("steam_username", "anonymous", "realowner"),        # SteamCMD: No subscription
    ("steam_username", " Anonymous ", "anonymous2"),
    ("steam_username", "+quit", "real_owner"),           # SteamCMD reads + as a command
    ("steam_username", "two words", "two_words"),
    # F4 (2026-11-17): the sender drops a non-Discord URL without a word.
    ("discord_webhook_url", "http://discord.com/api/webhooks/1/x", "https://discord.com/api/webhooks/1/x"),
    ("discord_webhook_url", "not a url at all", "https://ptb.discord.com/api/webhooks/1/x"),
    ("discord_webhook_url", "https://evil.example/discord.com", "https://discordapp.com/api/webhooks/1/x"),
    ("discord_webhook_url", "https://notdiscord.com/api/webhooks/1/x", ""),
]


@pytest.mark.parametrize("key,refused,accepted", _REFUSED_AND_ACCEPTED)
def test_set_refuses_a_value_that_breaks_something_later(key, refused, accepted):
    cfg = _fresh_config()
    cfg.save = MagicMock()
    with patch("app.commands.settings.DayzConfig.load", return_value=cfg):
        # No `--`: "-5" must reach the setting as a value, not an option.
        result = CliRunner().invoke(settings_group, ["set", key, refused])
    assert result.exit_code != 0, result.output
    assert key in result.output  # the message names the setting
    cfg.save.assert_not_called()


@pytest.mark.parametrize("key,refused,accepted", _REFUSED_AND_ACCEPTED)
def test_set_still_accepts_the_values_that_mean_something(key, refused, accepted):
    cfg = _fresh_config()
    cfg.save = MagicMock()
    with patch("app.commands.settings.DayzConfig.load", return_value=cfg):
        result = CliRunner().invoke(settings_group, ["set", key, accepted])
    assert result.exit_code == 0, result.output
    cfg.save.assert_called_once()


@pytest.mark.parametrize("key,value,expected", [
    ("update_max_players", "-1", -1),
    ("restart_warning_template", "-- restart in {minutes} min", "-- restart in {minutes} min"),
])
def test_a_value_starting_with_a_dash_needs_no_double_dash(key, value, expected):
    # `zsm settings set update_max_players -1` used to fail: Click read -1 as
    # an unknown option, so the documented "never check" value could only be
    # set by knowing to write `--` first.
    cfg = _fresh_config()
    cfg.save = MagicMock()
    with patch("app.commands.settings.DayzConfig.load", return_value=cfg):
        result = CliRunner().invoke(settings_group, ["set", key, value])
    assert result.exit_code == 0, result.output
    assert getattr(cfg, key) == expected
    cfg.save.assert_called_once()


def test_a_template_message_explains_the_placeholder():
    cfg = _fresh_config()
    cfg.save = MagicMock()
    with patch("app.commands.settings.DayzConfig.load", return_value=cfg):
        result = CliRunner().invoke(
            settings_group, ["set", "restart_warning_template", "Restart in {mins}"]
        )
    assert "{minutes}" in result.output


def _with_manifest(tmp_path, app: str) -> DayzConfig:
    cfg = _fresh_config()
    cfg.server_dir = str(tmp_path)
    (tmp_path / "steamapps").mkdir()
    (tmp_path / "steamapps" / f"appmanifest_{app}.acf").write_text("")
    cfg.save = MagicMock()
    return cfg


def test_server_branch_takes_only_its_two_values():
    cfg = _fresh_config()
    cfg.save = MagicMock()
    with patch("app.commands.settings.DayzConfig.load", return_value=cfg):
        good = CliRunner().invoke(settings_group, ["set", "server_branch", "experimental"])
        bad = CliRunner().invoke(settings_group, ["set", "server_branch", "beta"])
    assert good.exit_code == 0, good.output
    assert cfg.server_branch == "experimental"
    assert bad.exit_code != 0


def test_server_branch_cannot_switch_an_installed_server(tmp_path):
    cfg = _with_manifest(tmp_path, "223350")
    with patch("app.commands.settings.DayzConfig.load", return_value=cfg):
        result = CliRunner().invoke(settings_group, ["set", "server_branch", "experimental"])
    assert result.exit_code != 0
    assert "holds the stable DayZ server" in result.output
    assert "--branch experimental" in result.output
    assert cfg.server_branch == "stable"
    cfg.save.assert_not_called()


def test_server_branch_can_be_set_back_to_what_is_installed(tmp_path):
    cfg = _with_manifest(tmp_path, "223350")
    cfg.server_branch = "experimental"  # a hand edit
    with patch("app.commands.settings.DayzConfig.load", return_value=cfg):
        result = CliRunner().invoke(settings_group, ["set", "server_branch", "stable"])
    assert result.exit_code == 0, result.output
    assert cfg.server_branch == "stable"


# --- credentials in `settings list` (SEC-5) ----------------------------------

_API_KEY = "0123456789ABCDEF0123456789ABCDEF"
_WEBHOOK = "https://discord.com/api/webhooks/1/secret-token"


def test_list_says_only_whether_a_credential_is_set():
    cfg = _fresh_config()
    cfg.steam_api_key = _API_KEY
    cfg.discord_webhook_url = _WEBHOOK
    with patch("app.commands.settings.DayzConfig.load", return_value=cfg):
        result = CliRunner(env={"COLUMNS": "400"}).invoke(settings_group, ["list"])
    assert result.exit_code == 0, result.output
    assert _API_KEY not in result.output
    assert "secret-token" not in result.output
    assert "<set>" in result.output
    assert "alice" in result.output  # an ordinary value is still shown


def test_list_says_when_a_credential_is_not_set():
    with patch("app.commands.settings.DayzConfig.load", return_value=_fresh_config()):
        result = CliRunner(env={"COLUMNS": "400"}).invoke(settings_group, ["list", "--section", "Discord"])
    assert result.exit_code == 0, result.output
    assert "<not set>" in result.output


@pytest.mark.parametrize("key, value", [("steam_api_key", _API_KEY), ("discord_webhook_url", _WEBHOOK)])
def test_get_still_prints_a_credential(key, value):
    cfg = _fresh_config()
    setattr(cfg, key, value)
    with patch("app.commands.settings.DayzConfig.load", return_value=cfg):
        result = CliRunner(env={"COLUMNS": "400"}).invoke(settings_group, ["get", key])
    assert result.exit_code == 0, result.output
    assert value in result.output


def test_every_credential_looking_setting_is_on_the_one_list():
    """A new setting holding a secret has to be added to
    bundle_ops.SENSITIVE_CONFIG_FIELDS, which `settings list` masks from."""
    from app.ops.bundle_ops import SENSITIVE_CONFIG_FIELDS
    from app.shared.settings_registry import _REGISTRY

    listed = {field for field, _label in SENSITIVE_CONFIG_FIELDS}
    marks = ("secret", "password", "api_key", "webhook", "token")
    looks_secret = {s.key for s in _REGISTRY if any(m in s.key for m in marks)}
    assert looks_secret == {"steam_api_key", "discord_webhook_url"}
    assert looks_secret <= listed
