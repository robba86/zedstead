"""`web_allowed_hosts`: the panel answers only to the names it lists (plans/68 NET-9).

What it stops: a page whose own name has been pointed at 127.0.0.1 (DNS
rebinding) reaching a panel bound to loopback. Its requests carry that page's
name in ``Host``, which a page cannot change. Empty, the default, has to leave
every existing install exactly as it was.
"""

from __future__ import annotations

from pathlib import Path

import click
import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.shared import settings_registry
from app.web import allowed_hosts
from app.web.auth import hash_password
from tests.conftest import site_doc

PASSWORD = "testpass123"
LISTED = "zsm.example.com, localhost"
# Copied into each websocket_connect: it adds its handshake headers to the
# dict it is given.
REBOUND = {"host": "attacker.example:8433"}


def _app(sample_config, hosts: str = "", *, save: bool = False):
    from app.web import server

    sample_config.web_password_hash = hash_password(PASSWORD)
    sample_config.web_allowed_hosts = hosts
    if save:
        sample_config.save()
    return server.create_app(sample_config)


def _signed_in(app, host: str = "zsm.example.com") -> TestClient:
    client = TestClient(app, base_url=f"http://{host}")
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return client


# ── off by default ──────────────────────────────────────────────────────


def test_empty_answers_to_any_host_as_before(sample_config):
    app = _app(sample_config)
    client = TestClient(app)

    assert client.get("/api/health", headers=REBOUND).status_code == 200
    assert client.post(
        "/api/auth/login", json={"password": PASSWORD}, headers=REBOUND
    ).status_code == 200
    with client.websocket_connect("/ws", headers=dict(REBOUND)) as ws:
        assert ws.receive_json()["type"] == "hello"


# ── set ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["/api/health", "/", "/api/status"])
def test_an_unlisted_host_gets_400_on_every_path(sample_config, path):
    """The health check and the SPA page included: the panel does not answer
    to that name at all. The refusal still carries the security headers."""
    client = TestClient(_app(sample_config, LISTED))

    resp = client.get(path, headers=REBOUND)
    assert resp.status_code == 400
    assert "web_allowed_hosts" in resp.json()["detail"]
    assert resp.headers["X-Frame-Options"] == "DENY"


def test_a_rebound_page_cannot_reach_the_login_form(sample_config):
    """The route that answers without a session, and the reason for this:
    guessing the password from the victim's browser."""
    client = TestClient(_app(sample_config, LISTED))

    resp = client.post("/api/auth/login", json={"password": PASSWORD}, headers=REBOUND)
    assert resp.status_code == 400
    assert "set-cookie" not in resp.headers


@pytest.mark.parametrize("host", [
    "zsm.example.com", "ZSM.Example.COM:443", "zsm.example.com.", "localhost:8433",
])
def test_a_listed_host_is_answered_whatever_its_port_and_case(sample_config, host):
    client = TestClient(_app(sample_config, LISTED))
    assert client.get("/api/health", headers={"host": host}).status_code == 200


def test_ipv6_literals_are_compared_without_brackets_or_port(sample_config):
    client = TestClient(_app(sample_config, "::1"))
    assert client.get("/api/health", headers={"host": "[::1]:8433"}).status_code == 200
    assert client.get("/api/health", headers={"host": "[::2]:8433"}).status_code == 400


def test_a_missing_host_header_matches_no_entry():
    """No list can hold the empty name, so a request without Host is refused."""
    assert allowed_hosts.request_host(None) == ""
    assert "" not in settings_registry.host_entries(" , zsm.example.com, ,")


def test_x_forwarded_host_is_not_consulted(sample_config):
    """A rebound page is same-origin to the browser, so it can set this
    header; it cannot set Host."""
    client = TestClient(_app(sample_config, LISTED))
    resp = client.get(
        "/api/health", headers={**REBOUND, "x-forwarded-host": "zsm.example.com"}
    )
    assert resp.status_code == 400


def test_a_websocket_from_an_unlisted_host_is_closed_by_policy(sample_config):
    # The test client's socket always goes to ws://testserver, and the session
    # cookie with it only from a client signed in there.
    app = _app(sample_config, "testserver")
    client = _signed_in(app, "testserver")

    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect("/ws", headers=dict(REBOUND)) as ws:
            ws.receive_json()
    assert refused.value.code == allowed_hosts.CLOSE_POLICY == 1008

    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"


# ── /i/<id>/ pages ──────────────────────────────────────────────────────


def _instance_server(root: Path, name: str) -> str:
    server = root / "srv" / name
    (server / "mpmissions" / "dayzOffline.chernarusplus").mkdir(parents=True)
    (server / "serverDZ.cfg").write_text(
        'hostname = "x";\nclass Missions\n{\n\tclass DayZ\n\t{\n'
        '\t\ttemplate = "dayzOffline.chernarusplus";\n\t};\n};\n',
        encoding="utf-8",
    )
    return str(server)


def test_instance_paths_follow_the_same_rule(tmp_path, monkeypatch):
    """The setting is the startup instance's (a panel setting) and covers
    every instance's pages. It runs before the id is looked up, so a made-up
    id is refused for the name, not answered 404."""
    from app.server import instances
    from app.shared.config import DayzConfig
    from app.web import server

    monkeypatch.setattr("app.web.routers.instances.is_server_running", lambda config=None: False)
    instances.save_new_config(DayzConfig(
        server_dir=_instance_server(tmp_path, "main"), steamcmd_dir=str(tmp_path / "steamcmd"),
        web_notifier_enabled=False, web_password_hash=hash_password(PASSWORD),
        web_allowed_hosts=LISTED))
    pvp = instances.create_instance("pvp", server_dir=_instance_server(tmp_path, "pvp")).config
    pvp.web_notifier_enabled = False
    pvp.save()
    app = server.create_app(DayzConfig.for_instance("main"))

    with TestClient(app, base_url="http://zsm.example.com") as client:
        assert client.get("/i/pvp/api/health").status_code == 200
        assert client.get("/i/pvp/api/health", headers=REBOUND).status_code == 400
        assert client.get("/i/nope/api/health").status_code == 404
        assert client.get("/i/nope/api/health", headers=REBOUND).status_code == 400

        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        with pytest.raises(WebSocketDisconnect) as refused:
            with client.websocket_connect("/i/pvp/ws", headers=dict(REBOUND)) as ws:
                ws.receive_json()
        assert refused.value.code == 1008


# ── changing it, and getting back in ────────────────────────────────────


def test_a_save_from_the_panel_applies_to_the_next_request(sample_config):
    client = _signed_in(_app(sample_config))
    assert client.get("/api/health", headers=REBOUND).status_code == 200

    assert client.put(
        "/api/settings", json={"values": {"web_allowed_hosts": LISTED}}
    ).status_code == 200

    assert client.get("/api/health", headers=REBOUND).status_code == 400
    assert client.get("/api/auth/me").status_code == 200


def test_a_panel_save_that_leaves_out_this_browsers_host_is_refused(sample_config):
    """The typo would refuse the browser's next request, and only the CLI on
    the server could undo it. The refusal names the host so it can be added,
    and nothing in the request is saved, the other keys included."""
    client = _signed_in(_app(sample_config, save=True))

    resp = client.put("/api/settings", json={"values": {
        "web_allowed_hosts": "zsm.exmaple.com, localhost", "backup_max_count": "42"}})
    assert resp.status_code == 400
    assert "zsm.example.com" in resp.json()["detail"]

    on_disk = sample_config.reloaded()
    assert on_disk.web_allowed_hosts == ""
    assert on_disk.backup_max_count != 42
    assert client.get("/api/health", headers=REBOUND).status_code == 200


@pytest.mark.parametrize(("hosts", "host"), [
    ("zsm.example.com", "ZSM.Example.COM:8433"),
    ("ZSM.example.com, localhost", "zsm.example.com"),
    ("::1", "[::1]:8433"),
])
def test_a_panel_save_that_lists_this_browsers_host_is_saved(sample_config, hosts, host):
    """Compared as the middleware compares: case-insensitive, port ignored."""
    client = _signed_in(_app(sample_config, save=True))

    resp = client.put("/api/settings", json={"values": {"web_allowed_hosts": hosts}},
                      headers={"host": host})
    assert resp.status_code == 200, resp.text
    assert sample_config.reloaded().web_allowed_hosts == hosts
    assert client.get("/api/health", headers={"host": host}).status_code == 200


def test_clearing_the_list_from_the_panel_is_always_saved(sample_config):
    client = _signed_in(_app(sample_config, LISTED, save=True))

    assert client.put(
        "/api/settings", json={"values": {"web_allowed_hosts": ""}}
    ).status_code == 200
    assert sample_config.reloaded().web_allowed_hosts == ""
    assert client.get("/api/health", headers=REBOUND).status_code == 200


@pytest.mark.parametrize("locked", [False, True])
@pytest.mark.parametrize("in_backup", [{"web_allowed_hosts": "old.example.net"}, {}],
                         ids=["another list", "no list"])
def test_a_full_restore_keeps_this_panels_list(sample_config, monkeypatch, tmp_path,
                                               locked, in_backup):
    """The backup's list was right for the names the panel answered to when
    it was taken. Restored, it would refuse the browser running the restore,
    from the job's next poll on. A backup without the key would clear it."""
    import json
    import time
    import zipfile

    from app.ops.backup_ops import APP_CONFIG_ARC_NAME

    sample_config.web_lock_paths = locked
    app = _app(sample_config, LISTED, save=True)
    backup = type("Full", (), {"zip_path": tmp_path / "full.zip"})()
    with zipfile.ZipFile(backup.zip_path, "w") as zf:
        zf.writestr(APP_CONFIG_ARC_NAME, json.dumps(
            {**in_backup, "server_name": "From Backup", "web_lock_paths": locked}))
    monkeypatch.setattr("app.web.routers.backups._find_backup", lambda config, t, n: backup)
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)
    monkeypatch.setattr("app.ops.backup_ops.restore_full_backup", lambda config, b, log: 0)
    monkeypatch.setattr(allowed_hosts, "REREAD_SECONDS", 0.0)

    with TestClient(app, base_url="http://zsm.example.com") as client:
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        resp = client.post("/api/backups/restore", json={
            "type": "full", "name": "full", "restore_app_config": True})
        assert resp.status_code == 200
        for _ in range(250):
            body = client.get(f"/api/jobs/{resp.json()['job_id']}").json()
            if body["status"] not in ("pending", "running"):
                break
            time.sleep(0.02)
        assert body["status"] == "success", body
        assert client.get("/api/health").status_code == 200

    lines = [e["line"] for e in body["log"]]
    assert "kept web_allowed_hosts (the host names this app answers to)" in lines
    on_disk = sample_config.reloaded()
    assert on_disk.web_allowed_hosts == LISTED
    assert on_disk.server_name == "From Backup"


def test_the_cli_saves_a_list_without_asking_about_hosts(sample_config):
    """The CLI is the way back, so it takes any well-formed list."""
    from app.commands.settings import settings_group

    sample_config.save()
    result = CliRunner().invoke(settings_group, ["set", "web_allowed_hosts", "zsm.exmaple.com"])
    assert result.exit_code == 0, result.output
    assert sample_config.reloaded().web_allowed_hosts == "zsm.exmaple.com"


def test_a_typo_is_undone_from_the_cli_without_a_restart(sample_config, monkeypatch):
    """The way back that the help text promises: the browser is refused, and
    `zsm settings set web_allowed_hosts ""` on the server lets it in again
    on the running panel."""
    from app.commands.settings import settings_group

    monkeypatch.setattr(allowed_hosts, "REREAD_SECONDS", 0.0)
    client = TestClient(_app(sample_config, "zsm.exmaple.com", save=True),
                        base_url="http://zsm.example.com")
    assert client.get("/api/health").status_code == 400

    result = CliRunner().invoke(settings_group, ["set", "web_allowed_hosts", ""])
    assert result.exit_code == 0, result.output

    assert client.get("/api/health").status_code == 200
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200


def test_an_unreadable_config_file_leaves_the_panels_copy_in_force(sample_config, monkeypatch):
    """A half-written config.json must neither lift the list nor refuse
    every request with a 500."""
    def _unreadable(self):
        raise ValueError("half-written")

    monkeypatch.setattr(allowed_hosts, "REREAD_SECONDS", 0.0)
    client = TestClient(_app(sample_config, LISTED, save=True))
    assert client.get("/api/health", headers=REBOUND).status_code == 400

    monkeypatch.setattr(type(sample_config), "reloaded", _unreadable)
    assert client.get("/api/health", headers=REBOUND).status_code == 400
    assert client.get("/api/health", headers={"host": "localhost"}).status_code == 200


# ── the setting ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["", LISTED, " zsm.example.com ,localhost, ", "127.0.0.1, ::1"])
def test_the_setting_takes_names_and_addresses(value):
    setting = settings_registry._BY_KEY["web_allowed_hosts"]
    assert settings_registry._parse(setting, value) == value


@pytest.mark.parametrize("value", [
    "zsm.example.com:443", "https://zsm.example.com", "zsm example.com", "*", "[::1]",
])
def test_the_setting_refuses_what_no_host_header_could_match(value):
    setting = settings_registry._BY_KEY["web_allowed_hosts"]
    with pytest.raises(click.BadParameter, match="web_allowed_hosts lists"):
        settings_registry._parse(setting, value)


def test_it_is_a_panel_setting_and_its_help_says_how_to_undo_it():
    setting = settings_registry._BY_KEY["web_allowed_hosts"]
    assert setting.panel
    assert setting.section == settings_registry._BY_KEY["web_trusted_proxies"].section
    assert "zsm.example.com, localhost" in setting.description
    assert "rebinding" in setting.description
    assert 'zsm settings set web_allowed_hosts ""' in setting.description


def test_the_reverse_proxy_guide_lists_it():
    guide = site_doc("reverse-proxy.md").read_text(encoding="utf-8")
    row = next(line for line in guide.splitlines() if line.startswith("| `web_allowed_hosts`"))
    assert "X-Forwarded-Host" in row
