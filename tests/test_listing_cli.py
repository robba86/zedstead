"""Tests for `zsm listing` (app/commands/listing.py, plans/57 B5)."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest
import requests
from click.testing import CliRunner

from app.commands.listing import listing_group
from app.ops import site_ops
from app.shared.config import DayzConfig


@pytest.fixture()
def net(monkeypatch):
    """No test reaches the network; every request is recorded."""
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url))
        return SimpleNamespace(ok=True, status_code=204, text="")

    monkeypatch.setattr(
        site_ops,
        "requests",
        SimpleNamespace(request=request, RequestException=requests.RequestException),
    )
    return calls


def _json_blocks(output: str) -> list[dict]:
    return [json.loads(block) for block in re.findall(r"^\{\n.*?^\}$", output, re.S | re.M)]


def test_status_works_before_zsm_init(sample_config, net):
    """No config.json at all (a sandboxed HOME): both payloads, no config error."""
    assert not DayzConfig.load_or_none()

    result = CliRunner().invoke(listing_group, ["status"])

    assert result.exit_code == 0, result.output
    ping, listing = _json_blocks(result.output)
    assert set(ping) == {"install_id", "version", "os", "docker"}
    assert set(listing) == {"install_id", "query_port", "address"}
    assert "POST https://zedstead.com/v1/ping" in result.output  # the fixed endpoint
    assert net == []


def test_status_prints_the_exact_payloads_and_never_the_secret(sample_config, net, monkeypatch):
    monkeypatch.setattr(site_ops, "ENDPOINT", "https://api.example.test")
    site_ops.set_switch(sample_config, "site_listing", True)
    sample_config.save()

    result = CliRunner().invoke(listing_group, ["status"])

    assert result.exit_code == 0, result.output
    ping, listing = _json_blocks(result.output)
    # The ping's own ID is made at its first send, and status does not make it.
    assert ping == {**site_ops.ping_payload(sample_config), "install_id": "<made at the first ping>"}
    assert site_ops.ping_id(sample_config) == ""
    assert listing == site_ops.listing_payload(sample_config)
    assert "Bearer <set>" in result.output
    assert sample_config.site_secret not in result.output
    assert net == []


def test_status_shows_the_last_result(sample_config, net, monkeypatch):
    monkeypatch.setattr(site_ops, "ENDPOINT", "https://api.example.test")
    site_ops.set_switch(sample_config, "site_ping", True)
    sample_config.save()
    site_ops.send_ping(sample_config)

    result = CliRunner().invoke(listing_group, ["status"])

    assert "ping ok" in result.output
    assert "ping sent" in result.output


def test_forget_removes_the_listing_and_saves_the_cleared_ids(sample_config, net, monkeypatch):
    monkeypatch.setattr(site_ops, "ENDPOINT", "https://api.example.test")
    site_ops.set_switch(sample_config, "site_listing", True)
    sample_config.save()

    result = CliRunner().invoke(listing_group, ["forget"])

    assert result.exit_code == 0, result.output
    assert net == [("DELETE", f"https://api.example.test/v1/servers/{sample_config.site_install_id}")]
    saved = DayzConfig.load()
    assert saved.site_install_id == "" and saved.site_secret == ""
    assert not saved.site_ping and not saved.site_listing
