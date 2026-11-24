"""Stable vs Experimental server (plans/66): which Steam app an instance installs."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from app.shared import steam

PASSWORD = "testpass123"


def _app_id(cmd: list[str]) -> str:
    return cmd[cmd.index("+app_update") + 1]


# --- the builder ---------------------------------------------------------------


def test_the_builder_installs_stable_by_default(sample_config):
    assert _app_id(steam.build_server_update_cmd(sample_config)) == "223350"


def test_the_builder_installs_the_experimental_app(sample_config):
    sample_config.server_branch = "experimental"
    assert _app_id(steam.build_server_update_cmd(sample_config)) == "1042420"


@pytest.mark.parametrize("value", ["", "beta", "experimental_public", None])
def test_an_unknown_branch_reads_as_stable(sample_config, value):
    sample_config.server_branch = value
    assert steam.server_branch(sample_config) == "stable"
    assert steam.server_app_id(sample_config) == "223350"


def test_the_branch_is_read_without_regard_to_case_or_spaces(sample_config):
    sample_config.server_branch = " Experimental "
    assert steam.server_branch(sample_config) == "experimental"


def test_workshop_downloads_are_the_same_on_experimental(sample_config):
    sample_config.server_branch = "experimental"
    cmd = steam.build_mod_download_cmd(sample_config, "1559212036")
    assert cmd[cmd.index("+workshop_download_item") + 1] == "221100"


# --- every caller that downloads the server ------------------------------------
# The tripwire: each one must reach SteamCMD with the instance's app id, so a new
# caller that hardcodes 223350 shows up here, not on an admin's 1.30 server.


async def _pipeline_step(config, monkeypatch) -> list[str]:
    from app.ops.pipeline import PipelineContext, _run_update_server

    seen: list[list[str]] = []

    class Runner:
        async def run_command(self, cmd):
            seen.append(cmd)
            return 0

    ctx = PipelineContext(config=config, emit=lambda s: None, runner=Runner(), should_run=lambda k: True)
    await _run_update_server(ctx)
    return seen[0]


async def _update_dayz_server(config, monkeypatch) -> list[str]:
    seen: list[list[str]] = []
    monkeypatch.setattr(steam, "run_passthrough", lambda cmd: seen.append(cmd) or (0, ""))
    steam.update_dayz_server(config, log=lambda s: None)
    return seen[0]


def _vanilla_mission(tmp_path):
    path = tmp_path / "dayzOffline.chernarusplus"
    path.mkdir(exist_ok=True)
    return SimpleNamespace(template=path.name, path=path, map_name="Chernarus", source_type="vanilla",
                           zip_url=None, mission_path=None, has_storage=False)


async def _cli_reset(config, monkeypatch, tmp_path) -> list[str]:
    from app.commands import cleanup

    seen: list[list[str]] = []
    mission = _vanilla_mission(tmp_path)
    monkeypatch.setattr(cleanup, "IS_WINDOWS", False)
    monkeypatch.setattr("app.commands.service.is_server_running", lambda config=None: False)
    monkeypatch.setattr(cleanup.DayzConfig, "load", classmethod(lambda cls, *a, **k: config))
    monkeypatch.setattr(cleanup, "list_resettable_missions", lambda c: [mission])
    monkeypatch.setattr(cleanup, "clear_mission_dir", lambda path, keep: None)
    monkeypatch.setattr("subprocess.run", lambda cmd, check=False: seen.append(cmd) or SimpleNamespace(returncode=0))
    result = CliRunner().invoke(cleanup.cleanup_group, ["reset-mission", "--mission", mission.template],
                                obj={"non_interactive": True})
    assert result.exit_code == 0, result.output
    return seen[0]


def _web_app(config, monkeypatch, seen):
    from app.web import server
    from app.web.auth import hash_password
    from app.web.jobs import JobRunner

    async def _record(self, cmd):
        seen.append(cmd)
        return 0

    monkeypatch.setattr(JobRunner, "run_command", _record)
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)
    config.web_password_hash = hash_password(PASSWORD)
    return server.create_app(config)


def _run_job(app, url, body) -> dict:
    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        resp = client.post(url, json=body)
        assert resp.status_code == 200, resp.text
        job_id = resp.json()["job_id"]
        deadline = time.monotonic() + 3.0
        while True:
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] not in ("pending", "running"):
                return job
            assert time.monotonic() < deadline, job
            time.sleep(0.02)


async def _web_reset(config, monkeypatch, tmp_path) -> list[str]:
    seen: list[list[str]] = []
    mission = _vanilla_mission(tmp_path)
    monkeypatch.setattr("app.ops.cleanup_ops.list_resettable_missions", lambda c: [mission])
    monkeypatch.setattr("app.ops.cleanup_ops.clear_mission_dir", lambda path, keep: None)
    job = _run_job(_web_app(config, monkeypatch, seen), "/api/cleanup/mission-reset",
                   {"confirm": True, "mission": mission.template})
    assert job["status"] == "success", job
    return seen[0]


async def _web_reinstall(config, monkeypatch, tmp_path) -> list[str]:
    seen: list[list[str]] = []
    # sample_config's server dir holds its config dir, which the guard refuses
    # (tests/test_cleanup_ops.py); this test is about the download's branch.
    monkeypatch.setattr("app.ops.cleanup_ops.reinstall_refusal", lambda config: None)
    monkeypatch.setattr("app.ops.cleanup_ops.rmtree_mount_safe", lambda path: None)
    monkeypatch.setattr("app.modmgr.modlist.clear", lambda config: None)
    monkeypatch.setattr("app.shared.container.sync_battleye_cfg", lambda config, notes=None: None)
    monkeypatch.setattr("app.shared.container.sync_server_cfg", lambda config, notes=None: {})

    async def _fake_service(runner, config, **kw):
        return True

    monkeypatch.setattr("app.server.service_ops.generate_service_files_async", _fake_service)
    job = _run_job(_web_app(config, monkeypatch, seen), "/api/maintenance/reinstall-server", {"confirm": True})
    assert job["status"] == "success", job
    return seen[0]


CALLERS = {
    "pipeline update step": lambda c, m, t: _pipeline_step(c, m),
    "update_dayz_server": lambda c, m, t: _update_dayz_server(c, m),
    "CLI reset-mission": _cli_reset,
    "web reset-mission": _web_reset,
    "web reinstall-server": _web_reinstall,
}


@pytest.mark.parametrize("caller", CALLERS)
async def test_every_server_download_uses_the_instance_branch(caller, sample_config, monkeypatch, tmp_path):
    sample_config.server_branch = "experimental"
    cmd = await CALLERS[caller](sample_config, monkeypatch, tmp_path)
    assert _app_id(cmd) == "1042420"


# --- what is installed, and refusing the other branch (Step 2) ------------------


def _install(server_dir, *apps: str) -> None:
    steamapps = server_dir / "steamapps"
    steamapps.mkdir(exist_ok=True)
    for app in apps:
        (steamapps / f"appmanifest_{app}.acf").write_text('"AppState"\n{\n}\n')


@pytest.mark.parametrize("apps, expected", [
    ((), None),
    (("223350",), "stable"),
    (("1042420",), "experimental"),
    (("223350", "1042420"), "mixed"),
])
def test_the_installed_branch_comes_from_steamcmds_manifest(tmp_path, apps, expected):
    _install(tmp_path, *apps)
    assert steam.installed_server_branch(tmp_path) == expected


def test_a_directory_with_no_steamapps_has_no_branch(tmp_path):
    assert steam.installed_server_branch(tmp_path / "nowhere") is None


def test_an_empty_directory_installs_either_branch(sample_config):
    sample_config.server_branch = "experimental"
    assert steam.branch_mismatch(sample_config) is None
    assert _app_id(steam.build_server_update_cmd(sample_config)) == "1042420"


def test_the_same_branch_updates_as_before(sample_config):
    _install(sample_config.server_dir_path, "1042420")
    sample_config.server_branch = "experimental"
    assert steam.branch_mismatch(sample_config) is None
    assert _app_id(steam.build_server_update_cmd(sample_config)) == "1042420"


def test_the_builder_refuses_to_overwrite_the_other_branch(sample_config):
    _install(sample_config.server_dir_path, "223350")
    sample_config.server_branch = "experimental"
    sample_config.instance_id = "main"
    with pytest.raises(steam.ServerBranchMismatch) as exc:
        steam.build_server_update_cmd(sample_config)
    message = str(exc.value)
    assert str(sample_config.server_dir_path) in message
    assert "stable" in message and "Experimental" in message
    assert "set server_branch back to stable" in message
    assert message.endswith("--branch experimental")
    assert "zsm instances clone main <name> --server-dir <new dir> --branch experimental" in message
    assert "[" not in message  # markup-free: a job log, a 409 and a Doctor row show it raw


def test_a_directory_holding_both_is_refused_either_way(sample_config):
    _install(sample_config.server_dir_path, "223350", "1042420")
    for branch in ("stable", "experimental"):
        sample_config.server_branch = branch
        with pytest.raises(steam.ServerBranchMismatch, match="reinstall-server"):
            steam.build_server_update_cmd(sample_config)


async def test_the_pipeline_step_fails_with_the_mismatch(sample_config):
    from app.ops.pipeline import PipelineContext, _run_update_server

    _install(sample_config.server_dir_path, "223350")
    sample_config.server_branch = "experimental"

    class Runner:
        async def run_command(self, cmd):
            raise AssertionError("SteamCMD must not run")

    ctx = PipelineContext(config=sample_config, emit=lambda s: None, runner=Runner(), should_run=lambda k: True)
    with pytest.raises(steam.ServerBranchMismatch, match="one directory cannot hold both"):
        await _run_update_server(ctx)


def test_update_and_copy_reports_the_mismatch_without_a_traceback(sample_config, monkeypatch):
    from app.commands import run

    _install(sample_config.server_dir_path, "223350")
    sample_config.server_branch = "experimental"
    monkeypatch.setattr(run, "load_context", lambda ctx: (sample_config, True))
    monkeypatch.setattr(steam, "run_passthrough", lambda cmd: pytest.fail("SteamCMD must not run"))
    result = CliRunner().invoke(run.run, ["update-and-copy"], obj={"non_interactive": True})
    assert result.exit_code == 1
    assert "one directory cannot hold both" in result.output
    assert not isinstance(result.exception, steam.ServerBranchMismatch)


def _mission_with_a_file(sample_config):
    mission = _vanilla_mission(sample_config.server_dir_path / "mpmissions")
    (mission.path / "types.xml").write_text("<types/>")
    return mission


def test_a_refused_cli_reset_leaves_the_mission_alone(sample_config, monkeypatch):
    from app.commands import cleanup

    _install(sample_config.server_dir_path, "223350")
    sample_config.server_branch = "experimental"
    mission = _mission_with_a_file(sample_config)
    monkeypatch.setattr(cleanup, "IS_WINDOWS", False)
    monkeypatch.setattr("app.commands.service.is_server_running", lambda config=None: False)
    monkeypatch.setattr(cleanup.DayzConfig, "load", classmethod(lambda cls, *a, **k: sample_config))
    monkeypatch.setattr(cleanup, "list_resettable_missions", lambda c: [mission])
    monkeypatch.setattr("subprocess.run", lambda *a, **k: pytest.fail("SteamCMD must not run"))
    result = CliRunner().invoke(cleanup.cleanup_group, ["reset-mission", "--mission", mission.template],
                                obj={"non_interactive": True})
    assert result.exit_code == 1
    assert "one directory cannot hold both" in result.output
    assert (mission.path / "types.xml").read_text() == "<types/>"


def test_a_refused_web_reset_is_a_409_and_leaves_the_mission_alone(sample_config, monkeypatch):
    _install(sample_config.server_dir_path, "223350")
    sample_config.server_branch = "experimental"
    mission = _mission_with_a_file(sample_config)
    monkeypatch.setattr("app.ops.cleanup_ops.list_resettable_missions", lambda c: [mission])
    app = _web_app(sample_config, monkeypatch, [])
    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        resp = client.post("/api/cleanup/mission-reset", json={"confirm": True, "mission": mission.template})
    assert resp.status_code == 409
    assert "one directory cannot hold both" in resp.json()["detail"]
    assert (mission.path / "types.xml").read_text() == "<types/>"


def test_a_zip_reset_is_not_held_up_by_the_branch(sample_config, monkeypatch, tmp_path):
    """A community map's mission comes from its own repo, not from SteamCMD."""
    _install(sample_config.server_dir_path, "223350")
    sample_config.server_branch = "experimental"
    mission = SimpleNamespace(template="empty.banov", path=tmp_path, map_name="Banov", source_type="zip",
                              zip_url="https://x/repo.zip", mission_path="empty.banov", has_storage=False)
    monkeypatch.setattr("app.ops.cleanup_ops.list_resettable_missions", lambda c: [mission])
    monkeypatch.setattr("app.ops.cleanup_ops.reset_mission_zip", lambda *a: 3)
    job = _run_job(_web_app(sample_config, monkeypatch, []), "/api/cleanup/mission-reset",
                   {"confirm": True, "mission": mission.template})
    assert job["status"] == "success", job


# --- the Doctor row ------------------------------------------------------------


def _branch_row(config):
    from app.commands.doctor import _run_all_checks

    rows, _ = _run_all_checks(config)
    return next((r for r in rows if r[0] == "Server branch"), None)


@pytest.fixture()
def installed(sample_config):
    (sample_config.server_dir_path / "DayZServer").write_text("")
    (sample_config.server_dir_path / "DayZServer_x64.exe").write_text("")
    return sample_config


def test_doctor_shows_no_branch_row_without_a_server(sample_config):
    assert _branch_row(sample_config) is None


def test_doctor_says_stable(installed):
    _install(installed.server_dir_path, "223350")
    assert _branch_row(installed) == ("Server branch", True, "Stable")


def test_doctor_says_who_can_join_an_experimental_server(installed):
    _install(installed.server_dir_path, "1042420")
    installed.server_branch = "experimental"
    assert _branch_row(installed) == ("Server branch", True, "Experimental (only Experimental clients can join)")


def test_doctor_fails_on_a_mismatch_and_links_the_instances_page(installed):
    from app.ops.doctor_report import _links_for

    _install(installed.server_dir_path, "223350")
    installed.server_branch = "experimental"
    label, ok, detail = _branch_row(installed)
    assert not ok and "one directory cannot hold both" in detail
    links, _ = _links_for(label, ok, detail)
    assert {"label": "Instances", "to": "/instances"} in links
