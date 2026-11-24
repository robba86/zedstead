"""RCON business logic — BattlEye RCon commands via berconpy."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, AsyncIterator

import berconpy

if TYPE_CHECKING:
    from app.shared.config import DayzConfig


class RconError(Exception):
    """Raised when an RCON operation fails."""


class ServerStopped(RconError):
    """The server is not running on this machine, so no RCon can answer.

    Raised before any connect attempt: against a closed port the login takes
    ``CONNECT_TIMEOUT`` to fail, which made every panel action wait 20s on a
    stopped server (test pass 2, N5). A subclass, so a caller that catches
    ``RconError`` still does.
    """


#: What an RCon action on a stopped (local) server says.
SERVER_STOPPED = "The server is stopped — start it first."


#: What a command that was in flight when the connection died should say.
CONNECTION_LOST = "RCON error: connection closed while the command was in flight"


def is_foreign_cancel() -> bool:
    """True when the ``CancelledError`` just caught was not aimed at us.

    berconpy cancels every pending command future when its connection drops
    (``AsyncCommander.reset``), and ``send_command`` awaits that future through
    ``asyncio.shield`` — cancelling the inner future cancels the shield, so the
    drop surfaces in *our* coroutine as a ``CancelledError``.  It is a
    ``BaseException``, so every ``except Exception`` in the call chain misses it
    and it escaped all the way into the web request as a 500 (measured on
    ``/api/players/summaries`` when the server was stopped).  It means nothing
    more than "the connection went away mid-command", so callers turn it into
    :class:`RconError` — see ``CONNECTION_LOST``.

    A *genuine* cancellation (the caller's task was cancelled — client
    disconnect, shutdown) must still propagate, so the two are told apart the
    way ``asyncio.timeouts`` does: only a ``Task.cancel()`` aimed at *this* task
    raises its ``cancelling()`` counter.  That counter is 3.11+, which is why
    the package requires 3.11: on 3.10 there is nothing to tell them apart
    with, so every cancellation counts as genuine and is re-raised.
    """
    task = asyncio.current_task()
    cancelling = getattr(task, "cancelling", None)
    return cancelling is not None and cancelling() == 0


@dataclass
class RconPlayer:
    index: int
    ip: str
    port: int
    ping: int
    guid: str
    name: str
    lobby: bool


#: Hosts that mean "the server on this machine".
LOCAL_HOSTS = frozenset({"", "127.0.0.1", "localhost", "::1"})


def rcon_host(config: DayzConfig) -> str:
    """The host to dial. Empty config value falls back to loopback."""
    return (config.rcon_host or "").strip() or "127.0.0.1"


def is_remote_rcon(config: DayzConfig) -> bool:
    """True when RCon points somewhere other than this machine (TODO G1b).

    What it gates is everything that reads *local* files to explain an RCon
    problem: ``serverDZ.cfg``'s ``BattlEye`` line, ``beserver_x64.cfg``'s
    password and port. Against a rented server those files belong to a
    different machine — usually a different *install* — so checking them
    reports problems that are not the user's and misses the one that is.
    """
    return rcon_host(config).lower() not in LOCAL_HOSTS


def is_rcon_configured(config: DayzConfig) -> bool:
    """Check if RCON password and port are set."""
    return bool(config.rcon_password) and config.rcon_port > 0


def rcon_port_number(value: object) -> int | None:
    """*value* as an RCon port (1-65535), or ``None`` when it is not one."""
    text = str(value).strip() if value is not None else ""
    if not text.isdigit():
        return None
    port = int(text)
    return port if 1 <= port <= 65535 else None


def adopt_beserver_rcon(config: DayzConfig, be_data: dict[str, str]) -> None:
    """Point zsm's own RCon client at the password and port the server reads.

    The server takes ``RConPassword``/``RConPort`` from beserver_x64.cfg;
    zsm dials with ``config.rcon_password``/``rcon_port``. Outside Docker
    (where ``container.sync_battleye_cfg`` copies the other way, from
    ``RCON_PASSWORD``) nothing else sets that pair, so the BattlEye editor
    calls this with the values it has just written, and the two agree the way
    ``check_rcon_setup`` checks. Changes *config* in place; the caller saves it.

    Only keys present in *be_data* are copied, and a ``RConPort`` that is not a
    usable port leaves ``rcon_port`` alone. **A no-op when the host is
    remote** (``is_remote_rcon``): this machine's file belongs to a different
    install then, and copying it would overwrite the rented server's password.
    """
    if is_remote_rcon(config):
        return
    if "RConPassword" in be_data:
        config.rcon_password = be_data["RConPassword"]
    port = rcon_port_number(be_data.get("RConPort"))
    if port is not None:
        config.rcon_port = port


def check_rcon_setup(config: DayzConfig) -> list[str]:
    """Reasons the server would refuse zsm's RCON login.

    ``is_rcon_configured`` only says zsm has a password and a port; the
    *server* reads its own files, so the two halves have to agree or the
    connection fails with nothing on screen to explain it:

    * ``BattlEye`` off in serverDZ.cfg — the RCon listener never starts;
    * ``RConPassword``/``RConPort`` in beserver_x64.cfg pointing somewhere else.

    Returns plain sentences (no Rich markup — the web API and the CLI both
    print them). Empty list = nothing detectably wrong; a missing
    beserver_x64.cfg is not reported here because BattlEye writes it on first
    start, so its absence before the first run is normal.

    **Only the first two checks run against a remote ``rcon_host``** — the
    other two read this machine's server files, which say nothing about the
    server actually being dialled.
    """
    from app.parsers.battleye_cfg import live_beserver_cfg, read_beserver_cfg
    from app.parsers.servercfg import read_cfg_value

    problems: list[str] = []
    if not config.rcon_password:
        # Saving Configs -> BattlEye is what sets it (adopt_beserver_rcon),
        # except for a remote host, whose password that file does not hold.
        if is_remote_rcon(config):
            problems.append(
                "No RCon password set in Zedstead — for a remote host it is "
                "rcon_password in config.json."
            )
        else:
            problems.append("No RCon password set in Zedstead — set one in Configs -> BattlEye and save.")
    if config.rcon_port <= 0:
        problems.append(f"RCon port {config.rcon_port} is not a usable port.")

    if is_remote_rcon(config):
        return problems

    battleye = read_cfg_value(config.server_cfg_path, "BattlEye")
    if battleye is not None and battleye.strip().strip('"') in ("0", "false"):
        problems.append("BattlEye is disabled in serverDZ.cfg — RCon needs it on.")

    # While the server runs, BattlEye's active copy is the only one there and
    # is what the login is checked against; the sentence names the cfg.
    be_path = live_beserver_cfg(config)
    be_name = config.beserver_cfg_path.name
    if be_path.is_file():
        be_data = read_beserver_cfg(be_path)
        be_password = be_data.get("RConPassword", "")
        if config.rcon_password and be_password and be_password != config.rcon_password:
            problems.append(
                f"RConPassword in {be_name} does not match Zedstead's RCon password."
            )
        be_port = be_data.get("RConPort", "")
        if be_port.isdigit() and int(be_port) != config.rcon_port:
            problems.append(
                f"RConPort in {be_name} is {be_port}, Zedstead connects to {config.rcon_port}."
            )
    return problems


def local_rcon_bind(config: DayzConfig) -> str | None:
    """Where this machine's BattlEye binds RCon, by its own cfg (plans/68 NET-14).

    ``""`` when ``RConIP`` is unset or ``0.0.0.0``: every interface, which is
    what a running server was measured to do (app/ops/CLAUDE.md, "RCon
    exposure"). The address when it is set. ``None`` when there
    is nothing to say: a remote ``rcon_host`` (these files are not that
    server's), BattlEye off in serverDZ.cfg, no BattlEye cfg yet, or one
    without an ``RConPassword``, which is not the measured case.
    """
    from app.parsers.battleye_cfg import live_beserver_cfg, read_beserver_cfg
    from app.parsers.servercfg import read_cfg_value

    if is_remote_rcon(config):
        return None
    try:
        battleye = read_cfg_value(config.server_cfg_path, "BattlEye")
        be_path = live_beserver_cfg(config)
        be_data = read_beserver_cfg(be_path) if be_path.is_file() else {}
    except (OSError, UnicodeDecodeError):
        return None
    if battleye is not None and battleye.strip().strip('"') in ("0", "false"):
        return None
    if not be_data.get("RConPassword", "").strip():
        return None
    address = be_data.get("RConIP", "").strip()
    return "" if address in ("", "0.0.0.0") else address


# Ceiling on a login attempt. berconpy bounds only its *first* three attempts
# (3 x 3s plus exponential backoff); after those it reconnects forever while
# `wait_for_login()` never resolves, so a call against a closed RCon port hangs
# indefinitely instead of raising LoginTimeout — measured still waiting after
# 10 minutes. Every caller here is behind a web request or a CLI command, so it
# needs an answer, and "no answer in 20s" is one.
CONNECT_TIMEOUT = 20.0


@asynccontextmanager
async def _connect(config: DayzConfig) -> AsyncIterator[berconpy.ArmaClient]:
    """Connect to RCON, yield client, auto-close.

    The login is entered explicitly (rather than with a plain ``async with``)
    so ``CONNECT_TIMEOUT`` covers *only* that phase — a long-running command
    inside the body must not be cut off by the connect deadline.
    """
    if not is_rcon_configured(config):
        raise RconError("RCON not configured. Set up BattlEye first.")

    # This machine's unit says nothing about a server on another host.
    if not is_remote_rcon(config):
        from app.server.service_control import is_server_running

        if not await asyncio.to_thread(is_server_running, config):
            raise ServerStopped(SERVER_STOPPED)

    from app.rcon.rcon_manager import _quiet_unreachable_errors

    # A closed RCON port makes berconpy log an ERROR traceback per retry; the
    # RconError below is the message the caller should see instead.
    _quiet_unreachable_errors()

    client = berconpy.ArmaClient()
    # A one-shot client asks for what it needs itself. berconpy's cache would
    # also send "players" on login, after waiting up to 10s for admin_login —
    # by then this connection is usually closed, and the RuntimeError it gets
    # was printed as an unretrieved task exception once per command (seen in
    # the plans/53 container check). The persistent RconManager keeps it.
    client.dispatch.on_login.remove(client.cache.on_login)
    session = client.connect(rcon_host(config), config.rcon_port, config.rcon_password)
    try:
        await asyncio.wait_for(session.__aenter__(), CONNECT_TIMEOUT)
    except asyncio.TimeoutError:
        client.close()
        raise RconError(
            f"RCON did not answer within {CONNECT_TIMEOUT:.0f}s. Is the server "
            "running with BattlEye, and is the RCon port correct?"
        ) from None
    except berconpy.LoginTimeout:
        client.close()
        raise RconError(
            "RCON connection timed out. Is the server running with BattlEye?"
        )
    except berconpy.LoginFailure as exc:
        client.close()
        raise RconError(f"RCON login failed: {exc}") from exc
    except Exception as exc:
        client.close()
        raise RconError(f"RCON error: {exc}") from exc

    try:
        yield client
    except RconError:
        raise
    except asyncio.CancelledError:
        if not is_foreign_cancel():
            raise
        raise RconError(CONNECTION_LOST) from None
    except Exception as exc:
        raise RconError(f"RCON error: {exc}") from exc
    finally:
        # Closes the socket and awaits berconpy's protocol task; a failure
        # while tearing down must not mask the caller's own result.
        with suppress(Exception):
            await session.__aexit__(None, None, None)


async def rcon_send(config: DayzConfig, command: str) -> str:
    """Connect, send a command, disconnect. Returns the response string."""
    async with _connect(config) as client:
        return await client.send_command(command)


async def get_rcon_players(config: DayzConfig) -> list[RconPlayer]:
    """Fetch the player list via RCON."""
    async with _connect(config) as client:
        raw_response = await client.send_command("players")
        client.cache.update_players(raw_response)
        players_raw = client.players

    result: list[RconPlayer] = []
    for p in players_raw:
        result.append(
            RconPlayer(
                index=p.id,
                ip=p.ip or "",
                port=0,
                ping=p.ping or 0,
                guid=p.guid or "",
                name=p.name or "",
                lobby=bool(p.in_lobby),
            )
        )
    if result:
        return result

    # Fallback: berconpy's regex may not match DayZ RCON format
    from app.rcon.rcon_manager import _parse_players_fallback
    return _parse_players_fallback(raw_response)


async def kick_player(
    config: DayzConfig, player_index: int, reason: str = ""
) -> str:
    """Kick a player by index."""
    async with _connect(config) as client:
        return await client.kick(player_index, reason)


async def ban_player(
    config: DayzConfig,
    player_index: int,
    duration_minutes: int = 0,
    reason: str = "",
) -> str:
    """Ban a player by index. duration=0 means permanent."""
    async with _connect(config) as client:
        return await client.ban(player_index, duration_minutes, reason)


async def send_global_message(config: DayzConfig, message: str) -> str:
    """Broadcast a global message to all players."""
    async with _connect(config) as client:
        return await client.send(message)


async def get_bans(config: DayzConfig) -> str:
    """Get the ban list as formatted text."""
    async with _connect(config) as client:
        bans = await client.fetch_bans()

    if not bans:
        return ""
    lines: list[str] = []
    for b in bans:
        dur = "permanent" if b.duration is None else f"{b.duration}min"
        lines.append(f"#{b.index}  {dur}  {b.reason or ''}")
    return "\n".join(lines)


async def remove_ban(config: DayzConfig, ban_index: int) -> str:
    """Remove a ban by index."""
    async with _connect(config) as client:
        return await client.unban(ban_index)


async def add_ban(
    config: DayzConfig,
    guid: str,
    duration_minutes: int = 0,
    reason: str = "",
) -> str:
    """Add a ban by GUID. duration=0 means permanent."""
    async with _connect(config) as client:
        return await client.ban(guid, duration_minutes, reason)
