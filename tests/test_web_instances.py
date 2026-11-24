"""Tests for the Instances page's API (app/web/routers/instances.py)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.server import instances
from app.shared.config import DayzConfig
from app.shared.paths import instance_root, instances_file
from app.web.auth import hash_password

PASSWORD = "testpass123"


@pytest.fixture
def running(monkeypatch):
    """Which instances' servers run; empty means none. Both readers see it:
    the listing (the router's import) and the remove check."""
    up: set[str] = set()

    def fake(config=None):
        return config is not None and config.instance_id in up

    monkeypatch.setattr("app.web.routers.instances.is_server_running", fake)
    monkeypatch.setattr("app.server.service_control.is_server_running", fake)
    monkeypatch.setattr(instances, "installed_units", lambda config: [])
    return up


@pytest.fixture
def panel(sample_config, tmp_path, running):
    """A panel serving `main`, with a second instance `pvp` beside it."""
    from app.web import server

    instances.save_new_config(DayzConfig(server_dir=str(tmp_path / "srv" / "main"),
                                         steamcmd_dir="/opt/steamcmd"))
    instances.create_instance("pvp", server_dir=str(tmp_path / "srv" / "pvp"), label="PvP")
    config = DayzConfig.for_instance("main")
    config.web_password_hash = hash_password(PASSWORD)
    client = TestClient(server.create_app(config))
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return client


def _ids(client) -> list[str]:
    return [row["id"] for row in client.get("/api/instances").json()["instances"]]


def test_the_list_shows_every_instance_and_which_one_this_panel_serves(panel, running, tmp_path):
    running.add("pvp")
    data = panel.get("/api/instances").json()
    assert data["current"] == "main"
    assert data["default"] == "main"
    assert data["locked"] is False
    rows = {row["id"]: row for row in data["instances"]}
    assert rows["main"]["current"] is True and rows["pvp"]["current"] is False
    assert rows["pvp"]["label"] == "PvP"
    assert rows["pvp"]["server_dir"] == str(tmp_path / "srv" / "pvp")
    assert rows["pvp"]["ports"] == {"game": 2402, "query": 27116, "rcon": 2405}
    assert rows["pvp"]["running"] is True and rows["main"]["running"] is False
    assert rows["pvp"]["conflicts"] == []


def test_a_missing_config_is_listed_not_hidden(panel):
    (instance_root("pvp") / "config.json").unlink()
    row = next(r for r in panel.get("/api/instances").json()["instances"] if r["id"] == "pvp")
    assert row["missing"] is True and row["ports"] is None and row["server_dir"] == ""


def test_an_unreadable_registry_says_so(panel):
    instances_file().write_text("{not json", encoding="utf-8")
    resp = panel.get("/api/instances")
    assert resp.status_code == 409
    assert "instances.json could not be read" in resp.json()["detail"]


def test_add_creates_an_instance_with_the_next_free_ports(panel, tmp_path):
    resp = panel.post("/api/instances", json={
        "name": "test", "server_dir": str(tmp_path / "srv" / "test"), "label": "Test box"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["ports"] == {"game": 2502, "query": 27216, "rcon": 2505}
    assert _ids(panel) == ["main", "pvp", "test"]
    assert instances.load_registry().get("test").label == "Test box"


def test_add_explains_a_refusal(panel, tmp_path):
    relative = panel.post("/api/instances", json={"name": "x", "server_dir": "srv/x"})
    assert relative.status_code == 400
    assert "absolute path" in relative.json()["detail"]
    shared = panel.post("/api/instances", json={"name": "x", "server_dir": str(tmp_path / "srv" / "pvp")})
    assert shared.status_code == 400
    assert "already uses" in shared.json()["detail"]
    quote = panel.post("/api/instances", json={"name": "x", "server_dir": "/srv/da'yz"})
    assert quote.status_code == 400
    assert "Server directory contains" in quote.json()["detail"]
    clone = panel.post(
        "/api/instances", json={"name": "x", "server_dir": "/srv/%h/x", "source": "pvp"})
    assert clone.status_code == 400
    assert "Server directory contains" in clone.json()["detail"]
    assert _ids(panel) == ["main", "pvp"]


def test_clone_copies_the_source(panel, tmp_path):
    pvp = DayzConfig.for_instance("pvp")
    pvp.server_args = "-doLogs -adminLog"
    pvp.save()
    resp = panel.post("/api/instances", json={
        "name": "pvp2", "server_dir": str(tmp_path / "srv" / "pvp2"), "source": "pvp"})
    assert resp.status_code == 200, resp.text
    assert DayzConfig.for_instance("pvp2").server_args == "-doLogs -adminLog"


def test_clone_of_an_unknown_source_is_a_404(panel, tmp_path):
    resp = panel.post("/api/instances", json={
        "name": "x", "server_dir": str(tmp_path / "srv" / "x"), "source": "nope"})
    assert resp.status_code == 404


def test_the_path_lock_refuses_add_and_clone_only(panel, tmp_path):
    """D: add and clone take a server dir from the browser; the rest stays open."""
    panel.app.state.ctx.config.web_lock_paths = True
    panel.app.state.ctx.config.save()
    resp = panel.post("/api/instances", json={"name": "x", "server_dir": str(tmp_path / "srv" / "x")})
    assert resp.status_code == 403
    assert "web_lock_paths" in resp.json()["detail"]
    assert not instance_root("x").exists()
    assert panel.get("/api/instances").json()["locked"] is True
    assert panel.put("/api/instances/pvp", json={"label": "Renamed"}).status_code == 200
    assert panel.post("/api/instances/pvp/default").status_code == 200
    assert panel.delete("/api/instances/pvp").status_code == 200


def test_rename_changes_the_label_only(panel):
    resp = panel.put("/api/instances/pvp", json={"label": "  Deer   Isle  "})
    assert resp.json() == {"id": "pvp", "label": "Deer Isle"}
    assert _ids(panel) == ["main", "pvp"]
    assert panel.put("/api/instances/pvp", json={"label": "x" * 65}).status_code == 400
    assert panel.put("/api/instances/nope", json={"label": "x"}).status_code == 404


def test_set_default(panel):
    assert panel.post("/api/instances/pvp/default").json() == {"default": "pvp"}
    assert instances.load_registry().default_id == "pvp"
    assert panel.post("/api/instances/nope/default").status_code == 404


def test_remove_keeps_the_files(panel):
    resp = panel.delete("/api/instances/pvp")
    assert resp.status_code == 200, resp.text
    assert resp.json()["kept"] == str(instance_root("pvp"))
    assert (instance_root("pvp") / "config.json").is_file()
    assert _ids(panel) == ["main"]


def test_remove_refuses_the_panels_startup_instance(panel):
    resp = panel.delete("/i/pvp/api/instances/main")
    assert resp.status_code == 409
    assert "The app was started with 'main'" in resp.json()["detail"]
    assert _ids(panel) == ["main", "pvp"]


def test_remove_refuses_the_instance_the_page_is_about(panel, tmp_path):
    panel.post("/api/instances", json={"name": "test", "server_dir": str(tmp_path / "srv" / "test")})
    resp = panel.delete("/i/test/api/instances/test")
    assert resp.status_code == 409
    assert "This page is about 'test'" in resp.json()["detail"]
    assert _ids(panel) == ["main", "pvp", "test"]


def test_remove_refuses_a_running_server_and_installed_units(panel, running, monkeypatch):
    running.add("pvp")
    resp = panel.delete("/api/instances/pvp")
    assert resp.status_code == 409
    assert "is running" in resp.json()["detail"]
    running.clear()
    monkeypatch.setattr(instances, "installed_units", lambda config: ["dayz-server-pvp.service"])
    resp = panel.delete("/api/instances/pvp")
    assert resp.status_code == 409
    assert "dayz-server-pvp.service" in resp.json()["detail"]
    assert _ids(panel) == ["main", "pvp"]


def test_a_viewer_reads_the_list_and_changes_nothing(panel):
    panel.app.state.ctx.users.add("val", "viewerpass1", "viewer")
    viewer = TestClient(panel.app)
    viewer.post("/api/auth/login", json={"username": "val", "password": "viewerpass1"})
    assert viewer.get("/api/instances").status_code == 200
    assert viewer.put("/api/instances/pvp", json={"label": "x"}).status_code == 403
    assert viewer.delete("/api/instances/pvp").status_code == 403


def test_add_takes_a_branch_and_the_list_shows_it(panel, tmp_path):
    resp = panel.post("/api/instances", json={
        "name": "exp", "server_dir": str(tmp_path / "srv" / "exp"), "branch": "experimental"})
    assert resp.status_code == 200, resp.text
    assert any("1042420" in note for note in resp.json()["notes"])
    rows = {row["id"]: row for row in panel.get("/api/instances").json()["instances"]}
    assert rows["exp"]["branch"] == "experimental"
    assert rows["main"]["branch"] == "stable"


def test_an_unknown_branch_is_a_422(panel, tmp_path):
    resp = panel.post("/api/instances", json={
        "name": "exp", "server_dir": str(tmp_path / "srv" / "exp"), "branch": "beta"})
    assert resp.status_code == 422


def test_status_carries_the_branch(panel):
    assert panel.get("/api/status").json()["server_branch"] == "stable"
