"""Player management business logic — online players, sessions, activity history."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.rcon.rcon_ops import RconPlayer
    from app.shared.config import DayzConfig


@dataclass
class OnlinePlayer:
    guid: str
    name: str
    ping: int
    lobby: bool


@dataclass
class PlayerSession:
    guid: str
    name: str
    connect_time: datetime
    disconnect_time: datetime | None = None  # None = still connected
    # Closed by a later connect, never by a disconnect, and held to
    # MAX_UNCLOSED: the real end is unknown (test pass 1, F44).
    capped: bool = False

    @property
    def duration_seconds(self) -> int | None:
        if self.disconnect_time is None:
            return None
        return int((self.disconnect_time - self.connect_time).total_seconds())


@dataclass
class PlayerSummary:
    guid: str
    name: str
    total_sessions: int
    total_playtime_seconds: int
    first_seen: datetime
    last_seen: datetime
    is_online: bool
    # How many of the sessions behind total_playtime_seconds were capped.
    capped_sessions: int = 0


# ---------------------------------------------------------------------------
# Online players via RCON
# ---------------------------------------------------------------------------

# Why there is no live player list right now (OnlineStatus.reason).
REASON_OK = ""
REASON_NOT_CONFIGURED = "not_configured"
REASON_SERVER_STOPPED = "server_stopped"
REASON_RCON_UNREACHABLE = "rcon_unreachable"


@dataclass
class OnlineStatus:
    """The live player list, or the reason there isn't one.

    ``players`` are full ``RconPlayer`` rows (index, ip, ping, ...) — the web
    API hands them out as-is and the Players page kicks by ``index``, so this
    must not be narrowed to :class:`OnlinePlayer`.
    """

    players: list[RconPlayer]
    available: bool
    reason: str = REASON_OK
    detail: str = ""
    server_running: bool | None = None
    # Setup mistakes that would keep RCON from ever answering (rcon_ops.
    # check_rcon_setup) — only filled on the failure paths.
    setup_problems: list[str] = field(default_factory=list)


async def fetch_online(
    config: DayzConfig,
    rcon_manager: object | None = None,
) -> OnlineStatus:
    """Live players via RCON, or *why* they can't be fetched.

    There are three ways to have no player list and they need different words
    on screen, so the caller gets the reason instead of a bare failure:

    * ``not_configured`` — no RCON password/port in zsm;
    * ``server_stopped`` — nothing is listening because the server is down.
      **This is checked before any connect attempt**: connecting to a closed
      RCon port takes `rcon_ops.CONNECT_TIMEOUT` (20s) to fail, and before
      that timeout existed it never failed at all — which is what left the
      dashboard tile loading forever;
    * ``rcon_unreachable`` — the server is up but RCON did not answer (wrong
      password/port, BattlEye off). ``setup_problems`` says which, when the
      config files disagree.

    ``rcon_manager`` is the web app's persistent connection: when it is
    already logged in we ask *it* rather than opening a second connection per
    poll. A live manager is also proof the server is up, so the stopped check
    below never runs in the normal case.
    """
    from app.rcon.rcon_ops import check_rcon_setup, get_rcon_players, is_rcon_configured

    if not is_rcon_configured(config):
        return OnlineStatus(
            players=[],
            available=False,
            reason=REASON_NOT_CONFIGURED,
            detail="RCon is not configured — set the password and port in Configs -> BattlEye.",
            setup_problems=check_rcon_setup(config),
        )

    connected = (
        rcon_manager is not None
        and hasattr(rcon_manager, "is_connected")
        and rcon_manager.is_connected()
    )
    if connected:
        try:
            return OnlineStatus(
                players=await rcon_manager.fetch_players(),
                available=True,
                server_running=True,
            )
        except Exception:  # RconError, or anything berconpy raises
            pass  # fall through and diagnose like any other failure

    from app.server.service_control import is_server_running

    running = await asyncio.to_thread(is_server_running, config)
    if not running:
        return OnlineStatus(
            players=[],
            available=False,
            reason=REASON_SERVER_STOPPED,
            detail="The server is stopped — the live player list needs it running.",
            server_running=False,
        )

    try:
        players = await get_rcon_players(config)
    except Exception as exc:  # RconError, or anything berconpy raises
        return OnlineStatus(
            players=[],
            available=False,
            reason=REASON_RCON_UNREACHABLE,
            detail=f"RCon did not answer: {exc}",
            server_running=True,
            setup_problems=check_rcon_setup(config),
        )
    return OnlineStatus(players=players, available=True, server_running=True)


async def get_online_players(
    config: DayzConfig,
    rcon_manager: object | None = None,
) -> list[OnlinePlayer] | None:
    """Online players, or None when RCON can't answer (any reason).

    Thin wrapper over :func:`fetch_online` for callers that only want the
    list; use ``fetch_online`` when the reason has to be shown.
    """
    status = await fetch_online(config, rcon_manager)
    if not status.available:
        return None
    return [
        OnlinePlayer(guid=p.guid, name=p.name, ping=p.ping, lobby=p.lobby)
        for p in status.players
    ]


# ---------------------------------------------------------------------------
# Session events from tracker JSONL
# ---------------------------------------------------------------------------

def parse_session_events(config: DayzConfig, max_days: int = 30) -> list[dict]:
    """Read session events from the session tracker JSONL file."""
    from app.monitoring.session_tracker import read_session_events
    return read_session_events(max_days, path=config.paths.session_log)


def _parse_timestamp(entry: dict) -> datetime | None:
    """Parse timestamp from session event."""
    ts = entry.get("timestamp", "")
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        pass
    # Fallback: try ISO format
    try:
        return datetime.fromisoformat(ts.split("+")[0])
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Session computation
# ---------------------------------------------------------------------------

# A session that no disconnect ever closed counts for at most this long. The
# only thing that closes it is the player's next connect, which may be days
# later: one such pair read as a 101-hour session (test pass 1, F44). Since
# plans/53 C the RCon manager writes the missing disconnects itself, so this
# is for history written before that, and for a zsm that was killed outright.
MAX_UNCLOSED = timedelta(hours=6)


def compute_sessions(events: list[dict]) -> list[PlayerSession]:
    """Pair connect/disconnect events per GUID into sessions.

    Unpaired connects = ongoing sessions (disconnect_time=None). A session
    closed by the player's next connect is capped at ``MAX_UNCLOSED``.
    """
    open_sessions: dict[str, PlayerSession] = {}
    completed: list[PlayerSession] = []

    for entry in events:
        event_type = entry.get("event", "")
        guid = entry.get("guid", "")
        if not guid:
            continue

        name = entry.get("name", "")
        ts = _parse_timestamp(entry)
        if ts is None:
            continue

        if event_type == "connect":
            # Close any existing open session for this player
            if guid in open_sessions:
                old = open_sessions.pop(guid)
                limit = old.connect_time + MAX_UNCLOSED
                old.disconnect_time = min(ts, limit)
                old.capped = ts > limit
                completed.append(old)
            open_sessions[guid] = PlayerSession(
                guid=guid,
                name=name or guid,
                connect_time=ts,
            )

        elif event_type == "disconnect":
            if guid in open_sessions:
                session = open_sessions.pop(guid)
                session.disconnect_time = ts
                if name:
                    session.name = name
                completed.append(session)
            else:
                # Disconnect without connect — partial session
                completed.append(PlayerSession(
                    guid=guid,
                    name=name or guid,
                    connect_time=ts,
                    disconnect_time=ts,
                ))

    # Remaining open sessions are still connected
    completed.extend(open_sessions.values())

    return sorted(completed, key=lambda s: s.connect_time)


# ---------------------------------------------------------------------------
# Player summaries
# ---------------------------------------------------------------------------

def compute_player_summaries(
    sessions: list[PlayerSession],
    online_guids: set[str] | None = None,
) -> list[PlayerSummary]:
    """Aggregate sessions per player into summaries.

    Sort by last_seen desc.
    """
    if online_guids is None:
        online_guids = set()

    per_player: dict[str, list[PlayerSession]] = {}
    for s in sessions:
        per_player.setdefault(s.guid, []).append(s)

    summaries: list[PlayerSummary] = []
    for guid, player_sessions in per_player.items():
        total_playtime = 0
        capped = 0
        first_seen = player_sessions[0].connect_time
        last_seen = player_sessions[0].connect_time
        best_name = guid

        for s in player_sessions:
            if s.duration_seconds is not None:
                total_playtime += s.duration_seconds
            capped += s.capped
            if s.name and s.name != guid:
                best_name = s.name
            if s.connect_time < first_seen:
                first_seen = s.connect_time
            last = s.disconnect_time or s.connect_time
            if last > last_seen:
                last_seen = last

        summaries.append(PlayerSummary(
            guid=guid,
            name=best_name,
            total_sessions=len(player_sessions),
            total_playtime_seconds=total_playtime,
            first_seen=first_seen,
            last_seen=last_seen,
            is_online=guid in online_guids,
            capped_sessions=capped,
        ))

    summaries.sort(key=lambda s: s.last_seen, reverse=True)
    return summaries


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def format_duration(seconds: int) -> str:
    """Format seconds into 'Xh YYm' or 'Ym'."""
    if seconds < 60:
        return "<1m"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"
