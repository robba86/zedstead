"""Tests for /api/users — accounts in the browser (plans/43 step 5b).

The store's own rules are tested in `tests/test_users.py`; what is asserted
here is that they survive the trip through HTTP as **4xx with a sentence**
rather than as a 500, plus the one rule this layer adds that the store cannot
know: you may not demote or delete yourself.

That rule is not paranoia about a rare case. With two admins, `remove`'s
last-admin guard is satisfied by the *other* one, so an admin who misclicks
their own row has locked themselves out of a panel that cannot be repaired
from inside itself.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.web.auth import hash_password

PASSWORD = "testpass123"


@pytest.fixture()
def app(sample_config):
    """A migrated install: `admin` exists, made from the single password."""
    from app.web import server

    sample_config.web_password_hash = hash_password(PASSWORD)
    return server.create_app(sample_config)


@pytest.fixture()
def client(app):
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return c


# ── reading ─────────────────────────────────────────────────────────────


def test_the_list_never_carries_a_hash(client):
    """`User.public()` is the contract. A hash on the wire is a hash in a
    browser's memory, its devtools and anything that proxies the panel."""
    client.post("/api/users", json={"name": "mandy", "password": "password-p", "role": "moderator"})
    body = client.get("/api/users").json()

    assert [u["name"] for u in body["users"]] == ["admin", "mandy"]
    assert all("password_hash" not in u for u in body["users"])
    assert "scrypt$" not in client.get("/api/users").text
    assert body["roles"] == ["viewer", "moderator", "admin"]


def test_an_unreadable_file_is_reported_not_hidden(client, app):
    """It means sign-in has silently fallen back to the old single password,
    which the owner needs telling — and it is why every write refuses.

    The account session from before the file broke is refused until it
    reads again (plans/68), so the owner signs in with that password first."""
    app.state.ctx.users.path.write_text("{ truncated", encoding="utf-8")
    assert client.get("/api/users").status_code == 401
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    body = client.get("/api/users").json()
    assert body["degraded"] is True
    assert body["users"] == []

    resp = client.post("/api/users", json={"name": "x", "password": "password-p", "role": "admin"})
    assert resp.status_code == 400
    assert "could not be read" in resp.json()["detail"]


def test_the_account_list_is_not_viewer_business(app):
    """A read defaults to viewer, so this one has to be named in
    NON_VIEWER_READS or it silently stays open to everybody."""
    app.state.ctx.users.add("looker", "lookpass", "viewer")
    client = TestClient(app)
    client.post("/api/auth/login", json={"username": "looker", "password": "lookpass"})
    assert client.get("/api/users").status_code == 403


def test_a_moderator_cannot_make_accounts(app):
    """Writes take the default, which is admin. Asserted through the app so a
    later refactor of the default cannot quietly change it."""
    app.state.ctx.users.add("mandy", "modpass-1", "moderator")
    client = TestClient(app)
    client.post("/api/auth/login", json={"username": "mandy", "password": "modpass-1"})
    assert client.post(
        "/api/users", json={"name": "x", "password": "password-p", "role": "admin"}
    ).status_code == 403
    assert client.delete("/api/users/admin").status_code == 403


# ── writing ─────────────────────────────────────────────────────────────


def test_creating_an_account_lets_them_sign_in(client, app):
    resp = client.post(
        "/api/users", json={"name": "mandy", "password": "modpass-1", "role": "moderator"}
    )
    assert resp.status_code == 200, resp.text
    # The whole of `User.public()`, asserted as a whole: a new account has no
    # second factor (TODO B5), and an equality check is what keeps a field
    # added to that method from reaching a browser unnoticed.
    assert resp.json()["user"] == {"name": "mandy", "role": "moderator", "totp": False}

    other = TestClient(app)
    assert other.post(
        "/api/auth/login", json={"username": "mandy", "password": "modpass-1"}
    ).status_code == 200
    assert other.get("/api/auth/me").json()["role"] == "moderator"


def test_a_duplicate_name_is_a_400_with_a_sentence(client):
    client.post("/api/users", json={"name": "mandy", "password": "password-p", "role": "viewer"})
    resp = client.post("/api/users", json={"name": "MANDY", "password": "password-q", "role": "viewer"})
    assert resp.status_code == 400
    assert "already exists" in resp.json()["detail"]


def test_a_name_with_a_slash_is_a_400_with_a_sentence(client):
    """Created, it could never be changed or deleted here: `%2F` is decoded
    before routing and `PUT /api/users/a%2Fb/role` answered 405."""
    resp = client.post("/api/users", json={"name": "a/b", "password": "password-p", "role": "viewer"})
    assert resp.status_code == 400
    assert "slash" in resp.json()["detail"]
    names = [u["name"] for u in client.get("/api/users").json()["users"]]
    assert "a/b" not in names


def test_a_short_password_is_a_400_on_create_and_on_reset(client, app):
    """The sentence is the store's, and it is what the Users card shows
    (`api.js` throws `detail`, the card renders the message)."""
    resp = client.post("/api/users", json={"name": "mandy", "password": "short", "role": "viewer"})
    assert resp.status_code == 400
    assert "at least 8 characters" in resp.json()["detail"]
    assert app.state.ctx.users.get("mandy") is None

    client.post("/api/users", json={"name": "mandy", "password": "long-enough", "role": "viewer"})
    resp = client.put("/api/users/mandy/password", json={"password": "short"})
    assert resp.status_code == 400
    assert "at least 8 characters" in resp.json()["detail"]
    assert TestClient(app).post(
        "/api/auth/login", json={"username": "mandy", "password": "long-enough"}
    ).status_code == 200


def test_an_unknown_role_is_a_400_not_a_500(client):
    resp = client.post("/api/users", json={"name": "x", "password": "password-p", "role": "root"})
    assert resp.status_code == 400
    assert "Unknown role" in resp.json()["detail"]


def test_changing_a_role_takes_effect_immediately(client, app):
    client.post("/api/users", json={"name": "mandy", "password": "password-p", "role": "viewer"})
    other = TestClient(app)
    other.post("/api/auth/login", json={"username": "mandy", "password": "password-p"})
    assert other.post("/api/players/kick", json={"name": "nobody"}).status_code == 403

    assert client.put("/api/users/mandy/role", json={"role": "moderator"}).status_code == 200
    assert other.post("/api/players/kick", json={"name": "nobody"}).status_code != 403


def test_setting_a_password_keeps_the_role_and_admits_the_new_one(client, app):
    client.post("/api/users", json={"name": "mandy", "password": "old-password", "role": "moderator"})
    resp = client.put("/api/users/mandy/password", json={"password": "new-password"})
    assert resp.status_code == 200
    assert resp.json()["user"]["role"] == "moderator"

    other = TestClient(app)
    assert other.post(
        "/api/auth/login", json={"username": "mandy", "password": "old-password"}
    ).status_code == 401
    assert other.post(
        "/api/auth/login", json={"username": "mandy", "password": "new-password"}
    ).status_code == 200


def test_a_reset_password_signs_that_account_out_and_nobody_else(client, app):
    """A reset is what you do when somebody else may know the password, so a
    session opened with the old one must not outlive it. Scoped: the admin who
    made the reset, and every other account, stay signed in."""
    client.post("/api/users", json={"name": "mandy", "password": "old-password", "role": "moderator"})
    client.post("/api/users", json={"name": "looker", "password": "look-password", "role": "viewer"})
    mandy, looker = TestClient(app), TestClient(app)
    assert mandy.post(
        "/api/auth/login", json={"username": "mandy", "password": "old-password"}
    ).status_code == 200
    assert looker.post(
        "/api/auth/login", json={"username": "looker", "password": "look-password"}
    ).status_code == 200

    assert client.put("/api/users/mandy/password", json={"password": "new-password"}).status_code == 200

    assert mandy.get("/api/auth/me").status_code == 401
    assert looker.get("/api/auth/me").status_code == 200
    assert client.get("/api/auth/me").status_code == 200


def test_resetting_your_own_password_here_needs_your_current_one(client, app):
    """plans/68 2P-W1. Without it, a session alone (an unlocked browser, a
    stolen cookie) set the account a password its holder knew, signed the
    owner out everywhere else, and then turned the second factor off with it.
    Your account asks for the current password; so does this route now, for
    your own name in any spelling."""
    elsewhere = TestClient(app)
    assert elsewhere.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200

    missing = client.put("/api/users/admin/password", json={"password": "fresh-password"})
    assert missing.status_code == 400
    assert "current" in missing.json()["detail"]
    wrong = client.put(
        "/api/users/ADMIN/password", json={"password": "fresh-password", "current": "not-it-at-all"}
    )
    assert wrong.status_code == 400
    assert wrong.json()["detail"] == "Current password is incorrect"

    # Nothing changed: the old password still signs in, nobody was signed out.
    assert elsewhere.get("/api/auth/me").status_code == 200
    assert TestClient(app).post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    assert TestClient(app).post(
        "/api/auth/login", json={"password": "fresh-password"}
    ).status_code == 401


def test_resetting_your_own_password_here_keeps_this_session(client, app):
    """With the current password it works as Your account does: your other
    sessions end, the one you did it from does not."""
    elsewhere = TestClient(app)
    assert elsewhere.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200

    assert client.put(
        "/api/users/admin/password", json={"password": "fresh-password", "current": PASSWORD}
    ).status_code == 200

    assert client.get("/api/auth/me").status_code == 200
    assert elsewhere.get("/api/auth/me").status_code == 401
    assert TestClient(app).post(
        "/api/auth/login", json={"password": "fresh-password"}
    ).status_code == 200


def test_guessing_your_current_password_here_is_held_to_the_sign_in_limit(client, app):
    """`deps.confirm_password` (AUTH-8): the guesses fill the bucket a sign-in
    as this account fills, so a session cannot try passwords here faster than
    the login form allows."""
    for _ in range(6):
        assert client.put(
            "/api/users/admin/password", json={"password": "fresh-password", "current": "wrong-guess"}
        ).status_code == 400

    assert client.put(
        "/api/users/admin/password", json={"password": "fresh-password", "current": PASSWORD}
    ).status_code == 429
    assert TestClient(app).post(
        "/api/auth/login", json={"username": "admin", "password": PASSWORD}
    ).status_code == 429


def test_resetting_someone_elses_password_still_asks_for_nothing_else(client, app):
    """Only your own name needs `current`. An admin resetting a colleague's
    password, the reason this route exists, works as it did."""
    client.post("/api/users", json={"name": "mandy", "password": "old-password", "role": "moderator"})

    assert client.put("/api/users/mandy/password", json={"password": "new-password"}).status_code == 200
    assert TestClient(app).post(
        "/api/auth/login", json={"username": "mandy", "password": "new-password"}
    ).status_code == 200


# ── the account made from the single password (plans/68) ──────────────


def _deputy(client, app):
    """A second admin, signed in, so the migrated `admin` can be deleted."""
    assert client.post(
        "/api/users", json={"name": "deputy", "password": "deputypass", "role": "admin"}
    ).status_code == 200
    deputy = TestClient(app)
    assert deputy.post(
        "/api/auth/login", json={"username": "deputy", "password": "deputypass"}
    ).status_code == 200
    return deputy


def test_resetting_the_migrated_admins_password_moves_the_fallback(client, app):
    from app.web.auth import verify_password

    resp = client.put(
        "/api/users/admin/password", json={"password": "resetpass1", "current": PASSWORD}
    )
    assert resp.status_code == 200
    assert verify_password("resetpass1", app.state.ctx.config.web_password_hash)


def test_resetting_another_password_leaves_the_fallback(client, app):
    client.post("/api/users", json={"name": "mandy", "password": "password-p", "role": "moderator"})
    before = app.state.ctx.config.web_password_hash

    assert client.put(
        "/api/users/mandy/password", json={"password": "resetpass1"}
    ).status_code == 200
    assert app.state.ctx.config.web_password_hash == before


def test_deleting_the_migrated_admin_retires_the_fallback(client, app):
    """Otherwise the deleted admin's password stays a way in as admin for as
    long as users.json cannot be read."""
    from app.shared.config import DayzConfig

    deputy = _deputy(client, app)
    assert deputy.delete("/api/users/admin").status_code == 200

    assert DayzConfig.load().web_password_hash == ""
    app.state.ctx.users.path.write_text("{ truncated", encoding="utf-8")
    fresh = TestClient(app)
    assert fresh.post("/api/auth/login", json={"password": PASSWORD}).status_code == 401


def test_deleting_another_account_keeps_the_fallback(client, app):
    deputy = _deputy(client, app)
    before = app.state.ctx.config.web_password_hash

    assert client.delete("/api/users/deputy").status_code == 200
    assert deputy.get("/api/auth/me").status_code == 401
    assert app.state.ctx.config.web_password_hash == before


def test_demoting_the_migrated_admin_retires_the_fallback(client, app):
    """The fallback signs in as admin, so it must not stay the password of
    someone who no longer is one."""
    from app.shared.config import DayzConfig

    deputy = _deputy(client, app)
    resp = deputy.put("/api/users/admin/role", json={"role": "viewer"})
    assert resp.status_code == 200

    assert DayzConfig.load().web_password_hash == ""
    app.state.ctx.users.path.write_text("{ truncated", encoding="utf-8")
    fresh = TestClient(app)
    assert fresh.post("/api/auth/login", json={"password": PASSWORD}).status_code == 401


def test_promoting_it_back_restores_nothing_until_its_next_password(client, app):
    from app.web.auth import verify_password

    deputy = _deputy(client, app)
    deputy.put("/api/users/admin/role", json={"role": "viewer"})
    assert deputy.put("/api/users/admin/role", json={"role": "admin"}).status_code == 200
    assert app.state.ctx.config.web_password_hash == ""
    assert app.state.ctx.users.get("admin").migrated is True

    assert deputy.put(
        "/api/users/admin/password", json={"password": "resetpass1"}
    ).status_code == 200
    assert verify_password("resetpass1", app.state.ctx.config.web_password_hash)


def test_changing_another_accounts_role_keeps_the_fallback(client, app):
    _deputy(client, app)
    before = app.state.ctx.config.web_password_hash

    assert client.put("/api/users/deputy/role", json={"role": "viewer"}).status_code == 200
    assert app.state.ctx.config.web_password_hash == before


# ── the fallback's copies on other instances (2P-W2) ───────────────────


def _instance_server(root, name: str) -> str:
    server = root / "srv" / name
    (server / "mpmissions" / "dayzOffline.chernarusplus").mkdir(parents=True)
    (server / "serverDZ.cfg").write_text(
        'hostname = "x";\nclass Missions\n{\n\tclass DayZ\n\t{\n'
        '\t\ttemplate = "dayzOffline.chernarusplus";\n\t};\n};\n',
        encoding="utf-8",
    )
    return str(server)


@pytest.fixture()
def cloned(tmp_path):
    """A panel on `main`, which still has the single password; `pvp` cloned
    from it before any account existed, so it holds the same hash; and `pve`,
    whose fallback was set to a password of its own."""
    from app.server import instances
    from app.shared.config import DayzConfig
    from app.web import server

    instances.save_new_config(DayzConfig(
        server_dir=_instance_server(tmp_path, "main"), steamcmd_dir=str(tmp_path / "steamcmd"),
        web_notifier_enabled=False, web_password_hash=hash_password(PASSWORD)))
    instances.create_instance(
        "pvp", server_dir=_instance_server(tmp_path, "pvp"), source=DayzConfig.for_instance("main"))
    pve = instances.create_instance("pve", server_dir=_instance_server(tmp_path, "pve")).config
    pve.web_notifier_enabled = False
    pve.web_password_hash = hash_password("pve-own-pass")
    pve.save()
    return server.create_app(DayzConfig.for_instance("main"))


def test_a_new_password_reaches_every_copy_of_the_fallback(cloned):
    """The panel reads only its startup instance's fallback; a copy left on
    `pvp` signs in with the retired password once the panel starts there. A
    served instance is written through its own bundle, so the config it holds
    in memory follows too."""
    from app.shared.config import DayzConfig
    from app.web.auth import verify_password

    with TestClient(cloned) as c:
        assert c.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        bundles = cloned.state.panel.bundles
        assert {"pvp", "pve"} <= set(bundles)

        assert c.post(
            "/api/settings/password", json={"current": PASSWORD, "new": "newadminpw"}
        ).status_code == 200

        for ident in ("main", "pvp"):
            assert verify_password("newadminpw", DayzConfig.for_instance(ident).web_password_hash)
        assert verify_password("newadminpw", bundles["pvp"].config.web_password_hash)
        assert verify_password("pve-own-pass", DayzConfig.for_instance("pve").web_password_hash)
        assert verify_password("pve-own-pass", bundles["pve"].config.web_password_hash)


def test_retiring_the_fallback_retires_its_copies(cloned):
    """Without a lifespan the panel serves only `main`, so `pvp` is written on
    disk. The old password then no longer signs in on a panel started on
    `pvp` while users.json cannot be read."""
    from app.shared.config import DayzConfig
    from app.web import server
    from app.web.auth import verify_password

    client = TestClient(cloned)
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    deputy = _deputy(client, cloned)
    assert deputy.delete("/api/users/admin").status_code == 200

    assert DayzConfig.for_instance("main").web_password_hash == ""
    assert DayzConfig.for_instance("pvp").web_password_hash == ""
    assert verify_password("pve-own-pass", DayzConfig.for_instance("pve").web_password_hash)

    cloned.state.ctx.users.path.write_text("{ truncated", encoding="utf-8")
    on_pvp = TestClient(server.create_app(DayzConfig.for_instance("pvp")))
    assert on_pvp.post("/api/auth/login", json={"password": PASSWORD}).status_code == 401


def test_deleting_an_account_is_a_404_when_there_is_none(client):
    resp = client.delete("/api/users/ghost")
    assert resp.status_code == 404
    assert "No such account" in resp.json()["detail"]


def test_the_panel_cannot_be_left_without_an_admin(client):
    """Two guards meet here and only one of them ever fires.

    Through HTTP you always reach the self-rule before the store's last-admin
    guard, and that is arithmetic rather than luck: for the target to be the
    last admin *and* not be the caller, the caller would have to be an admin
    who is not counted. So the store's guard is dead code from this router —
    it is the CLI that meets it, and `tests/test_users_cli.py` is where that
    is asserted. What matters here is the invariant, whatever the route.
    """
    client.post("/api/users", json={"name": "deputy", "password": "password-d", "role": "admin"})
    assert client.put("/api/users/deputy/role", json={"role": "viewer"}).status_code == 200

    assert client.put("/api/users/admin/role", json={"role": "viewer"}).status_code == 400
    assert client.delete("/api/users/admin").status_code == 400
    assert "admin" in [u["role"] for u in client.get("/api/users").json()["users"]]


# ── the rule this layer adds ────────────────────────────────────────────


def test_you_cannot_demote_yourself(client, app):
    """With a second admin present the store is satisfied — its guard is about
    the *last* admin, not about who is asking. Only the request knows that."""
    client.post("/api/users", json={"name": "deputy", "password": "password-d", "role": "admin"})
    resp = client.put("/api/users/admin/role", json={"role": "viewer"})

    assert resp.status_code == 400
    assert "yourself" in resp.json()["detail"]
    assert app.state.ctx.users.get("admin").role == "admin"


def test_you_cannot_delete_yourself(client, app):
    client.post("/api/users", json={"name": "deputy", "password": "password-d", "role": "admin"})
    resp = client.delete("/api/users/admin")

    assert resp.status_code == 400
    assert "yourself" in resp.json()["detail"]
    assert app.state.ctx.users.get("admin") is not None


def test_self_is_matched_however_it_is_spelled(client):
    """The store matches names case-insensitively, so this must too — or
    `DELETE /api/users/ADMIN` walks around the guard."""
    client.post("/api/users", json={"name": "deputy", "password": "password-d", "role": "admin"})
    assert client.delete("/api/users/ADMIN").status_code == 400


def test_you_may_still_edit_somebody_else(client):
    client.post("/api/users", json={"name": "deputy", "password": "password-d", "role": "admin"})
    assert client.put("/api/users/deputy/role", json={"role": "viewer"}).status_code == 200
    assert client.delete("/api/users/deputy").status_code == 200
