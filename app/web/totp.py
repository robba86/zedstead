"""Time-based one-time passwords — RFC 6238 over RFC 4226, stdlib only (TODO B5).

Forty lines of `hmac` instead of `pyotp`, for the same reason `auth.py` derives
its own scrypt hashes instead of importing passlib: this is the whole of the
algorithm, it has published test vectors, and a dependency in the login path is
a dependency that has to be trusted on every install forever. The vectors from
RFC 6238 Appendix B are in `tests/test_totp.py`, which is the thing that makes
writing it ourselves defensible rather than merely cheaper.

**This module knows nothing about users.** It turns a secret and a moment into
six digits and back. Who has a secret, whether they are required to prove it,
and what a used code costs are all decided in `app/web/users.py` and
`app/web/routers/auth.py` — see `Replay` at the bottom for the one piece of
state that could not live there.

Three details that are easy to get subtly wrong, so they are stated:

* **The window is a *count of steps*, not seconds.** `WINDOW = 1` accepts the
  step before and the step after the current one, i.e. a 90-second span. That
  covers a phone whose clock is a little off and the seconds it takes to read
  six digits off it and type them. Widening it is not free: every extra step
  is another code an attacker may guess at.
* **A code is six digits *as a string*, left-padded.** `007123` is a valid
  code and `int()` would make it `7123`, which matches nothing. Every value
  here stays a string from generation to comparison.
* **SHA-1, and deliberately.** RFC 6238 allows SHA-256/512 and every
  authenticator app ignores the `algorithm=` parameter in the provisioning
  URI, so a secret enrolled as SHA-256 simply never verifies. The weakness
  that retired SHA-1 elsewhere is collision resistance, which an HMAC does not
  rest on.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import struct
import threading
import time
import urllib.parse

__all__ = [
    "DIGITS",
    "PERIOD",
    "WINDOW",
    "PendingSecrets",
    "Replay",
    "code_at",
    "counter_at",
    "format_secret",
    "generate_secret",
    "normalise_secret",
    "provisioning_uri",
    "verify",
]

DIGITS = 6
PERIOD = 30  # seconds per step
WINDOW = 1  # steps accepted either side of now — see the module docstring

# 160 bits, the shared-secret length RFC 4226 §4 R6 recommends, which is also
# what every authenticator app expects from a `otpauth://` URI.
SECRET_BYTES = 20

# What a base32 secret may contain once normalised. Not a cosmetic check: a
# secret that cannot be decoded must be refused when it is *stored*, or the
# account it belongs to can never sign in again.
_ALPHABET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")


def generate_secret() -> str:
    """A fresh base32 secret, unpadded.

    Unpadded because that is the form every authenticator's manual-entry box
    takes and the form that goes into an `otpauth://` URI; `_decode` puts the
    `=` back. `secrets`, not `random` — this is a credential.
    """
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def normalise_secret(secret: str) -> str:
    """Upper-case, stripped of the spaces and hyphens people type.

    Setup keys are shown in groups of four so they can be read aloud and typed
    without losing one's place (`format_secret`), and a secret pasted back from
    such a display carries the spaces with it.
    """
    return "".join((secret or "").split()).replace("-", "").upper()


def format_secret(secret: str) -> str:
    """The secret in groups of four, for a human to copy.

    Purely presentational — `normalise_secret` undoes it. It exists because the
    alternative to a QR code here is somebody typing 32 characters off a
    screen, and an unbroken run of 32 is where that goes wrong.
    """
    cleaned = normalise_secret(secret)
    return " ".join(cleaned[i : i + 4] for i in range(0, len(cleaned), 4))


def _decode(secret: str) -> bytes:
    """The secret's bytes. Raises ``ValueError`` on anything unusable.

    Raising rather than returning empty: a secret that decodes to nothing
    would make `verify` reject every code, which reads to the person holding
    the phone exactly like a wrong clock.
    """
    cleaned = normalise_secret(secret)
    if not cleaned or not set(cleaned) <= _ALPHABET:
        raise ValueError("A TOTP secret is base32 — A-Z and 2-7 only.")
    padding = "=" * (-len(cleaned) % 8)
    try:
        return base64.b32decode(cleaned + padding)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("That is not a usable TOTP secret.") from exc


def counter_at(timestamp: float | None = None) -> int:
    """Which 30-second step *timestamp* (default: now) falls in."""
    return int(timestamp if timestamp is not None else time.time()) // PERIOD


def code_at(secret: str, counter: int, digits: int = DIGITS) -> str:
    """The code for a given step — RFC 4226 §5.3's dynamic truncation.

    A negative *counter* is refused rather than left to `struct`, which raises
    its own error from three frames down. There is no step before zero, and
    `verify` can be asked for one: a box whose clock has not been set yet sits
    at the epoch, where the window reaches back past it.
    """
    if counter < 0:
        raise ValueError("There is no TOTP step before the epoch.")
    digest = hmac.new(_decode(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(truncated % (10**digits)).zfill(digits)


def now_code(secret: str, timestamp: float | None = None) -> str:
    """The code a phone would be showing. For enrollment checks and tests."""
    return code_at(secret, counter_at(timestamp))


def verify(
    secret: str,
    code: str,
    timestamp: float | None = None,
    window: int = WINDOW,
    after: int | None = None,
) -> int | None:
    """The step *code* belongs to, or ``None``.

    Returns the **counter** rather than a bool so the caller can record it and
    pass it back as *after* next time; that is how a code is stopped from being
    replayed inside its own 90-second window, which RFC 6238 §5.2 requires and
    which nothing in the algorithm itself provides.

    *after* refuses any step at or below a value already used. It is compared
    against the matched step, not against "now", so a code from the step
    *before* an accepted one is refused too — otherwise someone who watched a
    login could still spend the older of the two codes in hand.

    Every candidate step is compared, with `hmac.compare_digest`, and the loop
    does not break early. The digits are not secret, so this is not the timing
    defence it looks like; it is there so that *which* step matched cannot be
    read off the clock, and so the cost of a call does not depend on the input.
    """
    typed = (code or "").strip().replace(" ", "")
    if not typed.isdigit() or len(typed) != DIGITS:
        return None
    try:
        _decode(secret)
    except ValueError:
        return None

    centre = counter_at(timestamp)
    matched: int | None = None
    # Clamped at zero: on a box whose clock has not been set the window reaches
    # back past the epoch, and `code_at` refuses a negative step. Found by the
    # RFC-vector tests, which verify at `timestamp=1`.
    for step in range(max(0, centre - window), centre + window + 1):
        if hmac.compare_digest(code_at(secret, step), typed) and matched is None:
            matched = step
    if matched is None:
        return None
    if after is not None and matched <= after:
        return None
    return matched


def provisioning_uri(secret: str, account: str, issuer: str = "zsm") -> str:
    """The `otpauth://` URI an authenticator app enrolls from.

    The issuer appears **twice** — once as a label prefix and once as a
    parameter — because that is what the de-facto spec (Google's
    `key-uri-format`) says, and apps disagree about which one they read. The
    label is what the account is called in the app's list, so it carries the
    panel's name as well as the username: somebody administering two zsm
    panels needs to tell the two entries apart.

    `algorithm`, `digits` and `period` are left at their defaults and omitted
    deliberately: several apps ignore them, so stating a non-default would
    produce codes that never verify, and stating a default is noise.
    """
    # Each half is escaped, the separator is not. The colon *is* the label's
    # grammar — escaping it whole yields `zsm%3Amandy`, which several apps
    # then show as one long account name with no issuer.
    label = f"{urllib.parse.quote(issuer, safe='')}:{urllib.parse.quote(account, safe='')}"
    params = urllib.parse.urlencode(
        {"secret": normalise_secret(secret), "issuer": issuer}
    )
    return f"otpauth://totp/{label}?{params}"


class Replay:
    """The last step each account spent, so a code cannot be used twice.

    RFC 6238 §5.2 is explicit that this is the verifier's job: the algorithm
    itself will happily accept the same six digits for the whole 90 seconds
    they are valid, which is long enough to be read over a shoulder, off a
    shared screen, or out of a proxy's log and used before the person who typed
    it has finished loading the dashboard.

    **In memory, like `SessionStore`, and for the same reason** — a restart
    already ends every session, so a restart forgetting which codes were spent
    costs nothing that was not already lost. Writing it to `users.json` would
    mean a disk write per login and a file whose mtime is a login timestamp.

    Bounded the way `LoginRateLimiter` bounds its per-account buckets: this is
    keyed by a name an unauthenticated caller supplies part of, so an unbounded
    dict is a memory leak with a request behind it. Only accounts that really
    exist ever reach here (login resolves the name first), but the bound is
    cheap and does not depend on that staying true.

    **`claim` is the check and the record in one step, under a lock** (plans/68
    AUTH-15). Logins verify their code in the thread pool, so two sign-ins with
    the same code could both read `last` before either recorded it, and both
    got in. Whoever claims the step first wins; the other is refused.
    """

    def __init__(self, max_tracked: int = 64) -> None:
        self._spent: dict[str, int] = {}
        self._max_tracked = max_tracked
        self._lock = threading.Lock()

    @staticmethod
    def _key(username: str) -> str:
        return (username or "").strip().lower()[:64]

    def last(self, username: str) -> int | None:
        """The last step this account spent, or ``None``."""
        with self._lock:
            return self._spent.get(self._key(username))

    def claim(self, username: str, counter: int) -> bool:
        """Record *counter* as used, unless it, or a later step, already was.

        ``False`` means the code was spent by somebody else since it was
        verified, and the sign-in must be refused. Never moves backwards: two
        tabs landing in the same step would otherwise let the second overwrite
        the first with an equal value, and a later out-of-order call with an
        older step would re-open a code that had been spent.
        """
        key = self._key(username)
        if not key:
            return True
        with self._lock:
            if counter <= self._spent.get(key, -1):
                return False
            if key not in self._spent and len(self._spent) >= self._max_tracked:
                self._evict_oldest()
            self._spent[key] = counter
            return True

    def spend(self, username: str, counter: int) -> None:
        """Record *counter* as used (`claim`, when the answer does not matter)."""
        self.claim(username, counter)

    def forget(self, username: str) -> None:
        """Drop an account's record — used when its secret changes.

        A new secret makes the old step number meaningless, and keeping it
        would refuse the first code from the new one whenever the clock
        happened to be inside the same step.
        """
        with self._lock:
            self._spent.pop(self._key(username), None)

    def _evict_oldest(self) -> None:
        """Drop the account whose last accepted code is furthest in the past.
        Called with the lock held."""
        if not self._spent:
            return
        stalest = min(self._spent, key=lambda k: self._spent[k])
        del self._spent[stalest]


class PendingSecrets:
    """Secrets handed out for enrollment but not yet confirmed (TODO B5).

    Enrollment is two requests: one that generates a secret and shows it, and
    one that proves a phone took it. The secret has to survive between them,
    and the alternative — letting the browser send it back — would mean an
    account's second factor could be a secret chosen by whoever made the
    request, rather than one this process generated.

    **Nothing is written to `users.json` until a code confirms it.** A secret
    stored on the first request would leave a half-enrolled account behind
    every abandoned setup page: 2FA switched on, against a phone that never
    scanned it, and the way back in needs a code.

    In memory with a TTL, like everything else in this module. A restart
    forgets a setup in progress, which costs one more click on a page the user
    is already looking at.
    """

    def __init__(self, ttl_seconds: float = 600.0, max_pending: int = 32) -> None:
        self._pending: dict[str, tuple[str, float]] = {}
        self._ttl = ttl_seconds
        self._max_pending = max_pending

    @staticmethod
    def _key(username: str) -> str:
        return (username or "").strip().lower()[:64]

    def start(self, username: str) -> str:
        """Generate and hold a secret for *username*, replacing any earlier one.

        Replacing matters: someone who reloads the setup page gets a new
        secret, and confirming against the *old* one would enroll a key their
        phone is no longer showing.
        """
        key = self._key(username)
        if not key:
            raise ValueError("Enrollment needs a named account.")
        self._prune()
        if key not in self._pending and len(self._pending) >= self._max_pending:
            self._evict_oldest()
        secret = generate_secret()
        self._pending[key] = (secret, time.time() + self._ttl)
        return secret

    def get(self, username: str) -> str | None:
        """The held secret, or ``None`` if there is none or it has expired."""
        self._prune()
        entry = self._pending.get(self._key(username))
        return entry[0] if entry else None

    def clear(self, username: str) -> None:
        self._pending.pop(self._key(username), None)

    def _prune(self) -> None:
        now = time.time()
        for key in [k for k, (_s, expiry) in self._pending.items() if expiry < now]:
            del self._pending[key]

    def _evict_oldest(self) -> None:
        if not self._pending:
            return
        stalest = min(self._pending, key=lambda k: self._pending[k][1])
        del self._pending[stalest]
