"""Tests for `zsm presets` flag-driven subcommands."""

from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from app.commands.presets import presets_group
from app.presets.preset_engine import ChangeEntry, PresetPreview


def _preview(n: int, warnings: list[str] | None = None) -> PresetPreview:
    return PresetPreview(
        changes=[ChangeEntry("globals.xml", f"Key{i}", "1", "2") for i in range(n)],
        warnings=warnings or [],
    )


def test_list_shows_builtins():
    runner = CliRunner()
    with patch("app.commands.presets.list_custom_presets", return_value=[]):
        result = runner.invoke(presets_group, ["list"])
    assert result.exit_code == 0, result.output
    assert "Built-in presets" in result.output
    assert "beginner_friendly" in result.output


def test_apply_unknown_preset_errors():
    runner = CliRunner()
    with patch("app.commands.presets.list_custom_presets", return_value=[]):
        result = runner.invoke(presets_group, ["apply", "Atlantis"])
    assert result.exit_code != 0
    assert "Unknown preset" in result.output


def test_apply_dry_run_does_not_call_apply_preset():
    fake_preview = _preview(2)
    runner = CliRunner()
    with patch("app.commands.presets.preview_preset", return_value=fake_preview), \
         patch("app.commands.presets.apply_preset") as mock_apply, \
         patch("app.commands.presets._resolve_mission_dir", return_value="/tmp/mission"), \
         patch("app.commands.presets.DayzConfig.load"):
        result = runner.invoke(presets_group, ["apply", "beginner_friendly", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "2 change(s)" in result.output
    mock_apply.assert_not_called()


def test_apply_writes_when_confirmed():
    fake_preview = _preview(1)
    runner = CliRunner()
    with patch("app.commands.presets.preview_preset", return_value=fake_preview), \
         patch("app.commands.presets.apply_preset", return_value=["/tmp/serverDZ.cfg"]) as mock_apply, \
         patch("app.commands.presets._resolve_mission_dir", return_value="/tmp/mission"), \
         patch("app.commands.presets.DayzConfig.load"):
        result = runner.invoke(
            presets_group, ["apply", "beginner_friendly"],
            obj={"non_interactive": True},
        )
    assert result.exit_code == 0, result.output
    assert "Applied" in result.output
    mock_apply.assert_called_once()


def test_apply_no_changes_short_circuits():
    fake_preview = _preview(0)
    runner = CliRunner()
    with patch("app.commands.presets.preview_preset", return_value=fake_preview), \
         patch("app.commands.presets.apply_preset") as mock_apply, \
         patch("app.commands.presets._resolve_mission_dir", return_value="/tmp/mission"), \
         patch("app.commands.presets.DayzConfig.load"):
        result = runner.invoke(
            presets_group, ["apply", "beginner_friendly"],
            obj={"non_interactive": True},
        )
    assert result.exit_code == 0, result.output
    assert "Nothing to do" in result.output
    mock_apply.assert_not_called()


def test_delete_unknown_errors():
    runner = CliRunner()
    with patch("app.commands.presets.list_custom_presets", return_value=[]):
        result = runner.invoke(
            presets_group, ["delete", "missing"],
            obj={"non_interactive": True},
        )
    assert result.exit_code != 0
    assert "No custom preset" in result.output


def test_delete_calls_storage_delete():
    runner = CliRunner()
    with patch(
        "app.commands.presets.list_custom_presets",
        return_value=[("my_preset", "/tmp/my_preset.json")],
    ), patch("app.commands.presets.delete_custom_preset") as mock_delete:
        result = runner.invoke(
            presets_group, ["delete", "my_preset"],
            obj={"non_interactive": True},
        )
    assert result.exit_code == 0, result.output
    mock_delete.assert_called_once_with("/tmp/my_preset.json")


def test_save_calls_storage_save():
    runner = CliRunner()
    with patch("app.commands.presets.snapshot_current_config") as mock_snap, \
         patch("app.commands.presets.save_custom_preset", return_value="/tmp/x.json") as mock_save, \
         patch("app.commands.presets.DayzConfig.load") as mock_load:
        result = runner.invoke(presets_group, ["save", "my_preset"])
    assert result.exit_code == 0, result.output
    mock_snap.assert_called_once_with(mock_load.return_value, "my_preset")
    mock_save.assert_called_once()


def test_the_preview_prints_rows_and_warnings_readably():
    """The CLI printed ChangeEntry(...) reprs and no warnings, so the note
    about presets applied by an older zsm (plans/50 C) never showed."""
    fake_preview = _preview(1, warnings=["Use Configs [Types] first."])
    runner = CliRunner()
    with patch("app.commands.presets.preview_preset", return_value=fake_preview), \
         patch("app.commands.presets._resolve_mission_dir", return_value="/tmp/mission"), \
         patch("app.commands.presets.DayzConfig.load"):
        result = runner.invoke(presets_group, ["show", "beginner_friendly"])
    assert result.exit_code == 0, result.output
    assert "globals.xml: Key0 1 → 2" in result.output
    assert "ChangeEntry(" not in result.output
    assert "Use Configs [Types] first." in result.output  # printed verbatim, not as markup


def test_save_exits_1_on_a_name_clash():
    from app.presets.preset_storage import PresetNameClash

    runner = CliRunner()
    with patch("app.commands.presets.snapshot_current_config"), \
         patch("app.commands.presets.save_custom_preset", side_effect=PresetNameClash("Zz Test")), \
         patch("app.commands.presets.DayzConfig.load"):
        result = runner.invoke(presets_group, ["save", "zz test!"])
    assert result.exit_code == 1
    assert "Zz Test" in result.output
