"""Tests for /api/expansion/* (app/web/routers/expansion.py)."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from tests.conftest import write_test_mods

from tests.test_expansion_ops import (  # the same fixture template + class map
    MISSION_EVENTS,
    MISSION_TYPES,
    MOD_CLASSES,
    _write_template,
)

PASSWORD = "testpass123"


@pytest.fixture()
def app_and_config(sample_config, tmp_mission_dir, tmp_path, monkeypatch):
    from app.web import server
    from app.web.auth import hash_password

    root = tmp_path / "expansion-templates"
    _write_template(root, "Chernarus")
    sample_config.expansion_template_dir = str(root)
    (tmp_mission_dir / "db" / "types.xml").write_text(MISSION_TYPES)
    (tmp_mission_dir / "db" / "events.xml").write_text(MISSION_EVENTS)
    (tmp_mission_dir / "cfgeventspawns.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n<eventposdef>\n</eventposdef>\n'
    )
    (tmp_mission_dir / "cfgeconomycore.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n'
        "<economycore>\n\t<classes />\n\t<defaults />\n</economycore>\n"
    )
    write_test_mods(sample_config, ['@DayZ-Expansion-Core', '@DayZ-Expansion-Vehicles', '@DayZ-Expansion-Weapons'])
    monkeypatch.setattr(
        "app.modmgr.mod_conflict.extract_mod_classnames_cached",
        lambda server_dir, mod_list, sections=(), prune=True: {
            m: MOD_CLASSES[m] for m in mod_list if m in MOD_CLASSES
        },
    )

    sample_config.web_password_hash = hash_password(PASSWORD)
    return server.create_app(sample_config), sample_config


@pytest.fixture()
def client(app_and_config):
    app, _config = app_and_config
    tclient = TestClient(app)
    tclient.post("/api/auth/login", json={"password": PASSWORD})
    return tclient


def _poll_job(client: TestClient, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] not in ("pending", "running"):
            return body
        if time.monotonic() > deadline:
            raise AssertionError(f"job {job_id} did not finish: {body}")
        time.sleep(0.02)


def test_expansion_needs_a_session(app_and_config):
    app, _config = app_and_config
    assert TestClient(app).get("/api/expansion/templates").status_code == 401


def test_templates_names_the_one_matching_the_mission(client, sample_config):
    body = client.get("/api/expansion/templates").json()
    assert body["downloaded"] is True
    assert body["mission"] == "dayzOffline.chernarusplus"
    assert body["terrain"] == "chernarusplus"
    assert body["match"] == "Chernarus"
    assert body["map_name"] == "Chernarus"
    assert body["applied"] == []
    assert body["dir"] == sample_config.expansion_template_dir
    assert "DayZ-Expansion-Missions" in body["repo_url"]
    (tpl,) = body["templates"]
    assert tpl["name"] == "Chernarus" and tpl["complete"] is True
    assert tpl["map_folders"] is True


def test_templates_says_so_when_nothing_is_downloaded(client, sample_config):
    sample_config.expansion_template_dir = str(sample_config.server_dir_path / "nope")
    body = client.get("/api/expansion/templates").json()
    assert body["downloaded"] is False
    assert body["templates"] == [] and body["match"] == ""


def test_preview_reports_what_would_be_written_and_dropped(client):
    body = client.get("/api/expansion/mission").json()
    assert body["template"] == "Chernarus"
    assert body["modules"] == ["Core", "Vehicles", "Weapons"]
    assert body["kept"]["types"] == 3 and body["total"]["types"] == 4
    assert body["overrides"] == ["VehicleHatchback02"]
    assert body["dropped_count"] == len(body["dropped"])
    assert {d["name"] for d in body["dropped"]} >= {"Expansion_Longhorn", "M4Suppressor"}
    # The preview must not write anything.
    assert client.get("/api/expansion/templates").json()["applied"] == []


def test_preview_404s_for_a_template_that_is_not_there(client):
    assert client.get("/api/expansion/mission?template=Sakhal").status_code == 404


def test_a_template_name_is_matched_against_the_listing(client, sample_config):
    """`template` names a listed template, never a path, on both the preview
    and the apply (plans/57 E1). The apply refuses before any job starts."""
    root = sample_config.expansion_template_dir
    for name in ("../expansion-templates/Chernarus", f"{root}/Chernarus", ".."):
        assert client.get("/api/expansion/mission", params={"template": name}).status_code == 404, name
        assert client.post("/api/expansion/mission/apply", json={"template": name}).status_code == 404, name


def test_apply_then_remove(app_and_config, tmp_mission_dir):
    app, _config = app_and_config
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})

        resp = client.post("/api/expansion/mission/apply", json={})
        job = _poll_job(client, resp.json()["job_id"])
        assert job["status"] == "success", job
        assert job["result"]["files"] == [
            "expansion-types",
            "expansion-spawnabletypes",
            "expansion-events",
            "expansion-eventspawns",
        ]
        # Every drop is in the job log, not only in the count.
        assert any("Expansion_Longhorn" in entry["line"] for entry in job["log"])
        assert (tmp_mission_dir / "custom" / "user-expansion-types.xml").is_file()

        applied = client.get("/api/expansion/templates").json()["applied"]
        assert [a["kind"] for a in applied] == [
            "types",
            "spawnabletypes",
            "events",
            "cfgeventspawns",
        ]

        removed = client.post("/api/expansion/mission/remove").json()["removed"]
        assert len(removed) == 4
        assert client.get("/api/expansion/templates").json()["applied"] == []
        assert not (tmp_mission_dir / "custom" / "user-expansion-types.xml").exists()


def test_download_refuses_a_folder_zsm_did_not_fill(client, sample_config, monkeypatch):
    folder = sample_config.server_dir_path / "mine"
    folder.mkdir()
    (folder / "notes.txt").write_text("keep me")
    sample_config.expansion_template_dir = str(folder)

    def _no_download(*_a, **_k):
        raise AssertionError("downloaded into a folder it may not empty")

    monkeypatch.setattr("app.maps.map_ops.download_and_extract_mission", _no_download)

    resp = client.post("/api/expansion/templates/download")

    assert resp.status_code == 409
    assert str(folder) in resp.json()["detail"]
    assert (folder / "notes.txt").read_text() == "keep me"


def test_apply_404s_before_anything_is_downloaded(client, sample_config):
    sample_config.expansion_template_dir = str(sample_config.server_dir_path / "nope")
    resp = client.post("/api/expansion/mission/apply", json={})
    assert resp.status_code == 404


def test_market_zone_offers_list_and_add(client, sample_config):
    # plans/48 D, D13: a zone mod's items as a Market category, priced in the
    # request. Every name here is our own; nothing comes from the mods.
    import json

    write_test_mods(sample_config, ['@RadZone', '@DayZ-Expansion-Market'])
    (sample_config.server_dir_path / "@RadZone").mkdir()
    (sample_config.server_dir_path / "@RadZone" / "types.xml").write_text(
        '<type name="ZoneTestSuit"/>\n<type name="ZoneTestMask"/>\n', encoding="utf-8"
    )
    exp = sample_config.server_dir_path / sample_config.profiles_path / "ExpansionMod"
    (exp / "Market").mkdir(parents=True)
    (exp / "Traders").mkdir()
    (exp / "Market" / "Hats.json").write_text(json.dumps({"m_Version": 12, "Items": []}))
    (exp / "Traders" / "Tailor.json").write_text(json.dumps({"Categories": ["Hats"]}))

    body = client.get("/api/expansion/market/zone-offers").json()
    assert [t["id"] for t in body["traders"]] == ["Tailor"]
    assert body["offers"][0]["items"] == ["ZoneTestSuit", "ZoneTestMask"]

    row = {"classname": "ZoneTestSuit", "min_price": 50, "max_price": 90, "min_stock": 1, "max_stock": 20}
    resp = client.post("/api/expansion/market/zone-offers", json={
        "mod": "radzone", "category": "RadZone", "trader_ids": ["Tailor"], "mode": 1, "items": [row],
    })
    assert resp.status_code == 200
    assert resp.json()["added"] == ["ZoneTestSuit"]
    written = json.loads((exp / "Market" / "RadZone.json").read_text(encoding="utf-8"))
    assert written["Items"][0]["ClassName"] == "zonetestsuit"
    assert json.loads((exp / "Traders" / "Tailor.json").read_text(encoding="utf-8"))["Categories"] == ["Hats", "RadZone"]

    resp = client.post("/api/expansion/market/zone-offers", json={
        "mod": "radzone", "category": "RadZone", "items": [{**row, "min_price": 0}],
    })
    assert resp.status_code == 400
