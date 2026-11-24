"""Container config sync — `zsm container-init` / app.shared.container.

The critical property: a container recreate must MERGE env into the existing
config, never clobber state set in the web UI (scheduled tasks, mods, Discord).
"""

from __future__ import annotations

from click.testing import CliRunner

from pathlib import Path

import pytest

from app.__main__ import main
from app.parsers.battleye_cfg import read_beserver_cfg, write_beserver_cfg
from app.parsers.servercfg import read_cfg_value
from app.shared.config import DayzConfig
from tests.conftest import pinned_names, set_test_flags
from app.shared.container import (
    apply_env_overrides,
    sync_battleye_cfg,
    sync_config_from_env,
    sync_server_cfg,
)


def test_sync_first_boot_creates_from_env(sample_config):
    # sample_config patches the CONFIG paths but writes no file → first boot.
    env = {"SERVER_NAME": "My Server", "SERVER_PORT": "2500", "RCON_PASSWORD": "secret"}
    config, created, changed = sync_config_from_env(env)

    assert created is True
    assert config.server_name == "My Server"
    assert config.server_port == 2500
    assert config.rcon_password == "secret"
    # container path defaults are applied on first boot
    assert config.server_dir == "/home/zsm/server"
    assert config.backup_dir == "/home/zsm/backups"
    # persisted to disk, as the first instance (plans/44 step 4)
    assert DayzConfig.load().server_name == "My Server"
    assert config.instance_id == "main"
    assert not sample_config.paths.config_file.exists(), "nothing at the legacy path"


def test_sync_merge_preserves_panel_fields(sample_config):
    task = {"id": "r4", "task_type": "restart", "cron": "0 */4 * * *", "enabled": True}
    sample_config.scheduled_tasks = [task]
    set_test_flags(sample_config, pinned=["@CF", "@Foo"])
    sample_config.discord_webhook_url = "https://hook"
    sample_config.restart_warning_minutes = [20, 10]
    sample_config.server_name = "Old"
    sample_config.save()

    config, created, changed = sync_config_from_env({"SERVER_NAME": "New Name"})

    assert created is False
    assert changed == ["server_name"]
    assert config.server_name == "New Name"
    # everything the web UI owns survives the merge
    assert config.scheduled_tasks == [task]
    assert pinned_names(config) == ["@CF", "@Foo"]
    assert config.discord_webhook_url == "https://hook"
    assert config.restart_warning_minutes == [20, 10]
    # and it's persisted
    reloaded = DayzConfig.load()
    assert reloaded.server_name == "New Name"
    assert reloaded.scheduled_tasks == [task]


def test_sync_pins_service_scope_system(sample_config):
    """The container is unconditionally pinned to system scope (systemd PID 1 +
    NOPASSWD sudo — user scope is unsupported there)."""
    sample_config.service_scope = "user"
    sample_config.save()

    config, _created, changed = sync_config_from_env({})

    assert config.service_scope == "system"
    assert DayzConfig.load().service_scope == "system"
    # the pin is not reported as an env-driven change
    assert "service_scope" not in changed


def test_apply_env_skips_unset_and_empty(sample_config):
    sample_config.server_name = "Keep"
    changed = apply_env_overrides(sample_config, {"SERVER_NAME": "", "SERVER_ARGS": "-x"})
    assert "server_name" not in changed
    assert sample_config.server_name == "Keep"
    assert sample_config.server_args == "-x"


def test_apply_env_bad_int_skipped(sample_config):
    original = sample_config.server_port
    changed = apply_env_overrides(sample_config, {"SERVER_PORT": "notanint"})
    assert changed == []
    assert sample_config.server_port == original


def test_apply_env_no_change_when_value_matches(sample_config):
    sample_config.server_port = 2302
    changed = apply_env_overrides(sample_config, {"SERVER_PORT": "2302"})
    assert changed == []


def test_container_init_cli_reports(sample_config):
    result = CliRunner().invoke(main, ["container-init"], env={"SERVER_NAME": "CLI Srv"})
    assert result.exit_code == 0, result.output
    assert DayzConfig.load().server_name == "CLI Srv"


def test_sync_server_cfg_writes_hostname(sample_config):
    sample_config.server_name = "My New Server"
    written = sync_server_cfg(sample_config)
    assert written == {"hostname": "My New Server"}
    assert read_cfg_value(sample_config.server_cfg_path, "hostname") == "My New Server"


def test_sync_server_cfg_keeps_a_name_holding_a_semicolon(sample_config):
    """Read back cut at the `;`, the name differed at every start and was
    written again over the cut, leaving the tail behind each time (plans/68 A-3)."""
    cfg = Path(sample_config.server_cfg_path)
    sample_config.server_name = "[EU] Frosty; PvE"
    assert sync_server_cfg(sample_config) == {"hostname": "[EU] Frosty; PvE"}
    after_first = cfg.read_text()
    assert 'hostname = "[EU] Frosty; PvE";\n' in after_first

    assert sync_server_cfg(sample_config) == {}
    assert cfg.read_text() == after_first
    assert read_cfg_value(cfg, "hostname") == "[EU] Frosty; PvE"


def test_sync_server_cfg_noop_when_unchanged(sample_config):
    # tmp_server_dir ships hostname = "Test Server"
    sample_config.server_name = "Test Server"
    assert sync_server_cfg(sample_config) == {}


def test_sync_server_cfg_noop_when_file_missing(sample_config):
    Path(sample_config.server_cfg_path).unlink()
    sample_config.server_name = "Whatever"
    assert sync_server_cfg(sample_config) == {}  # no crash, no file created
    assert not Path(sample_config.server_cfg_path).exists()


def test_sync_server_cfg_writes_steam_query_port(sample_config):
    # hostname already in sync; only the query port should be written
    sample_config.server_name = "Test Server"
    sample_config.steam_query_port = 27020
    written = sync_server_cfg(sample_config)
    assert written == {"steamQueryPort": "27020"}
    assert read_cfg_value(sample_config.server_cfg_path, "steamQueryPort") == "27020"


def test_sync_server_cfg_zero_query_port_unmanaged(sample_config):
    # 0 (the default) must leave steamQueryPort in serverDZ.cfg alone
    sample_config.server_name = "Test Server"
    sample_config.steam_query_port = 0
    before = read_cfg_value(sample_config.server_cfg_path, "steamQueryPort")
    assert sync_server_cfg(sample_config) == {}
    assert read_cfg_value(sample_config.server_cfg_path, "steamQueryPort") == before


def test_apply_env_steam_query_port(sample_config):
    changed = apply_env_overrides(sample_config, {"STEAM_QUERY_PORT": "27020"})
    assert changed == ["steam_query_port"]
    assert sample_config.steam_query_port == 27020


def test_sync_server_cfg_cli(sample_config):
    sample_config.server_name = "CLI Named Server"
    sample_config.save()
    result = CliRunner().invoke(main, ["sync-server-cfg"])
    assert result.exit_code == 0, result.output
    assert read_cfg_value(sample_config.server_cfg_path, "hostname") == "CLI Named Server"


def test_sync_battleye_cfg_writes_rcon(sample_config):
    sample_config.rcon_password = "secretpw"
    sample_config.rcon_port = 2310
    port = sync_battleye_cfg(sample_config)
    assert port == 2310
    data = read_beserver_cfg(sample_config.beserver_cfg_path)
    assert data["RConPassword"] == "secretpw"
    assert data["RConPort"] == "2310"


def test_sync_battleye_cfg_noop_without_password(sample_config):
    sample_config.rcon_password = ""
    assert sync_battleye_cfg(sample_config) is None


def test_sync_battleye_cfg_preserves_other_keys(sample_config):
    write_beserver_cfg(
        sample_config.beserver_cfg_path,
        {"RConPassword": "old", "RConPort": "2305", "MaxPing": "250"},
    )
    sample_config.rcon_password = "new"
    sync_battleye_cfg(sample_config)
    data = read_beserver_cfg(sample_config.beserver_cfg_path)
    assert data["MaxPing"] == "250"          # unrelated key preserved
    assert data["RConPassword"] == "new"


def test_sync_server_cfg_cmd_applies_both(sample_config):
    sample_config.server_name = "Both Server"
    sample_config.rcon_password = "pw123"
    sample_config.rcon_port = 2305
    sample_config.save()
    result = CliRunner().invoke(main, ["sync-server-cfg"])
    assert result.exit_code == 0, result.output
    assert read_cfg_value(sample_config.server_cfg_path, "hostname") == "Both Server"
    assert read_beserver_cfg(sample_config.beserver_cfg_path)["RConPassword"] == "pw123"


# A value the cfg writers refuse (plans/68 LB-6): skipped and noted, never
# raised, since this runs at every container start.
_BAD_NAMES = ['X";\nverifySignatures = 0;\nhostname = "X', "Split\u2028Name", 'Say "hi"']


@pytest.mark.parametrize("name", _BAD_NAMES)
def test_sync_server_cfg_skips_a_name_the_writer_refuses(sample_config, name):
    cfg = Path(sample_config.server_cfg_path)
    before = read_cfg_value(cfg, "hostname")
    sample_config.server_name = name
    sample_config.steam_query_port = 27020
    notes: list[str] = []

    assert sync_server_cfg(sample_config, notes) == {"steamQueryPort": "27020"}

    assert read_cfg_value(cfg, "hostname") == before
    assert "verifySignatures = 0" not in cfg.read_text()
    assert len(notes) == 1 and notes[0].startswith("serverDZ.cfg hostname left as it was: ")


@pytest.mark.parametrize("password", ["hunter2\nRConIP 0.0.0.0", "hunter2\u2028RConIP 0.0.0.0"])
def test_sync_battleye_cfg_skips_a_password_the_writer_refuses(sample_config, password):
    write_beserver_cfg(sample_config.beserver_cfg_path, {"RConPassword": "old", "RConPort": "2305"})
    sample_config.rcon_password = password
    sample_config.rcon_port = 2310
    notes: list[str] = []

    assert sync_battleye_cfg(sample_config, notes) == 2310

    assert read_beserver_cfg(sample_config.beserver_cfg_path) == {
        "RConPassword": "old", "RConPort": "2310", "RestrictRCon": "0",
    }
    assert len(notes) == 1 and "hunter2" not in notes[0]


def test_sync_battleye_cfg_leaves_a_file_it_cannot_rewrite(sample_config):
    """A value already in the file that would not stay one line: the rewrite
    is refused, rather than writing the text after U+2028 as an RConIP line."""
    path = sample_config.beserver_cfg_path
    path.write_text("RConPassword old\nMaxPing 200\u2028RConIP 0.0.0.0\n", encoding="utf-8")
    before = path.read_bytes()
    sample_config.rcon_password = "new"
    notes: list[str] = []

    assert sync_battleye_cfg(sample_config, notes) is None

    assert path.read_bytes() == before
    assert len(notes) == 1 and notes[0].startswith("beserver_x64.cfg was not written: ")
    assert "0.0.0.0" not in notes[0]


def test_a_container_start_with_a_bad_server_name_syncs_everything_else(sample_config):
    cfg = Path(sample_config.server_cfg_path)
    before = read_cfg_value(cfg, "hostname")
    env = {"SERVER_NAME": _BAD_NAMES[0], "RCON_PASSWORD": "pw123456789", "STEAM_QUERY_PORT": "27020"}
    sample_config.save()  # container-init merges into it rather than the container's defaults

    result = CliRunner().invoke(main, ["container-init"], env=env)
    assert result.exit_code == 0, result.output
    result = CliRunner().invoke(main, ["sync-server-cfg"], env=env)

    assert result.exit_code == 0, result.output
    assert "hostname left as it was" in result.output
    assert read_cfg_value(cfg, "hostname") == before
    assert read_cfg_value(cfg, "steamQueryPort") == "27020"
    assert "verifySignatures = 0" not in cfg.read_text()
    assert read_beserver_cfg(sample_config.beserver_cfg_path)["RConPassword"] == "pw123456789"


def test_install_timers_cli_no_systemd(monkeypatch):
    monkeypatch.setattr("app.commands.service.IS_WINDOWS", False)
    monkeypatch.setattr("app.commands.service.has_systemd", lambda: False)
    result = CliRunner().invoke(main, ["service", "install-timers"])
    assert result.exit_code == 0
    assert "systemd is not available" in result.output


def test_install_timers_cli_installs(monkeypatch, sample_config):
    task = {"id": "r4", "task_type": "restart", "cron": "0 */4 * * *", "enabled": True}
    sample_config.scheduled_tasks = [task]
    sample_config.save()

    monkeypatch.setattr("app.commands.service.IS_WINDOWS", False)
    monkeypatch.setattr("app.commands.service.has_systemd", lambda: True)
    monkeypatch.setattr("app.commands.service._ensure_sudo_cli", lambda config=None: True)

    called = {}

    async def fake_install(cfg):
        called["cfg"] = cfg
        return ["dayz-task-r4"]

    monkeypatch.setattr("app.server.scheduler.install_timers_async", fake_install)

    result = CliRunner().invoke(main, ["-y", "service", "install-timers"])
    assert result.exit_code == 0, result.output
    assert "cfg" in called
    assert "dayz-task-r4" in result.output


def test_apply_env_sets_the_server_branch(sample_config):
    changed = apply_env_overrides(sample_config, {"SERVER_BRANCH": "experimental"})
    assert changed == ["server_branch"]
    assert sample_config.server_branch == "experimental"


# ---------------------------------------------------------------------------
# plans/68 NET-3 — no guessable RCon password, and never one in the log
# ---------------------------------------------------------------------------


def _battleye_form_password(config) -> str:
    from app.web.editors import get_editor

    body = get_editor("battleye").load(config)
    return {f["key"]: f["value"] for s in body["sections"] for f in s["fields"]}["RConPassword"]


def test_no_rcon_password_anywhere_gets_a_random_one_that_stays(sample_config):
    sample_config.rcon_password = ""
    sample_config.save()
    sample_config.beserver_cfg_path.unlink()

    notes: list[str] = []
    config, _created, _changed = sync_config_from_env({}, notes=notes)
    generated = config.rcon_password

    assert len(generated) == 24 and generated.isalnum()
    assert DayzConfig.load().rcon_password == generated
    assert any("generated a random one" in n for n in notes)
    assert not any(generated in n for n in notes)
    # The next boot keeps it: a password that changed on every boot would break
    # every RCon tool the admin set up.
    again, _c, _ch = sync_config_from_env({})
    assert again.rcon_password == generated


def test_changeme_in_the_env_is_ignored_and_replaced(sample_config):
    sample_config.rcon_password = ""
    sample_config.save()
    sample_config.beserver_cfg_path.unlink()

    notes: list[str] = []
    config, _created, changed = sync_config_from_env({"RCON_PASSWORD": "changeme"}, notes=notes)

    assert config.rcon_password not in ("", "changeme")
    assert "rcon_password" not in changed
    assert any("'changeme'" in n and "ignored" in n for n in notes)
    # A later boot with the same .env does not put it back.
    again, _c, _ch = sync_config_from_env({"RCON_PASSWORD": "changeme"})
    assert again.rcon_password == config.rcon_password


def test_changeme_in_config_and_battleye_cfg_is_replaced_everywhere(sample_config):
    sample_config.rcon_password = "changeme"
    sample_config.save()
    write_beserver_cfg(
        sample_config.beserver_cfg_path,
        {"RConPassword": "changeme", "RConPort": "2305", "RestrictRCon": "0"},
    )

    notes: list[str] = []
    config, _created, _changed = sync_config_from_env({}, notes=notes)
    sync_battleye_cfg(config)
    generated = config.rcon_password

    assert generated != "changeme" and len(generated) == 24
    assert read_beserver_cfg(config.beserver_cfg_path)["RConPassword"] == generated
    assert any("was the placeholder 'changeme'" in n for n in notes)
    # The place the notes send the admin really shows it.
    assert "Configs -> BattlEye" in " ".join(notes)
    assert _battleye_form_password(config) == generated


def test_a_real_password_in_the_battleye_cfg_is_kept(sample_config):
    """An admin who set RConPassword by hand in the cfg, with nothing in .env or
    config.json, keeps it rather than having it replaced."""
    sample_config.rcon_password = ""
    sample_config.save()
    # tmp_server_dir ships `RConPassword mypass`.
    config, _created, _changed = sync_config_from_env({})
    assert config.rcon_password == "mypass"


def test_a_real_env_password_still_wins_over_config(sample_config):
    sample_config.rcon_password = "fromconfig"
    sample_config.save()
    config, _created, changed = sync_config_from_env({"RCON_PASSWORD": "fromenv-long"})
    assert config.rcon_password == "fromenv-long"
    assert changed == ["rcon_password"]


def test_container_init_never_prints_the_password(sample_config):
    sample_config.rcon_password = "changeme"
    sample_config.save()
    sample_config.beserver_cfg_path.unlink()

    result = CliRunner().invoke(main, ["container-init"], env={"RCON_PASSWORD": "changeme"})

    assert result.exit_code == 0, result.output
    generated = DayzConfig.load().rcon_password
    assert generated not in ("", "changeme")
    assert generated not in result.output
    assert "generated a random one" in result.output
    assert "Configs -> BattlEye" in result.output


def test_env_example_ships_no_rcon_password():
    text = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln.startswith("RCON_PASSWORD=")]
    assert lines == ["RCON_PASSWORD="]


def test_sync_records_the_fields_the_env_sets(sample_config):
    from app.shared.container import env_locked_fields

    config, _created, _changed = sync_config_from_env(
        {"SERVER_DIR": "/srv/dayz", "RCON_PASSWORD": "changeme", "SERVER_PORT": "x", "BACKUP_DIR": ""})
    # The placeholder, an unparseable int and an empty value are not set by the env.
    assert env_locked_fields(config) == {"server_dir": "SERVER_DIR"}

    config, _created, _changed = sync_config_from_env({})
    assert env_locked_fields(config) == {}
    assert not config.paths.docker_env.exists()


def test_a_record_naming_a_field_under_the_wrong_var_is_ignored(sample_config):
    from app.shared.container import env_locked_fields

    sample_config.paths.docker_env.write_text(
        '{"server_dir": "STEAMCMD_DIR", "web_lock_paths": "SERVER_DIR", "backup_dir": "BACKUP_DIR"}')
    assert env_locked_fields(sample_config) == {"backup_dir": "BACKUP_DIR"}
    sample_config.paths.docker_env.write_text("not json")
    assert env_locked_fields(sample_config) == {}


# --- what a container recreate keeps (night-srv-03) ---------------------------


def test_a_dir_on_no_volume_is_lost_and_a_mounted_one_is_kept(container_mounts):
    from app.shared import container

    assert container.lost_on_recreate(container_mounts / "server-exp"), "a sibling of the volume is on /"
    assert container.lost_on_recreate(container_mounts / "run" / "exp"), "a tmpfs keeps nothing either"
    assert not container.lost_on_recreate(container_mounts / "server")
    assert not container.lost_on_recreate(container_mounts / "server" / "profiles")
    assert not container.lost_on_recreate(container_mounts / ".config" / "zsm" / "instances" / "exp")


def test_the_volume_problem_names_the_dir_and_the_recreate(container_mounts):
    from app.shared import container

    server_dir = str(container_mounts / "server-exp")
    problem = container.server_dir_volume_problem(server_dir)
    assert server_dir in problem and "--force-recreate" in problem
    assert container.server_dir_volume_problem(str(container_mounts / "server")) is None


def test_outside_a_container_or_without_mounts_nothing_is_flagged(monkeypatch, tmp_path):
    from app.shared import container

    monkeypatch.setattr(container, "IS_DOCKER", False)
    assert container.server_dir_volume_problem("/home/zsm/server-exp") is None
    monkeypatch.setattr(container, "IS_DOCKER", True)
    monkeypatch.setattr(container, "MOUNTINFO", tmp_path / "no-such-mountinfo")
    assert container.server_dir_volume_problem("/home/zsm/server-exp") is None
