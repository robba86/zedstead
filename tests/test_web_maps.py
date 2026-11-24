"""Tests for /api/maps* (app/web/routers/maps.py)."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from tests.conftest import installed_names, write_test_mods

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


def _poll_job(client: TestClient, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] not in ("pending", "running"):
            return body
        if time.monotonic() > deadline:
            raise AssertionError(f"job {job_id} did not finish in time: {body}")
        time.sleep(0.02)


def test_requires_auth(sample_config):
    from app.web import server

    app = server.create_app(sample_config)
    tclient = TestClient(app)
    resp = tclient.get("/api/maps")
    assert resp.status_code == 401


def test_get_maps_catalog(client: TestClient):
    resp = client.get("/api/maps")
    assert resp.status_code == 200
    body = resp.json()
    assert body["active"] == "Chernarus"
    ids = {m["id"] for m in body["maps"]}
    assert "Chernarus" in ids
    assert "Namalsk" in ids
    chernarus = next(m for m in body["maps"] if m["id"] == "Chernarus")
    assert chernarus["type"] == "vanilla"
    assert chernarus["active"] is True
    namalsk = next(m for m in body["maps"] if m["id"] == "Namalsk")
    assert namalsk["type"] == "workshop"
    assert namalsk["active"] is False
    # The card carries deps + the full mission block (template/source/zip).
    assert namalsk["deps"]
    assert namalsk["mission_template"] == "regular.namalsk"
    assert namalsk["mission_source"] == "zip"
    assert namalsk["mission_zip_url"].endswith(".zip")


def test_catalog_carries_terrain_for_the_thumbnail(client: TestClient):
    """`terrain` drives the card preview + its Map Editor deep link.

    It is only emitted when an image actually exists for it. No image ships,
    so a fresh install has one for no map; an uploaded one counts.
    `editor_terrain` is derived from the mission template whether or not there
    is an image, so both Namalsk presets land on the one terrain.
    """
    from app.maps import map_data

    terrains = {m["id"]: m["terrain"] for m in client.get("/api/maps").json()["maps"]}
    assert set(terrains.values()) == {None}
    map_data.save_own_image("banov", b"\xff\xd8\xff\xe0" + b"\x00" * 32)
    body = client.get("/api/maps").json()
    terrains = {m["id"]: m["terrain"] for m in body["maps"]}
    assert terrains["Banov"] == "banov"
    for no_image in ("Chernarus", "Livonia", "Sakhal", "Namalsk", "Bitterroot", "Badlands"):
        assert terrains[no_image] is None, no_image
    editor = {m["id"]: m["editor_terrain"] for m in body["maps"]}
    assert editor["Chernarus"] == "chernarusplus"
    assert editor["Livonia"] == "enoch"
    assert editor["Namalsk"] == editor["Namalsk Hardcore"] == "namalsk"
    # Every terrain we hand out has to be servable by the thumbnail route.
    for map_id, terrain in terrains.items():
        if terrain:
            resp = client.get(f"/api/maps/thumbnail?terrain={terrain}")
            assert resp.status_code == 200, f"no thumbnail for {map_id}"


def test_catalog_describes_every_map(client: TestClient):
    """Every catalog entry carries its blurb — an empty card reads as a bug."""
    from app.shared.seed_data import VANILLA_MAPS, WORKSHOP_MAPS

    body = client.get("/api/maps").json()
    known = set(VANILLA_MAPS) | set(WORKSHOP_MAPS)
    assert {m["id"] for m in body["maps"]} == known
    for m in body["maps"]:
        assert m["description"], f"no description for {m['id']}"


def test_unreleased_map_is_listed_but_not_switchable(client: TestClient, unreleased_map):
    """An announced official map with no mission yet: shown, flagged, refused.

    It lives in VANILLA_MAPS like every other Bohemia map, so the guard that
    keeps the greyed-out button honest is the route's own `map_available`
    check — 400, not the 404 an unknown name gets.
    """
    body = client.get("/api/maps").json()
    upcoming = next(m for m in body["maps"] if m["id"] == unreleased_map)
    assert upcoming["type"] == "vanilla"
    assert upcoming["available"] is False
    assert upcoming["active"] is False
    assert upcoming["mission_template"] is None
    assert upcoming["release"]
    assert upcoming["url"].startswith("https://")
    # Every released map stays available.
    assert all(m["available"] for m in body["maps"] if m["id"] != unreleased_map)

    resp = client.post("/api/maps/switch", json={"map_id": unreleased_map})
    assert resp.status_code == 400
    assert "released" in resp.json()["detail"]


def test_an_announced_map_switches_once_an_update_has_brought_its_mission(
    app_and_config, sample_config, unreleased_map
):
    # The catalog names no mission for an announced map, so the folder a
    # download puts in mpmissions/ is what unlocks it, without a zsm release.
    from app.parsers.servercfg import read_mission_template

    db = sample_config.server_dir_path / "mpmissions" / "dayzOffline.testlands" / "db"
    db.mkdir(parents=True)
    (db / "types.xml").write_text("<types/>")
    with TestClient(app_and_config) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        upcoming = next(m for m in client.get("/api/maps").json()["maps"] if m["id"] == unreleased_map)
        assert upcoming["available"] is True
        assert upcoming["mission_template"] == "dayzOffline.testlands"

        resp = client.post("/api/maps/switch", json={"map_id": unreleased_map})
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])
        assert body["status"] == "success", body
        assert client.get("/api/maps").json()["active"] == unreleased_map
    assert read_mission_template(sample_config.server_cfg_path) == "dayzOffline.testlands"


def test_thumbnail_serves_active_map(client: TestClient):
    # sample_config's serverDZ.cfg is dayzOffline.chernarusplus, so the
    # endpoint must resolve the terrain without the map editor state.
    from app.maps import map_data

    map_data.save_own_image("chernarusplus", b"\xff\xd8\xff\xe0" + b"\x00" * 32)
    resp = client.get("/api/maps/thumbnail")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/")
    assert resp.content[:2] == b"\xff\xd8"  # JPEG magic


def test_thumbnail_unknown_terrain_404(client: TestClient):
    resp = client.get("/api/maps/thumbnail?terrain=nosuchmap")
    assert resp.status_code == 404


def test_thumbnail_terrain_cannot_leave_the_image_dirs(client: TestClient, tmp_path, monkeypatch):
    """`terrain` is joined onto the image cache and the bundled dir, so a path
    in it served any image on the box (plans/57 E1). Relative and absolute."""
    from app.maps import map_data

    monkeypatch.setattr(map_data, "maps_dir", lambda: tmp_path / "maps")
    (tmp_path / "maps").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.png").write_bytes(b"not a map")

    for terrain in ("../outside/secret", str(outside / "secret")):
        resp = client.get("/api/maps/thumbnail", params={"terrain": terrain})
        assert resp.status_code == 404, terrain


def test_switch_unknown_map_404(client: TestClient):
    resp = client.post("/api/maps/switch", json={"map_id": "NotARealMap"})
    assert resp.status_code == 404


def test_switch_missing_map_id_422(client: TestClient):
    resp = client.post("/api/maps/switch", json={})
    assert resp.status_code == 422


def test_switch_while_server_running_409(client: TestClient, monkeypatch):
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: True)
    resp = client.post("/api/maps/switch", json={"map_id": "Livonia"})
    assert resp.status_code == 409


def test_switch_vanilla_map_job_success(app_and_config):
    with TestClient(app_and_config) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        resp = client.post("/api/maps/switch", json={"map_id": "Livonia"})
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "success", body
    assert body["result"]["map_id"] == "Livonia"



def test_a_vanilla_switch_applies_the_mods_xml_to_the_new_mission(
    app_and_config, sample_config, monkeypatch,
):
    # A switch selected a mission no merge had ever run on, and plain Start
    # does not merge: the installed mods' loot and events were missing on the
    # new map until someone pressed Re-apply all (browser pass, 2026 Q4).
    from app.ops import pipeline
    from app.parsers.servercfg import read_mission_template

    write_test_mods(sample_config, ['@SomeMod'])
    merged: list[str | None] = []
    monkeypatch.setattr(
        pipeline, "_merge_xml_sync",
        lambda ctx: merged.append(read_mission_template(ctx.config.server_cfg_path)),
    )
    with TestClient(app_and_config) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        resp = client.post("/api/maps/switch", json={"map_id": "Livonia"})
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "success", body
    assert body["result"]["mod_xml_applied"] is True
    assert merged == ["dayzOffline.enoch"]


def test_a_switch_whose_xml_merge_fails_still_switches_and_says_so(
    app_and_config, sample_config, monkeypatch,
):
    from app.ops import pipeline

    write_test_mods(sample_config, ['@SomeMod'])

    def _fail(ctx):
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline, "_merge_xml_sync", _fail)
    with TestClient(app_and_config) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        resp = client.post("/api/maps/switch", json={"map_id": "Livonia"})
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "success", body
    assert body["result"]["mod_xml_applied"] is False
    lines = [entry["line"] for entry in body["log"]]
    assert any("Could not apply mod XML: boom" in line for line in lines)
    assert any("Re-apply all" in line for line in lines)

def test_switch_vanilla_map_updates_active_map(app_and_config):
    with TestClient(app_and_config) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        resp = client.post("/api/maps/switch", json={"map_id": "Livonia"})
        _poll_job(client, resp.json()["job_id"])

        catalog = client.get("/api/maps").json()
        assert catalog["active"] == "Livonia"


def test_switch_workshop_map_job_success(app_and_config, monkeypatch):
    async def _fake_download_mod_async(runner, config, mod_id, mod_name="", max_retries=5):
        return True

    def _fake_extract(zip_url, mission_path, dest_dir, log=None, **kwargs):
        dest_dir.mkdir(parents=True, exist_ok=True)
        if log:
            log("  Extracted 0 file(s).")
        return 0

    monkeypatch.setattr("app.shared.steam.is_mod_downloaded", lambda config, mod_id: True)
    monkeypatch.setattr("app.shared.steam.download_mod_async", _fake_download_mod_async)
    monkeypatch.setattr("app.maps.map_ops.download_and_extract_mission", _fake_extract)
    monkeypatch.setattr("app.ops.fileops.copy_mod_to_server", lambda *a, **k: True)
    monkeypatch.setattr("app.maps.map_ops.apply_recommended_cfg", lambda *a, **k: False)

    with TestClient(app_and_config) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        resp = client.post("/api/maps/switch", json={"map_id": "Namalsk"})
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "success", body
    assert body["result"]["map_id"] == "Namalsk"


def test_switch_workshop_map_logs_the_cfg_keys_it_took_from_the_map(
    app_and_config, sample_config, monkeypatch
):
    async def _fake_download_mod_async(runner, config, mod_id, mod_name="", max_retries=5):
        return True

    def _fake_extract(zip_url, mission_path, dest_dir, log=None, **kwargs):
        dest_dir.mkdir(parents=True, exist_ok=True)
        return 0

    def _fake_cfg(zip_url, server_cfg_path, dest_path):
        dest_path.write_text('hostname = "Namalsk";\nlightingConfig = 1;\n')
        return True

    monkeypatch.setattr("app.shared.steam.is_mod_downloaded", lambda config, mod_id: True)
    monkeypatch.setattr("app.shared.steam.download_mod_async", _fake_download_mod_async)
    monkeypatch.setattr("app.maps.map_ops.download_and_extract_mission", _fake_extract)
    monkeypatch.setattr("app.maps.map_ops.extract_server_cfg_from_zip", _fake_cfg)
    monkeypatch.setattr("app.ops.fileops.copy_mod_to_server", lambda *a, **k: True)

    with TestClient(app_and_config) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        resp = client.post("/api/maps/switch", json={"map_id": "Namalsk"})
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "success", body
    lines = [entry["line"] for entry in body["log"]]
    assert "  lightingConfig: (not set) -> 1" in lines
    assert 'hostname = "Test Server";' in sample_config.server_cfg_path.read_text()


def test_switch_fails_before_mods_txt_when_the_mission_folder_is_missing(
    app_and_config, sample_config, monkeypatch
):
    """An archive without the mission folder ends the job at step 3 (plans/60 G).

    It used to extract 0 files, report success and go on to rewrite mods.txt
    and the mission template, leaving an empty mission.
    """
    async def _fake_download_mod_async(runner, config, mod_id, mod_name="", max_retries=5):
        return True

    def _missing(zip_url, mission_path, dest_dir, log=None, **kwargs):
        raise ValueError(f"Mission folder not found in the archive: {mission_path}")

    mods_list_updates = []
    monkeypatch.setattr("app.shared.steam.is_mod_downloaded", lambda config, mod_id: True)
    monkeypatch.setattr("app.shared.steam.download_mod_async", _fake_download_mod_async)
    monkeypatch.setattr("app.maps.map_ops.download_and_extract_mission", _missing)
    monkeypatch.setattr("app.ops.fileops.copy_mod_to_server", lambda *a, **k: True)
    monkeypatch.setattr(
        "app.maps.map_ops.update_mods_list_for_map",
        lambda *a, **k: mods_list_updates.append(a),
    )
    cfg_before = sample_config.server_cfg_path.read_text()

    with TestClient(app_and_config) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        resp = client.post("/api/maps/switch", json={"map_id": "Banov"})
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "failed", body
    assert "Mission folder not found" in body["error"]
    assert mods_list_updates == []
    assert sample_config.server_cfg_path.read_text() == cfg_before


# ── which serverDZ.cfg a switch leaves: the dialog's choice ──────────
#
# Switching back to a map keeps a copy of the cfg it was left with
# (serverDZ.cfg.<map>.backup). The switch dialog asks whether to bring that
# back or keep the current cfg, and sends `restore_previous`; a client that
# does not send it restores, as every switch did before the dialog asked.


def _saved_cfg_for(sample_config, map_name: str):
    backup = sample_config.server_cfg_path.parent / f"serverDZ.cfg.{map_name.lower()}.backup"
    backup.write_text(
        f'hostname = "Saved {map_name}";\npassword = "from-the-backup";\n'
        '\nclass Missions\n{\n\tclass DayZ\n\t{\n\t\ttemplate = "old";\n\t};\n};\n'
    )
    return backup


def _switch_quietly(monkeypatch, sample_config):
    """Everything a switch does besides serverDZ.cfg, patched where the router
    imports it from. The map's archive hands over Namalsk's own lighting."""
    async def _downloaded(runner, config, mod_id, mod_name="", max_retries=5):
        return True

    def _extract(zip_url, mission_path, dest_dir, log=None, **kwargs):
        dest_dir.mkdir(parents=True, exist_ok=True)
        return 0

    def _recommended_cfg(zip_url, server_cfg_path, dest_path):
        dest_path.write_text('hostname = "Namalsk";\nlightingConfig = 223;\n')
        return True

    async def _service_files(runner, config, **kwargs):
        return True

    monkeypatch.setattr("app.shared.steam.is_mod_downloaded", lambda config, mod_id: True)
    monkeypatch.setattr("app.shared.steam.download_mod_async", _downloaded)
    monkeypatch.setattr("app.maps.map_ops.download_and_extract_mission", _extract)
    monkeypatch.setattr("app.maps.map_ops.extract_server_cfg_from_zip", _recommended_cfg)
    monkeypatch.setattr("app.ops.fileops.copy_mod_to_server", lambda *a, **k: True)
    monkeypatch.setattr("app.server.service_ops.generate_service_files_async", _service_files)
    sample_config.server_cfg_path.write_text(
        'hostname = "Test Server";\npassword = "today";\n'
        '\nclass Missions\n{\n\tclass DayZ\n\t{\n\t\ttemplate = "dayzOffline.chernarusplus";\n\t};\n};\n'
    )


def _switch(app, body: dict) -> list[str]:
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        resp = client.post("/api/maps/switch", json=body)
        assert resp.status_code == 200, resp.text
        job = _poll_job(client, resp.json()["job_id"])
    assert job["status"] == "success", job
    return [entry["line"] for entry in job["log"]]


def test_catalog_says_which_maps_have_a_saved_config(client: TestClient, sample_config):
    """The dialog offers the choice only for a map with a saved cfg, and dates
    it from the file's mtime."""
    backup = _saved_cfg_for(sample_config, "Namalsk")
    maps = {m["id"]: m for m in client.get("/api/maps").json()["maps"]}

    assert maps["Namalsk"]["cfg_backup"] == {"modified": backup.stat().st_mtime}
    assert maps["Livonia"]["cfg_backup"] is None
    assert maps["Namalsk Hardcore"]["cfg_backup"] is None
    # What "your current config" does on top of the mission: only Namalsk's
    # archive carries a recommended cfg.
    assert maps["Namalsk"]["recommended_cfg"] is True
    assert maps["Deer Isle"]["recommended_cfg"] is False
    assert maps["Livonia"]["recommended_cfg"] is False


@pytest.mark.parametrize("choice", [{}, {"restore_previous": True}])
def test_a_workshop_switch_restores_the_saved_config_by_default(
    app_and_config, sample_config, monkeypatch, choice
):
    from app.parsers.servercfg import read_cfg_value, read_mission_template

    _switch_quietly(monkeypatch, sample_config)
    _saved_cfg_for(sample_config, "Namalsk")

    lines = _switch(app_and_config, {"map_id": "Namalsk", **choice})

    cfg = sample_config.server_cfg_path
    assert read_cfg_value(cfg, "hostname") == "Saved Namalsk"
    assert read_cfg_value(cfg, "password") == "today"
    assert read_mission_template(cfg) == "regular.namalsk"
    assert read_cfg_value(cfg, "lightingConfig") is None
    assert "  Restored your saved config for Namalsk (passwords kept)." in lines


def test_a_workshop_switch_can_keep_the_current_config(app_and_config, sample_config, monkeypatch):
    """The first-switch way: the map's own keys go onto today's cfg."""
    from app.parsers.servercfg import read_cfg_value, read_mission_template

    _switch_quietly(monkeypatch, sample_config)
    _saved_cfg_for(sample_config, "Namalsk")

    lines = _switch(app_and_config, {"map_id": "Namalsk", "restore_previous": False})

    cfg = sample_config.server_cfg_path
    assert read_cfg_value(cfg, "hostname") == "Test Server"
    assert read_cfg_value(cfg, "password") == "today"
    assert read_mission_template(cfg) == "regular.namalsk"
    assert read_cfg_value(cfg, "lightingConfig") == "223"
    assert "  Kept your current config and applied the map's recommended keys:" in lines
    assert "  lightingConfig: (not set) -> 223" in lines
    assert not any("Restored" in line for line in lines)


@pytest.mark.parametrize("choice", [{}, {"restore_previous": True}])
def test_a_vanilla_switch_restores_the_saved_config_by_default(
    app_and_config, sample_config, monkeypatch, choice
):
    from app.parsers.servercfg import read_cfg_value, read_mission_template

    _switch_quietly(monkeypatch, sample_config)
    _saved_cfg_for(sample_config, "Livonia")

    lines = _switch(app_and_config, {"map_id": "Livonia", **choice})

    cfg = sample_config.server_cfg_path
    assert read_cfg_value(cfg, "hostname") == "Saved Livonia"
    assert read_cfg_value(cfg, "password") == "today"
    assert read_mission_template(cfg) == "dayzOffline.enoch"
    assert "  Restored your saved config for Livonia (passwords kept)." in lines


def test_a_vanilla_switch_can_keep_the_current_config(app_and_config, sample_config, monkeypatch):
    from app.parsers.servercfg import read_cfg_value, read_mission_template

    _switch_quietly(monkeypatch, sample_config)
    _saved_cfg_for(sample_config, "Livonia")

    lines = _switch(app_and_config, {"map_id": "Livonia", "restore_previous": False})

    cfg = sample_config.server_cfg_path
    assert read_cfg_value(cfg, "hostname") == "Test Server"
    assert read_cfg_value(cfg, "password") == "today"
    assert read_mission_template(cfg) == "dayzOffline.enoch"
    assert "  Kept your current config; only the mission changed." in lines
    assert not any("Restored" in line for line in lines)


def test_switch_conflict_returns_409(app_and_config, monkeypatch):
    def _slow_update(*a, **k):
        time.sleep(0.2)

    monkeypatch.setattr("app.maps.map_ops.update_mods_list_for_map", _slow_update)

    with TestClient(app_and_config) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        first = client.post("/api/maps/switch", json={"map_id": "Livonia"})
        assert first.status_code == 200
        second = client.post("/api/maps/switch", json={"map_id": "Chernarus"})
        assert second.status_code == 409
        _poll_job(client, first.json()["job_id"])


# ── what goes to the new map: the switch dialog's choices (plans/83) ─


def _no_merge(monkeypatch) -> list[str | None]:
    """The closing merge, recorded instead of run: which mission it ran on."""
    from app.ops import pipeline
    from app.parsers.servercfg import read_mission_template

    merged: list[str | None] = []
    monkeypatch.setattr(
        pipeline, "_merge_xml_sync",
        lambda ctx: merged.append(read_mission_template(ctx.config.server_cfg_path)),
    )
    return merged


def _switch_job(app, body: dict) -> dict:
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        resp = client.post("/api/maps/switch", json=body)
        assert resp.status_code == 200, resp.text
        job = _poll_job(client, resp.json()["job_id"])
    assert job["status"] == "success", job
    return job


def _manual(file_id: str) -> None:
    from app.xml import xml_manual
    from tests.conftest import manual_dir

    xml_manual.create_file(file_id, "types", '<types><type name="X"/></types>', manual_dir=manual_dir())


def test_switch_plan_route_lists_what_follows(client: TestClient, sample_config):
    write_test_mods(sample_config, ["@CF", "@Trader", "@Old"], disabled=["@Old"])
    _manual("everywhere")

    resp = client.get("/api/maps/switch-plan", params={"map_id": "Deer Isle"})

    assert resp.status_code == 200, resp.text
    plan = resp.json()
    assert plan["map_id"] == "Deer Isle"
    assert plan["from"] == "Chernarus"
    assert [m["name"] for m in plan["map_mods"]] == ["@DeerIsle", "@CF"]
    assert [m["name"] for m in plan["mods"]] == ["@Trader"]
    assert plan["off_mods"] == ["@Old"]
    assert plan["manual_files"][0]["id"] == "everywhere"
    assert plan["manual_files"][0]["can_leave"] is True
    # The route writes nothing.
    assert installed_names(sample_config) == ["@CF", "@Trader", "@Old"]


def test_switch_plan_unknown_map_404(client: TestClient, unreleased_map):
    assert client.get("/api/maps/switch-plan", params={"map_id": "Atlantis"}).status_code == 404
    assert client.get("/api/maps/switch-plan", params={"map_id": unreleased_map}).status_code == 400


def test_a_switch_with_no_choices_does_what_it_did_before(app_and_config, sample_config, monkeypatch):
    from app.modmgr import modlist
    from app.xml import xml_manual
    from tests.conftest import manual_dir

    _switch_quietly(monkeypatch, sample_config)
    merged = _no_merge(monkeypatch)
    write_test_mods(sample_config, ["@Trader"])
    _manual("everywhere")

    job = _switch_job(app_and_config, {"map_id": "Livonia"})

    assert job["result"] == {
        "map_id": "Livonia", "left_mods": [], "left_manual_files": [], "turned_on": [],
        "mod_xml_applied": True,
    }
    assert modlist.disabled(sample_config) == set()
    assert xml_manual.get_file("everywhere", manual_dir=manual_dir())["terrain"] == ""
    assert merged == ["dayzOffline.enoch"]


def test_a_switch_disables_the_mods_left_behind(app_and_config, sample_config, monkeypatch):
    from app.modmgr import modlist

    _switch_quietly(monkeypatch, sample_config)
    _no_merge(monkeypatch)
    write_test_mods(sample_config, ["@Trader", "@Keep"])

    job = _switch_job(app_and_config, {"map_id": "Livonia", "disable_mods": ["@Trader"]})

    assert job["result"]["left_mods"] == ["@Trader"]
    assert installed_names(sample_config) == ["@Trader", "@Keep"]
    assert modlist.disabled(sample_config) == {"@Trader"}
    lines = [entry["line"] for entry in job["log"]]
    assert any(line.startswith("  Turned off @Trader: still installed") for line in lines)


def test_a_switch_turns_on_the_new_maps_own_dep(app_and_config, sample_config, monkeypatch):
    from app.modmgr import modlist

    _switch_quietly(monkeypatch, sample_config)
    _no_merge(monkeypatch)
    write_test_mods(sample_config, ["@CF", "@Trader"], disabled=["@CF"])
    sample_config.update_triage = {"@CF": {"reason": "test"}}
    sample_config.save()

    job = _switch_job(app_and_config, {"map_id": "Deer Isle"})

    assert job["result"]["turned_on"] == ["@CF"]
    assert "@CF" not in modlist.disabled(sample_config)
    # Taken out of the update triage, saved through mutate_config.
    from app.shared.config import DayzConfig

    assert "@CF" not in DayzConfig.load().update_triage


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ({"map_id": "Deer Isle", "disable_mods": ["@CF"]},
         "@CF is Deer Isle's dependency and always goes with it."),
        ({"map_id": "Deer Isle", "disable_mods": ["@DeerIsle"]},
         "@DeerIsle is Deer Isle's own mod and always goes with it."),
    ],
)
def test_a_switch_refuses_to_leave_the_maps_own_mod_behind(app_and_config, sample_config, body, detail):
    write_test_mods(sample_config, ["@CF", "@Trader"])
    with TestClient(app_and_config) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        resp = client.post("/api/maps/switch", json=body)
    assert resp.status_code == 400
    assert resp.json()["detail"] == detail
    assert installed_names(sample_config) == ["@CF", "@Trader"]


def test_a_switch_refuses_an_unknown_mod(client: TestClient, sample_config):
    write_test_mods(sample_config, ["@Trader"])
    resp = client.post("/api/maps/switch", json={"map_id": "Livonia", "disable_mods": ["@Nope"]})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "@Nope is not in the mod list."
    resp = client.post("/api/maps/switch", json={"map_id": "Livonia", "leave_manual_files": ["nope"]})
    assert resp.status_code == 400


def test_a_switch_can_skip_the_mod_xml(app_and_config, sample_config, monkeypatch):
    from app.maps.map_ops import MOD_XML_SKIPPED

    _switch_quietly(monkeypatch, sample_config)
    merged = _no_merge(monkeypatch)
    write_test_mods(sample_config, ["@Trader"])

    job = _switch_job(app_and_config, {"map_id": "Livonia", "apply_mod_xml": False})

    assert merged == []
    assert job["result"]["mod_xml_applied"] is False
    assert MOD_XML_SKIPPED in [entry["line"] for entry in job["log"]]


def test_a_switch_leaves_a_manual_file_with_the_old_map(app_and_config, sample_config, monkeypatch):
    from app.xml import xml_manual
    from tests.conftest import manual_dir

    _switch_quietly(monkeypatch, sample_config)
    _no_merge(monkeypatch)
    _manual("heli-spawns")
    _manual("loot")

    job = _switch_job(app_and_config, {"map_id": "Livonia", "leave_manual_files": ["heli-spawns"]})

    assert job["result"]["left_manual_files"] == ["heli-spawns"]
    assert xml_manual.get_file("heli-spawns", manual_dir=manual_dir())["terrain"] == "chernarusplus"
    assert xml_manual.get_file("loot", manual_dir=manual_dir())["terrain"] == ""
    assert "  Manual XML file heli-spawns stays with Chernarus." in [e["line"] for e in job["log"]]


def test_a_workshop_switch_can_skip_the_recommended_cfg(app_and_config, sample_config, monkeypatch):
    from app.parsers.servercfg import read_cfg_value, read_mission_template

    _switch_quietly(monkeypatch, sample_config)
    _no_merge(monkeypatch)

    job = _switch_job(
        app_and_config,
        {"map_id": "Namalsk", "restore_previous": False, "apply_recommended": False},
    )

    cfg = sample_config.server_cfg_path
    assert read_mission_template(cfg) == "regular.namalsk"
    assert read_cfg_value(cfg, "lightingConfig") is None
    lines = [entry["line"] for entry in job["log"]]
    assert "  Kept your current config; only the mission changed." in lines


# ── Download only: a workshop map's mods and mission, not switched to ─


def _record_downloads(fetched: list[str]):
    async def _download(runner, config, mod_id, mod_name="", max_retries=5):
        fetched.append(mod_id)
        return True

    return _download


def _invented_mission(zip_url, mission_path, dest_dir, log=None, **kwargs):
    dest_dir.mkdir(parents=True)
    (dest_dir / "init.c").write_text("// invented\n")
    return 1


def test_catalog_says_which_maps_are_downloaded_and_where_the_editor_opens(
    client: TestClient, sample_config, unreleased_map
):
    from app.shared.seed_data import WORKSHOP_MAPS

    maps = {m["id"]: m for m in client.get("/api/maps").json()["maps"]}
    assert maps["Chernarus"]["downloaded"] is True  # comes with the server
    assert maps["Namalsk"]["downloaded"] is False
    # The Map editor link does not wait for an image, unlike `terrain`.
    assert maps["Namalsk"]["editor_terrain"] == "namalsk"
    assert maps["Chernarus"]["editor_terrain"] == "chernarusplus"
    upcoming = next(m for m in maps.values() if m["available"] is False)
    assert upcoming["editor_terrain"] is None
    assert upcoming["downloaded"] is False

    namalsk = WORKSHOP_MAPS["Namalsk"]
    _invented_mission(None, None, sample_config.server_dir_path / "mpmissions" / namalsk["mission"]["template"])
    for name in [*namalsk["mods"], *namalsk["deps"]]:
        (sample_config.server_dir_path / name).mkdir()
    maps = {m["id"]: m for m in client.get("/api/maps").json()["maps"]}
    assert maps["Namalsk"]["downloaded"] is True
    assert maps["Namalsk"]["active"] is False


def test_download_only_fetches_a_maps_mods_and_mission_and_switches_nothing(
    app_and_config, sample_config, monkeypatch
):
    from app.shared.seed_data import WORKSHOP_MAPS

    def _no_copy(*a, **k):
        raise AssertionError("Download only must leave the server dir's mods alone")

    fetched: list[str] = []
    monkeypatch.setattr("app.shared.steam.is_mod_downloaded", lambda config, mod_id: False)
    monkeypatch.setattr("app.shared.steam.download_mod_async", _record_downloads(fetched))
    monkeypatch.setattr("app.maps.map_ops.download_and_extract_mission", _invented_mission)
    monkeypatch.setattr("app.ops.fileops.copy_mod_to_server", _no_copy)
    # Nothing the running server loads changes, so it may be running.
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: True)
    mods_before = installed_names(sample_config)
    cfg_before = sample_config.server_cfg_path.read_text()

    with TestClient(app_and_config) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        resp = client.post("/api/maps/download", json={"map_id": "Namalsk"})
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])
        active = client.get("/api/maps").json()["active"]

    assert body["status"] == "success", body
    assert body["name"] == "Download Namalsk"
    namalsk = WORKSHOP_MAPS["Namalsk"]
    assert fetched == [*namalsk["mods"].values(), *namalsk["deps"].values()]
    assert (sample_config.server_dir_path / "mpmissions" / "regular.namalsk" / "init.c").is_file()
    assert active == "Chernarus"
    assert sample_config.server_cfg_path.read_text() == cfg_before
    assert installed_names(sample_config) == mods_before


def test_download_only_keeps_a_mission_already_installed(app_and_config, sample_config, monkeypatch):
    def _no_extract(*a, **k):
        raise AssertionError("an installed mission is kept as it is, like a switch keeps it")

    fetched: list[str] = []
    mission = sample_config.server_dir_path / "mpmissions" / "regular.namalsk"
    _invented_mission(None, None, mission)
    monkeypatch.setattr("app.shared.steam.is_mod_downloaded", lambda config, mod_id: True)
    monkeypatch.setattr("app.shared.steam.download_mod_async", _record_downloads(fetched))
    monkeypatch.setattr("app.maps.map_ops.download_and_extract_mission", _no_extract)

    with TestClient(app_and_config) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        resp = client.post("/api/maps/download", json={"map_id": "Namalsk"})
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "success", body
    assert fetched == []
    lines = [entry["line"] for entry in body["log"]]
    assert "  Mission folder 'regular.namalsk' already exists — using it as-is." in lines


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"map_id": "Chernarus"}, 400),  # comes with the server
        ({"map_id": "Atlantis"}, 404),
        ({}, 422),
    ],
)
def test_download_only_refuses_what_it_cannot_download(client: TestClient, body, status):
    assert client.post("/api/maps/download", json=body).status_code == status


def test_only_an_admin_can_download_a_map(app_and_config):
    """No MODERATOR_WRITES entry: the permissions default makes it admin-only."""
    app_and_config.state.ctx.users.add("mandy", "mandypass", "moderator")
    with TestClient(app_and_config) as client:
        client.post("/api/auth/login", json={"username": "mandy", "password": "mandypass"})
        assert client.post("/api/maps/download", json={"map_id": "Namalsk"}).status_code == 403


# ── Remove: the undo of Download only ────────────────────────────────


def _downloaded(config, map_name: str, *, cached: str | None = None) -> None:
    """Put an invented copy of *map_name*'s mission and mods on disk. The mod
    named *cached* goes into the Workshop cache, the others into the server dir."""
    from app.shared.seed_data import WORKSHOP_MAPS

    data = WORKSHOP_MAPS[map_name]
    _invented_mission(None, None, config.server_dir_path / "mpmissions" / data["mission"]["template"])
    for name, mod_id in {**data["mods"], **data["deps"]}.items():
        folder = config.workshop_dir / mod_id if name == cached else config.server_dir_path / name
        (folder / "keys").mkdir(parents=True, exist_ok=True)
        (folder / "keys" / f"{name.strip('@')}.bikey").write_text("key")


def _remove(app, map_id: str) -> dict:
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        resp = client.post("/api/maps/remove", json={"map_id": map_id})
        assert resp.status_code == 200, resp.text
        return _poll_job(client, resp.json()["job_id"])


def test_remove_deletes_the_mission_and_the_mods_only_that_map_uses(
    app_and_config, sample_config, tmp_path, monkeypatch
):
    sample_config.workshop_content_dir = str(tmp_path / "workshop")  # not the dev box's
    _downloaded(sample_config, "Banov", cached="@Central European Asset Pack")
    keys = sample_config.keys_dir
    keys.mkdir(exist_ok=True)
    (keys / "Banov.bikey").write_text("key")
    # Nothing the running server loads changes, so it may be running.
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: True)
    mods_before = installed_names(sample_config)
    cfg_before = sample_config.server_cfg_path.read_text()

    body = _remove(app_and_config, "Banov")

    assert body["status"] == "success", [e["line"] for e in body["log"]][-5:]
    assert body["name"] == "Remove Banov"
    assert not (sample_config.server_dir_path / "mpmissions" / "empty.banov").exists()
    assert not (sample_config.server_dir_path / "@Banov").exists()
    assert not (keys / "Banov.bikey").exists()
    assert not (sample_config.workshop_dir / "2330497955").exists()
    assert installed_names(sample_config) == mods_before
    assert sample_config.server_cfg_path.read_text() == cfg_before


def test_remove_keeps_a_mod_another_downloaded_map_or_the_mod_list_uses(
    app_and_config, sample_config, tmp_path
):
    sample_config.workshop_content_dir = str(tmp_path / "workshop")
    _downloaded(sample_config, "Namalsk")
    _downloaded(sample_config, "Namalsk Hardcore")  # the same two mods
    _downloaded(sample_config, "Deer Isle")
    write_test_mods(sample_config, [*installed_names(sample_config), "@CF"])

    body = _remove(app_and_config, "Namalsk")
    body_deer = _remove(app_and_config, "Deer Isle")

    assert body["status"] == "success", body
    assert not (sample_config.server_dir_path / "mpmissions" / "regular.namalsk").exists()
    assert (sample_config.server_dir_path / "mpmissions" / "hardcore.namalsk").is_dir()
    assert (sample_config.server_dir_path / "@Namalsk Island").is_dir()
    assert (sample_config.server_dir_path / "@Namalsk Survival").is_dir()
    lines = [entry["line"] for entry in body["log"]]
    assert "  Kept @Namalsk Island: another downloaded map uses it." in lines

    assert body_deer["status"] == "success", body_deer
    assert not (sample_config.server_dir_path / "@DeerIsle").exists()
    assert (sample_config.server_dir_path / "@CF").is_dir()
    lines = [entry["line"] for entry in body_deer["log"]]
    assert "  Kept @CF: it is in your mod list." in lines


def test_remove_refuses_the_map_in_use(client: TestClient, monkeypatch):
    monkeypatch.setattr("app.maps.map_ops.get_active_map_name", lambda config: "Namalsk")
    resp = client.post("/api/maps/remove", json={"map_id": "Namalsk"})
    assert resp.status_code == 409


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"map_id": "Chernarus"}, 400),  # comes with the server
        ({"map_id": "Atlantis"}, 404),
        ({}, 422),
    ],
)
def test_remove_refuses_what_it_cannot_remove(client: TestClient, body, status):
    assert client.post("/api/maps/remove", json=body).status_code == status


def test_only_an_admin_can_remove_a_map(app_and_config):
    """No MODERATOR_WRITES entry: the permissions default makes it admin-only."""
    app_and_config.state.ctx.users.add("mandy", "mandypass", "moderator")
    with TestClient(app_and_config) as client:
        client.post("/api/auth/login", json={"username": "mandy", "password": "mandypass"})
        assert client.post("/api/maps/remove", json={"map_id": "Namalsk"}).status_code == 403


# ── Download only: what the card says it will fetch ──────────────────


@pytest.fixture()
def steam_sizes(monkeypatch, sample_config, tmp_path):
    """Invented Workshop sizes, and a record of every time Steam is asked."""
    from app.web.routers import maps

    sample_config.workshop_content_dir = str(tmp_path / "workshop")  # not the dev box's

    asked: list[list[str]] = []
    answer: dict[str, int] = {}

    def _sizes(mod_ids, timeout=15):
        asked.append(list(mod_ids))
        return {mod_id: answer[mod_id] for mod_id in mod_ids if mod_id in answer}

    monkeypatch.setattr("app.modmgr.workshop_api.get_public_file_sizes", _sizes)
    monkeypatch.setattr(maps, "_sizes_cache", {})
    return asked, answer


def test_the_download_size_is_what_is_not_on_this_machine_yet(client: TestClient, sample_config, steam_sizes):
    from app.shared.seed_data import WORKSHOP_MAPS

    asked, answer = steam_sizes
    for data in WORKSHOP_MAPS.values():
        for mod_id in {**data["mods"], **data["deps"]}.values():
            answer[mod_id] = 1000
    banov_mod = WORKSHOP_MAPS["Banov"]["mods"]["@Banov"]
    answer[banov_mod] = 3000
    # Banov's dep is Banov Frost's too; once it is here, Banov Frost needs only its map mod.
    (sample_config.server_dir_path / "@Central European Asset Pack").mkdir()

    sizes = client.get("/api/maps/download-sizes").json()["sizes"]
    assert sizes["Banov"] == 3000
    assert sizes["Banov Frost"] == 1000
    assert sizes["Namalsk"] == 2000
    assert "Chernarus" not in sizes  # comes with the server

    client.get("/api/maps/download-sizes")
    assert len(asked) == 1  # kept, not asked again at every view


def test_no_size_is_shown_rather_than_a_wrong_one(client: TestClient, steam_sizes):
    from app.shared.seed_data import WORKSHOP_MAPS

    asked, answer = steam_sizes
    namalsk = WORKSHOP_MAPS["Namalsk"]
    answer[namalsk["mods"]["@Namalsk Island"]] = 1000  # its dep is not answered for
    sizes = client.get("/api/maps/download-sizes").json()["sizes"]
    assert "Namalsk" not in sizes


def test_steam_unreachable_means_no_sizes_and_no_retry_at_every_view(client: TestClient, steam_sizes):
    asked, _answer = steam_sizes
    assert client.get("/api/maps/download-sizes").json() == {"sizes": {}}
    assert client.get("/api/maps/download-sizes").json() == {"sizes": {}}
    assert len(asked) == 1


def test_an_id_steam_leaves_out_is_not_asked_for_at_every_view(client: TestClient, steam_sizes):
    """NEW-2: a removed or hidden Workshop item is missing from Steam's answer,
    and every page view used to ask for the whole list again."""
    from app.shared.seed_data import WORKSHOP_MAPS

    asked, answer = steam_sizes
    ids = {i for data in WORKSHOP_MAPS.values() for i in {**data["mods"], **data["deps"]}.values()}
    left_out = sorted(ids)[0]
    for mod_id in ids - {left_out}:
        answer[mod_id] = 1000
    for _ in range(5):
        client.get("/api/maps/download-sizes")
    assert len(asked) == 1


def test_only_ids_not_known_yet_are_asked_for(steam_sizes):
    from app.web.routers import maps

    asked, answer = steam_sizes
    answer.update({"111": 1000, "222": 2000, "333": 3000})
    assert maps._workshop_sizes({"111", "222"}) == {"111": 1000, "222": 2000}
    assert maps._workshop_sizes({"111", "222", "333"}) == {"111": 1000, "222": 2000, "333": 3000}
    assert asked == [["111", "222"], ["333"]]


def test_no_size_is_asked_for_again_once_its_time_is_up(monkeypatch, steam_sizes):
    from app.web.routers import maps

    asked, answer = steam_sizes
    answer["111"] = 1000
    monkeypatch.setattr(maps, "_SIZES_RETRY", 0)
    assert maps._workshop_sizes({"111", "222"}) == {"111": 1000}
    answer["222"] = 2000
    assert maps._workshop_sizes({"111", "222"}) == {"111": 1000, "222": 2000}
    assert asked == [["111", "222"], ["222"]]


def test_page_views_that_arrive_together_share_one_request(monkeypatch):
    """NEW-2: the request used to be sent once per concurrent view."""
    import threading

    from app.web.routers import maps

    calls: list[list[str]] = []
    second_call = threading.Event()

    def _slow(mod_ids, timeout=15):
        calls.append(list(mod_ids))
        if len(calls) > 1:
            second_call.set()
        else:
            second_call.wait(0.5)  # long enough for the other view to arrive
        return {mod_id: 1000 for mod_id in mod_ids}

    monkeypatch.setattr("app.modmgr.workshop_api.get_public_file_sizes", _slow)
    monkeypatch.setattr(maps, "_sizes_cache", {})
    results: list[dict] = []
    threads = [
        threading.Thread(target=lambda: results.append(maps._workshop_sizes({"111", "222"})))
        for _ in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert results == [{"111": 1000, "222": 1000}] * 2
    assert len(calls) == 1


def test_an_own_image_thumbnail_is_revalidated_and_follows_a_replacement(client: TestClient):
    # plans/68 NB-2: the admin's own image is a card's thumbnail for a map with
    # nothing else (plans/72), and it can be replaced under the same URL, so a
    # browser must ask again each time instead of keeping it for an hour.
    import os

    from app.maps import map_data

    own = map_data.own_images_dir()
    own.mkdir(parents=True, exist_ok=True)
    img = own / "custommap.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"a" * 32)

    first = client.get("/api/maps/thumbnail", params={"terrain": "custommap"})
    assert first.status_code == 200
    assert first.headers["cache-control"] == "private, no-cache"
    etag = first.headers["etag"]

    again = client.get("/api/maps/thumbnail", params={"terrain": "custommap"}, headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.content == b""

    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"b" * 40)
    st = img.stat()
    os.utime(img, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    replaced = client.get("/api/maps/thumbnail", params={"terrain": "custommap"}, headers={"If-None-Match": etag})
    assert replaced.status_code == 200
    assert replaced.content.endswith(b"b" * 40)
    assert replaced.headers["etag"] != etag


# ── POST /api/maps/mission-check (RELEASE item 22) ───────────────────


_SHA = "0123456789abcdef0123456789abcdef01234567"


def _recorded_namalsk(sample_config):
    import json

    from app.maps.map_ops import MISSION_SOURCE_FILE

    mission = sample_config.server_dir_path / "mpmissions" / "regular.namalsk"
    _invented_mission(None, None, mission)
    (mission / MISSION_SOURCE_FILE).write_text(json.dumps({
        "zip_url": "x", "mission_path": "Mission Files/regular.namalsk", "commit": _SHA,
    }))
    return mission


def test_the_catalog_names_an_installed_missions_upstream_commit(client: TestClient, sample_config):
    _recorded_namalsk(sample_config)
    maps = {m["id"]: m for m in client.get("/api/maps").json()["maps"]}
    assert maps["Namalsk"]["mission_commit"] == _SHA[:7]
    assert maps["Namalsk Hardcore"]["mission_commit"] is None
    assert maps["Chernarus"]["mission_commit"] is None


def test_the_mission_check_answers_from_the_catalogs_archive(client: TestClient, sample_config, monkeypatch):
    from app.maps.map_ops import MissionUpdate
    from app.shared.seed_data import WORKSHOP_MAPS

    mission = _recorded_namalsk(sample_config)
    asked = []

    def _status(mission_dir, zip_url):
        asked.append((mission_dir, zip_url))
        return MissionUpdate(True, "Upstream changed 2 mission file(s).", 2)

    monkeypatch.setattr("app.maps.map_ops.mission_update_status", _status)
    resp = client.post("/api/maps/mission-check", json={"map_id": "Namalsk"})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "map_id": "Namalsk", "newer": True, "detail": "Upstream changed 2 mission file(s).", "changed": 2,
    }
    assert asked == [(mission, WORKSHOP_MAPS["Namalsk"]["mission"]["zip_url"])]


def test_a_mission_check_github_refuses_is_a_502(client: TestClient, sample_config, monkeypatch):
    from app.maps.map_ops import MissionCheckError

    _recorded_namalsk(sample_config)

    def _refused(*a):
        raise MissionCheckError("GitHub refused the check.")

    monkeypatch.setattr("app.maps.map_ops.mission_update_status", _refused)
    resp = client.post("/api/maps/mission-check", json={"map_id": "Namalsk"})
    assert resp.status_code == 502
    assert resp.json()["detail"] == "GitHub refused the check."


@pytest.mark.parametrize(("map_id", "status"), [
    ("Namalsk", 400),  # not installed
    ("Chernarus", 404),  # comes with the server
    ("Atlantis", 404),
])
def test_the_mission_check_refuses_what_it_cannot_check(client: TestClient, monkeypatch, map_id, status):
    def _never(*a):
        raise AssertionError("nothing is asked")

    monkeypatch.setattr("app.maps.map_ops.mission_update_status", _never)
    assert client.post("/api/maps/mission-check", json={"map_id": map_id}).status_code == status


def test_catalog_says_what_the_map_editor_draws(client: TestClient):
    """Each released card carries `editor_features`; an unreleased one has no terrain, so none."""
    maps = {m["id"]: m for m in client.get("/api/maps").json()["maps"]}
    assert maps["Chernarus"]["editor_features"]["roads"]
    assert maps["Banov"]["editor_features"]["maker_image"]
    assert not maps["Namalsk"]["editor_features"]["terrain"]
    for m in maps.values():
        if m["available"] is False:
            assert m["editor_features"] is None, m["id"]


def test_an_encrypted_world_file_takes_the_roads_off_the_card(client: TestClient, sample_config):
    """Sakhal ships `worlds_sakhal.ebo`: its terrain comes from the navmesh, with no roads."""
    addons = sample_config.server_dir_path / "sakhal" / "addons"
    addons.mkdir(parents=True)
    (addons / "worlds_sakhal.ebo").write_bytes(b"")
    maps = {m["id"]: m for m in client.get("/api/maps").json()["maps"]}
    sakhal = maps["Sakhal"]["editor_features"]
    assert sakhal["terrain_from_navmesh"] and not sakhal["roads"]
    assert maps["Chernarus"]["editor_features"]["roads"]
