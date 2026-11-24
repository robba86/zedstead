"""Tests for the CLI's response to an install that has never run `zsm init`.

A fresh box has no ~/.config/zsm/config.json, and that is the state every
subcommand meets first. `DayzConfig.load` raises ConfigNotFound; the root group
(`app.__main__.ConfigAwareGroup`) turns it into the "run zsm init first" line
the exception already carries, instead of letting it reach the user as a Python
traceback from whichever command they happened to type.
"""

import pytest
from click.testing import CliRunner

from app.__main__ import ConfigAwareGroup, main
from app.shared.config import ConfigNotFound
from app.shared.platform import IS_WINDOWS

# One command per way of loading config, because the two are fixed by different
# halves of this: `bundle export` goes through load_context, while `info`,
# `settings list` and `changes` call DayzConfig.load() directly — the ~38 sites
# that a fix confined to load_context would have left crashing.
COMMANDS = [
    ["bundle", "export"],
    ["info"],
    ["settings", "list"],
    ["changes"],
]


@pytest.fixture()
def no_config(sample_config, monkeypatch):
    """CONFIG_FILE patched into tmp, and deliberately never written."""
    monkeypatch.delenv("SUDO_USER", raising=False)
    from app.shared.config import CONFIG_FILE

    assert not CONFIG_FILE.exists()
    return CONFIG_FILE


def test_config_not_found_is_a_file_not_found_error():
    """Everything that already guards a config load catches FileNotFoundError —
    load_or_none, doctor's config check, status. Narrowing that would be a
    silent regression, so the marker type stays a subclass."""
    assert issubclass(ConfigNotFound, FileNotFoundError)


@pytest.mark.parametrize("argv", COMMANDS)
def test_missing_config_reports_instead_of_raising(no_config, argv):
    result = CliRunner().invoke(main, argv)

    assert result.exit_code == 1
    assert not isinstance(result.exception, ConfigNotFound)
    assert "Traceback" not in result.output
    assert "zsm init" in result.output


def test_root_group_keeps_the_handler():
    """The handling lives on the group, not in each command, so a plain
    click.group() here would quietly restore the traceback for all of them."""
    assert isinstance(main, ConfigAwareGroup)


@pytest.mark.skipif(IS_WINDOWS, reason="sudo is a POSIX concern")
def test_missing_config_under_sudo_says_it_read_roots_home(no_config, monkeypatch):
    """`sudo zsm ...` reads root's HOME, so the user's own config is invisible
    and the bare "no config found" line reads as data loss."""
    monkeypatch.setenv("SUDO_USER", "me")

    result = CliRunner().invoke(main, ["info"])

    assert result.exit_code == 1
    assert "sudo" in result.output


@pytest.mark.skipif(IS_WINDOWS, reason="firewall is Linux-only")
def test_firewall_under_sudo_without_a_config_is_reported_not_raised(no_config, monkeypatch):
    """The traceback this file guards against was first reported from
    `zsm service firewall` on a fresh VM. Since plans/40 F4 that command runs
    before `zsm init` with the default ports, so it left COMMANDS; under sudo it
    still refuses (the missing config is root's), and that refusal must still
    reach the user as the root group's line."""
    monkeypatch.setenv("SUDO_USER", "me")

    result = CliRunner().invoke(main, ["service", "firewall"])

    assert result.exit_code == 1
    assert not isinstance(result.exception, ConfigNotFound)
    assert "Traceback" not in result.output
    assert "sudo" in result.output
    assert "me" in result.output


def test_missing_config_without_sudo_stays_quiet(no_config):
    result = CliRunner().invoke(main, ["info"])

    assert "sudo" not in result.output
