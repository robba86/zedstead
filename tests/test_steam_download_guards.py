"""A silently-failed SteamCMD download must not read as a successful install.

SteamCMD exits 0 on workshop downloads that fetched nothing (an expired or
missing login is the usual cause), leaving an empty item directory. Trusting the
exit code copied that empty folder into the server dir, where the mod looked
installed but shipped no PBOs, keys or XML — and every downstream step (bikey
copy, XML merge, the Mods page XML column) correctly reported nothing to do.
"""
from __future__ import annotations

import pytest

from app.ops.fileops import copy_mod_to_server
from app.shared.steam import (
    SteamcmdRefused,
    build_mod_download_cmd,
    download_mod,
    download_mod_async,
    is_mod_downloaded,
)

MOD_ID = "123456"
MOD_NAME = "@DayZ-Bicycle"


class _FakeRunner:
    """Duck-types the runner passed to download_mod_async."""

    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.lines: list[str] = []
        self.calls = 0

    async def run_command(self, cmd: list[str]) -> int:
        self.calls += 1
        return self.returncode

    def write(self, text: str) -> None:
        self.lines.append(text)


@pytest.fixture()
def steam_config(sample_config, tmp_path):
    # workshop_content_dir MUST be pinned: DayzConfig.workshop_dir otherwise
    # auto-detects the host's real Steam install, and these tests create and
    # delete item directories under it.
    sample_config.steam_username = "someuser"
    sample_config.workshop_content_dir = str(tmp_path / "workshop" / "content" / "221100")
    sample_config.workshop_dir.mkdir(parents=True, exist_ok=True)
    return sample_config


def _item_dir(config, populated: bool):
    item = config.workshop_dir / MOD_ID
    item.mkdir(parents=True, exist_ok=True)
    if populated:
        (item / "mod.cpp").write_text("name = \"Bicycle\";\n")
    return item


# --- is_mod_downloaded ------------------------------------------------------


def test_is_mod_downloaded_rejects_empty_dir(steam_config):
    _item_dir(steam_config, populated=False)
    assert is_mod_downloaded(steam_config, MOD_ID) is False


def test_is_mod_downloaded_accepts_populated_dir(steam_config):
    _item_dir(steam_config, populated=True)
    assert is_mod_downloaded(steam_config, MOD_ID) is True


# --- download_mod_async -----------------------------------------------------


async def test_async_download_fails_when_item_dir_is_empty(steam_config, monkeypatch):
    monkeypatch.setattr("app.shared.steam.STEAM_RATE_LIMIT_SECONDS", 0)
    _item_dir(steam_config, populated=False)
    runner = _FakeRunner(returncode=0)

    ok = await download_mod_async(runner, steam_config, MOD_ID, MOD_NAME, max_retries=2)

    assert ok is False
    assert runner.calls == 2  # exit 0 with no content is a failed attempt, so it retried
    assert any("left no files in the workshop folder" in line for line in runner.lines)


async def test_async_download_stops_retrying_once_cancelled(steam_config, monkeypatch):
    """A cancelled web job used to spawn SteamCMD max_retries times for every
    mod still in its list before the Cancel could take effect."""
    monkeypatch.setattr("app.shared.steam.STEAM_RATE_LIMIT_SECONDS", 0)

    class _CancelledMidRun(_FakeRunner):
        cancelled = False

        async def run_command(self, cmd: list[str]) -> int:
            self.cancelled = True  # the user clicked Cancel during this attempt
            return await super().run_command(cmd)

    runner = _CancelledMidRun(returncode=1)

    ok = await download_mod_async(runner, steam_config, MOD_ID, MOD_NAME, max_retries=5)

    assert ok is False
    assert runner.calls == 1
    assert not any("Retrying" in line for line in runner.lines)


async def test_async_download_succeeds_when_item_lands(steam_config):
    _item_dir(steam_config, populated=True)
    runner = _FakeRunner(returncode=0)

    assert await download_mod_async(runner, steam_config, MOD_ID, MOD_NAME) is True
    assert runner.calls == 1


async def test_async_download_refuses_without_steam_username(steam_config):
    steam_config.steam_username = ""
    runner = _FakeRunner(returncode=0)

    ok = await download_mod_async(runner, steam_config, MOD_ID, MOD_NAME)

    assert ok is False
    assert runner.calls == 0  # never shells out with an empty +login
    assert any("zsm steam-login" in line for line in runner.lines)


# --- download_mod (sync) ----------------------------------------------------


def test_sync_download_raises_when_item_dir_is_empty(steam_config, monkeypatch):
    _item_dir(steam_config, populated=False)
    monkeypatch.setattr("app.shared.steam.run_passthrough", lambda *a, **kw: (0, ""))
    monkeypatch.setattr("app.shared.steam.time.sleep", lambda _s: None)

    with pytest.raises(RuntimeError, match="left no files in the workshop folder"):
        download_mod(steam_config, MOD_ID, MOD_NAME, max_retries=2, log=lambda _s: None)


def test_sync_download_refuses_without_steam_username(steam_config):
    steam_config.steam_username = ""

    with pytest.raises(RuntimeError, match="steam-login"):
        download_mod(steam_config, MOD_ID, MOD_NAME, log=lambda _s: None)


# --- a Workshop ID that is not digits (plans/68 CON-12) ----------------------

_NOT_IDS = ["+quit", "-x", "../123", "12 34", "١٢٣", ""]


@pytest.mark.parametrize("bad_id", _NOT_IDS)
def test_the_download_command_refuses_an_id_that_is_not_digits(steam_config, bad_id):
    with pytest.raises(SteamcmdRefused, match="digits only"):
        build_mod_download_cmd(steam_config, bad_id)


@pytest.mark.parametrize("bad_id", _NOT_IDS)
async def test_async_download_never_runs_steamcmd_for_an_id_that_is_not_digits(
    steam_config, bad_id, monkeypatch
):
    monkeypatch.setattr("app.shared.steam.STEAM_RATE_LIMIT_SECONDS", 0)
    runner = _FakeRunner(returncode=0)

    ok = await download_mod_async(runner, steam_config, bad_id, MOD_NAME, max_retries=1)

    assert ok is False
    assert runner.calls == 0


def test_sync_download_never_runs_steamcmd_for_an_id_that_is_not_digits(steam_config, monkeypatch):
    ran = []
    monkeypatch.setattr("app.shared.steam.run_passthrough", lambda *a, **kw: ran.append(a) or (0, ""))
    monkeypatch.setattr("app.shared.steam.time.sleep", lambda _s: None)

    with pytest.raises(RuntimeError):
        download_mod(steam_config, "+quit", MOD_NAME, max_retries=1, log=lambda _s: None)
    assert ran == []


# --- copy_mod_to_server -----------------------------------------------------


def test_copy_refuses_empty_workshop_dir(steam_config):
    _item_dir(steam_config, populated=False)
    lines: list[str] = []

    ok = copy_mod_to_server(steam_config, MOD_NAME, {MOD_NAME: MOD_ID}, log=lines.append)

    assert ok is False
    assert not (steam_config.server_dir_path / MOD_NAME).exists()
    assert any("is empty in the workshop cache" in line for line in lines)


def test_copy_accepts_populated_workshop_dir(steam_config):
    _item_dir(steam_config, populated=True)

    ok = copy_mod_to_server(steam_config, MOD_NAME, {MOD_NAME: MOD_ID}, log=lambda _s: None)

    assert ok is True
    assert (steam_config.server_dir_path / MOD_NAME / "mod.cpp").is_file()
