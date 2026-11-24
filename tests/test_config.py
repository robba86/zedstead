"""Tests for cli.config — DayzConfig dataclass, persistence, path properties."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.shared.config import DayzConfig
from app.shared.platform import IS_WINDOWS
from tests.conftest import disabled_names, set_test_flags


# ---------------------------------------------------------------------------
# Default creation
# ---------------------------------------------------------------------------


class TestDayzConfigDefaults:
    def test_default_fields(self):
        cfg = DayzConfig()
        assert cfg.server_dir == ""
        assert cfg.steamcmd_dir == ""
        assert cfg.steam_username == ""
        assert cfg.server_name == "DayZ Server"
        assert cfg.server_port == 2302
        assert cfg.profiles_path == "profiles"
        assert cfg.config_path == "serverDZ.cfg"
        assert cfg.backup_max_count == 5
        assert cfg.rcon_port == 2305
        assert cfg.log_cleanup_days == 0
        assert cfg.log_cleanup_keep == 0

    def test_default_server_args_contains_cpu_count(self):
        cfg = DayzConfig()
        assert "-cpuCount=2" in cfg.server_args

    def test_default_boolean_flags(self):
        cfg = DayzConfig()
        assert cfg.check_steam_login is True
        assert cfg.session_tracker_enabled is True
        assert cfg.discord_hook_start is True
        assert cfg.scheduler_enabled is True
        assert cfg.chat_log_enabled is True
        assert cfg.economy_analysis_enabled is True
        assert cfg.metrics_enabled is True
        assert cfg.config_version_enabled is True


# ---------------------------------------------------------------------------
# cpu_count property
# ---------------------------------------------------------------------------


class TestCpuCount:
    def test_getter_extracts_value(self):
        cfg = DayzConfig(server_args="-adminlog -cpuCount=4 -netlog")
        assert cfg.cpu_count == 4

    def test_getter_default_when_absent(self):
        cfg = DayzConfig(server_args="-adminlog -netlog")
        assert cfg.cpu_count == 2

    def test_setter_replaces_existing(self):
        cfg = DayzConfig(server_args="-adminlog -cpuCount=2 -netlog")
        cfg.cpu_count = 8
        assert cfg.cpu_count == 8
        assert "-cpuCount=8" in cfg.server_args
        assert "-cpuCount=2" not in cfg.server_args

    def test_setter_appends_when_absent(self):
        cfg = DayzConfig(server_args="-adminlog -netlog")
        cfg.cpu_count = 6
        assert "-cpuCount=6" in cfg.server_args


# ---------------------------------------------------------------------------
# Path properties
# ---------------------------------------------------------------------------


class TestPathProperties:
    def test_server_dir_path(self, tmp_path: Path):
        cfg = DayzConfig(server_dir=str(tmp_path))
        assert cfg.server_dir_path == tmp_path

    def test_server_cfg_path(self, tmp_path: Path):
        cfg = DayzConfig(server_dir=str(tmp_path), config_path="serverDZ.cfg")
        assert cfg.server_cfg_path == tmp_path / "serverDZ.cfg"

    def test_keys_dir(self, tmp_path: Path):
        cfg = DayzConfig(server_dir=str(tmp_path))
        assert cfg.keys_dir == tmp_path / "keys"

    def test_battleye_dir(self, tmp_path: Path):
        cfg = DayzConfig(server_dir=str(tmp_path), battleye_path="battleye")
        assert cfg.battleye_dir == tmp_path / "battleye"


# ---------------------------------------------------------------------------
# Workshop content directory detection (desktop Steam installs)
# ---------------------------------------------------------------------------


class TestWorkshopDir:
    def test_override_wins(self, tmp_path: Path):
        cfg = DayzConfig(workshop_content_dir=str(tmp_path))
        assert cfg.workshop_dir == tmp_path

    def test_default_used_when_present(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.shared.config.IS_WINDOWS", False)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        default = tmp_path / "Steam" / "steamapps" / "workshop" / "content" / "221100"
        default.mkdir(parents=True)
        cfg = DayzConfig()
        assert cfg.workshop_dir == default

    def test_detects_desktop_steam_when_default_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.shared.config.IS_WINDOWS", False)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        real = (
            tmp_path / ".local" / "share" / "Steam"
            / "steamapps" / "workshop" / "content" / "221100"
        )
        (real / "123456").mkdir(parents=True)  # one downloaded mod
        cfg = DayzConfig()
        assert cfg.workshop_dir == real

    def test_falls_back_to_default_when_nothing_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.shared.config.IS_WINDOWS", False)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        cfg = DayzConfig()
        assert cfg.workshop_dir == (
            tmp_path / "Steam" / "steamapps" / "workshop" / "content" / "221100"
        )

    def test_empty_candidate_dir_ignored(self, tmp_path, monkeypatch):
        # An existing-but-empty workshop dir must not shadow the default.
        monkeypatch.setattr("app.shared.config.IS_WINDOWS", False)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        empty = (
            tmp_path / ".local" / "share" / "Steam"
            / "steamapps" / "workshop" / "content" / "221100"
        )
        empty.mkdir(parents=True)  # exists but no mod subdirs
        cfg = DayzConfig()
        assert cfg.workshop_dir == (
            tmp_path / "Steam" / "steamapps" / "workshop" / "content" / "221100"
        )


# ---------------------------------------------------------------------------
# Tilde expansion in __post_init__
# ---------------------------------------------------------------------------


class TestTildeExpansion:
    def test_server_dir_expands_tilde(self):
        cfg = DayzConfig(server_dir="~/dayz")
        assert "~" not in cfg.server_dir
        assert cfg.server_dir.endswith("/dayz") or cfg.server_dir.endswith("\\dayz")

    def test_steamcmd_dir_expands_tilde(self):
        cfg = DayzConfig(steamcmd_dir="~/steamcmd")
        assert "~" not in cfg.steamcmd_dir

    def test_empty_paths_stay_empty(self):
        cfg = DayzConfig()
        assert cfg.server_dir == ""
        assert cfg.steamcmd_dir == ""
        assert cfg.backup_dir == ""


# ---------------------------------------------------------------------------
# Save / load round-trip
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_save_creates_file(self, sample_config):
        sample_config.save()
        from app.shared.config import CONFIG_FILE
        assert CONFIG_FILE.exists()

    def test_save_load_round_trip(self, sample_config):
        sample_config.save()
        loaded = DayzConfig.load()
        assert loaded.server_dir == sample_config.server_dir
        assert loaded.steam_username == sample_config.steam_username
        assert loaded.server_port == sample_config.server_port
        assert loaded.battleye_path == sample_config.battleye_path

    def test_save_retries_a_busy_rename_on_windows(self, sample_config, monkeypatch):
        """Windows refuses a rename over a file another handle has open, and
        the panel reads config.json from several threads while the notifier
        saves it. `atomic_write_text` tries the rename again there; `save`
        had its own copy of the write without that retry."""
        from app.shared import json_utils
        from app.shared.config import CONFIG_FILE

        real = json_utils.os.replace
        calls = []

        def flaky(src, dst):
            calls.append(dst)
            if len(calls) < 3:
                raise PermissionError("in use")
            real(src, dst)

        monkeypatch.setattr(json_utils.os, "replace", flaky)
        monkeypatch.setattr(json_utils, "_REPLACE_WAIT", 0)
        monkeypatch.setattr(json_utils, "IS_WINDOWS", True)
        sample_config.server_name = "Busy"
        sample_config.save()
        assert len(calls) == 3
        assert DayzConfig.load().server_name == "Busy"
        assert not CONFIG_FILE.with_suffix(".tmp").exists()

    def test_save_leaves_no_temp_files(self, sample_config):
        from app.shared.config import CONFIG_DIR

        sample_config.save()
        assert list(CONFIG_DIR.glob("*.tmp")) == []
        assert list(CONFIG_DIR.glob(".config-*")) == []

    @pytest.mark.skipif(IS_WINDOWS, reason="POSIX modes; Windows takes the folder's ACL")
    def test_save_is_owner_only_and_tightens_an_old_file(self, sample_config):
        """config.json holds the RCon password, the webhook URL, the Steam API
        key and the panel's password hash. It is 0600 because `save` writes a
        `mkstemp` file (always 0600) and renames it over the old one; nothing
        else says so, so a save rewritten as `write_text` would quietly make it
        0644. A file an older version left at 0644 is tightened by the next save."""
        import os
        import stat

        from app.shared.config import CONFIG_FILE

        CONFIG_FILE.write_text("{}", encoding="utf-8")
        CONFIG_FILE.chmod(0o644)
        old = os.umask(0o022)
        try:
            sample_config.save()
        finally:
            os.umask(old)
        assert stat.S_IMODE(CONFIG_FILE.stat().st_mode) == 0o600

    def test_concurrent_saves_never_corrupt(self, sample_config):
        """Atomic save: concurrent writers must never leave a partial file.

        The notification loop saves config off-thread while the main thread can
        save too. A non-atomic write_text() can interleave into corrupt JSON;
        os.replace() guarantees load() always sees a complete file.
        """
        import threading

        from app.shared.config import CONFIG_DIR

        errors: list[Exception] = []

        def hammer() -> None:
            try:
                for _ in range(25):
                    sample_config.save()
                    DayzConfig.load()  # must always parse
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=hammer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"concurrent save/load corrupted config: {errors[0]!r}"
        assert list(CONFIG_DIR.glob("*.tmp")) == []  # no leaked temp files

    def test_load_or_none_returns_none_when_missing(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "empty_config"
        config_dir.mkdir()
        config_file = config_dir / "config.json"
        monkeypatch.setattr("app.shared.config.CONFIG_FILE", config_file)
        assert DayzConfig.load_or_none() is None

    def test_load_raises_when_missing(self, tmp_path, monkeypatch):
        config_file = tmp_path / "nope" / "config.json"
        monkeypatch.setattr("app.shared.config.CONFIG_FILE", config_file)
        with pytest.raises(FileNotFoundError, match="No config found"):
            DayzConfig.load()

    def test_load_ignores_unknown_keys(self, sample_config, monkeypatch):
        """Extra keys in JSON should not crash load()."""
        sample_config.save()
        from app.shared.config import CONFIG_FILE
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        data["some_future_key"] = "whatever"
        CONFIG_FILE.write_text(json.dumps(data), encoding="utf-8")
        loaded = DayzConfig.load()
        assert loaded.server_dir == sample_config.server_dir

    def test_disabled_mods_default_empty(self):
        cfg = DayzConfig()
        assert disabled_names(cfg) == []

    def test_disabled_mods_round_trip(self, sample_config):
        set_test_flags(sample_config, disabled=["@TraderPlus", "@VPP"])
        sample_config.save()
        loaded = DayzConfig.load()
        assert disabled_names(loaded) == ["@TraderPlus", "@VPP"]

    def test_old_config_without_disabled_mods(self, sample_config):
        """Config files from before disabled_mods was added should still load."""
        sample_config.save()
        from app.shared.config import CONFIG_FILE
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        data.pop("disabled_mods", None)
        CONFIG_FILE.write_text(json.dumps(data), encoding="utf-8")
        loaded = DayzConfig.load()
        assert disabled_names(loaded) == []


# ---------------------------------------------------------------------------
# from_env_file
# ---------------------------------------------------------------------------


class TestFromEnvFile:
    def test_basic_import(self, tmp_path: Path):
        env = tmp_path / ".env"
        env.write_text(
            "STEAMCMD_DIR=/opt/steamcmd\n"
            "STEAM_USERNAME=bob\n"
            "SERVER_NAME=My Server\n"
            "SERVER_PORT=2402\n"
        )
        cfg = DayzConfig.from_env_file(env)
        assert cfg.server_dir == str(tmp_path)
        assert cfg.steamcmd_dir == "/opt/steamcmd"
        assert cfg.steam_username == "bob"
        assert cfg.server_name == "My Server"
        assert cfg.server_port == 2402

    def test_comments_and_blanks_ignored(self, tmp_path: Path):
        env = tmp_path / ".env"
        env.write_text(
            "# comment line\n"
            "\n"
            "STEAM_USERNAME=alice\n"
        )
        cfg = DayzConfig.from_env_file(env)
        assert cfg.steam_username == "alice"

    def test_missing_keys_get_defaults(self, tmp_path: Path):
        env = tmp_path / ".env"
        env.write_text("STEAM_USERNAME=charlie\n")
        cfg = DayzConfig.from_env_file(env)
        assert cfg.server_name == "DayZ Server"
        assert cfg.server_port == 2302
