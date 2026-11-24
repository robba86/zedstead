"""Tests for the access log and the exposure banner (TODO B6).

Two claims carry the feature and both are asserted below: a failed login is
recorded (it is the one worth seeing, and before this it left no trace at all
— `zsm web` runs uvicorn at `warning`, so the 401 was never printed), and
logging never costs anyone the ability to log in.
"""

from __future__ import annotations

import json
import os
import stat

import pytest
from fastapi.testclient import TestClient

from app.web import access_log

PASSWORD = "testpass123"


@pytest.fixture()
def log_path(tmp_path, monkeypatch):
    """Point the log at tmp by moving CONFIG_DIR, which is what the real code
    resolves per call — proving the module has no import-time constant to go
    stale (the trap listed in app/web/CLAUDE.md)."""
    monkeypatch.setattr("app.shared.paths.CONFIG_DIR", tmp_path)
    return tmp_path / access_log.LOG_NAME


@pytest.fixture()
def client(sample_config, log_path):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    return TestClient(server.create_app(sample_config))


def _entries(path):
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ---------------------------------------------------------------------------
# the log
# ---------------------------------------------------------------------------


def test_the_path_follows_config_dir_at_call_time(tmp_path, monkeypatch):
    monkeypatch.setattr("app.shared.paths.CONFIG_DIR", tmp_path)
    assert access_log.access_log_path() == tmp_path / "access_log.jsonl"


@pytest.fixture()
def umask_022():
    old = os.umask(0o022)
    yield
    os.umask(old)


def test_the_log_is_created_owner_only(tmp_path, umask_022):
    """plans/68 SEC-8: client addresses, account names and user agents."""
    path = tmp_path / "access_log.jsonl"

    access_log.record("login", "203.0.113.5", path=path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert _entries(path)[0]["ip"] == "203.0.113.5"


def test_an_existing_log_others_can_read_is_tightened(tmp_path, umask_022):
    path = tmp_path / "access_log.jsonl"
    path.write_text('{"event": "login"}\n', encoding="utf-8")
    path.chmod(0o644)

    access_log.record("logout", "203.0.113.5", path=path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [e["event"] for e in _entries(path)] == ["login", "logout"]


def test_on_windows_the_log_is_appended_as_before(tmp_path, monkeypatch):
    """chmod on Windows only sets the read-only flag; nothing is changed."""
    monkeypatch.setattr(access_log, "IS_WINDOWS", True)
    monkeypatch.setattr(access_log.os, "fchmod", lambda *a: pytest.fail("fchmod on Windows"),
                        raising=False)
    path = tmp_path / "access_log.jsonl"

    access_log.record("login", "203.0.113.5", path=path)

    assert [e["event"] for e in _entries(path)] == ["login"]


def test_a_successful_login_is_recorded(client, log_path):
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    entries = _entries(log_path)
    assert [e["event"] for e in entries] == ["login"]
    assert entries[0]["timestamp"].endswith("Z")
    assert "ip" in entries[0]


def test_a_failed_login_is_recorded_and_the_password_is_not(client, log_path):
    """The attempt nobody made is the one worth seeing; the secret in it is
    never worth keeping."""
    resp = client.post("/api/auth/login", json={"password": "hunter2-wrong"})
    assert resp.status_code == 401
    entries = _entries(log_path)
    assert [e["event"] for e in entries] == ["login_failed"]
    assert "hunter2-wrong" not in log_path.read_text(encoding="utf-8")


def test_a_rate_limited_attempt_is_recorded_too(client, log_path, sample_config):
    """A burst that trips the limiter is the single most interesting thing
    this log can show; dropping those entries would hide it."""
    app_state = client.app.state.ctx
    for _ in range(12):
        client.post("/api/auth/login", json={"password": "no"})
    # A blank name with one account fills that account's bucket (AUTH-17).
    assert app_state.login_limiter.is_limited("admin")
    resp = client.post("/api/auth/login", json={"password": "no"})
    assert resp.status_code == 429

    events = [e["event"] for e in _entries(log_path)]
    assert events.count("login_failed") == 13


def test_logging_out_is_recorded(client, log_path):
    client.post("/api/auth/login", json={"password": PASSWORD})
    client.post("/api/auth/logout")
    assert [e["event"] for e in _entries(log_path)] == ["login", "logout"]


def test_an_unwritable_log_never_blocks_a_login(sample_config, tmp_path, monkeypatch):
    """This sits in the login path. A full disk costs a log line, not the
    ability to get in."""
    from app.web import server
    from app.web.auth import hash_password

    # A real OSError rather than a patched one: mkdir under a path whose
    # parent is a regular file raises NotADirectoryError.
    blocker = tmp_path / "blocker"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        access_log, "access_log_path", lambda: blocker / "sub" / "log.jsonl"
    )
    sample_config.web_password_hash = hash_password(PASSWORD)
    tclient = TestClient(server.create_app(sample_config))
    assert tclient.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200


def test_the_user_agent_is_bounded(log_path):
    access_log.record("login", "1.2.3.4", "A" * 5000, path=log_path)
    assert len(_entries(log_path)[0]["user_agent"]) == 200


def test_the_file_is_trimmed_rather_than_grown_without_limit(log_path, monkeypatch):
    """An exposed panel under a login flood must not fill the disk."""
    monkeypatch.setattr(access_log, "MAX_BYTES", 500)
    monkeypatch.setattr(access_log, "MAX_ENTRIES", 5)
    for i in range(50):
        access_log.record("login_failed", f"10.0.0.{i}", path=log_path)
    entries = _entries(log_path)
    assert len(entries) <= 5
    # The newest are the ones kept.
    assert entries[-1]["ip"] == "10.0.0.49"


def test_reading_returns_newest_first_and_skips_junk(log_path):
    access_log.record("login", "1.1.1.1", path=log_path)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write("not json\n")
    access_log.record("logout", "2.2.2.2", path=log_path)

    entries = access_log.read_recent(path=log_path)
    assert [e["ip"] for e in entries] == ["2.2.2.2", "1.1.1.1"]


def test_a_forged_forwarded_header_is_not_believed(client, log_path):
    """`request.client` is already rewritten from X-Forwarded-For when the
    proxy is trusted. Reading the header here as well would let any caller
    write whatever address it liked into the log this exists to pin down."""
    client.post(
        "/api/auth/login",
        json={"password": PASSWORD},
        headers={"X-Forwarded-For": "9.9.9.9"},
    )
    assert _entries(log_path)[0]["ip"] != "9.9.9.9"


# ---------------------------------------------------------------------------
# the user column (plans/43 step 6)
# ---------------------------------------------------------------------------


@pytest.fixture()
def accounts(client):
    """The store behind `client`, already migrated to a single `admin`."""
    store = client.app.state.ctx.users
    store.add("mandy", "modpass123", "moderator")
    return store


def test_a_successful_login_names_the_account(client, accounts, log_path):
    assert client.post(
        "/api/auth/login", json={"username": "mandy", "password": "modpass123"}
    ).status_code == 200
    assert _entries(log_path)[0]["user"] == "mandy"


def test_a_failed_login_on_a_real_account_names_it(client, accounts, log_path):
    """The signal worth having: somebody is hammering an account that exists."""
    assert client.post(
        "/api/auth/login", json={"username": "mandy", "password": "wrong"}
    ).status_code == 401
    entry = _entries(log_path)[0]
    assert entry["event"] == "login_failed"
    assert entry["user"] == "mandy"
    assert "unknown_user" not in entry


def test_a_password_typed_into_the_username_box_is_not_written_down(
    client, accounts, log_path
):
    """The reason an unknown name is never recorded verbatim.

    The two boxes are next to each other on the form, and this file is served
    to a browser by `GET /api/security/access-log`. The attempt is still
    recorded — as one on a name nobody has."""
    resp = client.post(
        "/api/auth/login",
        json={"username": "hunter2-my-real-password", "password": ""},
    )
    assert resp.status_code == 401
    raw = log_path.read_text(encoding="utf-8")
    assert "hunter2-my-real-password" not in raw
    entry = _entries(log_path)[0]
    assert entry["event"] == "login_failed"
    assert entry["user"] == ""
    assert entry["unknown_user"] is True


def test_the_stored_spelling_is_logged_not_the_typed_one(client, accounts, log_path):
    """Otherwise one account reads as three: Mandy, mandy, MANDY.

    Both paths, because they name the account from different places — a
    success uses what `authenticate` returned, a failure uses the lookup in
    `_attempted_identity`. Checking only the success left that lookup free to
    echo the typed string back; mutation testing is what said so.
    """
    client.post("/api/auth/login", json={"username": "MANDY", "password": "modpass123"})
    assert _entries(log_path)[0]["user"] == "mandy"

    client.post("/api/auth/login", json={"username": "MaNdY", "password": "wrong"})
    failure = _entries(log_path)[-1]
    assert failure["event"] == "login_failed"
    assert failure["user"] == "mandy"


def test_a_login_on_an_install_with_no_accounts_still_names_admin(client, log_path):
    """The fallback path. `/api/auth/me` calls that session admin, so the log
    must not call it nobody."""
    client.app.state.ctx.users.path.unlink()
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    assert _entries(log_path)[0]["user"] == "admin"


def test_logging_out_names_the_session_not_the_request(client, accounts, log_path):
    client.post("/api/auth/login", json={"username": "mandy", "password": "modpass123"})
    client.post("/api/auth/logout", json={"username": "somebody-else"})
    logout = [e for e in _entries(log_path) if e["event"] == "logout"][0]
    assert logout["user"] == "mandy"


def test_the_user_column_is_bounded(log_path):
    """Belt and braces: the caller hands over a name it looked up, but a log
    field that trusts its input is how the User-Agent bound came to exist."""
    access_log.record("login", "1.2.3.4", user="B" * 5000, path=log_path)
    assert len(_entries(log_path)[0]["user"]) == 64


def test_a_line_from_before_accounts_is_read_without_a_user(log_path):
    """Every reader has to tolerate the missing key rather than backfill it."""
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"timestamp": "2026-01-01T00:00:00Z", "event": "login",
                             "ip": "1.1.1.1", "user_agent": "old"}) + "\n")
    entry = access_log.read_recent(path=log_path)[0]
    assert "user" not in entry
    assert entry["event"] == "login"


# ---------------------------------------------------------------------------
# the endpoints
# ---------------------------------------------------------------------------


def test_the_access_log_needs_a_session(client):
    assert client.get("/api/security/access-log").status_code == 401


def test_the_access_log_endpoint_returns_what_was_recorded(client, log_path):
    client.post("/api/auth/login", json={"password": PASSWORD})
    body = client.get("/api/security/access-log").json()
    assert body["entries"][0]["event"] == "login"
    assert body["path"].endswith("access_log.jsonl")


# ---------------------------------------------------------------------------
# the exposure banner
# ---------------------------------------------------------------------------


def _exposure(client, host, tls):
    client.app.state.ctx.bind_host = host
    client.app.state.ctx.tls_enabled = tls
    return client.get("/api/security").json()


def test_a_loopback_bind_is_not_exposed(client):
    client.post("/api/auth/login", json={"password": PASSWORD})
    for host in ("127.0.0.1", "::1", "localhost"):
        assert _exposure(client, host, False)["exposed"] is False, host


def test_an_off_box_bind_without_tls_is_exposed(client):
    client.post("/api/auth/login", json={"password": PASSWORD})
    body = _exposure(client, "0.0.0.0", False)
    assert body["exposed"] is True
    assert "no TLS" in body["reason"]


def test_tls_clears_it_even_on_every_interface(client):
    """At that point the panel is deliberately published and protected, which
    is a choice rather than an accident."""
    client.post("/api/auth/login", json={"password": PASSWORD})
    assert _exposure(client, "0.0.0.0", True)["exposed"] is False


def test_an_app_nobody_started_claims_nothing(client):
    """`create_app` on its own knows neither the bind address nor the certs.
    Warning on that default would train people to ignore the banner."""
    client.post("/api/auth/login", json={"password": PASSWORD})
    body = _exposure(client, "", False)
    assert body["exposed"] is False
    assert body["reason"] == ""


def test_start_server_is_what_records_it(sample_config, monkeypatch):
    """The banner is only as good as this assignment; nothing else in the app
    is told how `zsm web` was launched."""
    import app.web.server as server_mod

    captured = {}

    class _FakeServer:
        def __init__(self, cfg):
            captured["cfg"] = cfg

        async def serve(self):
            return None

    import uvicorn

    monkeypatch.setattr(uvicorn, "Server", _FakeServer)
    created = {}
    real_create = server_mod.create_app

    def spy_create(config):
        app = real_create(config)
        created["app"] = app
        return app

    monkeypatch.setattr(server_mod, "create_app", spy_create)

    import asyncio

    asyncio.run(
        server_mod.start_server(
            sample_config, host="0.0.0.0", port=8433, ssl_certfile=None, ssl_keyfile=None
        )
    )
    ctx = created["app"].state.ctx
    assert ctx.bind_host == "0.0.0.0"
    assert ctx.tls_enabled is False


def test_every_event_has_a_label_on_the_access_log_card():
    """A wrong second factor showed as a raw grey "Login_totp_failed" and was
    left out of the failed count."""
    import re
    from pathlib import Path

    from app.web.access_log import EVENTS

    card = (Path(__file__).resolve().parents[1] / "webui/src/components/AccessLogCard.jsx").read_text(
        encoding="utf-8"
    )
    labels = re.search(r"const LABELS = \{(.*?)\n\}", card, re.S).group(1)
    assert set(EVENTS) == set(re.findall(r"^\s*([a-z_]+):", labels, re.M))
    failed = re.search(r"const FAILED_EVENTS = new Set\(\[(.*?)\]\)", card).group(1)
    assert set(re.findall(r"'([a-z_]+)'", failed)) == {e for e in EVENTS if e.endswith("failed")}
