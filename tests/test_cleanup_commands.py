"""Tests for `zsm cleanup` flag-driven subcommands."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from app.commands.cleanup import cleanup_group


def _ni_obj():
    return {"non_interactive": True}


def test_remove_mods_requires_args_or_all():
    runner = CliRunner()
    with patch("app.commands.cleanup.DayzConfig.load"):
        result = runner.invoke(cleanup_group, ["remove-mods"], obj=_ni_obj())
    assert result.exit_code != 0
    assert "workshop IDs" in result.output or "--all" in result.output


def test_remove_mods_filters_by_workshop_id():
    """remove-mods 12345 should only target the matching workshop dir."""
    fake_mods = [
        SimpleNamespace(workshop_id="123", path="/tmp/123", mod_name="@A"),
        SimpleNamespace(workshop_id="456", path="/tmp/456", mod_name="@B"),
    ]
    runner = CliRunner()
    with patch("app.commands.cleanup.list_workshop_mods", return_value=fake_mods), \
         patch("app.commands.cleanup.calculate_dirs_size", return_value=0), \
         patch("app.commands.cleanup.shutil.rmtree") as mock_rm, \
         patch("app.commands.cleanup.DayzConfig.load"):
        result = runner.invoke(cleanup_group, ["remove-mods", "456"], obj=_ni_obj())
    assert result.exit_code == 0, result.output
    mock_rm.assert_called_once_with("/tmp/456")


def test_remove_mods_all_removes_every_workshop_dir():
    fake_mods = [
        SimpleNamespace(workshop_id="123", path="/tmp/123", mod_name="@A"),
        SimpleNamespace(workshop_id="456", path="/tmp/456", mod_name="@B"),
    ]
    runner = CliRunner()
    with patch("app.commands.cleanup.list_workshop_mods", return_value=fake_mods), \
         patch("app.commands.cleanup.calculate_dirs_size", return_value=0), \
         patch("app.commands.cleanup.shutil.rmtree") as mock_rm, \
         patch("app.commands.cleanup.DayzConfig.load"):
        result = runner.invoke(cleanup_group, ["remove-mods", "--all"], obj=_ni_obj())
    assert result.exit_code == 0, result.output
    assert mock_rm.call_count == 2


def test_clear_logs_no_files_short_circuits():
    info = SimpleNamespace(profiles_dir="/tmp/profiles", files=[], total_size=0)
    runner = CliRunner()
    with patch("app.commands.cleanup.find_log_files", return_value=info), \
         patch("app.commands.cleanup.DayzConfig.load"):
        result = runner.invoke(cleanup_group, ["clear-logs"], obj=_ni_obj())
    assert result.exit_code == 0, result.output
    assert "No log files found" in result.output


def test_migrate_players_rejects_unknown_missions():
    runner = CliRunner()
    with patch("app.commands.cleanup.get_available_missions", return_value=["m1"], create=True), \
         patch(
             "app.parsers.servercfg.get_available_missions",
             return_value=["m1", "m2"],
         ), \
         patch("app.commands.cleanup.DayzConfig.load"):
        result = runner.invoke(
            cleanup_group,
            ["migrate-players", "--from", "m1", "--to", "nope"],
            obj=_ni_obj(),
        )
    assert result.exit_code != 0
    assert "not a known mpmission" in result.output


def test_migrate_players_rejects_same_mission():
    runner = CliRunner()
    with patch(
        "app.parsers.servercfg.get_available_missions", return_value=["m1", "m2"],
    ), patch("app.commands.cleanup.DayzConfig.load"):
        result = runner.invoke(
            cleanup_group,
            ["migrate-players", "--from", "m1", "--to", "m1"],
            obj=_ni_obj(),
        )
    assert result.exit_code != 0
    assert "must differ" in result.output


def test_migrate_players_refuses_while_the_server_runs(sample_config, monkeypatch):
    """The web route refused, the CLI copied: the running server keeps
    players.db open and writes it back over the copy."""
    copies: list = []
    monkeypatch.setattr("app.commands.service.is_server_running", lambda config=None: True)
    monkeypatch.setattr("app.commands.service.IS_WINDOWS", False)
    monkeypatch.setattr("app.commands.cleanup.copy_players_db", lambda *a: copies.append(a))
    runner = CliRunner()
    with patch(
        "app.parsers.servercfg.get_available_missions", return_value=["m1", "m2"],
    ), patch("app.commands.cleanup.DayzConfig.load", return_value=sample_config):
        result = runner.invoke(
            cleanup_group,
            ["migrate-players", "--from", "m1", "--to", "m2", "--overwrite"],
            obj=_ni_obj(),
        )
    assert result.exit_code == 1
    assert "currently running" in result.output
    assert copies == []


@pytest.mark.parametrize("args", [["reset-mission", "--mission", "dayzOffline.chernarusplus"], ["reinstall-server"]])
def test_reset_and_reinstall_refuse_while_the_server_runs_on_linux(sample_config, monkeypatch, args):
    """Both checked for a running server on Windows only, so on Linux they
    replaced the files under a live DayZServer."""
    touched: list = []
    monkeypatch.setattr("app.commands.service.is_server_running", lambda config=None: True)
    monkeypatch.setattr("app.commands.service.IS_WINDOWS", False)
    monkeypatch.setattr("app.commands.cleanup.IS_WINDOWS", False)
    monkeypatch.setattr("app.commands.cleanup.list_resettable_missions", lambda c: touched.append("list") or [])
    monkeypatch.setattr("app.commands.cleanup.rmtree_mount_safe", lambda path: touched.append("rm"))
    runner = CliRunner()
    with patch("app.commands.cleanup.DayzConfig.load", return_value=sample_config):
        result = runner.invoke(cleanup_group, args, obj=_ni_obj())
    assert result.exit_code == 1
    assert "currently running" in result.output
    assert touched == []


def test_reset_mission_rejects_unknown_template():
    available = [
        SimpleNamespace(
            template="dayzOffline.chernarusplus",
            path="/tmp/m",
            map_name="Chernarus",
            source_type="vanilla",
            zip_url=None,
            mission_path=None,
            has_storage=False,
        ),
    ]
    runner = CliRunner()
    with patch("app.commands.cleanup.list_resettable_missions", return_value=available), \
         patch("app.commands.cleanup.IS_WINDOWS", False), \
         patch("app.commands.service.is_server_running", return_value=False), \
         patch("app.commands.cleanup.DayzConfig.load"):
        result = runner.invoke(
            cleanup_group,
            ["reset-mission", "--mission", "nope"],
            obj=_ni_obj(),
        )
    assert result.exit_code != 0
    assert "not resettable" in result.output


def test_reset_mission_reports_a_missing_mission_folder_cleanly():
    """The archive lacks the mission folder: an error line, not a traceback."""
    available = [
        SimpleNamespace(
            template="empty.banov",
            path="/tmp/m",
            map_name="Banov",
            source_type="zip",
            zip_url="https://x/repo.zip",
            mission_path="empty.banov",
            has_storage=False,
        ),
    ]
    missing = ValueError("Mission folder not found in the archive: empty.banov")
    runner = CliRunner()
    with patch("app.commands.cleanup.list_resettable_missions", return_value=available), \
         patch("app.commands.cleanup.reset_mission_zip", side_effect=missing), \
         patch("app.commands.cleanup.IS_WINDOWS", False), \
         patch("app.commands.service.is_server_running", return_value=False), \
         patch("app.commands.cleanup.DayzConfig.load"):
        result = runner.invoke(
            cleanup_group,
            ["reset-mission", "--mission", "empty.banov"],
            obj=_ni_obj(),
        )
    assert result.exit_code != 0
    assert "Mission folder not found" in result.output
    assert isinstance(result.exception, SystemExit)


def test_clear_xml_backups_no_files_short_circuits():
    info = SimpleNamespace(
        mission_dir=SimpleNamespace(name="dayzOffline.chernarusplus"),
        files=[],
        total_size=0,
    )
    runner = CliRunner()
    with patch("app.commands.cleanup.find_xml_backups", return_value=info), \
         patch("app.commands.cleanup.DayzConfig.load"):
        result = runner.invoke(cleanup_group, ["clear-xml-backups"], obj=_ni_obj())
    assert result.exit_code == 0, result.output
    assert "No XML backup files" in result.output


# ---------------------------------------------------------------------------
# wipe-server: never under a running server
# ---------------------------------------------------------------------------


def _wipe_service(monkeypatch, *, running=True, stop_ok=True, start_ok=True):
    """Fake the server and record what the wipe did to it, in order."""
    from app.server import service_control

    calls: list[str] = []
    state = {"running": running}

    def _is_running(config=None):
        return state["running"]

    def _stop(config):
        calls.append("stop")
        if stop_ok:
            state["running"] = False
        return service_control.ServiceResult(stop_ok, "" if stop_ok else "boom")

    def _start(config, anyway=False):
        assert anyway, "an automatic start must not stop at the runtime check"
        calls.append("start")
        state["running"] = start_ok
        return service_control.ServiceResult(start_ok, "" if start_ok else "no unit")

    monkeypatch.setattr(service_control, "is_server_running", _is_running)
    monkeypatch.setattr(service_control, "stop_server", _stop)
    monkeypatch.setattr(service_control, "start_server", _start)
    monkeypatch.setattr("app.commands.service.is_server_running", _is_running)
    monkeypatch.setattr(
        "app.commands.cleanup.find_storage",
        lambda config: SimpleNamespace(path=SimpleNamespace(name="storage_1"), mission="m"),
    )
    monkeypatch.setattr("app.commands.cleanup.create_backup", lambda *a, **k: None)
    monkeypatch.setattr("app.monitoring.discord_hooks.notify_wipe", lambda *a, **k: None)
    return calls


def _invoke_wipe(sample_config, calls, *args, rm_error=None):
    def _rm(path):
        calls.append("wipe")
        if rm_error:
            raise rm_error

    runner = CliRunner()
    with patch("app.commands.cleanup.shutil.rmtree", side_effect=_rm), \
         patch("app.commands.cleanup.IS_WINDOWS", False), \
         patch("app.commands.cleanup.DayzConfig.load", return_value=sample_config):
        return runner.invoke(cleanup_group, ["wipe-server", *args], obj=_ni_obj())


def test_a_manual_wipe_refuses_while_the_server_runs_on_linux(sample_config, monkeypatch):
    """A live DayZServer writes its in-memory world back over a deleted storage folder."""
    calls = _wipe_service(monkeypatch, running=True)
    result = _invoke_wipe(sample_config, calls)
    assert result.exit_code != 0
    assert calls == []


def test_a_manual_wipe_of_a_stopped_server_runs(sample_config, monkeypatch):
    calls = _wipe_service(monkeypatch, running=False)
    result = _invoke_wipe(sample_config, calls)
    assert result.exit_code == 0, result.output
    assert calls == ["wipe"]


def test_the_scheduled_wipe_stops_wipes_and_starts_the_server(sample_config, monkeypatch):
    calls = _wipe_service(monkeypatch, running=True)
    result = _invoke_wipe(sample_config, calls, "--restart")
    assert result.exit_code == 0, result.output
    assert calls == ["stop", "wipe", "start"]


def test_the_scheduled_wipe_starts_the_server_when_the_wipe_fails(sample_config, monkeypatch):
    calls = _wipe_service(monkeypatch, running=True)
    result = _invoke_wipe(sample_config, calls, "--restart", rm_error=OSError("busy"))
    assert result.exit_code != 0
    assert calls == ["stop", "wipe", "start"]


def test_the_scheduled_wipe_leaves_a_stopped_server_stopped(sample_config, monkeypatch):
    calls = _wipe_service(monkeypatch, running=False)
    result = _invoke_wipe(sample_config, calls, "--restart")
    assert result.exit_code == 0, result.output
    assert calls == ["wipe"]


def test_the_scheduled_wipe_does_not_wipe_when_the_stop_fails(sample_config, monkeypatch):
    calls = _wipe_service(monkeypatch, running=True, stop_ok=False)
    result = _invoke_wipe(sample_config, calls, "--restart")
    assert result.exit_code != 0
    assert calls == ["stop"]


def test_the_scheduled_wipe_reports_a_server_that_does_not_come_back(sample_config, monkeypatch):
    calls = _wipe_service(monkeypatch, running=True, start_ok=False)
    result = _invoke_wipe(sample_config, calls, "--restart")
    assert result.exit_code != 0
    assert calls == ["stop", "wipe", "start"]
