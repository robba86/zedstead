"""/api/users — accounts and roles in the browser (TODO B4, ``plans/43`` step 5b).

The same `UserStore` the `zsm users` CLI drives, so every rule lives in one
place: the first account has to be an admin, the last admin can be neither
removed nor demoted, and no write is accepted while ``users.json`` cannot be
read. This router adds two rules of its own, both about the caller, whom the
store cannot know about: **you may not change your own role or delete
yourself**, and **setting your own password needs your current one** (2P-W1).

Why that is here rather than in the store: the store has no notion of who is
asking. The CLI has no notion either, deliberately (`plans/43` §5 — shell
access is already full control). Only a request knows, and an admin who demotes
themselves by misclicking has locked themselves out of the panel with no way
back in from inside it. `remove`'s last-admin guard does not cover it: with two
admins, either may demote the other *and* themselves.

The second keeps a session alone from setting a password that its holder then
uses to turn the account's second factor off; Your account asks the same.

Every route here is **admin-only**, and by the default in
`app/web/permissions.py` rather than by an annotation — a write is admin
unless it is named, and none of these is named. The read is named, because a
read defaults to viewer and the account list is not viewer business.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request

from app.web.deps import confirm_password, get_panel_state, require_session, session_token
from app.web.schemas import UserCreateRequest, UserPasswordRequest, UserRoleRequest
from app.web.state import WebState
from app.web.users import ROLES, sync_linked_fallback

router = APIRouter(
    prefix="/api/users", tags=["users"], dependencies=[Depends(require_session)]
)

OWN_PASSWORD_NEEDS_CURRENT = (
    "Setting your own password needs your current one: send it as `current`, "
    "or use Your account, which asks for it."
)


def _caller(request: Request) -> str:
    """The signed-in name `require_session` stashed, lowercased for matching."""
    return (getattr(request.state, "username", "") or "").strip().lower()


def _refuse_self(request: Request, name: str, what: str) -> None:
    if _caller(request) == (name or "").strip().lower():
        raise HTTPException(status_code=400, detail=f"You cannot {what} yourself.")


def _fail(exc: ValueError) -> HTTPException:
    """A store refusal is the caller's problem, not a 500.

    Every ValueError the store raises is a rule the caller broke — the last
    admin, a duplicate name, an unknown role, an unreadable file — and each is
    phrased for a person already.
    """
    return HTTPException(status_code=400, detail=str(exc))


@router.get("")
async def list_users(state: WebState = Depends(get_panel_state)) -> dict:
    """The accounts, never their hashes (``User.public``).

    ``degraded`` is reported rather than hidden: an unreadable file means the
    panel has silently fallen back to the single pre-accounts password, which
    is a thing its owner needs to be told, and it is also why every write
    below will refuse until it is fixed.
    """
    users = await asyncio.to_thread(state.users.load)
    return {
        "users": [u.public() for u in users],
        "roles": list(ROLES),
        "path": str(state.users.path),
        "degraded": state.users.degraded,
    }


@router.post("")
async def create_user(
    body: UserCreateRequest, state: WebState = Depends(get_panel_state)
) -> dict:
    try:
        user = await asyncio.to_thread(
            state.users.add, body.name, body.password, body.role
        )
    except ValueError as exc:
        raise _fail(exc) from None
    return {"ok": True, "user": user.public()}


@router.put("/{name}/role")
async def set_user_role(
    name: str,
    body: UserRoleRequest,
    request: Request,
    state: WebState = Depends(get_panel_state),
) -> dict:
    _refuse_self(request, name, "change the role of")
    try:
        before = await asyncio.to_thread(state.users.get, name)
        user = await asyncio.to_thread(state.users.set_role, name, body.role)
    except ValueError as exc:
        raise _fail(exc) from None
    # Demoting the account made from the single password retires that
    # password, as deleting it does (plans/68).
    await sync_linked_fallback(
        state, user, previous_role=before.role if before else user.role
    )
    return {"ok": True, "user": user.public()}


@router.put("/{name}/password")
async def set_user_password(
    name: str,
    body: UserPasswordRequest,
    request: Request,
    state: WebState = Depends(get_panel_state),
) -> dict:
    """Set somebody's password, and sign them out everywhere.

    A reset is what an admin does when an account's password may be known to
    somebody else, so the sessions opened with the old one have to end with
    it; leaving them open kept whoever held one signed in for as long as they
    kept using it. ``revoke_all_for`` is scoped to that account, so resetting
    a colleague's password never ends the admin's own session, and resetting
    your own here keeps the session you did it from — the same rule as
    ``POST /api/settings/password``.

    **Your own account needs your current password** (plans/68 2P-W1), as on
    Your account. Without it, a session alone could set a password its holder
    then used to turn the account's second factor off. It is checked through
    `confirm_password`, so it is rate-limited like a sign-in as you. With no
    row to check against (a fallback session), the store refuses the name.
    """
    if _caller(request) == (name or "").strip().lower():
        own = await asyncio.to_thread(state.users.get, name)
        if own is not None:
            if not body.current:
                raise HTTPException(status_code=400, detail=OWN_PASSWORD_NEEDS_CURRENT)
            await confirm_password(
                state, own.name, body.current, own.password_hash, "Current password is incorrect"
            )
    try:
        user = await asyncio.to_thread(
            state.users.set_password, name, body.password
        )
    except ValueError as exc:
        raise _fail(exc) from None
    await sync_linked_fallback(state, user)
    state.sessions.revoke_all_for(user.name, session_token(request))
    return {"ok": True, "user": user.public()}


@router.delete("/{name}")
async def delete_user(
    name: str, request: Request, state: WebState = Depends(get_panel_state)
) -> dict:
    _refuse_self(request, name, "delete")
    try:
        removed = await asyncio.to_thread(state.users.remove, name)
    except ValueError as exc:
        raise _fail(exc) from None
    if not removed:
        raise HTTPException(status_code=404, detail=f"No such account: {name}")
    # Deleting the account made from the single password retires that password
    # too (plans/68): it would otherwise stay a way in as admin.
    await sync_linked_fallback(state, removed, removed=True)
    # Now rather than on their next request (`require_session` would drop the
    # session then): an open WebSocket makes no requests, and kept receiving
    # every broadcast after the account was gone (test pass 2, N27).
    state.sessions.revoke_all_for(name)
    return {"ok": True}
