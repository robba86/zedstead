"""GET /api/expansion-ai — the report behind Expansion AI's card (plans/64)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests.conftest import write_test_mods

from tests.test_expansion_ai import (  # the same fixture install
    _patrol,
    _settings,
    _write,
    eai,  # noqa: F401 - pytest fixture
)

PASSWORD = "expansion-ai-test"


@pytest.fixture()
def client(eai):
    from app.web import server
    from app.web.auth import hash_password

    eai.web_password_hash = hash_password(PASSWORD)
    c = TestClient(server.create_app(eai))
    assert c.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return c


def test_the_report_route(client, eai):
    body = client.get("/api/expansion-ai").json()
    assert body["installed"] is True and body["mod"] == "@DayZ-Expansion-AI"
    assert (body["patrols"], body["object_patrols"]) == (5, 3)
    assert body["factions"] == ["East", "Guards", "West"] and body["findings"] == []

    _write(eai, _settings(Patrols=[_patrol(Faction="Bandits")]))
    ids = [f["id"] for f in client.get("/api/expansion-ai").json()["findings"]]
    assert ids == ["expansion_ai_unknown_faction"]

    write_test_mods(eai, ['@CF'])
    assert client.get("/api/expansion-ai").json() == {"installed": False}


def test_the_report_needs_a_session(eai):
    from app.web import server

    assert TestClient(server.create_app(eai)).get("/api/expansion-ai").status_code == 401
