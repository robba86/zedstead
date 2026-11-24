"""RconManager — persistent shared ArmaClient with event-driven session tracking."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import TYPE_CHECKING, Awaitable, Callable, TypeVar

import berconpy

from app.rcon.rcon_ops import (
    CONNECTION_LOST,
    RconError,
    RconPlayer,
    is_foreign_cancel,
    is_rcon_configured,
    rcon_host,
)
from app.monitoring.session_tracker import cleanup_session_log

if TYPE_CHECKING:
    from app.shared.config import DayzConfig

log = logging.getLogger(__name__)

T = TypeVar("T")

# How often to retry the connection when the server is not up yet.
_RETRY_INTERVAL = 10.0
# How often to re-sync the player list (seconds) so known_players stays fresh.
_PLAYER_SYNC_INTERVAL = 60.0

_DISCORD_POOL: ThreadPoolExecutor | None = None


def _discord_pool() -> ThreadPoolExecutor:
    """The one thread every RCon event's Discord post runs on, made on first use."""
    global _DISCORD_POOL
    if _DISCORD_POOL is None:
        _DISCORD_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rcon-discord")
    return _DISCORD_POOL


# More permissive regex for parsing BattlEye "players" response.
# berconpy's built-in regex requires \w+ for GUID and status which fails
# when DayZ returns formats like -(?) or unverified GUIDs.
_PLAYER_RE = re.compile(
    r"(?P<id>\d+)\s+"
    r"(?P<addr>\S+:\d+)\s+"
    r"(?P<ping>\d+)\s+"
    r"(?P<guid>[^\s(]+)\((?P<status>[^)]*)\)\s+"
    r"(?P<name>.+)"
)
# BattlEye chat message format: (Channel) PlayerName: message
_CHAT_RE = re.compile(r"\((?P<channel>.+?)\) (?P<name>.+?): (?P<message>.+)")


# berconpy logs this at ERROR — plain, no exception attached — each time its
# login attempts run out. `_run_loop` then retries every `_RETRY_INTERVAL`
# seconds, so a server that is simply not running printed it forever.
_UNREACHABLE_MESSAGE = "failed to connect to the server"


class _QuietUnreachableFilter(logging.Filter):
    """Drop berconpy's ERROR output for an unreachable RCON port.

    A UDP socket surfaces an ICMP port-unreachable asynchronously, so asyncio
    hands it to ``DatagramProtocol.error_received`` and berconpy logs it at
    ERROR with a traceback ("unusual error occurred during session").  For us
    that is the ordinary "server isn't running yet" case — ``_run_loop``
    already retries every ``_RETRY_INTERVAL`` seconds — and the traceback
    would be printed on every attempt for as long as the server is down.

    ``_UNREACHABLE_MESSAGE`` is the same case reported a second way, from the
    other end of the same failed attempt.  Matching it by message is deliberate
    and stays narrow: berconpy's *other* ERROR on this path, "password
    authentication was denied", is a real misconfiguration and must still be
    seen.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        if isinstance(exc, ConnectionError):
            log.debug("RCON: port unreachable (server not running?): %s", exc)
            return False
        if record.getMessage() == _UNREACHABLE_MESSAGE:
            log.debug("RCON: %s (server not running?)", _UNREACHABLE_MESSAGE)
            return False
        return True


def _raised_under_keep_alive(exc: BaseException) -> bool:
    """True when *exc* was raised inside berconpy's keep-alive coroutine."""
    tb = exc.__traceback__
    while tb is not None:
        code = tb.tb_frame.f_code
        if code.co_name == "_send_keep_alive" and "berconpy" in code.co_filename:
            return True
        tb = tb.tb_next
    return False


class _QuietKeepAliveRaceFilter(logging.Filter):
    """Drop asyncio's traceback for berconpy's keep-alive losing the transport.

    berconpy sends a keep-alive on a timer; when the connection is torn down
    between the timer firing and the send, ``AsyncClientConnector.send`` trips
    ``assert self._transport is not None``, and the keep-alive's done callback
    re-raises anything that is not an ``RCONCommandError`` — so asyncio's
    default handler prints "Exception in callback ... AssertionError" with a
    full traceback.  The connection is already gone by then and ``_run_loop``
    reconnects; only the traceback is new, and it reads like a crash.

    Narrow on purpose: an ``AssertionError`` raised under that one coroutine.
    Anything else asyncio reports is still printed.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        if isinstance(exc, AssertionError) and _raised_under_keep_alive(exc):
            log.debug("RCON: keep-alive raced the disconnect: %r", exc)
            return False
        return True


def _quiet_unreachable_errors() -> None:
    """Install the RCON log filters once — the single mute point.

    Filters attached to a logger only see records logged through that logger,
    so berconpy's must target ``berconpy.io`` itself, not the ``berconpy``
    parent.  The keep-alive race is reported by asyncio's default exception
    handler rather than by berconpy, so it needs the ``asyncio`` logger.
    """
    bercon_log = logging.getLogger("berconpy.io")
    if not any(isinstance(f, _QuietUnreachableFilter) for f in bercon_log.filters):
        bercon_log.addFilter(_QuietUnreachableFilter())

    asyncio_log = logging.getLogger("asyncio")
    if not any(isinstance(f, _QuietKeepAliveRaceFilter) for f in asyncio_log.filters):
        asyncio_log.addFilter(_QuietKeepAliveRaceFilter())


def _parse_players_fallback(response: str) -> list[RconPlayer]:
    """Parse a BattlEye 'players' response with a permissive regex.

    Handles formats that berconpy's stricter regex misses (e.g. ``-(?)``
    for unverified GUIDs).
    """
    result: list[RconPlayer] = []
    for m in _PLAYER_RE.finditer(response):
        name = m.group("name")
        lobby = name.endswith(" (Lobby)")
        if lobby:
            name = name.removesuffix(" (Lobby)")
        guid = m.group("guid")
        if guid == "-":
            guid = ""
        result.append(
            RconPlayer(
                index=int(m.group("id")),
                ip=m.group("addr").split(":")[0],
                port=0,
                ping=int(m.group("ping")),
                guid=guid,
                name=name,
                lobby=lobby,
            )
        )
    return result


class RconManager:
    """Owns a single persistent ArmaClient for the lifetime of ``zsm web``
    (or ``zsm supervise``).

    * Stays connected (with auto-reconnect) after the first successful login.
    * Uses berconpy events for real-time session tracking (replaces polling).
    * Exposes the command methods its callers use over that connection.
    """

    def __init__(
        self,
        config: DayzConfig,
        on_event: Callable[[str, str, str], None] | None = None,
        on_chat: Callable[[str, str, str, str], None] | None = None,
        on_message: Callable[[str, str], None] | None = None,
    ) -> None:
        self._config = config
        self._on_event = on_event
        # (level, text): BattlEye's own server messages ("" level, the panel
        # classifies them) and this connection's state changes ("good",
        # "warn", "bad"), for the Activity drawer's Key lines pane.
        self._on_message = on_message
        self._last_status = ""
        # (name, guid, channel, message) — fired for every chat line in
        # addition to the chat-log/Discord handling in _handle_chat.
        self._on_chat = on_chat

        self._client: berconpy.ArmaClient | None = None
        self._run_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._connected = asyncio.Event()

        # Last connection error (surfaced in _ensure_connected)
        self._last_error: str = ""

        # guid -> name for the current connection. Emptied when a logged-in
        # connection ends, after its players' disconnects are written.
        self._known_players: dict[str, str] = {}
        # GUIDs that got a real connect event on the current connection, so
        # the first sync does not write a second one for them.
        self._joined_this_connection: set[str] = set()

    # -- public state --------------------------------------------------------

    def set_config(self, config: DayzConfig) -> None:
        """Adopt a changed config for what is read per event: the Discord
        posts, the chat log switch, the log paths.

        Not for a new host, port or password. Those are read when a connection
        starts, so the panel replaces the manager instead
        (`app.web.panel._follow_rcon_config`).
        """
        self._config = config

    @property
    def known_players(self) -> dict[str, str]:
        """Return a snapshot of currently-known online players (guid->name)."""
        return dict(self._known_players)

    def is_connected(self) -> bool:
        return self._connected.is_set()

    async def wait_until_connected(self, timeout: float = 15.0) -> bool:
        """Wait for the connection to be established, with timeout.

        Returns True if connected, False if timed out.
        """
        if self._connected.is_set():
            return True
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Start the connection in a background task."""
        if self._run_task is not None and not self._run_task.done():
            return
        _quiet_unreachable_errors()
        self._stop_event.clear()
        self._connected.clear()
        self._last_error = ""
        self._last_status = ""
        cleanup_session_log(path=self._config.paths.session_log)
        self._run_task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Disconnect and cancel the background task."""
        self._stop_event.set()
        if self._client is not None:
            self._client.close()
        if self._run_task is not None:
            self._run_task.cancel()
            try:
                await asyncio.wait_for(self._run_task, timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass
            self._run_task = None
        self._connected.clear()

    # -- connection loop -----------------------------------------------------

    async def _run_loop(self) -> None:
        """Try to connect; on failure, retry every _RETRY_INTERVAL seconds."""
        while not self._stop_event.is_set():
            try:
                await self._run_connection()
                # If _run_connection returns normally (close was called), exit.
                return
            except asyncio.CancelledError:
                return
            except (berconpy.LoginTimeout, berconpy.LoginFailure) as exc:
                self._last_error = f"Login failed: {exc}"
                if not isinstance(exc, berconpy.LoginTimeout):
                    # A refused password, not a closed port (the normal case).
                    self._status("bad", f"RCon login refused: {exc}")
                log.debug("RCON: %s, retrying in %ss", self._last_error, _RETRY_INTERVAL)
                self._connected.clear()
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=_RETRY_INTERVAL
                    )
                    return  # stop() was called during the wait
                except asyncio.TimeoutError:
                    continue  # retry
            except Exception as exc:
                self._last_error = f"Connection error: {exc}"
                log.debug("RCON: %s, retrying in %ss", self._last_error, _RETRY_INTERVAL)
                self._connected.clear()
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=_RETRY_INTERVAL
                    )
                    return
                except asyncio.TimeoutError:
                    continue

    async def _run_connection(self) -> None:
        """Connect and stay connected until stop() is called.

        Uses berconpy's on_login event to detect successful login (avoids a
        bug in wait_for_login() where the login future goes stale when
        berconpy's internal retries exhaust).  Falls back to a timeout.
        """
        self._client = berconpy.ArmaClient()
        self._register_events()

        ip = rcon_host(self._config)
        port = self._config.rcon_port
        password = self._config.rcon_password

        # Detect login via berconpy's event dispatch instead of wait_for_login()
        login_event = asyncio.Event()

        @self._client.dispatch.on_login
        async def _on_login() -> None:
            login_event.set()

        log.debug("RCON: connecting to %s:%s", ip, port)
        task = self._client.protocol.run(ip, port, password)
        logged_in = False
        try:
            # Wait for either: login event fires, berconpy task fails, or timeout
            login_waiter = asyncio.create_task(login_event.wait())
            done, pending = await asyncio.wait(
                {login_waiter, task},
                return_when=asyncio.FIRST_COMPLETED,
                timeout=15.0,
            )
            for p in pending:
                if p is login_waiter:
                    p.cancel()

            if not done:
                # Neither login nor task failure within timeout
                raise berconpy.LoginTimeout(
                    f"no response from {ip}:{port} after 15s"
                )

            if task in done:
                # berconpy task ended before login — re-raise its exception
                login_waiter.cancel()
                task.result()  # raises LoginTimeout, LoginRefused, etc.
                raise berconpy.LoginTimeout("connection closed before login")

            # Login succeeded
            log.info("RCON: connected to %s:%s", ip, port)
            self._last_error = ""
            self._status("good", "RCon connected")
            # Set *before* the first sync: `_initial_sync` goes through
            # `fetch_players`, whose `_ensure_connected` waits on this very
            # flag — so with the flag set afterwards the first sync could only
            # ever time out, spending 15s on it and leaving the player list
            # empty until the next `_PLAYER_SYNC_INTERVAL` tick.
            self._connected.set()
            logged_in = True
            self._joined_this_connection.clear()
            await self._initial_sync()
            self._open_resynced_sessions()

            # Periodically re-sync player list so known_players stays accurate.
            # berconpy's task is waited on alongside the stop event: when the
            # connection dies (server stopped, RCON timeout) that task ends, and
            # until we notice, `is_connected()` keeps saying yes — sending
            # callers down the fast path into a connection being torn down.
            # Waiting only on the stop event left that window open for a whole
            # `_PLAYER_SYNC_INTERVAL`.
            stop_waiter = asyncio.create_task(self._stop_event.wait())
            try:
                while True:
                    done, _ = await asyncio.wait(
                        {stop_waiter, task},
                        return_when=asyncio.FIRST_COMPLETED,
                        timeout=_PLAYER_SYNC_INTERVAL,
                    )
                    if stop_waiter in done:
                        break  # stop() was called
                    if task in done:
                        self._connected.clear()
                        task.result()  # re-raise whatever ended the connection
                        raise RconError("connection closed")
                    await self._initial_sync()
            finally:
                stop_waiter.cancel()
        finally:
            # Never leave `is_connected()` true once this connection is over —
            # `_run_loop` only clears it on the paths that raise.
            self._connected.clear()
            if logged_in:
                self._close_open_sessions()
                if not self._stop_event.is_set():
                    # A stopped server ends it too, so a warning, not an error.
                    self._status("warn", "RCon connection lost")
            self._client.close()
            if task is not None:
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=3.0)
                except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                    pass

    async def _initial_sync(self) -> None:
        """Fetch current player list after first connect (no events emitted)."""
        if self._client is None:
            return
        try:
            players = await self.fetch_players()
            self._known_players.clear()
            for p in players:
                if p.guid:
                    self._known_players[p.guid] = p.name
                elif p.name:
                    # Track players without GUID by name so count is accurate
                    self._known_players[f"_name:{p.name}"] = p.name
        except Exception:
            log.debug("Initial player sync failed", exc_info=True)

    # -- session bookkeeping across connections -----------------------------
    #
    # BattlEye only reports joins and leaves while a connection is up. When it
    # ends — server stopped or crashed, RCon lost, `zsm web` stopped — nobody
    # writes those players' disconnects, and each session stayed open until
    # the player's next connect, days later (test pass 1, F44). These two
    # write the missing halves. Every way a server stops ends the connection,
    # so this one place covers all of them. The events carry a `reason` and
    # skip `_on_event`: they are bookkeeping, not joins or leaves, so no toast,
    # no WS event and (the callbacks own those) no Discord post.

    def _close_open_sessions(self) -> None:
        for guid, name in list(self._known_players.items()):
            if not guid.startswith("_name:"):
                self._write_event("disconnect", guid, name, reason="connection_ended")
        self._known_players.clear()

    def _open_resynced_sessions(self) -> None:
        """After a connection's first sync, open a session for everyone who is
        already on — otherwise a player who stayed on through an RCon drop has
        no open session, and their real leave reads as a zero-length one."""
        for guid, name in list(self._known_players.items()):
            if guid.startswith("_name:") or guid in self._joined_this_connection:
                continue
            self._write_event("connect", guid, name, reason="resync")

    # -- berconpy event handlers ---------------------------------------------

    def _register_events(self) -> None:
        if self._client is None:
            return

        @self._client.dispatch.on_player_guid
        async def _on_player_guid(player: berconpy.Player) -> None:
            guid = player.guid
            if not guid:
                return
            name = player.name
            self._known_players[guid] = name
            self._joined_this_connection.add(guid)
            self._write_event("connect", guid, name)
            # Discord join notification
            self._fire_discord_join(name, guid)

        @self._client.dispatch.on_player_disconnect
        async def _on_player_disconnect(player: berconpy.Player) -> None:
            guid = player.guid
            name = player.name or self._known_players.get(guid, "")
            self._known_players.pop(guid, None)
            self._write_event("disconnect", guid, name)
            # Discord leave notification
            self._fire_discord_leave(name, guid)

        @self._client.dispatch.on_player_kick
        async def _on_player_kick(player: berconpy.Player, reason: str) -> None:
            # Kicks do NOT fire on_player_disconnect
            guid = player.guid
            name = player.name or self._known_players.get(guid, "")
            self._known_players.pop(guid, None)
            self._write_event("disconnect", guid, name)
            self._fire_discord_leave(name, guid)

        @self._client.dispatch.on_player_message
        async def _on_player_message(
            player: berconpy.Player, channel: str, message: str
        ) -> None:
            self._handle_chat(player.name, player.guid, channel, message)

        # Raw message handler: catches chat that berconpy drops when the
        # player isn't in its cache (common with DayZ where the "players"
        # command response doesn't match berconpy's Arma 3 regex).
        @self._client.dispatch.on_message
        async def _on_raw_message(message: str) -> None:
            self._handle_raw_message(message)

    # -- JSONL event writing -------------------------------------------------

    def _write_event(
        self, event_type: str, guid: str, name: str, reason: str | None = None
    ) -> None:
        entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "event": event_type,
            "guid": guid,
            "name": name,
        }
        if reason is not None:
            entry["reason"] = reason
        try:
            log = self._config.paths.session_log
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass

        if self._on_event is not None and reason is None:
            try:
                self._on_event(event_type, guid, name)
            except Exception:
                pass

    def _post_discord(self, hook: str, *args: str) -> None:
        """Run ``discord_hooks.<hook>(config, *args)`` fire-and-forget.

        Off the event loop: these are called from berconpy's handlers, and a
        post waits up to 10 s for Discord, which froze the whole panel once
        per join, leave or chat line. One worker, so posts keep their order.
        """
        config = self._config

        def _post() -> None:
            try:
                from app.monitoring import discord_hooks

                getattr(discord_hooks, hook)(config, *args)
            except Exception:
                log.debug("Discord %s failed", hook, exc_info=True)

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            _post()  # no loop to keep free
            return
        _discord_pool().submit(_post)

    def _fire_discord_join(self, name: str, guid: str) -> None:
        """Send Discord webhook for player join (fire-and-forget)."""
        self._post_discord("notify_player_join", name, guid)

    def _fire_discord_leave(self, name: str, guid: str) -> None:
        """Send Discord webhook for player leave (fire-and-forget)."""
        self._post_discord("notify_player_leave", name, guid)

    def _handle_chat(
        self, name: str, guid: str, channel: str, message: str
    ) -> None:
        """Log chat and optionally relay to Discord."""
        if self._on_chat is not None:
            try:
                self._on_chat(name, guid, channel, message)
            except Exception:
                pass

        if self._config.chat_log_enabled:
            try:
                from app.monitoring.chat_logger import write_chat_event

                write_chat_event(name, guid, channel, message, path=self._config.paths.chat_log)
            except Exception:
                pass

        self._post_discord("notify_chat", name, channel, message)

    def _notify(self, level: str, text: str) -> None:
        """Hand one line to `on_message`. A callback error never reaches RCon."""
        if self._on_message is None:
            return
        try:
            self._on_message(level, text)
        except Exception:
            pass

    def _status(self, level: str, text: str) -> None:
        """Report a connection state once, not on every retry of the same state."""
        if text == self._last_status:
            return
        self._last_status = text
        self._notify(level, text)

    def _handle_raw_message(self, message: str) -> None:
        """One raw BattlEye message: chat goes to the chat handling, anything
        else (kicks, admin logins, GUID checks) to `on_message` unclassified."""
        if _CHAT_RE.fullmatch(message) is None:
            self._notify("", message)
        self._handle_raw_chat(message)

    def _handle_raw_chat(self, message: str) -> None:
        """Catch chat from the raw RCON message stream.

        berconpy's on_player_message only fires when the player is in its
        cache.  When the cache is empty (DayZ RCON format mismatch), chat
        events are silently dropped.  This handler parses the raw message
        directly so chat logging works regardless of cache state.
        """
        m = _CHAT_RE.fullmatch(message)
        if m is None:
            return
        name = m.group("name")
        channel = m.group("channel")
        chat_msg = m.group("message")

        # Resolve GUID from known_players (reverse lookup)
        guid = ""
        for g, n in self._known_players.items():
            if n == name and not g.startswith("_name:"):
                guid = g
                break

        # Skip if berconpy already handled this (player was in cache)
        if self._client is not None:
            from berconpy.utils import get as bp_get
            if bp_get(self._client.cache.players, name=name) is not None:
                return

        self._handle_chat(name, guid, channel, chat_msg)

    # -- RCON commands -------------------------------------------------------

    async def _ensure_connected(self) -> None:
        """Wait for connection, raise RconError if not connected after timeout."""
        if self.is_connected():
            return
        if not await self.wait_until_connected(timeout=15.0):
            detail = self._last_error or "Server not responding"
            raise RconError(
                f"RCON not connected — {detail}\n"
                f"Connecting to {rcon_host(self._config)}:{self._config.rcon_port}"
            )

    async def _command(self, awaitable: Awaitable[T]) -> T:
        """Await a berconpy command, mapping every failure to ``RconError``.

        Including the cancellation berconpy raises when the connection drops
        under a command that is still in flight — see
        :func:`app.rcon.rcon_ops.is_foreign_cancel` for why that arrives as a
        ``CancelledError`` and why a real one is re-raised instead.
        """
        try:
            return await awaitable
        except asyncio.CancelledError:
            if not is_foreign_cancel():
                raise
            self._connected.clear()
            raise RconError(CONNECTION_LOST) from None
        except Exception as exc:
            raise RconError(f"RCON error: {exc}") from exc

    async def fetch_players(self) -> list[RconPlayer]:
        """Fetch current player list via the persistent connection.

        Uses berconpy's parser first; falls back to a more permissive
        regex when berconpy returns an empty list (common with DayZ
        servers whose RCON output differs slightly from Arma 3).
        """
        await self._ensure_connected()
        assert self._client is not None
        raw_response = await self._command(self._client.send_command("players"))
        try:
            self._client.cache.update_players(raw_response)
            players = self._client.players
        except Exception as exc:
            raise RconError(f"RCON error: {exc}") from exc

        if players:
            result: list[RconPlayer] = []
            for p in players:
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
            return result

        # berconpy returned nothing — try our own permissive parser
        fallback = _parse_players_fallback(raw_response)
        if fallback:
            log.debug(
                "berconpy parsed 0 players but fallback found %d — "
                "DayZ RCON format likely differs from Arma 3",
                len(fallback),
            )
            # Inject into berconpy's cache so event handlers (chat, disconnect)
            # can look up players. Without this, on_player_message silently
            # drops chat events because the player isn't in cache.
            for p in fallback:
                if self._client.cache.get_player(p.index) is None:
                    self._client.cache.add_missing_player({
                        "id": p.index,
                        "name": p.name,
                        "guid": p.guid,
                        "addr": f"{p.ip}:0",
                        "ping": p.ping,
                        "is_guid_valid": bool(p.guid),
                        "in_lobby": p.lobby,
                    })
        return fallback

    async def send_command(self, command: str) -> str:
        await self._ensure_connected()
        assert self._client is not None
        return await self._command(self._client.send_command(command))

    async def send_message(self, message: str) -> str:
        await self._ensure_connected()
        assert self._client is not None
        return await self._command(self._client.send(message))

