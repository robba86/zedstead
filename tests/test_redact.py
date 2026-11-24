"""Tests for app/web/redact.py — the passwords a non-admin is not served.

Found by the plans/43 browser pass (2026 Q3): a viewer opening Configs →
BattlEye read the live RCon password in plain text, because only the `/raw`
and `/download` routes were admin-only and the structured editor serves the
same value. RCon is worth more than a moderator — kick, ban and #shutdown with
no role check — so this leak made every viewer a super-admin.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.shared import config_versions
from app.web import editors, redact
from app.web.auth import hash_password

PASSWORD = "testpass123"
RCON = "mypass"  # tests/conftest.py's beserver_x64.cfg
JOIN = "join-secret-1"
ADMIN_PW = "admin-secret-2"


@pytest.fixture()
def app(sample_config):
    from app.web import server

    cfg = sample_config.server_cfg_path
    cfg.write_text(
        'hostname = "Test Server";\n'
        f'password = "{JOIN}";\n'
        f'passwordAdmin = "{ADMIN_PW}";\n'
        'template = "dayzOffline.chernarusplus";\n',
        encoding="utf-8",
    )
    sample_config.web_password_hash = hash_password(PASSWORD)
    app = server.create_app(sample_config)
    app.state.ctx.users.add("looker", "lookpass", "viewer")
    app.state.ctx.users.add("mandy", "modpass-1", "moderator")
    return app


def _as(app, name, password):
    client = TestClient(app)
    body = {"password": password} if name is None else {"username": name, "password": password}
    assert client.post("/api/auth/login", json=body).status_code == 200
    return client


def _field(body, key):
    return next(f for s in body["sections"] for f in s["fields"] if f["key"] == key)


# ── the structured editors ──────────────────────────────────────────────


@pytest.mark.parametrize("name,password", [("looker", "lookpass"), ("mandy", "modpass-1")])
def test_a_non_admin_never_receives_the_rcon_password(app, name, password):
    resp = _as(app, name, password).get("/api/configs/battleye")
    assert resp.status_code == 200
    assert RCON not in resp.text
    field = _field(resp.json(), "RConPassword")
    assert field["value"] == "" and field["redacted"] is True
    # Only the secret goes; the page stays useful.
    assert _field(resp.json(), "RConPort")["value"] == 2305


@pytest.mark.parametrize("name,password", [("looker", "lookpass"), ("mandy", "modpass-1")])
def test_a_non_admin_never_receives_the_serverdz_passwords(app, name, password):
    resp = _as(app, name, password).get("/api/configs/serverdz")
    assert resp.status_code == 200
    assert JOIN not in resp.text and ADMIN_PW not in resp.text
    assert _field(resp.json(), "hostname")["value"] == "Test Server"


def test_an_admin_still_sees_and_edits_them(app):
    client = _as(app, "admin", PASSWORD)
    body = client.get("/api/configs/battleye").json()
    assert _field(body, "RConPassword")["value"] == RCON
    assert "redacted" not in _field(body, "RConPassword")
    assert JOIN in client.get("/api/configs/serverdz").text


def test_every_password_field_is_marked_secret(app):
    """The form draws a `secret` field as a password box with a show button."""
    client = _as(app, "admin", PASSWORD)
    serverdz = client.get("/api/configs/serverdz").json()
    assert _field(serverdz, "password")["secret"] is True
    assert _field(serverdz, "passwordAdmin")["secret"] is True
    assert "secret" not in _field(serverdz, "hostname")
    assert _field(client.get("/api/configs/battleye").json(), "RConPassword")["secret"] is True


def test_every_credential_field_is_on_the_list(sample_config):
    """A new editor with a password field must add its key to SECRET_KEYS, or
    it goes to every viewer the way RConPassword did. Walks every form editor
    that loads against the fixture install."""
    sample_config.server_cfg_path.write_text(
        'hostname = "x";\npassword = "a";\npasswordAdmin = "b";\n', encoding="utf-8"
    )
    looks_secret = re.compile(r"password|webhook|token|secret|api_?key", re.IGNORECASE)
    seen = set()
    for spec in editors.list_editors():
        if spec.kind != "form":
            continue
        try:
            body = spec.load(sample_config)
        except Exception:
            continue
        for section in body.get("sections", []):
            for field in section.get("fields", []):
                seen.add(field["key"])
    assert {"RConPassword", "password", "passwordAdmin"} <= seen  # the walk reached them
    assert {k for k in seen if looks_secret.search(k)} <= redact.SECRET_KEYS


# ── the versions diff ───────────────────────────────────────────────────


def test_a_diff_shown_to_a_non_admin_hides_the_password_lines(app, sample_config):
    """A diff's context lines print whatever sits beside a change — here the
    hostname, one line above the passwords."""
    cfg = sample_config.server_cfg_path
    assert config_versions.snapshot(cfg) is not None
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("Test Server", "Renamed"), encoding="utf-8")
    query = f"/api/versions/diff?file=serverDZ.cfg&version=1&source_path={cfg}"

    viewer = _as(app, "looker", "lookpass").get(query)
    assert viewer.status_code == 200
    diff = viewer.json()["diff"]
    assert "Renamed" in diff
    assert JOIN not in diff and ADMIN_PW not in diff
    assert f"passwordAdmin = {redact.HIDDEN}" in diff

    admin = _as(app, "admin", PASSWORD).get(query).json()["diff"]
    assert JOIN in admin and ADMIN_PW in admin


# ── the change report ───────────────────────────────────────────────────


NEW_JOIN = "join-secret-3"
NEW_ADMIN_PW = "admin-secret-4"


@pytest.fixture()
def changed_passwords(app, sample_config):
    """A start snapshot (and the baseline it seeds) holding the old passwords,
    then serverDZ.cfg with new ones: both routes then print a cfg change for
    each, old value and new."""
    from app.ops import changeset_ops

    mission = sample_config.server_dir_path / "mpmissions" / "dayzOffline.chernarusplus"
    (mission / "db").mkdir(parents=True, exist_ok=True)
    changeset_ops.take_snapshot(sample_config)
    cfg = sample_config.server_cfg_path
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace(JOIN, NEW_JOIN).replace(ADMIN_PW, NEW_ADMIN_PW),
        encoding="utf-8",
    )
    return app


@pytest.mark.parametrize("route", ["/api/changes", "/api/changes/baseline"])
@pytest.mark.parametrize("name,password", [("looker", "lookpass"), ("mandy", "modpass-1")])
def test_the_change_report_hides_the_passwords_from_a_non_admin(
    changed_passwords, route, name, password
):
    resp = _as(changed_passwords, name, password).get(route)
    assert resp.status_code == 200
    for secret in (JOIN, ADMIN_PW, NEW_JOIN, NEW_ADMIN_PW):
        assert secret not in resp.text
    lines = [c["description"].strip() for c in resp.json()["changes"] if c["category"] == "cfg"]
    # The change itself is still reported; only the values go.
    assert f"password: {redact.HIDDEN}" in lines
    assert f"passwordAdmin: {redact.HIDDEN}" in lines


@pytest.mark.parametrize("route", ["/api/changes", "/api/changes/baseline"])
def test_an_admin_still_sees_the_changed_passwords(changed_passwords, route):
    text = _as(changed_passwords, "admin", PASSWORD).get(route).text
    for secret in (JOIN, ADMIN_PW, NEW_JOIN, NEW_ADMIN_PW):
        assert secret in text


def test_redact_changes_touches_only_the_secret_cfg_lines():
    report = {"changes": [
        {"category": "cfg", "description": "  password: a → b"},
        {"category": "cfg", "description": "  passwordAdmin: (empty) → c"},
        {"category": "cfg", "description": "  hostname: x → y"},
        {"category": "file", "description": "  password: is a file line, not a cfg one"},
    ]}
    out = redact.redact_changes(report)
    assert [c["description"] for c in out["changes"]] == [
        f"  password: {redact.HIDDEN}",
        f"  passwordAdmin: {redact.HIDDEN}",
        "  hostname: x → y",
        "  password: is a file line, not a cfg one",
    ]
    assert report["changes"][0]["description"] == "  password: a → b"  # a copy


def test_redact_diff_covers_both_file_formats():
    text = "\n".join([
        ' password = "a";',
        '-passwordAdmin = "b";',
        '+passwordAdmin = "c";',
        " RConPassword hunter2",
        ' hostname = "keep";',
        " disablePersonalLight = 1;",
    ])
    out = redact.redact_diff(text)
    for secret in ('"a"', '"b"', '"c"', "hunter2"):
        assert secret not in out
    assert ' hostname = "keep";' in out
    assert out.count(redact.HIDDEN) == 4


def test_a_secret_inside_a_form_s_nested_tree_is_hidden():
    from app.web.redact import HIDDEN, redact_form

    body = {"kind": "form", "sections": [], "nested": {"L": [{"RConPassword": "x", "n": 1}]}}
    out = redact_form(body)
    assert out["nested"] == {"L": [{"RConPassword": HIDDEN, "n": 1}]}
    assert body["nested"]["L"][0]["RConPassword"] == "x"


def test_a_secret_inside_a_table_row_s_nested_tree_is_hidden():
    from app.web.redact import HIDDEN, redact_form

    body = {"kind": "table", "columns": [], "rows": [
        {"id": "0", "values": {"n": 1}, "nested": {"L": [{"password": "x", "n": 2}]}},
        {"id": "1", "values": {"n": 3}},
    ]}
    out = redact_form(body)
    assert out["rows"][0]["nested"] == {"L": [{"password": HIDDEN, "n": 2}]}
    assert out["rows"][1] == body["rows"][1]
    assert body["rows"][0]["nested"]["L"][0]["password"] == "x"
