"""The WebSocket follows the session it opened with (test pass 2, N27).

A socket was authenticated once, at its handshake. Logging out, a password
change or a deleted account left it receiving every broadcast until the tab
closed. These tests drive a real app through TestClient.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.web.auth import SESSION_COOKIE_NAME, hash_password
from app.web.ws import WsHub

PASSWORD = "testpass123"


@pytest.fixture()
def web_app(sample_config):
    from app.web import server

    sample_config.web_password_hash = hash_password(PASSWORD)
    return server.create_app(sample_config)


def _cookie(token: str) -> dict:
    return {"cookie": f"{SESSION_COOKIE_NAME}={token}"}


def _hello(ws) -> None:
    assert ws.receive_json()["type"] == "hello"


def _closed_with(app, ws, code: int, open_left: int = 0) -> None:
    """Wait until the hub has dropped the socket, then read its close frame.

    Waiting on the hub first means a regression fails here after a deadline;
    a bare ``receive_json`` on a socket nobody closes blocks forever.
    """
    hub = app.state.ctx.hub
    deadline = time.monotonic() + 5
    while len(hub._tokens) > open_left:
        assert time.monotonic() < deadline, "the socket was never closed"
        time.sleep(0.02)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        ws.receive_json()
    assert exc_info.value.code == code


def test_logging_out_closes_the_socket(web_app):
    with TestClient(web_app) as client:
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        with client.websocket_connect("/ws") as ws:
            _hello(ws)
            assert client.post("/api/auth/logout").status_code == 200
            _closed_with(web_app, ws, 4401)


def test_a_message_that_is_not_an_object_is_ignored(web_app):
    """A list has no .get(): it raised and closed the socket."""
    with TestClient(web_app) as client:
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        with client.websocket_connect("/ws") as ws:
            _hello(ws)
            ws.send_json(["subscribe", "status"])
            ws.send_json(42)
            ws.send_json({"action": "ping"})
            assert ws.receive_json()["type"] == "pong"


def test_revoking_one_account_leaves_another_connected(web_app):
    sessions = web_app.state.ctx.sessions
    mod_token = sessions.create("mod")
    admin_token = sessions.create("admin")
    with TestClient(web_app) as client:
        with client.websocket_connect("/ws", headers=_cookie(mod_token)) as mod_ws, \
                client.websocket_connect("/ws", headers=_cookie(admin_token)) as admin_ws:
            _hello(mod_ws)
            _hello(admin_ws)
            # From this (non-loop) thread, the way the sync session
            # dependency revokes from FastAPI's thread pool.
            sessions.revoke_all_for("mod")
            _closed_with(web_app, mod_ws, 4401, open_left=1)
            admin_ws.send_json({"action": "ping"})
            assert admin_ws.receive_json()["type"] == "pong"


def test_an_expired_session_closes_its_socket(web_app):
    sessions = web_app.state.ctx.sessions
    token = sessions.create("admin")
    with TestClient(web_app) as client:
        with client.websocket_connect("/ws", headers=_cookie(token)) as ws:
            _hello(ws)
            sessions._sessions[token] = sessions._sessions[token]._replace(
                expiry=time.time() - 1
            )
            sessions.validate("some-other-token")  # any check prunes
            _closed_with(web_app, ws, 4401)


def test_a_foreign_origin_is_refused(web_app):
    token = web_app.state.ctx.sessions.create("admin")
    with TestClient(web_app) as client:
        for origin in ("http://evil.example", "http://testserver:9999"):
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect(
                    "/ws", headers={**_cookie(token), "origin": origin}
                ) as ws:
                    ws.receive_json()
            assert exc_info.value.code == 4403, origin


@pytest.mark.parametrize(
    "headers",
    [
        {},  # no Origin: curl, a healthcheck
        {"origin": "http://testserver"},  # TestClient's own Host
        {"origin": "https://panel.example.com", "x-forwarded-host": "panel.example.com"},
    ],
)
def test_same_origin_and_no_origin_are_accepted(web_app, headers):
    token = web_app.state.ctx.sessions.create("admin")
    with TestClient(web_app) as client:
        with client.websocket_connect("/ws", headers={**_cookie(token), **headers}) as ws:
            _hello(ws)


class _FakeWs:
    def __init__(self) -> None:
        self.closed_with: int | None = None

    async def send_json(self, message: dict) -> None:
        pass

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code


async def test_an_unknown_channel_is_not_subscribed():
    hub = WsHub()
    ws = _FakeWs()
    hub.connect(ws, "tok")
    await hub.subscribe(ws, "nonsense")
    await hub.subscribe(ws, "rpt")
    assert hub._connections[ws] == {"rpt"}


async def test_close_sessions_closes_only_matching_sockets():
    hub = WsHub()
    a, b = _FakeWs(), _FakeWs()
    hub.connect(a, "tok-a")
    hub.connect(b, "tok-b")
    await hub.close_sessions({"tok-a"})
    assert a.closed_with == 4401
    assert b.closed_with is None
    assert a not in hub._connections and b in hub._connections
