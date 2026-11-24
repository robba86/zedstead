"""GET/PUT /api/settings, POST /api/settings/password.

Everything here is admin-only by the default in `app/web/permissions.py`
**except** ``POST /password``, which every role may call for its own account
— see `permissions.SESSION_WRITES` and `change_password` below.
"""

from __future__ import annotations

import asyncio

import click
from fastapi import APIRouter, Depends, HTTPException, Request

from app.shared import container, settings_registry
from app.web.allowed_hosts import lockout_problem
from app.web.auth import check_new_password, hash_password
from app.web.deps import (
    confirm_password,
    current_user,
    get_panel_state,
    get_state,
    require_session,
    session_token,
)
from app.web.schemas import PasswordChangeRequest, SettingsUpdateRequest
from app.web.state import ConfigLocked, WebState
from app.web.users import sync_linked_fallback

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_session)])


def _panel_setting_note(state: WebState) -> str:
    """Why a panel setting is read-only here, and where it is edited."""
    from app.server.instances import instance_summary

    label = instance_summary(state.shared.startup.config.instance_id)["label"]
    return f"App setting: it applies to the whole app. Edit it on {label}."


@router.get("")
async def get_settings(state: WebState = Depends(get_state)) -> list[dict]:
    # The values come from the file, as a save does: a value set with the CLI
    # (web_lock_paths above all) showed its old value here until a restart.
    config = state.config.reloaded() or state.config
    # `locked` is true only while the path lock is on and this field is in the
    # locked set — B reads it to render the field read-only (plans/58). The
    # lock is the panel's as well as this instance's (plans/62 D5).
    locked_now = state.paths_locked()
    locked_fields = settings_registry.web_locked_fields()
    # A panel setting is read-only on every instance but the startup one,
    # where it takes effect (plans/62 D4).
    panel_note = "" if state.is_startup else _panel_setting_note(state)
    # Set by the container's environment at every start: an edit here would
    # save and then be undone, so it is shown read-only with the reason.
    env_set = container.env_locked_fields(config)
    sections: dict[str, list[dict]] = {}
    for s in settings_registry._REGISTRY:
        if s.editor:
            continue  # shown by its Configs editor (zsm_forms.py)
        entry = {
            "key": s.key,
            "type": s.kind,
            "value": settings_registry._read(s, config),
            "help": s.description,
            "locked": locked_now and s.key in locked_fields,
        }
        if s.choices is not None:
            # A closed list: the page shows a picker, not a text box.
            entry["choices"] = list(s.choices)
            if s.choice_help:
                entry["choice_help"] = dict(s.choice_help)
        if s.label:
            entry["label"] = s.label
        if s.unit:
            entry["unit"] = s.unit
        if s.link:
            entry["link"] = {"text": s.link[0], "url": s.link[1]}
        if s.key in env_set:
            entry["env"] = {"var": env_set[s.key], "note": container.env_lock_note(env_set[s.key])}
        if s.panel and panel_note:
            entry["panel"] = {
                "note": panel_note,
                "instance": state.shared.startup.config.instance_id,
            }
        sections.setdefault(s.section, []).append(entry)
    return [{"section": section, "keys": keys} for section, keys in sections.items()]


@router.put("")
async def update_settings(
    body: SettingsUpdateRequest, request: Request, state: WebState = Depends(get_state)
) -> dict:
    unknown = [k for k in body.values if k not in settings_registry._BY_KEY]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown setting(s): {', '.join(unknown)}")
    if not state.is_startup:
        panel_only = sorted(k for k in body.values if k in settings_registry.panel_keys())
        if panel_only:
            raise HTTPException(status_code=409, detail=(
                f"{', '.join(panel_only)}: {_panel_setting_note(state)}"))

    parsed: dict[str, object] = {}
    for key, raw in body.values.items():
        setting = settings_registry._BY_KEY[key]
        try:
            parsed[key] = settings_registry._parse(setting, raw)
        except click.BadParameter as e:
            raise HTTPException(status_code=422, detail=str(e))

    problem = container.env_lock_problem(
        container.env_locked_fields(state.config),
        [k for k, v in parsed.items() if getattr(state.config, k, v) != v])
    if problem:
        raise HTTPException(status_code=409, detail=problem)

    # A list without the name this browser uses would refuse its next
    # request; only `zsm settings set` on the server could undo it.
    if "web_allowed_hosts" in parsed:
        problem = lockout_problem(str(parsed["web_allowed_hosts"]), request.headers.get("host"))
        if problem:
            raise HTTPException(status_code=400, detail=problem)

    def _apply(cfg) -> None:
        for key, value in parsed.items():
            settings_registry._write(settings_registry._BY_KEY[key], cfg, value)

    try:
        await state.mutate_config(_apply)
    except ConfigLocked as e:
        raise HTTPException(status_code=403, detail=str(e))
    except click.BadParameter as e:
        # A setter that refuses what is on disk (server_branch, plans/66).
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True}


@router.post("/password")
async def change_password(
    body: PasswordChangeRequest,
    request: Request,
    state: WebState = Depends(get_panel_state),
    user=Depends(current_user),
) -> dict:
    """Change **your own** password — the one you actually sign in with.

    Which store that is, is not a preference. It is whichever one
    ``UserStore.authenticate`` will consult on the next login: the caller's row
    in ``users.json`` when accounts exist, and ``config.web_password_hash``
    only when they do not — a fresh install, or a ``users.json`` that cannot be
    read. Those are the same two states `authenticate` falls back on, and
    `current_user` returning ``None`` is exactly how they arrive here.

    **Between ``plans/43`` step 1 and 2026 Q3 this wrote the wrong one.**
    `create_app` migrates the single password into ``users.json`` on startup,
    after which nothing reads ``config.web_password_hash`` — but this route
    kept verifying against it and rewriting it. So on every install that had
    ever set a password it answered 200, the toast said the password had
    changed and other sessions had been signed out, and the old password went
    on working. That is worse than a failure: it is the one error whose whole
    consequence is that somebody keeps using a credential they believe they
    retired. `tests/test_web_settings.py` now signs in with the new password
    rather than trusting the 200.
    """
    token = session_token(request)
    # A wrong current password is 400, not 401: the caller is signed in, and
    # the web UI answers every 401 by sending the page to /login, which lost
    # the form instead of showing the sentence. 401 means "not signed in".
    # The guess counts in the login limiter like a sign-in (`confirm_password`).
    wrong_current = "Current password is incorrect"

    if user is not None:
        await confirm_password(state, user.name, body.current, user.password_hash, wrong_current)
        try:
            updated = await asyncio.to_thread(state.users.set_password, user.name, body.new)
        except ValueError as exc:
            # The store refuses while users.json is unreadable, and that is the
            # caller's problem phrased for a person, not a 500.
            raise HTTPException(status_code=400, detail=str(exc)) from None
        # The account made from the single password keeps that password in
        # step with its own (plans/68).
        await sync_linked_fallback(state, updated)
        state.sessions.revoke_all_for(user.name, token)
        return {"ok": True}

    # The single password signs in with a blank name, so its guesses count in
    # the bucket a blank sign-in fills.
    await confirm_password(state, "", body.current, state.config.web_password_hash, wrong_current)
    try:
        check_new_password(body.new)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    new_hash = await asyncio.to_thread(hash_password, body.new)
    await state.mutate_config(lambda cfg: setattr(cfg, "web_password_hash", new_hash))
    # Whole-panel here and only here: with no accounts, one password is what
    # every open session authenticated with.
    state.sessions.revoke_all_except(token)
    return {"ok": True}
