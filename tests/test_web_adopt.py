"""/api/adopt (app/web/routers/adopt.py, plans/78 step 7)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.modmgr import modlist
from tests.test_adopt import _findings_server, doctor_rows, units  # noqa: F401 (fixtures)

PASSWORD = "testpass123"


@pytest.fixture()
def client(sample_config, units):  # noqa: F811
    from app.web import server
    from app.web.auth import hash_password

    _findings_server(sample_config, units)
    sample_config.web_password_hash = hash_password(PASSWORD)
    sample_config.save()
    tclient = TestClient(server.create_app(sample_config))
    assert tclient.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return tclient


def test_get_reports_and_writes_nothing(client, sample_config, doctor_rows):  # noqa: F811
    body = client.get("/api/adopt").json()

    assert body["adopted"] is False
    assert {m["name"] for m in body["report"]["mods"]} >= {"@CF", "@MyRadZone"}
    labels = {c["label"]: c for c in body["findings"]["checks"]}
    assert labels["Mod keys"]["fix"] == "copy_keys"
    assert body["findings"]["skipped"]
    assert modlist.installed(sample_config) == []


def test_apply_writes_the_list_once(client, sample_config):
    r = client.post("/api/adopt/apply")
    assert r.status_code == 200, r.text
    assert "@CF" in modlist.installed(sample_config)
    assert sample_config.reloaded().profiles_path == "custom_profiles"

    again = client.post("/api/adopt/apply")
    assert again.status_code == 409


def test_the_path_lock_refuses_apply_before_anything_is_written(client, sample_config):
    sample_config.web_lock_paths = True
    sample_config.save()

    r = client.post("/api/adopt/apply")

    assert r.status_code == 403
    assert modlist.installed(sample_config) == []


def test_fixes_run_on_their_own_routes(client, sample_config):
    server = sample_config.server_dir_path
    assert client.post("/api/adopt/copy-keys").status_code == 200
    assert (server / "keys" / "Dabs.bikey").exists()

    r = client.post("/api/adopt/pasted", json={"mod": "@MyRadZone", "keep": "mod"})
    assert r.status_code == 200, r.text
    types = (server / "mpmissions" / "dayzOffline.chernarusplus" / "db" / "types.xml").read_text()
    assert "rz_geiger" not in types and "AKM" in types

    assert client.post("/api/adopt/pasted", json={"mod": "@CF", "keep": "mine"}).status_code == 409
    assert modlist.installed(sample_config) == [], "a fix is not an Apply"
