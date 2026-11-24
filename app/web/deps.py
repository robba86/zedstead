"""FastAPI dependencies: state access, session auth, and the role gate."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from fastapi import Depends, HTTPException, Request
from starlette.requests import HTTPConnection

from app.web import permissions
from app.web.auth import SECURE_SESSION_COOKIE_NAME, SESSION_COOKIE_NAME, verify_password
from app.web.state import WebState

_log = logging.getLogger("zsm.web")

# What a session gets when there are no accounts to look it up in. This is the
# pre-accounts install: one password, full control, which is what the panel has
# always been. See `_role_for`.
FALLBACK_ROLE = "admin"

# `_role_for`'s answer for a session that did not come through the fallback
# password while there are no readable accounts: refused, but not revoked, so
# it works again once users.json does.
NO_ACCOUNTS = "no-accounts"
NO_ACCOUNTS_DETAIL = (
    "Accounts cannot be read right now (users.json), so only the app's "
    "single password signs in until that file is fixed."
)


def get_state(request: Request) -> WebState:
    """The instance this request is about: its ``/i/<id>/`` prefix, or the
    instance the panel started with (plans/62)."""
    return request.app.state.panel.bundle_for(request)


def get_panel(request: Request):
    """The ``Panel``: every instance this ``zsm web`` serves."""
    return request.app.state.panel


def get_panel_state(request: Request) -> WebState:
    """The startup instance, whatever the URL says.

    For what belongs to the panel rather than to one server: signing in, the
    session cookie, your own password. Their settings (``web_password_hash``,
    ``web_force_secure_cookie``) take effect only on the instance the
    listener was started with, so reading them anywhere else would check a
    password nobody can sign in with.
    """
    return request.app.state.panel.startup


def cookie_is_secure(conn: HTTPConnection, state: WebState | None = None) -> bool:
    """Whether the session cookie is ``Secure``, which also decides its name.

    True when the connection arrived over https (or ``wss``, the same
    connection seen by the WebSocket) — directly (``zsm web --self-signed`` /
    ``--tls-cert``) or via a reverse proxy whose X-Forwarded-Proto uvicorn was
    told to trust (see ``web_trusted_proxies`` and ``start_server``).
    ``web_force_secure_cookie`` overrides for topologies where that header
    can't be relied on.

    Marking it Secure on a plain-http deployment would make the cookie
    undeliverable and lock the user out, so this must not default to True — the
    common deployment is a loopback bind over HTTP.

    *state* is the panel's bundle (`get_panel_state`): the override is a panel
    setting. Left out, it is looked up from the connection.
    """
    panel_state = state if state is not None else conn.app.state.panel.startup
    if panel_state.config.web_force_secure_cookie:
        return True
    return conn.url.scheme in ("https", "wss")


def session_cookie_name(conn: HTTPConnection, state: WebState | None = None) -> str:
    """``__Host-zsm_session`` when the cookie is Secure, ``zsm_session``
    otherwise (plans/68).

    Login sets the cookie under this name and logout deletes it under this
    name; `session_token` reads it under this name and no other. Over https a
    cookie called plain ``zsm_session`` is therefore not a session, which is
    the point of the prefix: only a secure response could have set the one
    that is.
    """
    return SECURE_SESSION_COOKIE_NAME if cookie_is_secure(conn, state) else SESSION_COOKIE_NAME


def session_token(conn: HTTPConnection) -> str | None:
    """The session token this request or WebSocket carries, or ``None``.

    The one reader of the session cookie: the role gate, the WebSocket
    handshake, logout and the two password routes that keep the caller's own
    session all go through it, so none of them can read a name the others
    do not.
    """
    return conn.cookies.get(session_cookie_name(conn))


def require_session(request: Request, state: WebState = Depends(get_state)) -> None:
    """Refuse the request unless the caller has a session **and** the role for it.

    Applied via ``APIRouter(dependencies=[Depends(require_session)])`` on
    every router except auth/login, health, static files, and the SPA
    fallback.

    Four refusals, and they say different things on purpose:

    * **401, no session** — not signed in.
    * **401, session revoked** — signed in as somebody who no longer exists.
      See `_role_for`; the session is dropped on the way out so the dead token
      stops coming back.
    * **401, no accounts** — signed in through an account while there are no
      readable accounts. Kept, not revoked: it works again once they read.
    * **403** — signed in, but this route needs a role the caller does not
      have. The body names both roles; `permissions.refusal_message` says why.

    The caller is stashed on ``request.state`` so a later dependency, a route
    and the access log can name them without re-reading the cookie.
    """
    token = session_token(request)
    username = state.sessions.validate(token)
    if username is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    request.state.username = username
    # The name a version saved in this request records (app/shared/actor.py).
    from app.shared import actor

    actor.set_name(username)

    role = _role_for(state, username, token)
    if role is None:
        state.sessions.revoke(token)
        raise HTTPException(status_code=401, detail="Not authenticated")
    if role == NO_ACCOUNTS:
        raise HTTPException(status_code=401, detail=NO_ACCOUNTS_DETAIL)
    request.state.role = role

    _enforce_role(request, username, role)


def _role_for(state: WebState, username: str, token: str | None) -> str | None:
    """The role behind this session, ``None`` if the session is stale, or
    `NO_ACCOUNTS`.

    Three states, and conflating the last two is a privilege escalation:

    1. **The account exists** → its role, read from ``users.json`` *now* rather
       than from the session, so demoting somebody takes effect on their next
       request instead of whenever their week-old cookie expires.
    2. **There are no accounts at all** → ``admin`` **for a session opened with
       the fallback password**, and `NO_ACCOUNTS` (refused, kept) for any
       other. This is the install that has one password and never migrated,
       or one whose ``users.json`` cannot be read (`UserStore.has_accounts`
       treats those alike, deliberately). Signing in with the single password
       is what keeps the admin from being locked out of their own box.
       Until plans/68 every session got ``admin`` here, so a viewer whose tab
       was open when the file broke had full control until it was fixed: the
       session names an account nobody can look up, and a name is not a role.
    3. **Accounts exist but this session names none of them** → ``None``. The
       account was removed while the session was open. Returning ``admin``
       here — the obvious way to write state 2 — would mean *deleting* an
       account **promoted** its live session to full control, which is the
       exact opposite of what the person clicking Remove intended.
    """
    user = state.users.get(username)
    if user is not None:
        return user.role
    if not state.users.has_accounts():
        return FALLBACK_ROLE if state.sessions.is_fallback(token) else NO_ACCOUNTS
    return None


def _enforce_role(request: Request, username: str, role: str) -> None:
    """Raise 403 unless *role* is enough for this method and path.

    **A failure in here refuses the request**, which is the opposite of what
    report mode did, and the reversal is the point of step 4. While nothing
    was enforced, a classifier bug had to cost a log line rather than the
    request. Now that the answer decides access, "could not tell" must not
    read as "allowed" — the same rule `firewall_ops` states for a port it
    could not probe, with the sign flipped to match the stakes. The recovery
    path if this ever fires wrongly is the CLI, which `plans/43` §5 keeps
    deliberately free of roles: shell access to the box is already full
    control.

    The path classified is ``scope["path"]``, the one the router matched, never
    ``request.url.path`` (plans/68 AUTH-7). Starlette rebuilds ``request.url``
    from the decoded path and parses it again, so an escaped ``?`` or ``#``
    (``/api/configs/serverdz%3F/raw``) cut it short and the gate classified
    ``/api/configs/serverdz`` while the router served the longer route.
    """
    path = request.scope["path"]
    try:
        refusal = permissions.would_refuse(request.method, path, role)
        detail = permissions.refusal_message(request.method, path, role) if refusal else ""
    except Exception:  # noqa: BLE001 - see the docstring
        _log.exception("[roles] classifier failed for %s %s", request.method, path)
        raise HTTPException(
            status_code=403, detail="Could not decide whether you may do this."
        ) from None
    if refusal:
        _log.info("[roles] refused %s: %s", username, refusal)
        raise HTTPException(status_code=403, detail=detail)


TOO_MANY_PASSWORD_GUESSES = "Too many wrong passwords. Try again in a few minutes."


async def confirm_password(
    state: WebState,
    who: str,
    password: str,
    hashed: str,
    wrong: str,
    then: Callable[[], str | None] | None = None,
) -> None:
    """Refuse unless *password* matches *hashed*: 400 saying *wrong*, or 429
    once this name has had too many wrong ones (plans/68 AUTH-8).

    *then* is a second check inside the same attempt, run once the password
    matched: it returns the 400 detail to refuse with, or ``None``. A refusal
    counts as a wrong guess, and only both passing resets the count, or a
    right password would wipe the count of wrong second-factor codes.

    For a signed-in caller re-entering their password (the password change,
    starting or turning off a second factor). The guess counts in the same
    login limiter bucket a sign-in as *who* fills: the account's name, or
    ``""`` for the single pre-accounts password. So a session cannot be used
    to try passwords faster than the login form allows, and a wrong password
    here counts against signing in too.

    400, not 401: the caller *is* signed in, and the web UI sends every 401
    to /login.
    """
    attempt = state.login_limiter.begin(who)
    if attempt is None:
        raise HTTPException(status_code=429, detail=TOO_MANY_PASSWORD_GUESSES)
    try:
        # scrypt is slow on purpose: off the event loop, as login does it.
        if not await asyncio.to_thread(verify_password, password or "", hashed):
            attempt.fail()
            raise HTTPException(status_code=400, detail=wrong)
        refusal = await asyncio.to_thread(then) if then else None
        if refusal:
            attempt.fail()
            raise HTTPException(status_code=400, detail=refusal)
        state.login_limiter.record_success(who)
    finally:
        attempt.close()


def current_user(request: Request, state: WebState = Depends(get_state)):
    """The ``User`` behind this request, or ``None``.

    ``None`` covers two states that `require_session` has already told apart
    and callers here must not: no session at all, and a session that
    authenticated through the pre-accounts fallback, where there is no
    ``users.json`` row to find. A session naming a *deleted* account never
    reaches a route — see `_role_for`.
    """
    username = getattr(request.state, "username", None)
    if username is None:
        username = state.sessions.validate(session_token(request))
    if not username:
        return None
    return state.users.get(username)
