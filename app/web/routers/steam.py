"""GET /api/steam/status, POST /api/steam/login.

The web UI's Steam login: credentials (and an optional Steam Guard code) are
posted once, handed to SteamCMD in a script only this user can read
(``steam.login_script``, never on its command line, where every local user
could read them), and never persisted — only ``steam_username`` is written
to the config after a successful login. Output lines are redacted so the
password/guard code can never land in the job log.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.shared import steamcmd_lock
from app.shared.steam import (
    diagnose_login_output,
    login_script,
    login_script_cmd,
    login_script_problems,
    steam_username_problem,
    steamcmd_word_problem,
)
from app.web.deps import get_state, require_session
from app.web.jobs import Job, JobConflictError, conflict_response, stream_subprocess
from app.web.state import WebState

router = APIRouter(
    prefix="/api/steam", tags=["steam"], dependencies=[Depends(require_session)]
)


@router.get("/status")
async def steam_status(check: bool = False, state: WebState = Depends(get_state)) -> dict:
    """``logged_in`` is the cached answer (``null`` = not measured yet);
    ``?check=1`` measures it now, which runs SteamCMD (plans/51 D)."""
    config = state.config
    return {
        "steamcmd_present": config.steamcmd_path.is_file(),
        "username": config.steam_username,
        "logged_in": await state.refresh_steam() if check else state.steam_known(),
    }


class SteamGuardRequest(BaseModel):
    username: str
    password: str


class SteamLoginRequest(BaseModel):
    username: str
    password: str
    guard_code: str = ""


@router.post("/request-guard")
async def steam_request_guard(
    body: SteamGuardRequest, state: WebState = Depends(get_state)
) -> dict:
    """Phase 1 of the two-pass web login.

    SteamCMD emails a Steam Guard code only *after* a login is attempted, but a
    codeless attempt fails for Guard-protected accounts. So we run one codeless
    login here (a ``login`` line and ``quit`` in ``steam.login_script``): rc==0
    means the account has no Guard (or is already cached) and we're logged in.

    A non-zero rc on its own does **not** mean a code was sent — a wrong
    password, a rate limit and a Guard prompt all exit non-zero. So the output
    is captured (redacted, never logged) and run through
    ``steam.diagnose_login_output``: a hard failure becomes a 400 with the real
    reason, a Guard prompt returns ``factor`` so the client can name the right
    place to read the code from. An unclassifiable failure still asks for a
    code — that was the only behaviour before.

    Not a job — the Guard "failure" is expected and must not surface as an
    error toast.
    """
    config = state.config
    if not config.steamcmd_path.is_file():
        raise HTTPException(status_code=409, detail="SteamCMD is not installed.")

    username = body.username.strip()
    _refuse_anonymous(username)
    password = body.password
    _refuse_unscriptable(username, password)
    redact = _make_redactor([password])

    def _attempt(cmd: list[str]) -> tuple[int, str]:
        import subprocess

        def _text(*parts) -> str:
            out = []
            for part in parts:
                if isinstance(part, bytes):
                    out.append(part.decode(errors="replace"))
                elif part:
                    out.append(str(part))
            return redact("\n".join(out))

        try:
            result = steamcmd_lock.run_to_end(cmd, timeout=120, capture=True)
            return result.returncode, _text(result.stdout, result.stderr)
        except subprocess.TimeoutExpired as exc:
            # A prompt SteamCMD is blocking on is in the partial output.
            return 1, _text(exc.stdout, exc.stderr)
        except OSError as exc:
            return 1, _text(str(exc))

    try:
        with login_script(username, password) as script:
            cmd = login_script_cmd(config, script)
            with steamcmd_lock.hold(cmd, wait=0):
                rc, output = await asyncio.to_thread(_attempt, cmd)
    except steamcmd_lock.SteamcmdBusy as exc:
        raise HTTPException(
            status_code=409, detail=f"{exc} Try again when it has finished."
        ) from None
    if rc == 0:
        def _set_username(cfg):
            cfg.steam_username = username

        await state.mutate_config(_set_username)
        state.note_steam_ok(True)
        await state.announce_steam()
        return {"logged_in": True, "needs_code": False, "username": username}

    diagnosis = diagnose_login_output(output)
    if diagnosis.error:
        raise HTTPException(status_code=400, detail=diagnosis.error)
    return {
        "logged_in": False,
        "needs_code": True,
        "username": username,
        "factor": diagnosis.factor,
    }


def _refuse_anonymous(username: str) -> None:
    """An anonymous login can't download DayZ, so it never becomes the account;
    and a value SteamCMD would not take as one argument never reaches it."""
    problem = steam_username_problem(username)
    if problem:
        raise HTTPException(status_code=400, detail=problem)


def _refuse_unscriptable(username: str, password: str, guard_code: str = "") -> None:
    """A value that cannot be written into SteamCMD's login script is a 400
    before anything runs (``steam.login_script_problems``)."""
    problem = login_script_problems(username, password, guard_code)
    if problem:
        raise HTTPException(status_code=400, detail=problem)


def _make_redactor(secrets: list[str]):
    secrets = [s for s in secrets if s]

    def _redact(line: str) -> str:
        for secret in secrets:
            line = line.replace(secret, "***")
        return line

    return _redact


@router.post("/login")
async def steam_login(body: SteamLoginRequest, state: WebState = Depends(get_state)):
    config = state.config
    if not config.steamcmd_path.is_file():
        raise HTTPException(status_code=409, detail="SteamCMD is not installed.")

    username = body.username.strip()
    _refuse_anonymous(username)
    password = body.password
    guard_code = body.guard_code.strip()
    if guard_code:
        problem = steamcmd_word_problem("The Steam Guard code", guard_code)
        if problem:
            raise HTTPException(status_code=400, detail=problem)
    _refuse_unscriptable(username, password, guard_code)
    redact = _make_redactor([password, guard_code])

    async def _run(job: Job) -> dict:
        # Keep the (already redacted) lines so a failure can say *why* instead
        # of the old catch-all "check the credentials" — a rejected code and a
        # rate limit need opposite reactions from the user.
        seen: list[str] = []

        def _capture(line: str) -> str:
            clean = redact(line)
            if len(seen) < 400:
                seen.append(clean)
            return clean

        job.log_line(f"Logging in to Steam as {username}...")
        # Created here, not with the request: the job may wait for another
        # SteamCMD first, and a job that never starts must leave nothing behind.
        with login_script(username, password, guard_code) as script:
            rc = await stream_subprocess(
                job, login_script_cmd(config, script), transform=_capture
            )
        if rc != 0:
            diagnosis = diagnose_login_output("\n".join(seen))
            raise RuntimeError(
                diagnosis.error
                or (
                    "Steam login failed. Check the credentials — if your account "
                    "uses Steam Guard, submit the code from your Steam mobile app "
                    "or the email Steam sent."
                )
            )
        job.log_line("Steam login successful — credentials cached.")

        def _set_username(cfg):
            cfg.steam_username = username

        await state.mutate_config(_set_username)
        state.note_steam_ok(True)
        await state.announce_steam()
        return {"logged_in": True, "username": username}

    try:
        # Every role sees job names; the account is half of a Steam login.
        job = state.jobs.start("Steam login", "steam_login", _run)
    except JobConflictError as exc:
        return conflict_response(exc)
    return {"job_id": job.id}
