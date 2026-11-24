"""Tests for app/web/users.py — the account store (TODO B4, plans/43 step 1).

Nothing here gates a route; that is steps 2 and 4. What this file is for is the
handful of ways an account store can go wrong that are **not** recoverable from
inside the panel: locking everyone out, removing the last admin, writing hashes
world-readable, or letting a typo in a hand-edited file grant privilege.
"""

from __future__ import annotations

import json
import os
import stat

import pytest

from app.web.install_id import install_id
from app.shared.platform import IS_WINDOWS
from app.web.auth import hash_password, verify_password
from app.web.users import (
    ROLES,
    User,
    UserStore,
    highest_role,
    linked_fallback_hash,
    role_at_least,
)


@pytest.fixture()
def store(tmp_path):
    """An empty store — no accounts at all, the pre-migration state."""
    return UserStore(path=tmp_path / "users.json")


@pytest.fixture()
def seeded(store):
    """A store that already has an admin, which is what `add` requires.

    Most tests here are about the *second* account. Seeding the first keeps
    them from restating "and an admin exists" in every body, and matches every
    real install: the migration creates one before anyone adds a second.
    """
    store.add("root", "rootpass", "admin")
    return store


# ── roles ───────────────────────────────────────────────────────────────


def test_roles_are_ordered_least_to_most_privileged():
    assert ROLES == ("viewer", "moderator", "admin")


@pytest.mark.parametrize(
    "role,minimum,expected",
    [
        ("admin", "viewer", True),
        ("admin", "admin", True),
        ("moderator", "viewer", True),
        ("moderator", "admin", False),
        ("viewer", "moderator", False),
    ],
)
def test_role_at_least(role, minimum, expected):
    assert role_at_least(role, minimum) is expected


def test_an_unknown_role_is_never_privileged():
    """A typo in a hand-edited users.json must cost access, never grant it.
    `role_at_least("Admin", ...)` is the realistic shape of that mistake."""
    for minimum in ROLES:
        assert role_at_least("Admin", minimum) is False
        assert role_at_least("superuser", minimum) is False
        assert role_at_least("", minimum) is False


def test_highest_role_ignores_unknown_names():
    assert highest_role(["viewer", "admin", "moderator"]) == "admin"
    assert highest_role(["nonsense"]) == "viewer"
    assert highest_role([]) == "viewer"


# ── the file ────────────────────────────────────────────────────────────


def test_a_missing_file_is_empty_and_not_degraded(store):
    assert store.load() == []
    assert store.degraded is False
    assert store.exists() is False


def test_add_then_read_back(seeded):
    seeded.add("mod", "s3cret-pw", "moderator")
    users = seeded.load()
    assert [u.name for u in users] == ["root", "mod"]
    assert users[-1].role == "moderator"
    assert verify_password("s3cret-pw", users[-1].password_hash)


def test_the_password_is_hashed_not_stored(seeded):
    seeded.add("mod", "s3cret-pw", "moderator")
    assert "s3cret-pw" not in seeded.path.read_text(encoding="utf-8")


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX mode bits")
def test_the_file_is_0600_from_creation(seeded):
    """0600 at open time, not by a later chmod: between a default-mode write
    and a chmod there is a window where the hashes are world-readable."""
    seeded.add("mod", "s3cret-pw", "moderator")
    mode = stat.S_IMODE(os.stat(seeded.path).st_mode)
    assert mode == 0o600, oct(mode)


def test_a_name_is_matched_case_insensitively(seeded):
    """The login form is typed, and `Mod` and `mod` are the same person."""
    seeded.add("Mod", "s3cret-pw", "moderator")
    assert seeded.get("mod").name == "Mod"
    assert seeded.get("MOD") is not None


def test_a_duplicate_name_is_refused_whatever_its_case(seeded):
    seeded.add("mod", "password-a", "moderator")
    with pytest.raises(ValueError, match="already exists"):
        seeded.add("MOD", "password-b", "viewer")


@pytest.mark.parametrize("name", ["night-a/b", "/", ".", "..", "tab\there", "new\nline"])
def test_a_name_the_panel_cannot_route_is_refused(seeded, name):
    """`/api/users/<name>/role` cannot reach a name with a slash (decoded
    before routing) or a dot segment (folded by the browser), so the account
    could be made and then never changed or deleted from the panel."""
    with pytest.raises(ValueError, match="cannot contain a slash"):
        seeded.add(name, "s3cret-pw", "viewer")
    assert [u.name for u in seeded.load()] == ["root"]


def test_a_name_with_dots_inside_is_fine(seeded):
    seeded.add("j.doe", "s3cret-pw", "viewer")
    assert seeded.get("j.doe") is not None


def test_an_unknown_role_is_refused_on_add(store):
    with pytest.raises(ValueError, match="Unknown role"):
        store.add("mod", "password-a", "superuser")


# ── a corrupt file must not lock anyone out ─────────────────────────────


def test_a_corrupt_file_reads_as_empty_and_says_so(store):
    store.path.write_text("{not json at all", encoding="utf-8")
    assert store.load() == []
    assert store.degraded is True


def test_a_corrupt_file_still_lets_the_old_password_in(store):
    """The whole point of `degraded`. A panel nobody can enter, on a box whose
    disk hiccuped, is worse than falling back to the credential that worked
    before accounts existed — and anyone who can corrupt this file already has
    the filesystem."""
    store.path.write_text("[[[", encoding="utf-8")
    fallback = hash_password("old-password")

    user = store.authenticate("anyone", "old-password", fallback_hash=fallback)
    assert user is not None and user.role == "admin"
    assert store.authenticate("anyone", "wrong", fallback_hash=fallback) is None


def test_entries_that_are_not_objects_are_skipped_not_fatal(store):
    store.path.write_text(
        json.dumps({"users": ["nonsense", {"name": "", "role": "admin"},
                              {"name": "real", "role": "admin", "password_hash": "x"}]}),
        encoding="utf-8",
    )
    assert [u.name for u in store.load()] == ["real"]


def test_a_bare_list_is_accepted_too(store):
    """Tolerant on read: a hand-written file is the likely source of one."""
    store.path.write_text(
        json.dumps([{"name": "a", "role": "viewer", "password_hash": "x"}]), encoding="utf-8"
    )
    assert [u.name for u in store.load()] == ["a"]


def test_an_entry_with_no_role_defaults_to_the_least_privileged(store):
    store.path.write_text(
        json.dumps({"users": [{"name": "a", "password_hash": "x"}]}), encoding="utf-8"
    )
    assert store.load()[0].role == "viewer"


def test_a_write_refuses_while_the_file_is_unreadable(store):
    """Reading shrugs at a corrupt file; writing must not.

    Every mutator loads, edits the list and saves the whole set back — so a
    load that quietly returned [] would **replace** whatever the file holds
    with one new row. The two failures are not symmetric: a bad read costs a
    downgrade to the old password, a bad write costs the accounts.
    """
    store.path.write_text('{"users": [ truncated', encoding="utf-8")
    before = store.path.read_text(encoding="utf-8")

    for call in (
        lambda: store.add("boss", "password-a", "admin"),
        lambda: store.remove("boss"),
        lambda: store.set_role("boss", "viewer"),
        lambda: store.set_password("boss", "password-x"),
    ):
        with pytest.raises(ValueError, match="could not be read"):
            call()
    assert store.path.read_text(encoding="utf-8") == before


def test_the_first_account_has_to_be_an_admin(store):
    """The third door into "a panel nobody can administer". `remove` and
    `set_role` already close the other two, and a CLI that creates a lone
    viewer would walk straight through this one — on a migrated install the
    old single password has stopped working by then."""
    with pytest.raises(ValueError, match="has to be an admin"):
        store.add("looker", "password-a", "viewer")
    assert store.exists() is False

    store.add("boss", "password-a", "admin")
    assert store.add("looker", "password-a", "viewer").role == "viewer"


# ── the last admin ──────────────────────────────────────────────────────


def test_the_last_admin_cannot_be_removed(store):
    """The one irreversible mistake this store can make: a panel with no admin
    cannot be administered and cannot be repaired from inside itself."""
    store.add("boss", "password-a", "admin")
    store.add("mod", "password-b", "moderator")
    with pytest.raises(ValueError, match="only admin"):
        store.remove("boss")
    assert store.get("boss") is not None


def test_an_admin_can_be_removed_when_another_remains(store):
    store.add("boss", "password-a", "admin")
    store.add("deputy", "password-b", "admin")
    assert store.remove("boss").name == "boss"
    assert [u.name for u in store.load()] == ["deputy"]


def test_the_last_admin_cannot_be_demoted_either(store):
    """Same hole, reached through the other door."""
    store.add("boss", "password-a", "admin")
    with pytest.raises(ValueError, match="only admin"):
        store.set_role("boss", "viewer")


def test_demoting_an_admin_is_fine_when_another_remains(store):
    store.add("boss", "password-a", "admin")
    store.add("deputy", "password-b", "admin")
    assert store.set_role("boss", "viewer").role == "viewer"


def test_removing_somebody_who_is_not_there_is_none_not_an_error(store):
    assert store.remove("ghost") is None


# ── the shortest password (plans/68) ────────────────────────────────────


def test_a_password_shorter_than_the_minimum_is_never_set(seeded):
    """Both doors that write a hash: a new account, and a new password."""
    from app.web.auth import MIN_PASSWORD_LENGTH

    short = "x" * (MIN_PASSWORD_LENGTH - 1)
    with pytest.raises(ValueError, match=f"at least {MIN_PASSWORD_LENGTH}"):
        seeded.add("mod", short, "moderator")
    assert seeded.get("mod") is None

    with pytest.raises(ValueError, match=f"at least {MIN_PASSWORD_LENGTH}"):
        seeded.set_password("root", short)
    assert seeded.authenticate("root", "rootpass") is not None

    assert seeded.add("mod", "x" * MIN_PASSWORD_LENGTH, "moderator").role == "moderator"


def test_a_short_password_set_before_the_minimum_still_signs_in(store):
    """The rule is about choosing a password, not about having one: an
    account or a single password from before it keeps working until its owner
    changes it."""
    store.save([User(name="boss", role="admin", password_hash=hash_password("abc"))])
    assert store.authenticate("boss", "abc") is not None

    empty = UserStore(path=store.path.with_name("none.json"))
    assert empty.authenticate("", "abc", fallback_hash=hash_password("abc")) is not None


# ── authentication ──────────────────────────────────────────────────────


def test_authenticate_returns_the_user(seeded):
    seeded.add("mod", "s3cret-pw", "moderator")
    user = seeded.authenticate("mod", "s3cret-pw")
    assert user is not None and user.role == "moderator"


def test_a_wrong_password_returns_none(seeded):
    seeded.add("mod", "s3cret-pw", "moderator")
    assert seeded.authenticate("mod", "nope") is None


def test_an_unknown_user_returns_none(seeded):
    seeded.add("mod", "s3cret-pw", "moderator")
    assert seeded.authenticate("ghost", "s3cret-pw") is None


def test_an_unknown_user_still_pays_for_a_hash(seeded, monkeypatch):
    """scrypt is slow on purpose, so returning early for a name that does not
    exist makes "which names are real" readable off a stopwatch."""
    seeded.add("mod", "s3cret-pw", "moderator")
    calls: list[str] = []
    import app.web.users as users_mod

    real = users_mod.verify_password
    monkeypatch.setattr(
        users_mod, "verify_password", lambda p, h: calls.append(h) or real(p, h)
    )

    seeded.authenticate("ghost", "whatever")
    assert calls, "no hash was verified for an unknown user"


def test_a_failed_fallback_login_also_pays_for_a_hash(store, monkeypatch):
    """The same oracle on the other branch — a box with no accounts yet. Found
    by mutation testing: the equivalent test above has accounts, so it never
    reached this path and a missing dummy hash here survived it."""
    import app.web.users as users_mod

    calls: list[str] = []
    real = users_mod.verify_password
    monkeypatch.setattr(
        users_mod, "verify_password", lambda p, h: calls.append(h) or real(p, h)
    )

    assert store.authenticate("admin", "whatever", fallback_hash="") is None
    assert calls, "no hash was verified when there are no accounts and no fallback"


def test_the_fallback_is_ignored_once_accounts_exist(seeded):
    """Otherwise the old single password stays a permanent second key to a
    panel whose owner thinks they replaced it with accounts."""
    seeded.add("mod", "s3cret-pw", "moderator")
    fallback = hash_password("old-password")
    assert seeded.authenticate("admin", "old-password", fallback_hash=fallback) is None


# ── migration ───────────────────────────────────────────────────────────


def test_migration_turns_the_old_password_into_an_admin(store, sample_config):
    sample_config.web_password_hash = hash_password("old-password")

    admin = store.migrate_from_config(sample_config)

    assert admin is not None and admin.name == "admin" and admin.role == "admin"
    assert store.authenticate("admin", "old-password") is not None


def test_migration_leaves_the_config_hash_in_place(store, sample_config):
    """It stays the fallback while this is young. Removing a working
    credential is not something a migration should do on its own."""
    original = hash_password("old-password")
    sample_config.web_password_hash = original

    store.migrate_from_config(sample_config)

    assert sample_config.web_password_hash == original


def test_migration_is_a_no_op_when_accounts_already_exist(seeded, sample_config):
    seeded.add("mod", "password-b", "moderator")
    sample_config.web_password_hash = hash_password("old-password")

    assert seeded.migrate_from_config(sample_config) is None
    assert [u.name for u in seeded.load()] == ["root", "mod"]


def test_migration_is_a_no_op_on_a_fresh_install(store, sample_config):
    """No password yet — `zsm web --set-password` still creates the first one."""
    sample_config.web_password_hash = ""
    assert store.migrate_from_config(sample_config) is None
    assert store.exists() is False


def test_creating_the_app_migrates_once(sample_config, tmp_path, monkeypatch):
    """The wiring: create_app runs it, and running it twice changes nothing."""
    from app.web import server

    sample_config.web_password_hash = hash_password("old-password")
    app = server.create_app(sample_config)
    store = app.state.ctx.users

    assert store.exists()
    assert [u.name for u in store.load()] == ["admin"]

    before = store.path.read_text(encoding="utf-8")
    server.create_app(sample_config)
    assert store.path.read_text(encoding="utf-8") == before


def test_creating_the_app_writes_nothing_without_a_password(sample_config):
    from app.web import server

    sample_config.web_password_hash = ""
    app = server.create_app(sample_config)
    assert app.state.ctx.users.exists() is False


# ── the migrated account stays tied to the single password (plans/68) ───


def _rows(store):
    return json.loads(store.path.read_text(encoding="utf-8"))["users"]


def test_migration_marks_the_account_it_makes(store, sample_config):
    sample_config.web_password_hash = hash_password("old-password")

    store.migrate_from_config(sample_config)
    store.add("mod", "password-b", "moderator")

    assert store.get("admin").migrated is True
    assert store.get("mod").migrated is False
    # Only on that row, so every other row keeps the shape it always had.
    assert [r.get("migrated") for r in _rows(store)] == [True, None]


def test_a_file_from_before_the_marker_gets_it_on_the_row_made_from_the_config(
    store, sample_config
):
    original = hash_password("old-password")
    store.save([
        User(name="admin", role="admin", password_hash=original),
        User(name="mod", role="moderator", password_hash=hash_password("password-b")),
    ])
    sample_config.web_password_hash = original

    assert store.migrate_from_config(sample_config) is None

    assert [r.get("migrated") for r in _rows(store)] == [True, None]


def test_no_row_with_the_configs_hash_marks_nothing(store, sample_config):
    """The admin changed their password before the marker existed, so no row
    is provably the one the migration made. A guess would tie someone else's
    password to the admin fallback."""
    store.save([User(name="admin", role="admin", password_hash=hash_password("rotated-1"))])
    before = store.path.read_text(encoding="utf-8")
    sample_config.web_password_hash = hash_password("old-password")

    store.migrate_from_config(sample_config)

    assert store.path.read_text(encoding="utf-8") == before


def test_marking_never_writes_over_an_unreadable_file(store, sample_config):
    store.path.write_text("{ truncated", encoding="utf-8")
    sample_config.web_password_hash = hash_password("old-password")

    store.migrate_from_config(sample_config)

    assert store.path.read_text(encoding="utf-8") == "{ truncated"


def test_every_store_write_keeps_the_marker(store, sample_config):
    sample_config.web_password_hash = hash_password("old-password")
    store.migrate_from_config(sample_config)
    store.add("deputy", "password-b", "admin")

    assert store.set_password("admin", "new-password").migrated is True
    assert store.set_role("admin", "moderator").migrated is True
    assert store.set_totp("admin", "JBSWY3DPEHPK3PXP").migrated is True
    assert store.get("admin").migrated is True


def test_only_the_migrated_admin_moves_the_fallback():
    marked = User(name="admin", role="admin", password_hash="scrypt$new", migrated=True)
    other = User(name="deputy", role="admin", password_hash="scrypt$other")

    assert linked_fallback_hash(marked) == "scrypt$new"
    assert linked_fallback_hash(other) is None
    assert linked_fallback_hash(other, removed=True) is None


def test_deleting_the_migrated_account_clears_the_fallback():
    marked = User(name="admin", role="admin", password_hash="scrypt$x", migrated=True)
    assert linked_fallback_hash(marked, removed=True) == ""


def test_leaving_admin_clears_the_fallback_and_nothing_else_does():
    """A role change of the migrated account: demotion from admin clears the
    fallback like a deletion; a promotion or a move between the other roles
    leaves it as it is, so a promotion restores nothing."""
    def marked(role):
        return User(name="admin", role=role, password_hash="scrypt$x", migrated=True)

    assert linked_fallback_hash(marked("viewer"), previous_role="admin") == ""
    assert linked_fallback_hash(marked("moderator"), previous_role="admin") == ""
    assert linked_fallback_hash(marked("admin"), previous_role="viewer") is None
    assert linked_fallback_hash(marked("moderator"), previous_role="viewer") is None
    assert linked_fallback_hash(marked("admin"), previous_role="admin") is None
    other = User(name="deputy", role="viewer", password_hash="scrypt$y")
    assert linked_fallback_hash(other, previous_role="admin") is None


def test_has_no_login_only_with_no_password_and_no_readable_account(sample_config):
    """The one condition behind `zsm web`'s first-password prompt and the
    refusal of `zsm service web install`."""
    from app.web.users import has_no_login

    store = UserStore()  # the machine-wide file `has_no_login` reads
    store.path.parent.mkdir(parents=True, exist_ok=True)
    sample_config.web_password_hash = ""
    assert has_no_login(sample_config) is True

    store.path.write_text("{ truncated", encoding="utf-8")
    assert has_no_login(sample_config) is True

    store.path.unlink()
    store.add("root", "rootpass", "admin")
    assert has_no_login(sample_config) is False

    store.path.unlink()
    sample_config.web_password_hash = hash_password("old-password")
    assert has_no_login(sample_config) is False


def test_a_demoted_migrated_account_does_not_move_the_admin_fallback():
    """The fallback signs in as admin and every role may change its own
    password, so copying a viewer's new password would make it an admin one."""
    demoted = User(name="admin", role="viewer", password_hash="scrypt$x", migrated=True)
    assert linked_fallback_hash(demoted) is None
    assert linked_fallback_hash(demoted, removed=True) == ""


# ── what a surface may see ──────────────────────────────────────────────


def test_public_never_carries_the_hash():
    user = User(name="mod", role="moderator", password_hash="scrypt$secret")
    assert user.public() == {"name": "mod", "role": "moderator", "totp": False}
    assert "password_hash" not in user.public()


# ── step 2: the session carries the name, the role is read live ─────────


def test_a_blank_username_uses_the_single_account(store):
    """The login form has no username field yet (step 5). A migrated install
    has exactly one account, and accepting a blank name against it is what
    keeps the existing form working across the migration."""
    store.add("admin", "s3cret-pw", "admin")
    assert store.authenticate("", "s3cret-pw").name == "admin"


def test_a_blank_username_is_ambiguous_once_there_are_two(store):
    """Picking one would be a guess about who is at the keyboard."""
    store.add("admin", "s3cret-pw", "admin")
    store.add("mod", "other-password", "moderator")
    assert store.authenticate("", "s3cret-pw") is None


def test_login_opens_a_session_named_for_the_account(sample_config):
    from fastapi.testclient import TestClient

    from app.web import server
    from app.web.auth import SESSION_COOKIE_NAME, hash_password

    sample_config.web_password_hash = hash_password("testpass123")
    app = server.create_app(sample_config)
    client = TestClient(app)

    assert client.post("/api/auth/login", json={"password": "testpass123"}).status_code == 200
    token = client.cookies.get(SESSION_COOKIE_NAME)
    assert app.state.ctx.sessions.validate(token) == "admin"


def test_login_accepts_an_explicit_username(sample_config):
    from fastapi.testclient import TestClient

    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password("testpass123")
    app = server.create_app(sample_config)
    app.state.ctx.users.add("mod", "modpass-1", "moderator")
    client = TestClient(app)

    resp = client.post("/api/auth/login", json={"username": "mod", "password": "modpass-1"})
    assert resp.status_code == 200
    assert client.get("/api/auth/me").json() == {
        "authenticated": True, "username": "mod", "role": "moderator", "totp": False,
        "instance": {"id": "", "label": "", "count": 0, "branch": "stable"},
        "install": install_id(),
    }


def test_a_wrong_username_and_a_wrong_password_give_the_same_answer(sample_config):
    """Two messages would say which names exist."""
    from fastapi.testclient import TestClient

    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password("testpass123")
    app = server.create_app(sample_config)
    app.state.ctx.users.add("mod", "modpass-1", "moderator")
    client = TestClient(app)

    bad_name = client.post("/api/auth/login", json={"username": "ghost", "password": "modpass-1"})
    bad_pass = client.post("/api/auth/login", json={"username": "mod", "password": "nope"})
    assert bad_name.status_code == bad_pass.status_code == 401
    assert bad_name.json() == bad_pass.json()


def test_the_role_is_read_live_not_off_the_session(sample_config):
    """Demoting somebody takes effect on their next request, not whenever
    their week-old cookie happens to expire."""
    from fastapi.testclient import TestClient

    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password("testpass123")
    app = server.create_app(sample_config)
    users = app.state.ctx.users
    users.add("boss", "password-a", "admin")  # so the demotion below is allowed
    users.add("mod", "modpass-1", "moderator")

    client = TestClient(app)
    client.post("/api/auth/login", json={"username": "mod", "password": "modpass-1"})
    assert client.get("/api/auth/me").json()["role"] == "moderator"

    users.set_role("mod", "viewer")
    assert client.get("/api/auth/me").json()["role"] == "viewer"


def test_deleting_an_account_ends_its_open_session(sample_config):
    """Step 2 left this open and said step 4 was where it would matter. It did.

    The obvious way to write "no row means the pre-accounts install" is to
    return admin for a missing row — which would make *deleting* an account
    **promote** its live session to full control. `deps._role_for` tells the
    two apart by whether any account exists at all.
    """
    from fastapi.testclient import TestClient

    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password("testpass123")
    app = server.create_app(sample_config)
    users = app.state.ctx.users
    users.add("boss", "password-a", "admin")
    users.add("mod", "modpass-1", "moderator")

    client = TestClient(app)
    client.post("/api/auth/login", json={"username": "mod", "password": "modpass-1"})
    assert client.get("/api/auth/me").status_code == 200
    users.remove("mod")

    assert client.get("/api/auth/me").status_code == 401
    # And not as an admin on the way past, which is the escalation.
    assert client.get("/api/settings").status_code == 401


def test_the_websocket_accepts_the_same_session_rest_does(sample_config):
    """`validate` returns a name now. A truthiness test on the socket and an
    `is None` test on REST would eventually disagree — the shape of "the panel
    loads, logs you in, and never updates"."""
    from fastapi.testclient import TestClient

    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password("testpass123")
    app = server.create_app(sample_config)
    client = TestClient(app)
    client.post("/api/auth/login", json={"password": "testpass123"})

    assert client.get("/api/auth/me").status_code == 200
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "ping"})


def test_two_account_writes_at_once_both_land(seeded, monkeypatch):
    """Each write loads, edits and saves the whole file. The routes run them in
    the thread pool, so two at once both read the same file and the second
    save dropped the first one's account."""
    import threading
    import time

    real_load = UserStore.load

    def slow_load(self):
        users = real_load(self)
        time.sleep(0.05)  # both threads read before either saves
        return users

    monkeypatch.setattr(UserStore, "load", slow_load)
    threads = [
        threading.Thread(target=seeded.add, args=(name, "longenough1", "viewer"))
        for name in ("alice", "bob")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    monkeypatch.setattr(UserStore, "load", real_load)
    assert sorted(u.name for u in seeded.load()) == ["alice", "bob", "root"]
