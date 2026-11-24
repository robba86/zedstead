"""GET /api/update/conflicts and POST /api/update/conflicts/resolve (plans/67)."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.ops import mission_stock
from tests.test_mission_stock import (
    CHERNARUS, EDITED_GLOBALS, LIVONIA, NEW_BUILD, STOCK, _download, _edit, _forget_stock, _install, _path,
)

PASSWORD = "testpass123"


def _app(config, monkeypatch):
    from app.web import server
    from app.web.auth import hash_password

    config.web_password_hash = hash_password(PASSWORD)
    return server.create_app(config)


def _conflicted(config):
    """A server with one whole-file conflict waiting in each stock mission
    (no old stock kept, so nothing is merged value by value)."""
    config.mission_update_conflict = "ask"
    _install(config, STOCK, "100")
    mission_stock.after_download(config, mission_stock.before_download(config))
    _forget_stock(config)
    _edit(config, f"{CHERNARUS}/db/globals.xml", EDITED_GLOBALS)
    _edit(config, f"{LIVONIA}/db/globals.xml", EDITED_GLOBALS)
    new_build = {**NEW_BUILD, f"{LIVONIA}/db/globals.xml": NEW_BUILD[f"{CHERNARUS}/db/globals.xml"]}
    _download(config, new_build, "200")
    return new_build


def _wait(client, job_id) -> dict:
    deadline = time.monotonic() + 3.0
    while True:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in ("pending", "running"):
            return job
        assert time.monotonic() < deadline, job
        time.sleep(0.02)


def test_the_list_names_each_waiting_file_and_the_policy(sample_config, monkeypatch):
    _conflicted(sample_config)
    with TestClient(_app(sample_config, monkeypatch)) as client:
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        body = client.get("/api/update/conflicts").json()
    assert body["policy"] == "ask"
    assert [row["file"] for row in body["conflicts"]] == [f"{CHERNARUS}/db/globals.xml", f"{LIVONIA}/db/globals.xml"]
    assert body["conflicts"][0]["build_after"] == "200"


def test_keep_mine_puts_it_back_and_merges_the_configured_mission(sample_config, monkeypatch):
    _conflicted(sample_config)
    merged: list[str] = []
    monkeypatch.setattr("app.ops.pipeline._merge_xml_sync", lambda ctx: merged.append("merge") or CHERNARUS)
    with TestClient(_app(sample_config, monkeypatch)) as client:
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        resp = client.post("/api/update/conflicts/resolve",
                           json={"keep": "mine", "files": [f"{CHERNARUS}/db/globals.xml"]})
        job = _wait(client, resp.json()["job_id"])
        left = client.get("/api/update/conflicts").json()["conflicts"]
    assert job["status"] == "success", job
    assert job["result"] == {"resolved": [f"{CHERNARUS}/db/globals.xml"], "merged": True}
    assert merged == ["merge"]
    assert _path(sample_config, f"{CHERNARUS}/db/globals.xml").read_text() == EDITED_GLOBALS
    assert [row["file"] for row in left] == [f"{LIVONIA}/db/globals.xml"]


def test_keep_dayz_for_all_merges_nothing(sample_config, monkeypatch):
    new_build = _conflicted(sample_config)
    monkeypatch.setattr("app.ops.pipeline._merge_xml_sync", lambda ctx: (_ for _ in ()).throw(AssertionError))
    with TestClient(_app(sample_config, monkeypatch)) as client:
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        job = _wait(client, client.post("/api/update/conflicts/resolve", json={"keep": "dayz"}).json()["job_id"])
        left = client.get("/api/update/conflicts").json()["conflicts"]
    assert job["status"] == "success", job
    assert job["result"]["merged"] is False
    assert left == []
    assert _path(sample_config, f"{LIVONIA}/db/globals.xml").read_text() == new_build[f"{LIVONIA}/db/globals.xml"]


def test_the_diff_route_serves_a_waiting_file_and_404s_the_rest(sample_config, monkeypatch):
    _conflicted(sample_config)
    with TestClient(_app(sample_config, monkeypatch)) as client:
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        ok = client.get("/api/update/conflicts/diff", params={"file": f"{CHERNARUS}/db/globals.xml"})
        missing = client.get("/api/update/conflicts/diff", params={"file": f"{CHERNARUS}/init.c"})
    assert ok.status_code == 200
    assert 'value="11"' in ok.json()["diff"]
    assert missing.status_code == 404


def test_an_unknown_side_is_refused(sample_config, monkeypatch):
    with TestClient(_app(sample_config, monkeypatch)) as client:
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        assert client.post("/api/update/conflicts/resolve", json={"keep": "both"}).status_code == 422


# --- merged files (plans/71) -----------------------------------------------------


def _merged_conflict(config):
    """A merged globals.xml with one value both sides changed, waiting (ask)."""
    from tests.test_mission_stock import G_REL, STOCK2, _globals

    _install(config, STOCK2, "100")
    mission_stock.after_download(config, mission_stock.before_download(config), remerge_mods=False)
    config.mission_update_conflict = "ask"
    _edit(config, G_REL, _globals(TimeLogin=11, TimeLogout=30))
    _download(config, {**STOCK2, G_REL: _globals(TimeLogin=20)}, "200")
    return G_REL


def test_the_list_carries_a_merged_files_values(sample_config, monkeypatch):
    rel = _merged_conflict(sample_config)
    with TestClient(_app(sample_config, monkeypatch)) as client:
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        [row] = client.get("/api/update/conflicts").json()["conflicts"]
    assert row["file"] == rel and row["merged"] is True
    [value] = row["values"]
    assert (value["entry"], value["mine"], value["theirs"], value["waiting"]) == ("TimeLogin", "11", "20", True)


def test_the_values_route_writes_my_value_and_merges_the_mods(sample_config, monkeypatch):
    rel = _merged_conflict(sample_config)
    merged: list[str] = []
    monkeypatch.setattr("app.ops.pipeline._merge_xml_sync", lambda ctx: merged.append("merge") or CHERNARUS)
    with TestClient(_app(sample_config, monkeypatch)) as client:
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        [value] = client.get("/api/update/conflicts").json()["conflicts"][0]["values"]
        job = _wait(client, client.post("/api/update/conflicts/values", json={"file": rel, "ids": [value["id"]], "keep": "mine"}).json()["job_id"])
        left = client.get("/api/update/conflicts").json()["conflicts"]
    assert job["status"] == "success", job
    assert job["result"] == {"settled": [value["id"]], "merged": True}
    assert merged == ["merge"] and left == []
    assert 'name="TimeLogin" type="0" value="11"' in _path(sample_config, rel).read_text()


def test_the_values_route_refuses_an_unknown_side(sample_config, monkeypatch):
    with TestClient(_app(sample_config, monkeypatch)) as client:
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        response = client.post("/api/update/conflicts/values", json={"file": "x", "ids": ["1"], "keep": "both"})
    assert response.status_code == 422


def test_dayzs_whole_file_on_a_merged_file_merges_the_mods_back(sample_config, monkeypatch):
    rel = _merged_conflict(sample_config)
    merged: list[str] = []
    monkeypatch.setattr("app.ops.pipeline._merge_xml_sync", lambda ctx: merged.append("merge") or CHERNARUS)
    with TestClient(_app(sample_config, monkeypatch)) as client:
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        job = _wait(client, client.post("/api/update/conflicts/resolve", json={"keep": "dayz", "files": [rel]}).json()["job_id"])
    assert job["status"] == "success", job
    assert job["result"]["merged"] is True and merged == ["merge"]
