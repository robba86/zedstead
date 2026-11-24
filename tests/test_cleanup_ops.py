"""Tests for app.ops.cleanup_ops — focused on storage-preserving mission reset."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.ops.cleanup_ops import reset_mission_zip
from tests.conftest import set_instance_id, write_test_mods


def _make_mission(tmp_path: Path) -> Path:
    """Build a mission dir with a populated storage_1 and some other files."""
    mission_dir = tmp_path / "mission"
    (mission_dir / "storage_1").mkdir(parents=True)
    (mission_dir / "storage_1" / "players.db").write_text("PLAYER_DATA")
    (mission_dir / "init.c").write_text("old")
    return mission_dir


@pytest.mark.parametrize("active, merged", [(True, 1), (False, 0)])
def test_a_reset_active_community_mission_gets_the_mods_xml_back(sample_config, monkeypatch, active, merged):
    """A plain Start does not merge, so the server would run its mods with none of their types."""
    from app.modmgr.mods import get_current_mission
    from app.ops.cleanup_ops import remerge_reset_mission

    calls = []
    monkeypatch.setattr("app.maps.map_ops.apply_mod_xml_to_new_mission", lambda config, log: calls.append(log))
    current = get_current_mission(sample_config.server_cfg_path)
    remerge_reset_mission(sample_config, current if active else "empty.banov", print)
    assert len(calls) == merged


class TestResetMissionZipPreservesStorage:
    def test_download_failure_does_not_destroy_storage(self, tmp_path):
        # The whole point of preserve_storage=True is to keep player data.
        # A failed download must not leave the storage destroyed.
        mission_dir = _make_mission(tmp_path)

        def boom(*args, **kwargs):
            raise RuntimeError("network down")

        with patch("app.maps.map_ops.download_and_extract_mission", side_effect=boom):
            with pytest.raises(RuntimeError, match="network down"):
                reset_mission_zip(
                    "http://x/mission.zip", "Mission", mission_dir,
                    preserve_storage=True,
                )

        # storage_1 and its contents survived the failed reset
        storage = mission_dir / "storage_1" / "players.db"
        assert storage.is_file()
        assert storage.read_text() == "PLAYER_DATA"

    def test_successful_reset_preserves_storage(self, tmp_path):
        mission_dir = _make_mission(tmp_path)

        def fake_download(zip_url, mission_path, dest, log=None, **kwargs):
            # Simulate a fresh mission extract (no storage_1 in the download).
            (dest / "init.c").write_text("new")
            return 1

        with patch(
            "app.maps.map_ops.download_and_extract_mission",
            side_effect=fake_download,
        ):
            count = reset_mission_zip(
                "http://x/mission.zip", "Mission", mission_dir,
                preserve_storage=True,
            )

        assert count == 1
        assert (mission_dir / "init.c").read_text() == "new"
        storage = mission_dir / "storage_1" / "players.db"
        assert storage.is_file()
        assert storage.read_text() == "PLAYER_DATA"

    def test_no_temp_dir_leaked(self, tmp_path):
        mission_dir = _make_mission(tmp_path)

        def fake_download(*args, **kwargs):
            return 0

        with patch(
            "app.maps.map_ops.download_and_extract_mission",
            side_effect=fake_download,
        ):
            reset_mission_zip(
                "http://x/mission.zip", "Mission", mission_dir,
                preserve_storage=True,
            )

        # No leftover zsm temp dirs from the move-aside step.
        import tempfile

        leaked = [
            p for p in Path(tempfile.gettempdir()).glob("tmp*")
            if (p / "storage_1").exists()
        ]
        assert not leaked


class TestRmtreeMountSafe:
    def test_removes_normal_directory(self, tmp_path):
        target = tmp_path / "server"
        (target / "mpmissions").mkdir(parents=True)
        (target / "serverDZ.cfg").write_text("x")

        from app.ops.cleanup_ops import rmtree_mount_safe

        rmtree_mount_safe(target)
        assert not target.exists()

    def test_mount_point_is_emptied_not_removed(self, tmp_path):
        # Docker bind mount: rmdir on the mount point itself raises EBUSY
        # after rmtree has already deleted the contents. The dir must be
        # left in place, empty, and no error raised.
        import errno
        import os

        target = tmp_path / "server"
        (target / "mpmissions").mkdir(parents=True)
        (target / "battleye").mkdir()
        (target / "serverDZ.cfg").write_text("x")
        (target / "link").symlink_to(target / "serverDZ.cfg")

        real_rmdir = os.rmdir

        def busy_rmdir(path, *args, **kwargs):
            if Path(path) == target:
                raise OSError(errno.EBUSY, "Device or resource busy", str(path))
            return real_rmdir(path, *args, **kwargs)

        from app.ops.cleanup_ops import rmtree_mount_safe

        with patch("os.rmdir", side_effect=busy_rmdir):
            rmtree_mount_safe(target)

        assert target.is_dir()
        assert list(target.iterdir()) == []

    def test_other_oserror_propagates(self, tmp_path):
        from app.ops.cleanup_ops import rmtree_mount_safe

        with pytest.raises(FileNotFoundError):
            rmtree_mount_safe(tmp_path / "does-not-exist")


class TestLeftoverModData:
    """`<profiles>/<Mod>/` dirs kept by uninstall and reported for cleanup.

    Uninstalling a mod deliberately keeps its profiles data (TraderPlus
    stock/banking, garage vehicles). These cover the reporting half.
    """

    @staticmethod
    def _profiles(sample_config) -> Path:
        d = sample_config.server_dir_path / sample_config.profiles_path
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_reports_data_of_an_uninstalled_known_mod(self, sample_config):
        from app.ops.cleanup_ops import find_leftover_mod_data

        data = self._profiles(sample_config) / "TraderPlus"
        (data / "TraderPlusConfig").mkdir(parents=True)
        (data / "TraderPlusConfig" / "TraderPlusGeneralConfig.json").write_text("{}")

        found = find_leftover_mod_data(sample_config)
        assert [(f.mod, f.name, f.files) for f in found] == [("@TraderPlus", "TraderPlus", 1)]

    def test_installed_mod_is_not_leftover(self, sample_config):
        from app.ops.cleanup_ops import find_leftover_mod_data

        (self._profiles(sample_config) / "TraderPlus").mkdir()
        (sample_config.server_dir_path / "@TraderPlus").mkdir()

        assert find_leftover_mod_data(sample_config) == []

    def test_mod_listed_in_mods_txt_is_not_leftover(self, sample_config):
        from app.ops.cleanup_ops import find_leftover_mod_data

        (self._profiles(sample_config) / "TraderPlus").mkdir()
        write_test_mods(sample_config, ['@TraderPlus'])

        assert find_leftover_mod_data(sample_config) == []

    def test_unknown_directory_is_ignored(self, sample_config):
        # The profiles dir also holds server-owned files; the delete this
        # feeds is destructive, so only known mod names are ever reported.
        from app.ops.cleanup_ops import find_leftover_mod_data

        (self._profiles(sample_config) / "SomeRandomFolder").mkdir()

        assert find_leftover_mod_data(sample_config) == []

    def test_remove_mod_data_dirs_only_touches_leftovers(self, sample_config):
        from app.ops.cleanup_ops import remove_mod_data_dirs

        profiles = self._profiles(sample_config)
        (profiles / "TraderPlus").mkdir()
        (profiles / "SomeRandomFolder").mkdir()

        removed = remove_mod_data_dirs(sample_config, ["TraderPlus", "SomeRandomFolder"])

        assert removed == ["TraderPlus"]
        assert not (profiles / "TraderPlus").exists()
        assert (profiles / "SomeRandomFolder").is_dir()

    def test_uninstall_keeps_profile_data_and_reports_it(self, sample_config):
        from app.modmgr.mod_ops import uninstall_mods

        data = self._profiles(sample_config) / "TraderPlus"
        data.mkdir()
        (data / "prices.json").write_text("{}")
        (sample_config.server_dir_path / "@TraderPlus").mkdir()
        write_test_mods(sample_config, ['@TraderPlus'])

        lines: list[str] = []
        uninstall_mods(sample_config, ["@TraderPlus"], log=lines.append)

        assert not (sample_config.server_dir_path / "@TraderPlus").exists()
        assert (data / "prices.json").is_file()
        assert any("TraderPlus" in line and "not deleted" in line for line in lines)


class TestMissionOutsideMpmissions:
    """A serverDZ.cfg whose template is not one folder name gives no mission,
    so the cleanup actions that delete under the mission act on nothing."""

    def _point_template_outside(self, sample_config) -> None:
        outside = Path(sample_config.server_dir) / "outside"
        (outside / "storage_1").mkdir(parents=True)
        (outside / "notes.backup").write_text("keep")
        sample_config.server_cfg_path.write_text(
            'class Missions { class DayZ { template = "../outside"; }; };\n'
        )

    def test_clear_xml_backups_finds_nothing(self, sample_config):
        from app.ops.cleanup_ops import find_xml_backups

        self._point_template_outside(sample_config)
        assert find_xml_backups(sample_config) is None

    def test_storage_wipe_finds_nothing(self, sample_config):
        from app.ops.cleanup_ops import find_storage

        self._point_template_outside(sample_config)
        assert find_storage(sample_config) is None


class TestStorageUnderAnotherInstanceId:
    """serverDZ.cfg's ``instanceId`` names the folder: 2 means storage_2."""

    @staticmethod
    def _worlds(mission_dir: Path, *names: str) -> None:
        for name in names:
            (mission_dir / name).mkdir(parents=True, exist_ok=True)
            (mission_dir / name / "players.db").write_text(name)

    def test_the_storage_found_is_the_one_the_server_uses(self, sample_config, tmp_server_dir, tmp_mission_dir):
        from app.ops.cleanup_ops import find_storage, get_players_db_path

        self._worlds(tmp_mission_dir, "storage_1", "storage_2")
        assert find_storage(sample_config).path == tmp_mission_dir / "storage_1"

        set_instance_id(tmp_server_dir, 2)
        assert find_storage(sample_config).path == tmp_mission_dir / "storage_2"
        assert get_players_db_path(sample_config, "empty.deerisle") == (
            tmp_server_dir / "mpmissions" / "empty.deerisle" / "storage_2" / "players.db"
        )

    def test_a_folder_under_another_id_is_not_this_servers(self, sample_config, tmp_server_dir, tmp_mission_dir):
        from app.ops.cleanup_ops import find_storage

        self._worlds(tmp_mission_dir, "storage_1")
        set_instance_id(tmp_server_dir, 2)
        assert find_storage(sample_config) is None

    def test_a_kept_reset_keeps_every_world(self, tmp_path):
        from app.ops.cleanup_ops import clear_mission_dir

        mission_dir = _make_mission(tmp_path)
        self._worlds(mission_dir, "storage_2", "storage_-1", "storage_old")

        clear_mission_dir(mission_dir, preserve_storage=True)
        # storage_old is not a name the server writes, and went before too.
        assert sorted(p.name for p in mission_dir.iterdir()) == ["storage_-1", "storage_1", "storage_2"]

        clear_mission_dir(mission_dir, preserve_storage=False)
        assert list(mission_dir.iterdir()) == []

    @pytest.mark.parametrize("download_fails", [False, True])
    def test_a_re_downloaded_mission_keeps_every_world(self, tmp_path, download_fails):
        mission_dir = _make_mission(tmp_path)
        self._worlds(mission_dir, "storage_2")

        def fake_download(zip_url, mission_path, dest, log=None, **kwargs):
            if download_fails:
                raise RuntimeError("network down")
            (dest / "init.c").write_text("new")
            return 1

        with patch("app.maps.map_ops.download_and_extract_mission", side_effect=fake_download):
            if download_fails:
                with pytest.raises(RuntimeError):
                    reset_mission_zip("http://x/m.zip", "Mission", mission_dir, preserve_storage=True)
            else:
                reset_mission_zip("http://x/m.zip", "Mission", mission_dir, preserve_storage=True)

        assert (mission_dir / "storage_1" / "players.db").read_text() == "PLAYER_DATA"
        assert (mission_dir / "storage_2" / "players.db").read_text() == "storage_2"

    def test_a_mission_with_only_another_ids_world_has_storage(self, sample_config, tmp_mission_dir):
        from app.ops.cleanup_ops import list_resettable_missions

        self._worlds(tmp_mission_dir, "storage_4")
        (mission,) = [m for m in list_resettable_missions(sample_config) if m.path == tmp_mission_dir]
        assert mission.has_storage is True




# ---------------------------------------------------------------------------
# Reinstall server never deletes more than a DayZ server
# ---------------------------------------------------------------------------


def test_reinstall_refuses_a_server_dir_that_holds_zsms_config(sample_config):
    """sample_config's server dir is tmp_path, which holds its config dir:
    the /home/zsm layout, where the reinstall's rmtree would take the
    accounts and every instance's config."""
    from app.ops.cleanup_ops import reinstall_refusal

    refusal = reinstall_refusal(sample_config)
    assert refusal and "holds Zedstead's own settings" in refusal


def test_reinstall_refuses_a_dir_with_files_but_no_server(sample_config, tmp_path):
    from app.ops.cleanup_ops import reinstall_refusal

    other = tmp_path / "srv" / "notdayz"
    other.mkdir(parents=True)
    (other / "holiday.jpg").write_bytes(b"x")
    sample_config.server_dir = str(other)
    refusal = reinstall_refusal(sample_config)
    assert refusal and "no DayZ server" in refusal


@pytest.mark.parametrize("marker", ["serverDZ.cfg", "steamapps", None])
def test_reinstall_allows_a_server_dir_or_an_empty_one(sample_config, tmp_path, marker):
    from app.ops.cleanup_ops import reinstall_refusal

    server = tmp_path / "srv" / "main"
    server.mkdir(parents=True)
    if marker == "steamapps":
        (server / "steamapps").mkdir()
    elif marker:
        (server / marker).write_text("hostname = \"x\";\n")
    sample_config.server_dir = str(server)
    assert reinstall_refusal(sample_config) is None


def test_clear_xml_backups_leaves_the_players_db_backup_alone(sample_config, tmp_mission_dir):
    """A migration keeps the only copy of the old player database as
    storage_<id>/players.db.backup, and Clear XML backups deleted it."""
    from app.ops.cleanup_ops import find_xml_backups

    (tmp_mission_dir / "db").mkdir(exist_ok=True)
    xml_backup = tmp_mission_dir / "db" / "types.xml.backup"
    xml_backup.write_text("<types/>")
    storage = tmp_mission_dir / "storage_1"
    storage.mkdir(exist_ok=True)
    (storage / "players.db.backup").write_bytes(b"sqlite")

    info = find_xml_backups(sample_config)

    assert info is not None
    assert info.files == [xml_backup]
