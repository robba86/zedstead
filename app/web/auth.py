"""Password hashing, session store, and login rate limiting — stdlib only.

No passlib/bcrypt/itsdangerous: ``hashlib.scrypt`` + ``hmac.compare_digest``
cover password hashing, and sessions are opaque random tokens held in memory
(see ``SessionStore``) rather than signed cookies.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, NamedTuple

log = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "zsm_session"
# The name when the cookie is Secure (plans/68). A browser accepts a
# ``__Host-`` cookie only from a secure origin, with ``Path=/`` and no
# ``Domain``, so no other host and no plain-http response can set one in its
# place. `deps.session_cookie_name` picks between the two.
SECURE_SESSION_COOKIE_NAME = "__Host-" + SESSION_COOKIE_NAME
SESSION_TTL_SECONDS = 7 * 24 * 3600  # 7 days, sliding
# However much a session is used, it ends this long after its login
# (plans/68). Without it, a token that kept being sent never expired.
SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600

# Set to "1" by ``zsm web --reload`` (and inherited by uvicorn's reload
# worker) — the only thing that turns on session persistence below.
DEV_RELOAD_ENV = "ZSM_DEV_RELOAD"
DEV_SESSION_FILENAME = "dev-sessions.json"

# N=2^14, r=8, p=5 is OWASP's listed equal of N=2^17, r=8, p=1 (plans/68
# AUTH-14): about 0.13 s a hash, and 16 MiB, inside OpenSSL's default 32 MiB
# `maxmem`, where N=2^17 needs 128 MiB per concurrent login beside a DayZ
# server. A hash carries its own N, r and p, so one made with p=1 still
# verifies; it moves to p=5 when its password is next set.
_SCRYPT_N = 16384
_SCRYPT_R = 8
_SCRYPT_P = 5
_SCRYPT_DKLEN = 32

# The shortest password that may be *set* (plans/68). Login never checks it, so
# a shorter password chosen before this existed keeps signing in until its
# owner changes it. The login limiter only slows guesses made through the
# form; a hash that has left the box (a bundle carries config.json's) is
# slowed by scrypt and the password's length alone.
MIN_PASSWORD_LENGTH = 8


def check_new_password(password: str) -> None:
    """Raise ``ValueError``, phrased for a person, if *password* may not be set.

    The one check behind every place a password is chosen: ``UserStore.add``
    and ``set_password`` (the Users page, ``zsm users``, an account's own
    password change), the single-password change in ``routers/settings.py``,
    and ``zsm web``'s two prompts.
    """
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"A password needs at least {MIN_PASSWORD_LENGTH} characters."
        )


def hash_password(password: str) -> str:
    """Hash *password* as ``scrypt$N$r$p$<salt_hex>$<hash_hex>``."""
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    """Re-derive the hash for *password* and compare in constant time."""
    if not hashed:
        return False
    try:
        algo, n_s, r_s, p_s, salt_hex, hash_hex = hashed.split("$")
        if algo != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=int(n_s),
        r=int(r_s),
        p=int(p_s),
        dklen=len(expected),
    )
    return hmac.compare_digest(derived, expected)


def dev_session_path() -> Path | None:
    """Where dev-mode sessions are cached, or ``None`` outside dev reload.

    Returns a path **only** when ``ZSM_DEV_RELOAD=1`` — i.e. under
    ``zsm web --reload``. In every normal run this is ``None`` and
    ``SessionStore`` stays purely in-memory, so nothing about the shipped
    server's auth changes.
    """
    if os.environ.get(DEV_RELOAD_ENV) != "1":
        return None
    from app.shared.paths import CONFIG_DIR  # late: tests monkeypatch it

    return CONFIG_DIR / DEV_SESSION_FILENAME


class _Session(NamedTuple):
    username: str
    expiry: float
    # Opened through the pre-accounts password (`UserStore.authenticate`'s
    # fallback), not through an account. See `SessionStore.is_fallback`.
    fallback: bool = False
    # The latest `expiry` may ever reach: login + the maximum age. The sliding
    # refresh in `validate` stops here; left at 0 it ends the session at once.
    deadline: float = 0.0


class SessionStore:
    """In-memory session tokens with a sliding TTL, each tied to a username.

    The TTL slides on use but never past *max_age_seconds* after login
    (`SESSION_MAX_AGE_SECONDS`), so a session in constant use still ends.

    A process restart logs everyone out — acceptable for a single-admin tool.

    *persist_path* is the one exception, and it exists for ``zsm web
    --reload`` only (see ``dev_session_path``): every backend edit restarts
    the worker, and without it each save bounced the browser to /login.
    Tokens are then mirrored to that file and reloaded on startup. Writes
    happen on create/revoke, **not** on the sliding-TTL refresh in
    ``validate`` — that would mean a disk write per request to buy at most a
    7-day extension.

    **A token carries a name** since ``plans/43`` step 2. ``validate`` returns
    that name rather than a bool, so callers say ``if store.validate(t)`` the
    same way and gain an identity by asking for the return value. The name is
    *not* a permission: the role is looked up from ``users.json`` at request
    time, so demoting somebody takes effect on their next request instead of
    whenever their week-old cookie happens to expire.

    **Every read and write of the token table holds ``_lock``** (plans/68).
    ``require_session`` is a sync dependency, so ``validate`` runs in
    FastAPI's thread pool while login, logout and the revokes run on the event
    loop. Without the lock, the expiry sweep in ``validate`` iterating the
    table while a login added a token raised "dictionary changed size during iteration", and the request
    that happened to be validating answered 500. ``on_revoke`` is called
    after the lock is released, so a listener may call back into the store.
    """

    def __init__(
        self,
        ttl_seconds: int = SESSION_TTL_SECONDS,
        persist_path: Path | None = None,
        max_age_seconds: int = SESSION_MAX_AGE_SECONDS,
    ) -> None:
        self._sessions: dict[str, _Session] = {}
        # Reentrant only for safety; no method takes it twice today.
        self._lock = threading.RLock()
        self._ttl = ttl_seconds
        self._max_age = max_age_seconds
        self._persist_path = persist_path
        # Told which tokens stopped being valid, however that happened (logout,
        # a password change, expiry). `WebState` points it at the WebSocket hub:
        # a socket is authenticated once, at its handshake, so without this a
        # revoked session kept receiving every broadcast until its tab closed
        # (test pass 2, N27). May be called from a worker thread.
        self.on_revoke: Callable[[set[str]], None] | None = None
        if persist_path is not None:
            self._load()

    def create(self, username: str, *, fallback: bool = False) -> str:
        """Open a session for *username*.

        The name is **required** and must not be blank. Every caller has one —
        login authenticates before it gets here, and even the fallback path for
        a pre-accounts install yields ``admin``. Allowing a blank would create
        a session that is valid but falsy, which is the kind of value one
        caller tests with ``is None`` and the next with ``if not`` until they
        disagree about whether it is logged in.

        *fallback* marks a session opened with ``config.web_password_hash``
        rather than an account (see `is_fallback`).
        """
        if not username:
            raise ValueError("A session needs a username.")
        token = secrets.token_urlsafe(32)
        with self._lock:
            now = time.time()
            deadline = now + self._max_age
            self._sessions[token] = _Session(
                username, min(now + self._ttl, deadline), fallback, deadline
            )
            self._save()
        return token

    def validate(self, token: str | None) -> str | None:
        """The session's username, or ``None``; refreshes its TTL on success,
        up to the session's deadline and never past it.

        The returned name is never blank (see ``create``), so a truthiness test
        is safe — but prefer ``is None`` at a gate, because that is what says
        what is being asked.
        """
        if not token:
            return None
        with self._lock:
            expired = self._prune_locked()
            entry = self._sessions.get(token)
            if entry is not None:
                self._sessions[token] = entry._replace(
                    expiry=min(time.time() + self._ttl, entry.deadline)
                )
        self._notify(expired)
        return entry.username if entry is not None else None

    def is_fallback(self, token: str | None) -> bool:
        """Whether *token* was opened through the pre-accounts password.

        That is the only kind of session the role gate lets act as admin while
        there are no readable accounts (`deps._role_for`): the password behind
        it is the one `UserStore.authenticate` is falling back on right now. A
        session opened through an account — a viewer's, say — carries a name
        and nothing else, so with ``users.json`` unreadable there is nothing to
        say what it may do.
        """
        if not token:
            return False
        with self._lock:
            entry = self._sessions.get(token)
        return entry is not None and entry.fallback

    def revoke(self, token: str | None) -> None:
        if token:
            with self._lock:
                dropped = self._sessions.pop(token, None)
                self._save()
            if dropped is not None:
                self._notify({token})

    def revoke_all_for(self, username: str, keep_token: str | None = None) -> None:
        """Drop *username*'s sessions, keeping *keep_token* if it is theirs.

        The scoped half of ``revoke_all_except``, and the one a password change
        wants once there is more than one account: signing everybody out
        because a moderator rotated their own password is somebody else's
        afternoon. ``revoke_all_except`` stays for the case where it is right —
        a pre-accounts install, where one password is what every open session
        authenticated with.

        Matched case-insensitively, the way ``UserStore.get`` matches: two
        sessions for one account can hold different spellings of its name.
        A blank *username* drops nothing, rather than everything.
        """
        wanted = (username or "").strip().lower()
        if not wanted:
            return
        with self._lock:
            doomed = [
                token
                for token, entry in self._sessions.items()
                if entry.username.strip().lower() == wanted and token != keep_token
            ]
            for token in doomed:
                del self._sessions[token]
            self._save()
        self._notify(set(doomed))

    def revoke_all_except(self, keep_token: str | None) -> None:
        """Drop every session but *keep_token*.

        Whole-panel, so it is right only when one credential stands behind
        every session — the install that never grew accounts. With accounts,
        use ``revoke_all_for``.
        """
        with self._lock:
            dropped = set(self._sessions) - {keep_token}
            if keep_token is not None and keep_token in self._sessions:
                self._sessions = {keep_token: self._sessions[keep_token]}
            else:
                self._sessions = {}
            self._save()
        self._notify(dropped)

    def _prune_locked(self) -> set[str]:
        """Drop expired tokens and return them. The caller holds ``_lock``
        and notifies after releasing it."""
        now = time.time()
        expired = [t for t, entry in self._sessions.items() if entry.expiry < now]
        for t in expired:
            del self._sessions[t]
        return set(expired)

    def _notify(self, tokens: set[str]) -> None:
        if not tokens or self.on_revoke is None:
            return
        try:
            self.on_revoke(tokens)
        except Exception:  # a listener must never break a logout
            log.warning("Session revoke listener failed", exc_info=True)

    def _load(self) -> None:
        """Read persisted tokens, dropping expired ones.

        Any unreadable/corrupt file is treated as "no sessions" — the cost is
        one extra login, never a startup failure. That covers each format
        change for free: a file written by an older build (bare floats before
        ``plans/43`` step 2, ``[name, expiry]`` before the fallback flag,
        three fields before the deadline) does not match the shape below, so
        the developer logs in once more and the file is rewritten.
        """
        try:
            raw = json.loads(self._persist_path.read_text(encoding="utf-8"))
            now = time.time()
            self._sessions = {
                str(t): _Session(
                    str(entry[0]),
                    min(float(entry[1]), float(entry[3])),
                    entry[2] is True,
                    float(entry[3]),
                )
                for t, entry in raw.items()
                if isinstance(entry, (list, tuple))
                and len(entry) == 4
                and str(entry[0])
                and isinstance(entry[1], (int, float))
                and isinstance(entry[3], (int, float))
                and min(float(entry[1]), float(entry[3])) > now
            }
        except (OSError, ValueError, TypeError, AttributeError, IndexError):
            self._sessions = {}

    def _save(self) -> None:
        if self._persist_path is None:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {t: list(entry) for t, entry in self._sessions.items()}
            self._persist_path.write_text(json.dumps(payload), encoding="utf-8")
            self._persist_path.chmod(0o600)
        except OSError:
            pass  # dev convenience only — never break a login over it


class LoginRateLimiter:
    """Blocks login attempts after too many recent failures.

    Two counters (``plans/43`` §3.6), and **which failure goes to which one**
    is the whole design:

    * A failure against a **real account** goes to that account's bucket and
      **nowhere else**. More than *max_user_failures* of them inside
      *window_seconds* refuses that name, and only that name.
    * A failure that names **nobody** — a blank name, or one that matches no
      account — goes to the global window. More than *max_failures* of those
      refuses everybody. This is the original counter, with its original
      threshold, and it is the flood ceiling.

    The split is what fixes the denial of service the global-only version
    handed out for free: someone guessing at ``admin`` used to lock out every
    other account too. It also stops sooner than before — six attempts on one
    account rather than eleven on the panel.

    **The ceiling must not be tighter than the per-account limit**, which is
    why known-account failures are kept out of it rather than counted in both.
    A global counter that trips first would mean a run against one account
    still locked out everybody, and the per-account limit would never be the
    thing that fired — the bug would survive the fix that was supposed to
    remove it.

    A success clears the global window and **only the succeeding account's**
    bucket. That asymmetry is deliberate: someone holding a legitimate viewer
    account must not be able to wipe the counter holding off a guessing run
    against ``admin``.

    Until the login form grows a username field (``plans/43`` step 5) every
    attempt arrives blank, so this behaves exactly as it always did.

    In-memory and per process, like ``SessionStore``: a restart forgives
    everyone. *max_tracked_users* bounds the dictionary so a caller rotating
    names cannot grow it without limit — though the caller only ever passes a
    name it has already looked up, so reaching that bound means the install has
    more accounts than the bound.

    **An attempt counts from the moment it is let through, not from when it
    fails** (`begin`, `LoginAttempt`). A failure is only known after the password hash,
    and checking `is_limited` and recording the failure on either side of that
    wait let every attempt that arrived inside it through: a burst of 40 at
    once got 40 password checks against a limit of six. Attempts still being
    checked are counted beside the failures, so a burst gets exactly what the
    same attempts made one after another would have got.
    """

    def __init__(
        self,
        max_failures: int = 10,
        window_seconds: float = 300.0,
        max_user_failures: int = 5,
        max_tracked_users: int = 64,
    ) -> None:
        self._failures: deque[float] = deque()
        self._per_user: dict[str, deque[float]] = {}
        # Attempts let through by `begin` and not yet ended, per bucket key
        # ("" is the global window). Bounded by the requests in flight.
        self._in_flight: dict[str, int] = {}
        self._max_failures = max_failures
        self._max_user_failures = max_user_failures
        self._max_tracked_users = max_tracked_users
        self._window_seconds = window_seconds
        # The login route records from the event loop and from the thread its
        # second-factor check runs in.
        self._lock = threading.RLock()

    @staticmethod
    def _key(username: str) -> str:
        """Accounts are matched case-insensitively, so the bucket is too.

        Otherwise ``Admin`` and ``admin`` are two buckets for one account, and
        alternating between them doubles the allowance.
        """
        return (username or "").strip().lower()[:64]

    def record_failure(self, username: str = "") -> None:
        """Record a failed attempt.

        *username* is the name of an account that **exists**, or ``""`` when
        the attempt named nobody. The caller has already looked it up — see
        ``routers/auth.py::_attempted_identity`` — and passing an unverified
        string here would let anyone fill the bucket of an account they only
        guessed at, which is the lockout this class exists to stop.
        """
        with self._lock:
            now = time.time()
            key = self._key(username)
            if not key:
                self._failures.append(now)
            else:
                if key not in self._per_user and len(self._per_user) >= self._max_tracked_users:
                    self._evict_oldest()
                self._per_user.setdefault(key, deque()).append(now)
            self._prune()

    def record_success(self, username: str = "") -> None:
        with self._lock:
            self._failures.clear()
            key = self._key(username)
            if key:
                self._per_user.pop(key, None)

    def begin(self, username: str = "") -> LoginAttempt | None:
        """Let one attempt through, or refuse it (``None``).

        The check and the reservation are one step, so attempts arriving
        together cannot all pass the check before any of them has failed. The
        returned `LoginAttempt` holds a place until `LoginAttempt.fail` or
        `LoginAttempt.close`; close it in a ``finally``, or a cancelled request
        would hold its place for the life of the process.
        """
        with self._lock:
            if self.is_limited(username):
                return None
            key = self._key(username)
            self._in_flight[key] = self._in_flight.get(key, 0) + 1
            return LoginAttempt(self, username)

    def _release(self, username: str) -> None:
        with self._lock:
            key = self._key(username)
            left = self._in_flight.get(key, 0) - 1
            if left > 0:
                self._in_flight[key] = left
            else:
                self._in_flight.pop(key, None)

    def is_limited(self, username: str = "") -> bool:
        """Whether this attempt should be refused before it is even checked.

        *username* is a **verified** account name or ``""``, exactly as
        ``record_failure`` takes it. Handing the raw submitted string here
        instead would hand out an empty bucket per made-up name and make the
        global ceiling unreachable — the limiter would stop limiting.

        Measured against **one** counter, the same one ``record_failure`` would
        have used: the account's own, or the global window when the name is
        blank or unknown. A known account is deliberately *not* also held by
        the global ceiling — if it were, spraying made-up names would lock the
        real admin out, which is the denial of service being removed here,
        re-entered through the other door. Such a run is still bounded: the
        moment it guesses a real name the failures land in that account's
        bucket instead, and stop at *max_user_failures*.

        Attempts `begin` let through and still open count as well, since any
        of them may yet be a failure.
        """
        with self._lock:
            self._prune()
            key = self._key(username)
            pending = self._in_flight.get(key, 0)
            if not key:
                return len(self._failures) + pending > self._max_failures
            bucket = self._per_user.get(key)
            return len(bucket or ()) + pending > self._max_user_failures

    def _evict_oldest(self) -> None:
        """Drop the bucket whose newest failure is the oldest.

        Reached only when every tracked name is still inside the window, so
        pruning has nothing to free. Evicting forgives that name early, which
        is the right way for a bound on *memory* to fail.
        """
        if not self._per_user:
            return
        stalest = min(self._per_user, key=lambda k: self._per_user[k][-1])
        del self._per_user[stalest]

    def _prune(self) -> None:
        cutoff = time.time() - self._window_seconds
        while self._failures and self._failures[0] < cutoff:
            self._failures.popleft()
        for key in list(self._per_user):
            bucket = self._per_user[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if not bucket:
                del self._per_user[key]


class LoginAttempt:
    """One attempt `LoginRateLimiter.begin` let through, holding its place.

    `fail` turns the place into a recorded failure in one step, so the attempt
    is never counted twice (in flight *and* failed) nor, for an instant, not at
    all. `close` gives the place back; it is safe to call after `fail` and
    twice, which is what lets the caller put it in a ``finally``.
    """

    def __init__(self, limiter: LoginRateLimiter, username: str) -> None:
        self._limiter = limiter
        self.username = username
        self._open = True

    def fail(self) -> None:
        """Record this attempt as a failure and release its place."""
        with self._limiter._lock:
            self._limiter.record_failure(self.username)
            self.close()

    def close(self) -> None:
        with self._limiter._lock:
            if self._open:
                self._open = False
                self._limiter._release(self.username)
