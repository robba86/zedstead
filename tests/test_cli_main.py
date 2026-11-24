"""Tests for CLI entry point — version, help output, no-args behaviour."""

import os

import click
import pytest
from click.testing import CliRunner

from app.__main__ import main
from app.shared.platform import IS_WINDOWS


def test_version():
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "zsm" in result.output


def test_help():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "DayZ server management CLI" in result.output
    assert "--version" in result.output
    assert "--yes" in result.output


def test_help_names_the_discord():
    from app.__main__ import DISCORD_URL

    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert DISCORD_URL in result.output


def test_subcommands_registered():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    for cmd in ("init", "run", "mods", "maps", "cleanup", "info",
                "service", "doctor", "backup",
                "changes",
                "presets", "settings", "steam-login",
                "start", "stop", "restart", "status"):
        assert cmd in result.output, f"Missing subcommand: {cmd}"


def test_menu_subcommands_removed():
    """Menu subcommands have been replaced by flag-driven CLI + the web UI."""
    runner = CliRunner()
    for group in ("backup", "cleanup", "mods", "maps", "service"):
        result = runner.invoke(main, [group, "--help"])
        assert result.exit_code == 0
        assert "menu" not in result.output.lower().split(), (
            f"{group} still exposes a 'menu' subcommand: {result.output}"
        )


def _stub_web(monkeypatch, calls):
    @click.command("web")
    def fake_web():
        calls.append("web")

    monkeypatch.setattr("app.__main__.web_cmd", fake_web)


def test_no_args_hands_over_to_the_web_ui(monkeypatch):
    """Bare `zsm` sets up if needed, then starts the web UI."""
    calls: list[str] = []
    monkeypatch.setattr("app.__main__.prompt_init_if_missing", lambda ctx: True)
    _stub_web(monkeypatch, calls)

    result = CliRunner().invoke(main, [])
    assert result.exit_code == 0, result.output
    assert calls == ["web"]


def test_no_args_stops_when_setup_is_declined(monkeypatch):
    """Declining the setup wizard must not start the web UI."""
    calls: list[str] = []
    monkeypatch.setattr("app.__main__.prompt_init_if_missing", lambda ctx: False)
    _stub_web(monkeypatch, calls)

    result = CliRunner().invoke(main, [])
    assert result.exit_code == 0, result.output
    assert calls == []


def test_config_and_types_editor_groups_removed():
    """`config` and `types-editor` are web-only now (settings has flag-driven CLI)."""
    runner = CliRunner()
    for group in ("config", "types-editor"):
        result = runner.invoke(main, [group, "--help"])
        assert result.exit_code != 0, f"{group} group is still registered"


# --- the instance (plans/44 step 4) -----------------------------------------------


def _instance(name: str, server_dir: str) -> None:
    from app.server.instances import ServerInstance, add_instance, load_registry, save_registry
    from app.shared.config import DayzConfig

    config = DayzConfig(server_dir=server_dir)
    config.instance_id = name
    config.save()
    save_registry(add_instance(load_registry(), ServerInstance(name)))


def test_the_root_group_moves_a_legacy_install_into_main(sample_config):
    from app.shared import paths

    sample_config.server_dir = "/srv/legacy"
    sample_config.save()

    result = CliRunner().invoke(main, ["settings", "get", "server_dir"])

    assert result.exit_code == 0, result.output
    assert "/srv/legacy" in result.stdout
    assert "Moved this install into" in result.stderr
    assert (paths.instances_dir() / "main" / "config.json").is_file()
    assert os.environ["ZSM_INSTANCE"] == "main"


def test_help_moves_nothing(sample_config):
    from app.shared import paths

    sample_config.save()
    CliRunner().invoke(main, ["--help"])
    assert not paths.instances_dir().exists()


def test_instance_picks_the_config_every_command_loads(sample_config):
    _instance("main", "/srv/main")
    _instance("pvp", "/srv/pvp")

    by_flag = CliRunner().invoke(main, ["--instance", "pvp", "settings", "get", "server_dir"])
    # The pin outlives an invocation, which is the point in a real process
    # (it runs the root group once); two invocations in one test need a reset.
    assert os.environ.pop("ZSM_INSTANCE") == "pvp"
    by_default = CliRunner().invoke(main, ["settings", "get", "server_dir"])

    assert "/srv/pvp" in by_flag.stdout
    assert "/srv/main" in by_default.stdout


def test_the_environment_variable_is_the_same_flag(sample_config):
    """D1: a `zsm` the panel spawns inherits its instance."""
    _instance("main", "/srv/main")
    _instance("pvp", "/srv/pvp")

    result = CliRunner().invoke(main, ["settings", "get", "server_dir"],
                                env={"ZSM_INSTANCE": "pvp"})

    assert "/srv/pvp" in result.stdout


def test_an_unknown_instance_lists_the_known_ones(sample_config):
    _instance("main", "/srv/main")

    result = CliRunner().invoke(main, ["--instance", "nope", "settings", "get", "server_dir"])

    assert result.exit_code == 2
    assert "no instance named 'nope'" in result.output
    assert "known: main" in result.output


def test_a_broken_registry_is_an_error_not_a_traceback(sample_config):
    from app.shared import paths

    paths.instances_file().write_text("{not json")

    result = CliRunner().invoke(main, ["settings", "get", "server_dir"])

    assert result.exit_code == 1
    assert "instances.json could not be read" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX modes")
def test_the_root_group_makes_the_config_dir_owner_only(sample_config):
    """Before the migration or any command writes into it (SEC-6)."""
    import stat

    from app.shared import paths

    sample_config.save()
    os.chmod(paths.config_dir(), 0o755)

    result = CliRunner().invoke(main, ["settings", "get", "server_dir"])

    assert result.exit_code == 0, result.output
    assert stat.S_IMODE(paths.config_dir().stat().st_mode) == 0o700
