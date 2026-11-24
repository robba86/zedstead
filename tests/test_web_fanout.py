"""One panel serving several instances through /i/<id>/ (plans/62 step 4).

The test the step exists for is the byte-identical one: a write made on one
instance's page must never touch another instance's files.
"""

from __future__ import annotations

import hashlib
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.server import instances
from app.shared.config import DayzConfig
from app.shared.paths import instance_root
from app.web.auth import hash_password

PASSWORD = "testpass123"
CFG = (
    'hostname = "{name}";\n'
    'class Missions\n{{\n\tclass DayZ\n\t{{\n\t\ttemplate = "dayzOffline.chernarusplus";\n\t}};\n}};\n'
)


def _server_dir(root: Path, name: str) -> Path:
    server = root / "srv" / name
    mission = server / "mpmissions" / "dayzOffline.chernarusplus"
    (mission / "db").mkdir(parents=True)
    (server / "serverDZ.cfg").write_text(CFG.format(name=name), encoding="utf-8")
    (mission / "db" / "events.xml").write_text(textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <events>
        </events>
    """), encoding="utf-8")
    (mission / "cfgeventspawns.xml").write_text(textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <eventposdef>
        </eventposdef>
    """), encoding="utf-8")
    return server


@pytest.fixture
def quiet(monkeypatch):
    """No systemd and no installed units, wherever the listing asks."""
    monkeypatch.setattr("app.web.routers.instances.is_server_running", lambda config=None: False)
    monkeypatch.setattr(instances, "installed_units", lambda config: [])


@pytest.fixture
def app(tmp_path, quiet):
    """A panel started with `main`, with `pvp` registered beside it."""
    from app.web import server

    steamcmd = str(tmp_path / "steamcmd")
    instances.save_new_config(DayzConfig(
        server_dir=str(_server_dir(tmp_path, "main")), steamcmd_dir=steamcmd,
        web_notifier_enabled=False, web_password_hash=hash_password(PASSWORD)))
    created = instances.create_instance("pvp", server_dir=str(_server_dir(tmp_path, "pvp")), label="PvP")
    created.config.web_notifier_enabled = False
    # A password of pvp's own, which must not sign anybody in: login is the
    # startup instance's.
    created.config.web_password_hash = hash_password("pvp-only-pass")
    created.config.save()
    return server.create_app(DayzConfig.for_instance("main"))


def _login(client: TestClient, password: str = PASSWORD, prefix: str = "") -> int:
    return client.post(f"{prefix}/api/auth/login", json={"password": password}).status_code


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        assert _login(client) == 200
        yield client


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _config_json(instance_id: str) -> Path:
    return instance_root(instance_id) / "config.json"


def _until_pong(ws) -> list[str]:
    """Every message type up to the reply to a ping sent now."""
    ws.send_json({"action": "ping"})
    seen = []
    while (message := ws.receive_json())["type"] != "pong":
        seen.append(message["type"])
    return seen


# -- routing -------------------------------------------------------------


def test_the_lifespan_serves_every_registered_instance(app, client):
    panel = app.state.panel
    assert set(panel.bundles) == {"main", "pvp"}
    assert panel.startup is app.state.ctx
    assert panel.bundles["pvp"].site_heartbeat is not None


def test_a_prefix_reads_that_instances_settings(client, tmp_path):
    def server_dir(prefix: str) -> str:
        sections = client.get(f"{prefix}/api/settings").json()
        return next(k["value"] for s in sections for k in s["keys"] if k["key"] == "server_dir")

    assert server_dir("") == str(tmp_path / "srv" / "main")
    assert server_dir("/i/main") == str(tmp_path / "srv" / "main")
    assert server_dir("/i/pvp") == str(tmp_path / "srv" / "pvp")


def test_a_settings_save_on_pvp_leaves_main_byte_identical(client):
    main_before = _digest(_config_json("main"))

    resp = client.put("/i/pvp/api/settings", json={"values": {"auto_backup_before_restart": "true"}})

    assert resp.status_code == 200, resp.text
    assert DayzConfig.for_instance("pvp").auto_backup_before_restart is True
    assert _digest(_config_json("main")) == main_before


def test_an_editor_save_on_pvp_leaves_main_byte_identical(client, tmp_path):
    main_cfg = tmp_path / "srv" / "main" / "serverDZ.cfg"
    main_before = _digest(main_cfg)
    new = CFG.format(name="PvP renamed")

    resp = client.put("/i/pvp/api/configs/serverdz/raw", json={"content": new})

    assert resp.status_code == 200, resp.text
    assert (tmp_path / "srv" / "pvp" / "serverDZ.cfg").read_text(encoding="utf-8") == new
    assert _digest(main_cfg) == main_before


def test_a_map_editor_write_on_pvp_leaves_main_byte_identical(client, tmp_path):
    def mission(name: str) -> Path:
        return tmp_path / "srv" / name / "mpmissions" / "dayzOffline.chernarusplus"

    main_before = {p.name: _digest(p) for p in (mission("main") / "db" / "events.xml",
                                                mission("main") / "cfgeventspawns.xml")}

    resp = client.post("/i/pvp/api/map-editor/events", json={"name": "FanOutEvent"})

    assert resp.status_code == 200, resp.text
    assert "FanOutEvent" in (mission("pvp") / "db" / "events.xml").read_text(encoding="utf-8")
    assert {p.name: _digest(p) for p in (mission("main") / "db" / "events.xml",
                                         mission("main") / "cfgeventspawns.xml")} == main_before


def test_the_page_under_a_prefix_is_the_app(client):
    resp = client.get("/i/pvp/mods")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize("path", ["/i/nope/api/status", "/i/NOPE/api/status", "/i//api/status",
                                  "/i/nope/mods", "/i/nope/ws"])
def test_an_unknown_instance_is_404_before_auth(app, path):
    anonymous = TestClient(app)
    resp = anonymous.get(path)
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}


def test_unknown_instances_do_not_make_the_panel_reread_the_registry(app, monkeypatch):
    """plans/68 AUTH-18. A registered instance whose config is missing left
    the panel's stamp unset, and every anonymous request for a made-up id read
    instances.json again and retried that config. Now a miss looks again only
    for a name an instance could have, and at most once per change of the
    file or per `MISS_RETRY_INTERVAL`."""
    instances.save_registry(instances.add_instance(
        instances.load_registry(), instances.ServerInstance("ghost")))
    reads = []
    real_load = instances.load_registry
    monkeypatch.setattr(instances, "load_registry", lambda *a, **k: reads.append(1) or real_load(*a, **k))

    with TestClient(app) as anonymous:
        reads.clear()  # the lifespan's own start-up read
        for path in ["/i/nope/api/status"] * 20 + ["/i/NOPE/api/status", "/i/a.b/api/status"] * 10:
            assert anonymous.get(path).status_code == 404

    assert len(reads) <= 1


def test_an_unknown_instance_closes_the_socket(client):
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/i/nope/ws") as ws:
            ws.receive_json()
    assert exc_info.value.code == 4404


# -- what stays the panel's ----------------------------------------------


def test_me_is_the_same_user_on_every_instance(client):
    plain, pvp = client.get("/api/auth/me").json(), client.get("/i/pvp/api/auth/me").json()
    assert (plain["username"], plain["role"]) == (pvp["username"], pvp["role"])
    assert plain["instance"]["label"] != pvp["instance"]["label"]


def test_login_checks_the_startup_instance_only(app, client):
    other = TestClient(app)
    assert _login(other, "pvp-only-pass", prefix="/i/pvp") == 401
    assert _login(other, PASSWORD, prefix="/i/pvp") == 200


def test_a_viewer_is_refused_a_write_on_any_instance(app, client):
    app.state.ctx.users.add("val", "viewerpass1", "viewer")
    viewer = TestClient(app)
    viewer.post("/api/auth/login", json={"username": "val", "password": "viewerpass1"})
    assert viewer.get("/i/pvp/api/settings").status_code == 403  # settings are admin
    assert viewer.put("/i/pvp/api/settings", json={"values": {"chat_log_enabled": "true"}}).status_code == 403
    assert viewer.get("/i/pvp/api/status").status_code == 200


# -- sockets ---------------------------------------------------------------


def test_a_pvp_socket_hears_pvp_and_not_main(client):
    with client.websocket_connect("/i/pvp/ws") as pvp, client.websocket_connect("/ws") as main:
        assert pvp.receive_json()["type"] == "hello"
        assert main.receive_json()["type"] == "hello"

        client.put("/api/settings", json={"values": {"chat_log_enabled": "true"}})
        assert "settings_changed" in _until_pong(main)
        assert "settings_changed" not in _until_pong(pvp)

        client.put("/i/pvp/api/settings", json={"values": {"chat_log_enabled": "true"}})
        assert "settings_changed" in _until_pong(pvp)
        assert "settings_changed" not in _until_pong(main)


# -- the registry changing under a running panel ---------------------------


def test_an_instance_removed_while_the_panel_runs_goes_away(app, client):
    pvp_bundle = app.state.panel.bundles["pvp"]
    with client.websocket_connect("/i/pvp/ws") as ws:
        assert ws.receive_json()["type"] == "hello"

        instances.delete_instance("pvp")
        client.get("/api/instances")  # looks at instances.json now, not in 30 s

        with pytest.raises(WebSocketDisconnect) as exc_info:
            while True:
                ws.receive_json()
        assert exc_info.value.code == 4404

    assert "pvp" not in app.state.panel.bundles
    assert pvp_bundle.site_heartbeat is None  # its services stopped
    assert client.get("/i/pvp/api/status").status_code == 404


def test_an_instance_added_while_the_panel_runs_is_served(app, client, tmp_path):
    resp = client.post("/api/instances", json={
        "name": "test", "server_dir": str(_server_dir(tmp_path, "test"))})
    assert resp.status_code == 200, resp.text

    assert "test" in app.state.panel.bundles
    assert client.get("/i/test/api/status").status_code == 200
    row = next(r for r in client.get("/api/instances").json()["instances"] if r["id"] == "test")
    assert row["served"] is True


def test_an_instance_added_from_the_cli_is_found_on_first_use(app, client, tmp_path):
    instances.create_instance("cli", server_dir=str(_server_dir(tmp_path, "cli")))
    assert client.get("/i/cli/api/status").status_code == 200


def test_an_open_page_hears_an_instance_the_cli_added(app, client, tmp_path):
    """The 30 s refresh announces what it found, so an open page's switcher
    follows a `zsm instances add` without a reload. Found in the container:
    it only did after a change made on the Instances page."""
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        instances.create_instance("cli", server_dir=str(_server_dir(tmp_path, "cli")))

        assert client.portal.call(app.state.panel.refresh) is True
        assert "instances_changed" in _until_pong(ws)
        # Nothing changed the second time, so nothing is said.
        assert client.portal.call(app.state.panel.refresh) is False
        assert "instances_changed" not in _until_pong(ws)


def test_a_change_on_the_instances_page_is_announced_once(client, tmp_path):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        assert client.post("/api/instances", json={
            "name": "one", "server_dir": str(_server_dir(tmp_path, "one"))}).status_code == 200
        assert _until_pong(ws).count("instances_changed") == 1
        assert client.put("/api/instances/one", json={"label": "One"}).status_code == 200
        assert _until_pong(ws).count("instances_changed") == 1


# -- jobs (step 5) -----------------------------------------------------------


def _hold_a_job(app, instance_id: str, client: TestClient):
    """Start an exclusive job on *instance_id* that runs until released."""
    import asyncio

    release = asyncio.Event()

    async def body(job):
        await release.wait()

    bundle = app.state.panel.bundles[instance_id]
    job = client.portal.call(lambda: bundle.jobs.start("Hold", "test", body))
    return job, lambda: client.portal.call(release.set)


def test_a_job_on_pvp_takes_the_one_slot_from_main(app, client):
    job, release = _hold_a_job(app, "pvp", client)
    try:
        # A full backup: a quick one on a server that never ran is refused
        # before the job slot is asked (ea5cd013).
        resp = client.post("/api/backups", json={"type": "full"})
        assert resp.status_code == 409
        assert resp.json()["active_job_id"] == job.id
    finally:
        release()


def test_a_job_says_which_instance_it_is_for(app, client):
    job, release = _hold_a_job(app, "pvp", client)
    try:
        listed = {j["id"]: j for j in client.get("/api/jobs").json()}
        assert listed[job.id]["instance"] == "pvp"
        assert client.get(f"/i/main/api/jobs/{job.id}").json()["instance"] == "pvp"
    finally:
        release()


def test_a_jobs_status_reaches_every_instances_pages(app, client):
    with client.websocket_connect("/ws") as main:
        assert main.receive_json()["type"] == "hello"
        job, release = _hold_a_job(app, "pvp", client)
        release()
        while (message := main.receive_json())["type"] != "job_status" or \
                message["job"]["status"] == "running":
            pass
        assert message["job"]["instance"] == "pvp"


def test_the_steam_answer_is_served_cached_while_any_instance_runs_a_job(app, client, monkeypatch):
    import app.shared.steam as steam

    main = app.state.panel.bundles["main"]
    main.note_steam_ok(False)
    monkeypatch.setattr(steam, "check_steam_login", lambda *a, **k: pytest.fail("probed beside a job"))
    job, release = _hold_a_job(app, "pvp", client)
    try:
        assert client.portal.call(main.steam_probe) is False
    finally:
        release()


# -- panel settings and the path lock (step 6) -------------------------------


def _setting(client, prefix: str, key: str) -> dict:
    sections = client.get(f"{prefix}/api/settings").json()
    return next(k for s in sections for k in s["keys"] if k["key"] == key)


def test_a_panel_setting_is_read_only_on_another_instance(client):
    assert "panel" not in _setting(client, "", "web_bind_host")
    on_pvp = _setting(client, "/i/pvp", "web_bind_host")
    assert on_pvp["panel"]["instance"] == "main"
    assert "Edit it on main" in on_pvp["panel"]["note"]
    assert "panel" not in _setting(client, "/i/pvp", "chat_log_enabled")


def test_a_panel_setting_save_on_another_instance_is_refused(client):
    before = _digest(_config_json("pvp"))
    resp = client.put("/i/pvp/api/settings", json={"values": {"web_bind_host": "0.0.0.0"}})
    assert resp.status_code == 409
    assert "web_bind_host" in resp.json()["detail"]
    assert _digest(_config_json("pvp")) == before
    # On the startup instance it is an ordinary setting.
    assert client.put("/api/settings", json={"values": {"web_bind_host": "0.0.0.0"}}).status_code == 200


def _lock(instance_id: str) -> None:
    config = DayzConfig.for_instance(instance_id)
    config.web_lock_paths = True
    config.save()


def test_the_startup_instances_lock_covers_every_instance(client, tmp_path):
    _lock("main")
    before = _digest(_config_json("pvp"))

    resp = client.put("/i/pvp/api/settings", json={"values": {"steamcmd_dir": str(tmp_path / "evil")}})

    assert resp.status_code == 403
    assert "--instance main settings set web_lock_paths false" in resp.json()["detail"]
    assert _digest(_config_json("pvp")) == before
    assert _setting(client, "/i/pvp", "steamcmd_dir")["locked"] is True
    # Nothing a path does not touch is locked.
    assert client.put("/i/pvp/api/settings", json={"values": {"chat_log_enabled": "true"}}).status_code == 200


def test_another_instances_own_lock_stays_its_own(client, tmp_path):
    _lock("pvp")
    assert client.put("/i/pvp/api/settings", json={
        "values": {"steamcmd_dir": str(tmp_path / "x")}}).status_code == 403
    assert client.put("/api/settings", json={
        "values": {"steamcmd_dir": str(tmp_path / "y")}}).status_code == 200


def test_an_instances_own_lock_names_that_instance(client, tmp_path):
    """Turning pvp's own lock off was refused as
    "web_lock_paths is on for 'main'" (decided after the change), and a path
    refusal gave a command without --instance, which lifts main's."""
    _lock("pvp")
    for values in ({"web_lock_paths": "false"}, {"steamcmd_dir": str(tmp_path / "x")}):
        resp = client.put("/i/pvp/api/settings", json={"values": values})
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert "`zsm --instance pvp settings set web_lock_paths false`" in detail, detail
        assert "'main'" not in detail


def test_an_instance_may_lock_itself_under_the_panels_lock(client):
    _lock("main")
    resp = client.put("/i/pvp/api/settings", json={"values": {"web_lock_paths": "true"}})
    assert resp.status_code == 200, resp.text
    assert DayzConfig.for_instance("pvp").web_lock_paths is True


def test_adding_an_instance_from_another_instances_page_honours_the_panels_lock(client, tmp_path):
    _lock("main")
    resp = client.post("/i/pvp/api/instances", json={"name": "x", "server_dir": str(tmp_path / "x")})
    assert resp.status_code == 403
    assert client.get("/i/pvp/api/instances").json()["locked"] is True


# -- the switcher and the login picker (step 7, D10) -------------------------


def test_login_names_the_instances_to_open(app):
    with TestClient(app) as client:
        body = client.post("/api/auth/login", json={"password": PASSWORD}).json()
    assert body["startup"] == "main"
    assert body["instances"] == [{"id": "main", "label": "main", "branch": "stable"},
                                 {"id": "pvp", "label": "PvP", "branch": "stable"}]


def test_me_carries_the_switcher_on_every_instance(client):
    for prefix in ("", "/i/pvp"):
        me = client.get(f"{prefix}/api/auth/me").json()
        assert [row["id"] for row in me["instances"]] == ["main", "pvp"]
        assert me["startup"] == "main"
    assert client.get("/i/pvp/api/auth/me").json()["instance"]["id"] == "pvp"


def test_one_instance_answers_as_before(sample_config):
    from app.web.server import create_app

    sample_config.web_password_hash = hash_password(PASSWORD)
    with TestClient(create_app(sample_config)) as client:
        assert client.post("/api/auth/login", json={"password": PASSWORD}).json() == {"ok": True}
        me = client.get("/api/auth/me").json()
    assert "instances" not in me and "startup" not in me
