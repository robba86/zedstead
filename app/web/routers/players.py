"""GET /api/players/online, POST /api/players/{kick,ban,message},
GET /api/players/{sessions,summaries}, GET/POST/DELETE /api/bans
(+ /api/bans/rcon), GET/POST/DELETE /api/priority, GET/POST/DELETE
/api/whitelist, GET /api/players/stats, GET/POST/DELETE
/api/players/watchlist, POST /api/rcon/command, GET /api/rcon/history,
GET /api/chat."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from app.ops import ban_ops, player_ops, priority_ops, whitelist_ops
from app.rcon import rcon_ops
from app.web.deps import get_state, require_session
from app.web.schemas import (
    BanAddRequest,
    PlayerBanRequest,
    PlayerKickRequest,
    PlayerMessageRequest,
    PriorityAddRequest,
    RconCommandRequest,
    WatchlistAddRequest,
    WhitelistAddRequest,
)
from app.web.state import WebState
from app.web.users import role_at_least

router = APIRouter(prefix="/api", tags=["players"], dependencies=[Depends(require_session)])


# A player's IP address is personal data, and a viewer is the role for
# someone who only watches. Moderators and admins keep it: a ban decision can
# need it. Every route here that would hand a viewer one goes through this
# (`/players/online`, `/bans`; `/bans/export` is moderator in permissions.py).
def shows_player_ips(request: Request) -> bool:
    """Whether this request may see player IP addresses. No role on the
    request reads as no, like `redact.is_admin`."""
    return role_at_least(getattr(request.state, "role", "") or "", "moderator")


def _hide_ip_bans(bans: list[dict]) -> list[dict]:
    """*bans* with each IP ban's address replaced. The placeholder is numbered
    so it can still key a row, and the ban, its reason and its expiry stay."""
    n = 0
    out = []
    for ban in bans:
        if ban.get("ban_type") == "ip":
            n += 1
            ban = {**ban, "identifier": f"IP address hidden #{n}"}
            ban["display_id"] = ban["identifier"]
        out.append(ban)
    return out

_RCON_BAN_LINE_RE = re.compile(r"^#(\d+)\s+(\S+)\s*(.*)$")


def _ban_to_dict(b: ban_ops.BanEntry) -> dict:
    return {
        "identifier": b.identifier,
        "ban_type": b.ban_type,
        "expiry": b.expiry,
        "reason": b.reason,
        "added_at": b.added_at,
        "added_by": b.added_by,
        "player_name": b.player_name,
        "notes": b.notes,
        "is_permanent": b.is_permanent,
        "is_expired": b.is_expired,
        "is_active": b.is_active,
        "expires_at": b.expires_at.isoformat() if b.expires_at else None,
        "time_remaining": b.time_remaining,
        "display_id": b.display_id,
    }


# ---------------------------------------------------------------------------
# Online players / RCON actions
# ---------------------------------------------------------------------------


@router.get("/players/online")
async def online_players(request: Request, state: WebState = Depends(get_state)) -> dict:
    """Live players, or a 200 saying why there are none.

    Only two of the three no-data cases are errors. A *stopped server* is the
    ordinary one — probing it used to hang until the client gave up, and then
    answering 502 made the dashboard tile hide itself with no explanation — so
    it comes back 200 with ``available: false`` and a ``reason`` the UI can
    phrase.

    A viewer's rows carry no ``ip`` key (`shows_player_ips`).
    """
    status = await player_ops.fetch_online(state.config, state.rcon_manager)
    if status.reason == player_ops.REASON_NOT_CONFIGURED:
        raise HTTPException(status_code=409, detail="Set the RCon password and port in Configs -> BattlEye")
    if status.reason == player_ops.REASON_RCON_UNREACHABLE:
        raise HTTPException(
            status_code=502, detail=" ".join([status.detail, *status.setup_problems])
        )
    players = [asdict(p) for p in status.players]
    if not shows_player_ips(request):
        for row in players:
            row.pop("ip", None)
    return {
        "players": players,
        "available": status.available,
        "reason": status.reason,
        "detail": status.detail,
        "server_running": status.server_running,
    }


def _rcon_http_error(e: rcon_ops.RconError) -> HTTPException:
    """409 when the server is simply stopped (the request was fine, the state
    is wrong), 502 when RCon itself failed."""
    status = 409 if isinstance(e, rcon_ops.ServerStopped) else 502
    return HTTPException(status_code=status, detail=str(e))


@router.post("/players/kick")
async def kick_player(body: PlayerKickRequest, state: WebState = Depends(get_state)) -> dict:
    try:
        response = await rcon_ops.kick_player(state.config, body.player_id, body.reason)
    except rcon_ops.RconError as e:
        raise _rcon_http_error(e)
    return {"response": response}


@router.post("/players/ban")
async def ban_player(body: PlayerBanRequest, state: WebState = Depends(get_state)) -> dict:
    try:
        ban_ops.check_ban_input(body.reason, body.duration_minutes)
    except ban_ops.BanInputError as e:
        raise HTTPException(status_code=422, detail=str(e))
    try:
        response = await rcon_ops.ban_player(
            state.config, body.player_id, body.duration_minutes, body.reason
        )
    except rcon_ops.RconError as e:
        raise _rcon_http_error(e)
    return {"response": response}


@router.post("/players/message")
async def message_players(body: PlayerMessageRequest, state: WebState = Depends(get_state)) -> dict:
    try:
        response = await rcon_ops.send_global_message(state.config, body.message)
    except rcon_ops.RconError as e:
        raise _rcon_http_error(e)
    return {"response": response}


# ---------------------------------------------------------------------------
# Sessions / summaries
# ---------------------------------------------------------------------------


@router.get("/players/sessions")
async def get_sessions(days: int = 30, state: WebState = Depends(get_state)) -> dict:
    def _compute() -> list[player_ops.PlayerSession]:
        events = player_ops.parse_session_events(state.config, max_days=days)
        return player_ops.compute_sessions(events)

    sessions = await asyncio.to_thread(_compute)
    return {
        "sessions": [
            {
                "guid": s.guid,
                "name": s.name,
                "connect_time": s.connect_time.isoformat(),
                "disconnect_time": s.disconnect_time.isoformat() if s.disconnect_time else None,
                "duration_seconds": s.duration_seconds,
                "capped": s.capped,
            }
            for s in sessions
        ]
    }


@router.get("/players/summaries")
async def get_summaries(days: int = 30, state: WebState = Depends(get_state)) -> dict:
    config = state.config

    def _compute_sessions() -> list[player_ops.PlayerSession]:
        events = player_ops.parse_session_events(config, max_days=days)
        return player_ops.compute_sessions(events)

    sessions = await asyncio.to_thread(_compute_sessions)

    # Same availability path as /players/online: without it a stopped server
    # cost this endpoint berconpy's full retry budget on every load.
    status = await player_ops.fetch_online(config, state.rcon_manager)
    online_guids = {p.guid for p in status.players} if status.available else None

    summaries = player_ops.compute_player_summaries(sessions, online_guids)
    return {
        "summaries": [
            {
                "guid": s.guid,
                "name": s.name,
                "total_sessions": s.total_sessions,
                "total_playtime_seconds": s.total_playtime_seconds,
                "first_seen": s.first_seen.isoformat(),
                "last_seen": s.last_seen.isoformat(),
                "is_online": s.is_online,
                "capped_sessions": s.capped_sessions,
            }
            for s in summaries
        ]
    }


# ---------------------------------------------------------------------------
# Bans (file-based bans.txt)
# ---------------------------------------------------------------------------


@router.get("/bans")
async def list_bans(request: Request, state: WebState = Depends(get_state)) -> dict:
    config = state.config

    def _load() -> tuple[list[ban_ops.BanEntry], dict]:
        bans = ban_ops.read_bans(config.battleye_dir / "bans.txt")
        server_banlist = {
            "path": ban_ops.SERVER_BANLIST_FILENAME,
            "ids": ban_ops.read_server_banlist(ban_ops.server_banlist_path(config)),
            "disabled": ban_ops.server_banlist_disabled(config),
        }
        return ban_ops.enrich_with_metadata(bans, config.paths.ban_metadata), server_banlist

    bans, server_banlist = await asyncio.to_thread(_load)
    stats = ban_ops.get_ban_stats(bans)
    # ban.txt stays out of `bans`/`stats`: it has no reason or expiry, and
    # the tab's remove/edit actions do not apply to it.
    rows = [_ban_to_dict(b) for b in bans]
    return {
        "bans": rows if shows_player_ips(request) else _hide_ip_bans(rows),
        "stats": asdict(stats),
        "server_banlist": server_banlist,
    }


@router.post("/bans/import")
async def import_bans(body: dict, state: WebState = Depends(get_state)) -> dict:
    """Import bans from pasted/uploaded bans.txt content (duplicates skipped)."""
    content = str(body.get("content", ""))
    if not content.strip():
        raise HTTPException(status_code=422, detail="No ban lines provided.")
    config = state.config

    def _import() -> ban_ops.ImportResult:
        import tempfile

        with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(content)
            source = Path(tmp.name)
        try:
            return ban_ops.import_bans(config.battleye_dir / "bans.txt", source)
        finally:
            source.unlink(missing_ok=True)

    result = await asyncio.to_thread(_import)
    await state.hub.broadcast({"type": "bans_changed"})
    return {**asdict(result), "loaded": bool(result.added) and await _reload_bans(state)}


@router.get("/bans/export")
async def export_bans(state: WebState = Depends(get_state)):
    """Download the current bans.txt."""
    from fastapi.responses import PlainTextResponse

    bans_path = state.config.battleye_dir / "bans.txt"
    # The file's own bytes: a download is the file, whatever it is saved as.
    content = await asyncio.to_thread(
        lambda: bans_path.read_bytes() if bans_path.is_file() else b""
    )
    return PlainTextResponse(
        content,
        headers={"Content-Disposition": 'attachment; filename="bans.txt"'},
    )


@router.post("/bans/cleanup-expired")
async def cleanup_expired_bans(state: WebState = Depends(get_state)) -> dict:
    removed = await asyncio.to_thread(
        ban_ops.cleanup_expired, state.config.battleye_dir / "bans.txt"
    )
    if removed:
        await state.hub.broadcast({"type": "bans_changed"})
    return {"removed": removed, "loaded": bool(removed) and await _reload_bans(state)}


async def _reload_bans(state: WebState) -> bool:
    """Tell the running server to re-read bans.txt.

    BattlEye reads the file only at start and on ``loadBans``, so a ban
    written here did nothing until the next restart. Only through the
    panel's own RCon connection when it is logged in: opening a one-shot
    connection could hold the request for its 20s connect deadline. False
    means the change applies at the next start.
    """
    manager = state.rcon_manager
    if manager is None or not manager.is_connected():
        return False
    try:
        await manager.send_command("loadBans")
    except Exception:  # noqa: BLE001 - the file is written; the reload is a bonus
        return False
    return True


@router.post("/bans")
async def add_ban(
    body: BanAddRequest, request: Request, state: WebState = Depends(get_state)
) -> dict:
    config = state.config
    # The account `require_session` stashed, as logout records it: never the
    # body, so nobody can write another person's name on a ban.
    added_by = getattr(request.state, "username", "") or ""
    if not ban_ops.is_valid_ban_identifier(body.identifier):
        raise HTTPException(status_code=422, detail=f"Not a valid GUID or IP: {body.identifier!r}")

    def _add() -> ban_ops.BanEntry:
        return ban_ops.add_ban(
            config.battleye_dir / "bans.txt",
            body.identifier,
            reason=body.reason,
            duration_minutes=body.duration_minutes,
            added_by=added_by,
            metadata_path=config.paths.ban_metadata,
        )

    try:
        entry = await asyncio.to_thread(_add)
    except ban_ops.BanInputError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ValueError as e:  # already banned
        raise HTTPException(status_code=409, detail=str(e))

    await state.hub.broadcast({"type": "bans_changed"})
    return {**_ban_to_dict(entry), "loaded": await _reload_bans(state)}


@router.delete("/bans")
async def delete_ban(identifier: str, state: WebState = Depends(get_state)) -> dict:
    config = state.config
    removed = await asyncio.to_thread(ban_ops.remove_ban, config.battleye_dir / "bans.txt", identifier)
    if not removed:
        raise HTTPException(status_code=404, detail=f"No ban found for {identifier!r}")
    await state.hub.broadcast({"type": "bans_changed"})
    return {"ok": True, "loaded": await _reload_bans(state)}


@router.get("/bans/rcon")
async def list_rcon_bans(state: WebState = Depends(get_state)) -> dict:
    try:
        text = await rcon_ops.get_bans(state.config)
    except rcon_ops.RconError as e:
        raise _rcon_http_error(e)

    bans: list[dict] = []
    # "\n" only: get_bans joins berconpy's rows with it, and a reason may hold
    # a character str.splitlines also breaks at (U+2028 and the like), which
    # would read as a row of its own (plans/68 LB-3).
    for line in text.split("\n"):
        match = _RCON_BAN_LINE_RE.match(line.strip())
        if match:
            index, duration, reason = match.groups()
            bans.append({"index": int(index), "duration": duration, "reason": reason})
    return {"bans": bans}


@router.delete("/bans/rcon/{index}")
async def delete_rcon_ban(index: int, state: WebState = Depends(get_state)) -> dict:
    try:
        response = await rcon_ops.remove_ban(state.config, index)
    except rcon_ops.RconError as e:
        raise _rcon_http_error(e)
    await state.hub.broadcast({"type": "bans_changed"})
    return {"response": response}


# ---------------------------------------------------------------------------
# Priority list
# ---------------------------------------------------------------------------


def _priority_path(config) -> Path:
    return config.server_dir_path / "priority.txt"


@router.get("/priority")
async def list_priority(state: WebState = Depends(get_state)) -> dict:
    ids = await asyncio.to_thread(priority_ops.read_priority_list, _priority_path(state.config))
    return {"ids": ids}


@router.post("/priority")
async def add_priority(body: PriorityAddRequest, state: WebState = Depends(get_state)) -> dict:
    path = _priority_path(state.config)
    try:
        added = await asyncio.to_thread(priority_ops.add_priority_id, path, body.steam_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    await state.hub.broadcast({"type": "bans_changed"})
    return {"added": added}


@router.delete("/priority")
async def remove_priority(steam_id: str, state: WebState = Depends(get_state)) -> dict:
    path = _priority_path(state.config)
    removed = await asyncio.to_thread(priority_ops.remove_priority_id, path, steam_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"No priority entry for {steam_id!r}")
    await state.hub.broadcast({"type": "bans_changed"})
    return {"ok": True}


# ---------------------------------------------------------------------------
# Whitelist
#
# The pair that matters is (enableWhitelist, the file): either one alone is
# harmless and the combination "on, and empty" locks every player out — which
# was reachable from the serverDZ.cfg editor before this existed. So the list
# endpoint reports the switch alongside the IDs rather than leaving the page to
# fetch it separately and maybe not bother.
# ---------------------------------------------------------------------------


def _whitelist_enabled(config) -> bool:
    from app.parsers.servercfg import read_cfg_values

    raw = read_cfg_values(config.server_cfg_path, ["enableWhitelist"]).get("enableWhitelist")
    return str(raw).strip().lower() in {"1", "true"}


def _read_whitelist(config) -> dict:
    path = whitelist_ops.whitelist_path(config)
    return {
        "ids": whitelist_ops.read_whitelist(path),
        "comments": whitelist_ops.whitelist_comments(path),
        "enabled": _whitelist_enabled(config),
        "path": str(path),
    }


@router.get("/whitelist")
async def list_whitelist(state: WebState = Depends(get_state)) -> dict:
    return await asyncio.to_thread(_read_whitelist, state.config)


@router.post("/whitelist")
async def add_whitelist(
    body: WhitelistAddRequest, state: WebState = Depends(get_state)
) -> dict:
    path = whitelist_ops.whitelist_path(state.config)
    try:
        added = await asyncio.to_thread(whitelist_ops.add_whitelist_id, path, body.steam_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    await state.hub.broadcast({"type": "bans_changed"})
    return {"added": added}


@router.delete("/whitelist")
async def remove_whitelist(steam_id: str, state: WebState = Depends(get_state)) -> dict:
    path = whitelist_ops.whitelist_path(state.config)
    removed = await asyncio.to_thread(whitelist_ops.remove_whitelist_id, path, steam_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"No whitelist entry for {steam_id!r}")
    await state.hub.broadcast({"type": "bans_changed"})
    return {"ok": True}


# ---------------------------------------------------------------------------
# Stats — kill feed, K/D, playtime leaderboard, watchlist (TODO D3)
# ---------------------------------------------------------------------------


@router.get("/players/stats")
async def get_player_stats(days: int = 30, state: WebState = Depends(get_state)) -> dict:
    """The .ADM's combat events joined to the session tracker's playtime.

    Read-only and derived: no new file, no new state. Delete the .ADM and the
    numbers go back to zero, which is right for a statistic whose whole source
    is a log the admin is free to rotate.
    """
    from app.ops import player_stats

    days = max(1, min(days, 365))
    stats = await asyncio.to_thread(player_stats.build_player_stats, state.config, days)
    return {
        "killfeed": stats.killfeed,
        "leaderboard": [
            {**asdict(row), "kd": row.kd} for row in stats.leaderboard
        ],
        "totals": stats.totals,
        "adm_found": stats.adm_found,
        "adm_file": stats.adm_file,
        "days": days,
    }


@router.get("/players/watchlist")
async def get_watchlist(state: WebState = Depends(get_state)) -> dict:
    from app.ops import player_stats

    return {"entries": player_stats.normalise_watchlist(state.config.player_watchlist)}


@router.post("/players/watchlist")
async def add_to_watchlist(
    body: WatchlistAddRequest, state: WebState = Depends(get_state)
) -> dict:
    """Add a name or a UID. Both, because an admin adds whichever they have."""
    from app.ops import player_stats

    entry = body.entry.strip()
    if not entry:
        raise HTTPException(status_code=400, detail="Enter a player name or UID.")

    def _add(cfg) -> None:
        cfg.player_watchlist = player_stats.normalise_watchlist(
            [*cfg.player_watchlist, entry]
        )

    config = await state.mutate_config(_add)
    return {"entries": config.player_watchlist}


@router.delete("/players/watchlist")
async def remove_from_watchlist(entry: str, state: WebState = Depends(get_state)) -> dict:
    from app.ops import player_stats

    current = player_stats.normalise_watchlist(state.config.player_watchlist)
    if entry not in current:
        raise HTTPException(status_code=404, detail=f"{entry!r} is not on the watchlist.")

    def _remove(cfg) -> None:
        cfg.player_watchlist = [e for e in cfg.player_watchlist if e != entry]

    config = await state.mutate_config(_remove)
    return {"entries": config.player_watchlist}


# ---------------------------------------------------------------------------
# Raw RCon console (TODO D1)
#
# A free-text console is not a new capability: the panel can already kick, ban,
# broadcast, stop and restart. What it adds is reach — every BattlEye command
# the server understands, including the ones zsm has no button for and the
# ones a mod adds, which is precisely why a fixed button list can never cover
# it.
# ---------------------------------------------------------------------------

# Bounded because it is memory, and unbounded memory behind a text box that
# invites repetition is a slow leak. A console is worth the last few commands.
RCON_HISTORY_LIMIT = 50


@router.post("/rcon/command")
async def run_rcon_command(
    body: RconCommandRequest, state: WebState = Depends(get_state)
) -> dict:
    """Send one raw command and return what the server said.

    Goes through `rcon_ops.rcon_send` like kick/ban/message do, rather than the
    persistent `RconManager`: same path, same hardening (`CONNECT_TIMEOUT`
    bounds the login, `is_foreign_cancel` tells a dropped connection from a
    real cancel), and the manager's own connection is left to the player poll.
    """
    command = body.command.strip()
    if not command:
        raise HTTPException(status_code=400, detail="Type a command first.")

    entry = {
        "command": command,
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ok": True,
        "response": "",
    }
    try:
        entry["response"] = await rcon_ops.rcon_send(state.config, command)
    except rcon_ops.RconError as e:
        # Kept in the history rather than only raised: "what did I send and
        # what happened" is the whole point, and a failure is an answer.
        entry["ok"] = False
        entry["response"] = str(e)
        _remember_rcon(state, entry)
        raise _rcon_http_error(e)

    _remember_rcon(state, entry)
    return entry


def _remember_rcon(state: WebState, entry: dict) -> None:
    state.rcon_history.append(entry)
    del state.rcon_history[:-RCON_HISTORY_LIMIT]


@router.get("/rcon/history")
async def get_rcon_history(state: WebState = Depends(get_state)) -> dict:
    """What this `zsm web` has sent since it started, oldest first.

    In memory on purpose: a console's value is the last few commands and their
    replies, and a reply is meaningless once the server has restarted. It is
    deliberately **not** folded into the access log, which answers a different
    question (who reached the panel) and is kept on disk for that reason.
    """
    return {"entries": list(state.rcon_history), "limit": RCON_HISTORY_LIMIT}


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


@router.get("/chat")
async def get_chat(hours: int = 24, q: str | None = None, state: WebState = Depends(get_state)) -> dict:
    from app.monitoring import chat_logger

    path = state.config.paths.chat_log
    if q:
        events = await asyncio.to_thread(chat_logger.search_chat, q, hours, path=path)
    else:
        events = await asyncio.to_thread(chat_logger.read_chat_events, hours, path=path)
    return {"events": events}
