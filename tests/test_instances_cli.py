"""Tests for `zsm instances` (app/commands/instances.py, plans/44 step 5)."""

from __future__ import annotations

import os

import pytest
from click.testing import CliRunner

from app.__main__ import main
from app.server import instances
from app.shared.config import DayzConfig


def _run(*args: str):
    # The root group pins the instance in the environment; each invocation
    # here stands for a separate `zsm` process.
    os.environ.pop("ZSM_INSTANCE", None)
    return CliRunner().invoke(main, ["--yes", *args])


@pytest.fixture
def main_instance(sample_config, tmp_path):
    instances.save_new_config(DayzConfig(server_dir=str(tmp_path / "srv" / "main"),
                                         steamcmd_dir="/opt/steamcmd"))
    return DayzConfig.for_instance("main")


@pytest.fixture
def stopped(monkeypatch):
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)


def test_add_then_list_shows_both_with_their_ports(main_instance, tmp_path):
    added = _run("instances", "add", "pvp", "--server-dir", str(tmp_path / "pvp"), "--label", "PvP")
    listed = _run("instances", "list")

    assert added.exit_code == 0, added.output
    assert "game 2402, query 27116, RCon 2405" in added.output
    assert "zsm --instance pvp setup" in added.output
    assert listed.exit_code == 0, listed.output
    assert "main (default)" in listed.output
    assert "PvP" in listed.output and "2402" in listed.output


def test_add_explains_a_refusal(main_instance):
    result = _run("instances", "add", "pvp", "--server-dir", main_instance.server_dir)

    assert result.exit_code == 1
    assert "already uses" in result.output


def test_list_names_a_port_conflict(main_instance, tmp_path):
    _run("instances", "add", "pvp", "--server-dir", str(tmp_path / "pvp"))
    pvp = DayzConfig.for_instance("pvp")
    pvp.server_port = 2303  # inside main's 2302-2305
    pvp.save()

    result = _run("instances", "list")

    assert "used by" in result.output and "'main'" in result.output


def test_clone_copies_and_says_what_it_moved(main_instance, tmp_path):
    main_instance.bundle_export_dir = "/data/bundles"
    main_instance.save()

    result = _run("instances", "clone", "main", "pvp", "--server-dir", str(tmp_path / "pvp"))

    assert result.exit_code == 0, result.output
    assert "Cloned 'main' into instance 'pvp'" in result.output
    assert "bundle_export_dir was /data/bundles" in result.output
    assert DayzConfig.for_instance("pvp").steamcmd_dir == "/opt/steamcmd"


def test_clone_of_an_unknown_source_fails(main_instance, tmp_path):
    result = _run("instances", "clone", "nope", "pvp", "--server-dir", str(tmp_path / "pvp"))
    assert result.exit_code == 1
    assert "No instance named 'nope'" in result.output


def test_use_changes_what_a_plain_command_reads(main_instance, tmp_path):
    _run("instances", "add", "pvp", "--server-dir", str(tmp_path / "pvp"))

    used = _run("instances", "use", "pvp")
    read = _run("settings", "get", "server_dir")

    assert used.exit_code == 0, used.output
    assert "restart the app" in used.output
    assert str(tmp_path / "pvp") in read.stdout


def test_remove_keeps_files_unless_purged(main_instance, tmp_path, stopped):
    from app.shared.paths import instance_root

    _run("instances", "add", "pvp", "--server-dir", str(tmp_path / "pvp"))
    _run("instances", "add", "pve", "--server-dir", str(tmp_path / "pve"))

    kept = _run("instances", "remove", "pvp")
    purged = _run("instances", "remove", "pve", "--purge")

    assert kept.exit_code == 0, kept.output
    assert "--purge" in kept.output and instance_root("pvp").is_dir()
    assert purged.exit_code == 0, purged.output
    assert not instance_root("pve").exists()
    assert instances.load_registry().ids() == ["main"]


def test_purge_after_a_web_remove_deletes_the_folder_and_frees_the_name(main_instance, tmp_path, stopped):
    from app.shared.paths import instance_root

    server = tmp_path / "pvp"
    server.mkdir()
    (server / "keep.txt").write_text("server files")
    _run("instances", "add", "pvp", "--server-dir", str(server))
    instances.delete_instance("pvp")  # what the web's Remove calls
    assert instance_root("pvp").is_dir()

    purged = _run("instances", "remove", "pvp", "--purge")

    assert purged.exit_code == 0, purged.output
    assert not instance_root("pvp").exists()
    assert (server / "keep.txt").exists(), "never the server directory"
    again = _run("instances", "add", "pvp", "--server-dir", str(server))
    assert again.exit_code == 0, again.output


def test_purge_refuses_a_leftover_whose_server_runs(main_instance, tmp_path, monkeypatch):
    from app.shared.paths import instance_root

    _run("instances", "add", "pvp", "--server-dir", str(tmp_path / "pvp"))
    instances.delete_instance("pvp")
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: True)

    result = _run("instances", "remove", "pvp", "--purge")

    assert result.exit_code != 0 and "running" in result.output
    assert instance_root("pvp").is_dir()


@pytest.mark.parametrize("name", ["ghost", "../main"])
def test_remove_refuses_a_name_with_no_list_entry_and_no_folder(main_instance, stopped, name):
    from app.shared.paths import instance_root

    result = _run("instances", "remove", name, "--purge")

    assert result.exit_code != 0 and "No instance named" in result.output
    assert instance_root("main").is_dir()


def test_remove_without_purge_refuses_a_leftover(main_instance, tmp_path, stopped):
    from app.shared.paths import instance_root

    _run("instances", "add", "pvp", "--server-dir", str(tmp_path / "pvp"))
    instances.delete_instance("pvp")

    result = _run("instances", "remove", "pvp")

    assert result.exit_code != 0 and "No instance named" in result.output
    assert instance_root("pvp").is_dir()


def test_remove_refuses_the_last_and_a_running_one(main_instance, tmp_path, monkeypatch):
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)
    # System scope reads /etc/systemd/system, and a box with a real
    # dayz-server.service there refuses main for its unit, not as the last one.
    monkeypatch.setattr(instances, "installed_units", lambda config: [])
    last = _run("instances", "remove", "main")
    assert last.exit_code == 1 and "only instance" in last.output

    _run("instances", "add", "pvp", "--server-dir", str(tmp_path / "pvp"))
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: True)
    running = _run("instances", "remove", "pvp")
    assert running.exit_code == 1 and "Stop it first" in running.output
    assert instances.load_registry().ids() == ["main", "pvp"]


def test_removing_the_default_says_which_took_over(main_instance, tmp_path, stopped):
    _run("instances", "add", "pvp", "--server-dir", str(tmp_path / "pvp"))
    _run("instances", "use", "pvp")

    result = _run("instances", "remove", "pvp")

    assert "The default is now 'main'" in result.output


def test_setup_writes_the_query_port_the_instance_was_given(main_instance, tmp_path, monkeypatch):
    """The port `add` picked lives only in config until serverDZ.cfg exists."""
    from app.shared import container

    _run("instances", "add", "pvp", "--server-dir", str(tmp_path / "pvp"))
    pvp = DayzConfig.for_instance("pvp")
    pvp.server_dir_path.mkdir(parents=True)
    (pvp.server_dir_path / "DayZServer").write_text("")
    pvp.server_cfg_path.write_text("steamQueryPort = 27016;\n")

    result = _run("--instance", "pvp", "setup")

    assert result.exit_code == 0, result.output
    assert "steamQueryPort = 27116" in result.output
    assert "27116" in pvp.server_cfg_path.read_text()
    assert container.sync_query_port(pvp) == {}, "idempotent"


def _cli_install_init(config, monkeypatch):
    from types import SimpleNamespace

    from app.commands import init

    monkeypatch.setattr(init, "IS_WINDOWS", False)
    monkeypatch.setattr(init.inquirer, "confirm", lambda **k: SimpleNamespace(execute=lambda: True))
    init._offer_server_install(config, True)


def _cli_install_run(config, monkeypatch):
    from app.commands import run

    monkeypatch.setattr(run, "load_context", lambda ctx: (config, True))
    monkeypatch.setattr(run, "_mods_pipeline", lambda config, ni: None)
    result = CliRunner().invoke(run.run, ["update-and-copy"], obj={"non_interactive": True})
    assert result.exit_code == 0, result.output


def _cli_install_reinstall(config, monkeypatch):
    from app.commands import cleanup

    monkeypatch.setattr(cleanup, "IS_WINDOWS", False)
    monkeypatch.setattr("app.commands.service.is_server_running", lambda config=None: False)
    monkeypatch.setattr(cleanup.DayzConfig, "load", classmethod(lambda cls, *a, **k: config))
    # sample_config's server dir holds its config dir, which the guard refuses.
    monkeypatch.setattr("app.ops.cleanup_ops.reinstall_refusal", lambda config: None)
    monkeypatch.setattr(cleanup, "rmtree_mount_safe", lambda path: config.server_cfg_path.unlink())
    result = CliRunner().invoke(cleanup.cleanup_group, ["reinstall-server"], obj={"non_interactive": True})
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("install", [_cli_install_init, _cli_install_run, _cli_install_reinstall],
                         ids=["init", "run update-and-copy", "cleanup reinstall-server"])
def test_every_cli_install_writes_the_query_port(sample_config, monkeypatch, install):
    """srv-04: `zsm setup` and the pipeline wrote it, these three did not,
    so a second instance installed through them bound 27016 beside main's."""
    from app.commands import cleanup, init, run
    from app.parsers.servercfg import read_cfg_value

    sample_config.steam_query_port = 27116
    cfg = sample_config.server_cfg_path

    def download(config, **kwargs):
        cfg.write_text('hostname = "EXAMPLE NAME";\n')  # what a fresh SteamCMD install leaves

    for module in (init, run, cleanup):
        monkeypatch.setattr(module, "update_dayz_server", download)
    install(sample_config, monkeypatch)
    assert read_cfg_value(cfg, "steamQueryPort") == "27116"


def test_an_unmanaged_query_port_is_left_alone(sample_config):
    from app.shared import container

    sample_config.server_cfg_path.write_text("steamQueryPort = 27016;\n")
    assert sample_config.steam_query_port == 0
    assert container.sync_query_port(sample_config) == {}
    assert "27016" in sample_config.server_cfg_path.read_text()


def test_purge_asks_first_without_yes(main_instance, tmp_path, stopped, monkeypatch):
    from app.shared.paths import instance_root

    _run("instances", "add", "pvp", "--server-dir", str(tmp_path / "pvp"))
    asked = []
    monkeypatch.setattr("app.commands.instances.confirm", lambda prompt: asked.append(prompt) or False)
    os.environ.pop("ZSM_INSTANCE", None)

    result = CliRunner().invoke(main, ["instances", "remove", "pvp", "--purge"])

    assert "Cancelled" in result.output
    assert asked and "instances/pvp" in asked[0]
    assert instance_root("pvp").is_dir()
    assert instances.load_registry().ids() == ["main", "pvp"]


def test_remove_refuses_while_the_instance_has_units_installed(main_instance, tmp_path, stopped, monkeypatch):
    """Found in the container test: a removed instance left its unit and
    timers installed, pointing at a server nothing managed any more."""
    from types import SimpleNamespace

    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    _run("instances", "add", "pvp", "--server-dir", str(tmp_path / "pvp"))
    (unit_dir / "dayz-server-pvp.service").write_text("[Unit]\n")
    (unit_dir / "dayz-task.pvp.nightly.timer").write_text("[Timer]\n")
    (unit_dir / "dayz-task-mainjob.timer").write_text("[Timer]\n")  # main's, not pvp's
    monkeypatch.setattr("app.shared.platform.has_systemd", lambda: True)
    monkeypatch.setattr("app.shared.platform.IS_WINDOWS", False)
    monkeypatch.setattr("app.shared.systemd_scope.get_scope",
                        lambda config=None: SimpleNamespace(unit_dir=unit_dir))

    refused = _run("instances", "remove", "pvp")

    assert refused.exit_code == 1
    assert "dayz-server-pvp.service, dayz-task.pvp.nightly.timer" in refused.output
    assert "dayz-task-mainjob" not in refused.output
    assert "service remove-timers" in refused.output
    assert instances.load_registry().ids() == ["main", "pvp"]

    for name in ("dayz-server-pvp.service", "dayz-task.pvp.nightly.timer"):
        (unit_dir / name).unlink()
    assert _run("instances", "remove", "pvp").exit_code == 0


def test_service_remove_with_yes_removes(main_instance, monkeypatch, tmp_path):
    """`-y` made the confirmation answer no, so `zsm -y service remove` did
    nothing and said nothing."""
    from types import SimpleNamespace

    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    (unit_dir / "dayz-server.service").write_text("[Unit]\n")
    removed = []
    monkeypatch.setattr("app.commands.service.IS_WINDOWS", False)
    monkeypatch.setattr("app.commands.service.has_systemd", lambda: True)
    monkeypatch.setattr("app.commands.service.get_scope",
                        lambda config=None: SimpleNamespace(unit_dir=unit_dir, needs_sudo=False))
    monkeypatch.setattr("app.server.service_control.remove_service",
                        lambda config: removed.append(config.instance_id) or SimpleNamespace(ok=True, message=""))

    result = _run("service", "remove")

    assert result.exit_code == 0, result.output
    assert removed == ["main"]
    assert "Service removed" in result.output


def test_service_remove_without_yes_still_asks(main_instance, monkeypatch, tmp_path):
    from types import SimpleNamespace

    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    (unit_dir / "dayz-server.service").write_text("[Unit]\n")
    monkeypatch.setattr("app.commands.service.IS_WINDOWS", False)
    monkeypatch.setattr("app.commands.service.has_systemd", lambda: True)
    monkeypatch.setattr("app.commands.service.get_scope",
                        lambda config=None: SimpleNamespace(unit_dir=unit_dir, needs_sudo=False))
    monkeypatch.setattr("app.commands.service.confirm", lambda prompt, **k: False)
    monkeypatch.setattr("app.server.service_control.remove_service",
                        lambda config: pytest.fail("removed without a yes"))
    os.environ.pop("ZSM_INSTANCE", None)

    result = CliRunner().invoke(main, ["service", "remove"])

    assert result.exit_code == 0, result.output


def test_remove_timers_removes_only_this_instances(main_instance, monkeypatch, tmp_path):
    seen = []

    async def fake_remove(config):
        seen.append(config.instance_id)
        return ["dayz-task.pvp.nightly"]

    monkeypatch.setattr("app.commands.service.IS_WINDOWS", False)
    monkeypatch.setattr("app.commands.service.has_systemd", lambda: True)
    monkeypatch.setattr("app.commands.service._ensure_sudo_cli", lambda config=None: True)
    monkeypatch.setattr("app.server.scheduler.remove_timers_async", fake_remove)
    _run("instances", "add", "pvp", "--server-dir", str(tmp_path / "pvp"))

    result = _run("--instance", "pvp", "service", "remove-timers")

    assert result.exit_code == 0, result.output
    assert seen == ["pvp"]
    assert "Removed 1 timer(s): dayz-task.pvp.nightly" in result.output


def test_add_installs_experimental_when_asked(main_instance, tmp_path):
    result = _run("instances", "add", "exp", "--server-dir", str(tmp_path / "exp"), "--branch", "experimental")

    assert result.exit_code == 0, result.output
    assert DayzConfig.for_instance("exp").server_branch == "experimental"
    assert "Experimental server (Steam app 1042420)" in result.output


def test_clone_keeps_the_source_branch_unless_told(main_instance, tmp_path):
    main_instance.server_branch = "experimental"
    main_instance.save()

    kept = _run("instances", "clone", "main", "exp2", "--server-dir", str(tmp_path / "exp2"))
    changed = _run("instances", "clone", "main", "st", "--server-dir", str(tmp_path / "st"), "--branch", "stable")

    assert kept.exit_code == 0 and changed.exit_code == 0, kept.output + changed.output
    assert DayzConfig.for_instance("exp2").server_branch == "experimental"
    assert "Steam app" not in kept.output
    assert DayzConfig.for_instance("st").server_branch == "stable"
    assert ("stable server (Steam app 223350) on its first update, not the Experimental one"
            in " ".join(changed.output.split()))


def test_add_refuses_a_directory_holding_the_other_server(main_instance, tmp_path):
    server = tmp_path / "old"
    (server / "steamapps").mkdir(parents=True)
    (server / "steamapps" / "appmanifest_223350.acf").write_text("")

    result = _run("instances", "add", "exp", "--server-dir", str(server), "--branch", "experimental")

    assert result.exit_code == 1
    output = " ".join(result.output.split())
    assert "already holds the stable DayZ server" in output
    assert "Give the Experimental instance a server directory of its own." in output
    assert not instances.load_registry().get("exp")


def test_an_unknown_branch_is_refused(main_instance, tmp_path):
    result = _run("instances", "add", "exp", "--server-dir", str(tmp_path / "exp"), "--branch", "beta")
    assert result.exit_code == 2
    assert "beta" in result.output
