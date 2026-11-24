"""Tests for cli.mod_backups — mod rollback snapshots."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app.modmgr.mod_backups import (
    ModSnapshot,
    create_snapshot,
    delete_all_snapshots,
    delete_snapshot,
    list_mod_ids_with_snapshots,
    list_snapshots,
    restore_snapshot,
    rotate_snapshots,
    snapshot_mods,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def workshop(tmp_path: Path) -> Path:
    """A fake workshop directory with two mods."""
    ws = tmp_path / "workshop"
    (ws / "111111" / "addons").mkdir(parents=True)
    (ws / "111111" / "addons" / "mod.pbo").write_bytes(b"x" * 100)
    (ws / "111111" / "meta.cpp").write_text("name = ModA;", encoding="utf-8")
    (ws / "222222" / "addons").mkdir(parents=True)
    (ws / "222222" / "addons" / "mod.pbo").write_bytes(b"y" * 200)
    return ws


@pytest.fixture()
def backup_dir(tmp_path: Path) -> Path:
    return tmp_path / "backups"


@pytest.fixture()
def cfg(tmp_path: Path, monkeypatch):
    from app.shared.config import DayzConfig

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.json"
    monkeypatch.setattr("app.shared.paths.CONFIG_DIR", config_dir)
    monkeypatch.setattr("app.shared.paths.CONFIG_FILE", config_file)
    monkeypatch.setattr("app.shared.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("app.shared.config.CONFIG_FILE", config_file)

    ws = tmp_path / "workshop"
    server = tmp_path / "server"
    server.mkdir()

    c = DayzConfig(
        server_dir=str(server),
        steamcmd_dir=str(tmp_path / "steamcmd"),
        workshop_content_dir=str(ws),
        mod_backup_before_update=True,
        mod_backup_max_versions=2,
    )
    c.save()
    return c


# ---------------------------------------------------------------------------
# ModSnapshot
# ---------------------------------------------------------------------------


class TestModSnapshot:
    def test_dir_name(self):
        snap = ModSnapshot(mod_id="123", timestamp=1700000000.0)
        assert snap.dir_name == "123_1700000000000"

    def test_label(self):
        snap = ModSnapshot(mod_id="123", timestamp=1700000000.0, mod_name="TestMod", size_bytes=1024)
        assert "TestMod" in snap.label
        assert "1.0 KB" in snap.label

    def test_roundtrip(self):
        snap = ModSnapshot(mod_id="123", timestamp=1234.0, mod_name="X", size_bytes=42)
        restored = ModSnapshot.from_dict(snap.to_dict())
        assert restored.mod_id == snap.mod_id
        assert restored.timestamp == snap.timestamp


# ---------------------------------------------------------------------------
# Create snapshot
# ---------------------------------------------------------------------------


class TestCreateSnapshot:
    def test_creates_snapshot(self, workshop, backup_dir):
        snap = create_snapshot(workshop, "111111", "ModA", backup_dir=backup_dir)
        assert snap is not None
        assert snap.mod_id == "111111"
        assert snap.mod_name == "ModA"
        assert snap.size_bytes > 0

        # Verify files exist
        snap_path = backup_dir / snap.dir_name
        assert (snap_path / "meta.json").is_file()
        assert (snap_path / "mod" / "addons" / "mod.pbo").is_file()

    def test_a_link_in_the_mod_is_not_followed(self, workshop, backup_dir, tmp_path):
        """copytree's default copied the linked file, which can be anything."""
        secret = tmp_path / "secret.txt"
        secret.write_text("not the mod's", encoding="utf-8")
        (workshop / "111111" / "addons" / "link.txt").symlink_to(secret)

        snap = create_snapshot(workshop, "111111", backup_dir=backup_dir)
        copied = backup_dir / snap.dir_name / "mod"
        assert (copied / "addons" / "mod.pbo").is_file()
        assert not (copied / "addons" / "link.txt").exists()

        # Restore leaves a link out too.
        (copied / "planted.txt").symlink_to(secret)
        assert restore_snapshot(workshop, snap, backup_dir=backup_dir) is True
        assert not (workshop / "111111" / "planted.txt").exists()
        assert (workshop / "111111" / "addons" / "mod.pbo").is_file()

    def test_nonexistent_mod_returns_none(self, workshop, backup_dir):
        snap = create_snapshot(workshop, "999999", backup_dir=backup_dir)
        assert snap is None


# ---------------------------------------------------------------------------
# List snapshots
# ---------------------------------------------------------------------------


class TestListSnapshots:
    def test_empty(self, backup_dir):
        assert list_snapshots(backup_dir=backup_dir) == []

    def test_lists_all(self, workshop, backup_dir):
        create_snapshot(workshop, "111111", "ModA", backup_dir=backup_dir)
        create_snapshot(workshop, "222222", "ModB", backup_dir=backup_dir)
        snaps = list_snapshots(backup_dir=backup_dir)
        assert len(snaps) == 2

    def test_filter_by_mod_id(self, workshop, backup_dir):
        create_snapshot(workshop, "111111", "ModA", backup_dir=backup_dir)
        create_snapshot(workshop, "222222", "ModB", backup_dir=backup_dir)
        snaps = list_snapshots(mod_id="111111", backup_dir=backup_dir)
        assert len(snaps) == 1
        assert snaps[0].mod_id == "111111"

    def test_newest_first(self, workshop, backup_dir):
        create_snapshot(workshop, "111111", "ModA", backup_dir=backup_dir)
        # Manually create an older snapshot
        old = ModSnapshot(mod_id="111111", timestamp=1000.0, mod_name="ModA")
        old_dir = backup_dir / old.dir_name / "mod"
        old_dir.mkdir(parents=True)
        (backup_dir / old.dir_name / "meta.json").write_text(
            json.dumps(old.to_dict()), encoding="utf-8"
        )

        snaps = list_snapshots(mod_id="111111", backup_dir=backup_dir)
        assert len(snaps) == 2
        assert snaps[0].timestamp > snaps[1].timestamp


    @pytest.mark.parametrize(
        "meta",
        [b"[]", b'"x"', b"7", b"null", b'{"mod_id": "111111", "timestamp": "soon"}', b"\xff\xfe"],
    )
    def test_a_meta_that_is_not_a_snapshot_is_skipped(self, workshop, backup_dir, meta):
        create_snapshot(workshop, "111111", "ModA", backup_dir=backup_dir)
        bad = backup_dir / "111111_1"
        (bad / "mod").mkdir(parents=True)
        (bad / "meta.json").write_bytes(meta)

        snaps = list_snapshots(backup_dir=backup_dir)
        assert [s.mod_name for s in snaps] == ["ModA"]


class TestListModIds:
    def test_unique_ids(self, workshop, backup_dir):
        create_snapshot(workshop, "111111", "ModA", backup_dir=backup_dir)
        create_snapshot(workshop, "111111", "ModA", backup_dir=backup_dir)
        create_snapshot(workshop, "222222", "ModB", backup_dir=backup_dir)
        ids = list_mod_ids_with_snapshots(backup_dir=backup_dir)
        assert ids == ["111111", "222222"]


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


class TestRestore:
    def test_restore_replaces_current(self, workshop, backup_dir):
        snap = create_snapshot(workshop, "111111", "ModA", backup_dir=backup_dir)
        assert snap is not None

        # Modify the current mod
        (workshop / "111111" / "addons" / "mod.pbo").write_bytes(b"modified")
        (workshop / "111111" / "new_file.txt").write_text("new", encoding="utf-8")

        # Restore
        assert restore_snapshot(workshop, snap, backup_dir=backup_dir) is True

        # Original content restored
        assert (workshop / "111111" / "addons" / "mod.pbo").read_bytes() == b"x" * 100
        assert (workshop / "111111" / "meta.cpp").read_text(encoding="utf-8") == "name = ModA;"
        # New file should be gone (rmtree + copytree)
        assert not (workshop / "111111" / "new_file.txt").exists()

    def test_a_failed_copy_leaves_the_current_folder(self, workshop, backup_dir, monkeypatch):
        from app.modmgr import mod_backups

        snap = create_snapshot(workshop, "111111", "ModA", backup_dir=backup_dir)
        (workshop / "111111" / "addons" / "mod.pbo").write_bytes(b"current")

        def _half_copy(src, dst, **kw):
            Path(dst).mkdir(parents=True)
            (Path(dst) / "partial.txt").write_text("half")
            raise OSError("disk full")

        monkeypatch.setattr(mod_backups, "copytree_without_links", _half_copy)
        with pytest.raises(OSError):
            restore_snapshot(workshop, snap, backup_dir=backup_dir)

        assert (workshop / "111111" / "addons" / "mod.pbo").read_bytes() == b"current"
        assert sorted(p.name for p in workshop.iterdir()) == ["111111", "222222"]

    def test_restore_leaves_nothing_beside_the_folder(self, workshop, backup_dir):
        snap = create_snapshot(workshop, "111111", "ModA", backup_dir=backup_dir)
        assert restore_snapshot(workshop, snap, backup_dir=backup_dir) is True
        assert sorted(p.name for p in workshop.iterdir()) == ["111111", "222222"]

    def test_restore_missing_snapshot_returns_false(self, workshop, backup_dir):
        snap = ModSnapshot(mod_id="999", timestamp=0)
        assert restore_snapshot(workshop, snap, backup_dir=backup_dir) is False


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------


class TestRotation:
    def test_rotate_keeps_newest(self, workshop, backup_dir):
        # Create 4 snapshots with different timestamps (spaced apart)
        timestamps = [1000.0, 2000.0, 3000.0, 4000.0]
        for ts in timestamps:
            snap = ModSnapshot(
                mod_id="111111", timestamp=ts,
                mod_name="ModA", size_bytes=10,
            )
            snap_dir = backup_dir / snap.dir_name / "mod"
            snap_dir.mkdir(parents=True)
            (backup_dir / snap.dir_name / "meta.json").write_text(
                json.dumps(snap.to_dict()), encoding="utf-8"
            )

        deleted = rotate_snapshots("111111", max_versions=2, backup_dir=backup_dir)
        assert deleted == 2

        remaining = list_snapshots(mod_id="111111", backup_dir=backup_dir)
        assert len(remaining) == 2
        # Newest should be kept
        assert remaining[0].timestamp == 4000.0
        assert remaining[1].timestamp == 3000.0

    def test_rotate_noop_under_limit(self, workshop, backup_dir):
        create_snapshot(workshop, "111111", "ModA", backup_dir=backup_dir)
        deleted = rotate_snapshots("111111", max_versions=5, backup_dir=backup_dir)
        assert deleted == 0


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


class TestDelete:
    def test_delete_single(self, workshop, backup_dir):
        snap = create_snapshot(workshop, "111111", "ModA", backup_dir=backup_dir)
        assert snap is not None
        assert delete_snapshot(snap, backup_dir=backup_dir) is True
        assert list_snapshots(mod_id="111111", backup_dir=backup_dir) == []

    def test_delete_all_for_mod(self, workshop, backup_dir):
        # Create snapshots with distinct timestamps to avoid dir name collision
        for ts in (1000.0, 2000.0):
            snap = ModSnapshot(mod_id="111111", timestamp=ts, mod_name="ModA")
            d = backup_dir / snap.dir_name / "mod"
            d.mkdir(parents=True)
            (backup_dir / snap.dir_name / "meta.json").write_text(
                json.dumps(snap.to_dict()), encoding="utf-8"
            )
        create_snapshot(workshop, "222222", "ModB", backup_dir=backup_dir)
        deleted = delete_all_snapshots(mod_id="111111", backup_dir=backup_dir)
        assert deleted == 2
        assert len(list_snapshots(backup_dir=backup_dir)) == 1

    def test_delete_all(self, workshop, backup_dir):
        create_snapshot(workshop, "111111", "ModA", backup_dir=backup_dir)
        create_snapshot(workshop, "222222", "ModB", backup_dir=backup_dir)
        deleted = delete_all_snapshots(backup_dir=backup_dir)
        assert deleted == 2
        assert list_snapshots(backup_dir=backup_dir) == []


# ---------------------------------------------------------------------------
# snapshot_mods (integration with config)
# ---------------------------------------------------------------------------


class TestSnapshotMods:
    def test_snapshots_multiple_mods(self, cfg, workshop, monkeypatch):
        # Create mod dirs in the workshop
        (workshop / "111111" / "addons").mkdir(parents=True, exist_ok=True)
        (workshop / "111111" / "addons" / "mod.pbo").write_bytes(b"x")
        (workshop / "222222" / "addons").mkdir(parents=True, exist_ok=True)
        (workshop / "222222" / "addons" / "mod.pbo").write_bytes(b"y")

        created = snapshot_mods(
            cfg,
            ["111111", "222222"],
            mod_names={"111111": "ModA", "222222": "ModB"},
        )
        assert len(created) == 2
        # Into this instance's own directory (plans/44).
        assert (cfg.paths.mod_backups_dir / created[0].dir_name / "meta.json").is_file()

    def test_disabled_returns_empty(self, cfg, workshop):
        cfg.mod_backup_before_update = False
        cfg.save()
        created = snapshot_mods(cfg, ["111111"])
        assert created == []

    def test_skips_missing_mods(self, cfg):
        created = snapshot_mods(cfg, ["999999"])
        assert created == []
