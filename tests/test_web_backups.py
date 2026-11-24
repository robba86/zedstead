"""Tests for /api/backups*, /api/bundle* (app/web/routers/backups.py)."""

from __future__ import annotations

import pathlib
import shutil
import time

import pytest
from fastapi.testclient import TestClient
from tests.conftest import installed_names, write_test_mods

PASSWORD = "testpass123"


@pytest.fixture()
def client(sample_config, tmp_path):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    sample_config.backup_dir = str(tmp_path / "backups")
    app = server.create_app(sample_config)
    tclient = TestClient(app)
    tclient.post("/api/auth/login", json={"password": PASSWORD})
    return tclient


@pytest.fixture()
def app_and_config(sample_config, tmp_path):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    sample_config.backup_dir = str(tmp_path / "backups")
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


def _make_storage(tmp_mission_dir):
    storage = tmp_mission_dir / "storage_1"
    storage.mkdir()
    (storage / "players.db").write_text("fake players db")
    return storage


def test_backups_requires_session(sample_config):
    from app.web import server

    app = server.create_app(sample_config)
    resp = TestClient(app).get("/api/backups")
    assert resp.status_code == 401


def test_list_backups_empty(client, sample_config):
    from app.ops import backup_ops

    resp = client.get("/api/backups")
    assert resp.status_code == 200
    # `dir` rides along so the page can name the .meta.json sidecar's home.
    assert resp.json() == {
        "quick_problem": backup_ops.quick_backup_problem(sample_config),
        "dir": str(backup_ops.get_backup_dir(sample_config)),
        "quick": [],
        "full": [],
    }


def test_create_list_delete_quick_backup(app_and_config, tmp_mission_dir, monkeypatch):
    _make_storage(tmp_mission_dir)
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/backups", json={"type": "quick", "note": "test backup"})
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])
        assert body["status"] == "success", body
        assert body["result"]["path"] is not None

        listed = client.get("/api/backups").json()
        assert len(listed["quick"]) == 1
        name = listed["quick"][0]["name"]
        assert listed["quick"][0]["note"] == "test backup"
        # The Size column shows the zip on disk, not the files before
        # compression (3835.5 MB for a 1.7 GB zip).
        zip_path = pathlib.Path(body["result"]["path"])
        assert listed["quick"][0]["size"] == zip_path.stat().st_size

        del_resp = client.request("DELETE", "/api/backups", json={"type": "quick", "names": [name]})
        assert del_resp.status_code == 200
        assert del_resp.json() == {"deleted": 1}
        assert client.get("/api/backups").json()["quick"] == []


def test_quick_backup_mod_data_follows_the_body(app_and_config, tmp_mission_dir):
    import zipfile

    storage = _make_storage(tmp_mission_dir)
    (storage / "expansion").mkdir()
    (storage / "expansion" / "a.json").write_text("{}")

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/backups", json={"type": "quick", "mod_data": False})
        body = _poll_job(client, resp.json()["job_id"])
        assert body["status"] == "success", body
        [listed] = client.get("/api/backups").json()["quick"]

    assert listed["contents"]["mod_data"] is False
    with zipfile.ZipFile(body["result"]["path"]) as zf:
        assert zf.namelist() == ["players.db"]


def test_restore_backup_409_when_server_running(app_and_config, tmp_mission_dir, monkeypatch):
    _make_storage(tmp_mission_dir)
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: True)

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/backups/restore", json={"type": "quick", "name": "does-not-exist"})
        assert resp.status_code == 409


def test_restore_backup_404_when_missing(app_and_config, monkeypatch):
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/backups/restore", json={"type": "quick", "name": "does-not-exist"})
        assert resp.status_code == 404


def test_create_full_backup(app_and_config, sample_config, monkeypatch):
    (sample_config.server_dir_path / "serverDZ.cfg").write_text("hostname = \"x\";")
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/backups", json={"type": "full"})
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])
        assert body["status"] == "success", body

        listed = client.get("/api/backups").json()
        assert len(listed["full"]) == 1


def test_create_backup_conflict_returns_409(app_and_config, tmp_mission_dir, monkeypatch):
    _make_storage(tmp_mission_dir)

    def _slow_create(config, contents, note, log=None):
        time.sleep(0.2)
        return None

    monkeypatch.setattr("app.ops.backup_ops.create_backup", _slow_create)

    with TestClient(app_and_config) as client:
        _login(client)
        first = client.post("/api/backups", json={"type": "quick"})
        assert first.status_code == 200
        second = client.post("/api/backups", json={"type": "quick"})
        assert second.status_code == 409
        _poll_job(client, first.json()["job_id"])


def test_export_bundle_job(app_and_config):
    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/bundle/export")
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])
        assert body["status"] == "success", body
        assert body["result"]["path"]
        # The zip account has to reach the job log — it used to go to the
        # server's stdout only, leaving the browser's process pane empty.
        lines = [entry["line"] for entry in body["log"]]
        assert any(line.startswith("Creating bundle:") for line in lines), lines
        assert any(line.startswith("Bundle exported to ") for line in lines), lines


def test_list_bundles_empty(client, sample_config):
    from app.ops import bundle_ops

    resp = client.get("/api/bundle")
    assert resp.status_code == 200
    body = resp.json()
    assert body["bundles"] == []
    assert body["dir"] == str(bundle_ops.get_bundle_dir(sample_config))
    assert "missions" in body
    # The credential note travels with the listing rather than on its own
    # endpoint: a page that had to remember to ask separately is one that
    # eventually won't, and this is the line to read before sharing a bundle.
    assert "sensitive_note" in body


def test_import_bundle_rejects_path_escape(client):
    resp = client.post("/api/bundle/import", json={"filename": "../../etc/passwd"})
    assert resp.status_code == 400


def test_import_bundle_404_when_missing(client):
    resp = client.post("/api/bundle/import", json={"filename": "does-not-exist.zip"})
    assert resp.status_code == 404


def test_import_bundle_job(app_and_config, sample_config):
    import zipfile

    from app.ops.bundle_ops import get_bundle_dir

    bundle_dir = get_bundle_dir(sample_config)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    zip_path = bundle_dir / "test-bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("mods.json", '{"version": 1, "mods": [{"name": "@CF"}]}')

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/bundle/import", json={"filename": "test-bundle.zip"})
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])
        assert body["status"] == "success", body
        assert "mods.json" in body["result"]["imported"]
    assert installed_names(sample_config) == ["@CF"]


def test_a_bundle_from_before_mods_json_keeps_the_list(app_and_config, sample_config):
    """Its mods.txt is not read (plans/77 D4): the import says so and the
    list stays as it was."""
    import zipfile

    from app.ops.bundle_ops import get_bundle_dir

    write_test_mods(sample_config, ["@Mine"])
    bundle_dir = get_bundle_dir(sample_config)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle_dir / "old-bundle.zip", "w") as zf:
        zf.writestr("mods.txt", "@CF;\n")

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/bundle/import", json={"filename": "old-bundle.zip"})
        body = _poll_job(client, resp.json()["job_id"])
    assert body["status"] == "success", body
    assert "mods.txt" in body["result"]["skipped"]
    assert any("from before Zedstead kept the mod list" in e["line"] for e in body["log"])
    assert installed_names(sample_config) == ["@Mine"]


@pytest.mark.parametrize("locked", [False, True])
def test_import_bundle_skips_a_launch_value_the_save_would_refuse(
    app_and_config, sample_config, locked
):
    """Skipped and logged with the path lock off or on; the rest still applies."""
    import json
    import zipfile

    from app.ops.bundle_ops import get_bundle_dir
    from app.shared.config import DayzConfig

    sample_config.profiles_path = "profiles"
    sample_config.server_args = "-dologs"
    sample_config.web_lock_paths = locked
    sample_config.save()
    app_and_config.state.ctx.config = sample_config

    bundle_dir = get_bundle_dir(sample_config)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle_dir / "test-bundle.zip", "w") as zf:
        zf.writestr(
            "config.json",
            json.dumps(
                {
                    "server_name": "Imported",
                    "profiles_path": "profiles%PATH%",
                    "server_args": "-dologs & calc",
                }
            ),
        )

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/bundle/import", json={"filename": "test-bundle.zip"})
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])
        assert body["status"] == "success", body
        lines = [e["line"] for e in body["log"]]
        assert any("profiles_path was not imported" in line for line in lines)
        assert any("server_args was not imported" in line for line in lines)

    cfg = DayzConfig.load()
    assert (cfg.profiles_path, cfg.server_args) == ("profiles", "-dologs")
    assert cfg.server_name == "Imported"


def test_import_bundle_refuses_a_target_mission_that_is_not_a_folder_name(
    app_and_config, sample_config
):
    """Refused before the job starts, so no other part of the bundle is
    written either."""
    import zipfile

    from app.ops.bundle_ops import get_bundle_dir

    bundle_dir = get_bundle_dir(sample_config)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle_dir / "test-bundle.zip", "w") as zf:
        zf.writestr("mods.txt", "@CF;\n")

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post(
            "/api/bundle/import", json={"filename": "test-bundle.zip", "target_mission": ".."}
        )
    assert resp.status_code == 400
    assert not sample_config.paths.mods_json.exists()


def _bundle_with_battleye_cfg(sample_config) -> str:
    import zipfile

    from app.ops.bundle_ops import get_bundle_dir

    bundle_dir = get_bundle_dir(sample_config)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle_dir / "be-bundle.zip", "w") as zf:
        zf.writestr("mods.txt", "@CF;\n")
        zf.writestr("battleye/beserver_x64.cfg", "RConPassword frombundle\nRConPort 2310\n")
    return "be-bundle.zip"


def test_import_bundle_with_battleye_cfg_is_refused_while_running(
    client, sample_config, battleye_running
):
    """BattlEye puts its active copy back at the next stop (measured
    2026 Q3), so the bundle's cfg would be lost; refused before the job
    starts, so nothing else in it is written either."""
    before = battleye_running.read_text()
    resp = client.post("/api/bundle/import", json={"filename": _bundle_with_battleye_cfg(sample_config)})
    assert resp.status_code == 409
    assert "Stop the server first." in resp.json()["detail"]
    assert not sample_config.beserver_cfg_path.exists()
    assert battleye_running.read_text() == before
    assert not sample_config.paths.mods_json.exists()
    assert client.get("/api/jobs").json() == []


def test_import_bundle_with_battleye_cfg_when_stopped(app_and_config, sample_config, server_stopped):
    name = _bundle_with_battleye_cfg(sample_config)
    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/bundle/import", json={"filename": name})
        assert resp.status_code == 200, resp.text
        body = _poll_job(client, resp.json()["job_id"])
        assert body["status"] == "success", body
    assert "RConPassword frombundle" in sample_config.beserver_cfg_path.read_text()


def test_delete_bundles(client, sample_config):
    from app.ops import bundle_ops

    bundle_dir = bundle_ops.get_bundle_dir(sample_config)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "dayz-server-x-bundle-a.zip").write_bytes(b"PK")
    (bundle_dir / "dayz-server-x-bundle-b.zip").write_bytes(b"PK")

    resp = client.request("DELETE", "/api/bundle", json={"names": ["dayz-server-x-bundle-a.zip"]})
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 1
    assert [b["name"] for b in client.get("/api/bundle").json()["bundles"]] == [
        "dayz-server-x-bundle-b.zip"
    ]


def test_delete_bundles_requires_names(client):
    resp = client.request("DELETE", "/api/bundle", json={})
    assert resp.status_code == 422


@pytest.mark.parametrize("names", ["dayz-server-x-bundle-a.zip", [1], {"a": 1}])
def test_delete_bundles_takes_only_a_list_of_names(client, sample_config, names):
    """A string was read as a set of its characters, each matched as a name."""
    from app.ops import bundle_ops

    bundle_dir = bundle_ops.get_bundle_dir(sample_config)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "dayz-server-x-bundle-a.zip").write_bytes(b"PK")

    resp = client.request("DELETE", "/api/bundle", json={"names": names})
    assert resp.status_code == 422
    assert (bundle_dir / "dayz-server-x-bundle-a.zip").exists()


def test_delete_bundles_ignores_unknown_names(client, sample_config):
    from app.ops import bundle_ops

    bundle_dir = bundle_ops.get_bundle_dir(sample_config)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "dayz-server-x-bundle-keep.zip").write_bytes(b"PK")

    resp = client.request("DELETE", "/api/bundle", json={"names": ["../../etc/passwd", "other.zip"]})
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 0
    assert [b["name"] for b in client.get("/api/bundle").json()["bundles"]] == [
        "dayz-server-x-bundle-keep.zip"
    ]


def test_full_restore_extras_flags(app_and_config, sample_config, monkeypatch):
    """restore_app_config + redownload_mods run their extra steps after a
    full restore."""
    calls: list[str] = []

    class _FakeFull:
        pass

    fake = _FakeFull()
    fake.zip_path = sample_config.server_dir_path / "full-backup.zip"

    monkeypatch.setattr(
        "app.web.routers.backups._find_backup", lambda config, t, n: fake
    )
    monkeypatch.setattr(
        "app.server.service_control.is_server_running", lambda config=None: False
    )
    monkeypatch.setattr(
        "app.ops.backup_ops.restore_full_backup",
        lambda config, backup, log: calls.append("restore") or 3,
    )
    monkeypatch.setattr(
        "app.ops.backup_ops.extract_app_config",
        lambda zip_path, config_file: calls.append("app_config"),
    )
    monkeypatch.setattr("app.modmgr.modlist.installed", lambda config: ["@CF"])

    async def _fake_redownload(job, config):
        calls.append("mods")

    monkeypatch.setattr("app.web.routers.backups._redownload_mods", _fake_redownload)

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post(
            "/api/backups/restore",
            json={
                "type": "full",
                "name": "full-backup",
                "restore_app_config": True,
                "redownload_mods": True,
            },
        )
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "success", body
    assert calls == ["restore", "app_config", "mods"]


# ── Downloads (TODO C2) ──────────────────────────────────────────────


def test_download_quick_backup(app_and_config, tmp_mission_dir, monkeypatch):
    _make_storage(tmp_mission_dir)
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/backups", json={"type": "quick"})
        _poll_job(client, resp.json()["job_id"])
        name = client.get("/api/backups").json()["quick"][0]["name"]

        dl = client.get("/api/backups/download", params={"type": "quick", "name": name})
        assert dl.status_code == 200
        assert dl.headers["content-disposition"] == f'attachment; filename="{name}.zip"'
        assert dl.headers["cache-control"] == "no-store"
        assert dl.content[:2] == b"PK"


def test_download_backup_rejects_unknown_type_and_name(client):
    assert client.get("/api/backups/download", params={"type": "nope", "name": "x"}).status_code == 422
    assert client.get("/api/backups/download", params={"type": "quick", "name": "x"}).status_code == 404


def test_download_backup_name_is_matched_against_the_listing(client, sample_config):
    """A name is never joined onto the backup dir — an escape is a 404, not a file."""
    from app.ops import backup_ops

    backup_dir = backup_ops.get_backup_dir(sample_config)
    backup_dir.mkdir(parents=True, exist_ok=True)
    secret = backup_dir.parent / "secret.zip"
    secret.write_bytes(b"PKsecret")

    resp = client.get("/api/backups/download", params={"type": "quick", "name": "../secret"})
    assert resp.status_code == 404
    assert secret.read_bytes() == b"PKsecret"


def test_restore_and_delete_match_the_backup_name_against_the_listing(
    app_and_config, sample_config, monkeypatch
):
    """The download's rule holds for the other two routes that take a backup
    name (plans/57 E1): an escape restores nothing and deletes nothing."""
    from app.ops import backup_ops

    monkeypatch.setattr("app.server.service_control.is_server_running", lambda *a, **k: False)
    backup_dir = backup_ops.get_backup_dir(sample_config)
    backup_dir.mkdir(parents=True, exist_ok=True)
    secret = backup_dir.parent / "secret.zip"
    secret.write_bytes(b"PKsecret")

    with TestClient(app_and_config) as client:
        _login(client)
        for kind in ("quick", "full"):
            resp = client.post("/api/backups/restore", json={"type": kind, "name": "../secret"})
            assert resp.status_code == 404, kind
            resp = client.request("DELETE", "/api/backups", json={"type": kind, "names": ["../secret"]})
            assert resp.json()["deleted"] == 0, kind
    assert secret.read_bytes() == b"PKsecret"


def test_download_backup_requires_session(app_and_config):
    resp = TestClient(app_and_config).get(
        "/api/backups/download", params={"type": "quick", "name": "x"}
    )
    assert resp.status_code == 401


def test_download_bundle(client, sample_config):
    from app.ops import bundle_ops

    bundle_dir = bundle_ops.get_bundle_dir(sample_config)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "dayz-server-x-bundle-a.zip").write_bytes(b"PKbundle")

    resp = client.get("/api/bundle/download", params={"name": "dayz-server-x-bundle-a.zip"})
    assert resp.status_code == 200
    assert resp.content == b"PKbundle"
    assert resp.headers["content-disposition"] == 'attachment; filename="dayz-server-x-bundle-a.zip"'


def test_download_bundle_404_for_unlisted_name(client, sample_config):
    from app.ops import bundle_ops

    bundle_dir = bundle_ops.get_bundle_dir(sample_config)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    # A real file in the bundle dir that the listing glob does not match:
    # the endpoint follows the listing, not the directory.
    (bundle_dir / "notes.txt").write_text("not a bundle")

    assert client.get("/api/bundle/download", params={"name": "notes.txt"}).status_code == 404
    assert client.get("/api/bundle/download", params={"name": "../../etc/passwd"}).status_code == 404


# ── Offsite target (TODO C1) ─────────────────────────────────────────


@pytest.fixture()
def offsite_paths(tmp_path, monkeypatch):
    """rclone.conf must land in tmp, never the real config dir.

    The accounts come along: users.json lives in the config dir, and a
    signed-in ``client`` built before this fixture would otherwise find no
    accounts at all, which refuses its session (plans/68). A real panel's
    config dir never moves under it.
    """
    from app.shared import paths

    users = paths.CONFIG_DIR / "users.json"
    monkeypatch.setattr("app.shared.paths.CONFIG_DIR", tmp_path / "cfg")
    (tmp_path / "cfg").mkdir(exist_ok=True)
    if users.is_file():
        shutil.copy2(users, tmp_path / "cfg" / "users.json")
    return tmp_path / "cfg"


@pytest.fixture()
def fake_rclone_binary(offsite_paths, sample_config):
    binary = offsite_paths / "rclone"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    sample_config.rclone_path = str(binary)
    return binary


def test_remote_status_is_empty_until_configured(client, offsite_paths):
    body = client.get("/api/backups/remote").json()
    assert body["configured"] is False
    assert body["kind"] == ""
    assert body["supported_kinds"] == ["sftp", "s3", "b2"]


def test_set_remote_writes_the_config_and_never_echoes_the_secret(
    client, offsite_paths, fake_rclone_binary
):
    resp = client.post(
        "/api/backups/remote",
        json={
            "kind": "s3",
            "path": "my-bucket/dayz",
            "auto": True,
            "fields": {"access_key_id": "AKIA1", "secret_access_key": "topsecret"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert body["kind"] == "s3"
    assert body["path"] == "my-bucket/dayz"
    assert "topsecret" not in resp.text

    conf = (offsite_paths / "rclone.conf").read_text()
    assert "type = s3" in conf
    assert "topsecret" in conf  # it has to live somewhere; 0600 is the guard
    assert oct((offsite_paths / "rclone.conf").stat().st_mode)[-3:] == "600"


def test_set_remote_rejects_an_unknown_kind(client, offsite_paths):
    resp = client.post("/api/backups/remote", json={"kind": "dropbox", "fields": {}})
    assert resp.status_code == 422


def test_set_remote_ignores_fields_outside_the_kinds_schema(
    client, offsite_paths, fake_rclone_binary
):
    """The payload is a web form; an arbitrary key is an arbitrary rclone option."""
    client.post(
        "/api/backups/remote",
        json={"kind": "sftp", "fields": {"host": "h", "shell_type": "cmd"}},
    )
    conf = (offsite_paths / "rclone.conf").read_text()
    assert "host = h" in conf
    assert "shell_type" not in conf


def test_saving_only_the_path_keeps_the_stored_credentials(
    client, offsite_paths, fake_rclone_binary, monkeypatch
):
    """The card is blank after a reload; a second save used to wipe the target."""
    import subprocess

    from app.ops import offsite_ops

    monkeypatch.setattr(
        offsite_ops.subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout="OBSCURED\n", stderr=""),
    )
    first = {"host": "backup.example", "user": "dayz", "port": "2222", "pass": "hunter2"}
    client.post("/api/backups/remote", json={"kind": "sftp", "path": "a", "fields": first})
    resp = client.post(
        "/api/backups/remote",
        json={"kind": "sftp", "path": "b", "auto": False, "fields": {"host": "", "pass": ""}},
    )
    assert resp.status_code == 200

    conf = (offsite_paths / "rclone.conf").read_text()
    assert "host = backup.example" in conf
    assert "user = dayz" in conf
    assert "port = 2222" in conf
    assert "pass = OBSCURED" in conf

    body = client.get("/api/backups/remote").json()
    assert body["path"] == "b"
    assert body["auto_upload"] is False
    assert body["set_fields"] == ["host", "user", "port", "pass"]
    for text in (resp.text, client.get("/api/backups/remote").text):
        assert "hunter2" not in text
        assert "OBSCURED" not in text
        assert "backup.example" not in text


def test_clear_and_a_kind_switch_through_the_route(client, offsite_paths, fake_rclone_binary):
    client.post("/api/backups/remote", json={"kind": "sftp", "fields": {"host": "h", "key_file": "/k"}})
    client.post("/api/backups/remote", json={"kind": "sftp", "fields": {}, "clear": ["key_file"]})
    conf = (offsite_paths / "rclone.conf").read_text()
    assert "host = h" in conf
    assert "key_file" not in conf

    assert client.post("/api/backups/remote", json={"kind": "sftp", "clear": "key_file"}).status_code == 422

    client.post(
        "/api/backups/remote", json={"kind": "b2", "path": "bucket", "fields": {"account": "acc", "key": "k"}}
    )
    conf = (offsite_paths / "rclone.conf").read_text()
    assert "type = b2" in conf
    assert "host = h" not in conf


def test_a_save_without_the_required_fields_writes_nothing(client, offsite_paths, fake_rclone_binary):
    """An empty form used to save a target that could never upload."""
    resp = client.post("/api/backups/remote", json={"kind": "s3", "fields": {"access_key_id": "A"}})
    assert resp.status_code == 422
    assert set(resp.json()["detail"]["fields"]) == {"secret_access_key", "path"}
    assert not (offsite_paths / "rclone.conf").exists()

    resp = client.post("/api/backups/remote", json={"kind": "sftp", "fields": {"host": "h", "port": "22x"}})
    assert resp.json()["detail"]["fields"] == {"port": "A number from 1 to 65535."}


def test_a_stored_field_counts_as_filled_until_cleared_or_the_kind_changes(
    client, offsite_paths, fake_rclone_binary
):
    """Blank means keep, so changing only the path must not ask for the host again."""
    assert client.post("/api/backups/remote", json={"kind": "sftp", "fields": {"host": "h"}}).status_code == 200
    assert client.post("/api/backups/remote", json={"kind": "sftp", "path": "p", "fields": {}}).status_code == 200

    resp = client.post("/api/backups/remote", json={"kind": "sftp", "fields": {}, "clear": ["host"]})
    assert resp.json()["detail"]["fields"] == {"host": "Required."}

    resp = client.post("/api/backups/remote", json={"kind": "b2", "path": "b", "fields": {}})
    assert set(resp.json()["detail"]["fields"]) == {"account", "key"}


def test_clear_remote(client, offsite_paths, fake_rclone_binary):
    client.post("/api/backups/remote", json={"kind": "sftp", "fields": {"host": "h"}})
    assert client.request("DELETE", "/api/backups/remote").json() == {"removed": True}
    assert client.get("/api/backups/remote").json()["configured"] is False


def test_test_remote_answers_200_with_ok_false(client, offsite_paths, fake_rclone_binary):
    """A wrong password is an answer, not a broken request."""
    resp = client.post("/api/backups/remote/test")
    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "message": "No offsite target configured."}


# A throwaway ed25519 key and what `ssh-keygen -lf` prints for it.
HOST_KEY = ("ssh-ed25519", "AAAAC3NzaC1lZDI1NTE5AAAAIKTME9hVYL+mYt05CxfRVs/IZPPm2XrrZqjs6T8x5Km2")
HOST_KEY_FP = "SHA256:QpLCxrWY/MucRqtszcT54pVg1zxQcvST0nO9IUXqDK8"


@pytest.fixture()
def sftp_remote(client, offsite_paths, fake_rclone_binary, monkeypatch):
    """An SFTP target whose host is faked where zsm asks it for its key, and
    an rclone that answers every listing. Nothing reaches the network."""
    import subprocess

    from app.ops import offsite_ops

    host = {"keys": [HOST_KEY]}
    monkeypatch.setattr(offsite_ops, "scan_host_keys", lambda _h, _p: list(host["keys"]))
    monkeypatch.setattr(offsite_ops, "ensure_rclone", lambda *_a, **_k: fake_rclone_binary)
    monkeypatch.setattr(
        offsite_ops.subprocess,
        "run",
        lambda argv, **_k: subprocess.CompletedProcess(argv, 0, stdout="", stderr=""),
    )
    client.post("/api/backups/remote", json={"kind": "sftp", "fields": {"host": "h"}})
    return host


def test_test_remote_shows_the_sftp_host_key_and_saves_nothing(client, offsite_paths, sftp_remote):
    resp = client.post("/api/backups/remote/test")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["host_key"]["state"] == "unknown"
    assert body["host_key"]["keys"] == [{"type": "ssh-ed25519", "fingerprint": HOST_KEY_FP}]
    assert not (offsite_paths / "offsite_known_hosts").exists()
    assert client.get("/api/backups/remote").json()["host_key"] == "unconfirmed"


def test_confirming_the_host_key_saves_it_and_tests_again(client, offsite_paths, sftp_remote):
    resp = client.post("/api/backups/remote/host-key", json={"fingerprints": [HOST_KEY_FP]})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True, body
    assert body["host_key"]["state"] == "known"
    assert (offsite_paths / "offsite_known_hosts").read_text() == f"h {HOST_KEY[0]} {HOST_KEY[1]}\n"
    assert client.get("/api/backups/remote").json()["host_key"] == "confirmed"


def test_the_host_key_route_saves_only_what_the_host_offers(client, offsite_paths, sftp_remote):
    resp = client.post("/api/backups/remote/host-key", json={"fingerprints": ["SHA256:other"]})

    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert "different key" in resp.json()["message"]
    assert not (offsite_paths / "offsite_known_hosts").exists()


def test_a_changed_host_key_is_refused_by_both_routes(client, offsite_paths, sftp_remote):
    client.post("/api/backups/remote/host-key", json={"fingerprints": [HOST_KEY_FP]})
    before = (offsite_paths / "offsite_known_hosts").read_text()
    sftp_remote["keys"] = [("ssh-ed25519", "AAAAC3NzaC1lZDI1NTE5AAAAIO" + "B" * 42)]

    test = client.post("/api/backups/remote/test").json()
    assert test["ok"] is False
    assert test["host_key"]["state"] == "changed"

    new_fp = test["host_key"]["keys"][0]["fingerprint"]
    trust = client.post("/api/backups/remote/host-key", json={"fingerprints": [new_fp]}).json()
    assert trust["ok"] is False
    assert trust["message"].startswith("Refused")
    assert (offsite_paths / "offsite_known_hosts").read_text() == before


@pytest.mark.parametrize(
    "body", [{}, {"fingerprints": []}, {"fingerprints": "SHA256:x"}, {"fingerprints": [1]}]
)
def test_the_host_key_route_wants_a_list_of_fingerprints(client, offsite_paths, body):
    assert client.post("/api/backups/remote/host-key", json=body).status_code == 422


def test_push_requires_a_configured_remote(
    app_and_config, tmp_mission_dir, offsite_paths, monkeypatch
):
    _make_storage(tmp_mission_dir)
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/backups", json={"type": "quick"})
        _poll_job(client, resp.json()["job_id"])
        name = client.get("/api/backups").json()["quick"][0]["name"]

        push = client.post("/api/backups/push", json={"type": "quick", "name": name})
        assert push.status_code == 409


def test_push_404s_for_an_unknown_backup(client, offsite_paths):
    resp = client.post("/api/backups/push", json={"type": "quick", "name": "nope"})
    assert resp.status_code == 404


def test_backup_job_reports_a_failed_offsite_upload_without_failing(
    app_and_config, sample_config, tmp_mission_dir, offsite_paths, monkeypatch
):
    """The zip is on disk — a remote being down must not say otherwise."""
    from app.ops import offsite_ops

    _make_storage(tmp_mission_dir)
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)
    sample_config.backup_remote_auto = True
    monkeypatch.setattr(offsite_ops, "read_remote_kind", lambda: "sftp")

    def _boom(*_a, **_k):
        raise offsite_ops.RcloneError("dial tcp: connection refused")

    monkeypatch.setattr(offsite_ops, "push_backup", _boom)

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/backups", json={"type": "quick"})
        body = _poll_job(client, resp.json()["job_id"])

        assert body["status"] == "success", body
        assert body["result"]["path"] is not None
        assert "connection refused" in body["result"]["offsite_error"]
        lines = [e["line"] for e in body["log"]]
        assert any("Offsite upload FAILED" in line for line in lines), lines


def test_quick_backup_waits_for_the_first_server_run(app_and_config, tmp_mission_dir):
    """User test in 2026 Q4: on a server that never ran, Create said only
    "No storage_1 found for current mission." The listing now says why
    Create is off, the create is refused with that sentence, and both clear
    once the persistence folder exists."""
    with TestClient(app_and_config) as client:
        _login(client)
        problem = client.get("/api/backups").json()["quick_problem"]
        assert problem.startswith("Nothing to back up yet") and "Start the server once" in problem
        resp = client.post("/api/backups", json={"type": "quick"})
        assert resp.status_code == 409 and resp.json()["detail"] == problem

        _make_storage(tmp_mission_dir)
        assert client.get("/api/backups").json()["quick_problem"] is None


def test_a_backup_job_that_writes_nothing_fails(app_and_config, sample_config, monkeypatch):
    """On an instance with no server the full backup
    job ended as a green Success with path null, and the toast said
    "finished"."""
    monkeypatch.setattr("app.ops.backup_ops.create_full_backup", lambda config, options, log: None)
    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/backups", json={"type": "full"})
        assert resp.status_code == 200, resp.text
        body = _poll_job(client, resp.json()["job_id"])
    assert body["status"] == "failed", body
    assert "No backup was written" in (body.get("error") or "")


def test_a_full_backup_with_no_server_dir_is_refused_up_front(app_and_config, sample_config, tmp_path):
    sample_config.server_dir = str(tmp_path / "server-exp")
    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/backups", json={"type": "full"})
    assert resp.status_code == 409
    assert "does not exist yet" in resp.json()["detail"]


def test_unlocked_full_restore_is_what_the_panel_serves_next(app_and_config, sample_config, monkeypatch):
    """The wholesale config.json write is adopted by the running panel: its
    config and every on_config_change listener (the RCon manager, the
    poller). It used to keep serving the config from before the restore
    until some other save happened to re-read the file."""
    import json
    import zipfile

    from app.ops.backup_ops import APP_CONFIG_ARC_NAME

    sample_config.save()
    fake = type("F", (), {})()
    fake.zip_path = sample_config.server_dir_path / "full.zip"
    restored = {**json.loads(sample_config.paths.config_file.read_text()), "server_name": "From Backup"}
    with zipfile.ZipFile(fake.zip_path, "w") as zf:
        zf.writestr(APP_CONFIG_ARC_NAME, json.dumps(restored))
    monkeypatch.setattr("app.web.routers.backups._find_backup", lambda c, t, n: fake)
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)
    monkeypatch.setattr("app.ops.backup_ops.restore_full_backup", lambda config, backup, log: 1)

    ctx = app_and_config.state.ctx
    heard: list[str] = []
    ctx.on_config_change(lambda cfg: heard.append(cfg.server_name))

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post(
            "/api/backups/restore",
            json={"type": "full", "name": "full", "restore_app_config": True},
        )
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])
        assert body["status"] == "success", body

    assert ctx.config.server_name == "From Backup"
    assert heard == ["From Backup"]
