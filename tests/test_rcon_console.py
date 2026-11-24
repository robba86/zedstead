"""The raw RCon console (TODO D1).

A free-text console is not a new capability — the panel can already kick, ban,
broadcast, stop and restart. What it adds is reach: every BattlEye command the
server understands, including the ones zsm has no button for and the ones a
mod adds. So the tests are about the things reach makes easy to get wrong —
sending nothing, losing what you sent when it fails, and an unbounded history.

No RCON server is contacted: `rcon_ops.rcon_send` is patched in the module the
router calls it through.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.rcon import rcon_ops

PASSWORD = "testpass123"


@pytest.fixture()
def client(sample_config):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    tclient = TestClient(server.create_app(sample_config))
    tclient.post("/api/auth/login", json={"password": PASSWORD})
    return tclient


@pytest.fixture()
def sent(monkeypatch):
    """Record what was sent; answer with a canned reply."""
    calls: list[str] = []
    reply = {"text": "Players on server:\n[#] [IP] [Name]"}

    async def fake_send(config, command):
        calls.append(command)
        if isinstance(reply["text"], Exception):
            raise reply["text"]
        return reply["text"]

    monkeypatch.setattr(rcon_ops, "rcon_send", fake_send)
    return calls, reply


def test_the_console_needs_a_session(sample_config):
    from app.web import server

    app = server.create_app(sample_config)
    resp = TestClient(app).post("/api/rcon/command", json={"command": "players"})
    assert resp.status_code == 401


def test_a_command_is_sent_verbatim_and_the_reply_returned(client, sent):
    calls, reply = sent
    resp = client.post("/api/rcon/command", json={"command": "  players  "})

    assert resp.status_code == 200, resp.text
    # Trimmed, but otherwise untouched — the point of a raw console is that
    # nothing between the box and BattlEye rewrites it.
    assert calls == ["players"]
    body = resp.json()
    assert body["response"] == reply["text"]
    assert body["ok"] is True
    assert body["command"] == "players"


def test_an_empty_command_is_refused_without_contacting_the_server(client, sent):
    calls, _ = sent
    for blank in ("", "   ", "\n"):
        assert client.post("/api/rcon/command", json={"command": blank}).status_code == 400
    assert calls == []


def test_a_failure_is_a_502_and_is_still_remembered(client, sent):
    """"What did I send and what happened" is the whole point, so a failure
    belongs in the history as much as a success does."""
    _calls, reply = sent
    reply["text"] = rcon_ops.RconError("RCon is not reachable")

    resp = client.post("/api/rcon/command", json={"command": "#shutdown"})
    assert resp.status_code == 502
    assert "not reachable" in resp.json()["detail"]

    entries = client.get("/api/rcon/history").json()["entries"]
    assert entries[-1]["command"] == "#shutdown"
    assert entries[-1]["ok"] is False
    assert "not reachable" in entries[-1]["response"]


def test_the_history_is_oldest_first_and_carries_both_halves(client, sent):
    _calls, reply = sent
    reply["text"] = "one"
    client.post("/api/rcon/command", json={"command": "first"})
    reply["text"] = "two"
    client.post("/api/rcon/command", json={"command": "second"})

    entries = client.get("/api/rcon/history").json()["entries"]
    assert [e["command"] for e in entries] == ["first", "second"]
    assert [e["response"] for e in entries] == ["one", "two"]
    assert all(e["at"].endswith("Z") for e in entries)


def test_the_history_is_bounded(client, sent):
    """It is memory behind a text box that invites repetition."""
    from app.web.routers.players import RCON_HISTORY_LIMIT

    for i in range(RCON_HISTORY_LIMIT + 10):
        client.post("/api/rcon/command", json={"command": f"say {i}"})

    body = client.get("/api/rcon/history").json()
    assert len(body["entries"]) == RCON_HISTORY_LIMIT
    assert body["limit"] == RCON_HISTORY_LIMIT
    # The newest survive, not the oldest.
    assert body["entries"][-1]["command"] == f"say {RCON_HISTORY_LIMIT + 9}"


def test_the_history_belongs_to_the_app_not_the_process(sample_config, sent):
    """`WebState` is per-app so tests can run several; the history must live
    there rather than in a module global, or two apps would share one."""
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    first = TestClient(server.create_app(sample_config))
    first.post("/api/auth/login", json={"password": PASSWORD})
    second = TestClient(server.create_app(sample_config))
    second.post("/api/auth/login", json={"password": PASSWORD})

    first.post("/api/rcon/command", json={"command": "players"})

    assert len(first.get("/api/rcon/history").json()["entries"]) == 1
    assert second.get("/api/rcon/history").json()["entries"] == []


def test_the_console_does_not_write_to_the_access_log(client, sent, tmp_path, monkeypatch):
    """Different question, different store: the access log answers "who
    reached the panel" and is on disk for that reason."""
    from app.web import access_log

    monkeypatch.setattr("app.shared.paths.CONFIG_DIR", tmp_path)
    client.post("/api/rcon/command", json={"command": "players"})
    assert access_log.read_recent() == []
