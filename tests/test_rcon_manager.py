"""Tests for RconManager helpers that don't need a live BattlEye server."""

import asyncio
import logging
from types import SimpleNamespace

import berconpy
import pytest

from app.rcon.rcon_manager import (
    RconManager,
    _QuietKeepAliveRaceFilter,
    _QuietUnreachableFilter,
    _quiet_unreachable_errors,
)
from app.rcon.rcon_ops import RconError


def _record(exc: BaseException | None) -> logging.LogRecord:
    exc_info = (type(exc), exc, None) if exc is not None else None
    return logging.LogRecord(
        name="berconpy.io",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="unusual error occurred during session",
        args=(),
        exc_info=exc_info,
    )


def test_filter_drops_connection_refused():
    f = _QuietUnreachableFilter()
    assert f.filter(_record(ConnectionRefusedError(111, "Connection refused"))) is False


def test_filter_keeps_other_errors():
    f = _QuietUnreachableFilter()
    assert f.filter(_record(ValueError("bad packet"))) is True
    assert f.filter(_record(None)) is True


def test_install_is_idempotent():
    bercon_log = logging.getLogger("berconpy.io")
    before = list(bercon_log.filters)
    try:
        _quiet_unreachable_errors()
        _quiet_unreachable_errors()
        installed = [
            f for f in bercon_log.filters if isinstance(f, _QuietUnreachableFilter)
        ]
        assert len(installed) == 1
    finally:
        bercon_log.filters = before


# --- the "failed to connect" ERROR berconpy logs without an exception --------


def _message_record(msg: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="berconpy.io",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )


def test_filter_drops_failed_to_connect():
    f = _QuietUnreachableFilter()
    assert f.filter(_message_record("failed to connect to the server")) is False


def test_filter_keeps_a_denied_password():
    f = _QuietUnreachableFilter()
    assert f.filter(_message_record("password authentication was denied")) is True


# --- berconpy's keep-alive racing the disconnect -----------------------------


def _keep_alive_error() -> AssertionError:
    """An AssertionError carrying a traceback that looks like berconpy's.

    The filter matches on the frame, so the frame has to be real: compiled
    under berconpy's filename, in a function with berconpy's name.
    """
    src = "def _send_keep_alive():\n    assert None is not None\n"
    ns: dict = {}
    exec(compile(src, "/site-packages/berconpy/ext/arma/io.py", "exec"), ns)
    try:
        ns["_send_keep_alive"]()
    except AssertionError as exc:
        return exc
    raise RuntimeError("the fake keep-alive did not raise")


def _asyncio_record(exc: BaseException) -> logging.LogRecord:
    return logging.LogRecord(
        name="asyncio",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="Exception in callback AsyncClientConnector._begin_keep_alive...",
        args=(),
        exc_info=(type(exc), exc, exc.__traceback__),
    )


def test_keep_alive_filter_drops_the_transport_assertion():
    f = _QuietKeepAliveRaceFilter()
    assert f.filter(_asyncio_record(_keep_alive_error())) is False


def test_keep_alive_filter_keeps_other_asyncio_errors():
    f = _QuietKeepAliveRaceFilter()
    assert f.filter(_asyncio_record(AssertionError("somewhere else"))) is True
    assert f.filter(_asyncio_record(ValueError("bad"))) is True


def test_keep_alive_filter_install_is_idempotent():
    asyncio_log = logging.getLogger("asyncio")
    before = list(asyncio_log.filters)
    try:
        _quiet_unreachable_errors()
        _quiet_unreachable_errors()
        installed = [
            f for f in asyncio_log.filters if isinstance(f, _QuietKeepAliveRaceFilter)
        ]
        assert len(installed) == 1
    finally:
        asyncio_log.filters = before


# --- a dropped connection under an in-flight command -------------------------


def _manager() -> RconManager:
    cfg = SimpleNamespace(
        rcon_port=2302, rcon_password="secret", rcon_host="127.0.0.1", paths=_paths(),
    )
    return RconManager(cfg)  # type: ignore[arg-type]


async def test_command_maps_berconpys_cancel_to_rcon_error():
    """berconpy cancels pending command futures when the connection drops."""

    async def _connection_dies() -> str:
        fut = asyncio.get_running_loop().create_future()
        fut.cancel()  # what AsyncCommander.reset() does to every pending command
        return await fut

    mgr = _manager()
    mgr._connected.set()
    with pytest.raises(RconError) as excinfo:
        await mgr._command(_connection_dies())
    assert "connection closed" in str(excinfo.value)
    # ...and the manager stops claiming it is connected.
    assert mgr.is_connected() is False


async def test_command_reraises_a_real_cancellation():
    """Cancelling the caller's task must still cancel it, not become an error."""
    started = asyncio.Event()

    async def _hangs() -> str:
        started.set()
        await asyncio.sleep(3600)
        return ""

    mgr = _manager()
    task = asyncio.create_task(mgr._command(_hangs()))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_command_still_maps_ordinary_failures():
    async def _boom() -> str:
        raise RuntimeError("no route to host")

    with pytest.raises(RconError, match="no route to host"):
        await _manager()._command(_boom())


# --- is_connected() follows berconpy's connection, not a 60s timer -----------


class _FakeDispatch:
    """Records the handlers RconManager registers so a test can fire them."""

    def __init__(self) -> None:
        self.handlers: dict = {}

    def __getattr__(self, name: str):
        def register(fn):
            self.handlers[name] = fn
            return fn

        return register


class _FakeClient:
    """Just enough ArmaClient for `_run_connection`'s connect/teardown path."""

    def __init__(self, hold: asyncio.Event) -> None:
        self.dispatch = _FakeDispatch()
        self.protocol = SimpleNamespace(
            run=lambda ip, port, password: asyncio.create_task(hold.wait())
        )
        self.closed = False

    def close(self) -> None:
        self.closed = True


async def _until(predicate, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition never became true")
        await asyncio.sleep(0.005)


async def test_is_connected_clears_when_the_connection_dies(monkeypatch):
    hold = asyncio.Event()  # released to end berconpy's task = connection lost
    client = _FakeClient(hold)
    monkeypatch.setattr(berconpy, "ArmaClient", lambda: client)

    mgr = _manager()

    async def _no_sync() -> None:
        return None

    monkeypatch.setattr(mgr, "_initial_sync", _no_sync)

    run = asyncio.create_task(mgr._run_connection())
    await _until(lambda: "on_login" in client.dispatch.handlers)
    await client.dispatch.handlers["on_login"]()
    await _until(mgr.is_connected)

    hold.set()
    await _until(lambda: not mgr.is_connected())

    with pytest.raises(RconError):
        await run
    assert client.closed is True


async def test_run_connection_dials_the_configured_host(monkeypatch):
    """TODO G1b: the manager's host was hardcoded to loopback."""
    hold = asyncio.Event()
    dialled: list[tuple] = []

    class _RecordingClient(_FakeClient):
        def __init__(self) -> None:
            super().__init__(hold)
            self.protocol = SimpleNamespace(
                run=lambda ip, port, password: (
                    dialled.append((ip, port, password)),
                    asyncio.create_task(hold.wait()),
                )[1]
            )

    monkeypatch.setattr(berconpy, "ArmaClient", _RecordingClient)

    cfg = SimpleNamespace(rcon_port=2305, rcon_password="secret", rcon_host="203.0.113.7")
    mgr = RconManager(cfg)  # type: ignore[arg-type]

    async def _no_sync() -> None:
        return None

    monkeypatch.setattr(mgr, "_initial_sync", _no_sync)

    run = asyncio.create_task(mgr._run_connection())
    await _until(lambda: bool(dialled))
    assert dialled == [("203.0.113.7", 2305, "secret")]

    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    hold.set()


# --- a connection's end closes its players' sessions (test pass 1, F44) ------


def _read_log(path):
    import json

    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.fixture()
def session_log():
    """Where a manager built by `_recording_manager` writes its session log:
    the pre-instances layout, under the suite's throwaway config dir."""
    return _paths().session_log


def _paths():
    from app.shared.paths import InstancePaths, instance_root

    return InstancePaths(instance_root(""))


def _recording_manager(monkeypatch):
    events: list[tuple] = []
    discord: list[tuple] = []
    cfg = SimpleNamespace(
        rcon_port=2302, rcon_password="secret", rcon_host="127.0.0.1", paths=_paths(),
    )
    mgr = RconManager(cfg, on_event=lambda *e: events.append(e))  # type: ignore[arg-type]
    monkeypatch.setattr(mgr, "_fire_discord_join", lambda *a: discord.append(("join", *a)))
    monkeypatch.setattr(mgr, "_fire_discord_leave", lambda *a: discord.append(("leave", *a)))
    return mgr, events, discord


async def test_a_logged_in_connection_closes_its_sessions_when_it_ends(monkeypatch, session_log):
    hold = asyncio.Event()
    client = _FakeClient(hold)
    monkeypatch.setattr(berconpy, "ArmaClient", lambda: client)
    mgr, events, discord = _recording_manager(monkeypatch)

    async def _sync() -> None:
        mgr._known_players.clear()
        mgr._known_players.update({"g1": "Anna", "g2": "Ben", "_name:Cid": "Cid"})

    monkeypatch.setattr(mgr, "_initial_sync", _sync)

    run = asyncio.create_task(mgr._run_connection())
    await _until(lambda: "on_login" in client.dispatch.handlers)
    await client.dispatch.handlers["on_login"]()
    await _until(mgr.is_connected)
    hold.set()  # the server stopped
    with pytest.raises(RconError):
        await run

    got = [(e["event"], e["guid"], e.get("reason")) for e in _read_log(session_log)]
    assert got == [
        ("connect", "g1", "resync"),
        ("connect", "g2", "resync"),
        ("disconnect", "g1", "connection_ended"),
        ("disconnect", "g2", "connection_ended"),
    ]
    assert mgr.known_players == {}
    # Bookkeeping, not joins and leaves: no toast/WS event, no Discord post.
    assert events == [] and discord == []


async def test_a_connection_that_never_logged_in_writes_nothing(monkeypatch, session_log):
    hold = asyncio.Event()
    hold.set()  # berconpy's task ends before any login
    client = _FakeClient(hold)
    monkeypatch.setattr(berconpy, "ArmaClient", lambda: client)
    mgr, _, _ = _recording_manager(monkeypatch)
    mgr._known_players["g1"] = "Anna"  # stale, from nowhere this connection saw

    with pytest.raises(Exception):
        await mgr._run_connection()
    assert _read_log(session_log) == []


async def test_only_the_first_sync_of_a_connection_opens_sessions(monkeypatch, session_log):
    import app.rcon.rcon_manager as rcon_manager

    hold = asyncio.Event()
    client = _FakeClient(hold)
    monkeypatch.setattr(berconpy, "ArmaClient", lambda: client)
    monkeypatch.setattr(rcon_manager, "_PLAYER_SYNC_INTERVAL", 0.01)
    mgr, _, _ = _recording_manager(monkeypatch)
    syncs = []

    async def _sync() -> None:
        syncs.append(1)
        mgr._known_players.clear()
        mgr._known_players["g1"] = "Anna"

    monkeypatch.setattr(mgr, "_initial_sync", _sync)

    run = asyncio.create_task(mgr._run_connection())
    await _until(lambda: "on_login" in client.dispatch.handlers)
    await client.dispatch.handlers["on_login"]()
    await _until(lambda: len(syncs) >= 4)
    hold.set()
    with pytest.raises(RconError):
        await run

    connects = [e for e in _read_log(session_log) if e["event"] == "connect"]
    assert len(connects) == 1


def test_a_player_who_joined_this_connection_is_not_opened_twice(session_log):
    mgr = _manager()
    mgr._joined_this_connection = {"g1"}
    mgr._known_players = {"g1": "Anna", "g2": "Ben"}

    mgr._open_resynced_sessions()

    assert [(e["guid"], e["reason"]) for e in _read_log(session_log)] == [("g2", "resync")]


# --- Discord posts leave the event loop free ---------------------------------


async def test_discord_posts_from_rcon_events_do_not_block_the_event_loop(monkeypatch):
    """A join, leave or chat line posts to Discord with a 10 s timeout. Sent
    from berconpy's handler on the panel's event loop, a slow Discord froze
    every request and WebSocket for that long, once per chat line."""
    import threading

    from app.monitoring import discord_hooks

    loop_thread = threading.get_ident()
    posted: list[tuple] = []
    done = threading.Event()

    def _slow(*args, **kwargs):
        posted.append((threading.get_ident(), args[1:]))
        if len(posted) == 3:
            done.set()

    for name in ("notify_player_join", "notify_player_leave", "notify_chat"):
        monkeypatch.setattr(discord_hooks, name, _slow)

    mgr = _manager()
    mgr._config.chat_log_enabled = False
    mgr._fire_discord_join("Alice", "g1")
    mgr._fire_discord_leave("Alice", "g1")
    mgr._handle_chat("Alice", "g1", "Global", "hi")

    assert await asyncio.to_thread(done.wait, 5)
    assert all(thread != loop_thread for thread, _ in posted)
    # One after another, in the order they happened.
    assert [args for _, args in posted] == [("Alice", "g1"), ("Alice", "g1"), ("Alice", "Global", "hi")]


def test_discord_posts_outside_an_event_loop_still_go_out(monkeypatch):
    from app.monitoring import discord_hooks

    posted: list[tuple] = []
    monkeypatch.setattr(discord_hooks, "notify_player_join", lambda *a: posted.append(a[1:]))
    _manager()._fire_discord_join("Bob", "g2")
    assert posted == [("Bob", "g2")]


# --- messages for the Activity drawer's Key lines pane -----------------------


def _message_manager(sent: list) -> RconManager:
    cfg = SimpleNamespace(
        rcon_port=2302, rcon_password="secret", rcon_host="127.0.0.1", paths=_paths(),
    )
    return RconManager(cfg, on_message=lambda level, text: sent.append((level, text)))  # type: ignore[arg-type]


def test_a_battleye_message_that_is_not_chat_goes_to_on_message():
    sent: list = []
    mgr = _message_manager(sent)
    mgr._handle_raw_message("Player #3 Bob (abc) has been kicked by BattlEye: Client not responding")
    assert sent == [("", "Player #3 Bob (abc) has been kicked by BattlEye: Client not responding")]


def test_chat_does_not_go_to_on_message():
    sent: list = []
    mgr = _message_manager(sent)
    chats: list = []
    mgr._handle_raw_chat = chats.append  # the chat path is not under test here
    mgr._handle_raw_message("(Global) Bob: hello")
    assert sent == [] and chats == ["(Global) Bob: hello"]


def test_a_status_is_reported_once_until_it_changes():
    sent: list = []
    mgr = _message_manager(sent)
    for _ in range(3):
        mgr._status("bad", "RCon login refused: denied")
    mgr._status("good", "RCon connected")
    assert sent == [("bad", "RCon login refused: denied"), ("good", "RCon connected")]


def test_a_failing_callback_never_reaches_rcon():
    def boom(level, text):
        raise RuntimeError("callback broke")

    cfg = SimpleNamespace(rcon_port=2302, rcon_password="x", rcon_host="127.0.0.1", paths=_paths())
    RconManager(cfg, on_message=boom)._notify("", "anything")  # type: ignore[arg-type]
