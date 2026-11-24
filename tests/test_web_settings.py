"""Tests for /api/settings* (app/web/routers/settings.py)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

PASSWORD = "testpass123"


@pytest.fixture()
def client(sample_config):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    app = server.create_app(sample_config)
    tclient = TestClient(app)
    tclient.post("/api/auth/login", json={"password": PASSWORD})
    return tclient


def test_settings_requires_session(sample_config):
    from app.web import server

    app = server.create_app(sample_config)
    resp = TestClient(app).get("/api/settings")
    assert resp.status_code == 401


def test_get_settings_shape_and_excludes_password_hash(client):
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    all_keys = {k["key"] for section in body for k in section["keys"]}
    assert "server_dir" in all_keys
    assert "web_password_hash" not in all_keys


def test_update_settings_round_trip(client):
    resp = client.put("/api/settings", json={"values": {"backup_max_count": "10"}})
    assert resp.status_code == 200

    from app.shared.config import DayzConfig

    assert DayzConfig.load().backup_max_count == 10


def test_update_settings_unknown_key_422(client):
    resp = client.put("/api/settings", json={"values": {"not_a_real_key": "1"}})
    assert resp.status_code == 422


def test_update_settings_invalid_value_422(client):
    resp = client.put("/api/settings", json={"values": {"backup_max_count": "not-an-int"}})
    assert resp.status_code == 422


def test_update_settings_bool_round_trip(client):
    resp = client.put("/api/settings", json={"values": {"chat_log_enabled": "true"}})
    assert resp.status_code == 200

    from app.shared.config import DayzConfig

    assert DayzConfig.load().chat_log_enabled is True


def test_settings_never_serve_the_site_ids(sample_config):
    """GET /api/settings serves registry fields, and the listing key must
    never reach a browser (plans/57 B2) — nor the install ID it pairs with."""
    from app.ops.site_ops import set_switch
    from app.web import server
    from app.web.auth import hash_password

    set_switch(sample_config, "site_listing", True)
    sample_config.web_password_hash = hash_password(PASSWORD)
    sample_config.save()
    app = server.create_app(sample_config)

    with TestClient(app) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        resp = client.get("/api/settings")

    assert resp.status_code == 200
    keys = {k["key"] for section in resp.json() for k in section["keys"]}
    assert {"site_ping", "site_listing", "site_address"} <= keys
    assert "site_endpoint" not in keys  # fixed in code, never a setting
    assert "site_secret" not in resp.text
    assert "site_install_id" not in resp.text
    assert sample_config.site_secret not in resp.text
    assert sample_config.site_install_id not in resp.text


def test_turning_a_site_switch_on_from_the_page_creates_the_ids(client):
    from app.shared.config import DayzConfig

    resp = client.put("/api/settings", json={"values": {"site_ping": "true"}})
    assert resp.status_code == 200

    saved = DayzConfig.load()
    assert saved.site_ping is True
    assert saved.site_install_id and saved.site_secret


def test_a_wrong_current_password_is_a_400_and_keeps_the_session(client):
    """Not 401: the web UI sends every 401 to /login, which lost the form
    instead of showing the sentence (plans/68). 401 means "not signed in"."""
    resp = client.post("/api/settings/password", json={"current": "wrong", "new": "newpass123"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Current password is incorrect"
    assert client.get("/api/auth/me").status_code == 200


def test_a_wrong_current_single_password_is_a_400_too(sample_config):
    """The other branch: no readable accounts, so the single password."""
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    app = server.create_app(sample_config)
    app.state.ctx.users.path.unlink()
    client = TestClient(app)
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200

    resp = client.post("/api/settings/password", json={"current": "wrong", "new": "newpass123"})
    assert resp.status_code == 400
    assert client.get("/api/auth/me").status_code == 200


def test_guessing_the_current_password_is_held_to_the_sign_in_limit(sample_config):
    """plans/68 AUTH-8. A session re-checking the account's password counts
    in the login limiter, in the bucket a sign-in as that account fills: six
    wrong guesses, then 429 here and on the login form alike."""
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    app = server.create_app(sample_config)
    client = TestClient(app)
    assert client.post(
        "/api/auth/login", json={"username": "admin", "password": PASSWORD}
    ).status_code == 200

    for _ in range(6):
        resp = client.post("/api/settings/password", json={"current": "wrong", "new": "newpass123"})
        assert resp.status_code == 400
    refused = client.post("/api/settings/password", json={"current": PASSWORD, "new": "newpass123"})

    assert refused.status_code == 429
    assert TestClient(app).post(
        "/api/auth/login", json={"username": "admin", "password": PASSWORD}
    ).status_code == 429
    assert client.get("/api/auth/me").status_code == 200  # still signed in


def test_changing_a_password_without_a_session_is_still_a_401(sample_config):
    from app.web import server

    app = server.create_app(sample_config)
    resp = TestClient(app).post(
        "/api/settings/password", json={"current": "wrong", "new": "newpass123"}
    )
    assert resp.status_code == 401


def test_change_password_success_revokes_other_sessions(sample_config):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    app = server.create_app(sample_config)

    client_a = TestClient(app)
    client_a.post("/api/auth/login", json={"password": PASSWORD})
    client_b = TestClient(app)
    client_b.post("/api/auth/login", json={"password": PASSWORD})

    resp = client_a.post("/api/settings/password", json={"current": PASSWORD, "new": "newpass456"})
    assert resp.status_code == 200

    assert client_a.get("/api/auth/me").status_code == 200
    assert client_b.get("/api/auth/me").status_code == 401


# ── the password that changes is the one you sign in with ───────────────
#
# Everything below signs in with the new password rather than trusting the
# 200. Between plans/43 step 1 and 2026 Q3 every one of these passed on the
# status code alone while the change went to a field nothing reads: `create_app`
# migrates the single password into users.json at startup, and this route kept
# rewriting `config.web_password_hash`. The route said yes, the toast said other
# sessions had been signed out, and the old password still worked.


def _app(sample_config):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    return server.create_app(sample_config)


def _signs_in(app, username, password) -> bool:
    fresh = TestClient(app)
    resp = fresh.post("/api/auth/login", json={"username": username, "password": password})
    return resp.status_code == 200


def test_the_new_password_is_the_one_that_works(sample_config):
    """The regression test, and it can only be written this way round: a 200
    from the route proved nothing, because the route really did change a
    password — just not the one `authenticate` reads."""
    app = _app(sample_config)
    client = TestClient(app)
    client.post("/api/auth/login", json={"password": PASSWORD})

    assert client.post(
        "/api/settings/password", json={"current": PASSWORD, "new": "brandnew456"}
    ).status_code == 200

    assert _signs_in(app, "admin", "brandnew456")
    assert not _signs_in(app, "admin", PASSWORD)


def test_it_writes_the_account_not_the_config(sample_config):
    """Named separately from the behaviour above so the *reason* has a test:
    once users.json exists it is the only thing login consults, so a change
    that lands in config.json is a change that did not happen."""
    app = _app(sample_config)
    client = TestClient(app)
    client.post("/api/auth/login", json={"password": PASSWORD})

    client.post("/api/settings/password", json={"current": PASSWORD, "new": "brandnew456"})

    from app.web.auth import verify_password

    admin = app.state.ctx.users.get("admin")
    assert verify_password("brandnew456", admin.password_hash)


def test_the_migrated_admins_new_password_is_the_fallback_too(sample_config):
    """plans/68: the account made from the single password keeps that password
    in step with its own. Before, rotating it left the old password as the way
    in as admin whenever users.json could not be read."""
    from app.shared.config import DayzConfig
    from app.web.auth import verify_password

    app = _app(sample_config)
    client = TestClient(app)
    client.post("/api/auth/login", json={"password": PASSWORD})
    assert client.post(
        "/api/settings/password", json={"current": PASSWORD, "new": "brandnew456"}
    ).status_code == 200

    assert verify_password("brandnew456", DayzConfig.load().web_password_hash)
    app.state.ctx.users.path.write_text("{ truncated", encoding="utf-8")
    assert _signs_in(app, "", "brandnew456")
    assert not _signs_in(app, "", PASSWORD)


def test_another_accounts_new_password_leaves_the_fallback_alone(sample_config):
    app = _app(sample_config)
    app.state.ctx.users.add("mandy", "mandypass", "moderator")
    before = app.state.ctx.config.web_password_hash

    client = TestClient(app)
    client.post("/api/auth/login", json={"username": "mandy", "password": "mandypass"})
    assert client.post(
        "/api/settings/password", json={"current": "mandypass", "new": "mandynew456"}
    ).status_code == 200

    assert app.state.ctx.config.web_password_hash == before


def test_a_demoted_migrated_account_cannot_set_the_admin_fallback(sample_config):
    """Every role may change its own password, and the fallback signs in as
    admin: copying a viewer's new password there would make it an admin one."""
    app = _app(sample_config)
    users = app.state.ctx.users
    users.add("deputy", "deputypass", "admin")
    users.set_role("admin", "viewer")
    before = app.state.ctx.config.web_password_hash

    client = TestClient(app)
    client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    assert client.post(
        "/api/settings/password", json={"current": PASSWORD, "new": "viewerpass1"}
    ).status_code == 200

    assert app.state.ctx.config.web_password_hash == before


def test_a_new_password_shorter_than_the_minimum_is_refused(sample_config):
    """Both branches: an account's own password, and the single password of an
    install with no accounts. A 400 with the sentence, which the Account page
    toasts, and the old password still the one that works."""
    from app.web.auth import verify_password

    app = _app(sample_config)
    client = TestClient(app)
    client.post("/api/auth/login", json={"password": PASSWORD})
    resp = client.post("/api/settings/password", json={"current": PASSWORD, "new": "short"})
    assert resp.status_code == 400
    assert "at least 8 characters" in resp.json()["detail"]
    assert _signs_in(app, "admin", PASSWORD)

    app.state.ctx.users.path.unlink()  # no accounts: the config password
    fallback = TestClient(app)
    assert fallback.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    resp = fallback.post("/api/settings/password", json={"current": PASSWORD, "new": "short"})
    assert resp.status_code == 400
    assert "at least 8 characters" in resp.json()["detail"]
    assert verify_password(PASSWORD, app.state.ctx.config.web_password_hash)


def test_the_current_password_is_checked_against_the_account(sample_config):
    """A second account's password is not the caller's, and the old code could
    not tell the difference — it compared everyone against one config field."""
    app = _app(sample_config)
    app.state.ctx.users.add("mandy", "mandypass", "moderator")

    client = TestClient(app)
    client.post("/api/auth/login", json={"username": "mandy", "password": "mandypass"})

    resp = client.post("/api/settings/password", json={"current": PASSWORD, "new": "nope12345"})
    assert resp.status_code == 400
    assert _signs_in(app, "mandy", "mandypass")


def test_changing_your_own_password_is_not_an_admin_action(sample_config):
    """Default-deny makes every write admin, so without a `SESSION_WRITES`
    entry a moderator has to ask an admin to rotate their password — which
    means one of them saying it out loud."""
    app = _app(sample_config)
    app.state.ctx.users.add("mandy", "mandypass", "moderator")

    client = TestClient(app)
    client.post("/api/auth/login", json={"username": "mandy", "password": "mandypass"})

    assert client.post(
        "/api/settings/password", json={"current": "mandypass", "new": "mandynew456"}
    ).status_code == 200
    assert _signs_in(app, "mandy", "mandynew456")

    # The rest of the router stays admin — the entry is one route, not a hole.
    assert client.get("/api/settings").status_code == 403


def test_only_your_own_other_sessions_are_signed_out(sample_config):
    """`revoke_all_except` is whole-panel, which is right for one shared
    password and wrong the moment there are accounts: a viewer rotating theirs
    would end the admin's afternoon."""
    app = _app(sample_config)
    app.state.ctx.users.add("mandy", "mandypass", "moderator")

    admin = TestClient(app)
    admin.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    mandy_one = TestClient(app)
    mandy_one.post("/api/auth/login", json={"username": "mandy", "password": "mandypass"})
    mandy_two = TestClient(app)
    mandy_two.post("/api/auth/login", json={"username": "mandy", "password": "mandypass"})

    assert mandy_one.post(
        "/api/settings/password", json={"current": "mandypass", "new": "mandynew456"}
    ).status_code == 200

    assert mandy_one.get("/api/auth/me").status_code == 200   # the one in hand
    assert mandy_two.get("/api/auth/me").status_code == 401   # their other one
    assert admin.get("/api/auth/me").status_code == 200       # somebody else's


def test_an_install_with_no_accounts_still_changes_the_config_password(sample_config):
    """The pre-accounts path, unchanged. `authenticate` falls back to
    `config.web_password_hash` when there are no accounts, so that is the
    password this must rewrite — the same rule, not an exception to it."""
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    app = server.create_app(sample_config)
    app.state.ctx.users.path.unlink()  # undo create_app's migration

    client = TestClient(app)
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    assert client.post(
        "/api/settings/password", json={"current": PASSWORD, "new": "brandnew456"}
    ).status_code == 200

    from app.web.auth import verify_password

    assert verify_password("brandnew456", app.state.ctx.config.web_password_hash)
    assert _signs_in(app, "", "brandnew456")


def test_settings_include_notification_and_monitoring_fields(client):
    body = client.get("/api/settings").json()
    keys = {f["key"] for section in body for f in section["keys"]}
    assert {
        "toast_player_connect",
        "toast_restart_warning",
        "toast_doctor_check",
        "toast_version_update",
        "metrics_interval",
        "mod_update_check_interval",
        "log_cleanup_days",
        "log_cleanup_keep",
    } <= keys

    resp = client.put(
        "/api/settings",
        json={"values": {"metrics_interval": "120", "toast_doctor_check": "false"}},
    )
    assert resp.status_code == 200

    from app.shared.config import DayzConfig

    cfg = DayzConfig.load()
    assert cfg.metrics_interval == 120
    assert cfg.toast_doctor_check is False


def test_a_value_that_breaks_something_later_is_a_422_naming_the_key(client):
    """plans/51 A (N7): `metrics_interval = 0` was saved with a 200 and turned
    the notifier's metrics loop into a busy loop. One bad key refuses the whole
    request, so nothing in it is half-applied."""
    def current():
        body = client.get("/api/settings").json()
        return {f["key"]: f["value"] for section in body for f in section["keys"]}

    before = current()
    resp = client.put(
        "/api/settings",
        json={"values": {"toast_doctor_check": "false", "metrics_interval": "0"}},
    )
    assert resp.status_code == 422
    assert "metrics_interval" in resp.json()["detail"]

    after = current()
    assert after["metrics_interval"] == before["metrics_interval"]
    assert after["toast_doctor_check"] == before["toast_doctor_check"]


def test_a_server_dir_the_unit_could_not_render_is_refused_on_save(client):
    before = client.get("/api/settings").json()
    resp = client.put("/api/settings", json={"values": {"server_dir": "/srv/da\"yz"}})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "server_dir" in detail and "Server directory" in detail
    assert client.get("/api/settings").json() == before


def _editor_field(client, editor_id, key):
    body = client.get(f"/api/configs/{editor_id}").json()
    return next(f for section in body["sections"] for f in section["fields"] if f["key"] == key)


def test_server_branch_is_in_configs_updates_and_takes_only_its_two_values(client):
    entry = _editor_field(client, "updates", "server_branch")
    assert entry["value"] == "stable"
    assert entry["choices"] == ["stable", "experimental"]
    assert "Experimental" in entry["help"]
    assert client.put("/api/configs/updates", json={"values": {"server_branch": "beta"}}).status_code == 400


def test_server_control_settings_live_in_configs_not_settings(client):
    """settings_registry._Setting.editor: Settings is Zedstead's own behaviour; a
    value about how Zedstead runs the server is shown by its Configs editor."""
    from app.shared import settings_registry

    moved = {s.key for s in settings_registry._REGISTRY if s.editor}
    listed = {k["key"] for section in client.get("/api/settings").json() for k in section["keys"]}
    assert moved and not moved & listed
    for s in settings_registry._REGISTRY:
        if s.editor:
            assert _editor_field(client, s.editor, s.key)["help"] == s.description


def test_a_configs_settings_editor_checks_and_saves_like_settings(client):
    from app.shared.config import DayzConfig

    r = client.put("/api/configs/restart-warnings", json={"values": {"restart_warning_minutes": "30,,5"}})
    assert r.status_code == 400
    r = client.put("/api/configs/restart-warnings", json={"values": {
        "restart_warning_minutes": "30,10", "restart_lock_at_minutes": 2}})
    assert r.status_code == 200
    saved = DayzConfig.load()
    assert (saved.restart_warning_minutes, saved.restart_lock_at_minutes) == ([30, 10], 2)
    assert _editor_field(client, "restart-warnings", "restart_warning_minutes")["value"] == "30,10"
    assert client.put("/api/configs/crash-recovery", json={"values": {"rcon_host": "1.2.3.4"}}).status_code == 400


def test_rcon_host_saves_in_battleye_while_the_server_runs(client, battleye_running):
    """server_forms._battleye_save: a save of Zedstead's own rcon_host touches no
    file, so BattlEye's rename while the server runs does not refuse it."""
    from app.shared.config import DayzConfig

    before = battleye_running.read_text()
    assert _editor_field(client, "battleye", "rcon_host")["value"] == "127.0.0.1"
    assert client.put("/api/configs/battleye", json={"values": {"rcon_host": "10.0.0.5"}}).status_code == 200
    assert DayzConfig.load().rcon_host == "10.0.0.5"
    assert battleye_running.read_text() == before
    assert client.put("/api/configs/battleye", json={"values": {"RConPort": 2310}}).status_code == 409


def test_switching_an_installed_server_branch_is_a_409(client, sample_config):
    sample_config.save()
    steamapps = sample_config.server_dir_path / "steamapps"
    steamapps.mkdir()
    (steamapps / "appmanifest_223350.acf").write_text("")
    resp = client.put("/api/settings", json={"values": {"server_branch": "experimental"}})
    assert resp.status_code == 409
    assert "holds the stable DayZ server" in resp.json()["detail"]

    from app.shared.config import DayzConfig

    assert DayzConfig.load().server_branch == "stable"


def test_a_setting_the_container_env_sets_is_read_only(client, sample_config):
    from app.shared.container import record_env_fields

    record_env_fields(sample_config, {"backup_dir": "BACKUP_DIR"})
    entries = {k["key"]: k for s in client.get("/api/settings").json() for k in s["keys"]}
    assert entries["backup_dir"]["env"]["var"] == "BACKUP_DIR"
    assert "compose .env" in entries["backup_dir"]["env"]["note"]
    assert "env" not in entries["steamcmd_dir"]

    resp = client.put("/api/settings", json={"values": {"backup_dir": "/elsewhere"}})
    assert resp.status_code == 409, resp.text
    assert "BACKUP_DIR" in resp.json()["detail"]
    # The value it already has is not a change, so a section save still works.
    current = entries["backup_dir"]["value"]
    resp = client.put("/api/settings", json={"values": {"backup_dir": current, "steamcmd_dir": "/opt/steamcmd"}})
    assert resp.status_code == 200, resp.text


def test_an_int_setting_with_no_maximum_is_capped(client):
    """99999999999999999999999 was saved for
    backup_max_count and the page showed it as 1e+23."""
    resp = client.put("/api/settings", json={"values": {"backup_max_count": "99999999999999999999999"}})
    assert resp.status_code in (400, 422), resp.text
    assert "2147483647" in resp.text
    assert client.put("/api/settings", json={"values": {"backup_max_count": "7"}}).status_code == 200


def test_a_list_with_an_empty_item_is_refused(client):
    resp = client.put("/api/settings", json={"values": {"restart_warning_minutes": "15,,5"}})
    assert resp.status_code in (400, 422), resp.text
    assert "comma-separated list" in resp.text
