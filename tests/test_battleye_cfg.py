"""Tests for cli.battleye_cfg — BEServer_x64.cfg reader/writer."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.parsers.battleye_cfg import (
    BeserverCfgInUse,
    BeserverValueError,
    check_beserver_cfg,
    live_beserver_cfg,
    parse_beserver_text,
    read_beserver_cfg,
    refuse_while_running,
    write_beserver_cfg,
)
from app.server import service_control
from app.shared import systemd_scope


@pytest.fixture()
def unit(monkeypatch):
    """Answer `systemctl show` from a dict the test controls, never systemd."""
    state = {"out": ""}
    monkeypatch.setattr(service_control, "has_systemd", lambda: True)
    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    # On the class: `get_scope` builds the instance.
    monkeypatch.setattr(systemd_scope.SystemdScope, "query", lambda self, *a: state["out"])
    return state


# ---------------------------------------------------------------------------
# read_beserver_cfg
# ---------------------------------------------------------------------------


class TestReadBeserverCfg:
    def test_read_basic_keys(self, tmp_path: Path):
        cfg = tmp_path / "beserver_x64.cfg"
        cfg.write_text("RConPassword mypass\nRConPort 2305\n")
        data = read_beserver_cfg(cfg)
        assert data["RConPassword"] == "mypass"
        assert data["RConPort"] == "2305"

    def test_read_missing_file(self, tmp_path: Path):
        cfg = tmp_path / "nope.cfg"
        assert read_beserver_cfg(cfg) == {}

    def test_comments_and_blanks_skipped(self, tmp_path: Path):
        cfg = tmp_path / "beserver_x64.cfg"
        cfg.write_text(
            "# comment\n"
            "// another comment\n"
            "\n"
            "RConPassword secret\n"
        )
        data = read_beserver_cfg(cfg)
        assert "RConPassword" in data
        assert len(data) == 1

    def test_key_without_value(self, tmp_path: Path):
        cfg = tmp_path / "beserver_x64.cfg"
        cfg.write_text("SomeFlag\n")
        data = read_beserver_cfg(cfg)
        assert data["SomeFlag"] == ""

    def test_value_with_spaces(self, tmp_path: Path):
        cfg = tmp_path / "beserver_x64.cfg"
        cfg.write_text("RConPassword my secret pass\n")
        data = read_beserver_cfg(cfg)
        assert data["RConPassword"] == "my secret pass"


# ---------------------------------------------------------------------------
# write_beserver_cfg
# ---------------------------------------------------------------------------


class TestWriteBeserverCfg:
    def test_write_basic(self, tmp_path: Path):
        cfg = tmp_path / "beserver_x64.cfg"
        write_beserver_cfg(cfg, {"RConPassword": "test", "RConPort": "2305"})
        data = read_beserver_cfg(cfg)
        assert data["RConPassword"] == "test"
        assert data["RConPort"] == "2305"

    def test_write_creates_parent_dirs(self, tmp_path: Path):
        cfg = tmp_path / "battleye" / "beserver_x64.cfg"
        write_beserver_cfg(cfg, {"RConPassword": "pass"})
        assert cfg.exists()

    def test_round_trip(self, tmp_path: Path):
        cfg = tmp_path / "beserver_x64.cfg"
        original = {"RConPassword": "secret", "RConPort": "2305", "MaxPing": "200"}
        write_beserver_cfg(cfg, original)
        loaded = read_beserver_cfg(cfg)
        assert loaded == original

    def test_overwrites_existing(self, tmp_path: Path):
        cfg = tmp_path / "beserver_x64.cfg"
        write_beserver_cfg(cfg, {"RConPassword": "old"})
        write_beserver_cfg(cfg, {"RConPassword": "new"})
        data = read_beserver_cfg(cfg)
        assert data["RConPassword"] == "new"

    @pytest.mark.parametrize("sep", ["\n", "\r", "\x0b", "\x0c", "\x1d", "\x85", "\u2028", "\u2029", "\udc00"])
    def test_a_value_that_breaks_its_line_is_refused(self, tmp_path: Path, sep: str):
        """A newline added a BattlEye directive; U+2028 was one line to BattlEye
        and two keys to zsm (plans/68 LB-5)."""
        cfg = tmp_path / "beserver_x64.cfg"
        cfg.write_text("RConPassword old\nRConPort 2305\n")
        with pytest.raises(BeserverValueError, match="^RConPassword contains "):
            write_beserver_cfg(cfg, {"RConPassword": f"pw{sep}RConIP 0.0.0.0", "RConPort": "2305"})
        with pytest.raises(BeserverValueError, match="^A key in the BattlEye cfg contains "):
            write_beserver_cfg(cfg, {f"RConPort 2305{sep}RConIP": "0.0.0.0"})
        assert cfg.read_text() == "RConPassword old\nRConPort 2305\n"

    def test_a_tab_in_a_value_is_still_written(self, tmp_path: Path):
        cfg = tmp_path / "beserver_x64.cfg"
        write_beserver_cfg(cfg, {"RConPassword": "a\tb"})
        assert read_beserver_cfg(cfg) == {"RConPassword": "a\tb"}


class TestAWriteEditsTheFile:
    """The writer regenerated the file from the dict, so a comment line went
    and a key written twice became one line on every save."""

    ORIGINAL = (
        "// set by the host\r\n"
        "RConPassword  secret\r\n"
        "# keep loopback\r\n"
        "RestrictRCon 0\r\n"
        "MaxPing 200\r\n"
        "MaxPing 250"
    )

    def test_an_unedited_save_is_byte_identical(self, tmp_path: Path):
        cfg = tmp_path / "beserver_x64.cfg"
        cfg.write_bytes(self.ORIGINAL.encode())
        write_beserver_cfg(cfg, read_beserver_cfg(cfg))
        assert cfg.read_bytes() == self.ORIGINAL.encode()

    def test_only_the_edited_key_changes(self, tmp_path: Path):
        cfg = tmp_path / "beserver_x64.cfg"
        cfg.write_bytes(self.ORIGINAL.encode())
        data = read_beserver_cfg(cfg)
        data["RConPort"] = "2306"
        data["MaxPing"] = "300"
        del data["RestrictRCon"]
        write_beserver_cfg(cfg, data)
        assert cfg.read_bytes().decode() == (
            "// set by the host\r\n"
            "RConPassword  secret\r\n"
            "# keep loopback\r\n"
            "MaxPing 300\r\n"
            "MaxPing 300\r\n"
            "RConPort 2306"
        )

    def test_a_file_that_is_not_utf8_is_refused(self, tmp_path: Path):
        from app.shared.json_utils import NotUtf8Error

        cfg = tmp_path / "beserver_x64.cfg"
        cfg.write_bytes(b"RConPassword p\xe5ss\n")
        with pytest.raises(NotUtf8Error):
            write_beserver_cfg(cfg, {"RConPassword": "x"})
        assert cfg.read_bytes() == b"RConPassword p\xe5ss\n"


def test_the_file_is_read_by_newline_as_battleye_reads_it(tmp_path: Path):
    """A U+2028 inside a value is one line on disk: no hidden RConIP key."""
    cfg = tmp_path / "beserver_x64.cfg"
    cfg.write_text("RConPassword pw\u2028RConIP 0.0.0.0\r\nRConPort 2305\r\n", encoding="utf-8")
    assert read_beserver_cfg(cfg) == {"RConPassword": "pw\u2028RConIP 0.0.0.0", "RConPort": "2305"}
    assert check_beserver_cfg(cfg.read_text(encoding="utf-8")) is None


def test_a_raw_save_and_the_file_read_the_same_pairs(tmp_path: Path):
    """One parser for both: a byte order mark, comments, a bare key and a
    repeated key (the last one wins) read the same from text and from disk."""
    text = "﻿RConPassword old\n// note\nRestrictRCon\nRConPassword new\r\n"
    cfg = tmp_path / "beserver_x64.cfg"
    cfg.write_text(text, encoding="utf-8")
    expected = {"RConPassword": "new", "RestrictRCon": ""}
    assert parse_beserver_text(text) == expected
    assert read_beserver_cfg(cfg) == expected


# ---------------------------------------------------------------------------
# BattlEye's active copy while the server runs (measured in 2026 Q3)
# ---------------------------------------------------------------------------


class TestLiveBeserverCfg:
    def test_the_active_copy_is_what_battleye_uses(self, sample_config, battleye_running):
        assert not sample_config.beserver_cfg_path.exists()
        assert live_beserver_cfg(sample_config) == battleye_running
        assert read_beserver_cfg(live_beserver_cfg(sample_config))["RConPassword"] == "livepass"

    def test_without_an_active_copy_it_is_the_cfg(self, sample_config):
        assert live_beserver_cfg(sample_config) == sample_config.beserver_cfg_path

    def test_the_name_matches_in_any_case(self, sample_config):
        """Windows is believed to write BEServer_x64_active_<hex>.cfg (not
        measured)."""
        active = sample_config.battleye_dir / "BEServer_x64_active_ABCDEF12.cfg"
        active.write_text("RConPassword win\n")
        assert live_beserver_cfg(sample_config) == active

    def test_the_newest_active_copy_wins(self, sample_config):
        old = sample_config.battleye_dir / "beserver_x64_active_00000001.cfg"
        new = sample_config.battleye_dir / "beserver_x64_active_00000002.cfg"
        old.write_text("RConPassword old\n")
        new.write_text("RConPassword new\n")
        os.utime(old, (1_000_000, 1_000_000))
        os.utime(new, (2_000_000, 2_000_000))
        assert live_beserver_cfg(sample_config) == new

    def test_other_files_in_the_dir_are_not_it(self, sample_config):
        (sample_config.battleye_dir / "bans.txt").write_text("")
        (sample_config.battleye_dir / "beserver_x64_active_backup.txt").write_text("")
        assert live_beserver_cfg(sample_config) == sample_config.beserver_cfg_path


class TestRefuseWhileRunning:
    def test_running_refuses_and_says_why(self, sample_config, battleye_running):
        with pytest.raises(BeserverCfgInUse) as exc_info:
            refuse_while_running(sample_config)
        message = str(exc_info.value)
        assert "puts it back when the server stops" in message
        assert "Stop the server first." in message
        assert sample_config.beserver_cfg_path.name in message

    def test_stopped_lets_it_through(self, sample_config, server_stopped):
        refuse_while_running(sample_config)

    def test_it_asks_with_the_callers_config(self, sample_config, unit, monkeypatch):
        seen = []
        monkeypatch.setattr(
            service_control,
            "systemd_unit_state",
            lambda config=None: seen.append(config) or ("inactive", "dead"),
        )
        refuse_while_running(sample_config)
        assert seen == [sample_config]

    # BattlEye puts its copy back when the process exits, so everything from
    # the start of the unit to the end of a stop refuses.
    @pytest.mark.parametrize(
        ("active", "sub", "refused"),
        [
            ("active", "running", True),
            ("activating", "start", True),
            ("reloading", "reload", True),
            ("deactivating", "stop-sigterm", True),
            ("deactivating", "stop-sigkill", True),
            # Crashed and waiting out RestartSec: no process holds the file.
            ("activating", "auto-restart", False),
            ("inactive", "dead", False),
            ("failed", "failed", False),
            # No answer from systemd (unit not installed): as before.
            ("", "", False),
        ],
    )
    def test_refused_until_the_process_has_exited(self, sample_config, unit, active, sub, refused):
        unit["out"] = f"ActiveState={active}\nSubState={sub}\n" if active else ""
        if refused:
            with pytest.raises(BeserverCfgInUse):
                refuse_while_running(sample_config)
        else:
            refuse_while_running(sample_config)

    def test_a_slow_stop_is_not_running_but_is_refused(self, sample_config, unit):
        """The status tile's answer is unchanged; only the guard waits."""
        unit["out"] = "ActiveState=deactivating\nSubState=stop-sigterm\n"
        assert service_control.is_server_running(sample_config) is False
        with pytest.raises(BeserverCfgInUse) as exc_info:
            refuse_while_running(sample_config)
        assert "still stopping" in str(exc_info.value)

    def test_a_stale_active_file_does_not_block_a_stopped_server(self, sample_config, unit):
        """A copied server folder carried one; the unit decides, not the file."""
        from tests.conftest import ACTIVE_BESERVER_CFG, ACTIVE_BESERVER_TEXT

        (sample_config.battleye_dir / ACTIVE_BESERVER_CFG).write_text(ACTIVE_BESERVER_TEXT)
        unit["out"] = "ActiveState=inactive\nSubState=dead\n"
        refuse_while_running(sample_config)

    @pytest.mark.parametrize("alive", [True, False])
    def test_without_systemd_the_process_table_answers(self, sample_config, unit, monkeypatch, alive):
        """`zsm supervise` runs the server with no unit to ask."""
        monkeypatch.setattr(service_control, "has_systemd", lambda: False)
        monkeypatch.setattr(service_control, "is_dayz_process_running", lambda config=None: alive)
        unit["out"] = "ActiveState=active\nSubState=running\n"  # must not be read
        if alive:
            with pytest.raises(BeserverCfgInUse):
                refuse_while_running(sample_config)
        else:
            refuse_while_running(sample_config)

    @pytest.mark.parametrize("listed", [True, False])
    def test_windows_asks_tasklist(self, sample_config, monkeypatch, listed):
        """tasklist lists DayZServer_x64.exe until it exits, stop included."""
        monkeypatch.setattr(service_control, "IS_WINDOWS", True)
        monkeypatch.setattr(service_control, "is_server_running", lambda config=None: listed)
        if listed:
            with pytest.raises(BeserverCfgInUse):
                refuse_while_running(sample_config)
        else:
            refuse_while_running(sample_config)
