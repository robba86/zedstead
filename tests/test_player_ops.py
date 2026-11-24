"""Tests for app/ops/player_ops.py — why the live player list is (un)available.

Session/summary computation is covered through tests/test_web_players.py.
"""

from __future__ import annotations

import pytest

from app.ops import player_ops
from app.rcon.rcon_ops import RconError, RconPlayer


@pytest.fixture()
def rcon_config(sample_config):
    sample_config.rcon_password = "mypass"
    sample_config.rcon_port = 2305
    return sample_config


def _player(name: str = "Bob") -> RconPlayer:
    return RconPlayer(index=1, ip="1.2.3.4", port=0, ping=20, guid="g1", name=name, lobby=False)


class _Manager:
    def __init__(self, connected: bool, players: list[RconPlayer] | None = None) -> None:
        self._connected = connected
        self._players = players or []
        self.fetched = False

    def is_connected(self) -> bool:
        return self._connected

    async def fetch_players(self) -> list[RconPlayer]:
        self.fetched = True
        return self._players


@pytest.mark.asyncio
async def test_not_configured(sample_config):
    sample_config.rcon_password = ""
    status = await player_ops.fetch_online(sample_config)
    assert status.available is False
    assert status.reason == player_ops.REASON_NOT_CONFIGURED
    assert status.players == []


@pytest.mark.asyncio
async def test_stopped_server_is_not_probed(rcon_config, monkeypatch):
    """The whole point: no connect attempt when nothing can be listening."""

    async def _unexpected(config):
        raise AssertionError("probed RCON on a stopped server")

    monkeypatch.setattr("app.rcon.rcon_ops.get_rcon_players", _unexpected)
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)

    status = await player_ops.fetch_online(rcon_config)
    assert status.reason == player_ops.REASON_SERVER_STOPPED
    assert status.available is False
    assert status.server_running is False


@pytest.mark.asyncio
async def test_running_server_with_dead_rcon_lists_setup_problems(rcon_config, monkeypatch):
    async def _fail(config):
        raise RconError("timed out")

    monkeypatch.setattr("app.rcon.rcon_ops.get_rcon_players", _fail)
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: True)
    monkeypatch.setattr("app.rcon.rcon_ops.check_rcon_setup", lambda config: ["RConPort mismatch."])

    status = await player_ops.fetch_online(rcon_config)
    assert status.reason == player_ops.REASON_RCON_UNREACHABLE
    assert status.server_running is True
    assert status.setup_problems == ["RConPort mismatch."]


@pytest.mark.asyncio
async def test_connected_manager_is_preferred(rcon_config, monkeypatch):
    async def _unexpected(config):
        raise AssertionError("opened a second RCON connection")

    monkeypatch.setattr("app.rcon.rcon_ops.get_rcon_players", _unexpected)
    manager = _Manager(connected=True, players=[_player()])

    status = await player_ops.fetch_online(rcon_config, manager)
    assert manager.fetched is True
    assert status.available is True
    assert [p.name for p in status.players] == ["Bob"]
    # Full RconPlayer rows, not the narrowed OnlinePlayer — the Players page
    # kicks by index.
    assert status.players[0].index == 1


@pytest.mark.asyncio
async def test_disconnected_manager_falls_back_to_one_shot(rcon_config, monkeypatch):
    async def _fetch(config):
        return [_player("Ann")]

    monkeypatch.setattr("app.rcon.rcon_ops.get_rcon_players", _fetch)
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: True)

    status = await player_ops.fetch_online(rcon_config, _Manager(connected=False))
    assert status.available is True
    assert [p.name for p in status.players] == ["Ann"]


@pytest.mark.asyncio
async def test_get_online_players_wrapper(rcon_config, monkeypatch):
    async def _fetch(config):
        return [_player()]

    monkeypatch.setattr("app.rcon.rcon_ops.get_rcon_players", _fetch)
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: True)
    players = await player_ops.get_online_players(rcon_config)
    assert players is not None
    assert players[0] == player_ops.OnlinePlayer(guid="g1", name="Bob", ping=20, lobby=False)

    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)
    assert await player_ops.get_online_players(rcon_config) is None


# ---------------------------------------------------------------------------
# A session no disconnect closed is capped (test pass 1, F44)
# ---------------------------------------------------------------------------


def _e(ts: str, event: str, guid: str = "g1", name: str = "Anna") -> dict:
    return {"timestamp": ts, "event": event, "guid": guid, "name": name}


def test_the_findings_phantom_session_is_capped_at_six_hours():
    """Connect 09-20 05:25, next connect 09-24 10:17, no disconnect between:
    the first session read as ~101 hours."""
    from app.ops.player_ops import compute_player_summaries, compute_sessions

    sessions = compute_sessions([
        _e("2026-09-20T05:25:00", "connect"),
        _e("2026-09-24T10:17:00", "connect"),
    ])

    first = sessions[0]
    assert first.duration_seconds == 6 * 3600
    assert first.capped is True
    assert sessions[1].disconnect_time is None  # still on

    [summary] = compute_player_summaries(sessions)
    assert summary.total_playtime_seconds == 6 * 3600
    assert summary.capped_sessions == 1


def test_a_closed_seven_hour_session_stays_seven_hours():
    from app.ops.player_ops import compute_player_summaries, compute_sessions

    sessions = compute_sessions([
        _e("2026-09-20T05:00:00", "connect"),
        _e("2026-09-20T12:00:00", "disconnect"),
    ])

    assert sessions[0].duration_seconds == 7 * 3600
    assert sessions[0].capped is False
    assert compute_player_summaries(sessions)[0].capped_sessions == 0


def test_a_short_unclosed_session_is_not_marked_capped():
    """A reconnect inside the cap closes the old session at the reconnect."""
    from app.ops.player_ops import compute_sessions

    sessions = compute_sessions([
        _e("2026-09-20T05:00:00", "connect"),
        _e("2026-09-20T06:30:00", "connect"),
    ])

    assert sessions[0].duration_seconds == 90 * 60
    assert sessions[0].capped is False


def test_the_bookkeeping_reason_key_is_ignored():
    """plans/53 C events carry an extra `reason`; they pair like any other."""
    from app.ops.player_ops import compute_sessions

    sessions = compute_sessions([
        {**_e("2026-09-20T05:00:00", "connect"), "reason": "resync"},
        {**_e("2026-09-20T06:00:00", "disconnect"), "reason": "connection_ended"},
    ])

    assert [(s.duration_seconds, s.capped) for s in sessions] == [(3600, False)]
