"""Tests for app/rcon/rcon_ops.py — the RCON setup check and connect timeout.

The commands themselves are exercised through tests/test_web_players.py (they
need a live BattlEye socket to test for real).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from app.rcon import rcon_ops
from app.rcon.rcon_ops import RconError, check_rcon_setup, is_rcon_configured


def test_configured_server_reports_no_problems(sample_config):
    # tmp_server_dir ships beserver_x64.cfg with RConPassword mypass / 2305.
    sample_config.rcon_password = "mypass"
    sample_config.rcon_port = 2305
    assert is_rcon_configured(sample_config)
    assert check_rcon_setup(sample_config) == []


def test_missing_password_is_reported(sample_config):
    sample_config.rcon_password = ""
    problems = check_rcon_setup(sample_config)
    assert any("password" in p.lower() for p in problems)


def test_password_mismatch_with_beserver_cfg(sample_config):
    sample_config.rcon_password = "somethingelse"
    sample_config.rcon_port = 2305
    problems = check_rcon_setup(sample_config)
    assert any("RConPassword" in p for p in problems)


def test_port_mismatch_with_beserver_cfg(sample_config):
    sample_config.rcon_password = "mypass"
    sample_config.rcon_port = 2310
    problems = check_rcon_setup(sample_config)
    assert any("RConPort" in p and "2305" in p for p in problems)


def test_battleye_disabled_in_serverdz_cfg(sample_config):
    sample_config.rcon_password = "mypass"
    sample_config.rcon_port = 2305
    path = sample_config.server_cfg_path
    path.write_text(path.read_text() + "BattlEye = 0;\n")
    problems = check_rcon_setup(sample_config)
    assert any("BattlEye" in p for p in problems)


def test_missing_beserver_cfg_is_not_a_problem(sample_config):
    """BattlEye writes it on first start — absence before that is normal."""
    sample_config.rcon_password = "mypass"
    sample_config.rcon_port = 2305
    sample_config.beserver_cfg_path.unlink()
    assert check_rcon_setup(sample_config) == []


def test_while_running_battleyes_own_copy_is_checked(sample_config, battleye_running):
    """BattlEye renames the cfg to its active copy while the server runs
    (measured in 2026 Q3). Reading the missing cfg skipped both checks, just
    when a failed login needs them."""
    sample_config.rcon_password = "mypass"  # the value beserver_x64.cfg had
    sample_config.rcon_port = 2305
    problems = check_rcon_setup(sample_config)
    assert "RConPassword in beserver_x64.cfg does not match Zedstead's RCon password." in problems
    assert "RConPort in beserver_x64.cfg is 2306, Zedstead connects to 2305." in problems

    sample_config.rcon_password = "livepass"
    sample_config.rcon_port = 2306
    assert check_rcon_setup(sample_config) == []


# ---------------------------------------------------------------------------
# _connect's login timeout
# ---------------------------------------------------------------------------


class _FakeClient:
    """Stands in for berconpy.ArmaClient. ``delay`` never finishes logging in."""

    def __init__(self, delay: float = 0.0) -> None:
        from types import SimpleNamespace

        self.delay = delay
        self.closed = False
        self.exited = False
        # berconpy's cache listener, which `_connect` takes off one-shot clients.
        self.cache = SimpleNamespace(on_login=object())
        self.login_listeners = [self.cache.on_login]
        self.dispatch = SimpleNamespace(
            on_login=SimpleNamespace(remove=self.login_listeners.remove)
        )

    def connect(self, ip, port, password):
        client = self

        @asynccontextmanager
        async def _session():
            if client.delay:
                await asyncio.sleep(client.delay)
            try:
                yield client
            finally:
                client.exited = True

        return _session()

    def close(self) -> None:
        self.closed = True

    async def send_command(self, command: str) -> str:
        return f"ran {command}"


@pytest.fixture()
def rcon_config(sample_config, monkeypatch):
    """A configured, *running* local server. `_connect` asks systemd first
    (plans/53 A), and on the dev box the real answer is "stopped"."""
    from app.server import service_control

    sample_config.rcon_password = "mypass"
    sample_config.rcon_port = 2305
    monkeypatch.setattr(service_control, "is_server_running", lambda config=None: True)
    return sample_config


# ---------------------------------------------------------------------------
# A stopped server is refused before any connect (test pass 2, N5)
# ---------------------------------------------------------------------------


def _refuse_connects(monkeypatch):
    attempts = []
    monkeypatch.setattr(rcon_ops.berconpy, "ArmaClient", lambda: attempts.append(1) or _FakeClient())
    return attempts


async def test_a_stopped_local_server_is_refused_without_connecting(rcon_config, monkeypatch):
    from app.server import service_control

    monkeypatch.setattr(service_control, "is_server_running", lambda config=None: False)
    attempts = _refuse_connects(monkeypatch)

    with pytest.raises(rcon_ops.ServerStopped, match="stopped"):
        await rcon_ops.send_global_message(rcon_config, "hello")
    assert attempts == []


async def test_a_remote_rcon_host_is_not_judged_by_this_machine(rcon_config, monkeypatch):
    """The local unit says nothing about a server on another host."""
    from app.server import service_control

    def _must_not_ask(config=None):
        raise AssertionError("asked this machine's systemd about a remote server")

    monkeypatch.setattr(service_control, "is_server_running", _must_not_ask)
    attempts = _refuse_connects(monkeypatch)
    rcon_config.rcon_host = "203.0.113.7"

    assert await rcon_ops.rcon_send(rcon_config, "players") == "ran players"
    assert attempts == [1]


def test_server_stopped_is_still_an_rcon_error():
    """Every caller that catches RconError keeps catching it."""
    assert issubclass(rcon_ops.ServerStopped, RconError)


async def test_connect_times_out_instead_of_hanging(rcon_config, monkeypatch):
    """berconpy retries forever once its initial attempts fail — we must not."""
    fake = _FakeClient(delay=30)
    monkeypatch.setattr(rcon_ops, "CONNECT_TIMEOUT", 0.05)
    monkeypatch.setattr(rcon_ops.berconpy, "ArmaClient", lambda: fake)

    with pytest.raises(RconError, match="did not answer"):
        async with rcon_ops._connect(rcon_config):
            pass
    assert fake.closed is True


async def test_connect_yields_and_closes_on_success(rcon_config, monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(rcon_ops.berconpy, "ArmaClient", lambda: fake)

    async with rcon_ops._connect(rcon_config) as client:
        assert await client.send_command("players") == "ran players"
    assert fake.exited is True


async def test_command_failure_becomes_rcon_error(rcon_config, monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(rcon_ops.berconpy, "ArmaClient", lambda: fake)

    with pytest.raises(RconError, match="RCON error"):
        async with rcon_ops._connect(rcon_config):
            raise RuntimeError("socket died")
    assert fake.exited is True


# ---------------------------------------------------------------------------
# rcon_host — dialling a server zsm does not run (TODO G1b)
# ---------------------------------------------------------------------------


def test_rcon_host_defaults_to_loopback(sample_config):
    assert rcon_ops.rcon_host(sample_config) == "127.0.0.1"
    assert rcon_ops.is_remote_rcon(sample_config) is False


def test_blank_rcon_host_is_loopback_not_a_blank_dial(sample_config):
    sample_config.rcon_host = "   "
    assert rcon_ops.rcon_host(sample_config) == "127.0.0.1"
    assert rcon_ops.is_remote_rcon(sample_config) is False


def test_localhost_spellings_are_not_remote(sample_config):
    for host in ("localhost", "::1", "LOCALHOST"):
        sample_config.rcon_host = host
        assert rcon_ops.is_remote_rcon(sample_config) is False, host


def test_a_named_host_is_remote(sample_config):
    sample_config.rcon_host = "dayz.example.com"
    assert rcon_ops.is_remote_rcon(sample_config) is True


async def test_connect_dials_the_configured_host(rcon_config, monkeypatch):
    rcon_config.rcon_host = "203.0.113.7"
    dialled: list[tuple] = []

    class _Recording(_FakeClient):
        def connect(self, ip, port, password):
            dialled.append((ip, port, password))
            return super().connect(ip, port, password)

    monkeypatch.setattr(rcon_ops.berconpy, "ArmaClient", _Recording)

    async with rcon_ops._connect(rcon_config):
        pass
    assert dialled == [("203.0.113.7", 2305, "mypass")]


def test_remote_host_skips_the_local_file_checks(sample_config):
    """A rented server's serverDZ.cfg/beserver_x64.cfg are not on this disk.

    tmp_server_dir ships beserver_x64.cfg with RConPassword mypass; against a
    remote host a different password is not a mismatch to report.
    """
    sample_config.rcon_password = "the-remote-password"
    sample_config.rcon_port = 2305
    sample_config.rcon_host = "203.0.113.7"
    assert check_rcon_setup(sample_config) == []


def test_remote_host_still_reports_zsm_side_problems(sample_config):
    sample_config.rcon_password = ""
    sample_config.rcon_host = "203.0.113.7"
    problems = check_rcon_setup(sample_config)
    assert any("password" in p.lower() for p in problems)
    # Configs -> BattlEye cannot set a remote host's password.
    assert not any("BattlEye" in p for p in problems)


# ---------------------------------------------------------------------------
# adopt_beserver_rcon — the BattlEye editor's sync into zsm's config
# ---------------------------------------------------------------------------


def test_adopt_copies_the_files_password_and_port(sample_config):
    rcon_ops.adopt_beserver_rcon(sample_config, {"RConPassword": "pw", "RConPort": "2310"})
    assert (sample_config.rcon_password, sample_config.rcon_port) == ("pw", 2310)
    assert check_rcon_setup(sample_config) != []  # the fixture file still says mypass/2305


@pytest.mark.parametrize("port", ["", "abc", "0", "70000", None])
def test_adopt_leaves_the_port_alone_when_the_file_has_no_usable_one(sample_config, port):
    sample_config.rcon_port = 2305
    data = {"RConPassword": "pw"} if port is None else {"RConPassword": "pw", "RConPort": port}
    rcon_ops.adopt_beserver_rcon(sample_config, data)
    assert (sample_config.rcon_password, sample_config.rcon_port) == ("pw", 2305)


def test_adopt_leaves_the_password_alone_when_the_file_has_none(sample_config):
    sample_config.rcon_password = "kept"
    rcon_ops.adopt_beserver_rcon(sample_config, {"RConPort": "2305"})
    assert sample_config.rcon_password == "kept"


def test_adopt_is_a_no_op_for_a_remote_host(sample_config):
    sample_config.rcon_host = "203.0.113.7"
    sample_config.rcon_password = "the-remote-password"
    sample_config.rcon_port = 2402
    rcon_ops.adopt_beserver_rcon(sample_config, {"RConPassword": "local", "RConPort": "2305"})
    assert (sample_config.rcon_password, sample_config.rcon_port) == ("the-remote-password", 2402)


# ---------------------------------------------------------------------------
# One-shot clients do not run berconpy's automatic player fetch (plans/53)
# ---------------------------------------------------------------------------


async def test_a_one_shot_client_drops_the_caches_login_fetch(rcon_config, monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(rcon_ops.berconpy, "ArmaClient", lambda: fake)

    await rcon_ops.rcon_send(rcon_config, "players")

    assert fake.login_listeners == []


def test_berconpy_still_registers_the_listener_we_remove():
    """The removal in `_connect` is a silent no-op if the listener is not
    there, so a berconpy update that registers the fetch another way would
    bring the traceback back without failing anything else. This fails."""
    import berconpy

    client = berconpy.ArmaClient()
    assert client.cache.on_login in client.dispatch._event_listeners["on_login"]
    client.dispatch.on_login.remove(client.cache.on_login)
    assert client.dispatch._event_listeners["on_login"] == []
