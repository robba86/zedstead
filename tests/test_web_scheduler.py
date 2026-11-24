"""Tests for /api/schedule* (app/web/routers/scheduler.py)."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

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


def test_schedule_requires_session(sample_config):
    from app.web import server

    app = server.create_app(sample_config)
    resp = TestClient(app).get("/api/schedule")
    assert resp.status_code == 401


def test_schedule_empty_with_presets(client):
    resp = client.get("/api/schedule")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tasks"] == []
    assert "restart_4h" in body["presets"]


def test_schedule_names_the_server_time_zone(client, monkeypatch):
    # Cron times are the server's wall clock; a browser elsewhere needs the zone.
    from datetime import datetime, timedelta, timezone

    from app.server import scheduler

    cest = timezone(timedelta(hours=2), "CEST")
    monkeypatch.setattr(scheduler, "cron_now", lambda: datetime(2026, 10, 13, 4, 0, tzinfo=cest))
    assert client.get("/api/schedule").json()["time_zone"] == "CEST (UTC+2)"


def test_create_update_delete_task(client):
    resp = client.post(
        "/api/schedule", json={"id": "restart-1", "task_type": "restart", "cron": "0 */4 * * *"}
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == "restart-1"

    resp = client.get("/api/schedule")
    assert len(resp.json()["tasks"]) == 1

    resp = client.put("/api/schedule/restart-1", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    resp = client.delete("/api/schedule/restart-1")
    assert resp.status_code == 200
    assert client.get("/api/schedule").json()["tasks"] == []


def test_create_custom_cron_task(client):
    resp = client.post(
        "/api/schedule", json={"id": "night-wipe", "task_type": "wipe", "cron": "30 5 * * 0"}
    )
    assert resp.status_code == 200

    tasks = client.get("/api/schedule").json()["tasks"]
    assert len(tasks) == 1
    task = tasks[0]
    assert task["id"] == "night-wipe"
    assert task["task_type"] == "wipe"
    assert task["cron"] == "30 5 * * 0"
    assert task["description"]
    assert task["next_run_display"]


def test_edit_task_type_and_cron(client):
    client.post("/api/schedule", json={"id": "job", "task_type": "restart", "cron": "0 */4 * * *"})

    resp = client.put("/api/schedule/job", json={"task_type": "backup", "cron": "0 */6 * * *"})
    assert resp.status_code == 200
    assert resp.json()["task_type"] == "backup"
    assert resp.json()["cron"] == "0 */6 * * *"

    task = client.get("/api/schedule").json()["tasks"][0]
    assert task["task_type"] == "backup"
    assert task["cron"] == "0 */6 * * *"


def test_edit_invalid_cron_422(client):
    client.post("/api/schedule", json={"id": "job", "task_type": "restart", "cron": "0 */4 * * *"})
    resp = client.put("/api/schedule/job", json={"cron": "not-a-cron"})
    assert resp.status_code == 422


def test_scheduler_write_goes_through_mutate_config(client, sample_config):
    """A schedule write must re-read config from disk under the lock so a
    concurrent CLI edit to an unrelated field isn't clobbered."""
    from app.shared.config import DayzConfig

    # Persist the app's starting config, then simulate a concurrent external
    # editor changing an unrelated field on disk — on a SEPARATE object, so
    # state.config's in-memory copy stays stale (server_name default).
    sample_config.save()
    external = DayzConfig.load()
    external.server_name = "EXTERNAL-EDIT"
    external.save()

    resp = client.post(
        "/api/schedule", json={"id": "night", "task_type": "wipe", "cron": "30 5 * * 0"}
    )
    assert resp.status_code == 200

    reloaded = DayzConfig.load()
    assert any(t["id"] == "night" for t in reloaded.scheduled_tasks)
    assert reloaded.server_name == "EXTERNAL-EDIT"


def test_create_task_duplicate_id_422(client):
    body = {"id": "restart-1", "task_type": "restart", "cron": "0 */4 * * *"}
    assert client.post("/api/schedule", json=body).status_code == 200
    resp = client.post("/api/schedule", json=body)
    assert resp.status_code == 422


def test_create_task_invalid_cron_422(client):
    resp = client.post(
        "/api/schedule", json={"id": "bad", "task_type": "restart", "cron": "not-a-cron"}
    )
    assert resp.status_code == 422


def test_update_unknown_task_404(client, monkeypatch):
    """Answered before config.json is saved, as the delete route does."""
    from app.shared.config import DayzConfig

    monkeypatch.setattr(DayzConfig, "save", lambda self: pytest.fail("saved config.json"))
    resp = client.put("/api/schedule/does-not-exist", json={"enabled": False})
    assert resp.status_code == 404


def test_delete_unknown_task_404(client):
    resp = client.delete("/api/schedule/does-not-exist")
    assert resp.status_code == 404


def test_validate_cron(client):
    resp = client.post("/api/schedule/validate-cron", json={"expr": "0 */4 * * *"})
    assert resp.status_code == 200
    assert resp.json()["valid"] is True

    resp = client.post("/api/schedule/validate-cron", json={"expr": "nope"})
    assert resp.status_code == 200
    assert resp.json()["valid"] is False


@pytest.mark.parametrize("expr, reason", [
    ("0 4 1-7 * 1", "cron runs this when either the day or the weekday matches"),
    ("0 4 15W * *", "systemd timers have no nearest-weekday 'W'"),
    ("0 4 * *", "a schedule has five fields"),
    ("0 25 * * *", "a field is not cron syntax or is out of range"),
])
def test_validate_cron_says_why_a_save_would_be_refused(client, expr, reason):
    """plans/68 B24: the page showed only "Invalid cron expression" while the
    save's 422 knew why. The live check carries the same words."""
    body = client.post("/api/schedule/validate-cron", json={"expr": expr}).json()
    assert body["valid"] is False
    assert reason in body["reason"]

    resp = client.post("/api/schedule", json={"id": "t1", "task_type": "restart", "cron": expr})
    assert resp.status_code == 422
    assert resp.json()["detail"] == f"Invalid cron expression: {expr} ({body['reason']})"

    ok = client.post("/api/schedule/validate-cron", json={"expr": "0 4 * * *"}).json()
    assert ok["valid"] is True and ok["reason"] is None


def test_schedule_info_shape(client):
    resp = client.get("/api/schedule/info")
    assert resp.status_code == 200
    assert {"has_systemd", "sudo_available", "scope", "privileged_ops_ok"} <= resp.json().keys()


def test_install_timers_409_without_sudo(client, monkeypatch):
    monkeypatch.setattr("app.shared.systemd_scope.sudo_available_noninteractive", lambda: False)
    resp = client.post("/api/schedule/install-timers")
    assert resp.status_code == 409


def test_install_timers_job(app_and_config, monkeypatch):
    monkeypatch.setattr("app.shared.systemd_scope.sudo_available_noninteractive", lambda: True)

    async def _fake_install(config, log=None):
        # A task skipped at install is named in the job's own log (INJ-9).
        log("Skipped scheduled task 'bad': Unknown task type: nope")
        return ["zsm-restart-1"]

    monkeypatch.setattr("app.server.scheduler.install_timers_async", _fake_install)

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/schedule/install-timers")
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])
        assert body["status"] == "success", body
        assert body["result"]["installed"] == ["zsm-restart-1"]
        lines = [entry["line"] for entry in body["log"]]
        assert "Skipped scheduled task 'bad': Unknown task type: nope" in lines


def test_remove_timers_job(app_and_config, monkeypatch):
    monkeypatch.setattr("app.shared.systemd_scope.sudo_available_noninteractive", lambda: True)

    async def _fake_remove(config):
        return ["zsm-restart-1"]

    monkeypatch.setattr("app.server.scheduler.remove_timers_async", _fake_remove)

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/schedule/remove-timers")
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])
        assert body["status"] == "success", body


# ── Payload task types (TODO C4) ─────────────────────────────────────


def test_create_announce_task_requires_a_message(client):
    resp = client.post(
        "/api/schedule",
        json={"id": "say-1", "task_type": "announce", "cron": "0 20 * * *"},
    )
    assert resp.status_code == 422
    assert "message" in resp.json()["detail"]


def test_create_announce_task_with_a_message(client):
    resp = client.post(
        "/api/schedule",
        json={
            "id": "say-1",
            "task_type": "announce",
            "cron": "0 20 * * *",
            "options": {"message": "Restart in 15 minutes"},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["options"] == {"message": "Restart in 15 minutes"}

    listed = client.get("/api/schedule").json()["tasks"]
    assert listed[0]["options"]["message"] == "Restart in 15 minutes"


def test_a_multiline_rcon_command_is_refused(client):
    """One command must not become two."""
    resp = client.post(
        "/api/schedule",
        json={
            "id": "lock-1",
            "task_type": "rcon",
            "cron": "0 3 * * *",
            "options": {"command": "#lock\n#shutdown"},
        },
    )
    assert resp.status_code == 422
    assert "single line" in resp.json()["detail"]


def test_editing_a_task_can_clear_its_payload_only_by_failing(client):
    client.post(
        "/api/schedule",
        json={
            "id": "say-1",
            "task_type": "announce",
            "cron": "0 20 * * *",
            "options": {"message": "hello"},
        },
    )
    resp = client.put("/api/schedule/say-1", json={"options": {}})
    assert resp.status_code == 422
    # ...and the stored task is untouched.
    assert client.get("/api/schedule").json()["tasks"][0]["options"] == {"message": "hello"}


def test_the_schedule_says_which_timers_are_installed(client, sample_config, tmp_path, monkeypatch):
    """F42: "Next run" showed for a task whose timer was never installed. The
    state is read from the unit files; a page load spawns nothing."""
    import subprocess

    from app.server import scheduler

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(scheduler, "has_systemd", lambda: True)
    sample_config.service_scope = "user"
    unit_dir = tmp_path / "xdg" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    (unit_dir / "dayz-task-gone.timer").write_text("left behind")

    def _no_subprocess(*a, **k):
        raise AssertionError("GET /api/schedule spawned a process")

    monkeypatch.setattr(subprocess, "run", _no_subprocess)
    resp = client.post("/api/schedule", json={"id": "nightly", "task_type": "restart", "cron": "0 4 * * *"})
    assert resp.status_code == 200, resp.text

    body = client.get("/api/schedule").json()

    assert body["install_state"] == {"nightly": "missing"}
    assert body["orphans"] == ["dayz-task-gone"]
