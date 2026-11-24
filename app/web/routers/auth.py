"""Auth endpoints: login, logout, session check.

Not router-wide ``require_session``-gated (unlike every other router) since
``/login`` must be reachable while unauthenticated; ``require_session`` is
applied per-route instead on logout/me.
"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app.server.instances import instance_summary
from app.shared.steam import server_branch
from app.web import access_log, totp
from app.web.install_id import install_id
from app.web.auth import SESSION_MAX_AGE_SECONDS, LoginAttempt
from app.web.deps import (
    confirm_password,
    cookie_is_secure,
    current_user,
    get_panel,
    get_panel_state,
    get_state,
    require_session,
    session_cookie_name,
    session_token,
)
from app.web.panel import Panel
from app.web.schemas import (
    LoginRequest,
    TotpDisableRequest,
    TotpEnableRequest,
    TotpStartRequest,
)
from app.web.state import WebState

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _attempted_identity(state: WebState, attempted: str) -> tuple[str, bool]:
    """Turn a submitted name into a **verified** one, or nothing.

    Returns ``(user, unknown_user)`` — the stored spelling of an account that
    exists, or ``("", True)`` for a name nobody has. Two things downstream
    need exactly this and neither may be handed the raw string:

    * **The access log.** The username box is next to the password box on the
      same form, so an unrecognised name may be a password typed into the
      wrong field, and `GET /api/security/access-log` hands that file to a
      browser. The attempt is still recorded — as one on a name nobody has.
    * **The rate limiter.** Its per-account buckets are keyed by this, so an
      unverified string would let anyone fill the bucket of an account they
      merely guessed at, and a made-up name would get a bucket of its own
      instead of counting against the global ceiling.

    The stored spelling, not the submitted one, so the log reads as one
    account rather than as ``Admin``/``admin``/``ADMIN``.

    **A blank name is the only account's name when there is exactly one**
    (plans/68 AUTH-17): `UserStore.authenticate` signs a blank name in as that
    account, so a guess made that way is a guess at it and counts in its
    bucket. Blank otherwise (no accounts, or several) is ``("", False)``.
    """
    try:
        if not attempted:
            users = state.users.load()
            return (users[0].name, False) if len(users) == 1 else ("", False)
        found = state.users.get(attempted)
    except Exception:  # noqa: BLE001 - a log column never costs a login
        return "", False
    return (found.name, False) if found else ("", True)


def _check_code(
    state: WebState, user, submitted: str, ip: str, agent: str, attempt: LoginAttempt
):
    """``None`` when the code is good, otherwise the response to send back.

    Two refusals, and they are not the same thing:

    * **No code at all** — the form has not been told to ask yet. That is the
      first half of a two-step sign-in, not a wrong guess, so it costs the
      caller nothing and is not written down as a failure. It answers 401 with
      ``totp_required`` so the form knows to show the box and resubmit; the
      password comes with it a second time, which is what keeps this stateless
      — there is no half-finished login sitting in memory waiting to be
      claimed.
    * **A wrong code** — a real failure, and the most interesting one this log
      can hold: somebody had the password. It gets its own event
      (`login_totp_failed`) rather than sharing `login_failed`, because the two
      say opposite things about how much trouble the panel is in.

    The failure is recorded through *attempt*, so against *who* — the key the
    limiter was checked with at the top of `login` — and not against
    ``user.name``. They are the same string whenever the attempt names an
    account, typed or (with one account) left blank. They differ when no
    account could be read and the single password signed in as
    ``DEFAULT_ADMIN``: there that name's bucket is the one nothing reads, so
    recording into it would be a limiter that counts and never stops anybody.
    """
    code = (submitted or "").strip()
    if not code:
        return JSONResponse(
            status_code=401,
            content={
                "detail": "Enter the 6-digit code from your authenticator app.",
                "totp_required": True,
            },
        )

    matched = totp.verify(
        user.totp_secret, code, after=state.totp_replay.last(user.name)
    )
    # Spent here, so the same six digits cannot be used again inside the 90
    # seconds they stay valid — RFC 6238 §5.2, and see `totp.Replay`. `claim`
    # checks and records in one step: two sign-ins verifying the same code at
    # once both passed `last` above, and only one of them may get in.
    if matched is None or not state.totp_replay.claim(user.name, matched):
        attempt.fail()
        access_log.record("login_totp_failed", ip, agent, user=user.name)
        time.sleep(0.5)
        return JSONResponse(
            status_code=401,
            content={
                # Says nothing about *why* it failed. A code that is merely
                # stale and one that was never right are the same answer here,
                # or "that one was already used" would confirm a password to
                # somebody replaying what they shoulder-surfed.
                "detail": "That code is not right. Check your authenticator app and try again.",
                "totp_required": True,
            },
        )
    return None


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    state: WebState = Depends(get_panel_state),
    panel: Panel = Depends(get_panel),
) -> dict:
    ip = access_log.client_ip(request)
    agent = request.headers.get("user-agent", "")
    # Resolved before the limiter, because the limiter is keyed by it. Both
    # counters and the log line all read the same verified value.
    who, unknown = await asyncio.to_thread(
        _attempted_identity, state, (body.username or "").strip()
    )

    # `begin` checks the limit and reserves this attempt in one step. A check
    # here and a failure recorded after the slow hash below let every attempt
    # of a simultaneous burst through (see `LoginRateLimiter`).
    attempt = state.login_limiter.begin(who)
    if attempt is None:
        # Recorded too: a burst that trips the limiter is the single most
        # interesting thing this log can show, and dropping those entries
        # would hide exactly the attempt worth seeing.
        await asyncio.to_thread(
            access_log.record, "login_failed", ip, agent, user=who, unknown_user=unknown
        )
        raise HTTPException(
            status_code=429, detail="Too many failed login attempts. Try again later."
        )
    try:
        # scrypt is deliberately slow, so this goes off the event loop — the
        # old inline `verify_password` blocked every other request for its
        # duration.
        user = await asyncio.to_thread(
            state.users.authenticate,
            body.username,
            body.password,
            state.config.web_password_hash,
        )
        if user is None:
            attempt.fail()
            await asyncio.to_thread(
                access_log.record, "login_failed", ip, agent, user=who, unknown_user=unknown
            )
            await asyncio.sleep(0.5)
            # One message for a bad name and a bad password, on purpose: two
            # would say which names exist. The access log draws that
            # distinction because only an admin reads it; the response must
            # not.
            raise HTTPException(status_code=401, detail="Invalid password")

        # The password was right. A second factor, if this account enrolled one.
        if user.totp_enabled:
            refused = await asyncio.to_thread(
                _check_code, state, user, body.code, ip, agent, attempt
            )
            if refused is not None:
                return refused

        state.login_limiter.record_success(user.name)
    finally:
        attempt.close()
    await asyncio.to_thread(access_log.record, "login", ip, agent, user=user.name)
    token = state.sessions.create(user.name, fallback=user.fallback)
    # The session this browser came with ends here (plans/68 AUTH-10): the
    # cookie below replaces it, so nothing should still answer to the old one
    # (signing in as somebody else in the same browser left it valid).
    state.sessions.revoke(session_token(request))
    # Path=/ and no Domain: the ``__Host-`` name the cookie has when it is
    # Secure is refused by a browser without both.
    #
    # Max-Age is set once, here, so it is the session's hard limit (30 days),
    # not its 7-day idle TTL (plans/68-review-findings B3). The TTL slides on
    # the server with every request, which the cookie never hears about: at
    # 7 days the browser dropped a session still in daily use. The server
    # still ends a session idle for 7 days.
    response.set_cookie(
        session_cookie_name(request, state),
        token,
        httponly=True,
        samesite="lax",
        secure=cookie_is_secure(request, state),
        path="/",
        max_age=SESSION_MAX_AGE_SECONDS,
    )
    # Several instances: the form asks which one to open (plans/62 D10). The
    # session is the panel's either way; this only says where to land.
    return {"ok": True, **_switchable(panel)}


def _switchable(panel: Panel) -> dict:
    """The instances a page can switch to and the one unprefixed URLs serve,
    or nothing when there is only one."""
    served = panel.served()
    return {"instances": served, "startup": panel.startup_id} if served else {}


@router.post("/logout", dependencies=[Depends(require_session)])
async def logout(
    request: Request, response: Response, state: WebState = Depends(get_panel_state)
) -> dict:
    state.sessions.revoke(session_token(request))
    await asyncio.to_thread(
        access_log.record,
        "logout",
        access_log.client_ip(request),
        request.headers.get("user-agent", ""),
        # `require_session` put it there, so the session is what names this —
        # no second lookup, and no way for the body to claim someone else.
        user=getattr(request.state, "username", ""),
    )
    # Mirror the attributes the cookie was set with — a delete_cookie whose
    # attributes differ from the original can leave it in place in some
    # browsers.
    response.delete_cookie(
        session_cookie_name(request, state),
        path="/",
        httponly=True,
        samesite="lax",
        secure=cookie_is_secure(request, state),
    )
    return {"ok": True}


@router.get("/me", dependencies=[Depends(require_session)])
async def me(user=Depends(current_user), state: WebState = Depends(get_state),
             panel: Panel = Depends(get_panel)) -> dict:
    """Who is logged in, and which instance this panel serves.

    ``user`` is ``None`` for a session that predates accounts — that is still a
    valid session, so this reports ``authenticated: true`` with the admin role
    the panel has always granted. The frontend reads `role` from here in
    ``plans/43`` step 5; nothing enforces it yet.

    ``instance`` names the server in the sidebar and the tab title, so two
    panels in two tabs can be told apart (plans/62 step 1, D2: always shown).
    """
    return {
        "authenticated": True,
        "username": user.name if user else "",
        "role": user.role if user else "admin",
        # Whether *this* caller has a second factor, never whether anyone does.
        # A pre-accounts session has no row to enroll against, which is why it
        # reports False rather than guessing.
        "totp": bool(user and user.totp_enabled),
        # `branch` badges an Experimental server beside its name (plans/66).
        "instance": {**instance_summary(state.config.instance_id), "branch": server_branch(state.config)},
        # The sidebar's switcher (plans/62 step 7), only with two or more.
        **_switchable(panel),
        # What the browser keys its cached jobs on (app/web/install_id.py).
        "install": await asyncio.to_thread(install_id),
    }


# ── your own second factor (TODO B5) ────────────────────────────────────
#
# Self-service only: these three act on the caller's own account and there is
# no route here that touches anybody else's. An admin who needs to un-enroll a
# colleague whose phone is in a river uses `zsm users totp disable`, which is
# the same recovery path `plans/43` §5 keeps the CLI free of roles for — shell
# access to the box is already full control, and a web route that could strip
# another admin's second factor would make every admin session as strong as
# the weakest admin's password.
#
# They are `viewer` in `permissions.SESSION_WRITES`, not admin: a second factor
# is not a privilege and a moderator must be able to protect their own account.


def _own_account(request: Request, state: WebState):
    """The caller's row, or a 409 explaining why there isn't one.

    A session from the pre-accounts fallback has no row to enroll against —
    `authenticate` synthesised its `User` out of ``config.web_password_hash``.
    That is a real state on an install that has never created an account, and
    "create one first" is the answer rather than a 500 three frames down.
    """
    user = state.users.get(getattr(request.state, "username", ""))
    if user is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "This app still signs in with a single shared password, which "
                "has no account to attach a second factor to. Create one with "
                "the Accounts card, or `zsm users add <name> --role admin`."
            ),
        )
    return user


async def _confirm_password(state: WebState, user, password: str, code: str = "") -> None:
    """Refuse unless *password* is the caller's own. 400 if not, 429 after too
    many wrong ones (`deps.confirm_password`, which counts them in the login
    limiter like a sign-in as this account).

    An account with a second factor also needs a current *code* from it, or a
    stolen session plus the password could turn it off or swap in another
    phone. A wrong code counts in the same limiter. The code is spent like a
    login's (RFC 6238 §5.2, `_check_code`).

    Not 401: the caller *is* signed in, and the web UI treats every 401 as
    "your session ended" and sends the page to /login, so a mistyped password
    lost the page instead of showing this sentence. 401 stays for "not
    signed in" (`deps.require_session`)."""
    def code_refusal() -> str | None:
        if not user.totp_enabled:
            return None
        if not (code or "").strip():
            return "Enter the 6-digit code from your authenticator app."
        matched = totp.verify(
            user.totp_secret, code, after=state.totp_replay.last(user.name)
        )
        if matched is None or not state.totp_replay.claim(user.name, matched):
            return "That code is not right. Check your authenticator app and try again."
        return None

    await confirm_password(
        state,
        user.name,
        password,
        user.password_hash,
        "That is not your current password.",
        then=code_refusal,
    )


@router.post("/totp/start", dependencies=[Depends(require_session)])
async def totp_start(
    body: TotpStartRequest, request: Request, state: WebState = Depends(get_panel_state)
) -> dict:
    """Generate a secret to enroll, and show it. Nothing is saved yet.

    The password is required because enrolling is an attack in the *other*
    direction from disabling: somebody holding a stolen session who enrolls
    their own phone has locked the account's real owner out, and the owner
    cannot undo it from inside the panel — the way back in needs a code.

    No QR code. Rendering one needs a dependency on one side of the wire or
    the other, and every authenticator app accepts a typed setup key; the
    secret is returned grouped in fours (`totp.format_secret`) because the
    alternative is somebody reading 32 unbroken characters off a screen.
    """
    user = _own_account(request, state)
    await _confirm_password(state, user, body.password, body.code)

    secret = state.totp_pending.start(user.name)
    return {
        "secret": secret,
        "formatted": totp.format_secret(secret),
        "uri": totp.provisioning_uri(secret, user.name),
        "digits": totp.DIGITS,
        "period": totp.PERIOD,
    }


@router.post("/totp/enable", dependencies=[Depends(require_session)])
async def totp_enable(
    body: TotpEnableRequest, request: Request, state: WebState = Depends(get_panel_state)
) -> dict:
    """Confirm the pending secret with a code from the phone that took it.

    **The code is what makes this safe to save.** Storing the secret at
    `/start` would switch 2FA on for every abandoned setup page, against a
    phone that never scanned it — and the way back in needs a code.
    """
    user = _own_account(request, state)
    pending = state.totp_pending.get(user.name)
    if not pending:
        raise HTTPException(
            status_code=409,
            detail="That setup has expired. Start again to get a fresh key.",
        )

    matched = totp.verify(pending, body.code)
    if matched is None:
        # Not recorded as a login failure and not rate-limited: the caller is
        # already signed in, so guessing here buys nothing that the session
        # does not already grant.
        raise HTTPException(
            status_code=400,
            detail="That code is not right. Check the key went into the app, and its clock.",
        )

    try:
        await asyncio.to_thread(state.users.set_totp, user.name, pending)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    state.totp_pending.clear(user.name)
    # The step number carried over from an earlier secret means nothing against
    # this one, and would refuse the first code from it.
    state.totp_replay.forget(user.name)
    # The code that confirmed it is spent like a login's (RFC 6238 §5.2), or it
    # signed in once more inside the 90 seconds it stays valid.
    # After `forget`, so the record is this secret's step.
    state.totp_replay.spend(user.name, matched)
    return {"ok": True}


@router.post("/totp/disable", dependencies=[Depends(require_session)])
async def totp_disable(
    body: TotpDisableRequest, request: Request, state: WebState = Depends(get_panel_state)
) -> dict:
    """Turn the second factor off for your own account.

    The password is required for the reason the feature exists: somebody at an
    unlocked browser can already do whatever the role allows, and the one
    setting whose entire job is to survive that must not be one of them.
    """
    user = _own_account(request, state)
    await _confirm_password(state, user, body.password, body.code)

    try:
        await asyncio.to_thread(state.users.set_totp, user.name, "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    state.totp_pending.clear(user.name)
    state.totp_replay.forget(user.name)
    return {"ok": True}
