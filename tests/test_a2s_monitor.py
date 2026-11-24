"""Tests for cli.a2s_monitor — A2S query result handling."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.monitoring.a2s_monitor import ServerQueryResult, query_server, query_server_sync


class TestServerQueryResult:
    def test_online_result(self):
        r = ServerQueryResult(
            online=True,
            server_name="Test Server",
            map_name="chernarusplus",
            player_count=24,
            max_players=60,
        )
        assert r.online is True
        assert r.player_count == 24
        assert r.error is None

    def test_offline_result(self):
        r = ServerQueryResult(online=False, error="Timeout")
        assert r.online is False
        assert r.error == "Timeout"


class TestQueryServerSync:
    @patch("app.monitoring.a2s_monitor.a2s")
    def test_success(self, mock_a2s):
        info = MagicMock()
        info.server_name = "DayZ Server"
        info.map_name = "chernarusplus"
        info.player_count = 10
        info.max_players = 60
        info.vac_enabled = True
        info.version = "1.26"
        info.ping = 0.015  # a2s reports seconds
        info.keywords = "battleye,lqs0"
        mock_a2s.info.return_value = info

        result = query_server_sync("127.0.0.1", 27016)
        assert result.online is True
        assert result.server_name == "DayZ Server"
        assert result.player_count == 10
        assert result.version == "1.26"
        assert result.ping_ms == pytest.approx(15.0)

    @patch("app.monitoring.a2s_monitor.a2s")
    def test_timeout(self, mock_a2s):
        import socket

        mock_a2s.info.side_effect = socket.timeout("timed out")

        result = query_server_sync("127.0.0.1", 27016)
        assert result.online is False
        assert "Timeout" in result.error

    @patch("app.monitoring.a2s_monitor.a2s")
    def test_connection_refused(self, mock_a2s):
        mock_a2s.info.side_effect = ConnectionRefusedError()

        result = query_server_sync("127.0.0.1", 27016)
        assert result.online is False
        assert "refused" in result.error

    @patch("app.monitoring.a2s_monitor.a2s")
    def test_malformed_response_does_not_raise(self, mock_a2s):
        # a2s raises BrokenMessageError (not OSError) on a garbage UDP packet.
        from a2s.exceptions import BrokenMessageError

        mock_a2s.info.side_effect = BrokenMessageError("bad packet")

        result = query_server_sync("127.0.0.1", 27016)
        assert result.online is False
        assert "Bad query response" in result.error


class TestQueryServerAsync:
    @pytest.mark.asyncio
    @patch("app.monitoring.a2s_monitor.a2s")
    async def test_success(self, mock_a2s):
        info = MagicMock()
        info.server_name = "Async Server"
        info.map_name = "enoch"
        info.player_count = 5
        info.max_players = 40
        info.vac_enabled = False
        info.version = "1.27"
        info.ping = 0.008
        info.keywords = ""
        mock_a2s.ainfo = AsyncMock(return_value=info)

        result = await query_server("127.0.0.1", 27016)
        assert result.online is True
        assert result.server_name == "Async Server"
        assert result.map_name == "enoch"
        assert result.ping_ms == pytest.approx(8.0)

    @pytest.mark.asyncio
    @patch("app.monitoring.a2s_monitor.a2s")
    async def test_timeout(self, mock_a2s):
        import asyncio

        mock_a2s.ainfo = AsyncMock(side_effect=asyncio.TimeoutError())

        result = await query_server("127.0.0.1", 27016)
        assert result.online is False
        assert "Timeout" in result.error

    @pytest.mark.asyncio
    @patch("app.monitoring.a2s_monitor.a2s")
    async def test_malformed_response_does_not_raise(self, mock_a2s):
        from a2s.exceptions import BufferExhaustedError

        mock_a2s.ainfo = AsyncMock(side_effect=BufferExhaustedError())

        result = await query_server("127.0.0.1", 27016)
        assert result.online is False
        assert "Bad query response" in result.error
