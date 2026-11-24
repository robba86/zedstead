"""Tests for app.shared.steam.login_script — the web login's password and
Steam Guard code reach SteamCMD in a private script, never on its command
line, where every local user can read them in /proc/<pid>/cmdline (SEC-3)."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from app.shared import steam
from app.shared.platform import IS_WINDOWS


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_the_script_logs_in_and_quits():
    with steam.login_script("bob", "hunter2", "ABCDE") as script:
        data = script.read_bytes()
    assert data == b'@ShutdownOnFailedCommand 1\nlogin "bob" "hunter2" "ABCDE"\nquit\n'


def test_a_codeless_login_has_no_third_value():
    with steam.login_script("bob", "hunter2") as script:
        assert script.read_text(encoding="utf-8").splitlines()[1] == 'login "bob" "hunter2"'


def test_an_empty_password_keeps_its_place():
    """A Guard code must never slide into the password's position."""
    with steam.login_script("bob", "", "ABCDE") as script:
        assert script.read_text(encoding="utf-8").splitlines()[1] == 'login "bob" "" "ABCDE"'


def test_quotes_keep_spaces_semicolons_and_slashes_in_the_password():
    with steam.login_script("bob", "a b;c//d") as script:
        assert 'login "bob" "a b;c//d"' in script.read_text(encoding="utf-8")


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX modes; Windows takes %TEMP%'s ACL")
def test_only_the_owner_can_open_it():
    import os

    old = os.umask(0o022)
    try:
        with steam.login_script("bob", "hunter2") as script:
            assert _mode(script.parent) == 0o700
            assert _mode(script) == 0o600
    finally:
        os.umask(old)


def test_it_is_removed_when_the_block_ends():
    with steam.login_script("bob", "hunter2") as script:
        pass
    assert not script.exists()
    assert not script.parent.exists()


def test_it_is_removed_when_the_block_raises():
    with pytest.raises(RuntimeError):
        with steam.login_script("bob", "hunter2") as script:
            raise RuntimeError("SteamCMD timed out")
    assert not script.parent.exists()


@pytest.mark.parametrize(
    "bad", ['pa"ss', "pa\nquit", "pa\rss", "pa\0ss", "pa\x0bquit", "pa\x85quit", "pa\u2028quit"]
)
@pytest.mark.parametrize("field", ["username", "password", "guard_code"])
def test_a_value_that_cannot_be_written_is_refused_without_being_named(bad, field):
    values = {"username": "bob", "password": "hunter2", "guard_code": "ABCDE"}
    values[field] = bad
    with pytest.raises(steam.LoginScriptRefused) as exc:
        with steam.login_script(**values):
            pytest.fail("a script was written")
    assert bad not in str(exc.value)
    assert "zsm steam-login" in str(exc.value)
    assert isinstance(exc.value, steam.SteamcmdRefused)  # callers catch the base


def test_the_command_line_carries_only_the_script_path(sample_config):
    with steam.login_script("bob", "hunter2", "ABCDE") as script:
        cmd = steam.login_script_cmd(sample_config, script)
    assert cmd == [str(sample_config.steamcmd_path), "+runscript", str(script), "+quit"]
    assert not [part for part in cmd if "hunter2" in part or "ABCDE" in part]
