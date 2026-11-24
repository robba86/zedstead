"""GET|DELETE /api/update/triage, POST /api/update/triage/apply, and the Update
job's boot check (plans/73)."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.ops import post_update
from tests.test_web_jobs import _mock_pipeline_ops
from tests.conftest import write_test_mods

PASSWORD = "testpass123"


def _app(config):
    from app.web import server
    from app.web.auth import hash_password

    config.web_password_hash = hash_password(PASSWORD)
    return server.create_app(config)


def _login(client) -> None:
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200


def _wait(client, job_id) -> dict:
    deadline = time.monotonic() + 3.0
    while True:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] not in ("pending", "running"):
            return body
        assert time.monotonic() < deadline, body
        time.sleep(0.02)


@pytest.fixture()
def cfg(sample_config, tmp_path):
    sample_config.workshop_content_dir = str(
        tmp_path / "steam" / "steamapps" / "workshop" / "content" / "221100"
    )
    write_test_mods(sample_config, ['@CF', '@Broken'])
    return sample_config


def _failed(cfg, *mods):
    post_update.save_report(cfg, {
        "outcome": post_update.CRASHED, "started": 0, "build_after": "200", "detail": "crashed",
        "suspects": [{"mod": m, "lines": ["x"]} for m in mods], "set_aside": [], "recheck": None,
    })


def test_triage_with_no_report(cfg):
    with TestClient(_app(cfg)) as client:
        _login(client)
        body = client.get("/api/update/triage").json()
    assert body == {"report": None, "occasion": "", "offerable": [], "offer": None, "set_aside": []}


def test_triage_offers_the_suspects_of_a_failed_boot(cfg, monkeypatch):
    from app.modmgr import mod_conflict

    monkeypatch.setattr(mod_conflict, "get_mod_dependencies", lambda mods, server_dir, ids=None, **kw: {})
    _failed(cfg, "@Broken")
    with TestClient(_app(cfg)) as client:
        _login(client)
        body = client.get("/api/update/triage").json()
    assert body["report"]["outcome"] == "crashed"
    assert body["offerable"] == ["@Broken"]
    assert body["offer"]["mods"] == ["@Broken"]
    assert "1,334" in body["offer"]["warning"]


def test_triage_narrows_the_offer_to_the_ticked_mods(cfg, monkeypatch):
    from app.modmgr import mod_conflict

    monkeypatch.setattr(mod_conflict, "get_mod_dependencies", lambda mods, server_dir, ids=None, **kw: {})
    _failed(cfg, "@Broken", "@CF")
    with TestClient(_app(cfg)) as client:
        _login(client)
        body = client.get("/api/update/triage", params={"mods": ["@CF", "@NotOffered"]}).json()
    assert body["offerable"] == ["@Broken", "@CF"]
    assert body["offer"]["mods"] == ["@CF"]


def test_apply_without_a_failed_boot_is_refused(cfg):
    with TestClient(_app(cfg)) as client:
        _login(client)
        resp = client.post("/api/update/triage/apply", json={"mods": ["@Broken"]})
    assert resp.status_code == 409


def test_apply_accepts_only_the_offered_mods(cfg):
    _failed(cfg, "@Broken")
    with TestClient(_app(cfg)) as client:
        _login(client)
        resp = client.post("/api/update/triage/apply", json={"mods": ["@CF"]})
    assert resp.status_code == 422
    assert "@CF" in resp.json()["detail"]


def test_apply_runs_set_aside_as_a_job(cfg, monkeypatch):
    _failed(cfg, "@Broken")
    seen = {}

    async def _set_aside(config, mods, *, mutate, regenerate, emit=None, backup_first=True):
        seen.update(mods=mods, backup_first=backup_first)
        await mutate(lambda c: c.update_triage.update({"@Broken": {"build": "200"}}))
        return {"outcome": "crashed", "set_aside": mods, "recheck": {"outcome": "joinable"}}

    monkeypatch.setattr(post_update, "set_aside", _set_aside)
    with TestClient(_app(cfg)) as client:
        _login(client)
        resp = client.post("/api/update/triage/apply", json={"mods": ["@Broken"], "backup_first": False})
        assert resp.status_code == 200
        job = _wait(client, resp.json()["job_id"])
    assert job["status"] == "success", job
    assert seen == {"mods": ["@Broken"], "backup_first": False}
    assert job["result"]["report"]["recheck"]["outcome"] == "joinable"


def test_dismiss_forgets_the_report(cfg):
    _failed(cfg, "@Broken")
    with TestClient(_app(cfg)) as client:
        _login(client)
        assert client.delete("/api/update/triage").json()["cleared"] is True
    assert post_update.load_report(cfg) is None


def test_a_viewer_cannot_set_mods_aside():
    from app.web.permissions import ADMIN, required_role

    assert required_role("POST", "/api/update/triage/apply") == ADMIN
    assert required_role("DELETE", "/api/update/triage") == ADMIN


def test_the_update_job_checks_the_boot_when_asked(cfg, monkeypatch):
    _mock_pipeline_ops(monkeypatch)
    calls = []

    async def _start_and_check(config, before, *, emit=None, timeout=None):
        calls.append(before)
        return {"outcome": "joinable"}

    monkeypatch.setattr(post_update, "start_and_check", _start_and_check)
    with TestClient(_app(cfg)) as client:
        _login(client)
        resp = client.post("/api/update/run", json={"start_after": True})
        job = _wait(client, resp.json()["job_id"])
        assert job["status"] == "success", job
        assert job["result"]["boot"] == {"outcome": "joinable"}
        assert len(calls) == 1

        resp = client.post("/api/update/run", json={})
        job = _wait(client, resp.json()["job_id"])
    assert job["result"]["boot"] is None
    assert len(calls) == 1


def test_the_update_job_says_when_dayz_itself_updated(cfg, monkeypatch):
    _mock_pipeline_ops(monkeypatch)
    from app.ops import mission_stock

    monkeypatch.setattr(post_update, "snapshot", lambda config: post_update.UpdateSnapshot(1.0, "100"))
    monkeypatch.setattr(mission_stock, "installed_buildid", lambda config: "200")
    with TestClient(_app(cfg)) as client:
        _login(client)
        resp = client.post("/api/update/run", json={})
        job = _wait(client, resp.json()["job_id"])
    assert job["result"]["game"] == {"before": "100", "after": "200"}
