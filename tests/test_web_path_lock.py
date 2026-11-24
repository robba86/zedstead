"""The web path lock — web_lock_paths (plans/58 A).

While the lock is on, no web request may change a path or program setting; only
the CLI on the box can. With the lock off everything behaves exactly as before,
which the "unlocked" cases pin.
"""

from __future__ import annotations

import time
import zipfile

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from app.shared import settings_registry
from app.shared.config import DayzConfig

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
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200


def _poll_job(client: TestClient, job_id: str, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] not in ("pending", "running"):
            return body
        if time.monotonic() > deadline:
            raise AssertionError(f"job {job_id} did not finish: {body}")
        time.sleep(0.02)


def _lock(client: TestClient) -> None:
    """Turn the lock on from the web (allowed) — this also writes config.json,
    so the next mutate_config loads a fresh copy and a refusal leaves it be."""
    resp = client.put("/api/settings", json={"values": {"web_lock_paths": "true"}})
    assert resp.status_code == 200


def _setting(client: TestClient, key: str):
    for section in client.get("/api/settings").json():
        for entry in section["keys"]:
            if entry["key"] == key:
                return entry
    raise KeyError(key)


# --- the locked set is derived, and the tripwire keeps it honest ------------


def test_web_locked_fields_derived_set():
    locked = settings_registry.web_locked_fields()
    for f in (
        "server_dir", "steamcmd_dir", "expansion_template_dir", "backup_dir",
        "bundle_export_dir", "web_tls_cert", "web_tls_key",  # _absolute_path checks
        "rclone_path", "workshop_content_dir",               # programs / paths, no such check
        "config_path", "profiles_path", "battleye_path",     # RUNCOMMAND_CONFIG_FIELDS
        "web_lock_paths",                                    # can't be lifted from the web
    ):
        assert f in locked, f


def test_tripwire_every_pathish_field_is_decided():
    """Every DayzConfig field whose name ends in a path-ish suffix is either
    locked or explicitly exempted with a reason — never decided by omission."""
    locked = settings_registry.web_locked_fields()
    exempt = settings_registry.NOT_WEB_LOCKED
    suffixes = ("_dir", "_path", "_file", "_cert", "_key")
    for name in DayzConfig.__dataclass_fields__:
        if name.endswith(suffixes):
            assert name in locked or name in exempt, (
                f"{name} ends in a path-ish suffix but is neither locked nor in "
                f"NOT_WEB_LOCKED — decide it"
            )


def test_locked_values_include_server_args_path_flags():
    cfg = DayzConfig(server_args="-adminLog -BEpath=/elsewhere -cpuCount=2")
    values = settings_registry.locked_values(cfg)
    assert values.get("server_args -bepath") == "/elsewhere"


# --- Settings page ----------------------------------------------------------


@pytest.mark.parametrize("key, value", [
    ("server_dir", "/tmp/elsewhere"),
    ("steamcmd_dir", "/tmp/steam2"),
    ("rclone_path", "/tmp/rclone"),
])
def test_locked_settings_refuse_a_path_change(client, key, value):
    _lock(client)
    before = _setting(client, key)["value"]
    resp = client.put("/api/settings", json={"values": {key: value}})
    assert resp.status_code == 403
    assert "web_lock_paths" in resp.json()["detail"]
    assert _setting(client, key)["value"] == before  # unchanged


def test_locked_cannot_lift_the_lock_from_the_web(client):
    _lock(client)
    resp = client.put("/api/settings", json={"values": {"web_lock_paths": "false"}})
    assert resp.status_code == 403
    assert _setting(client, "web_lock_paths")["value"] is True


def test_locked_a_non_path_setting_still_saves(client):
    _lock(client)
    resp = client.put("/api/settings", json={"values": {"steam_username": "bob"}})
    assert resp.status_code == 200
    assert _setting(client, "steam_username")["value"] == "bob"


def test_get_settings_marks_locked_fields(client):
    _lock(client)
    assert _setting(client, "server_dir")["locked"] is True
    assert _setting(client, "steam_username")["locked"] is False


def test_a_lock_set_with_the_cli_holds_without_a_panel_restart(client, sample_config):
    """User test S8 (2026 Q4): the lock is set with the CLI, which writes
    config.json behind the running panel. Settings showed the path fields
    editable, the Run Command form too, and adding an instance (which takes a
    server dir from the browser) was allowed until the panel restarted."""
    from click.testing import CliRunner as _Runner

    from app.commands.settings import settings_group

    sample_config.save()
    assert _setting(client, "server_dir")["locked"] is False
    result = _Runner().invoke(settings_group, ["set", "web_lock_paths", "true"])
    assert result.exit_code == 0, result.output
    assert _setting(client, "server_dir")["locked"] is True
    assert _setting(client, "web_lock_paths")["value"] is True
    assert _runcommand_locked(client, "config_path") is True
    resp = client.post("/api/instances", json={"name": "x", "server_dir": str(sample_config.server_dir_path / "x")})
    assert resp.status_code == 403
    resp = client.put("/api/settings", json={"values": {"steamcmd_dir": "/opt/elsewhere"}})
    assert resp.status_code == 403


def test_unlocked_a_path_change_still_saves(client):
    # Nothing regresses for an install that never turns the lock on.
    resp = client.put("/api/settings", json={"values": {"steamcmd_dir": "/opt/steam2"}})
    assert resp.status_code == 200
    assert _setting(client, "steamcmd_dir")["value"] == "/opt/steam2"
    assert _setting(client, "server_dir")["locked"] is False


# --- Run Command editor, and the E1 source-read reproduction ----------------


def _runcommand_field(client, key):
    for section in client.get("/api/configs/runcommand").json()["sections"]:
        for f in section["fields"]:
            if f["key"] == key:
                return f["value"]
    raise KeyError(key)


def _runcommand_locked(client, key):
    for section in client.get("/api/configs/runcommand").json()["sections"]:
        for f in section["fields"]:
            if f["key"] == key:
                return f.get("locked", False)
    raise KeyError(key)


def test_runcommand_marks_the_path_fields_locked(client):
    """B reads this flag to render the three path fields read-only."""
    assert _runcommand_locked(client, "config_path") is False  # off by default
    _lock(client)
    assert _runcommand_locked(client, "config_path") is True
    assert _runcommand_locked(client, "profiles_path") is True
    assert _runcommand_locked(client, "battleye_path") is True
    # A non-path launch flag is never locked.
    assert _runcommand_locked(client, "adminLog") is False


def test_locked_runcommand_refuses_a_config_path_change(client):
    _lock(client)
    before = _runcommand_field(client, "config_path")
    resp = client.put(
        "/api/configs/runcommand", json={"values": {"config_path": "/etc/other.cfg"}}
    )
    assert resp.status_code == 403
    assert _runcommand_field(client, "config_path") == before


def test_locked_blocks_the_e1_source_read(client, sample_config):
    """E1: repointing config_path at a .py made GET /configs/serverdz/raw serve
    the source. With the lock on the repoint is refused, so it cannot."""
    marker = "SECRET_SOURCE_TOKEN"
    py = sample_config.server_dir_path / "leak.py"
    py.write_text(f"# {marker}\n")

    _lock(client)
    resp = client.put(
        "/api/configs/runcommand", json={"values": {"config_path": str(py)}}
    )
    assert resp.status_code == 403

    raw = client.get("/api/configs/serverdz/raw")
    assert raw.status_code == 200
    assert marker not in raw.text


def test_locked_battleye_save_still_syncs_the_rcon_password(client):
    """The BattlEye editor saves through mutate_config too, but rcon_password
    and rcon_port are credentials, not paths, so the lock lets them through."""
    _lock(client)
    resp = client.put(
        "/api/configs/battleye", json={"values": {"RConPassword": "newpass", "RConPort": "2310"}}
    )
    assert resp.status_code == 200, resp.text
    live = client.app.state.ctx.config
    assert (live.rcon_password, live.rcon_port, live.web_lock_paths) == ("newpass", 2310, True)


# --- a refused save writes nothing ------------------------------------------


def test_a_refused_editor_save_writes_no_file(client, sample_config, server_stopped, monkeypatch):
    """An editor save that writes files and changes a locked field is refused
    with nothing written: no file, no snapshot, no config.json. mutate_config
    checked the lock after fn had run, and by then fn had written the files.
    BattlEye is the editor that writes files; here its save is made to repoint
    config_path as well, which none does today."""
    from app.rcon import rcon_ops
    from app.shared.config_versions import load_index

    adopt = rcon_ops.adopt_beserver_rcon

    def adopt_and_repoint(config, be_data):
        adopt(config, be_data)
        config.config_path = "elsewhere.cfg"

    monkeypatch.setattr(rcon_ops, "adopt_beserver_rcon", adopt_and_repoint)
    _lock(client)
    be_cfg = sample_config.beserver_cfg_path.read_text()
    server_cfg = sample_config.server_cfg_path.read_text()

    resp = client.put(
        "/api/configs/battleye", json={"values": {"RConPassword": "newpass", "BattlEye": True}}
    )
    assert resp.status_code == 403, resp.text
    assert sample_config.beserver_cfg_path.read_text() == be_cfg
    assert sample_config.server_cfg_path.read_text() == server_cfg
    assert load_index(sample_config.paths.versions_dir).entries == []
    saved = DayzConfig.load()
    assert (saved.rcon_password, saved.config_path) == ("", sample_config.config_path)


async def test_the_writes_a_change_returns_run_only_past_the_lock(sample_config, tmp_path):
    """The protocol under that: fn changes the config and returns its file
    writes; mutate_config runs them after the lock check, before the save."""
    from app.web.state import ConfigLocked, WebState

    state = WebState(sample_config)
    written = tmp_path / "written.txt"

    def change(field, value):
        def fn(cfg):
            setattr(cfg, field, value)
            return lambda: written.write_text(value)
        return fn

    # Lock off: written and saved, as before.
    await state.mutate_config(change("config_path", "first.cfg"))
    assert written.read_text() == "first.cfg"
    assert DayzConfig.load().config_path == "first.cfg"

    await state.mutate_config(lambda cfg: setattr(cfg, "web_lock_paths", True))
    # Lock on, a locked field: refused, nothing written or saved.
    with pytest.raises(ConfigLocked):
        await state.mutate_config(change("config_path", "second.cfg"))
    assert written.read_text() == "first.cfg"
    assert DayzConfig.load().config_path == "first.cfg"

    # Lock on, a free field: written and saved.
    await state.mutate_config(change("server_name", "Renamed"))
    assert written.read_text() == "Renamed"
    assert DayzConfig.load().server_name == "Renamed"


def test_unlocked_runcommand_still_saves_config_path(client):
    resp = client.put(
        "/api/configs/runcommand", json={"values": {"config_path": "custom.cfg"}}
    )
    assert resp.status_code == 200
    assert _runcommand_field(client, "config_path") == "custom.cfg"


# --- Full-backup restore keeps locked fields, doesn't fail (A6) -------------


def _write_full_backup(zip_path, config_dict):
    import json

    from app.ops.backup_ops import APP_CONFIG_ARC_NAME

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(APP_CONFIG_ARC_NAME, json.dumps(config_dict))


def test_locked_full_restore_keeps_locked_fields(app_and_config, sample_config, monkeypatch):
    from app.web.state import WebState

    ctx: WebState = app_and_config.state.ctx
    # Lock is on before the restore.
    sample_config.web_lock_paths = True
    sample_config.save()
    ctx.config = sample_config

    fake = type("F", (), {})()
    fake.zip_path = sample_config.server_dir_path / "full.zip"
    _write_full_backup(
        fake.zip_path,
        {
            "web_lock_paths": False,          # locked — must be kept True
            "server_dir": "/somewhere/else",  # locked — must be kept
            "server_name": "From Backup",     # not locked — must be applied
        },
    )
    monkeypatch.setattr("app.web.routers.backups._find_backup", lambda c, t, n: fake)
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)
    monkeypatch.setattr(
        "app.ops.backup_ops.restore_full_backup", lambda config, backup, log: 1
    )

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post(
            "/api/backups/restore",
            json={"type": "full", "name": "full", "restore_app_config": True},
        )
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])
        assert body["status"] == "success", body

    cfg = DayzConfig.load()
    assert cfg.web_lock_paths is True          # lock not lifted
    assert cfg.server_dir == sample_config.server_dir  # path kept
    assert cfg.server_name == "From Backup"    # non-locked field applied


def test_unlocked_full_restore_writes_config_verbatim(app_and_config, sample_config, monkeypatch):
    # With the lock off, restore is the old wholesale extract — byte for byte.
    calls = []
    fake = type("F", (), {})()
    fake.zip_path = sample_config.server_dir_path / "full.zip"
    monkeypatch.setattr("app.web.routers.backups._find_backup", lambda c, t, n: fake)
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)
    monkeypatch.setattr("app.ops.backup_ops.restore_full_backup", lambda config, backup, log: 1)
    monkeypatch.setattr(
        "app.ops.backup_ops.extract_app_config",
        lambda zip_path, config_file: calls.append("extract")
    )

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post(
            "/api/backups/restore",
            json={"type": "full", "name": "full", "restore_app_config": True},
        )
        assert resp.status_code == 200
        _poll_job(client, resp.json()["job_id"])

    assert calls == ["extract"]  # the wholesale write, unchanged


# --- Bundle import keeps locked fields and logs them (A7) --------------------


def test_locked_bundle_import_keeps_locked_fields(app_and_config, sample_config):
    from app.ops.bundle_ops import get_bundle_dir
    from app.web.state import WebState

    ctx: WebState = app_and_config.state.ctx
    sample_config.web_lock_paths = True
    sample_config.save()
    ctx.config = sample_config

    bundle_dir = get_bundle_dir(sample_config)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    zip_path = bundle_dir / "test-bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            "config.json",
            '{"config_path": "/etc/evil.cfg", "server_name": "Imported"}',
        )

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/bundle/import", json={"filename": "test-bundle.zip"})
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])
        assert body["status"] == "success", body
        assert any("kept config_path (locked)" in e["line"] for e in body["log"])

    cfg = DayzConfig.load()
    assert cfg.config_path != "/etc/evil.cfg"  # locked path kept
    assert cfg.server_name == "Imported"       # non-locked field applied


# --- The CLI is never locked ------------------------------------------------


def test_cli_can_lift_the_lock_while_locked(monkeypatch, sample_config):
    from app.commands.settings import settings_group

    sample_config.web_lock_paths = True
    sample_config.save()
    result = CliRunner().invoke(settings_group, ["set", "web_lock_paths", "false"])
    assert result.exit_code == 0, result.output
    assert DayzConfig.load().web_lock_paths is False
