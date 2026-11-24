"""Tests for /api/xml (app/web/routers/xmltools.py) — the Format/Check buttons."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

PASSWORD = "testpass123"


@pytest.fixture()
def client(sample_config):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    tclient = TestClient(server.create_app(sample_config))
    tclient.post("/api/auth/login", json={"password": PASSWORD})
    return tclient


def test_requires_session(sample_config):
    from app.web import server

    anon = TestClient(server.create_app(sample_config))
    assert anon.post("/api/xml/check", json={"content": "<a/>"}).status_code == 401
    assert anon.post("/api/xml/format", json={"content": "<a/>"}).status_code == 401


def test_check_accepts_valid_xml(client):
    body = client.post(
        "/api/xml/check", json={"content": '<types><type name="X"/></types>'},
    ).json()

    assert body["ok"] is True
    assert body["root_tag"] == "types"
    assert body["elements"] == 2
    assert body["error"] is None


def test_check_reports_line_column_hint_and_summary(client):
    body = client.post(
        "/api/xml/check", json={"content": "<types>\n<type>\n</typo>\n</types>"},
    ).json()

    assert body["ok"] is False
    assert body["error"]["line"] == 3
    assert body["error"]["column"] > 0
    assert body["error"]["hint"] == "closing tag doesn't match opening tag"
    assert body["error"]["summary"].startswith("Line 3, column ")


def test_check_rejects_a_fragment_unless_fragment_is_set(client):
    fragment = '<type name="X"/><type name="Y"/>'
    assert client.post("/api/xml/check", json={"content": fragment}).json()["ok"] is False

    body = client.post("/api/xml/check", json={"content": fragment, "fragment": True}).json()
    assert body["ok"] is True and body["fragment"] is True and body["root_tag"] == ""


def test_format_indents_and_reports_changed(client):
    body = client.post("/api/xml/format", json={"content": "<a><b/></a>"}).json()

    assert body["ok"] is True and body["changed"] is True
    assert body["content"] == "<a>\n\t<b />\n</a>\n"

    again = client.post("/api/xml/format", json={"content": body["content"]}).json()
    assert again["changed"] is False


def test_format_keeps_comments_and_the_authors_declaration(client):
    source = (
        "<?xml version='1.0' encoding='utf-8'?>\n"
        "<eventposdef><!-- START MOD: @Bikes --><event name='E'/>"
        "<!-- END MOD: @Bikes --></eventposdef>"
    )
    body = client.post("/api/xml/format", json={"content": source}).json()

    assert body["content"].startswith("<?xml version='1.0' encoding='utf-8'?>\n")
    assert "<!-- START MOD: @Bikes -->" in body["content"]
    assert "<!-- END MOD: @Bikes -->" in body["content"]


def test_format_of_a_fragment_stays_rootless(client):
    body = client.post(
        "/api/xml/format", json={"content": '<type name="X"/><type name="Y"/>'},
    ).json()

    assert body["ok"] is True and body["root_tag"] == ""
    assert body["content"] == '<type name="X" />\n<type name="Y" />\n'


def test_broken_xml_is_200_with_the_untouched_input(client):
    """Not a 400: malformed XML is the expected case for these endpoints, and a
    400 would go through the frontend's throw path instead of the error strip."""
    broken = "<types>\n<type>\n</typo>"
    resp = client.post("/api/xml/format", json={"content": broken})

    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert resp.json()["content"] == broken


def test_oversized_content_is_refused_without_parsing(client):
    from app.web.routers import xmltools

    huge = "<a>" + ("x" * (xmltools.MAX_XML_BYTES + 1)) + "</a>"
    body = client.post("/api/xml/check", json={"content": huge}).json()

    assert body["ok"] is False and "too large" in body["error"]["hint"]
