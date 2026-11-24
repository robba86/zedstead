"""A Steam username that never passed a setter must not reach SteamCMD.

The setters refuse a username SteamCMD would not take as one argument (one
starting with + is read as its next command), but a value can arrive without
them: the Docker env var, a restored config.json, a hand edit. So every
builder that puts the configured username after +login checks it too, and
each caller reports the refusal the way it already reports one.
"""
from __future__ import annotations

import subprocess

import pytest
from click.testing import CliRunner

from app.shared import steam
from tests.test_server_branch import _run_job, _vanilla_mission, _web_app  # the same harness

BAD = ["+quit", "-bob", "two words", "bob\nx"]


def _refuse_spawn(*_a, **_k):
    raise AssertionError("SteamCMD must not run")


@pytest.fixture()
def bad_config(sample_config, monkeypatch):
    sample_config.steam_username = "+quit"
    steamcmd = sample_config.steamcmd_path
    steamcmd.parent.mkdir(parents=True, exist_ok=True)
    steamcmd.write_text("")

    def guard(real):
        def spawn(cmd, *a, **k):
            if isinstance(cmd, (list, tuple)) and cmd and str(cmd[0]) == str(steamcmd):
                _refuse_spawn()
            return real(cmd, *a, **k)
        return spawn

    monkeypatch.setattr(subprocess, "run", guard(subprocess.run))
    monkeypatch.setattr(subprocess, "Popen", guard(subprocess.Popen))
    monkeypatch.setattr(steam, "run_passthrough", _refuse_spawn)
    return sample_config


# --- the builders ---------------------------------------------------------------


BUILDERS = {
    "server update": lambda c: steam.build_server_update_cmd(c),
    "workshop download": lambda c: steam.build_mod_download_cmd(c, "1559212036"),
    "interactive login": lambda c: steam.steam_login(c),
}


@pytest.mark.parametrize("builder", BUILDERS)
@pytest.mark.parametrize("value", BAD)
def test_each_builder_refuses_with_the_setters_message(bad_config, builder, value):
    bad_config.steam_username = value
    with pytest.raises(steam.SteamUsernameRefused) as exc:
        BUILDERS[builder](bad_config)
    assert str(exc.value) == steam.steam_username_problem(value)


@pytest.mark.parametrize("value", BAD)
def test_the_login_checks_answer_no_without_running_steamcmd(bad_config, value):
    bad_config.steam_username = value
    assert steam.check_steam_login(bad_config) is False
    assert steam.probe_login_failure(bad_config) == steam.LoginDiagnosis()


def test_empty_and_anonymous_keep_their_own_paths(sample_config):
    sample_config.steam_username = ""
    assert steam.steamcmd_username(sample_config) == ""
    # SteamCMD's own "No subscription" explains anonymous where it happens.
    sample_config.steam_username = "anonymous"
    cmd = steam.build_mod_download_cmd(sample_config, "1559212036")
    assert cmd[cmd.index("+login") + 1] == "anonymous"


def test_the_docker_env_var_is_stored_unchecked_and_refused_at_use(sample_config):
    from app.shared.container import apply_env_overrides

    assert apply_env_overrides(sample_config, {"STEAM_USERNAME": "+quit"}) == ["steam_username"]
    assert sample_config.steam_username == "+quit"
    with pytest.raises(steam.SteamUsernameRefused):
        steam.build_server_update_cmd(sample_config)


# --- the callers report it, and nothing is deleted first ---------------------------


def test_a_workshop_download_says_why_and_stops(bad_config):
    lines: list[str] = []
    with pytest.raises(steam.SteamUsernameRefused):
        steam.download_mod(bad_config, "1559212036", "@CF", log=lines.append)
    assert any("@CF: The Steam username cannot start with '+'" in line for line in lines)


async def test_a_job_download_says_why_and_returns_false(bad_config):
    class Runner:
        lines: list[str] = []

        async def run_command(self, cmd):
            raise AssertionError("SteamCMD must not run")

        def write(self, text):
            self.lines.append(text)

    runner = Runner()
    assert await steam.download_mod_async(runner, bad_config, "1559212036", "@CF") is False
    assert any("@CF: The Steam username cannot start with '+'" in line for line in runner.lines)


def test_a_server_update_refuses_before_the_mission_is_touched(bad_config, monkeypatch):
    from app.ops import mission_stock

    monkeypatch.setattr(mission_stock, "before_download", _refuse_spawn)
    with pytest.raises(steam.SteamUsernameRefused):
        steam.update_dayz_server(bad_config, log=lambda s: None)


async def test_the_pipeline_step_fails_with_the_message(bad_config):
    from app.ops.pipeline import PipelineContext, _run_update_server

    class Runner:
        async def run_command(self, cmd):
            raise AssertionError("SteamCMD must not run")

    ctx = PipelineContext(config=bad_config, emit=lambda s: None, runner=Runner(), should_run=lambda k: True)
    with pytest.raises(steam.SteamUsernameRefused, match="cannot start with"):
        await _run_update_server(ctx)


def _assert_cli_message(result):
    assert result.exit_code == 1, result.output
    assert "The Steam username cannot start with '+'" in result.output
    assert not isinstance(result.exception, steam.SteamUsernameRefused)  # no traceback


def test_update_and_copy_reports_it_without_a_traceback(bad_config, monkeypatch):
    from app.commands import run

    monkeypatch.setattr(run, "load_context", lambda ctx: (bad_config, True))
    result = CliRunner().invoke(run.run, ["update-and-copy"], obj={"non_interactive": True})
    _assert_cli_message(result)


def test_cli_reinstall_refuses_before_deleting_the_server(bad_config, monkeypatch):
    from app.commands import cleanup

    monkeypatch.setattr(cleanup, "IS_WINDOWS", False)
    monkeypatch.setattr("app.commands.service.is_server_running", lambda config=None: False)
    monkeypatch.setattr(cleanup.DayzConfig, "load", classmethod(lambda cls, *a, **k: bad_config))
    # sample_config's server dir holds its config dir, which the reinstall guard
    # refuses first (tests/test_cleanup_ops.py); this test is about the username.
    monkeypatch.setattr("app.ops.cleanup_ops.reinstall_refusal", lambda config: None)
    monkeypatch.setattr(cleanup, "rmtree_mount_safe", _refuse_spawn)
    result = CliRunner().invoke(cleanup.cleanup_group, ["reinstall-server"], obj={"non_interactive": True})
    _assert_cli_message(result)
    assert bad_config.server_dir_path.is_dir()


def test_cli_reset_mission_refuses_before_clearing(bad_config, monkeypatch, tmp_path):
    from app.commands import cleanup

    mission = _vanilla_mission(tmp_path)
    monkeypatch.setattr(cleanup, "IS_WINDOWS", False)
    monkeypatch.setattr("app.commands.service.is_server_running", lambda config=None: False)
    monkeypatch.setattr(cleanup.DayzConfig, "load", classmethod(lambda cls, *a, **k: bad_config))
    monkeypatch.setattr(cleanup, "list_resettable_missions", lambda c: [mission])
    monkeypatch.setattr(cleanup, "clear_mission_dir", _refuse_spawn)
    result = CliRunner().invoke(cleanup.cleanup_group, ["reset-mission", "--mission", mission.template],
                                obj={"non_interactive": True})
    _assert_cli_message(result)


def test_a_map_switch_reports_it_without_a_traceback(bad_config):
    import click

    from app.commands.maps import _download_map_mods

    with pytest.raises(click.ClickException, match="cannot start with"):
        _download_map_mods(bad_config, {"mods": {"@DeerIsle": "1602372402"}, "deps": {}})


def test_web_reinstall_fails_with_the_message_and_keeps_the_server(bad_config, monkeypatch):
    seen: list = []
    monkeypatch.setattr("app.ops.cleanup_ops.reinstall_refusal", lambda config: None)
    monkeypatch.setattr("app.ops.cleanup_ops.rmtree_mount_safe", _refuse_spawn)
    job = _run_job(_web_app(bad_config, monkeypatch, seen), "/api/maintenance/reinstall-server",
                   {"confirm": True})
    assert job["status"] == "failed", job
    assert "The Steam username cannot start with '+'" in job["error"]
    assert seen == []
    assert bad_config.server_dir_path.is_dir()


def test_web_reset_mission_fails_with_the_message(bad_config, monkeypatch, tmp_path):
    seen: list = []
    mission = _vanilla_mission(tmp_path)
    monkeypatch.setattr("app.ops.cleanup_ops.list_resettable_missions", lambda c: [mission])
    monkeypatch.setattr("app.ops.cleanup_ops.clear_mission_dir", _refuse_spawn)
    job = _run_job(_web_app(bad_config, monkeypatch, seen), "/api/cleanup/mission-reset",
                   {"confirm": True, "mission": mission.template})
    assert job["status"] == "failed", job
    assert "The Steam username cannot start with '+'" in job["error"]
    assert seen == []
