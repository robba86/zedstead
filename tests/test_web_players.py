"""Tests for /api/players*, /api/bans*, /api/priority, /api/chat
(app/web/routers/players.py)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

PASSWORD = "testpass123"


@pytest.fixture()
def client(sample_config, tmp_path, monkeypatch):
    from app.web import server
    from app.web.auth import hash_password

    # player_ops.fetch_online refuses to open an RCON connection to a stopped
    # server, so the live-player tests have to say the server is up (and no
    # test may shell out to systemctl).
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: True)
    sample_config.web_password_hash = hash_password(PASSWORD)
    sample_config.rcon_password = "mypass"
    sample_config.rcon_port = 2305
    sample_config.save()
    app = server.create_app(sample_config)
    tclient = TestClient(app)
    tclient.post("/api/auth/login", json={"password": PASSWORD})
    return tclient


def test_players_requires_session(sample_config):
    from app.web import server

    app = server.create_app(sample_config)
    resp = TestClient(app).get("/api/players/online")
    assert resp.status_code == 401


def test_online_players_409_when_rcon_not_configured(sample_config):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    sample_config.rcon_password = ""
    app = server.create_app(sample_config)
    tclient = TestClient(app)
    tclient.post("/api/auth/login", json={"password": PASSWORD})

    resp = tclient.get("/api/players/online")
    assert resp.status_code == 409


def test_online_players_shape(client, monkeypatch):
    from app.rcon.rcon_ops import RconPlayer

    async def _fake_get_players(config):
        return [RconPlayer(index=0, ip="1.2.3.4", port=0, ping=20, guid="abc", name="Bob", lobby=False)]

    monkeypatch.setattr("app.rcon.rcon_ops.get_rcon_players", _fake_get_players)
    resp = client.get("/api/players/online")
    assert resp.status_code == 200
    body = resp.json()
    assert body["players"] == [
        {"index": 0, "ip": "1.2.3.4", "port": 0, "ping": 20, "guid": "abc", "name": "Bob", "lobby": False}
    ]
    assert body["available"] is True
    assert body["reason"] == ""


def test_online_players_200_when_server_stopped(client, monkeypatch):
    """A stopped server is not an error — and must not be probed over RCON."""
    probed = False

    async def _fake_get_players(config):
        nonlocal probed
        probed = True
        return []

    monkeypatch.setattr("app.rcon.rcon_ops.get_rcon_players", _fake_get_players)
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)

    resp = client.get("/api/players/online")
    assert resp.status_code == 200
    body = resp.json()
    assert body["players"] == []
    assert body["available"] is False
    assert body["reason"] == "server_stopped"
    assert body["server_running"] is False
    assert body["detail"]
    assert probed is False


def test_online_players_502_detail_names_setup_problems(client, monkeypatch):
    """A running server whose RCON refuses us says what is misconfigured."""
    from app.rcon.rcon_ops import RconError

    async def _fake_get_players(config):
        raise RconError("timeout")

    monkeypatch.setattr("app.rcon.rcon_ops.get_rcon_players", _fake_get_players)
    monkeypatch.setattr("app.rcon.rcon_ops.check_rcon_setup", lambda config: ["RConPort mismatch."])

    resp = client.get("/api/players/online")
    assert resp.status_code == 502
    assert "RConPort mismatch." in resp.json()["detail"]


def test_online_players_uses_persistent_manager(client, monkeypatch):
    """With the shared connection logged in, no second connection is opened."""
    from app.rcon.rcon_ops import RconPlayer

    async def _unexpected(config):
        raise AssertionError("opened a second RCON connection")

    monkeypatch.setattr("app.rcon.rcon_ops.get_rcon_players", _unexpected)

    class _Manager:
        def is_connected(self):
            return True

        async def fetch_players(self):
            return [RconPlayer(index=1, ip="", port=0, ping=5, guid="g", name="Ann", lobby=True)]

    client.app.state.ctx.rcon_manager = _Manager()
    try:
        resp = client.get("/api/players/online")
    finally:
        client.app.state.ctx.rcon_manager = None
    assert resp.status_code == 200
    assert resp.json()["players"][0]["name"] == "Ann"


def test_online_players_502_on_rcon_error(client, monkeypatch):
    from app.rcon.rcon_ops import RconError

    async def _fake_get_players(config):
        raise RconError("timeout")

    monkeypatch.setattr("app.rcon.rcon_ops.get_rcon_players", _fake_get_players)
    resp = client.get("/api/players/online")
    assert resp.status_code == 502


def test_kick_player(client, monkeypatch):
    async def _fake_kick(config, index, reason=""):
        assert index == 3
        assert reason == "afk"
        return "Player kicked"

    monkeypatch.setattr("app.rcon.rcon_ops.kick_player", _fake_kick)
    resp = client.post("/api/players/kick", json={"player_id": 3, "reason": "afk"})
    assert resp.status_code == 200
    assert resp.json() == {"response": "Player kicked"}


def test_ban_player(client, monkeypatch):
    async def _fake_ban(config, index, duration, reason):
        return "Player banned"

    monkeypatch.setattr("app.rcon.rcon_ops.ban_player", _fake_ban)
    resp = client.post("/api/players/ban", json={"player_id": 1, "duration_minutes": 60, "reason": "cheating"})
    assert resp.status_code == 200


def test_message_players(client, monkeypatch):
    async def _fake_send(config, message):
        return "OK"

    monkeypatch.setattr("app.rcon.rcon_ops.send_global_message", _fake_send)
    resp = client.post("/api/players/message", json={"message": "hello"})
    assert resp.status_code == 200


def test_rcon_actions_on_a_stopped_server_answer_409_at_once(client, monkeypatch):
    """Test pass 2, N5: every one-shot RCon route waited CONNECT_TIMEOUT (20s)
    on a stopped server, then answered 502. The real `_connect` runs here; no
    socket may be opened."""
    import time

    import berconpy

    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)

    def _no_socket():
        raise AssertionError("opened an RCon connection to a stopped server")

    monkeypatch.setattr(berconpy, "ArmaClient", _no_socket)

    for method, path, body in (
        ("post", "/api/players/message", {"message": "hello"}),
        ("post", "/api/players/kick", {"player_id": 1, "reason": "x"}),
        ("post", "/api/players/ban", {"player_id": 1, "duration_minutes": 60, "reason": "x"}),
        ("get", "/api/bans/rcon", None),
        ("delete", "/api/bans/rcon/0", None),
        ("post", "/api/rcon/command", {"command": "players"}),
    ):
        started = time.monotonic()
        resp = getattr(client, method)(path, **({"json": body} if body else {}))
        assert resp.status_code == 409, (path, resp.text)
        assert resp.json()["detail"] == "The server is stopped — start it first."
        assert time.monotonic() - started < 1.0, path

    # The console still records the failure: a refusal is an answer.
    [entry] = client.get("/api/rcon/history").json()["entries"]
    assert entry["ok"] is False and "stopped" in entry["response"]


def test_sessions_empty(client):
    resp = client.get("/api/players/sessions")
    assert resp.status_code == 200
    assert resp.json() == {"sessions": []}


def test_summaries_empty(client, monkeypatch):
    from app.rcon.rcon_ops import RconError

    async def _fail(config):
        raise RconError("not connected")

    monkeypatch.setattr("app.rcon.rcon_ops.get_rcon_players", _fail)
    resp = client.get("/api/players/summaries")
    assert resp.status_code == 200
    assert resp.json() == {"summaries": []}


def test_sessions_from_events(client, monkeypatch):
    events = [
        {"event": "connect", "guid": "g1", "name": "Bob", "timestamp": "2026-01-01T10:00:00"},
        {"event": "disconnect", "guid": "g1", "name": "Bob", "timestamp": "2026-01-01T11:00:00"},
    ]
    monkeypatch.setattr(
        "app.monitoring.session_tracker.read_session_events", lambda max_days, path: events,
    )
    resp = client.get("/api/players/sessions")
    assert resp.status_code == 200
    sessions = resp.json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["guid"] == "g1"
    assert sessions[0]["duration_seconds"] == 3600


# ---------------------------------------------------------------------------
# Bans (file-based)
# ---------------------------------------------------------------------------


def test_bans_empty(client):
    resp = client.get("/api/bans")
    assert resp.status_code == 200
    body = resp.json()
    assert body["bans"] == []
    assert body["stats"]["total"] == 0


def test_bans_carry_the_server_banlist_apart_from_the_stats(client, sample_config):
    """F45: DayZ's own ban.txt is listed, read-only, and not counted as a
    BattlEye ban."""
    (sample_config.server_dir_path / "ban.txt").write_text(
        "//comment\n\n76561198000000011\n76561198000000012\n"
    )
    body = client.get("/api/bans").json()

    assert body["server_banlist"] == {
        "path": "ban.txt",
        "ids": ["76561198000000011", "76561198000000012"],
        "disabled": False,
    }
    assert body["bans"] == []
    assert body["stats"]["total"] == 0


def test_add_and_delete_ban(client):
    resp = client.post(
        "/api/bans", json={"identifier": "12345678901234567890123456789012", "reason": "cheating"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["identifier"] == "12345678901234567890123456789012"
    assert body["ban_type"] == "guid"
    assert body["is_permanent"] is True

    resp = client.get("/api/bans")
    assert len(resp.json()["bans"]) == 1

    resp = client.delete("/api/bans", params={"identifier": "12345678901234567890123456789012"})
    assert resp.status_code == 200
    resp = client.get("/api/bans")
    assert resp.json()["bans"] == []



class _FakeManager:
    def __init__(self, connected: bool):
        self.connected = connected
        self.sent: list[str] = []

    def is_connected(self) -> bool:
        return self.connected

    async def send_command(self, command: str) -> str:
        self.sent.append(command)
        return ""


def test_a_ban_written_while_rcon_is_up_is_loaded_into_the_running_server(client):
    # BattlEye reads bans.txt only at start and on loadBans; a ban written to
    # the file alone did nothing until the next restart (browser pass,
    # 2026 Q4).
    manager = _FakeManager(connected=True)
    client.app.state.ctx.rcon_manager = manager
    guid = "12345678901234567890123456789012"

    assert client.post("/api/bans", json={"identifier": guid}).json()["loaded"] is True
    assert client.delete("/api/bans", params={"identifier": guid}).json()["loaded"] is True
    assert manager.sent == ["loadBans", "loadBans"]


def test_imported_and_tidied_bans_are_loaded_into_the_running_server(client):
    """Import and Remove expired wrote bans.txt and stopped there, so an
    imported ban did nothing until the next restart, as a single one once did."""
    import datetime

    manager = _FakeManager(connected=True)
    client.app.state.ctx.rcon_manager = manager
    body = client.post(
        "/api/bans/import", json={"content": "12345678901234567890123456789012 -1 imported\n"}
    ).json()
    assert body["added"] == 1 and body["loaded"] is True

    past = int(datetime.datetime(2020, 1, 1).timestamp())
    client.post("/api/bans/import", json={"content": f"abcdefabcdefabcdefabcdefabcdefab {past} old\n"})
    body = client.post("/api/bans/cleanup-expired").json()
    assert body["removed"] == 1 and body["loaded"] is True
    assert manager.sent == ["loadBans", "loadBans", "loadBans"]


def test_a_ban_written_without_a_connection_says_it_waits_for_a_start(client):
    manager = _FakeManager(connected=False)
    client.app.state.ctx.rcon_manager = manager

    body = client.post("/api/bans", json={"identifier": "12345678901234567890123456789012"}).json()
    assert body["loaded"] is False
    assert manager.sent == []

def test_add_ban_invalid_identifier_422(client):
    resp = client.post("/api/bans", json={"identifier": "not-a-guid-or-ip"})
    assert resp.status_code == 422


def test_add_ban_duplicate_409(client):
    body = {"identifier": "12345678901234567890123456789012"}
    assert client.post("/api/bans", json=body).status_code == 200
    resp = client.post("/api/bans", json=body)
    assert resp.status_code == 409


def test_delete_ban_not_found_404(client):
    resp = client.delete("/api/bans", params={"identifier": "12345678901234567890123456789012"})
    assert resp.status_code == 404


def test_rcon_bans_list_and_delete(client, monkeypatch):
    async def _fake_get_bans(config):
        return "#0  permanent  cheater\n#1  60min  afk"

    monkeypatch.setattr("app.rcon.rcon_ops.get_bans", _fake_get_bans)
    resp = client.get("/api/bans/rcon")
    assert resp.status_code == 200
    bans = resp.json()["bans"]
    assert bans == [
        {"index": 0, "duration": "permanent", "reason": "cheater"},
        {"index": 1, "duration": "60min", "reason": "afk"},
    ]

    async def _fake_remove(config, index):
        assert index == 1
        return "unbanned"

    monkeypatch.setattr("app.rcon.rcon_ops.remove_ban", _fake_remove)
    resp = client.delete("/api/bans/rcon/1")
    assert resp.status_code == 200


def test_rcon_bans_a_separator_in_a_reason_does_not_forge_a_row(client, monkeypatch):
    """berconpy reads BattlEye's answer row by row and keeps a U+2028 inside
    the reason; the listing must not split there (plans/68 LB-3). The answer
    is hand-written, parsed by berconpy's own parser, and formatted by the real
    rcon_ops.get_bans."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from berconpy.ext.arma.parser import parse_bans

    answer = (
        "GUID Bans:\n"
        "[#] [GUID] [Minutes left] [Reason]\n"
        "----------------------------------------------\n"
        f"0  {'a' * 32} perm griefing #1  perm  forged row\n"
        f"1  {'b' * 32} perm aimbot\n"
    )

    class _Client:
        async def fetch_bans(self):
            return [
                SimpleNamespace(index=b["index"], id=b["ban_id"], duration=b["duration"], reason=b["reason"])
                for b in parse_bans(answer)
            ]

    @asynccontextmanager
    async def _fake_connect(config):
        yield _Client()

    monkeypatch.setattr("app.rcon.rcon_ops._connect", _fake_connect)
    resp = client.get("/api/bans/rcon")

    assert resp.status_code == 200
    assert resp.json()["bans"] == [
        {"index": 0, "duration": "permanent", "reason": "griefing #1  perm  forged row"},
        {"index": 1, "duration": "permanent", "reason": "aimbot"},
    ]


# ---------------------------------------------------------------------------
# Priority list
# ---------------------------------------------------------------------------


def test_priority_round_trip(client):
    resp = client.get("/api/priority")
    assert resp.status_code == 200
    assert resp.json() == {"ids": []}

    resp = client.post("/api/priority", json={"steam_id": "76561198000000001"})
    assert resp.status_code == 200
    assert resp.json() == {"added": True}

    resp = client.get("/api/priority")
    assert resp.json() == {"ids": ["76561198000000001"]}

    resp = client.delete("/api/priority", params={"steam_id": "76561198000000001"})
    assert resp.status_code == 200
    assert client.get("/api/priority").json() == {"ids": []}


def test_priority_add_invalid_422(client):
    resp = client.post("/api/priority", json={"steam_id": "not-valid"})
    assert resp.status_code == 422


def test_priority_remove_not_found_404(client):
    resp = client.delete("/api/priority", params={"steam_id": "76561198000000001"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


def test_chat_empty(client):
    resp = client.get("/api/chat")
    assert resp.status_code == 200
    assert resp.json() == {"events": []}


def test_chat_search(client, monkeypatch):
    monkeypatch.setattr(
        "app.monitoring.chat_logger.search_chat",
        lambda query, max_hours, path: [{"name": "Bob", "message": "hello world"}],
    )
    resp = client.get("/api/chat", params={"q": "hello"})
    assert resp.status_code == 200
    assert resp.json() == {"events": [{"name": "Bob", "message": "hello world"}]}


def test_ban_stats_counts(client):
    client.post("/api/bans", json={"identifier": "12345678901234567890123456789012"})
    client.post("/api/bans", json={"identifier": "192.168.1.50", "duration_minutes": 60})

    stats = client.get("/api/bans").json()["stats"]
    assert stats["total"] == 2
    assert stats["permanent"] == 1
    assert stats["temporary"] == 1
    assert stats["guid_bans"] == 1
    assert stats["ip_bans"] == 1


def test_ban_import_export_roundtrip(client):
    resp = client.post(
        "/api/bans/import",
        json={"content": "12345678901234567890123456789012 -1 cheating\n192.168.1.50 -1 grief\n"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["added"] == 2
    assert body["total_source"] == 2

    # Re-import skips duplicates.
    resp = client.post(
        "/api/bans/import",
        json={"content": "12345678901234567890123456789012 -1 cheating\n"},
    )
    assert resp.json()["skipped_duplicates"] == 1

    resp = client.get("/api/bans/export")
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    assert "12345678901234567890123456789012" in resp.text


def test_ban_import_empty_422(client):
    assert client.post("/api/bans/import", json={"content": "  "}).status_code == 422


def test_ban_cleanup_expired(client, sample_config):
    bans_path = sample_config.battleye_dir / "bans.txt"
    # One expired temp ban (epoch 1000 is long past), one permanent.
    bans_path.write_text(
        "12345678901234567890123456789012 1000 old\n"
        "abcdefabcdefabcdefabcdefabcdefab -1 keep\n"
    )
    resp = client.post("/api/bans/cleanup-expired")
    assert resp.status_code == 200
    assert resp.json()["removed"] == 1
    bans = client.get("/api/bans").json()["bans"]
    assert [b["identifier"] for b in bans] == ["abcdefabcdefabcdefabcdefabcdefab"]


def test_summaries_survive_a_connection_dropped_mid_command(client, monkeypatch):
    """A drop under an in-flight command must not answer 500.

    berconpy cancels every pending command future when its connection dies, and
    that cancellation reaches the caller as `CancelledError` — a
    `BaseException`, which `except Exception` does not catch. It escaped into
    the request and uvicorn logged "Exception in ASGI application".
    """
    import asyncio

    from app.rcon.rcon_manager import RconManager
    from app.rcon.rcon_ops import RconError

    class _DyingClient:
        async def send_command(self, command: str) -> str:
            fut = asyncio.get_running_loop().create_future()
            fut.cancel()  # what AsyncCommander.reset() does on a dropped link
            return await fut

    async def _no_second_connection(config):
        raise RconError("server is down")

    monkeypatch.setattr("app.rcon.rcon_ops.get_rcon_players", _no_second_connection)

    manager = RconManager(client.app.state.ctx.config)
    manager._client = _DyingClient()  # type: ignore[assignment]
    manager._connected.set()
    client.app.state.ctx.rcon_manager = manager
    try:
        resp = client.get("/api/players/summaries")
    finally:
        client.app.state.ctx.rcon_manager = None

    assert resp.status_code == 200
    assert resp.json() == {"summaries": []}


# --- ban input that would not mean what was typed (test pass 2, N14/N15) ----

def test_a_multi_line_ban_reason_writes_no_second_ban(client, sample_config):
    resp = client.post(
        "/api/bans",
        json={"identifier": "2" * 32, "reason": "line one\n" + "3" * 32 + " -1 injected"},
    )
    assert resp.status_code == 422
    assert "single line" in resp.json()["detail"]
    bans = sample_config.battleye_dir / "bans.txt"
    assert "3" * 32 not in (bans.read_text() if bans.exists() else "")


def test_a_guid_with_a_trailing_newline_is_refused(client, sample_config):
    resp = client.post("/api/bans", json={"identifier": "2" * 32 + "\n", "duration_minutes": 60})
    assert resp.status_code == 422
    bans = sample_config.battleye_dir / "bans.txt"
    assert not bans.exists() or bans.read_text() == ""


def test_a_negative_ban_duration_is_not_permanent(client):
    resp = client.post("/api/bans", json={"identifier": "2" * 32, "duration_minutes": -50})
    assert resp.status_code == 422
    assert client.get("/api/bans").json()["bans"] == []


@pytest.mark.parametrize("ip", ["300.300.300.300", "999.1.1.1"])
def test_an_impossible_ip_is_refused(client, ip):
    resp = client.post("/api/bans", json={"identifier": ip, "reason": "night"})
    assert resp.status_code == 422
    assert resp.json()["detail"] == f"Not a valid GUID or IP: {ip!r}"
    assert client.get("/api/bans").json()["bans"] == []


def test_a_ban_too_long_is_refused_with_a_sentence(client):
    resp = client.post("/api/bans", json={"identifier": "1" * 32, "duration_minutes": 99999999999})
    assert resp.status_code == 422
    assert "0 means permanent" in resp.json()["detail"]
    assert client.get("/api/bans").json()["bans"] == []


def test_a_ban_past_2038_is_refused_on_both_ban_routes(client, monkeypatch):
    """auth-02: BattlEye may read the expiry as a 32-bit time, so neither the
    bans.txt route nor the RCon ban takes an end after 2038-01-19. Twenty
    years was inside the earlier 100-year cap."""
    sent = []

    async def _fake_ban(config, index, duration, reason):
        sent.append(duration)
        return "Player banned"

    monkeypatch.setattr("app.rcon.rcon_ops.ban_player", _fake_ban)
    twenty_years = 20 * 365 * 24 * 60
    for path, body in (("/api/bans", {"identifier": "1" * 32}), ("/api/players/ban", {"player_id": 1})):
        resp = client.post(path, json={**body, "duration_minutes": twenty_years})
        assert resp.status_code == 422, path
        assert resp.json()["detail"].startswith(
            "A temporary ban must end by 19 January 2038, 03:14:07 UTC"
        )
    assert sent == []
    assert client.get("/api/bans").json()["bans"] == []


def test_a_bans_txt_line_with_a_huge_expiry_still_lists(client, sample_config):
    """One such line answered 500 for everyone until
    it was removed."""
    bans = sample_config.battleye_dir / "bans.txt"
    bans.parent.mkdir(parents=True, exist_ok=True)
    bans.write_text("0123456789abcdef0123456789abcde1 6001792184061 night big\n")
    resp = client.get("/api/bans")
    assert resp.status_code == 200
    [ban] = resp.json()["bans"]
    assert ban["expires_at"] is None and ban["is_permanent"] is False


def test_an_rcon_ban_with_a_multi_line_reason_is_refused_before_rcon(client, monkeypatch):
    sent = []

    async def _fake_ban(config, index, duration, reason):
        sent.append(reason)
        return "Player banned"

    monkeypatch.setattr("app.rcon.rcon_ops.ban_player", _fake_ban)
    resp = client.post("/api/players/ban", json={"player_id": 1, "reason": "a\nb"})
    assert resp.status_code == 422
    resp = client.post("/api/players/ban", json={"player_id": 1, "duration_minutes": -1})
    assert resp.status_code == 422
    assert sent == []


# ---------------------------------------------------------------------------
# A viewer is never handed a player's IP address (SEC-7)
# ---------------------------------------------------------------------------


@pytest.fixture()
def roles_app(sample_config, monkeypatch):
    from app.rcon.rcon_ops import RconPlayer
    from app.web import server
    from app.web.auth import hash_password

    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: True)

    async def _fake_get_players(config):
        return [RconPlayer(index=0, ip="203.0.113.7", port=0, ping=20, guid="abc", name="Bob", lobby=False)]

    monkeypatch.setattr("app.rcon.rcon_ops.get_rcon_players", _fake_get_players)
    sample_config.web_password_hash = hash_password(PASSWORD)
    sample_config.rcon_password = "mypass"
    sample_config.rcon_port = 2305
    sample_config.save()
    app = server.create_app(sample_config)
    app.state.ctx.users.add("looker", "lookpass", "viewer")
    app.state.ctx.users.add("mandy", "modpass-1", "moderator")
    bans = sample_config.battleye_dir / "bans.txt"
    bans.parent.mkdir(parents=True, exist_ok=True)
    bans.write_text(
        "12345678901234567890123456789012 -1 cheating\n"
        "198.51.100.23 -1 grief\n"
        "198.51.100.24 -1 grief again\n",
        encoding="utf-8",
    )
    return app


def _as(app, name, password):
    tclient = TestClient(app)
    assert tclient.post(
        "/api/auth/login", json={"username": name, "password": password}
    ).status_code == 200
    return tclient


VIEWER = ("looker", "lookpass")
MODERATOR = ("mandy", "modpass-1")
ADMIN = ("admin", PASSWORD)


def test_a_viewer_gets_online_players_without_their_ip(roles_app):
    resp = _as(roles_app, *VIEWER).get("/api/players/online")
    assert resp.status_code == 200
    assert "203.0.113.7" not in resp.text
    [row] = resp.json()["players"]
    assert "ip" not in row
    assert row["name"] == "Bob" and row["guid"] == "abc"  # the rest stays


@pytest.mark.parametrize("who", [MODERATOR, ADMIN])
def test_moderators_and_admins_keep_the_ip(roles_app, who):
    [row] = _as(roles_app, *who).get("/api/players/online").json()["players"]
    assert row["ip"] == "203.0.113.7"


def test_a_viewer_sees_ip_bans_without_the_address(roles_app):
    resp = _as(roles_app, *VIEWER).get("/api/bans")
    assert resp.status_code == 200
    assert "198.51.100" not in resp.text
    bans = resp.json()["bans"]
    ip_bans = [b for b in bans if b["ban_type"] == "ip"]
    assert [b["identifier"] for b in ip_bans] == ["IP address hidden #1", "IP address hidden #2"]
    assert [b["display_id"] for b in ip_bans] == [b["identifier"] for b in ip_bans]
    assert [b["reason"] for b in ip_bans] == ["grief", "grief again"]
    # A GUID ban is not an address and is unchanged.
    assert any(b["identifier"] == "12345678901234567890123456789012" for b in bans)
    assert resp.json()["stats"]["ip_bans"] == 2


@pytest.mark.parametrize("who", [MODERATOR, ADMIN])
def test_moderators_and_admins_see_ip_bans(roles_app, who):
    text = _as(roles_app, *who).get("/api/bans").text
    assert "198.51.100.23" in text and "198.51.100.24" in text


def test_an_impossible_ip_already_in_bans_txt_stays_hidden_from_a_viewer(roles_app, sample_config):
    (sample_config.battleye_dir / "bans.txt").write_text("300.300.300.300 -1 by hand\n")
    assert "300.300.300.300" not in _as(roles_app, *VIEWER).get("/api/bans").text


def test_added_by_is_the_signed_in_account_not_the_body(roles_app):
    """The body's `added_by` was written as sent, so a moderator could put
    "admin" on a ban, and the panel's form never sent one."""
    guid = "0123456789abcdef0123456789abcdef"
    mod = _as(roles_app, *MODERATOR)
    resp = mod.post(
        "/api/bans",
        json={"identifier": guid, "reason": "x", "duration_minutes": 60, "added_by": "admin"},
    )
    assert resp.status_code == 200
    assert resp.json()["added_by"] == "mandy"
    [ban] = [b for b in mod.get("/api/bans").json()["bans"] if b["identifier"] == guid]
    assert ban["added_by"] == "mandy"


def test_the_bans_txt_download_is_for_moderators(roles_app):
    assert _as(roles_app, *VIEWER).get("/api/bans/export").status_code == 403
    resp = _as(roles_app, *MODERATOR).get("/api/bans/export")
    assert resp.status_code == 200
    assert "198.51.100.23" in resp.text
