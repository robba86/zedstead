"""Tests for the web field-help sanitizer and its wiring into editor payloads
(app/web/editors/_help.py + the load() adapters + /api/mods/help)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.web.editors._help import field_help, inject_help, strip_markup

PASSWORD = "testpass123"


# --- sanitizer -------------------------------------------------------------


def test_strip_markup_removes_rich_tags():
    assert strip_markup("[bold]maxPlayers[/bold] [dim]default: 60[/dim]") == "maxPlayers default: 60"


def test_strip_markup_flattens_action_link():
    src = "[@click=screen.open_run_command][bold underline]Run command[/bold underline][/]"
    assert strip_markup(src) == "Run command"


def test_strip_markup_preserves_newlines():
    out = strip_markup("Maximum players\n\n[bold]maxPlayers[/bold]")
    assert out == "Maximum players\n\nmaxPlayers"


def test_strip_markup_empty_and_none():
    assert strip_markup("") == ""
    assert strip_markup(None) == ""


def test_field_help_missing_key_returns_empty():
    assert field_help({"a": "x"}, "b") == ""


def test_field_help_prefixed_and_dotted_fallback():
    desc = {"leaf": "help text"}
    assert field_help(desc, "state:leaf") == "help text"
    assert field_help(desc, "a.b.leaf") == "help text"


# --- inject_help -----------------------------------------------------------


def test_inject_help_form_fills_missing_only():
    payload = {
        "kind": "form",
        "sections": [{"title": "S", "fields": [{"key": "a"}, {"key": "b", "help": "keep"}]}],
    }
    inject_help(payload, {"a": "help-a", "b": "override"})
    fields = payload["sections"][0]["fields"]
    assert fields[0]["help"] == "help-a"
    assert fields[1]["help"] == "keep"  # existing help is not overwritten


def test_inject_help_table_columns():
    payload = {"kind": "table", "columns": [{"key": "init"}, {"key": "name"}], "rows": []}
    inject_help(payload, {"init": "init flag"})
    assert payload["columns"][0]["help"] == "init flag"
    assert "help" not in payload["columns"][1]  # no source -> untouched


# --- integration: editor payloads carry help -------------------------------


@pytest.fixture()
def client(sample_config):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    tclient = TestClient(server.create_app(sample_config))
    tclient.post("/api/auth/login", json={"password": PASSWORD})
    return tclient


def test_serverdz_form_fields_have_help(client):
    body = client.get("/api/configs/serverdz").json()
    fields = {f["key"]: f for s in body["sections"] for f in s["fields"]}
    assert fields["maxPlayers"]["help"]  # non-empty
    assert "Maximum number of players" in fields["maxPlayers"]["help"]
    # sanitized: no Rich markup leaks through
    assert "[bold]" not in fields["maxPlayers"]["help"]


def test_battleye_rcon_fields_have_help(client):
    body = client.get("/api/configs/battleye").json()
    fields = {f["key"]: f for s in body["sections"] for f in s["fields"]}
    assert fields["RConPassword"]["help"]


def test_mods_help_endpoint(client):
    body = client.get("/api/mods/help").json()
    assert "Install" in body["tabs"] and body["tabs"]["Install"]
    assert "[bold]" not in body["tabs"]["Install"]  # markup stripped
    assert body["actions"]["uninstall"]
