"""Tests for app/web/same_origin.py — a write must come from a page this
panel served.

SameSite=lax keeps another *site* from sending the session cookie with a
POST, but a page on another port of the same host is same-site, so the
browser sends it. These tests stand in for that page by sending its
``Origin``; TestClient's own host is ``testserver``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.web.auth import hash_password
from app.web.same_origin import is_same_origin

PASSWORD = "testpass123"

FOREIGN = (
    "http://evil.example",
    # Another port on this host: same-site, so the cookie would go along.
    "http://testserver:9999",
    "null",
)


@pytest.fixture()
def app(sample_config):
    from app.web import server

    sample_config.web_password_hash = hash_password(PASSWORD)
    return server.create_app(sample_config)


@pytest.fixture()
def client(app):
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return c


@pytest.mark.parametrize("origin", FOREIGN)
def test_a_write_from_another_origin_is_refused(client, origin):
    """Logout takes no body, so a plain HTML form elsewhere could send it.
    Refused before the route runs: the session is still there afterwards."""
    resp = client.post("/api/auth/logout", headers={"origin": origin})
    assert resp.status_code == 403
    assert "another site or port" in resp.json()["detail"]
    assert client.get("/api/auth/me").status_code == 200


@pytest.mark.parametrize("origin", FOREIGN)
def test_an_admin_write_from_another_origin_changes_nothing(client, app, origin):
    resp = client.post(
        "/api/users",
        json={"name": "mallory", "password": "x", "role": "admin"},
        headers={"origin": origin},
    )
    assert resp.status_code == 403
    assert app.state.ctx.users.get("mallory") is None


@pytest.mark.parametrize("origin", FOREIGN)
def test_signing_in_from_another_origin_is_refused(app, origin):
    """No session is opened in the visitor's browser by a page elsewhere."""
    resp = TestClient(app).post(
        "/api/auth/login", json={"password": PASSWORD}, headers={"origin": origin}
    )
    assert resp.status_code == 403
    assert "set-cookie" not in resp.headers


@pytest.mark.parametrize(
    "headers",
    [
        {},  # not a browser: curl, a script
        {"origin": "http://testserver"},  # the panel's own page
        {"origin": "https://panel.example.com", "x-forwarded-host": "panel.example.com"},
    ],
)
def test_writes_from_the_panel_and_from_non_browsers_pass(client, headers):
    assert client.post("/api/auth/logout", headers=headers).status_code == 200


def test_reads_are_not_checked(client):
    assert client.get("/api/auth/me", headers={"origin": "http://evil.example"}).status_code == 200


def test_the_rule_compares_host_and_port_and_refuses_what_it_cannot_parse():
    assert is_same_origin({"origin": "http://a:1", "host": "a:1"})
    assert not is_same_origin({"origin": "http://a:2", "host": "a:1"})
    assert not is_same_origin({"origin": "http://[::1", "host": "a:1"})
