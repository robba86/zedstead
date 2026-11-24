"""Tests for app.web.server's lifespan wiring of WebNotifier + RconManager
(Stage 8)."""

from __future__ import annotations

from fastapi.testclient import TestClient

PASSWORD = "testpass123"


def _app(sample_config):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    return server.create_app(sample_config)


def test_notifier_starts_and_stops_when_enabled(sample_config):
    sample_config.web_notifier_enabled = True
    app = _app(sample_config)

    with TestClient(app) as client:
        state = client.app.state.ctx
        assert state.notifier is not None
        assert state.rcon_manager is None  # rcon not configured by default

    assert app.state.ctx.notifier is None


def test_notifier_skipped_when_disabled(sample_config):
    sample_config.web_notifier_enabled = False
    app = _app(sample_config)

    with TestClient(app):
        assert app.state.ctx.notifier is None


def test_rcon_manager_starts_when_configured(sample_config):
    sample_config.web_notifier_enabled = True
    sample_config.session_tracker_enabled = True
    sample_config.rcon_password = "secret"
    sample_config.rcon_port = 2305
    app = _app(sample_config)

    with TestClient(app):
        state = app.state.ctx
        assert state.rcon_manager is not None
        assert state.notifier is not None

    assert app.state.ctx.rcon_manager is None


def test_rcon_manager_skipped_when_session_tracker_disabled(sample_config):
    sample_config.session_tracker_enabled = False
    sample_config.rcon_password = "secret"
    sample_config.rcon_port = 2305
    app = _app(sample_config)

    with TestClient(app):
        assert app.state.ctx.rcon_manager is None


def test_saving_the_battleye_form_starts_the_rcon_connection(sample_config, monkeypatch, server_stopped):
    """A fresh install outside Docker: zsm has no RCon password until the
    BattlEye editor is saved. The connection used to wait for the next
    `zsm web` start (plans/68 open findings, row 8)."""
    import time

    from app.rcon.rcon_manager import RconManager

    # No socket: this test is about who starts the manager, not what it dials.
    # `server_stopped`, because the editor refuses to write beserver_x64.cfg
    # under a running server.
    monkeypatch.setattr(RconManager, "start", lambda self: None)
    sample_config.web_notifier_enabled = False
    app = _app(sample_config)

    with TestClient(app) as client:
        state = app.state.ctx
        assert state.rcon_manager is None
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200

        resp = client.put(
            "/api/configs/battleye", json={"values": {"RConPassword": "newpass", "RConPort": "2310"}}
        )
        assert resp.status_code == 200, resp.text

        deadline = time.monotonic() + 3.0
        while state.rcon_manager is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert state.rcon_manager is not None
        assert state.rcon_target == ("127.0.0.1", 2310, "newpass")

    assert app.state.ctx.rcon_manager is None
