"""Tests for app/ops/site_ops.py and `zsm web`'s zedstead.com heartbeat
(TODO F7, plans/57 B).

Nothing here reaches the network. `requests` is replaced on
`app.ops.site_ops`, which is the binding the module calls (root CLAUDE.md:
patch the binding the caller uses), and every request is recorded so a test
can assert that none was made.
"""

from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace

import pytest
import requests
from fastapi.testclient import TestClient

from app import __version__
from app.ops import site_ops
from app.shared.config import DayzConfig
from app.shared.settings_registry import _BY_KEY, _parse, _write
from app.web.site_heartbeat import SiteHeartbeat

ENDPOINT = "https://api.example.test"


class FakeResponse:
    def __init__(self, status: int = 204, body=None, text: str = "") -> None:
        self.status_code = status
        self._body = body
        self.text = json.dumps(body) if body is not None else text

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400

    def json(self):
        if self._body is None:
            raise ValueError("no JSON body")
        return self._body


@pytest.fixture()
def net(monkeypatch):
    """Every request made, in order; answered with ``net.response``, or
    raising ``net.error`` when that is set."""
    fake = SimpleNamespace(calls=[], response=FakeResponse(), error=None)

    def request(method, url, **kwargs):
        fake.calls.append(SimpleNamespace(method=method, url=url, **kwargs))
        if fake.error is not None:
            raise fake.error
        return fake.response

    monkeypatch.setattr(
        site_ops,
        "requests",
        SimpleNamespace(request=request, RequestException=requests.RequestException),
    )
    return fake


def _switch(config: DayzConfig, key: str, on: bool) -> None:
    """Through the registry's setter, the way the CLI and the web do it."""
    _write(_BY_KEY[key], config, on)


@pytest.fixture()
def config(sample_config, monkeypatch):
    """Both switches on, pointed at a fake backend: everything may send."""
    monkeypatch.setattr(site_ops, "ENDPOINT", ENDPOINT)
    _switch(sample_config, "site_ping", True)
    _switch(sample_config, "site_listing", True)
    return sample_config


@pytest.fixture()
def running(monkeypatch):
    """The server's running state as `heartbeat` reads it."""
    flag = SimpleNamespace(value=True)
    monkeypatch.setattr(
        "app.server.service_control.is_server_running", lambda config=None: flag.value
    )
    return flag


# ---------------------------------------------------------------------------
# The payloads are exactly what the setting descriptions promise
# ---------------------------------------------------------------------------


def test_the_ping_payload_is_exactly_its_four_fields(config):
    payload = site_ops.ping_payload(config)

    assert set(payload) == {"install_id", "version", "os", "docker"}
    assert payload["install_id"] == site_ops.ping_id(config)
    assert payload["version"] == __version__
    assert payload["os"] in ("linux", "windows")
    assert isinstance(payload["docker"], bool)


def test_the_ping_carries_an_id_of_its_own_never_the_listings(config, net):
    """plans/68 NET-16: the anonymous count must not share an ID with the
    public listing. The ping's ID is made once, kept outside config.json, and
    goes with `forget`."""
    site_ops.send_ping(config)
    site_ops.send_ping(config)
    first, second = (call.json["install_id"] for call in net.calls)

    assert first != config.site_install_id
    assert first == second == site_ops.ping_id(config)
    assert len(first) == 32 and all(c in "0123456789abcdef" for c in first)
    # Not a config field, so not in config.json or a bundle.
    assert first not in json.dumps(dataclasses.asdict(config))

    site_ops.forget(config)
    assert site_ops.ping_id(config) == ""
    _switch(config, "site_ping", True)
    site_ops.send_ping(config)
    assert net.calls[-1].json["install_id"] not in ("", first)


def test_the_listing_payload_is_exactly_its_three_fields(config):
    """No name, map, player count or version: the backend reads those over A2S,
    and a client that could post its own player count could post any number."""
    payload = site_ops.listing_payload(config)

    assert set(payload) == {"install_id", "query_port", "address"}
    assert payload["install_id"] == config.site_install_id
    assert payload["query_port"] == 27016
    assert payload["address"] is None


def test_the_listing_reads_the_query_port_and_address_it_sends(config):
    cfg = config.server_cfg_path
    cfg.write_text(cfg.read_text() + "steamQueryPort = 27020;\n")
    config.site_address = "play.example.test"

    payload = site_ops.listing_payload(config)

    assert payload["query_port"] == 27020
    assert payload["address"] == "play.example.test"


# ---------------------------------------------------------------------------
# The switches, the IDs and the settings registry
# ---------------------------------------------------------------------------


def test_an_install_that_never_opts_in_has_no_ids():
    config = DayzConfig()
    assert not config.site_ping and not config.site_listing
    assert config.site_install_id == "" and config.site_secret == ""


@pytest.mark.parametrize("key", ["site_ping", "site_listing"])
def test_the_first_turn_on_generates_both_ids(key):
    config = DayzConfig()
    _switch(config, key, True)

    assert getattr(config, key) is True
    assert len(config.site_install_id) == 32
    assert len(config.site_secret) >= 40


def test_the_ids_survive_the_switches_going_off_and_on():
    config = DayzConfig()
    _switch(config, "site_ping", True)
    ids = (config.site_install_id, config.site_secret)

    _switch(config, "site_ping", False)
    _switch(config, "site_listing", True)

    assert (config.site_install_id, config.site_secret) == ids


def test_the_ids_are_not_settings():
    """GET /api/settings serves every registry field; the secret must never
    reach a browser."""
    assert "site_install_id" not in _BY_KEY
    assert "site_secret" not in _BY_KEY


def test_the_endpoint_is_fixed_to_site_and_a_fresh_install_sends_nothing(
    sample_config, net, running
):
    """The backend is live (plans/57 C) and fixed in code, not a setting. Both
    switches still default to off, so an install that never opts in sends
    nothing."""
    assert site_ops.ENDPOINT == "https://zedstead.com"
    assert "site_endpoint" not in _BY_KEY
    assert not (sample_config.site_ping or sample_config.site_listing)

    site_ops.heartbeat(sample_config)

    assert net.calls == []


def test_every_sent_field_is_named_in_its_switch():
    ping = _BY_KEY["site_ping"].description
    for words in ("install ID", "version", "OS", "Docker"):
        assert words in ping, words
    listing = _BY_KEY["site_listing"].description
    for words in ("install ID", "query port", "site_address", "key", "IP address", "game port"):
        assert words in listing, words


# ---------------------------------------------------------------------------
# Nothing is sent unless a switch says so
# ---------------------------------------------------------------------------


def test_nothing_is_sent_with_the_switches_off(config, net):
    config.site_ping = False
    config.site_listing = False

    assert site_ops.send_ping(config) == (False, "site_ping is off")
    assert site_ops.publish(config) == (False, "site_listing is off")
    assert net.calls == []


def test_turning_a_switch_on_or_off_sends_nothing_by_itself(sample_config, net):
    _switch(sample_config, "site_listing", True)
    _switch(sample_config, "site_listing", False)
    assert net.calls == []


# ---------------------------------------------------------------------------
# What goes over the wire
# ---------------------------------------------------------------------------


def test_the_secret_is_in_the_header_and_never_in_a_body(config, net):
    secret = config.site_secret

    site_ops.send_ping(config)
    site_ops.publish(config)
    site_ops.unpublish(config)

    ping, put, delete = net.calls
    assert (ping.method, ping.url) == ("POST", f"{ENDPOINT}/v1/ping")
    assert "headers" not in vars(ping)
    assert (put.method, put.url) == ("PUT", f"{ENDPOINT}/v1/servers/{config.site_install_id}")
    assert (delete.method, delete.url) == ("DELETE", put.url)
    for call in (put, delete):
        assert call.headers == {"Authorization": f"Bearer {secret}"}
    for call in (ping, put):
        assert secret not in json.dumps(call.json)
    assert all(call.timeout == site_ops.TIMEOUT for call in net.calls)


@pytest.mark.parametrize(
    "error", [requests.ConnectionError("connection refused"), requests.Timeout("timed out")]
)
def test_a_backend_that_is_down_is_a_result_not_an_exception(config, net, error):
    net.error = error

    for call in (site_ops.send_ping, site_ops.publish, site_ops.unpublish):
        ok, message = call(config)
        assert ok is False
        assert message.startswith(f"could not reach {ENDPOINT}")


def test_a_refusal_is_reported_verbatim(config, net):
    """The backend writes its 422s for `zsm listing status` to show."""
    reason = "your query port 27016 did not answer from the internet — is it open in the firewall?"
    net.response = FakeResponse(422, {"detail": reason})

    assert site_ops.publish(config) == (False, f"HTTP 422: {reason}")
    last = site_ops.read_state(config)["last_result"]
    assert (last["action"], last["ok"], last["message"]) == ("publish", False, f"HTTP 422: {reason}")


def test_a_plain_text_refusal_is_reported_too(config, net):
    net.response = FakeResponse(503, text="Service Unavailable\n")
    assert site_ops.send_ping(config) == (False, "HTTP 503: Service Unavailable")


def test_removing_a_listing_that_is_not_there_counts_as_done(config, net):
    net.response = FakeResponse(404)
    assert site_ops.unpublish(config) == (True, "removed from the list")


# ---------------------------------------------------------------------------
# The heartbeat's tick
# ---------------------------------------------------------------------------


def test_the_ping_goes_at_most_once_a_day(config, net, running):
    running.value = False
    site_ops.heartbeat(config)
    first = site_ops.read_state(config)["last_ping"]

    site_ops.heartbeat(config, now=first + site_ops.PING_INTERVAL - 1)
    assert [c.method for c in net.calls] == ["POST"]

    site_ops.heartbeat(config, now=first + site_ops.PING_INTERVAL)
    assert [c.method for c in net.calls] == ["POST", "POST"]


def test_a_failed_ping_still_waits_a_day(config, net, running):
    """"At most once a day" holds for a backend that is down, too."""
    running.value = False
    net.error = requests.ConnectionError("refused")
    site_ops.heartbeat(config)
    site_ops.heartbeat(config)
    assert len(net.calls) == 1


def test_the_listing_is_refreshed_only_while_the_server_runs(config, net, running):
    config.site_ping = False

    running.value = False
    site_ops.heartbeat(config)
    assert net.calls == []

    running.value = True
    site_ops.heartbeat(config)
    site_ops.heartbeat(config)
    assert [c.method for c in net.calls] == ["PUT", "PUT"]
    assert site_ops.read_state(config)["last_publish"]


def test_turning_the_listing_off_removes_it(config, net, running):
    config.site_ping = False
    site_ops.heartbeat(config)
    assert [c.method for c in net.calls] == ["PUT"]

    _switch(config, "site_listing", False)
    site_ops.heartbeat(config)
    site_ops.heartbeat(config)  # once removed, it is not removed again

    assert [c.method for c in net.calls] == ["PUT", "DELETE"]
    assert site_ops.read_state(config)["last_publish"] is None


def test_a_listing_never_published_is_not_deleted(config, net, running):
    config.site_ping = False
    _switch(config, "site_listing", False)
    site_ops.heartbeat(config)
    assert net.calls == []


# ---------------------------------------------------------------------------
# forget
# ---------------------------------------------------------------------------


def test_forget_removes_the_listing_and_clears_both_ids(config, net):
    site_ops.publish(config)

    assert site_ops.forget(config) == (True, "removed from the list")

    assert net.calls[-1].method == "DELETE"
    assert config.site_install_id == "" and config.site_secret == ""
    assert not config.site_ping and not config.site_listing
    assert not site_ops.state_path(config).exists()


def test_forget_clears_the_ids_even_when_the_removal_fails(config, net):
    net.error = requests.ConnectionError("refused")

    ok, _ = site_ops.forget(config)

    assert ok is False
    assert config.site_install_id == "" and config.site_secret == ""


# ---------------------------------------------------------------------------
# `zsm web`'s loop
# ---------------------------------------------------------------------------


def test_a_site_change_wakes_the_heartbeat_and_nothing_else_does(sample_config):
    heartbeat = SiteHeartbeat(sample_config)

    heartbeat.set_config(dataclasses.replace(sample_config, backup_max_count=9))
    assert not heartbeat._wake.is_set()

    heartbeat.set_config(dataclasses.replace(sample_config, site_listing=True))
    assert heartbeat._wake.is_set()


async def test_a_switch_turned_on_ticks_now_not_after_the_first_delay(sample_config, monkeypatch):
    import asyncio

    heartbeat = SiteHeartbeat(sample_config)
    ticked = asyncio.Event()
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(heartbeat, "tick", lambda: loop.call_soon_threadsafe(ticked.set))
    heartbeat.start()
    try:
        await asyncio.sleep(0)
        assert not ticked.is_set()  # FIRST_TICK_DELAY is a minute

        heartbeat.set_config(dataclasses.replace(sample_config, site_ping=True))
        await asyncio.wait_for(ticked.wait(), timeout=5)
    finally:
        await heartbeat.stop()


def test_the_tick_honours_an_opt_out_made_from_the_cli(config, net, running):
    """`zsm settings set site_listing false` in a shell writes config.json;
    the web's in-memory copy still says on."""
    config.site_ping = False
    config.save()
    heartbeat = SiteHeartbeat(config)
    heartbeat.tick()
    assert [c.method for c in net.calls] == ["PUT"]

    on_disk = DayzConfig.load()
    on_disk.site_listing = False
    on_disk.save()
    heartbeat.tick()

    assert [c.method for c in net.calls] == ["PUT", "DELETE"]


def test_the_heartbeat_runs_without_the_notifier(sample_config):
    """`web_notifier_enabled` stops duplicate Discord posts; it must not
    withdraw a listing the admin opted into."""
    from app.web import server

    sample_config.web_notifier_enabled = False
    app = server.create_app(sample_config)
    state = app.state.ctx

    with TestClient(app):
        assert state.notifier is None
        assert state.site_heartbeat is not None
        assert not state.site_heartbeat._task.done()

    assert state.site_heartbeat is None
