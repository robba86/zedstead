"""Tests for `zsm mods` CLI commands whose exit code a scheduled task reads."""

from __future__ import annotations

from click.testing import CliRunner

from app.commands import mods as mods_cmd
from app.commands.mods import mods_group
from tests.conftest import disabled_names, server_only_names, write_test_mods


def _config(**kwargs):
    from app.shared.config import DayzConfig

    return DayzConfig(
        server_dir="/srv/dayz", steamcmd_dir="/opt/steamcmd", steam_username="alice", **kwargs
    )


def test_check_updates_without_an_api_key_exits_non_zero(monkeypatch):
    """TODO C3 point 4: under a timer, exit 0 reads as "checked, nothing found"."""
    monkeypatch.setattr(mods_cmd.DayzConfig, "load", staticmethod(lambda: _config()))

    result = CliRunner().invoke(mods_group, ["check-updates"])
    assert result.exit_code == 1
    assert "No Steam API key set" in result.output


def test_check_updates_with_a_key_and_nothing_outdated_exits_zero(monkeypatch):
    monkeypatch.setattr(
        mods_cmd.DayzConfig, "load", staticmethod(lambda: _config(steam_api_key="k"))
    )
    from app.modmgr.workshop_api import UpdateCheck

    monkeypatch.setattr(
        "app.modmgr.mod_ops.detect_mod_updates",
        lambda config: UpdateCheck(checked=2, total=2, checked_at=1),
    )

    result = CliRunner().invoke(mods_group, ["check-updates"])
    assert result.exit_code == 0, result.output
    assert "up to date" in result.output


def test_check_updates_exits_one_when_steam_does_not_answer(monkeypatch, tmp_path):
    """plans/50 B: a failed check printed "All mods are up to date." and exited 0."""
    from app.modmgr import workshop_api

    monkeypatch.setattr(
        mods_cmd.DayzConfig, "load", staticmethod(lambda: _config(steam_api_key="k"))
    )
    monkeypatch.setattr("app.shared.paths.CONFIG_DIR", tmp_path)

    def _fail(config):
        raise workshop_api.UpdateCheckError(workshop_api.UNREACHABLE, "Could not reach the Steam Web API (down).")

    monkeypatch.setattr("app.modmgr.mod_ops.detect_mod_updates", _fail)

    result = CliRunner().invoke(mods_group, ["check-updates"])
    assert result.exit_code == 1
    assert "Could not reach the Steam Web API" in result.output
    assert "up to date" not in result.output
    assert not (tmp_path / "mod_updates.json").exists()


def test_check_updates_saves_an_empty_answer_too(monkeypatch, tmp_path):
    """A check that finds nothing must clear a stale list, not leave it."""
    from app.modmgr import workshop_api
    from app.modmgr.workshop_api import ModUpdateInfo, UpdateCheck

    monkeypatch.setattr(
        mods_cmd.DayzConfig, "load", staticmethod(lambda: _config(steam_api_key="k"))
    )
    monkeypatch.setattr("app.shared.paths.CONFIG_DIR", tmp_path)
    stale = UpdateCheck(updates=[ModUpdateInfo("1", "A", 10, 5)], checked=1, total=1, checked_at=1)
    workshop_api.save_detected_updates(tmp_path / "mod_updates.json", stale)
    monkeypatch.setattr(
        "app.modmgr.mod_ops.detect_mod_updates",
        lambda config: UpdateCheck(checked=1, total=1, checked_at=2),
    )

    result = CliRunner().invoke(mods_group, ["check-updates"])
    assert result.exit_code == 0, result.output
    check = workshop_api.load_update_check(tmp_path / "mod_updates.json")
    assert check.updates == [] and check.checked_at == 2


def test_check_updates_prints_a_title_holding_markup_as_it_is(monkeypatch, tmp_path):
    """plans/68 CON-16: a Workshop title with "[/x]" in it ended the command
    (and the scheduled mod_check) with MarkupError after the list was saved."""
    from app.modmgr.workshop_api import ModUpdateInfo, UpdateCheck

    monkeypatch.setattr(
        mods_cmd.DayzConfig, "load", staticmethod(lambda: _config(steam_api_key="k"))
    )
    monkeypatch.setattr("app.shared.paths.CONFIG_DIR", tmp_path)
    monkeypatch.setattr(mods_cmd.console, "width", 200)
    updates = [ModUpdateInfo("111", "Trader [x] [/y]", 10, 5), ModUpdateInfo("222", "[/bad]", 10, 5)]
    monkeypatch.setattr(
        "app.modmgr.mod_ops.detect_mod_updates",
        lambda config: UpdateCheck(updates=updates, checked=2, total=2, checked_at=1),
    )

    result = CliRunner().invoke(mods_group, ["check-updates"])
    assert result.exit_code == 0, result.output
    assert "Trader [x] [/y]" in result.output and "[/bad]" in result.output


def test_server_side_marks_the_mod_and_regenerates_the_unit(sample_config, monkeypatch):
    """plans/47 A: `zsm mods server-side NAME [--off]`."""
    from app.shared.config import DayzConfig

    write_test_mods(sample_config, ['@CF', '@DayZ-Dynamic-AI-Addon'])
    sample_config.save()
    regen = []
    monkeypatch.setattr(
        "app.server.service_ops.generate_service_files",
        lambda config: regen.append(list(server_only_names(config))),
    )

    result = CliRunner().invoke(mods_group, ["server-side", "DayZ-Dynamic-AI-Addon"])
    assert result.exit_code == 0, result.output
    assert "-serverMod=" in result.output
    assert server_only_names(sample_config) == ["@DayZ-Dynamic-AI-Addon"]

    result = CliRunner().invoke(mods_group, ["server-side", "@DayZ-Dynamic-AI-Addon", "--off"])
    assert result.exit_code == 0, result.output
    assert server_only_names(sample_config) == []
    assert regen == [["@DayZ-Dynamic-AI-Addon"], []]


def test_server_side_refuses_a_mod_not_in_mods_txt(sample_config, monkeypatch):
    from app.shared.config import DayzConfig

    write_test_mods(sample_config, ['@CF'])
    sample_config.save()
    monkeypatch.setattr(
        "app.server.service_ops.generate_service_files",
        lambda config: (_ for _ in ()).throw(AssertionError("must not regenerate")),
    )

    result = CliRunner().invoke(mods_group, ["server-side", "@Nope"])
    assert "not in the mod list" in result.output
    assert server_only_names(sample_config) == []


def test_adding_a_catalogued_server_side_mod_marks_it_once(sample_config):
    write_test_mods(sample_config, ["@CF", "@DayZ-Dynamic-AI-Addon"])
    mods_cmd._mark_known_server_side(sample_config, ["@CF", "@DayZ-Dynamic-AI-Addon"])
    assert server_only_names(sample_config) == ["@DayZ-Dynamic-AI-Addon"]

    mods_cmd._mark_known_server_side(sample_config, ["@DayZ-Dynamic-AI-Addon"])
    assert server_only_names(sample_config) == ["@DayZ-Dynamic-AI-Addon"]


def test_enable_and_disable_rebuild_the_unit(sample_config, monkeypatch):
    # A disabled mod kept loading until something else rebuilt the unit: the
    # CLI stopped at config.json while the web route regenerated (2026 Q3).
    from app.shared.config import DayzConfig

    write_test_mods(sample_config, ['@CF', '@DayZ-Dynamic-AI-Addon'])
    sample_config.save()
    monkeypatch.setattr("app.modmgr.mods.apply_mod_state_to_mission_xml", lambda *a, **k: None)
    regen = []
    monkeypatch.setattr(
        "app.server.service_ops.generate_service_files",
        lambda config: regen.append(list(disabled_names(config))),
    )

    result = CliRunner().invoke(mods_group, ["disable", "@DayZ-Dynamic-AI-Addon"])
    assert result.exit_code == 0, result.output
    assert disabled_names(sample_config) == ["@DayZ-Dynamic-AI-Addon"]

    result = CliRunner().invoke(mods_group, ["enable", "@DayZ-Dynamic-AI-Addon"])
    assert result.exit_code == 0, result.output
    assert regen == [["@DayZ-Dynamic-AI-Addon"], []]


def test_a_failed_unit_rebuild_keeps_the_setting_and_says_how_to_recover(sample_config, monkeypatch):
    from app.shared.config import DayzConfig

    write_test_mods(sample_config, ['@CF'])
    sample_config.save()
    monkeypatch.setattr("app.modmgr.mods.apply_mod_state_to_mission_xml", lambda *a, **k: None)

    def _boom(config):
        raise PermissionError("sudo needed")

    monkeypatch.setattr("app.server.service_ops.generate_service_files", _boom)

    result = CliRunner().invoke(mods_group, ["disable", "@CF"])
    assert result.exit_code == 0, result.output
    assert disabled_names(sample_config) == ["@CF"]
    assert "zsm service generate" in result.output


def test_a_mod_name_holding_brackets_is_printed_as_it_is(sample_config, monkeypatch):
    """Rich read "[x]" and "[b]" in a mod folder's name as tags and dropped
    them. ("[/y]", which ended the command with a MarkupError, cannot be a
    folder name, and mods.json refuses it.)"""
    from app.shared.config import DayzConfig

    write_test_mods(sample_config, ['@Cars[x]', '@Odd[b]'])
    sample_config.save()
    monkeypatch.setattr(mods_cmd.console, "width", 200)
    monkeypatch.setattr("app.modmgr.mods.apply_mod_state_to_mission_xml", lambda *a, **k: None)
    monkeypatch.setattr("app.server.service_ops.generate_service_files", lambda config: None)

    result = CliRunner().invoke(mods_group, ["disable", "@Odd[b]"])
    assert result.exit_code == 0, result.output
    assert "Disabled @Odd[b]" in result.output
    assert disabled_names(sample_config) == ["@Odd[b]"]

    result = CliRunner().invoke(mods_group, ["list"])
    assert result.exit_code == 0, result.output
    assert "@Cars[x]" in result.output and "@Odd[b]" in result.output

    result = CliRunner().invoke(mods_group, ["enable", "@Odd[b]"])
    assert result.exit_code == 0, result.output
    assert "Enabled @Odd[b]" in result.output


def _spawn_test_cli(monkeypatch):
    """`mods spawn-test start` with the mod listed, the server stopped and the
    ops' start recorded instead of run."""
    from app.ops import spawn_test_ops

    monkeypatch.setattr(mods_cmd.DayzConfig, "load", staticmethod(lambda: _config()))
    monkeypatch.setattr(mods_cmd.modlist, "installed", lambda config: ["@Bikes"])
    monkeypatch.setattr(mods_cmd, "_refuse_while_running", lambda config: None)
    monkeypatch.setattr(spawn_test_ops, "spawn_count_warning", lambda: (None, ""))
    monkeypatch.setattr(
        spawn_test_ops,
        "mission_locations",
        lambda config: ([{"name": "Adamovka", "x": 5340.7, "z": 11380.0}], 15360),
    )
    calls = []
    monkeypatch.setattr(
        spawn_test_ops,
        "start_at_location",
        lambda config, mod, cls, count, x, z, name, log: calls.append((mod, cls, count, x, z, name)),
    )
    return calls


def test_spawn_test_at_a_named_location_uses_its_coordinates(monkeypatch):
    calls = _spawn_test_cli(monkeypatch)
    result = CliRunner().invoke(
        mods_group,
        ["spawn-test", "start", "@Bikes", "Bicycle", "--mode", "at-location",
         "--location", "adamovka", "--count", "2"],
    )
    assert result.exit_code == 0, result.output
    assert calls == [("@Bikes", "Bicycle", 2, 5340.7, 11380.0, "Adamovka")]


def test_spawn_test_at_a_location_needs_a_place_or_both_coordinates(monkeypatch):
    calls = _spawn_test_cli(monkeypatch)
    args = ["spawn-test", "start", "@Bikes", "Bicycle", "--mode", "at-location"]
    result = CliRunner().invoke(mods_group, [*args, "--x", "100"])
    assert result.exit_code == 1
    assert "needs --location, or both --x and --z" in result.output
    result = CliRunner().invoke(mods_group, [*args, "--location", "Nowhere"])
    assert result.exit_code == 1
    assert "No place called Nowhere" in result.output
    result = CliRunner().invoke(mods_group, [*args, "--x", "100", "--z", "200"])
    assert result.exit_code == 0, result.output
    assert calls == [("@Bikes", "Bicycle", 10, 100.0, 200.0, None)]
