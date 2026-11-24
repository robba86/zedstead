"""Tests for app.ops.backup_ops."""

from __future__ import annotations

from datetime import datetime
import json
import zipfile
from pathlib import Path

import pytest

from app.ops.backup_ops import (
    FullBackupInfo,
    FullBackupMetadata,
    list_backups,
    restore_full_backup,
)
from app.shared.platform import IS_WINDOWS
from tests.conftest import installed_names, set_instance_id, write_test_mods


def _write_backup(backup_dir: Path, mission: str, file_ts: str, meta_ts: str) -> None:
    """Drop a backup zip + meta sidecar pair into backup_dir."""
    base = f"dayz-backup-{mission}-{file_ts}"
    (backup_dir / f"{base}.zip").write_bytes(b"")  # must exist or list skips it
    (backup_dir / f"{base}.meta.json").write_text(
        json.dumps(
            {
                "timestamp": meta_ts,
                "mission": mission,
                "map_name": mission,
                "contents": {"players": True, "vehicles": True, "world": True},
            }
        ),
        encoding="utf-8",
    )


def test_list_backups_is_globally_newest_first(sample_config, tmp_path):
    """Backups must sort newest-first by timestamp across all missions.

    Regression: the filename is ``dayz-backup-<mission>-<ts>``, so sorting by
    filename groups by mission (variable-length) before time. The newest backup
    here belongs to the alphabetically-first mission, so a filename sort would
    push it to the end and hide it from "recent backups" previews.
    """
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    sample_config.backup_dir = str(backup_dir)

    _write_backup(backup_dir, "alpha", "20260601-000000", "2026-06-01T00:00:00")  # newest
    _write_backup(backup_dir, "zulu", "20260301-000000", "2026-03-01T00:00:00")
    _write_backup(backup_dir, "zulu", "20260101-000000", "2026-01-01T00:00:00")  # oldest

    backups = list_backups(sample_config)

    assert [b.metadata.timestamp for b in backups] == [
        "2026-06-01T00:00:00",
        "2026-03-01T00:00:00",
        "2026-01-01T00:00:00",
    ]
    # The newest backup belongs to "alpha" — it must come first, not last.
    assert backups[0].metadata.mission == "alpha"


@pytest.mark.parametrize("sidecar", [
    b"\xff\xfe not utf-8",
    b"[1, 2]",
    b'{"timestamp": 5, "server_dir": "/x", "mission": "m", "map_name": "m", "contents": {}}',
])
def test_one_unreadable_sidecar_does_not_hide_the_other_backups(sample_config, tmp_path, sidecar):
    """A sidecar that is not UTF-8, not an object or has a non-text timestamp
    raised out of list_backups (UnicodeDecodeError, TypeError), which took
    the Backups list down and failed every later create_backup at its
    rotation, after the zip was written."""
    from app.ops.backup_ops import list_full_backups

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    sample_config.backup_dir = str(backup_dir)
    _write_backup(backup_dir, "dayzOffline.chernarusplus", "20260101-120000", "2026-01-01T12:00:00+00:00")
    for base in ("dayz-backup-bad-20260102-120000", "dayz-server-backup-20260102-120000"):
        (backup_dir / f"{base}.zip").write_bytes(b"")
        (backup_dir / f"{base}.meta.json").write_bytes(sidecar)

    assert [b.metadata.mission for b in list_backups(sample_config)] == ["dayzOffline.chernarusplus"]
    assert list_full_backups(sample_config) == []


def test_list_backups_filters_by_mission(sample_config, tmp_path):
    """The mission filter returns only that mission's backups, newest-first."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    sample_config.backup_dir = str(backup_dir)

    _write_backup(backup_dir, "alpha", "20260601-000000", "2026-06-01T00:00:00")
    _write_backup(backup_dir, "zulu", "20260301-000000", "2026-03-01T00:00:00")
    _write_backup(backup_dir, "zulu", "20260101-000000", "2026-01-01T00:00:00")

    zulu = list_backups(sample_config, mission="zulu")

    assert [b.metadata.mission for b in zulu] == ["zulu", "zulu"]
    assert [b.metadata.timestamp for b in zulu] == [
        "2026-03-01T00:00:00",
        "2026-01-01T00:00:00",
    ]


def _full_backup_with_entry(tmp_path: Path, arc_name: str) -> FullBackupInfo:
    """Build a one-entry full-backup zip whose member is named *arc_name*."""
    zip_path = tmp_path / "full.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(arc_name, b"pwned")
    meta = FullBackupMetadata(
        timestamp="2026-06-01T00:00:00",
        server_dir="srv",
        include_mods=False,
        include_logs=False,
        file_count=1,
        total_size=5,
        compressed_size=5,
    )
    return FullBackupInfo(zip_path=zip_path, meta_path=tmp_path / "x.meta.json", metadata=meta)


def test_restore_full_backup_rejects_zip_slip(sample_config, tmp_path):
    """A full-backup entry escaping server_dir (../) must not be written outside it."""
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    sample_config.server_dir = str(server_dir)

    outside = tmp_path / "evil.txt"
    backup = _full_backup_with_entry(tmp_path, "../evil.txt")

    assert restore_full_backup(sample_config, backup) == 0
    assert not outside.exists()


def test_restore_full_backup_skips_an_unsafe_entry_and_restores_the_rest(sample_config, tmp_path):
    """plans/68 B11: one entry safe_extract_path refuses is named in the log
    and skipped; the entries after it are still restored."""
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    sample_config.server_dir = str(server_dir)
    backup = _full_backup_with_entry(tmp_path, "mpmissions/x/types.xml")
    with zipfile.ZipFile(backup.zip_path, "a") as zf:
        zf.writestr("../evil.txt", b"pwned")
        zf.writestr("/abs-evil.txt", b"pwned")
        zf.writestr("serverDZ.cfg", b"cfg")
    lines: list[str] = []

    count = restore_full_backup(sample_config, backup, log=lines.append)

    assert count == 2
    assert (server_dir / "mpmissions" / "x" / "types.xml").read_bytes() == b"pwned"
    assert (server_dir / "serverDZ.cfg").read_bytes() == b"cfg"
    assert not (tmp_path / "evil.txt").exists()
    assert "  - ../evil.txt (unsafe path)" in lines
    assert lines[-1] == "Restored 2 file(s)."


def test_create_backup_writes_parseable_meta(sample_config, tmp_server_dir, tmp_path):
    """create_backup writes a .meta.json that exists and parses correctly."""
    from app.ops.backup_ops import BackupContents, create_backup

    # Set up storage_1 with at least one file so create_backup has something to zip
    mission_dir = tmp_server_dir / "mpmissions" / "dayzOffline.chernarusplus"
    mission_dir.mkdir(parents=True, exist_ok=True)
    storage = mission_dir / "storage_1"
    storage.mkdir()
    (storage / "players.db").write_bytes(b"fakedb")

    # Point backup_dir at a temp location
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    sample_config.backup_dir = str(backup_dir)

    zip_path = create_backup(sample_config, BackupContents())
    assert zip_path is not None and zip_path.exists()

    meta_path = zip_path.with_name(zip_path.name.replace(".zip", ".meta.json"))
    assert meta_path.exists()

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["mission"] == "dayzOffline.chernarusplus"
    assert "contents" in meta
    # With its UTC offset, so a browser in another zone shows the right moment.
    assert datetime.fromisoformat(meta["timestamp"]).utcoffset() is not None
    (listed,) = list_backups(sample_config)
    assert listed.metadata.timestamp == meta["timestamp"]


def test_a_full_backup_carries_the_mod_list_and_restores_it(sample_config, tmp_path):
    """mods.json lives in the config dir, not the server dir, so the backup
    packs it under _zsm/ and the restore puts it back (plans/77)."""
    from app.ops.backup_ops import MODS_ARC_NAME, FullBackupOptions, create_full_backup

    sample_config.backup_dir = str(tmp_path / "backups")
    write_test_mods(sample_config, ["@CF", "@Made-Up"])
    zip_path = create_full_backup(sample_config, FullBackupOptions())
    with zipfile.ZipFile(zip_path) as zf:
        assert MODS_ARC_NAME in zf.namelist()

    write_test_mods(sample_config, [])
    meta = FullBackupMetadata(
        timestamp="2026-06-01T00:00:00", server_dir="srv", include_mods=False,
        include_logs=False, file_count=1, total_size=5, compressed_size=5,
    )
    lines: list[str] = []
    restore_full_backup(sample_config, FullBackupInfo(zip_path, tmp_path / "x.meta.json", meta), log=lines.append)
    assert installed_names(sample_config) == ["@CF", "@Made-Up"]


def test_restore_full_backup_extracts_normal_entry(sample_config, tmp_path):
    """A normal entry restores under server_dir."""
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    sample_config.server_dir = str(server_dir)

    backup = _full_backup_with_entry(tmp_path, "mpmissions/x/types.xml")
    count = restore_full_backup(sample_config, backup)

    assert count == 1
    assert (server_dir / "mpmissions" / "x" / "types.xml").read_bytes() == b"pwned"


# ---------------------------------------------------------------------------
# Mod data (test pass 1, F58): Expansion, CF and the TraderPlus databases
# ---------------------------------------------------------------------------


def _mod_data_server(sample_config, tmp_server_dir, tmp_path):
    from app.ops.backup_ops import profiles_dir

    sample_config.server_dir = str(tmp_server_dir)
    sample_config.backup_dir = str(tmp_path / "backups")
    storage = tmp_server_dir / "mpmissions" / "dayzOffline.chernarusplus" / "storage_1"
    for rel, data in {
        "players.db": b"players",
        "spawnpoints.bin": b"spawns",
        "data/vehicles.bin": b"vehicles",
        "data/dynamic_000.bin": b"world",
        "data/dynamic_000.001": b"engine rotation",
        "data/types.bin": b"cache",
        "backup/000/players.db": b"engine backup",
        "expansion/garage/a.json": b"expansion",
        "communityframework/modstorageplayers.bin": b"cf",
        "communityframework/modstorageplayers.lock": b"",
    }.items():
        (storage / rel).parent.mkdir(parents=True, exist_ok=True)
        (storage / rel).write_bytes(data)
    tp = profiles_dir(sample_config) / "TraderPlus"
    for rel, data in {
        "TraderPlusBankDatabase/Account_1.json": b"bank",
        "TraderPlusGarageDatabase/1/2.json": b"garage",
        "TraderPlusDatabase/ID_0/Tools.json": b"stock",
        "TraderPlusConfig/TraderPlusGeneralConfig.json": b"config",
    }.items():
        (tp / rel).parent.mkdir(parents=True, exist_ok=True)
        (tp / rel).write_bytes(data)
    return storage, tp


def _names(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        return set(zf.namelist())


OLD_PARTS = {"players.db", "spawnpoints.bin", "data/vehicles.bin", "data/dynamic_000.bin"}
MOD_DATA = {
    "expansion/garage/a.json",
    "communityframework/modstorageplayers.bin",
    "profiles/TraderPlus/TraderPlusBankDatabase/Account_1.json",
    "profiles/TraderPlus/TraderPlusGarageDatabase/1/2.json",
    "profiles/TraderPlus/TraderPlusDatabase/ID_0/Tools.json",
}


def test_mod_data_is_in_a_default_backup(sample_config, tmp_server_dir, tmp_path):
    from app.ops.backup_ops import BackupContents, create_backup

    _mod_data_server(sample_config, tmp_server_dir, tmp_path)
    zip_path = create_backup(sample_config, BackupContents())

    # Not the cache, not the engine's own rotations, not a lock file, and not
    # TraderPlus' config — only persistence.
    assert _names(zip_path) == OLD_PARTS | MOD_DATA
    meta = json.loads(zip_path.with_name(zip_path.stem + ".meta.json").read_text())
    assert meta["contents"]["mod_data"] is True


def test_no_mod_data_is_the_three_old_parts(sample_config, tmp_server_dir, tmp_path):
    from app.ops.backup_ops import BackupContents, create_backup

    _mod_data_server(sample_config, tmp_server_dir, tmp_path)
    zip_path = create_backup(sample_config, BackupContents(mod_data=False))

    assert _names(zip_path) == OLD_PARTS
    meta = json.loads(zip_path.with_name(zip_path.stem + ".meta.json").read_text())
    assert meta["contents"]["mod_data"] is False


def test_mod_data_round_trip(sample_config, tmp_server_dir, tmp_path):
    from app.ops.backup_ops import BackupContents, create_backup, list_backups, restore_backup

    storage, tp = _mod_data_server(sample_config, tmp_server_dir, tmp_path)
    create_backup(sample_config, BackupContents())
    (tp / "TraderPlusBankDatabase/Account_1.json").write_bytes(b"spent")
    (tp / "TraderPlusGarageDatabase/1/2.json").unlink()
    (tp / "TraderPlusDatabase/ID_0/Tools.json").write_bytes(b"sold out")
    (storage / "expansion/garage/a.json").write_bytes(b"changed")

    backup = list_backups(sample_config)[0]
    lines: list[str] = []
    restore_backup(sample_config, backup, BackupContents(), log=lines.append)

    assert (tp / "TraderPlusBankDatabase/Account_1.json").read_bytes() == b"bank"
    assert (tp / "TraderPlusGarageDatabase/1/2.json").read_bytes() == b"garage"
    assert (tp / "TraderPlusDatabase/ID_0/Tools.json").read_bytes() == b"stock"
    assert (storage / "expansion/garage/a.json").read_bytes() == b"expansion"
    assert "  + profiles/TraderPlus/TraderPlusBankDatabase/Account_1.json" in lines


def test_restore_without_mod_data_leaves_it(sample_config, tmp_server_dir, tmp_path):
    from app.ops.backup_ops import BackupContents, create_backup, list_backups, restore_backup

    storage, tp = _mod_data_server(sample_config, tmp_server_dir, tmp_path)
    create_backup(sample_config, BackupContents())
    (tp / "TraderPlusBankDatabase/Account_1.json").write_bytes(b"spent")
    (storage / "players.db").write_bytes(b"new players")

    restore_backup(
        sample_config, list_backups(sample_config)[0], BackupContents(mod_data=False)
    )

    assert (tp / "TraderPlusBankDatabase/Account_1.json").read_bytes() == b"spent"
    assert (storage / "players.db").read_bytes() == b"players"


def _quick_backup_with(tmp_path, entries: dict[str, bytes]):
    from app.ops.backup_ops import BackupInfo, BackupMetadata

    zip_path = tmp_path / "dayz-backup-x-20260101-000000.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    meta = BackupMetadata(
        timestamp="2026-01-01T00:00:00", mission="dayzOffline.chernarusplus",
        map_name="Chernarus", contents={"players": True, "vehicles": True, "world": True},
        file_sizes={}, total_size=0,
    )
    return BackupInfo(zip_path=zip_path, meta_path=tmp_path / "x.meta.json", metadata=meta)


def test_an_old_backup_restores_as_before(sample_config, tmp_server_dir, tmp_path):
    """No `mod_data` key and no mod entries: players, vehicles and world go
    back exactly where they always did, whatever the mod_data switch says."""
    from app.ops.backup_ops import BackupContents, format_backup_label, restore_backup

    storage, _ = _mod_data_server(sample_config, tmp_server_dir, tmp_path)
    backup = _quick_backup_with(tmp_path, {
        "players.db": b"old players",
        "data/vehicles.bin": b"old vehicles",
        "data/dynamic_000.bin": b"old world",
        "spawnpoints.bin": b"old spawns",
    })

    for contents in (BackupContents(), BackupContents(mod_data=False)):
        assert restore_backup(sample_config, backup, contents) == 4
    assert (storage / "players.db").read_bytes() == b"old players"
    assert (storage / "data/dynamic_000.bin").read_bytes() == b"old world"
    assert "[P+V+W]" in format_backup_label(backup)

    assert restore_backup(sample_config, backup, BackupContents(world=False)) == 2
    assert restore_backup(sample_config, backup, BackupContents(players=False)) == 3


@pytest.mark.parametrize("name", [
    "../outside.bin",
    "expansion/../../../outside.bin",
    "profiles/TraderPlus/TraderPlusBankDatabase/../TraderPlusConfig/x.json",
    "profiles/TraderPlus/TraderPlusConfig/x.json",
    "profiles/serverDZ.cfg",
])
def test_a_crafted_entry_is_refused(sample_config, tmp_server_dir, tmp_path, name):
    from app.ops.backup_ops import BackupContents, restore_backup

    _, tp = _mod_data_server(sample_config, tmp_server_dir, tmp_path)
    before = (tp / "TraderPlusConfig/TraderPlusGeneralConfig.json").read_bytes()
    backup = _quick_backup_with(tmp_path, {name: b"pwned", "players.db": b"ok"})
    lines: list[str] = []

    assert restore_backup(sample_config, backup, BackupContents(), log=lines.append) == 1
    assert any(line.startswith(f"  - {name} (") for line in lines), lines
    assert not list(tmp_path.rglob("outside.bin"))
    assert not (tp / "TraderPlusConfig/x.json").exists()
    assert (tp / "TraderPlusConfig/TraderPlusGeneralConfig.json").read_bytes() == before


def test_the_cli_restore_refuses_while_the_server_runs(sample_config, tmp_path, monkeypatch):
    """The web route already refused; the CLI did not, and a running server
    writes its persistence back over the restore on shutdown."""
    from click.testing import CliRunner

    from app.commands.backup import backup_group
    from app.server import service_control

    sample_config.save()
    monkeypatch.setattr(service_control, "is_server_running", lambda config=None: True)
    zip_path = tmp_path / "b.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("players.db", b"x")

    result = CliRunner().invoke(backup_group, ["restore", str(zip_path)])

    assert result.exit_code == 1
    assert "Stop it first" in result.output


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX modes; Windows takes the folder's ACL")
def test_a_full_backup_is_readable_by_its_owner_only(sample_config, tmp_path):
    """A full backup carries config.json (RCon password, webhook URL, Steam
    API key, password hash) and the server's cfg passwords; under the usual
    umask 022 the zip came out 0644."""
    import os
    import stat

    from app.ops.backup_ops import APP_CONFIG_ARC_NAME, FullBackupOptions, create_full_backup

    sample_config.backup_dir = str(tmp_path / "backups")
    sample_config.rcon_password = "SUPERSECRET"
    sample_config.save()
    old = os.umask(0o022)
    try:
        zip_path = create_full_backup(sample_config, FullBackupOptions())
    finally:
        os.umask(old)

    assert zip_path is not None
    with zipfile.ZipFile(zip_path) as zf:
        assert APP_CONFIG_ARC_NAME in zf.namelist()
    assert stat.S_IMODE(zip_path.stat().st_mode) == 0o600


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX modes; Windows takes the folder's ACL")
def test_a_quick_backup_is_readable_by_its_owner_only(sample_config, tmp_server_dir, tmp_path):
    """A quick backup carries player characters and mod databases (TraderPlus
    banking); under the usual umask 022 the zip came out 0644 (plans/68 SEC-9)."""
    import os
    import stat

    from app.ops.backup_ops import BackupContents, create_backup

    _mod_data_server(sample_config, tmp_server_dir, tmp_path)
    old = os.umask(0o022)
    try:
        zip_path = create_backup(sample_config, BackupContents())
    finally:
        os.umask(old)

    assert zip_path is not None
    assert _names(zip_path) == OLD_PARTS | MOD_DATA
    assert stat.S_IMODE(zip_path.stat().st_mode) == 0o600


# ── instanceId: the world is in storage_<instanceId> ─────────────────────


def _moved_from_a_host(sample_config, tmp_server_dir, tmp_path):
    """A server whose cfg came from a host with ``instanceId = 2``: the world
    is in storage_2, beside a storage_1 nothing has written since."""
    stale, _tp = _mod_data_server(sample_config, tmp_server_dir, tmp_path)
    set_instance_id(tmp_server_dir, 2)
    live = stale.with_name("storage_2")
    (live / "data").mkdir(parents=True)
    (live / "players.db").write_bytes(b"live players")
    (live / "data" / "dynamic_000.bin").write_bytes(b"live world")
    return stale, live


def test_a_backup_takes_the_folder_instance_id_names(sample_config, tmp_server_dir, tmp_path):
    from app.ops.backup_ops import BackupContents, create_backup

    _moved_from_a_host(sample_config, tmp_server_dir, tmp_path)
    zip_path = create_backup(sample_config, BackupContents(mod_data=False))

    with zipfile.ZipFile(zip_path) as zf:
        assert set(zf.namelist()) == {"players.db", "data/dynamic_000.bin"}
        assert zf.read("players.db") == b"live players"


def test_a_restore_goes_back_to_that_folder(sample_config, tmp_server_dir, tmp_path):
    from app.ops.backup_ops import BackupContents, create_backup, list_backups, restore_backup

    stale, live = _moved_from_a_host(sample_config, tmp_server_dir, tmp_path)
    create_backup(sample_config, BackupContents(mod_data=False))
    (live / "players.db").write_bytes(b"after a bad night")

    restore_backup(sample_config, list_backups(sample_config)[0], BackupContents())

    assert (live / "players.db").read_bytes() == b"live players"
    assert (stale / "players.db").read_bytes() == b"players"


def test_a_restore_creates_the_folder_instance_id_names(sample_config, tmp_server_dir, tmp_path):
    import shutil

    from app.ops.backup_ops import BackupContents, create_backup, list_backups, restore_backup

    stale, live = _moved_from_a_host(sample_config, tmp_server_dir, tmp_path)
    create_backup(sample_config, BackupContents(mod_data=False))
    shutil.rmtree(live)
    shutil.rmtree(stale)

    restore_backup(sample_config, list_backups(sample_config)[0], BackupContents())

    assert (live / "players.db").read_bytes() == b"live players"
    assert not stale.exists()


def test_no_folder_for_this_instance_id_is_said_by_name(sample_config, tmp_server_dir, tmp_path):
    from app.ops.backup_ops import BackupContents, create_backup

    _mod_data_server(sample_config, tmp_server_dir, tmp_path)  # storage_1 only
    set_instance_id(tmp_server_dir, 2)
    lines: list[str] = []

    assert create_backup(sample_config, BackupContents(), log=lines.append) is None
    assert lines == ["No storage_2 found for current mission."]


def test_rotation_names_each_backup_it_deletes(sample_config, tmp_server_dir, tmp_path, monkeypatch):
    """backup_max_count 1 deleted the "pre-wipe
    auto-backup" without a word in the job log."""
    import itertools

    from app.ops import backup_ops

    mission_dir = tmp_server_dir / "mpmissions" / "dayzOffline.chernarusplus"
    mission_dir.mkdir(parents=True, exist_ok=True)
    (mission_dir / "storage_1").mkdir()
    (mission_dir / "storage_1" / "players.db").write_bytes(b"db")
    sample_config.backup_dir = str(tmp_path / "backups")
    sample_config.backup_max_count = 1
    stamps = itertools.count(1)
    real = backup_ops.datetime

    class _Clock(real):  # one second apart, so the two names differ
        @classmethod
        def now(cls, tz=None):
            return real(2026, 10, 16, 21, 0, next(stamps), tzinfo=tz)

    monkeypatch.setattr(backup_ops, "datetime", _Clock)
    first = backup_ops.create_backup(sample_config, backup_ops.BackupContents(), "pre-wipe auto-backup")
    lines: list[str] = []
    backup_ops.create_backup(sample_config, backup_ops.BackupContents(), "night-retention-1", lines.append)
    assert not first.exists()
    assert f"Removed {first.stem} (pre-wipe auto-backup): older than the newest 1 (backup_max_count)" in lines


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX modes; Windows takes the folder's ACL")
@pytest.mark.parametrize("existing", [False, True])
def test_the_restored_app_config_is_readable_by_its_owner_only(tmp_path, existing):
    """config.json holds the RCon password, the webhook URL and the panel's
    password hash: extract_app_config wrote it with write_bytes, so a new file
    came out 0644 under umask 022 (and a crash mid-write left it truncated)."""
    import os
    import stat

    from app.ops.backup_ops import APP_CONFIG_ARC_NAME, extract_app_config

    zip_path = tmp_path / "backup.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(APP_CONFIG_ARC_NAME, '{"rcon_password": "SUPERSECRET"}')
    config_file = tmp_path / "instances" / "main" / "config.json"
    if existing:
        config_file.parent.mkdir(parents=True)
        config_file.write_text("{}", encoding="utf-8")
        config_file.chmod(0o644)
    old = os.umask(0o022)
    try:
        extract_app_config(zip_path, config_file)
    finally:
        os.umask(old)

    assert json.loads(config_file.read_text(encoding="utf-8")) == {"rcon_password": "SUPERSECRET"}
    assert stat.S_IMODE(config_file.stat().st_mode) == 0o600


def _same_second(monkeypatch):
    """A clock that stays in one second and moves only its microseconds."""
    import itertools

    from app.ops import backup_ops

    ticks = itertools.count(1)
    real = backup_ops.datetime

    class _Clock(real):
        @classmethod
        def now(cls, tz=None):
            return real(2026, 10, 16, 21, 0, 0, next(ticks), tzinfo=tz)

    monkeypatch.setattr(backup_ops, "datetime", _Clock)


def test_two_quick_backups_in_one_second_both_survive(sample_config, tmp_server_dir, tmp_path, monkeypatch):
    from app.ops import backup_ops

    mission_dir = tmp_server_dir / "mpmissions" / "dayzOffline.chernarusplus"
    mission_dir.mkdir(parents=True, exist_ok=True)
    (mission_dir / "storage_1").mkdir()
    (mission_dir / "storage_1" / "players.db").write_bytes(b"db")
    sample_config.backup_dir = str(tmp_path / "backups")
    sample_config.backup_max_count = 0
    _same_second(monkeypatch)

    first = backup_ops.create_backup(sample_config, backup_ops.BackupContents(), "first")
    second = backup_ops.create_backup(sample_config, backup_ops.BackupContents(), "second")

    assert first.name == "dayz-backup-dayzOffline.chernarusplus-20261016-210000.zip"
    assert second != first and first.exists() and second.exists()
    assert [b.metadata.note for b in list_backups(sample_config)] == ["second", "first"]

    sample_config.backup_max_count = 1
    assert backup_ops.rotate_backups(sample_config, "dayzOffline.chernarusplus") == 1
    assert not first.exists() and second.exists()


def test_two_full_backups_in_one_second_both_survive(sample_config, tmp_path, monkeypatch):
    from app.ops import backup_ops

    sample_config.backup_dir = str(tmp_path / "backups")
    sample_config.backup_max_count = 0
    _same_second(monkeypatch)

    first = backup_ops.create_full_backup(sample_config, backup_ops.FullBackupOptions(note="first"))
    second = backup_ops.create_full_backup(sample_config, backup_ops.FullBackupOptions(note="second"))

    assert first.name == "dayz-server-backup-20261016-210000.zip"
    assert second != first and first.exists() and second.exists()
    assert [b.metadata.note for b in backup_ops.list_full_backups(sample_config)] == ["second", "first"]

    sample_config.backup_max_count = 1
    assert backup_ops.rotate_full_backups(sample_config) == 1
    assert not first.exists() and second.exists()


def test_the_world_part_holds_every_engine_file_in_data(tmp_path):
    """The pre-wipe backup held only dynamic_*.bin, so a restore could not
    undo a wipe of doors and buildings (browser test 2026-11-17, Q2)."""
    from app.ops.backup_ops import BackupContents, collect_backup_files

    storage = tmp_path / "storage_1"
    for rel in (
        "data/dynamic_000.bin", "data/building_000.bin", "data/building.bin", "data/doors.bin",
        "data/zombies.bin", "data/animals.bin", "data/vehicles.bin", "data/types.bin",
        "data/events.bin", "spawnpoints.bin", "players.db",
    ):
        (storage / rel).parent.mkdir(parents=True, exist_ok=True)
        (storage / rel).write_bytes(b"x")

    world = collect_backup_files(storage, BackupContents(players=False, vehicles=False, world=True))
    assert set(world) == {
        "data/dynamic_000.bin", "data/building_000.bin", "data/building.bin", "data/doors.bin",
        "data/zombies.bin", "data/animals.bin", "spawnpoints.bin",
    }
    vehicles = collect_backup_files(storage, BackupContents(players=False, vehicles=True, world=False))
    assert set(vehicles) == {"data/vehicles.bin"}
