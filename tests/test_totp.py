"""Tests for app/web/totp.py — TOTP written against its own RFC (TODO B5).

The first two blocks are the **published test vectors**, RFC 4226 Appendix D
(HOTP, the truncation) and RFC 6238 Appendix B (TOTP, the time step). They are
the reason writing this rather than importing `pyotp` is defensible: they are
what a dependency's own test suite would be checking, and without them a home
-rolled implementation is only proven to agree with itself.

Everything after them is about the parts the RFC leaves to the verifier — the
window, replay, and what to do with input a person typed.
"""

from __future__ import annotations

import base64

import pytest

from app.web import totp

# RFC 4226 Appendix D / RFC 6238 Appendix B both use this ASCII secret.
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode().rstrip("=")


# ── RFC 4226 Appendix D: HOTP, SHA-1, 6 digits ──────────────────────────

RFC4226_VECTORS = [
    (0, "755224"),
    (1, "287082"),
    (2, "359152"),
    (3, "969429"),
    (4, "338314"),
    (5, "254676"),
    (6, "287922"),
    (7, "162583"),
    (8, "399871"),
    (9, "520489"),
]


@pytest.mark.parametrize("counter,expected", RFC4226_VECTORS)
def test_the_rfc_4226_vectors(counter, expected):
    assert totp.code_at(RFC_SECRET, counter) == expected


def test_a_leading_zero_survives():
    """Vector 7 is `162583`, but the failure this guards is the one an `int()`
    anywhere in the chain would cause: a code beginning with 0 shortening to
    five digits and matching nothing. Asserted on the type, not on a value, so
    it holds for every future code too."""
    for counter, expected in RFC4226_VECTORS:
        got = totp.code_at(RFC_SECRET, counter)
        assert isinstance(got, str) and len(got) == totp.DIGITS
        assert got == expected


# ── RFC 6238 Appendix B: the same secret, on a clock ────────────────────
#
# The published table is 8-digit; these are the 6-digit tails of those values,
# which is what a 6-digit verifier produces for the same steps. The *step*
# arithmetic is what is under test here — Appendix B is the only place that
# pins `T = unix_time / 30` against real numbers.

RFC6238_VECTORS = [
    (59, "287082"),
    (1111111109, "081804"),
    (1111111111, "050471"),
    (1234567890, "005924"),
    (2000000000, "279037"),
    (20000000000, "353130"),
]


@pytest.mark.parametrize("when,expected", RFC6238_VECTORS)
def test_the_rfc_6238_vectors(when, expected):
    assert totp.now_code(RFC_SECRET, timestamp=when) == expected


def test_the_step_is_thirty_seconds():
    """59 and 60 are the boundary the table above straddles: T=1 and T=2."""
    assert totp.counter_at(0) == 0
    assert totp.counter_at(29) == 0
    assert totp.counter_at(30) == 1
    assert totp.counter_at(59) == 1
    assert totp.counter_at(60) == 2


# ── secrets ─────────────────────────────────────────────────────────────


def test_a_generated_secret_is_the_recommended_length():
    """RFC 4226 §4 R6 asks for 160 bits, and it is also what every
    authenticator expects from an otpauth:// URI."""
    secret = totp.generate_secret()
    assert len(base64.b32decode(secret + "=" * (-len(secret) % 8))) == totp.SECRET_BYTES


def test_two_generated_secrets_differ():
    assert len({totp.generate_secret() for _ in range(20)}) == 20


def test_a_generated_secret_carries_no_padding():
    """The `=` is what a manual-entry box chokes on, and it is not part of the
    string an otpauth:// URI carries."""
    assert "=" not in totp.generate_secret()


def test_a_secret_typed_back_with_its_spaces_still_works():
    """`format_secret` groups it in fours so it can be read off a screen
    without losing one's place, and people paste that back verbatim."""
    secret = totp.generate_secret()
    grouped = totp.format_secret(secret)

    assert " " in grouped
    assert totp.normalise_secret(grouped) == secret
    assert totp.code_at(grouped, 1) == totp.code_at(secret, 1)


def test_lowercase_and_hyphens_are_accepted():
    secret = totp.generate_secret()
    typed = secret.lower().replace("", "")
    assert totp.code_at(typed, 3) == totp.code_at(secret, 3)
    assert totp.normalise_secret("abcd-efgh") == "ABCDEFGH"


@pytest.mark.parametrize("bad", ["", "   ", "not base32!", "01234567", "ABC$DEF"])
def test_an_unusable_secret_raises_rather_than_returning_nothing(bad):
    """It has to raise where the secret is *stored*. A `_decode` that shrugged
    would let a bad secret be saved, and then every code would be refused —
    which reads to the person holding the phone exactly like a wrong clock,
    and is unrecoverable from inside the panel.

    `01234567` is in the list because 0 and 1 are **not** in the base32
    alphabet; it is the most plausible typo there is.
    """
    with pytest.raises(ValueError):
        totp.code_at(bad, 1)


def test_verify_refuses_a_bad_secret_instead_of_raising():
    """The other side of the same rule: at *login* a bad stored secret must be
    a refused code, not a 500 on the login route."""
    assert totp.verify("not base32!", "123456") is None


# ── the window ──────────────────────────────────────────────────────────


def test_the_current_code_verifies():
    secret = totp.generate_secret()
    now = 1_700_000_000
    assert totp.verify(secret, totp.now_code(secret, now), timestamp=now) is not None


def test_the_step_before_and_after_are_accepted():
    """A phone's clock drifts and six digits take a few seconds to type. This
    is the only reason `verify` is not an equality check."""
    secret = totp.generate_secret()
    now = 1_700_000_000
    centre = totp.counter_at(now)

    for step in (centre - 1, centre, centre + 1):
        assert totp.verify(secret, totp.code_at(secret, step), timestamp=now) == step


def test_two_steps_out_is_refused():
    """Every extra accepted step is another code in play at once. The window
    is a deliberate width, not a fudge factor."""
    secret = totp.generate_secret()
    now = 1_700_000_000
    centre = totp.counter_at(now)

    assert totp.verify(secret, totp.code_at(secret, centre - 2), timestamp=now) is None
    assert totp.verify(secret, totp.code_at(secret, centre + 2), timestamp=now) is None


def test_a_wrong_code_is_refused():
    secret = totp.generate_secret()
    now = 1_700_000_000
    wrong = "000000" if totp.now_code(secret, now) != "000000" else "111111"
    assert totp.verify(secret, wrong, timestamp=now) is None


@pytest.mark.parametrize("bad", ["", "12345", "1234567", "abcdef", "12345a", None])
def test_anything_that_is_not_six_digits_is_refused(bad):
    """Including `None`, which is what an absent JSON field arrives as."""
    assert totp.verify(totp.generate_secret(), bad, timestamp=1) is None


def test_a_clock_at_the_epoch_refuses_rather_than_crashing():
    """`timestamp=1` puts the window's lower edge at step -1, and there is no
    such step. A box that has not reached its first NTP sync really does sit
    here, and an unhandled `struct.error` on the login route is the worst
    possible way to find that out — found by the test above, which happened to
    verify at `timestamp=1`."""
    secret = totp.generate_secret()
    assert totp.verify(secret, "123456", timestamp=1) is None
    assert totp.verify(secret, totp.now_code(secret, 1), timestamp=1) == 0

    with pytest.raises(ValueError):
        totp.code_at(secret, -1)


def test_spaces_inside_a_typed_code_are_tolerated():
    """Some authenticators display `123 456` and the space comes along on a
    copy. Refusing that is a support ticket, not a security measure."""
    secret = totp.generate_secret()
    now = 1_700_000_000
    code = totp.now_code(secret, now)
    spaced = f"{code[:3]} {code[3:]}"
    assert totp.verify(secret, spaced, timestamp=now) is not None


# ── replay ──────────────────────────────────────────────────────────────


def test_a_spent_code_is_refused_by_the_after_argument():
    """RFC 6238 §5.2. Without this the same six digits stay good for 90
    seconds — long enough to be read off a shared screen and used before the
    person who typed them has finished loading the dashboard."""
    secret = totp.generate_secret()
    now = 1_700_000_000
    code = totp.now_code(secret, now)

    spent = totp.verify(secret, code, timestamp=now)
    assert spent is not None
    assert totp.verify(secret, code, timestamp=now, after=spent) is None


def test_the_older_of_the_two_codes_in_hand_is_refused_too():
    """`after` is compared against the *matched* step, not against now. The
    previous step's code is still inside the window, so a comparison against
    the clock would leave it spendable after a login was observed."""
    secret = totp.generate_secret()
    now = 1_700_000_000
    centre = totp.counter_at(now)

    assert totp.verify(secret, totp.code_at(secret, centre), timestamp=now) == centre
    older = totp.code_at(secret, centre - 1)
    assert totp.verify(secret, older, timestamp=now, after=centre) is None


def test_the_next_code_still_works_after_one_is_spent():
    """The point of refusing a replay is not to refuse the next login."""
    secret = totp.generate_secret()
    now = 1_700_000_000
    spent = totp.verify(secret, totp.now_code(secret, now), timestamp=now)

    later = now + totp.PERIOD
    assert totp.verify(secret, totp.now_code(secret, later), timestamp=later, after=spent)


def test_the_replay_record_never_moves_backwards():
    """Two tabs logging in inside one step, or two calls arriving out of
    order — either would re-open a code that had been spent."""
    replay = totp.Replay()
    replay.spend("admin", 100)
    replay.spend("admin", 99)
    assert replay.last("admin") == 100


def test_the_replay_record_is_per_account():
    replay = totp.Replay()
    replay.spend("admin", 100)
    assert replay.last("mandy") is None


def test_the_replay_record_matches_a_name_case_insensitively():
    """`UserStore.get` does, so a name typed `Admin` must not get a fresh
    record — that would be the replay guard switched off by the shift key."""
    replay = totp.Replay()
    replay.spend("Admin", 100)
    assert replay.last("admin") == 100
    assert replay.last("ADMIN") == 100


def test_a_new_secret_forgets_the_old_step():
    """The step number means nothing against a different secret, and keeping
    it would refuse the first code from the new one."""
    replay = totp.Replay()
    replay.spend("admin", 100)
    replay.forget("admin")
    assert replay.last("admin") is None


def test_the_number_of_tracked_accounts_is_bounded():
    """Keyed by a name a caller supplies part of, so an unbounded dict is a
    memory leak with a request behind it — the same bound `LoginRateLimiter`
    puts on its per-account buckets."""
    replay = totp.Replay(max_tracked=3)
    for i in range(6):
        replay.spend(f"user{i}", 100 + i)

    assert len(replay._spent) == 3
    # The *stalest* goes, not an arbitrary one: asserting the size alone would
    # pass for an eviction that dropped the newest.
    assert set(replay._spent) == {"user3", "user4", "user5"}


def test_a_blank_name_records_nothing():
    replay = totp.Replay()
    replay.spend("", 100)
    replay.spend("   ", 100)
    assert replay._spent == {}


# ── the provisioning URI ────────────────────────────────────────────────


def test_the_uri_carries_the_secret_and_names_the_panel():
    secret = totp.generate_secret()
    uri = totp.provisioning_uri(secret, "mandy")

    assert uri.startswith("otpauth://totp/zsm:mandy?")
    assert f"secret={secret}" in uri
    assert "issuer=zsm" in uri


def test_the_uri_escapes_a_name_but_not_the_separator():
    """An account name is chosen by an admin, not validated as URI-safe, and a
    `/` in the label is a different path. The colon between issuer and account
    is the label's grammar, though — escaping *that* makes several apps show
    `zsm%3Amandy` as one long account name."""
    uri = totp.provisioning_uri(totp.generate_secret(), "a/b c")
    assert "otpauth://totp/zsm:a%2Fb%20c?" in uri


def test_the_uri_states_no_defaults():
    """Several apps ignore `algorithm`/`digits`/`period`, so stating a
    non-default produces codes that never verify — and stating a default is
    noise that invites someone to change it."""
    uri = totp.provisioning_uri(totp.generate_secret(), "mandy")
    assert "algorithm=" not in uri
    assert "digits=" not in uri
    assert "period=" not in uri


def test_a_grouped_secret_is_normalised_into_the_uri():
    """The URI is built from whatever the caller holds; a display-formatted
    secret in it would enroll a phone with spaces in its key."""
    secret = totp.generate_secret()
    uri = totp.provisioning_uri(totp.format_secret(secret), "mandy")
    assert f"secret={secret}" in uri
    assert "+" not in uri.split("secret=")[1].split("&")[0]
