"""Tests for /api/cleanup*, /api/maintenance* (app/web/routers/cleanup.py)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import set_instance_id

PASSWORD = "testpass123"


@pytest.fixture()
def client(sample_config):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    app = server.create_app(sample_config)
    tclient = TestClient(app)
    tclient.post("/api/auth/login", json={"password": PASSWORD})
    return tclient


@pytest.fixture()
def app_and_config(sample_config):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    return server.create_app(sample_config)


def _login(client: TestClient) -> None:
    resp = client.post("/api/auth/login", json={"password": PASSWORD})
    assert resp.status_code == 200


def _poll_job(client: TestClient, job_id: str, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] not in ("pending", "running"):
            return body
        if time.monotonic() > deadline:
            raise AssertionError(f"job {job_id} did not finish in time: {body}")
        time.sleep(0.02)


def test_cleanup_requires_session(sample_config):
    from app.web import server

    app = server.create_app(sample_config)
    resp = TestClient(app).get("/api/cleanup/overview")
    assert resp.status_code == 401


def test_cleanup_overview_shape_when_empty(client):
    resp = client.get("/api/cleanup/overview")
    assert resp.status_code == 200
    body = resp.json()
    for key in (
        "storage",
        "log_files",
        "workshop_mods",
        "xml_backups",
        "resettable_missions",
        "leftover_mod_data",
    ):
        assert key in body


def test_cleanup_mod_data_requires_confirm(client):
    resp = client.post("/api/cleanup/mod-data", json={"confirm": False})
    assert resp.status_code == 422


def test_cleanup_mod_data_lists_and_deletes(client, sample_config):
    profiles = sample_config.server_dir_path / sample_config.profiles_path
    (profiles / "TraderPlus").mkdir(parents=True, exist_ok=True)
    (profiles / "TraderPlus" / "prices.json").write_text("{}")

    body = client.get("/api/cleanup/overview").json()
    assert [d["mod"] for d in body["leftover_mod_data"]] == ["@TraderPlus"]

    resp = client.post("/api/cleanup/mod-data", json={"confirm": True, "names": ["TraderPlus"]})
    assert resp.status_code == 200
    assert resp.json() == {"removed": ["TraderPlus"]}
    assert not (profiles / "TraderPlus").exists()


def test_cleanup_mod_data_ignores_unlisted_names(client, sample_config):
    # A name that isn't a current leftover must never be joined onto the
    # profiles path — the server-owned files there are not deletable here.
    profiles = sample_config.server_dir_path / sample_config.profiles_path
    (profiles / "SomeRandomFolder").mkdir(parents=True, exist_ok=True)

    resp = client.post(
        "/api/cleanup/mod-data", json={"confirm": True, "names": ["SomeRandomFolder", "../.."]}
    )
    assert resp.status_code == 200
    assert resp.json() == {"removed": []}
    assert (profiles / "SomeRandomFolder").is_dir()


def test_cleanup_logs_requires_confirm(client):
    resp = client.post("/api/cleanup/logs", json={"confirm": False})
    assert resp.status_code == 422


def test_cleanup_logs_deletes_files(client, sample_config, server_stopped):
    profiles = sample_config.server_dir_path / sample_config.profiles_path
    (profiles / "server.RPT").write_text("log data")
    resp = client.post("/api/cleanup/logs", json={"confirm": True})
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 1, "kept": 0}
    assert not (profiles / "server.RPT").exists()


def test_clear_logs_takes_the_adm_files_and_keeps_error_log(client, sample_config, server_stopped):
    """Clear logs left every .ADM file and deleted
    error.log, which the automatic cleanup keeps."""
    server = sample_config.server_dir_path
    profiles = server / sample_config.profiles_path
    for name in ("DayZServer_night.RPT", "DayZServer_night.ADM", "script_night.log", "night.mdmp"):
        (profiles / name).write_text("x")
    (profiles / "error.log").write_text("kept")
    (server / "filesystem.log").write_text("root log")
    resp = client.post("/api/cleanup/logs", json={"confirm": True})
    assert resp.json() == {"deleted": 5, "kept": 0}
    assert sorted(p.name for p in profiles.iterdir() if p.is_file() and p.suffix != ".cfg") == ["error.log"]
    assert not (server / "filesystem.log").exists()


def test_clear_logs_keeps_the_logs_a_running_server_writes(client, sample_config, battleye_running):
    """The live .RPT and .ADM were deleted under the server: a 500 halfway on
    Windows, no current log for the readers on Linux."""
    import os

    profiles = sample_config.server_dir_path / sample_config.profiles_path
    for age, name in ((300, "old.RPT"), (0, "new.RPT"), (300, "old.ADM"), (0, "new.ADM")):
        path = profiles / name
        path.write_text("x")
        stamp = path.stat().st_mtime - age
        os.utime(path, (stamp, stamp))
    resp = client.post("/api/cleanup/logs", json={"confirm": True})
    assert resp.json() == {"deleted": 2, "kept": 2}
    assert sorted(p.name for p in profiles.iterdir() if p.suffix in (".RPT", ".ADM")) == ["new.ADM", "new.RPT"]


def test_a_log_that_cannot_be_deleted_is_kept_not_a_500(client, sample_config, server_stopped, monkeypatch):
    from pathlib import Path

    profiles = sample_config.server_dir_path / sample_config.profiles_path
    (profiles / "locked.RPT").write_text("x")
    (profiles / "free.log").write_text("x")
    real_unlink = Path.unlink

    def _unlink(self, *a, **k):
        if self.name == "locked.RPT":
            raise PermissionError("in use")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", _unlink)
    resp = client.post("/api/cleanup/logs", json={"confirm": True})
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 1, "kept": 1}


def test_cleanup_xml_backups_deletes_files(client, monkeypatch):
    from app.ops.cleanup_ops import BackupFilesInfo

    fake_files = []
    monkeypatch.setattr(
        "app.ops.cleanup_ops.find_xml_backups",
        lambda config: BackupFilesInfo(files=fake_files, total_size=0, mission="m", mission_dir=Path("/tmp")),
    )
    monkeypatch.setattr("app.ops.cleanup_ops.delete_backup_files", lambda files: len(files))
    resp = client.post("/api/cleanup/xml-backups", json={"confirm": True})
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 0}


def test_cleanup_workshop_job(app_and_config, monkeypatch, tmp_path):
    from app.ops.cleanup_ops import WorkshopModInfo

    mod_dir = tmp_path / "123456"
    mod_dir.mkdir()
    monkeypatch.setattr(
        "app.ops.cleanup_ops.list_workshop_mods",
        lambda config: [WorkshopModInfo(path=mod_dir, workshop_id="123456", mod_name="CF")],
    )

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/cleanup/workshop", json={"confirm": True})
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])
        assert body["status"] == "success", body
        assert body["result"]["removed"] == 1
        assert not mod_dir.exists()


def test_cleanup_storage_requires_confirm(client):
    resp = client.post("/api/cleanup/storage", json={"confirm": False})
    assert resp.status_code == 422


def test_cleanup_storage_409_when_running(client, monkeypatch):
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: True)
    resp = client.post("/api/cleanup/storage", json={"confirm": True})
    assert resp.status_code == 409


def test_cleanup_storage_job(app_and_config, tmp_mission_dir, monkeypatch):
    storage = tmp_mission_dir / "storage_1"
    storage.mkdir()
    (storage / "players.db").write_text("data")
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/cleanup/storage", json={"confirm": True, "backup_before": False})
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])
        assert body["status"] == "success", body
        assert not storage.exists()


def test_the_wipe_takes_the_folder_instance_id_names(app_and_config, sample_config, tmp_mission_dir, monkeypatch):
    """instanceId = 2 puts the world in storage_2; a storage_1 beside it is
    not what "Wipe server" is about, and the page names the folder it means."""
    set_instance_id(sample_config.server_dir_path, 2)
    for name in ("storage_1", "storage_2"):
        (tmp_mission_dir / name).mkdir()
        (tmp_mission_dir / name / "players.db").write_text(name)
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)

    with TestClient(app_and_config) as client:
        _login(client)
        overview = client.get("/api/cleanup/overview").json()
        assert overview["storage_name"] == "storage_2"
        assert overview["storage"]["path"] == str(tmp_mission_dir / "storage_2")

        resp = client.post("/api/cleanup/storage", json={"confirm": True, "backup_before": False})
        body = _poll_job(client, resp.json()["job_id"])
        assert body["status"] == "success", body

    assert not (tmp_mission_dir / "storage_2").exists()
    assert (tmp_mission_dir / "storage_1" / "players.db").read_text() == "storage_1"


def test_the_overview_names_the_folder_when_there_is_none(client):
    assert client.get("/api/cleanup/overview").json()["storage_name"] == "storage_1"


def test_migrate_players_follows_instance_id(client, sample_config, server_stopped):
    set_instance_id(sample_config.server_dir_path, 2)
    source = _make_mission(sample_config, "dayzOffline.chernarusplus", with_db=True)  # storage_1
    _make_mission(sample_config, "empty.deerisle")

    body = {"source": "dayzOffline.chernarusplus", "target": "empty.deerisle"}
    refused = client.post("/api/cleanup/migrate-players", json=body)
    assert refused.status_code != 200 and "storage_2" in refused.text

    (source / "storage_2").mkdir()
    (source / "storage_2" / "players.db").write_bytes(b"live")
    assert client.post("/api/cleanup/migrate-players", json=body).status_code == 200
    target = sample_config.server_dir_path / "mpmissions" / "empty.deerisle"
    assert (target / "storage_2" / "players.db").read_bytes() == b"live"
    assert not (target / "storage_1").exists()


def test_cleanup_mission_reset_404_unknown_mission(app_and_config, monkeypatch):
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)
    monkeypatch.setattr("app.ops.cleanup_ops.list_resettable_missions", lambda config: [])

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/cleanup/mission-reset", json={"confirm": True, "mission": "nope"})
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])
        assert body["status"] == "failed"


def test_a_community_mission_reset_puts_the_mods_xml_back(app_and_config, sample_config, monkeypatch):
    from types import SimpleNamespace

    from app.modmgr.mods import get_current_mission

    current = get_current_mission(sample_config.server_cfg_path)
    mission = SimpleNamespace(template=current, path=Path("/tmp/m"), map_name="X", source_type="zip",
                              zip_url="https://x/repo.zip", mission_path="m", has_storage=True)
    merged = []
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)
    monkeypatch.setattr("app.ops.cleanup_ops.list_resettable_missions", lambda config: [mission])
    monkeypatch.setattr("app.ops.cleanup_ops.reset_mission_zip", lambda *a: 3)
    monkeypatch.setattr("app.maps.map_ops.apply_mod_xml_to_new_mission", lambda config, log: merged.append(1))

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/cleanup/mission-reset", json={"confirm": True, "mission": current})
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "success", body
    assert merged == [1]


def test_reinstall_server_409_when_running(client, monkeypatch):
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: True)
    resp = client.post("/api/maintenance/reinstall-server", json={"confirm": True})
    assert resp.status_code == 409


def test_reinstall_server_requires_confirm(client):
    resp = client.post("/api/maintenance/reinstall-server", json={"confirm": False})
    assert resp.status_code == 422


def test_reinstall_server_job(app_and_config, monkeypatch):
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)
    # sample_config's server dir holds its config dir, which the guard refuses
    # (tests/test_cleanup_ops.py); this test is about the job.
    monkeypatch.setattr("app.ops.cleanup_ops.reinstall_refusal", lambda config: None)
    monkeypatch.setattr("app.ops.cleanup_ops.rmtree_mount_safe", lambda path: None)
    monkeypatch.setattr("app.shared.steam.build_server_update_cmd", lambda config: ["true"])
    monkeypatch.setattr("app.modmgr.modlist.clear", lambda config: None)
    monkeypatch.setattr("app.shared.container.sync_battleye_cfg", lambda config, notes=None: None)
    monkeypatch.setattr("app.shared.container.sync_server_cfg", lambda config, notes=None: {})

    async def _fake_service(runner, config, **kw):
        return True

    monkeypatch.setattr("app.server.service_ops.generate_service_files_async", _fake_service)

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/maintenance/reinstall-server", json={"confirm": True})
        assert resp.status_code == 200, resp.text
        body = _poll_job(client, resp.json()["job_id"])
        assert body["status"] == "success", body


def test_reinstall_server_is_refused_before_anything_is_deleted(app_and_config, monkeypatch):
    deleted = []
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)
    monkeypatch.setattr("app.ops.cleanup_ops.rmtree_mount_safe", deleted.append)
    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/maintenance/reinstall-server", json={"confirm": True})
    assert resp.status_code == 409
    assert "holds Zedstead's own settings" in resp.json()["detail"]
    assert deleted == []


def test_reinstall_steamcmd_409_without_sudo(client, monkeypatch):
    """Root is still required when packages really are missing."""
    monkeypatch.setattr("app.shared.platform.IS_WINDOWS", False)
    monkeypatch.setattr("app.shared.platform.sudo_available_noninteractive", lambda: False)
    monkeypatch.setattr("app.shared.os_packages.missing_packages", lambda _n: ["libcurl4"])
    resp = client.post("/api/maintenance/reinstall-steamcmd", json={"confirm": True})
    assert resp.status_code == 409
    assert "libcurl4" in resp.json()["detail"]


def test_reinstall_steamcmd_needs_no_sudo_when_nothing_is_missing(
    app_and_config, sample_config, monkeypatch, tmp_path
):
    """Since plan 26 the common case runs no apt command at all, so the sudo
    gate was refusing the case it could serve. See plans/28 E3."""
    monkeypatch.setattr("app.shared.platform.IS_WINDOWS", False)
    monkeypatch.setattr("app.shared.platform.sudo_available_noninteractive", lambda: False)
    monkeypatch.setattr("app.shared.os_packages.missing_packages", lambda _n: [])
    monkeypatch.setattr(
        "app.server.service_control.is_server_running", lambda config=None: False
    )

    steamcmd_dir = tmp_path / "steamcmd"
    steamcmd_dir.mkdir()
    (steamcmd_dir / "steamcmd.sh").write_text("old")
    sample_config.steamcmd_dir = str(steamcmd_dir)

    def _fake_install(install_path, *, log=None):
        Path(install_path).mkdir(parents=True, exist_ok=True)
        (Path(install_path) / "steamcmd.sh").write_text("new")
        return True

    monkeypatch.setattr("app.shared.steam.install_steamcmd", _fake_install)

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/maintenance/reinstall-steamcmd", json={"confirm": True})
        assert resp.status_code == 200, resp.text
        body = _poll_job(client, resp.json()["job_id"])
        assert body["status"] == "success", body


def test_reinstall_steamcmd_job(app_and_config, sample_config, monkeypatch, tmp_path):
    """install_steamcmd must be called with log as a KEYWORD arg (it is
    keyword-only — a positional call raised TypeError after the old install
    had already been deleted)."""
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)
    monkeypatch.setattr("app.shared.platform.sudo_available_noninteractive", lambda: True)
    monkeypatch.setattr("app.shared.os_packages.missing_packages", lambda _n: [])

    steamcmd_dir = tmp_path / "steamcmd"
    steamcmd_dir.mkdir()
    (steamcmd_dir / "steamcmd.sh").write_text("old")
    sample_config.steamcmd_dir = str(steamcmd_dir)

    def _fake_install(install_path, *, log=None):
        assert log is not None
        log("installing")
        Path(install_path).mkdir(parents=True, exist_ok=True)
        (Path(install_path) / "steamcmd.sh").write_text("new")
        return True

    monkeypatch.setattr("app.shared.steam.install_steamcmd", _fake_install)

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/maintenance/reinstall-steamcmd", json={"confirm": True})
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])
        assert body["status"] == "success", body

    assert (steamcmd_dir / "steamcmd.sh").read_text() == "new"
    assert not steamcmd_dir.with_name("steamcmd.old").exists()


def test_reinstall_steamcmd_restores_old_install_on_failure(
    app_and_config, sample_config, monkeypatch, tmp_path
):
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)
    monkeypatch.setattr("app.shared.platform.sudo_available_noninteractive", lambda: True)
    monkeypatch.setattr("app.shared.os_packages.missing_packages", lambda _n: [])

    steamcmd_dir = tmp_path / "steamcmd"
    steamcmd_dir.mkdir()
    (steamcmd_dir / "steamcmd.sh").write_text("old")
    sample_config.steamcmd_dir = str(steamcmd_dir)

    def _fake_install(install_path, *, log=None):
        Path(install_path).mkdir(parents=True, exist_ok=True)
        return False

    monkeypatch.setattr("app.shared.steam.install_steamcmd", _fake_install)

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/maintenance/reinstall-steamcmd", json={"confirm": True})
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])
        assert body["status"] == "failed", body

    assert (steamcmd_dir / "steamcmd.sh").read_text() == "old"


# ---------------------------------------------------------------------------
# Migrate players.db + TraderPlus reset
# ---------------------------------------------------------------------------


def _make_mission(sample_config, name: str, with_db: bool = False):
    d = sample_config.server_dir_path / "mpmissions" / name
    d.mkdir(parents=True, exist_ok=True)
    if with_db:
        db = d / "storage_1" / "players.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        db.write_bytes(b"sqlite-data")
    return d


def test_list_missions(client, sample_config):
    _make_mission(sample_config, "dayzOffline.chernarusplus", with_db=True)
    _make_mission(sample_config, "empty.deerisle")

    resp = client.get("/api/cleanup/missions")
    assert resp.status_code == 200
    missions = {m["mission"]: m for m in resp.json()["missions"]}
    assert missions["dayzOffline.chernarusplus"]["has_players_db"] is True
    assert missions["empty.deerisle"]["has_players_db"] is False


def test_migrate_players(client, sample_config, server_stopped):
    _make_mission(sample_config, "dayzOffline.chernarusplus", with_db=True)
    _make_mission(sample_config, "empty.deerisle")

    resp = client.post(
        "/api/cleanup/migrate-players",
        json={"source": "dayzOffline.chernarusplus", "target": "empty.deerisle"},
    )
    assert resp.status_code == 200
    target_db = (
        sample_config.server_dir_path / "mpmissions" / "empty.deerisle" / "storage_1" / "players.db"
    )
    assert target_db.read_bytes() == b"sqlite-data"


def test_migrate_players_backs_up_existing_target(client, sample_config, server_stopped):
    _make_mission(sample_config, "dayzOffline.chernarusplus", with_db=True)
    d = _make_mission(sample_config, "empty.deerisle", with_db=True)
    (d / "storage_1" / "players.db").write_bytes(b"old-target")

    resp = client.post(
        "/api/cleanup/migrate-players",
        json={"source": "dayzOffline.chernarusplus", "target": "empty.deerisle"},
    )
    assert resp.status_code == 200
    assert resp.json()["backed_up"] == "players.db.backup"
    assert (d / "storage_1" / "players.db.backup").read_bytes() == b"old-target"
    assert (d / "storage_1" / "players.db").read_bytes() == b"sqlite-data"


def test_migrate_players_is_refused_while_the_server_runs(client, sample_config, monkeypatch):
    """The running server holds the target's players.db open and writes it back."""
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: True)
    _make_mission(sample_config, "dayzOffline.chernarusplus", with_db=True)
    d = _make_mission(sample_config, "empty.deerisle", with_db=True)
    (d / "storage_1" / "players.db").write_bytes(b"live-target")

    resp = client.post(
        "/api/cleanup/migrate-players",
        json={"source": "dayzOffline.chernarusplus", "target": "empty.deerisle"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Server is running. Stop it first."
    assert (d / "storage_1" / "players.db").read_bytes() == b"live-target"
    assert not (d / "storage_1" / "players.db.backup").exists()


def test_migrate_players_validation(client, sample_config, server_stopped):
    _make_mission(sample_config, "dayzOffline.chernarusplus", with_db=True)

    resp = client.post(
        "/api/cleanup/migrate-players", json={"source": "a", "target": "a"}
    )
    assert resp.status_code == 422

    resp = client.post(
        "/api/cleanup/migrate-players",
        json={"source": "../../etc", "target": "dayzOffline.chernarusplus"},
    )
    assert resp.status_code == 400


def test_reset_traderplus(client, monkeypatch):
    monkeypatch.setattr(
        "app.traderplus.trader_common.reset_traderplus_json",
        lambda config: (True, ["copied defaults"]),
    )
    resp = client.post("/api/cleanup/reset-traderplus", json={"kind": "json"})
    assert resp.status_code == 200
    assert resp.json()["messages"] == ["copied defaults"]

    monkeypatch.setattr(
        "app.traderplus.trader_common.reset_traderplus_xml",
        lambda config: (False, ["@TraderPlus not found"]),
    )
    assert client.post("/api/cleanup/reset-traderplus", json={"kind": "xml"}).status_code == 400
    assert client.post("/api/cleanup/reset-traderplus", json={"kind": "bogus"}).status_code == 422


def test_repair_workshop_manifest_endpoint(client, sample_config, tmp_path):
    # A stale manifest entry (mod folder gone, ACF still lists it) makes
    # SteamCMD abort every later workshop download with "(Failure)".
    sample_config.workshop_content_dir = str(
        tmp_path / "Steam" / "steamapps" / "workshop" / "content" / "221100"
    )
    sample_config.workshop_dir.mkdir(parents=True, exist_ok=True)
    sample_config.workshop_acf_path.write_text(
        '"AppWorkshop"\n{\n\t"WorkshopItemsInstalled"\n\t{\n\t\t"222222"\n\t\t{\n'
        '\t\t\t"timeupdated"\t\t"1700000001"\n\t\t}\n\t}\n}\n',
        encoding="utf-8",
    )

    resp = client.post("/api/cleanup/repair-workshop")
    assert resp.status_code == 200
    assert resp.json()["removed"] == ["222222"]

    # Idempotent — nothing left to repair.
    assert client.post("/api/cleanup/repair-workshop").json()["removed"] == []


def test_reset_traderplus_json_replaces_only_the_configs(client, sample_config):
    """The reset deleted all of profiles/TraderPlus
    and copied the mod's ServerProfile in its place, so every bank account,
    garage and trader stock file went, with no backup, and the mod author's
    sample account and garage arrived instead."""
    from app.shared.config_versions import load_index, store_for

    server = sample_config.server_dir_path
    mod = server / "@TraderPlus" / "ServerProfile" / "TraderPlus"
    (mod / "TraderPlusConfig").mkdir(parents=True)
    (mod / "TraderPlusConfig" / "TraderPlusGeneralConfig.json").write_text('{"default": 1}')
    (mod / "TraderPlusBankDatabase").mkdir()
    (mod / "TraderPlusBankDatabase" / "Account_76561198047475641.json").write_text('{"MoneyAmount": 1}')
    (mod / "TraderPlusGarageDatabase").mkdir()
    (mod / "TraderPlusGarageDatabase" / "Garage_47475641.json").write_text("{}")

    live = server / sample_config.profiles_path / "TraderPlus"
    config_file = live / "TraderPlusConfig" / "TraderPlusGeneralConfig.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text('{"mine": 2}')
    (live / "TraderPlusConfig" / "MyNotes.txt").write_text("keep")
    players = {
        "TraderPlusBankDatabase/Account_76561190000000001.json": '{"MoneyAmount": 250000}',
        "TraderPlusGarageDatabase/Garage_00000001.json": "{}",
        "TraderPlusDatabase/ID_0/stock.json": "{}",
        "TraderPlusLogs/TraderPlus_2026_10_16.log": "log",
    }
    for rel, text in players.items():
        (live / rel).parent.mkdir(parents=True, exist_ok=True)
        (live / rel).write_text(text)

    resp = client.post("/api/cleanup/reset-traderplus", json={"kind": "json"})
    assert resp.status_code == 200, resp.text

    assert config_file.read_text() == '{"default": 1}'
    assert (live / "TraderPlusConfig" / "MyNotes.txt").read_text() == "keep"
    for rel, text in players.items():
        assert (live / rel).read_text() == text, rel
    assert not (live / "TraderPlusBankDatabase" / "Account_76561198047475641.json").exists()
    assert not (live / "TraderPlusGarageDatabase" / "Garage_47475641.json").exists()
    versions = load_index(store_for(config_file)).get_versions(
        config_file.name, source_path=str(config_file)
    )
    assert [v.trigger for v in versions] == ["pre_restore"]


def _fake_github(monkeypatch, files: dict[str, str]) -> list[str]:
    """Serve *files* (repo path -> text) as TheDmitri/TraderPlusV1; returns the URLs asked."""
    import json
    from urllib.parse import unquote

    from app.traderplus import upstream_defaults as up

    asked: list[str] = []

    def _get(url, dest, max_bytes):
        asked.append(url)
        if url == up.TRADERPLUS.tree_url:
            tree = [{"path": path, "type": "blob"} for path in files]
            dest.write_text(json.dumps({"tree": tree}))
            return
        assert url.startswith(up.TRADERPLUS.raw_base), url
        dest.write_text(files[unquote(url[len(up.TRADERPLUS.raw_base):])])

    monkeypatch.setattr(up, "_get", _get)
    return asked


def test_traderplus_reset_downloads_what_the_mod_no_longer_ships(
    client, sample_config, tmp_mission_dir, monkeypatch,
):
    """The 2026-11-09 Workshop TraderPlus ships only Addons, Keys and meta.cpp.
    Both reset buttons then failed with "not found" toasts on a server that has
    the mod. The author keeps the defaults in TheDmitri/TraderPlusV1, so the
    reset now takes them from there."""
    from app.traderplus import trader_common
    from app.xml import xml_manual

    mod = sample_config.server_dir_path / "@TraderPlus"
    (mod / "Addons").mkdir(parents=True)
    (tmp_mission_dir / "cfgeconomycore.xml").write_text(
        '<economycore>\n  <ce folder="db">\n    <file name="types.xml" type="types"/>\n  </ce>\n</economycore>\n'
    )
    asked = _fake_github(monkeypatch, {
        "serverProfileFiles/TraderPlus/TraderPlusConfig/TraderPlusGeneralConfig.json": '{"up": 1}',
        "serverProfileFiles/TraderPlus/TraderPlusConfig/TraderPlusPriceConfig.json_stock": "{}",
        "serverProfileFiles/TraderPlus/TraderPlusBankDatabase/Account_1.json": "{}",
        "economy/Centraleconomy/TraderPlus_ce/TraderPlus_types.xml":
            '<types><type name="InventedTraderItem"><nominal>0</nominal></type></types>',
    })

    tp = client.get("/api/cleanup/overview").json()["traderplus_reset"]
    assert (tp["installed"], tp["json"], tp["xml"]) == (True, "github", "github")
    assert asked == [], "the overview is a GET and never downloads"

    resp = client.post("/api/cleanup/reset-traderplus", json={"kind": "json"})
    assert resp.status_code == 200, resp.text
    live = sample_config.server_dir_path / sample_config.profiles_path / "TraderPlus"
    assert sorted(p.relative_to(live).as_posix() for p in live.rglob("*") if p.is_file()) == [
        "TraderPlusConfig/TraderPlusGeneralConfig.json",
    ], "only the config JSON, never the sample account or the _stock copy"

    resp = client.post("/api/cleanup/reset-traderplus", json={"kind": "xml"})
    assert resp.status_code == 200, resp.text
    manual_dir = sample_config.paths.custom_xml_dir
    file_id = trader_common.UPSTREAM_TYPES_FILE_ID
    assert xml_manual.get_file(file_id, manual_dir=manual_dir)["kind"] == "types"
    applied = tmp_mission_dir / "custom" / xml_manual.custom_file_name(file_id)
    assert "InventedTraderItem" in applied.read_text()
    assert xml_manual.custom_file_name(file_id) in (tmp_mission_dir / "cfgeconomycore.xml").read_text()

    # A file taken over from the old mod copy forgets that source: the mod
    # file is gone, and the Mod XML page would report it missing.
    baseline = xml_manual.baseline_path(file_id, manual_dir=manual_dir)
    baseline.write_text("<types/>")
    rows = xml_manual.load_files(manual_dir=manual_dir)
    rows[0].update(source="@TraderPlus/Centraleconomy/TraderPlus_types.xml", source_sha256="ab")
    xml_manual.save_files(rows, manual_dir=manual_dir)

    resp = client.post("/api/cleanup/reset-traderplus", json={"kind": "xml"})
    assert resp.status_code == 200, resp.text
    row = xml_manual.get_file(file_id, manual_dir=manual_dir)
    assert "source" not in row and "source_sha256" not in row
    assert not baseline.exists()
    assert "github.com/TheDmitri/TraderPlusV1" in row["note"]


def test_traderplus_reset_reports_a_failed_download(client, sample_config, monkeypatch):
    from app.traderplus import upstream_defaults as up

    (sample_config.server_dir_path / "@TraderPlus" / "Addons").mkdir(parents=True)

    def _down(url, dest, max_bytes):
        raise up.UpstreamError(f"Could not download {url}: offline")

    monkeypatch.setattr(up, "_get", _down)
    resp = client.post("/api/cleanup/reset-traderplus", json={"kind": "json"})
    assert resp.status_code == 400
    assert "offline" in resp.json()["detail"]
    assert not (sample_config.server_dir_path / sample_config.profiles_path / "TraderPlus").exists()


def test_traderplus_reset_takes_only_bare_names_from_the_listing(sample_config, monkeypatch, tmp_path):
    """A path from GitHub's listing is joined onto the raw URL only when it is a
    plain file name directly inside the config folder."""
    from app.traderplus import upstream_defaults as up

    asked = _fake_github(monkeypatch, {
        "serverProfileFiles/TraderPlus/TraderPlusConfig/Good.json": "{}",
        "serverProfileFiles/TraderPlus/TraderPlusConfig/sub/Nested.json": "{}",
        "serverProfileFiles/TraderPlus/TraderPlusConfig/../Escape.json": "{}",
    })
    written = up.fetch_config_files(tmp_path / "out")
    assert [p.name for p in written] == ["Good.json"]
    assert len(asked) == 2


def test_reconfigure_all_fetches_what_traderplus_no_longer_ships(
    sample_config, tmp_mission_dir, monkeypatch,
):
    """Re-configure all XML (`pipeline._merge_xml_sync`, also `zsm run` and
    install) never reached the GitHub fallback: only the Cleanup reset buttons
    did. A TraderPlus updated past 2026-11-09 then had no types in the mission
    and no default JSON. The merge now fills both in, once, and never
    overwrites the admin's files."""
    from app.modmgr import modlist
    from app.ops import pipeline
    from app.traderplus import trader_common
    from app.xml import xml_manual

    (sample_config.server_dir_path / "@TraderPlus" / "Addons").mkdir(parents=True)
    modlist.add(sample_config, "@TraderPlus", "2458896948")
    (tmp_mission_dir / "cfgeconomycore.xml").write_text(
        '<economycore>\n  <ce folder="db">\n    <file name="types.xml" type="types"/>\n  </ce>\n</economycore>\n'
    )
    asked = _fake_github(monkeypatch, {
        "serverProfileFiles/TraderPlus/TraderPlusConfig/TraderPlusGeneralConfig.json": '{"up": 1}',
        "economy/Centraleconomy/TraderPlus_ce/TraderPlus_types.xml":
            '<types><type name="InventedTraderItem"><nominal>0</nominal></type></types>',
    })
    ctx = pipeline.PipelineContext(
        config=sample_config, emit=lambda _l: None, runner=None, should_run=lambda _k: True
    )

    pipeline._merge_xml_sync(ctx)

    live = sample_config.server_dir_path / sample_config.profiles_path / "TraderPlus" / "TraderPlusConfig"
    assert (live / "TraderPlusGeneralConfig.json").read_text() == '{"up": 1}'
    file_id = trader_common.UPSTREAM_TYPES_FILE_ID
    assert xml_manual.get_file(file_id, manual_dir=sample_config.paths.custom_xml_dir)
    assert "InventedTraderItem" in (tmp_mission_dir / "custom" / xml_manual.custom_file_name(file_id)).read_text()
    assert xml_manual.custom_file_name(file_id) in (tmp_mission_dir / "cfgeconomycore.xml").read_text()

    # A second merge downloads nothing and keeps the admin's edit.
    (live / "TraderPlusGeneralConfig.json").write_text('{"mine": 2}')
    asked.clear()
    pipeline._merge_xml_sync(ctx)
    assert asked == []
    assert (live / "TraderPlusGeneralConfig.json").read_text() == '{"mine": 2}'

    # A file the admin deleted stays deleted: the merge downloads only once.
    xml_manual.delete_file(file_id, manual_dir=sample_config.paths.custom_xml_dir)
    pipeline._merge_xml_sync(ctx)
    assert asked == []
    assert not xml_manual.get_file(file_id, manual_dir=sample_config.paths.custom_xml_dir)
