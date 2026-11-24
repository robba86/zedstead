"""Tests for /api/status, /api/doctor, /api/service/* (app/web/routers/status.py
and service.py)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

PASSWORD = "testpass123"


@pytest.fixture()
def client(sample_config, monkeypatch):
    from app.server import service_control
    from app.web import server
    from app.web.auth import hash_password

    # sample_config holds no server binary, and the unit check would ask this
    # machine's systemd: a test of the refusal sets its own.
    monkeypatch.setattr(service_control, "start_refusal", lambda config: None)
    sample_config.web_password_hash = hash_password(PASSWORD)
    app = server.create_app(sample_config)
    tclient = TestClient(app)
    tclient.post("/api/auth/login", json={"password": PASSWORD})
    return tclient


def test_status_requires_session(sample_config):
    from app.web import server

    app = server.create_app(sample_config)
    resp = TestClient(app).get("/api/status")
    assert resp.status_code == 401


def test_status_shape_when_server_not_running(client, monkeypatch):
    from app.server import service_control

    monkeypatch.setattr(service_control, "is_server_running", lambda config=None: False)

    def _no_steamcmd(config, timeout=90, **k):
        raise AssertionError("GET /api/status ran SteamCMD")

    monkeypatch.setattr("app.shared.steam.check_steam_login", _no_steamcmd)

    resp = client.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    for key in (
        "running", "version", "uptime_seconds", "fps", "memory", "cpu", "map",
        "mod_count", "hostname", "max_players", "template",
        "next_restart_minutes", "steam_ok",
    ):
        assert key in body
    assert body["running"] is False
    # No username and no SteamCMD: known to be logged out without measuring.
    assert body["steam_ok"] is False


def test_status_serves_the_cached_steam_answer_null_until_measured(client, sample_config, monkeypatch):
    """plans/51 D: the cached answer, never a probe."""
    from app.server import service_control

    monkeypatch.setattr(service_control, "is_server_running", lambda config=None: False)
    monkeypatch.setattr(
        "app.shared.steam.check_steam_login",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("GET /api/status ran SteamCMD")),
    )
    state = client.app.state.ctx
    state.config.steam_username = "bob"
    state.config.steamcmd_path.parent.mkdir(parents=True, exist_ok=True)
    state.config.steamcmd_path.write_text("#!/bin/sh\nexit 0\n")

    assert client.get("/api/status").json()["steam_ok"] is None
    state.note_steam_ok(False)
    assert client.get("/api/status").json()["steam_ok"] is False


def test_collect_status_has_all_fields(sample_config):
    from app.web.status_poller import collect_status

    data = collect_status(sample_config)
    for k in ("running", "version", "uptime_seconds", "fps", "memory", "cpu", "map",
              "mod_count", "hostname", "max_players", "template", "next_restart_minutes"):
        assert k in data


def test_next_restart_is_the_scheduled_restart(sample_config, monkeypatch, timers_unknown):
    """The tile read only messages.xml's shutdown deadline and showed "—"
    beside an enabled Server Restart task. It asks
    what the restart warnings count down to now."""
    from datetime import datetime, timezone

    import app.server.scheduler as sched
    from app.web.notifier import WebNotifier
    from app.web.status_poller import collect_status

    sample_config.scheduled_tasks = [
        {"id": "restart_custom_0-4", "task_type": "restart", "cron": "0 */4 * * *", "enabled": True}
    ]
    monkeypatch.setattr(
        sched, "cron_now", lambda: datetime(2026, 10, 16, 20, 52, tzinfo=timezone.utc)
    )
    monkeypatch.setattr("app.server.server_status.get_server_uptime", lambda config=None: None)
    monkeypatch.setattr("app.server.server_status.get_shutdown_deadline", lambda config: None)

    minutes = collect_status(sample_config)["next_restart_minutes"]
    assert minutes == 188  # 3h 8m to 00:00
    hub = type("Hub", (), {"broadcast": lambda self, msg: None})()
    assert WebNotifier(sample_config, hub)._get_remaining_and_deadline()[0] == minutes


def test_next_restart_falls_back_to_the_messages_countdown_only_while_running(
    sample_config, monkeypatch
):
    from datetime import timedelta

    from app.web.status_poller import collect_status

    sample_config.scheduled_tasks = []
    monkeypatch.setattr("app.server.server_status.get_shutdown_deadline", lambda config: 60)
    monkeypatch.setattr(
        "app.server.server_status.get_server_uptime", lambda config=None: timedelta(minutes=45)
    )
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: True)
    assert collect_status(sample_config)["next_restart_minutes"] == 15

    monkeypatch.setattr("app.server.server_status.get_server_uptime", lambda config=None: None)
    assert collect_status(sample_config)["next_restart_minutes"] is None


def test_status_template_is_the_mission_dayz_loads(sample_config):
    """`template` sits in class Missions { class DayZ }, where Bohemia's file
    has it. Read as a root key it was None on every stock cfg, and a root
    key that only names it is not the mission."""
    from app.web.status_poller import collect_status

    sample_config.server_cfg_path.write_text(
        'hostname = "Best template server";\n'
        "maxPlayers = 60;\n"
        'template = "dayzOffline.enoch";\n'
        'class Missions\n{\n\tclass DayZ\n\t{\n\t\ttemplate = "dayzOffline.sakhal";\n\t};\n};\n'
    )

    data = collect_status(sample_config)
    assert data["template"] == "dayzOffline.sakhal"
    assert data["hostname"] == "Best template server"
    assert data["max_players"] == "60"


async def test_status_poller_tick_broadcasts(sample_config, monkeypatch):
    from app.web import status_poller as sp

    class _Hub:
        def __init__(self):
            self.sent = []

        async def broadcast(self, msg, channel=None):
            self.sent.append((msg, channel))

    monkeypatch.setattr(sp, "collect_status", lambda cfg: {"running": True})
    hub = _Hub()
    poller = sp.StatusPoller(sample_config, hub)
    await poller._tick()

    assert len(hub.sent) == 1
    msg, channel = hub.sent[0]
    assert channel == "status" and msg["type"] == "status"
    assert msg["data"] == {"running": True} and "ts" in msg


def test_status_poller_failure_warns_not_debug(sample_config, caplog):
    """A failing tick must be visible at default log level.

    A permanently failing poll leaves the dashboard on stale/empty status with
    no other symptom, so hiding the traceback at debug level removes the only
    clue that explains it.
    """
    import logging

    from app.web import status_poller as sp

    poller = sp.StatusPoller(sample_config, hub=None)
    with caplog.at_level(logging.WARNING, logger=sp.log.name):
        poller._note_failure()

    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert "status poll tick failed" in caplog.text


def test_status_poller_failure_does_not_flood(sample_config, caplog):
    """First failure warns; the rest stay quiet until the next minute."""
    import logging

    from app.web import status_poller as sp

    poller = sp.StatusPoller(sample_config, hub=None)
    with caplog.at_level(logging.WARNING, logger=sp.log.name):
        for _ in range(6):  # 6 ticks * 5s = 30s — under the 1-minute repeat
            poller._note_failure()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, f"expected 1 warning, got {len(warnings)}"


def test_status_poller_failure_counter_resets_on_success(sample_config):
    from app.web import status_poller as sp

    poller = sp.StatusPoller(sample_config, hub=None)
    poller._note_failure()
    poller._note_failure()
    assert poller._failures == 2
    poller._failures = 0  # what _run does after a good tick
    poller._note_failure()
    assert poller._failures == 1


def test_status_channel_wired(sample_config):
    from app.web import server

    app = server.create_app(sample_config)
    ctx = app.state.ctx
    key = ("status", sample_config.instance_id)
    assert ctx.hub._on_first_subscriber[key] == ctx.status_poller.start
    assert ctx.hub._on_last_unsubscriber[key] == ctx.status_poller.stop


def test_doctor_requires_session(sample_config):
    from app.web import server

    app = server.create_app(sample_config)
    resp = TestClient(app).get("/api/doctor")
    assert resp.status_code == 401


def test_doctor_returns_structured_shape(client):
    resp = client.get("/api/doctor")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, dict)
    for key in ("checks", "summary", "conflicts", "server_info", "disc_usage",
                "log_analysis", "service_file"):
        assert key in body
    for check in body["checks"]:
        assert {"label", "severity", "ok", "detail", "remediation"} <= check.keys()
        assert check["severity"] in {"ok", "warning", "error"}


def test_doctor_reports_busy_while_a_job_runs(client):
    """The page banners off this and stops trusting the findings — a job that
    is mid-uninstall makes checks fail for reasons that aren't real."""
    from app.web.jobs import Job

    assert client.get("/api/doctor").json()["busy"] is False

    ctx = client.app.state.ctx
    ctx.jobs._active_exclusive = Job(id="x", name="n", kind="k", hub=ctx.hub)
    try:
        assert client.get("/api/doctor").json()["busy"] is True
    finally:
        ctx.jobs._active_exclusive = None


def test_doctor_summary_counts_match(client):
    body = client.get("/api/doctor").json()
    summary = body["summary"]
    assert summary["total"] == len(body["checks"])
    assert summary["ok"] + summary["warning"] + summary["error"] == summary["total"]


def test_doctor_conflicts_shape(client):
    conflicts = client.get("/api/doctor").json()["conflicts"]
    assert isinstance(conflicts["issues"], list)
    assert isinstance(conflicts["error_count"], int)
    assert isinstance(conflicts["warning_count"], int)
    assert isinstance(conflicts["dependencies"]["edges"], list)
    assert isinstance(conflicts["dependencies"]["pbos_scanned"], int)


# ---------------------------------------------------------------------------
# /api/service/*
# ---------------------------------------------------------------------------


def test_service_info_requires_session(sample_config):
    from app.web import server

    app = server.create_app(sample_config)
    resp = TestClient(app).get("/api/service/info")
    assert resp.status_code == 401


def test_service_info_shape(client):
    resp = client.get("/api/service/info")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {
        "has_systemd",
        "is_windows",
        "scope",
        "linger_enabled",
        "privileged_ops_ok",
        "privileged_ops_reason",
        "sudo_available",
    }
    assert body["scope"] in ("system", "user")


def test_service_start_success_broadcasts_and_notifies(client, monkeypatch):
    from app.server import service_control

    monkeypatch.setattr(
        service_control, "start_server", lambda config, anyway=False: service_control.ServiceResult(True)
    )
    monkeypatch.setattr("app.ops.changeset_ops.take_snapshot", lambda config: None)
    notified = []
    monkeypatch.setattr(
        "app.monitoring.discord_hooks.notify_start", lambda config: notified.append(config)
    )

    resp = client.post("/api/service/start")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert len(notified) == 1


def _capture_broadcasts(client, monkeypatch):
    ctx = client.app.state.ctx
    sent = []

    async def _rec(msg, channel=None):
        sent.append(msg)

    monkeypatch.setattr(ctx.hub, "broadcast", _rec)
    return sent


def test_service_start_broadcast_tags_action(client, monkeypatch):
    from app.server import service_control

    monkeypatch.setattr(
        service_control, "start_server", lambda config, anyway=False: service_control.ServiceResult(True)
    )
    monkeypatch.setattr("app.ops.changeset_ops.take_snapshot", lambda config: None)
    monkeypatch.setattr("app.monitoring.discord_hooks.notify_start", lambda config: None)
    sent = _capture_broadcasts(client, monkeypatch)

    resp = client.post("/api/service/start")
    assert resp.status_code == 200
    assert {"type": "service_changed", "action": "start"} in sent


def test_service_restart_broadcast_tags_action(client, monkeypatch):
    from app.server import service_control

    monkeypatch.setattr(
        service_control, "restart_server", lambda config: service_control.ServiceResult(True)
    )
    sent = _capture_broadcasts(client, monkeypatch)

    resp = client.post("/api/service/restart")
    assert resp.status_code == 200
    assert {"type": "service_changed", "action": "restart"} in sent


def test_service_stop_broadcast_has_no_action(client, monkeypatch):
    from app.server import service_control

    monkeypatch.setattr(
        service_control, "stop_server", lambda config: service_control.ServiceResult(True)
    )
    monkeypatch.setattr("app.monitoring.discord_hooks.notify_stop", lambda config: None)
    sent = _capture_broadcasts(client, monkeypatch)

    resp = client.post("/api/service/stop")
    assert resp.status_code == 200
    changed = [m for m in sent if m.get("type") == "service_changed"]
    assert changed and all("action" not in m for m in changed)


def test_service_start_failure_is_409(client, monkeypatch):
    from app.server import service_control

    monkeypatch.setattr(
        service_control,
        "start_server",
        lambda config, anyway=False: service_control.ServiceResult(False, "No start script found"),
    )
    monkeypatch.setattr("app.ops.changeset_ops.take_snapshot", lambda config: None)

    resp = client.post("/api/service/start")
    assert resp.status_code == 409
    assert "No start script found" in resp.json()["detail"]


def test_service_start_notify_failure_does_not_fail_request(client, monkeypatch):
    """discord notify is best-effort — a raise there must not surface as an error."""
    from app.server import service_control

    monkeypatch.setattr(
        service_control, "start_server", lambda config, anyway=False: service_control.ServiceResult(True)
    )
    monkeypatch.setattr("app.ops.changeset_ops.take_snapshot", lambda config: None)

    def boom(config):
        raise RuntimeError("discord is down")

    monkeypatch.setattr("app.monitoring.discord_hooks.notify_start", boom)

    resp = client.post("/api/service/start")
    assert resp.status_code == 200


def test_service_stop_already_stopped_is_ok(client, monkeypatch):
    from app.server import service_control

    monkeypatch.setattr(
        service_control,
        "stop_server",
        lambda config: service_control.ServiceResult(True, "Service already stopped."),
    )
    monkeypatch.setattr("app.monitoring.discord_hooks.notify_stop", lambda config: None)

    resp = client.post("/api/service/stop")
    assert resp.status_code == 200


def test_service_restart_failure_is_409(client, monkeypatch):
    from app.server import service_control

    monkeypatch.setattr(
        service_control,
        "restart_server",
        lambda config: service_control.ServiceResult(False, "systemctl not found."),
    )

    resp = client.post("/api/service/restart")
    assert resp.status_code == 409


def test_version_check_endpoint(client, monkeypatch):
    monkeypatch.setattr(
        "app.shared.version_check.check_for_update", lambda current, config=None: "99.0.0"
    )
    resp = client.get("/api/version-check")
    assert resp.status_code == 200
    body = resp.json()
    assert body["latest"] == "99.0.0"
    assert body["current"]
    assert body["enabled"] is True


def test_version_check_up_to_date(client, monkeypatch):
    monkeypatch.setattr(
        "app.shared.version_check.check_for_update", lambda current, config=None: None
    )
    assert client.get("/api/version-check").json()["latest"] is None


def test_version_check_turned_off_makes_no_request(client, monkeypatch):
    """The panel's version_check_enabled off: GitHub is never asked (NET-13)."""
    asked: list[str] = []

    def _get(url, **kwargs):
        asked.append(url)
        raise AssertionError("the version check asked GitHub while turned off")

    monkeypatch.setattr("requests.get", _get)
    client.app.state.panel.startup.config.version_check_enabled = False
    body = client.get("/api/version-check").json()
    assert body["latest"] is None
    assert body["enabled"] is False
    assert asked == []


def test_a_start_with_a_runtime_missing_is_refused_unless_anyway(client, monkeypatch):
    """The Start dialog asks first (GET start-check); the route refuses a plain
    start and lets "Start anyway" through, since the DLL check can be wrong."""
    from app.server import service_control, windows_runtime

    problem = "DayZServer_x64.exe cannot start on this computer: X.dll is missing."
    monkeypatch.setattr(windows_runtime, "runtime_problem", lambda config: problem)
    monkeypatch.setattr("app.ops.changeset_ops.take_snapshot", lambda config: None)
    monkeypatch.setattr("app.monitoring.discord_hooks.notify_start", lambda config: None)
    launched = []
    monkeypatch.setattr(
        service_control, "_windows_launch_start_bat",
        lambda config: launched.append(1) or service_control.ServiceResult(True),
    )
    monkeypatch.setattr(service_control, "IS_WINDOWS", True)

    assert client.get("/api/service/start-check").json() == {"refusal": None, "problem": problem}
    refused = client.post("/api/service/start")
    assert refused.status_code == 409
    assert refused.json()["detail"] == problem
    assert launched == []

    assert client.post("/api/service/start", json={"anyway": True}).status_code == 200
    assert launched == [1]


def test_a_start_that_cannot_work_answers_its_sentence_even_anyway(client, monkeypatch):
    """The exp instance had no server; start-check said nothing and
    Start answered systemctl's "exit status 5"."""
    from app.server import service_control

    refusal = "The DayZ server is not installed in /srv/exp yet."
    monkeypatch.setattr(service_control, "start_refusal", lambda config: refusal)
    monkeypatch.setattr(
        service_control, "start_server", lambda config, anyway=False: pytest.fail("start_server ran")
    )
    assert client.get("/api/service/start-check").json() == {"refusal": refusal, "problem": None}
    for body in (None, {"anyway": True}):
        resp = client.post("/api/service/start", json=body)
        assert resp.status_code == 409
        assert resp.json()["detail"] == refusal
