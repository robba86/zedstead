"""Tests for `zsm maps switch` — flag parsing and dispatch."""

from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from app.commands.maps import maps_group
from app.maps.map_ops import SwitchChoices
from tests.conftest import installed_names, write_test_mods


def test_unknown_map_errors():
    runner = CliRunner()
    with patch("app.commands.maps.DayzConfig.load"):
        result = runner.invoke(maps_group, ["switch", "Atlantis"])
    assert result.exit_code != 0
    assert "Unknown map" in result.output


def test_unreleased_map_refuses_before_dispatch(unreleased_map):
    """An announced map is in VANILLA_MAPS with no mission — never reach _activate."""
    runner = CliRunner()
    with patch("app.commands.maps._activate_vanilla_map") as mock_activate, \
         patch("app.commands.maps.DayzConfig.load"):
        result = runner.invoke(maps_group, ["switch", unreleased_map])
    assert result.exit_code != 0
    assert "not been released" in result.output
    mock_activate.assert_not_called()


def test_vanilla_map_dispatches_with_defaults():
    runner = CliRunner()
    with patch("app.commands.maps._activate_vanilla_map") as mock_activate, \
         patch("app.commands.maps.DayzConfig.load"):
        result = runner.invoke(maps_group, ["switch", "Chernarus"])
    assert result.exit_code == 0, result.output
    mock_activate.assert_called_once()
    _, kwargs = mock_activate.call_args[0], mock_activate.call_args[1]
    assert kwargs == {
        "choices": SwitchChoices(),
        "run_service_update": False,
    }


def test_vanilla_map_no_restore_flag_propagates():
    runner = CliRunner()
    with patch("app.commands.maps._activate_vanilla_map") as mock_activate, \
         patch("app.commands.maps.DayzConfig.load"):
        result = runner.invoke(maps_group, ["switch", "Chernarus", "--no-restore-previous-config"])
    assert result.exit_code == 0, result.output
    assert mock_activate.call_args.kwargs["choices"].restore_previous is False


def test_workshop_map_dispatches_with_all_flags():
    runner = CliRunner()
    with patch("app.commands.maps._activate_workshop_map") as mock_activate, \
         patch("app.commands.maps.DayzConfig.load"):
        result = runner.invoke(maps_group, [
            "switch", "Namalsk",
            "--no-restore-previous-config",
            "--no-apply-recommended",
            "--service-update",
        ])
    assert result.exit_code == 0, result.output
    mock_activate.assert_called_once()
    kwargs = mock_activate.call_args.kwargs
    assert kwargs["choices"].restore_previous is False
    assert kwargs["choices"].apply_recommended is False
    assert kwargs["run_service_update"] is True


def test_a_missing_mission_folder_is_a_clean_click_error(tmp_path):
    """No traceback when the archive lacks the mission folder (plans/60 G)."""
    import click
    import pytest
    from types import SimpleNamespace

    from app.commands.maps import _install_mission
    from app.shared.seed_data import WORKSHOP_MAPS

    config = SimpleNamespace(server_dir_path=tmp_path)
    missing = ValueError("Mission folder not found in the archive: dayzOffline.banov")
    with patch("app.commands.maps.download_and_extract_mission", side_effect=missing):
        with pytest.raises(click.ClickException, match="Mission folder not found"):
            _install_mission(config, WORKSHOP_MAPS["Banov"], {})


def test_workshop_map_default_flags():
    runner = CliRunner()
    with patch("app.commands.maps._activate_workshop_map") as mock_activate, \
         patch("app.commands.maps.DayzConfig.load"):
        result = runner.invoke(maps_group, ["switch", "Namalsk"])
    assert result.exit_code == 0, result.output
    kwargs = mock_activate.call_args.kwargs
    assert kwargs["choices"] == SwitchChoices()
    assert kwargs["run_service_update"] is False


def test_the_cli_settles_the_cfg_the_way_the_web_does(sample_config, capsys):
    """`--no-restore-previous-config` goes through map_ops.switch_map_cfg, the
    rule the web switch uses, and says which cfg it left."""
    from app.commands.maps import _activate_vanilla_map
    from app.parsers.servercfg import read_cfg_value, read_mission_template

    cfg = sample_config.server_cfg_path
    (cfg.parent / "serverDZ.cfg.livonia.backup").write_text('hostname = "Saved Livonia";\n')

    _activate_vanilla_map(
        None, sample_config, "Livonia", "dayzOffline.enoch",
        choices=SwitchChoices(restore_previous=False),
    )

    assert read_cfg_value(cfg, "hostname") == "Test Server"
    assert read_mission_template(cfg) == "dayzOffline.enoch"
    assert "Kept your current config; only the mission changed." in capsys.readouterr().out



def test_the_cli_switch_applies_the_mods_xml_to_the_new_mission(sample_config, monkeypatch):
    from app.commands.maps import _activate_vanilla_map
    from app.ops import pipeline
    from app.parsers.servercfg import read_mission_template

    write_test_mods(sample_config, ['@SomeMod'])
    merged: list[str | None] = []
    monkeypatch.setattr(
        pipeline, "_merge_xml_sync",
        lambda ctx: merged.append(read_mission_template(ctx.config.server_cfg_path)),
    )

    _activate_vanilla_map(
        None, sample_config, "Livonia", "dayzOffline.enoch",
        choices=SwitchChoices(restore_previous=False),
    )

    assert merged == ["dayzOffline.enoch"]

# ── what goes to the new map: the dialog's choices as flags (plans/83) ─


def _manual(file_id: str) -> None:
    from app.xml import xml_manual
    from tests.conftest import manual_dir

    xml_manual.create_file(file_id, "types", '<types><type name="X"/></types>', manual_dir=manual_dir())


def _invoke(sample_config, args, activate="_activate_vanilla_map"):
    runner = CliRunner()
    with patch(f"app.commands.maps.{activate}") as mock_activate, \
         patch("app.commands.maps.DayzConfig.load", return_value=sample_config):
        result = runner.invoke(maps_group, ["switch", *args])
    return result, mock_activate


def test_disable_mod_flag_propagates(sample_config):
    write_test_mods(sample_config, ["@Trader", "@Keep"])
    result, mock_activate = _invoke(
        sample_config, ["Livonia", "--disable-mod", "Trader", "--disable-mod", "@Keep"],
    )
    assert result.exit_code == 0, result.output
    assert mock_activate.call_args.kwargs["choices"].disable_mods == ("Trader", "@Keep")


def test_no_apply_mod_xml_flag_propagates(sample_config):
    result, mock_activate = _invoke(sample_config, ["Livonia", "--no-apply-mod-xml"])
    assert result.exit_code == 0, result.output
    assert mock_activate.call_args.kwargs["choices"].apply_mod_xml is False


def test_leave_manual_file_flag_propagates(sample_config):
    _manual("heli-spawns")
    result, mock_activate = _invoke(sample_config, ["Livonia", "--leave-manual-file", "heli-spawns"])
    assert result.exit_code == 0, result.output
    assert mock_activate.call_args.kwargs["choices"].leave_manual_files == ("heli-spawns",)


def test_dry_run_prints_the_plan_and_changes_nothing(sample_config):
    from app.modmgr import modlist
    from app.xml import xml_manual
    from tests.conftest import manual_dir

    write_test_mods(sample_config, ["@CF", "@Trader", "@Old"], disabled=["@CF", "@Old"])
    _manual("heli-spawns")
    cfg_before = sample_config.server_cfg_path.read_text()

    result, mock_activate = _invoke(
        sample_config,
        ["Deer Isle", "--dry-run", "--disable-mod", "@Trader", "--leave-manual-file", "heli-spawns"],
        activate="_activate_workshop_map",
    )

    assert result.exit_code == 0, result.output
    mock_activate.assert_not_called()
    out = result.output
    assert "Switch from Chernarus to Deer Isle (dry run: nothing is changed)" in out
    assert "@CF (dependency, turned on by the switch)" in out
    assert "@Trader: turned off, still installed, off on every map" in out
    assert "Already off: @Old" in out
    assert "heli-spawns: stays with Chernarus" in out
    assert modlist.disabled(sample_config) == {"@CF", "@Old"}
    assert xml_manual.get_file("heli-spawns", manual_dir=manual_dir())["terrain"] == ""
    assert sample_config.server_cfg_path.read_text() == cfg_before


def test_the_cli_refuses_to_leave_the_maps_own_mod_behind(sample_config):
    write_test_mods(sample_config, ["@CF", "@Trader"])
    result, mock_activate = _invoke(
        sample_config, ["Deer Isle", "--disable-mod", "@CF"], activate="_activate_workshop_map",
    )
    assert result.exit_code != 0
    assert "@CF is Deer Isle's dependency and always goes with it." in result.output
    mock_activate.assert_not_called()


def test_the_cli_rebuilds_the_unit_when_it_turns_mods_off(sample_config, monkeypatch):
    from app.commands import mods as mods_cmd
    from app.commands.maps import _activate_vanilla_map
    from app.modmgr import modlist
    from app.ops import pipeline

    write_test_mods(sample_config, ["@Trader", "@Keep"])
    monkeypatch.setattr(pipeline, "_merge_xml_sync", lambda ctx: None)
    rebuilt: list[object] = []
    monkeypatch.setattr(mods_cmd, "_regenerate_unit", rebuilt.append)

    _activate_vanilla_map(
        None, sample_config, "Livonia", "dayzOffline.enoch",
        choices=SwitchChoices(disable_mods=("@Trader",)),
    )

    assert modlist.disabled(sample_config) == {"@Trader"}
    assert rebuilt == [sample_config]


def test_the_cli_leaves_the_unit_alone_when_no_mod_changed(sample_config, monkeypatch):
    from app.commands import mods as mods_cmd
    from app.commands.maps import _activate_vanilla_map
    from app.ops import pipeline

    write_test_mods(sample_config, ["@Trader"])
    monkeypatch.setattr(pipeline, "_merge_xml_sync", lambda ctx: None)
    rebuilt: list[object] = []
    monkeypatch.setattr(mods_cmd, "_regenerate_unit", rebuilt.append)

    _activate_vanilla_map(None, sample_config, "Livonia", "dayzOffline.enoch")

    assert rebuilt == []


def test_the_cli_doc_names_every_switch_flag():
    from tests.conftest import site_doc

    text = site_doc("cli.md").read_text(encoding="utf-8")
    section = text[text.index("zsm maps switch"):]
    for flag in (
        "--restore-previous-config", "--apply-recommended", "--disable-mod",
        "--leave-manual-file", "--apply-mod-xml", "--dry-run", "--service-update",
    ):
        assert flag in section, flag


# ── which mods a switch may remove ──────────────────────────────────
#
# The bug these pin, measured in 2026 Q3 on the test container: `zsm maps
# switch Livonia` against a 21-mod Expansion install removed `@CF` and
# `@Dabs Framework` — from mods.txt, from the server directory, and their
# bikeys from keys/ — because Deer Isle lists `@CF` under `deps` and
# TakistanPlus lists `@Dabs Framework`. The resulting server could not load.


def _mods_config(tmp_path, names):
    from app.shared.config import DayzConfig

    config = DayzConfig()
    config.server_dir = str(tmp_path)
    write_test_mods(config, names)
    return config


def test_a_framework_another_mod_needs_is_not_a_map_mod(tmp_path):
    """The reported case. `@CF` is Deer Isle's dep and every Expansion
    module's; leaving a map is no reason to delete it."""
    from app.maps.map_ops import partition_old_map_mods

    config = _mods_config(tmp_path, ["@CF", "@Dabs Framework", "@DayZ-Expansion-Core"])
    removable, kept = partition_old_map_mods(config, None)
    assert removable == []
    # No map mod leaves, so no map brought them: a switch from Chernarus said
    # it left @CF behind (browser test 2026-11-17, F3).
    assert kept == []


def test_the_map_itself_still_goes(tmp_path):
    from app.maps.map_ops import partition_old_map_mods

    config = _mods_config(tmp_path, ["@DeerIsle", "@CF", "@DayZ-Expansion-Core"])
    removable, kept = partition_old_map_mods(config, None)
    assert removable == ["@DeerIsle"]
    assert kept == ["@CF"]


def test_a_dep_nothing_else_wants_is_removed(tmp_path):
    """With only the map and its dep installed there is nothing left that could
    need `@Namalsk Survival`, so the switch really does clean up."""
    from app.maps.map_ops import partition_old_map_mods

    config = _mods_config(tmp_path, ["@Namalsk Island", "@Namalsk Survival"])
    removable, kept = partition_old_map_mods(config, None)
    assert removable == ["@Namalsk Island", "@Namalsk Survival"]
    assert kept == []


def test_the_new_map_keeps_the_dep_it_shares(tmp_path):
    """Deer Isle -> TakistanPlus: `@Dabs Framework` is the new map's own dep."""
    from app.maps.map_ops import partition_old_map_mods
    from app.shared.seed_data import WORKSHOP_MAPS

    config = _mods_config(tmp_path, ["@DeerIsle", "@CF", "@Dabs Framework"])
    removable, kept = partition_old_map_mods(config, WORKSHOP_MAPS["TakistanPlus"])
    assert "@DeerIsle" in removable
    assert "@Dabs Framework" not in removable and "@Dabs Framework" not in kept
    assert "@CF" in removable  # nothing else installed wants it


def test_mods_txt_loses_exactly_what_the_partition_removes(tmp_path):
    """The line that leaves mods.txt and the directory that leaves the server
    dir have to be decided by one rule, or they drift apart."""
    from app.maps.map_ops import partition_old_map_mods, update_mods_list_for_map

    names = ["@CF", "@Dabs Framework", "@DeerIsle", "@DayZ-Expansion-Core"]
    config = _mods_config(tmp_path, names)
    removable, _ = partition_old_map_mods(config, None)
    update_mods_list_for_map(config, None)

    after = installed_names(config)
    assert after == [n for n in names if n not in removable]
    assert "@CF" in after and "@Dabs Framework" in after
