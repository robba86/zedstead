"""Two-factor sign-in through the login route (TODO B5).

`tests/test_totp.py` proves the algorithm against its RFCs. What is asserted
here is the part the RFCs leave open and that a panel gets wrong: when the
second factor is asked for, what a missing code costs versus a wrong one,
which counter a failed code fills, and that the whole thing stays invisible to
every account that never enrolled.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.web import totp
from app.web.auth import hash_password

PASSWORD = "testpass123"


@pytest.fixture()
def app(sample_config):
    """A migrated install: `admin` exists, made from the single password."""
    from app.web import server

    sample_config.web_password_hash = hash_password(PASSWORD)
    return server.create_app(sample_config)


@pytest.fixture()
def secret(app):
    """`admin` with two-factor sign-in switched on."""
    key = totp.generate_secret()
    app.state.ctx.users.set_totp("admin", key)
    return key


def _login(client, **body):
    return client.post("/api/auth/login", json={"password": PASSWORD, **body})


# ── an account without a second factor is untouched ─────────────────────


def test_an_account_that_never_enrolled_signs_in_as_before(app):
    """The feature is opt-in per account. Every install that ignores B5 has to
    behave exactly as it did, including the blank-username form."""
    client = TestClient(app)
    assert _login(client).status_code == 200
    assert client.get("/api/auth/me").json()["totp"] is False


def test_a_code_sent_to_an_account_without_one_is_ignored(app):
    """Not refused. The form sends what it has, and an account that disabled
    its second factor between the two halves of a sign-in would otherwise be
    told its correct password was wrong."""
    client = TestClient(app)
    assert _login(client, code="123456").status_code == 200


def test_the_pre_accounts_fallback_has_no_second_factor(app):
    """`authenticate` synthesises a User for an install with no accounts, and
    there is no row there to enroll — so it must not be possible for that path
    to demand a code nobody can produce."""
    app.state.ctx.users.path.unlink()
    client = TestClient(app)
    assert _login(client).status_code == 200


def _totp_card_text() -> str:
    page = Path(__file__).resolve().parent.parent / "webui" / "src" / "pages" / "AccountPage.jsx"
    text = page.read_text(encoding="utf-8")
    return text[text.index("function TotpCard"):text.index("export default function AccountPage")]


def _is_real_command(line: str) -> bool:
    """Whether ``zsm <group…> <command> [--flag]`` names a registered command
    and, for a flag, one of its options. Placeholders (``&lt;name&gt;``) end
    the walk."""
    import click

    from app.__main__ import main

    command: click.Command = main
    for word in line.split()[1:]:
        if word.startswith("&lt;"):
            break
        if word.startswith("-"):
            return any(word in p.opts for p in command.params)
        if not isinstance(command, click.Group) or word not in command.commands:
            return False
        command = command.commands[word]
    return True


def test_the_two_factor_card_says_the_fallback_needs_no_code(app, secret):
    """plans/68 2P-W3. The fallback above has no second factor, and the admin
    account made from it keeps it set to its own password, so while users.json
    cannot be read that password alone signs in as admin even with 2FA on.
    The 2FA card says so, and the command it gives for changing the fallback
    is one that exists."""
    app.state.ctx.users.path.write_text("{ truncated", encoding="utf-8")
    assert _login(TestClient(app)).status_code == 200

    card = _totp_card_text()
    assert "users.json" in card and "without a code" in card
    commands = re.findall(r"<code>(zsm [^<]+)</code>", card)
    assert "zsm web --set-password" in commands
    assert all(_is_real_command(c) for c in commands), commands


# ── the two-step sign-in ────────────────────────────────────────────────


def test_the_right_password_alone_is_not_enough(app, secret):
    client = TestClient(app)
    resp = _login(client)

    assert resp.status_code == 401
    assert resp.json()["totp_required"] is True
    assert client.get("/api/auth/me").status_code == 401


def test_the_password_and_the_code_together_sign_you_in(app, secret):
    client = TestClient(app)
    resp = _login(client, code=totp.now_code(secret))

    assert resp.status_code == 200
    assert client.get("/api/auth/me").json()["totp"] is True


def test_a_wrong_password_never_reveals_that_a_code_is_wanted(app, secret):
    """The order is load-bearing. Answering `totp_required` before checking the
    password would tell an unauthenticated caller which accounts have a second
    factor — and therefore which are worth attacking and which are not."""
    client = TestClient(app)
    resp = client.post("/api/auth/login", json={"password": "wrong", "code": "123456"})

    assert resp.status_code == 401
    assert "totp_required" not in resp.json()
    assert resp.json()["detail"] == "Invalid password"


def test_a_wrong_code_is_refused(app, secret):
    client = TestClient(app)
    wrong = "000000" if totp.now_code(secret) != "000000" else "111111"

    resp = _login(client, code=wrong)
    assert resp.status_code == 401
    assert client.get("/api/auth/me").status_code == 401


def test_a_stale_code_is_refused(app, secret):
    """Two steps out. The window is 90 seconds, not "whatever the phone last
    showed"."""
    stale = totp.code_at(secret, totp.counter_at() - 3)
    assert _login(TestClient(app), code=stale).status_code == 401


def test_a_code_cannot_be_used_twice(app, secret):
    """RFC 6238 §5.2, through the whole route: the same six digits stay valid
    for the rest of their window, which is long enough to be read off a shared
    screen and spent before the person who typed them reaches the dashboard."""
    code = totp.now_code(secret)

    first = TestClient(app)
    assert _login(first, code=code).status_code == 200

    second = TestClient(app)
    assert _login(second, code=code).status_code == 401
    assert second.get("/api/auth/me").status_code == 401


def test_two_sign_ins_with_the_same_code_at_once_let_one_in(app, secret, monkeypatch):
    """plans/68 AUTH-15. Both requests verify the code before either records
    it (the barrier holds each one just after `verify`), so the check and the
    record have to be one step, or both sign in."""
    import threading

    real_verify = totp.verify
    barrier = threading.Barrier(2, timeout=10)

    def verify_then_wait(*args, **kwargs):
        matched = real_verify(*args, **kwargs)
        barrier.wait()
        return matched

    monkeypatch.setattr(totp, "verify", verify_then_wait)
    code = totp.now_code(secret)
    statuses: list[int] = []

    def sign_in():
        statuses.append(_login(TestClient(app), code=code).status_code)

    threads = [threading.Thread(target=sign_in) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(statuses) == [200, 401]


def test_the_replay_guard_does_not_block_the_next_login(app, secret):
    """The point of refusing a replay is not to refuse the next sign-in."""
    state = app.state.ctx
    now = totp.counter_at()

    first = TestClient(app)
    assert _login(first, code=totp.code_at(secret, now)).status_code == 200

    # Pretend a step has passed rather than sleeping 30 seconds for it.
    state.totp_replay.spend("admin", now)
    later = TestClient(app)
    # `verify` still measures against the real clock, so the code for the
    # *next* step is inside the window and above the spent one.
    assert _login(later, code=totp.code_at(secret, now + 1)).status_code == 200


def test_one_account_s_spent_code_does_not_block_another(app, secret):
    """The replay record is per account. Two people on one panel would
    otherwise take turns locking each other out."""
    key = totp.generate_secret()
    app.state.ctx.users.add("mandy", "mandypass", "moderator")
    app.state.ctx.users.set_totp("mandy", key)

    admin = TestClient(app)
    assert _login(admin, username="admin", code=totp.now_code(secret)).status_code == 200

    mandy = TestClient(app)
    resp = mandy.post(
        "/api/auth/login",
        json={"username": "mandy", "password": "mandypass", "code": totp.now_code(key)},
    )
    assert resp.status_code == 200


# ── what a failed code costs ────────────────────────────────────────────


def test_a_wrong_code_fills_the_rate_limiter(app, secret):
    """Without this, holding the password buys unlimited guesses at six digits
    — and the whole security of a 6-digit code rests on how few tries there
    are. Three steps are in the window at once, so it is 3-in-a-million per
    attempt, and only the attempt count keeps that a small number."""
    client = TestClient(app)
    wrong = "000000" if totp.now_code(secret) != "000000" else "111111"

    for _ in range(12):
        client.post("/api/auth/login", json={"password": PASSWORD, "code": wrong})

    # A blank name with one account is that account's bucket (AUTH-17).
    assert app.state.ctx.login_limiter.is_limited("admin")
    resp = _login(client, code=totp.now_code(secret))
    assert resp.status_code == 429


def test_a_missing_code_is_not_counted_as_a_failure(app, secret):
    """The first half of a two-step sign-in is not a wrong guess. Counting it
    would lock out anyone whose browser reloaded the login page twice."""
    client = TestClient(app)
    for _ in range(12):
        _login(client)

    assert not app.state.ctx.login_limiter.is_limited("admin")
    assert _login(client, code=totp.now_code(secret)).status_code == 200


def test_a_failed_code_is_logged_as_its_own_event(app, secret):
    """It means somebody had the password. Folded into `login_failed` it would
    be buried in the noise an exposed panel gets all day — which is the one
    entry in this log worth waking up for.

    `sample_config` already points CONFIG_DIR at a tmp dir, and the log and
    `users.json` live in the same one — re-pointing it here would move the
    accounts out from under the app mid-test and quietly send login down the
    pre-accounts fallback, where there is no second factor to fail.
    """
    from app.web import access_log

    client = TestClient(app)
    wrong = "000000" if totp.now_code(secret) != "000000" else "111111"
    _login(client, code=wrong)

    entries = access_log.read_recent()
    assert [e["event"] for e in entries] == ["login_totp_failed"]
    assert entries[0]["user"] == "admin"


def test_a_successful_second_factor_is_logged_as_a_plain_login(app, secret):
    """One `login` line either way. The log records that somebody got in, not
    how many doors they came through — that is what the account's own 2FA
    state says, and duplicating it here would let the two disagree."""
    from app.web import access_log

    assert _login(TestClient(app), code=totp.now_code(secret)).status_code == 200
    assert [e["event"] for e in access_log.read_recent()] == ["login"]


# ── the store ───────────────────────────────────────────────────────────


def test_the_secret_never_reaches_a_browser(app, secret):
    """It *is* the second factor. Serving it to a session would make the
    factor exactly as strong as the session that asked for it — which is the
    thing it exists to be independent of."""
    client = TestClient(app)
    _login(client, code=totp.now_code(secret))

    body = client.get("/api/users")
    assert body.status_code == 200
    assert secret not in body.text
    assert "totp_secret" not in body.text
    assert body.json()["users"][0]["totp"] is True


def test_an_unusable_secret_is_refused_when_it_is_stored(app):
    """The only place that failure is cheap. A stored secret that cannot be
    decoded verifies no code at all, which reads exactly like a wrong clock —
    and the way back in needs a code."""
    with pytest.raises(ValueError):
        app.state.ctx.users.set_totp("admin", "not base32!")
    assert app.state.ctx.users.get("admin").totp_enabled is False


def test_turning_it_off_leaves_the_account_signing_in_with_a_password(app, secret):
    app.state.ctx.users.set_totp("admin", "")

    assert app.state.ctx.users.get("admin").totp_enabled is False
    assert _login(TestClient(app)).status_code == 200


def test_a_password_change_does_not_un_enroll_the_phone(app, secret):
    """Two independent factors. Dropping one because the other changed is a
    downgrade nobody asked for, and a silent one."""
    app.state.ctx.users.set_password("admin", "brandnew456")

    user = app.state.ctx.users.get("admin")
    assert user.totp_secret == secret


def test_a_role_change_does_not_un_enroll_the_phone(app, secret):
    app.state.ctx.users.add("other", "otherpass", "admin")
    app.state.ctx.users.set_role("admin", "viewer")

    assert app.state.ctx.users.get("admin").totp_secret == secret


def test_an_account_without_one_writes_no_totp_key(app):
    """An install that never touches B5 keeps the file it always had."""
    import json

    raw = json.loads(app.state.ctx.users.path.read_text(encoding="utf-8"))
    assert "totp_secret" not in raw["users"][0]


def test_a_secret_survives_a_round_trip_through_the_file(app, secret):
    fresh_store = type(app.state.ctx.users)(path=app.state.ctx.users.path)
    assert fresh_store.get("admin").totp_secret == secret


# ── enrolling, from the browser ─────────────────────────────────────────


@pytest.fixture()
def signed_in(app):
    """An `admin` session with no second factor yet."""
    client = TestClient(app)
    assert _login(client).status_code == 200
    return client


def _enroll(client, password=PASSWORD):
    """Run the whole two-request enrollment and return the secret."""
    started = client.post("/api/auth/totp/start", json={"password": password})
    assert started.status_code == 200, started.text
    key = started.json()["secret"]
    done = client.post("/api/auth/totp/enable", json={"code": totp.now_code(key)})
    assert done.status_code == 200, done.text
    return key


def test_enrolling_takes_two_requests_and_a_code(app, signed_in):
    key = _enroll(signed_in)
    spent = app.state.ctx.totp_replay.last("admin")

    assert app.state.ctx.users.get("admin").totp_secret == key
    assert _login(TestClient(app)).status_code == 401
    # The next code the phone shows. The one that enrolled is spent.
    assert _login(TestClient(app), code=totp.code_at(key, spent + 1)).status_code == 200


def test_starting_saves_nothing(app, signed_in):
    """A secret stored on the first request would switch 2FA on for every
    abandoned setup page — against a phone that never scanned it, and the way
    back in needs a code."""
    resp = signed_in.post("/api/auth/totp/start", json={"password": PASSWORD})

    assert resp.status_code == 200
    assert app.state.ctx.users.get("admin").totp_enabled is False
    assert _login(TestClient(app)).status_code == 200


def test_a_wrong_confirmation_code_saves_nothing(app, signed_in):
    signed_in.post("/api/auth/totp/start", json={"password": PASSWORD})

    resp = signed_in.post("/api/auth/totp/enable", json={"code": "000000"})
    assert resp.status_code == 400
    assert app.state.ctx.users.get("admin").totp_enabled is False


def test_confirming_without_starting_is_refused(app, signed_in):
    resp = signed_in.post("/api/auth/totp/enable", json={"code": "123456"})
    assert resp.status_code == 409
    assert app.state.ctx.users.get("admin").totp_enabled is False


def test_starting_again_replaces_the_pending_key(app, signed_in):
    """Someone who reloads the setup page is looking at a new key. Confirming
    against the old one would enroll something their phone is not showing."""
    first = signed_in.post("/api/auth/totp/start", json={"password": PASSWORD}).json()
    second = signed_in.post("/api/auth/totp/start", json={"password": PASSWORD}).json()
    assert first["secret"] != second["secret"]

    assert signed_in.post(
        "/api/auth/totp/enable", json={"code": totp.now_code(first["secret"])}
    ).status_code == 400
    assert signed_in.post(
        "/api/auth/totp/enable", json={"code": totp.now_code(second["secret"])}
    ).status_code == 200
    assert app.state.ctx.users.get("admin").totp_secret == second["secret"]


def test_enrolling_needs_your_password(app, signed_in):
    """The attack this stops runs the other way from disabling: somebody with a
    stolen session who enrolls *their* phone has locked out the real owner, who
    cannot undo it from inside the panel."""
    resp = signed_in.post("/api/auth/totp/start", json={"password": "wrong"})

    # 400, not 401: the caller is signed in, and the web UI sends every 401 to
    # /login instead of showing the sentence (plans/68).
    assert resp.status_code == 400
    assert resp.json()["detail"] == "That is not your current password."
    assert signed_in.get("/api/auth/me").status_code == 200
    assert app.state.ctx.users.get("admin").totp_enabled is False


@pytest.mark.parametrize("route", ["/api/auth/totp/start", "/api/auth/totp/disable"])
def test_guessing_your_password_here_is_held_to_the_sign_in_limit(app, signed_in, route):
    """plans/68 AUTH-8. Both password checks count in the login limiter, in
    the account's own bucket, so a session cannot try passwords faster than
    the login form allows."""
    for _ in range(6):
        assert signed_in.post(route, json={"password": "wrong"}).status_code == 400

    assert signed_in.post(route, json={"password": PASSWORD}).status_code == 429
    assert app.state.ctx.users.get("admin").totp_enabled is False


def test_disabling_needs_your_password(app, signed_in):
    """Somebody at an unlocked browser can already do whatever the role allows.
    The one setting whose entire job is to survive that must not be one of
    them."""
    _enroll(signed_in)

    key = app.state.ctx.users.get("admin").totp_secret
    code = _next_code(app, key)
    assert signed_in.post(
        "/api/auth/totp/disable", json={"password": "wrong", "code": code}
    ).status_code == 400
    assert signed_in.get("/api/auth/me").status_code == 200
    assert app.state.ctx.users.get("admin").totp_enabled is True

    assert signed_in.post(
        "/api/auth/totp/disable", json={"password": PASSWORD, "code": code}
    ).status_code == 200
    assert app.state.ctx.users.get("admin").totp_enabled is False
    assert _login(TestClient(app)).status_code == 200


def _next_code(app, key):
    """The next code the phone shows: the one that enrolled is spent."""
    return totp.code_at(key, app.state.ctx.totp_replay.last("admin") + 1)


@pytest.mark.parametrize("code", [None, "", "000000"])
def test_turning_it_off_needs_a_current_code(app, signed_in, code):
    """A stolen session plus the password must not be enough to strip the one
    factor that exists to survive a stolen password."""
    key = _enroll(signed_in)
    if code == "000000" and _next_code(app, key) == code:
        code = "111111"
    body = {"password": PASSWORD} if code is None else {"password": PASSWORD, "code": code}

    resp = signed_in.post("/api/auth/totp/disable", json=body)

    assert resp.status_code == 400
    assert signed_in.get("/api/auth/me").status_code == 200
    assert app.state.ctx.users.get("admin").totp_secret == key


def test_enrolling_again_needs_a_current_code(app, signed_in):
    """Re-enrolling replaces the secret, so without a code it turns the
    second factor off and on again with somebody else's phone."""
    key = _enroll(signed_in)

    refused = signed_in.post("/api/auth/totp/start", json={"password": PASSWORD})
    assert refused.status_code == 400
    assert app.state.ctx.totp_pending.get("admin") is None

    started = signed_in.post(
        "/api/auth/totp/start", json={"password": PASSWORD, "code": _next_code(app, key)}
    )
    assert started.status_code == 200, started.text
    assert app.state.ctx.users.get("admin").totp_secret == key


def test_guessing_the_code_here_is_held_to_the_sign_in_limit(app, signed_in):
    """With the password in hand, only the attempt count keeps a 6-digit code
    from being guessed. A right password must not reset that count."""
    key = _enroll(signed_in)
    wrong = "000000" if _next_code(app, key) != "000000" else "111111"
    for _ in range(6):
        assert signed_in.post(
            "/api/auth/totp/disable", json={"password": PASSWORD, "code": wrong}
        ).status_code == 400

    resp = signed_in.post(
        "/api/auth/totp/disable", json={"password": PASSWORD, "code": _next_code(app, key)}
    )
    assert resp.status_code == 429
    assert app.state.ctx.users.get("admin").totp_enabled is True


def test_the_start_response_carries_a_typable_key_and_a_uri(app, signed_in):
    """There is no QR code — rendering one needs a dependency on one side of
    the wire or the other — so the typable form is the whole of the setup
    path and cannot be an unbroken run of 32 characters."""
    body = signed_in.post("/api/auth/totp/start", json={"password": PASSWORD}).json()

    assert totp.normalise_secret(body["formatted"]) == body["secret"]
    assert " " in body["formatted"]
    assert body["uri"].startswith("otpauth://totp/zsm:admin?")
    assert body["digits"] == 6 and body["period"] == 30


def test_a_second_factor_is_not_an_admin_privilege(app):
    """Default-deny makes every write admin. A moderator who cannot protect
    their own account is the weakest password on the panel whatever their role
    says."""
    app.state.ctx.users.add("mandy", "mandypass", "moderator")
    client = TestClient(app)
    client.post("/api/auth/login", json={"username": "mandy", "password": "mandypass"})

    key = _enroll(client, password="mandypass")
    assert app.state.ctx.users.get("mandy").totp_secret == key
    # And still not an admin.
    assert client.get("/api/users").status_code == 403


def test_you_can_only_enroll_your_own_account(app, signed_in):
    """There is no route here that names somebody else — asserted against the
    live table rather than by reading the file, so a future `/totp/{name}`
    cannot be added without this failing."""
    from app.web import permissions

    totp_routes = [
        (method, path)
        for method, path in permissions.enumerate_routes(app)
        if "/totp" in path
    ]
    assert totp_routes == [
        ("POST", "/api/auth/totp/disable"),
        ("POST", "/api/auth/totp/enable"),
        ("POST", "/api/auth/totp/start"),
    ]
    assert not any("{" in path for _m, path in totp_routes)


def test_a_pre_accounts_session_is_told_to_make_an_account(app):
    """`authenticate` synthesises a User out of the single password, and there
    is no row there to attach a secret to. That is a real state, so it gets an
    answer rather than a 500 three frames down."""
    app.state.ctx.users.path.unlink()
    client = TestClient(app)
    _login(client)

    resp = client.post("/api/auth/totp/start", json={"password": PASSWORD})
    assert resp.status_code == 409
    assert "single shared password" in resp.json()["detail"]


def test_enrolling_clears_a_replay_record_from_an_earlier_secret(app, signed_in):
    """A step number from a different secret means nothing, and keeping it
    would refuse the first code from the new one. What is left is the step
    of the code that enrolled, which is spent."""
    earlier = totp.counter_at() + 5
    app.state.ctx.totp_replay.spend("admin", earlier)

    key = _enroll(signed_in)
    spent = app.state.ctx.totp_replay.last("admin")
    assert spent is not None and spent < earlier
    assert _login(TestClient(app), code=totp.code_at(key, spent + 1)).status_code == 200


def test_the_code_that_turned_it_on_cannot_sign_in(app, signed_in):
    """RFC 6238 §5.2 covers the code that confirms the setup too. It stays
    valid for the rest of its 90 seconds, so unless enabling spends it like a
    login, whoever read it off the screen during setup signs in once with it."""
    key = signed_in.post("/api/auth/totp/start", json={"password": PASSWORD}).json()["secret"]
    code = totp.now_code(key)
    assert signed_in.post("/api/auth/totp/enable", json={"code": code}).status_code == 200

    replayed = TestClient(app)
    assert _login(replayed, code=code).status_code == 401
    assert replayed.get("/api/auth/me").status_code == 401
