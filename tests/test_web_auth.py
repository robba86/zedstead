"""Tests for app/web/auth.py (hashing, sessions, rate limiter) and the
/api/auth/* endpoints."""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.web.install_id import install_id
from app.web.auth import (
    DEV_RELOAD_ENV,
    SECURE_SESSION_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    LoginRateLimiter,
    SessionStore,
    dev_session_path,
    hash_password,
    verify_password,
)

# ---------------------------------------------------------------------------
# Unit tests: auth primitives
# ---------------------------------------------------------------------------


def test_hash_and_verify_roundtrip():
    hashed = hash_password("correct horse battery staple")
    assert hashed.startswith("scrypt$")
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong password", hashed)


@pytest.mark.real_scrypt_cost
def test_a_new_hash_uses_the_raised_scrypt_cost():
    """plans/68 AUTH-14: p=5 (OWASP's equal of N=2^17 at r=8). The suite
    hashes at p=1 (conftest `_cheap_scrypt`); this test checks auth.py's own."""
    _algo, n, r, p, _salt, _hash = hash_password("correct horse battery staple").split("$")
    assert (n, r, p) == ("16384", "8", "5")


def test_a_hash_made_with_the_old_cost_still_verifies():
    """A hash carries its own N, r and p, so raising the cost cannot lock out
    a password set before it (this one is scrypt N=16384, r=8, p=1)."""
    import hashlib

    salt = bytes(range(16))
    derived = hashlib.scrypt(b"old password", salt=salt, n=16384, r=8, p=1, dklen=32)
    old = f"scrypt$16384$8$1${salt.hex()}${derived.hex()}"
    assert verify_password("old password", old)
    assert not verify_password("wrong password", old)


def test_verify_password_rejects_empty_hash():
    assert not verify_password("anything", "")


def test_verify_password_rejects_malformed_hash():
    assert not verify_password("anything", "not-a-valid-hash")


def test_session_store_create_validate_revoke():
    store = SessionStore()
    token = store.create("admin")
    # The name comes back, not just a bool (plans/43 step 2).
    assert store.validate(token) == "admin"
    store.revoke(token)
    assert store.validate(token) is None


def test_a_session_needs_a_name():
    """A blank would be a session that is valid but falsy — the kind of value
    one caller tests with `is None` and the next with `if not`."""
    with pytest.raises(ValueError):
        SessionStore().create("")


def test_session_store_rejects_unknown_token():
    store = SessionStore()
    assert store.validate("does-not-exist") is None
    assert store.validate(None) is None


def test_session_store_revoke_all_except():
    store = SessionStore()
    keep = store.create("admin")
    other = store.create("admin")
    store.revoke_all_except(keep)
    assert store.validate(keep)
    assert not store.validate(other)


DAY = 24 * 3600


@pytest.fixture()
def clock(monkeypatch):
    """`time.time` as seen by the session store, moved by hand."""
    from app.web import auth

    now = [1_000_000.0]
    monkeypatch.setattr(auth.time, "time", lambda: now[0])
    return now


def test_an_unused_session_still_expires_after_the_sliding_ttl(clock):
    store = SessionStore()
    token = store.create("admin")
    clock[0] += 7 * DAY + 1
    assert store.validate(token) is None


def test_a_session_in_constant_use_still_ends_at_its_maximum_age(clock):
    """The sliding TTL alone let a session that kept being used live for ever
    (plans/68). It now ends 30 days after login whatever its use."""
    from app.web.auth import SESSION_MAX_AGE_SECONDS

    assert SESSION_MAX_AGE_SECONDS == 30 * DAY
    store = SessionStore()
    ended: list[set[str]] = []
    store.on_revoke = ended.append
    token = store.create("admin")
    login = clock[0]

    while clock[0] + 6 * DAY < login + 30 * DAY:
        clock[0] += 6 * DAY
        assert store.validate(token) == "admin"
    clock[0] = login + 30 * DAY - 1
    assert store.validate(token) == "admin"

    clock[0] = login + 30 * DAY + 1
    assert store.validate(token) is None
    assert {token} in ended  # its WebSocket is closed with it


def test_the_maximum_age_survives_a_restart(tmp_path, clock):
    path = tmp_path / "dev-sessions.json"
    store = SessionStore(persist_path=path)
    token = store.create("admin")
    login = clock[0]
    for _ in range(4):
        clock[0] += 6 * DAY
        store.validate(token)
    store.revoke(store.create("admin"))  # a write, carrying the slid expiry

    clock[0] = login + 29 * DAY
    assert SessionStore(persist_path=path).validate(token) == "admin"
    clock[0] = login + 30 * DAY + 1
    assert SessionStore(persist_path=path).validate(token) is None


def test_revoke_all_for_leaves_everybody_else_alone():
    """The scoped half. `revoke_all_except` is right for one shared password
    and wrong once there are accounts — a moderator rotating theirs must not
    sign the admin out."""
    store = SessionStore()
    mine = store.create("mandy")
    my_other = store.create("mandy")
    theirs = store.create("admin")

    store.revoke_all_for("mandy", mine)

    assert store.validate(mine)
    assert not store.validate(my_other)
    assert store.validate(theirs)


def test_revoke_all_for_matches_the_name_case_insensitively():
    """`UserStore.get` matches that way, so two sessions for one account can
    hold different spellings of its name — and the one spelled differently is
    exactly the session a password change needs to end."""
    store = SessionStore()
    upper = store.create("Mandy")
    lower = store.create("mandy")

    store.revoke_all_for("MANDY")

    assert not store.validate(upper)
    assert not store.validate(lower)


def test_revoke_all_for_a_blank_name_drops_nothing():
    """The direction matters: the neighbouring `revoke_all_except(None)` clears
    the whole store, so a blank falling through to "match everything" would
    turn one caller's missing username into a panel-wide logout."""
    store = SessionStore()
    token = store.create("admin")

    store.revoke_all_for("")
    store.revoke_all_for("   ")

    assert store.validate(token)


class _LoginDuringIteration(dict):
    """A token table that, the first time something iterates it, has another
    thread open a session (a login on the event loop) and waits for it.

    That is the interleaving behind the 500 (plans/68): `require_session`
    validates in the thread pool, and `validate` prunes by iterating the
    table, while a login on the event loop adds to it. Without the store's
    lock the login lands mid-iteration and the iterator raises "dictionary
    changed size during iteration". With it, the login waits; the wait here
    is bounded so the test cannot hang either way.
    """

    def __init__(self, store, *args):
        super().__init__(*args)
        self.store = store
        self.login: threading.Thread | None = None
        self.login_waited = None
        self.late_token: list[str] = []

    def items(self):
        it = iter(super().items())
        first = next(it)
        if self.login is None:
            self.login = threading.Thread(
                target=lambda: self.late_token.append(self.store.create("late"))
            )
            self.login.start()
            self.login.join(timeout=0.3)
            self.login_waited = self.login.is_alive()
        yield first
        yield from it


@pytest.mark.parametrize(
    "iterate",
    [
        pytest.param(lambda store, token: store.validate(token), id="validate"),
        pytest.param(lambda store, token: store.revoke_all_for("nobody"), id="revoke_all_for"),
    ],
)
def test_a_login_waits_while_another_thread_walks_the_sessions(iterate):
    store = SessionStore()
    token = store.create("admin")
    table = _LoginDuringIteration(store, store._sessions)
    store._sessions = table

    iterate(store, token)  # raised RuntimeError before the lock

    table.login.join(timeout=5)
    assert table.login_waited is True
    assert store.validate(token) == "admin"
    assert store.validate(table.late_token[0]) == "late"


def test_login_rate_limiter_trips_after_threshold():
    """The original global counter, unchanged: a nameless attempt is held by
    it, which is every attempt until the login form grows a username field."""
    limiter = LoginRateLimiter(max_failures=10, window_seconds=300)
    for _ in range(10):
        limiter.record_failure()
    assert not limiter.is_limited()
    limiter.record_failure()
    assert limiter.is_limited()
    limiter.record_success()
    assert not limiter.is_limited()


# ── per-account limiting (plans/43 step 6) ─────────────────────────────────


def _limiter(**kw):
    return LoginRateLimiter(
        max_failures=10, window_seconds=300, max_user_failures=3, **kw
    )


def test_guessing_at_one_account_does_not_lock_out_another():
    """The denial of service the split exists to remove. Before this, eleven
    guesses at `admin` locked out every other account as well."""
    limiter = _limiter()
    for _ in range(4):
        limiter.record_failure("admin")
    assert limiter.is_limited("admin")
    assert not limiter.is_limited("mandy")
    assert not limiter.is_limited("")


def test_the_ceiling_is_not_reachable_by_naming_a_real_account():
    """A known account's failures stay in its own bucket. If they also fed the
    global window, the global would trip first and the per-account limit would
    never be the thing that fired — the bug would survive its own fix."""
    limiter = _limiter()
    for name in ("admin", "mandy", "looker"):
        for _ in range(4):
            limiter.record_failure(name)
    assert not limiter.is_limited("")
    assert all(limiter.is_limited(n) for n in ("admin", "mandy", "looker"))


def test_spraying_made_up_names_does_not_lock_out_a_real_account():
    """The other door into the same denial of service. The caller only ever
    passes a name it verified, so a made-up one arrives as `""` and lands on
    the global ceiling — which must not then refuse someone who exists."""
    limiter = _limiter()
    for _ in range(11):
        limiter.record_failure("")
    assert limiter.is_limited("")
    assert not limiter.is_limited("admin")


def test_a_success_clears_only_the_account_that_succeeded():
    """Otherwise anyone holding a viewer account can wipe the counter that is
    holding off a guessing run against admin, on demand."""
    limiter = _limiter()
    for _ in range(4):
        limiter.record_failure("admin")
    limiter.record_success("looker")
    assert limiter.is_limited("admin")
    limiter.record_success("admin")
    assert not limiter.is_limited("admin")


def test_a_bucket_is_case_insensitive():
    """Accounts are matched case-insensitively, so alternating the spelling
    must not hand out a fresh allowance per spelling."""
    limiter = _limiter()
    for name in ("admin", "Admin", "ADMIN", "aDmIn"):
        limiter.record_failure(name)
    assert limiter.is_limited("AdMiN")


def test_a_bucket_empties_as_its_window_slides():
    limiter = LoginRateLimiter(
        max_failures=10, window_seconds=0.05, max_user_failures=1
    )
    limiter.record_failure("admin")
    limiter.record_failure("admin")
    assert limiter.is_limited("admin")
    time.sleep(0.06)
    assert not limiter.is_limited("admin")


def test_the_number_of_tracked_accounts_is_bounded():
    """A bound on memory has to fail by forgiving someone early, not by
    growing. Reaching it means the install has more accounts than the bound.

    **Which** bucket goes matters, and asserting only the size does not say:
    dropping the *newest* keeps the count just as well while forgiving the
    account being attacked right now. Found by mutation testing — `min` to
    `max` in `_evict_oldest` passed the first version of this test.
    """
    limiter = LoginRateLimiter(
        max_failures=10, window_seconds=300, max_user_failures=0, max_tracked_users=3
    )
    for i in range(10):
        limiter.record_failure(f"user{i}")
    assert set(limiter._per_user) == {"user7", "user8", "user9"}
    assert limiter.is_limited("user9"), "the newest must still be held"
    assert not limiter.is_limited("user0"), "the stalest is the one forgiven"


def test_attempts_still_being_checked_count_against_the_limit():
    """An attempt holds its place from `begin`, so attempts that arrive
    together get what the same attempts one after another would have got:
    three failures allowed means four attempts, at once or in turn."""
    limiter = _limiter()  # max_user_failures=3
    held = [limiter.begin("admin") for _ in range(4)]
    assert all(held)
    assert limiter.begin("admin") is None
    assert not limiter.is_limited("mandy")

    held[0].close()
    held[0].close()  # a second close gives back nothing more
    assert limiter.begin("admin") is not None
    assert limiter.begin("admin") is None


def test_a_failed_attempt_keeps_its_place_as_a_failure():
    """`fail` moves the attempt from in flight to failed in one step, and the
    `close` that follows it in a `finally` does not release it a second time."""
    limiter = _limiter()
    for _ in range(4):
        attempt = limiter.begin("admin")
        attempt.fail()
        attempt.close()
    assert limiter.is_limited("admin")
    assert limiter.begin("admin") is None


# ---------------------------------------------------------------------------
# HTTP-level tests
# ---------------------------------------------------------------------------

PASSWORD = "testpass123"


@pytest.fixture()
def client(sample_config):
    from app.web import server

    sample_config.web_password_hash = hash_password(PASSWORD)
    app = server.create_app(sample_config)
    return TestClient(app)


def test_login_wrong_password_401(client):
    resp = client.post("/api/auth/login", json={"password": "nope"})
    assert resp.status_code == 401


def test_login_correct_password_sets_cookie(client):
    resp = client.post("/api/auth/login", json={"password": PASSWORD})
    assert resp.status_code == 200
    assert SESSION_COOKIE_NAME in resp.cookies


def test_one_accounts_lockout_does_not_lock_out_another(client):
    """End to end. `create_app` migrated the config password into `admin`;
    guessing at it must not cost `mandy` her login."""
    state = client.app.state.ctx
    state.login_limiter = LoginRateLimiter(max_user_failures=0)
    state.users.add("mandy", "modpass123", "moderator")

    bad = {"username": "admin", "password": "no"}
    assert client.post("/api/auth/login", json=bad).status_code == 401
    assert client.post("/api/auth/login", json=bad).status_code == 429
    assert client.post(
        "/api/auth/login", json={"username": "mandy", "password": "modpass123"}
    ).status_code == 200


def test_a_made_up_name_cannot_hand_itself_a_fresh_allowance(client):
    """The submitted string never becomes a bucket key — only a name the
    server looked up does. Were it otherwise, rotating made-up names would
    give an endless supply of empty buckets and the ceiling would never trip.
    """
    client.app.state.ctx.login_limiter = LoginRateLimiter(max_failures=0)
    assert client.post(
        "/api/auth/login", json={"username": "ghost-1", "password": "no"}
    ).status_code == 401
    assert client.post(
        "/api/auth/login", json={"username": "ghost-2", "password": "no"}
    ).status_code == 429


@pytest.mark.parametrize("username", ["admin", "", "nobody"])
def test_a_burst_of_simultaneous_guesses_gets_no_more_than_the_limit(client, username):
    """Attempts sent at once must not all be checked before the first fails.

    The password check takes a while (scrypt, stood in for here by a sleep),
    and the limit used to be read before it and the failure written after it,
    so every attempt of a burst passed the check: 12 at once against an
    account allowed six were all checked. Both counters: a named account (a
    blank name here, with one account, is that account too) and the global
    window an unknown name lands on.
    """
    import asyncio

    import httpx

    state = client.app.state.ctx
    state.login_limiter = LoginRateLimiter(max_failures=5, max_user_failures=5)

    def slow_wrong(*_args, **_kwargs):
        time.sleep(0.2)
        return None

    state.users.authenticate = slow_wrong

    async def burst():
        transport = httpx.ASGITransport(app=client.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            return await asyncio.gather(*[
                c.post("/api/auth/login", json={"username": username, "password": f"guess{i}"})
                for i in range(12)
            ])

    codes = sorted(r.status_code for r in asyncio.run(burst()))
    assert codes == [401] * 6 + [429] * 6


def test_login_with_no_password_set_is_rejected(sample_config):
    from app.web import server

    app = server.create_app(sample_config)  # web_password_hash left empty
    client = TestClient(app)
    resp = client.post("/api/auth/login", json={"password": "anything"})
    assert resp.status_code == 401


def test_me_requires_session(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401

    client.post("/api/auth/login", json={"password": PASSWORD})
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    # `username`/`role` since plans/43 step 2. The migration turned the
    # config's single password into the `admin` account, and the role is read
    # from users.json at request time rather than off the session.
    # `totp` since TODO B5 — whether *this* caller has a second factor, which
    # is False for an account that never enrolled one.
    # `instance` since plans/62 step 1. `sample_config` is the pre-instances
    # layout, which has no instance to name.
    assert resp.json() == {
        "authenticated": True, "username": "admin", "role": "admin", "totp": False,
        "instance": {"id": "", "label": "", "count": 0, "branch": "stable"},
        "install": install_id(),
    }


def _instance_seen(sample_config, instance_id, registry=None, raw=None):
    """`/api/auth/me`'s `instance` for a panel serving *instance_id*, with
    `instances.json` holding *registry* (or the *raw* text, to break it)."""
    from app.server.instances import save_registry
    from app.shared.paths import instances_file
    from app.web import server

    if registry is not None:
        save_registry(registry)
    if raw is not None:
        instances_file().write_text(raw, encoding="utf-8")
    sample_config.instance_id = instance_id
    sample_config.web_password_hash = hash_password(PASSWORD)
    client = TestClient(server.create_app(sample_config))
    client.post("/api/auth/login", json={"password": PASSWORD})
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    return resp.json()["instance"]


def test_me_names_main_when_it_is_the_only_instance(sample_config):
    """D2 is "always": a single-`main` panel names itself too."""
    from app.server.instances import InstanceRegistry, ServerInstance

    registry = InstanceRegistry(instances=(ServerInstance("main"),), default="main")
    assert _instance_seen(sample_config, "main", registry) == {
        "id": "main", "label": "main", "count": 1, "branch": "stable"
    }


def test_me_names_the_instance_by_its_label(sample_config):
    from app.server.instances import InstanceRegistry, ServerInstance

    registry = InstanceRegistry(
        instances=(ServerInstance("main"), ServerInstance("pvp", "PvP")), default="main"
    )
    assert _instance_seen(sample_config, "pvp", registry) == {
        "id": "pvp", "label": "PvP", "count": 2, "branch": "stable"
    }


def test_me_survives_an_unreadable_registry(sample_config):
    """A hand edit gone wrong costs the count, not the login check."""
    assert _instance_seen(sample_config, "pvp", raw="{not json") == {
        "id": "pvp", "label": "pvp", "count": 0, "branch": "stable"
    }


def test_logout_clears_session(client):
    client.post("/api/auth/login", json={"password": PASSWORD})
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200

    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_a_new_login_ends_the_session_the_browser_came_with(client):
    """plans/68 AUTH-10. Signing in again, as anybody, replaces the cookie,
    so the session behind the old one has to end: nothing else would."""
    store = client.app.state.ctx.sessions
    client.post("/api/auth/login", json={"password": PASSWORD})
    old = client.cookies.get(SESSION_COOKIE_NAME)
    assert store.validate(old) == "admin"

    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200

    new = client.cookies.get(SESSION_COOKIE_NAME)
    assert new != old
    assert store.validate(old) is None
    assert store.validate(new) == "admin"


def test_a_failed_login_leaves_the_session_alone(client):
    client.post("/api/auth/login", json={"password": PASSWORD})

    assert client.post("/api/auth/login", json={"password": "nope"}).status_code == 401

    assert client.get("/api/auth/me").status_code == 200


def test_login_rate_limited_after_repeated_failures(client):
    """A blank name on this one-account install is a guess at `admin`, so it
    is held to that account's limit (plans/68 AUTH-17)."""
    for _ in range(6):
        resp = client.post("/api/auth/login", json={"password": "nope"})
        assert resp.status_code == 401
    resp = client.post("/api/auth/login", json={"password": "nope"})
    assert resp.status_code == 429


@pytest.mark.parametrize("first, then", [("", "admin"), ("admin", "")])
def test_one_account_has_one_guess_budget_however_it_is_named(client, first, then):
    """plans/68 AUTH-17. With one account a blank name signs in as it, so
    blank and named guesses fill the same bucket: six wrong in all, not six
    named plus eleven blank."""
    for _ in range(6):
        assert client.post(
            "/api/auth/login", json={"username": first, "password": "nope"}
        ).status_code == 401

    assert client.post(
        "/api/auth/login", json={"username": then, "password": PASSWORD}
    ).status_code == 429


def test_a_blank_name_with_several_accounts_still_fills_the_global_window(client):
    """Several accounts: a blank name signs in as nobody, so it stays on the
    global window and does not touch `admin`'s bucket."""
    client.app.state.ctx.users.add("mandy", "modpass123", "moderator")
    for _ in range(11):
        assert client.post("/api/auth/login", json={"password": "nope"}).status_code == 401

    assert client.app.state.ctx.login_limiter.is_limited("")
    assert client.post(
        "/api/auth/login", json={"username": "admin", "password": PASSWORD}
    ).status_code == 200


# ---------------------------------------------------------------------------
# Dev-mode session persistence (`zsm web --reload`) — see auth.dev_session_path
# ---------------------------------------------------------------------------


def test_dev_session_path_is_none_without_the_env_var(monkeypatch):
    """Default (shipped) behaviour: no file, sessions die with the process."""
    monkeypatch.delenv(DEV_RELOAD_ENV, raising=False)
    assert dev_session_path() is None


def test_dev_session_path_lives_in_config_dir(monkeypatch, tmp_path):
    monkeypatch.setenv(DEV_RELOAD_ENV, "1")
    monkeypatch.setattr("app.shared.paths.CONFIG_DIR", tmp_path)
    assert dev_session_path() == tmp_path / "dev-sessions.json"


def test_session_store_without_persist_path_writes_nothing(tmp_path):
    store = SessionStore()
    store.create("admin")
    assert list(tmp_path.iterdir()) == []


def test_session_survives_a_restart_when_persisted(tmp_path):
    """The whole point: a uvicorn reload must not bounce the browser to /login."""
    path = tmp_path / "dev-sessions.json"
    token = SessionStore(persist_path=path).create("admin")

    restarted = SessionStore(persist_path=path)  # fresh process, same file
    assert restarted.validate(token)


def test_the_fallback_mark_survives_a_restart(tmp_path):
    """A session opened with the pre-accounts password is the only one the
    role gate trusts while users.json cannot be read, so a reload must not
    turn it into an ordinary one, or the other way round."""
    path = tmp_path / "dev-sessions.json"
    store = SessionStore(persist_path=path)
    fallback, account = store.create("admin", fallback=True), store.create("admin")

    restarted = SessionStore(persist_path=path)
    assert restarted.is_fallback(fallback)
    assert not restarted.is_fallback(account)
    assert not restarted.is_fallback(None)


def test_persisted_session_revoke_survives_a_restart(tmp_path):
    path = tmp_path / "dev-sessions.json"
    store = SessionStore(persist_path=path)
    token = store.create("admin")
    store.revoke(token)

    assert not SessionStore(persist_path=path).validate(token)


def test_persisted_revoke_all_except_survives_a_restart(tmp_path):
    path = tmp_path / "dev-sessions.json"
    store = SessionStore(persist_path=path)
    keep, other = store.create("admin"), store.create("admin")
    store.revoke_all_except(keep)

    restarted = SessionStore(persist_path=path)
    assert restarted.validate(keep)
    assert not restarted.validate(other)


def test_expired_persisted_sessions_are_dropped_on_load(tmp_path):
    path = tmp_path / "dev-sessions.json"
    token = SessionStore(ttl_seconds=-1, persist_path=path).create("admin")
    assert not SessionStore(persist_path=path).validate(token)


def test_corrupt_session_file_is_ignored(tmp_path):
    """A truncated write must cost one login, not a startup crash."""
    path = tmp_path / "dev-sessions.json"
    path.write_text("{not json")
    store = SessionStore(persist_path=path)
    assert not store.validate("anything")
    assert store.validate(store.create("admin"))  # still usable


def test_persisted_session_file_is_not_world_readable(tmp_path):
    path = tmp_path / "dev-sessions.json"
    SessionStore(persist_path=path).create("admin")
    assert path.stat().st_mode & 0o077 == 0


# ---------------------------------------------------------------------------
# Session cookie `Secure` attribute (plan 15 / TODO-2 B2)
#
# The invariant these pin: Secure must never default to True. The common
# deployment is a loopback HTTP bind, where a Secure cookie is silently dropped
# by the browser and nobody can log in at all.
# ---------------------------------------------------------------------------


def _session_set_cookie(resp, name: str = SESSION_COOKIE_NAME) -> str:
    """The Set-Cookie header for the session cookie, as sent on the wire.

    ``resp.cookies`` drops the attributes, so the raw header is the only place
    Secure/HttpOnly/SameSite are visible.
    """
    for header in resp.headers.get_list("set-cookie"):
        if header.startswith(f"{name}="):
            return header
    raise AssertionError(
        f"no {name} Set-Cookie header in {resp.headers.get_list('set-cookie')}"
    )


def test_the_cookie_lasts_as_long_as_a_session_can(client):
    """plans/68-review-findings B3. The cookie's Max-Age is sent once, at
    login, so it must be the session's hard limit: the 7-day TTL slides on
    the server only, and a browser holding a 7-day cookie dropped a session
    still in daily use. Idle sessions still end on the server at 7 days."""
    from app.web.auth import SESSION_MAX_AGE_SECONDS

    resp = client.post("/api/auth/login", json={"password": PASSWORD})

    assert f"Max-Age={SESSION_MAX_AGE_SECONDS};" in _session_set_cookie(resp)


def test_login_cookie_not_secure_over_http(client):
    resp = client.post("/api/auth/login", json={"password": PASSWORD})
    assert resp.status_code == 200
    assert "Secure" not in _session_set_cookie(resp)


def test_login_cookie_secure_over_https(sample_config):
    from app.web import server

    sample_config.web_password_hash = hash_password(PASSWORD)
    app = server.create_app(sample_config)
    https_client = TestClient(app, base_url="https://testserver")

    resp = https_client.post("/api/auth/login", json={"password": PASSWORD})
    assert resp.status_code == 200
    assert "Secure" in _session_set_cookie(resp, SECURE_SESSION_COOKIE_NAME)


def test_login_cookie_secure_when_forced(sample_config):
    from app.web import server

    sample_config.web_password_hash = hash_password(PASSWORD)
    sample_config.web_force_secure_cookie = True
    app = server.create_app(sample_config)

    resp = TestClient(app).post("/api/auth/login", json={"password": PASSWORD})
    assert resp.status_code == 200
    assert "Secure" in _session_set_cookie(resp, SECURE_SESSION_COOKIE_NAME)


def test_logout_clears_cookie_with_matching_attributes(client):
    client.post("/api/auth/login", json={"password": PASSWORD})
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200

    header = _session_set_cookie(resp)
    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    # Plain http here, so the deletion must not carry Secure either — an
    # attribute mismatch can leave the cookie in place in some browsers.
    assert "Secure" not in header


def test_cookie_is_secure_helper_prefers_force_flag(sample_config):
    import types

    from app.web.deps import cookie_is_secure

    sample_config.web_force_secure_cookie = True
    request = types.SimpleNamespace(url=types.SimpleNamespace(scheme="http"))
    state = types.SimpleNamespace(config=sample_config)

    assert cookie_is_secure(request, state) is True


def test_cookie_is_secure_helper_follows_scheme_by_default(sample_config):
    import types

    from app.web.deps import cookie_is_secure

    state = types.SimpleNamespace(config=sample_config)
    http = types.SimpleNamespace(url=types.SimpleNamespace(scheme="http"))
    https = types.SimpleNamespace(url=types.SimpleNamespace(scheme="https"))
    ws = types.SimpleNamespace(url=types.SimpleNamespace(scheme="ws"))
    wss = types.SimpleNamespace(url=types.SimpleNamespace(scheme="wss"))

    assert cookie_is_secure(http, state) is False
    assert cookie_is_secure(https, state) is True
    assert cookie_is_secure(ws, state) is False
    assert cookie_is_secure(wss, state) is True  # the WebSocket over TLS


# ---------------------------------------------------------------------------
# The cookie's name follows Secure (plans/68): ``__Host-zsm_session`` over
# https, ``zsm_session`` over http, and every reader uses the same rule.
# ---------------------------------------------------------------------------


def _https_app(sample_config):
    from app.web import server

    sample_config.web_password_hash = hash_password(PASSWORD)
    app = server.create_app(sample_config)
    client = TestClient(app, base_url="https://testserver")
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return app, client


def test_over_https_the_cookie_is_a_host_cookie(sample_config):
    """``__Host-`` is only honoured with Secure, Path=/ and no Domain, so the
    header must carry all three, and nothing goes out under the plain name."""
    from app.web import server

    sample_config.web_password_hash = hash_password(PASSWORD)
    app = server.create_app(sample_config)
    resp = TestClient(app, base_url="https://testserver").post(
        "/api/auth/login", json={"password": PASSWORD}
    )
    header = _session_set_cookie(resp, SECURE_SESSION_COOKIE_NAME)
    assert "Secure" in header and "Path=/" in header
    assert "Domain" not in header
    assert not any(
        h.startswith(f"{SESSION_COOKIE_NAME}=") for h in resp.headers.get_list("set-cookie")
    )


def test_over_https_the_session_works_and_the_plain_name_is_not_read(sample_config):
    app, client = _https_app(sample_config)
    assert client.get("/api/auth/me").status_code == 200

    token = client.cookies.get(SECURE_SESSION_COOKIE_NAME)
    assert token
    plain = TestClient(app, base_url="https://testserver")
    plain.cookies.set(SESSION_COOKIE_NAME, token)
    assert plain.get("/api/auth/me").status_code == 401


def test_the_websocket_reads_the_same_name(sample_config):
    from starlette.websockets import WebSocketDisconnect

    app, client = _https_app(sample_config)
    token = client.cookies.get(SECURE_SESSION_COOKIE_NAME)

    with client.websocket_connect(
        "wss://testserver/ws", headers={"cookie": f"{SECURE_SESSION_COOKIE_NAME}={token}"}
    ) as ws:
        assert ws.receive_json()["type"] == "hello"

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "wss://testserver/ws", headers={"cookie": f"{SESSION_COOKIE_NAME}={token}"}
        ) as ws:
            ws.receive_json()


def test_over_https_logout_ends_the_session_and_deletes_the_host_cookie(sample_config):
    app, client = _https_app(sample_config)
    token = client.cookies.get(SECURE_SESSION_COOKIE_NAME)

    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200
    header = _session_set_cookie(resp, SECURE_SESSION_COOKIE_NAME)
    assert "Secure" in header and "Path=/" in header
    assert app.state.ctx.sessions.validate(token) is None


def test_over_https_your_own_password_change_keeps_this_session(sample_config):
    """Both routes keep the caller's session by reading its token. Read under
    the wrong name, the token is None and the caller is signed out too."""
    app, client = _https_app(sample_config)
    assert client.post(
        "/api/settings/password", json={"current": PASSWORD, "new": "brandnew456"}
    ).status_code == 200
    assert client.get("/api/auth/me").status_code == 200

    assert client.put(
        "/api/users/admin/password", json={"password": "another-one", "current": "brandnew456"}
    ).status_code == 200
    assert client.get("/api/auth/me").status_code == 200


def test_start_server_leaves_forwarded_allow_ips_none_when_unset(sample_config):
    """Empty `web_trusted_proxies` must pass None, not "127.0.0.1".

    Passing the string explicitly overrides uvicorn's own FORWARDED_ALLOW_IPS
    environment handling, which the reverse proxy docs page documents as the
    off-host-proxy lever. See plan 15's Amendments.
    """
    import asyncio
    from unittest.mock import patch

    from app.web import server

    sample_config.web_trusted_proxies = ""
    captured = {}

    class _FakeServer:
        def __init__(self, cfg):
            captured["cfg"] = cfg

        async def serve(self):
            return None

    with patch("uvicorn.Config", side_effect=lambda *a, **kw: kw) as cfg, patch(
        "uvicorn.Server", _FakeServer
    ):
        asyncio.run(server.start_server(sample_config))

    kwargs = captured["cfg"]
    assert kwargs["proxy_headers"] is True
    assert kwargs["forwarded_allow_ips"] is None
    assert cfg.called


def test_start_server_passes_configured_trusted_proxies(sample_config):
    import asyncio
    from unittest.mock import patch

    from app.web import server

    sample_config.web_trusted_proxies = " 203.0.113.10 "
    captured = {}

    class _FakeServer:
        def __init__(self, cfg):
            captured["cfg"] = cfg

        async def serve(self):
            return None

    with patch("uvicorn.Config", side_effect=lambda *a, **kw: kw), patch(
        "uvicorn.Server", _FakeServer
    ):
        asyncio.run(server.start_server(sample_config))

    assert captured["cfg"]["forwarded_allow_ips"] == "203.0.113.10"


def test_trusting_every_proxy_is_offered_with_its_cost():
    """plans/68 NET-8. `*` stays possible (multi-host TLS termination needs
    it), but the help and the reverse-proxy guide say what it hands to every
    client instead of listing it as a normal choice."""
    from app.shared import settings_registry
    from tests.conftest import site_doc

    help_text = settings_registry._BY_KEY["web_trusted_proxies"].description
    assert "(or '*' for any)" not in help_text
    assert "'*' trusts every client" in help_text
    assert "access log" in help_text

    guide = site_doc("reverse-proxy.md").read_text(encoding="utf-8")
    row = next(line for line in guide.splitlines() if line.startswith("| `web_trusted_proxies`"))
    assert "(or `*`)" not in row
    assert "every client" in row
