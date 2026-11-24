"""Steam A2S query for server visibility and public status.

Uses the python-a2s library to check if the server is responding to
Steam query protocol requests — the same mechanism server browsers use.
A server can be "running" (process alive) but invisible if the query
port is blocked or misconfigured.
"""

from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass

import a2s


@dataclass
class ServerQueryResult:
    """Result of an A2S_INFO query."""

    online: bool
    server_name: str = ""
    map_name: str = ""
    player_count: int = 0
    max_players: int = 0
    vac_enabled: bool = False
    version: str = ""
    ping_ms: float = 0.0
    keywords: str = ""
    error: str | None = None


def _online_result(info) -> ServerQueryResult:
    """The result for a server that answered A2S_INFO."""
    return ServerQueryResult(
        online=True,
        server_name=info.server_name,
        map_name=info.map_name,
        player_count=info.player_count,
        max_players=info.max_players,
        vac_enabled=info.vac_enabled,
        version=info.version,
        # a2s measures the round trip in seconds.
        ping_ms=(getattr(info, "ping", 0.0) or 0.0) * 1000,
        keywords=getattr(info, "keywords", "") or "",
    )


async def query_server(
    ip: str, query_port: int, timeout: float = 3.0
) -> ServerQueryResult:
    """Query server via A2S_INFO. Returns visibility and public metadata."""
    try:
        info = await a2s.ainfo((ip, query_port), timeout=timeout)
        return _online_result(info)
    except (asyncio.TimeoutError, socket.timeout):
        return ServerQueryResult(
            online=False,
            error="Timeout - server not responding to queries",
        )
    except ConnectionRefusedError:
        return ServerQueryResult(
            online=False,
            error="Connection refused - query port may be wrong",
        )
    except OSError as exc:
        return ServerQueryResult(online=False, error=str(exc))
    except Exception as exc:
        # a2s raises BrokenMessageError/BufferExhaustedError (not OSError) on a
        # malformed or truncated query response — treat as "not visible".
        return ServerQueryResult(online=False, error=f"Bad query response: {exc}")


async def query_players(
    ip: str, query_port: int, timeout: float = 3.0
) -> list[dict] | None:
    """Query A2S_PLAYER. Returns player list or None on failure.

    Note: DayZ often returns empty player names via A2S.
    Use RCON for reliable player data.
    """
    try:
        players = await a2s.aplayers((ip, query_port), timeout=timeout)
        return [
            {"name": p.name, "duration": p.duration, "score": p.score}
            for p in players
        ]
    except Exception:
        return None


def query_server_sync(
    ip: str, query_port: int, timeout: float = 3.0
) -> ServerQueryResult:
    """Synchronous version for use in threads and CLI commands."""
    try:
        info = a2s.info((ip, query_port), timeout=timeout)
        return _online_result(info)
    except (socket.timeout, TimeoutError):
        return ServerQueryResult(
            online=False,
            error="Timeout - server not responding to queries",
        )
    except ConnectionRefusedError:
        return ServerQueryResult(
            online=False,
            error="Connection refused - query port may be wrong",
        )
    except OSError as exc:
        return ServerQueryResult(online=False, error=str(exc))
    except Exception as exc:
        # a2s raises BrokenMessageError/BufferExhaustedError (not OSError) on a
        # malformed or truncated query response — treat as "not visible".
        return ServerQueryResult(online=False, error=f"Bad query response: {exc}")
