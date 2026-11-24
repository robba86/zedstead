"""GET /api/quick-setup/mods and POST /api/quick-setup/apply: a first server in
one click. Apply is one job, because ``JobManager`` runs one job at a time. Each
part reuses the code of the Update, Mods and Presets pages, and each part is safe
to run again after a stop."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.modmgr import mod_ops, modlist, mods, recommended_mods
from app.modmgr.recommended_mods import ModSet
from app.ops import pipeline, post_update
from app.server import service_control
from app.server.instances import server_dir_unwritable
from app.shared.json_utils import atomic_write_text
from app.web.deps import get_state, require_session
from app.web.jobs import Job, JobConflictError, conflict_response
from app.web.routers.mods import _regenerate_service, _toggle_mod_economy, run_mod_install
from app.web.routers.presets import apply_preset_to_mission
from app.web.routers.setup import _STOCK_MAP
from app.web.routers.update import _make_context
from app.web.map_editor_svc import reload_map_editor
from app.web.routers.maps import _switch_vanilla_map
from app.web.schemas import QuickSetupApplyRequest, QuickSetupServer
from app.web.state import WebState

router = APIRouter(prefix="/api/quick-setup", tags=["quick-setup"], dependencies=[Depends(require_session)])

_WORKSHOP_URL = "https://steamcommunity.com/sharedfiles/filedetails/?id="


def quick_maps() -> list[str]:
    """The maps the "Your server" card offers: Bohemia's maps that ship with the
    server, so the setup downloads no map. Community maps stay on the Maps page."""
    from app.shared.seed_data import VANILLA_MAPS

    return [name for name, data in VANILLA_MAPS.items() if data.get("mission") and data.get("released", True)]


def _map_choices() -> list[dict]:
    """The card's map list, each with the paid DLC a player needs to join it."""
    from app.shared.seed_data import VANILLA_MAPS

    return [
        {"name": name, "dlc": VANILLA_MAPS[name].get("dlc", ""), "dlc_note": VANILLA_MAPS[name].get("dlc_note", "")}
        for name in quick_maps()
    ]


def _target_map(config, chosen: str | None = None) -> str | None:
    """*chosen*, else the active map. A server that is not installed gets DayZ's stock map."""
    from app.commands.doctor import server_is_installed
    from app.maps.map_ops import get_active_map_name

    if chosen:
        return chosen
    name = get_active_map_name(config)
    if name is None and not server_is_installed(config):
        return _STOCK_MAP
    return name


def _deps_of(config, workshop_ids: list[str]) -> list[tuple[str, str]]:
    """Every dependency of the mods, also the ones that are installed already."""
    return mod_ops.get_missing_deps_ex(config, workshop_ids, set())


def _listed_mods(config) -> list[tuple[str, str]]:
    """The mods the server loads as ``(name, Workshop ID)``, with an empty ID when
    none is known. A mod turned off on the Mods page (or by "Turn off these
    mods") is not loaded, so it neither blocks Vanilla nor mixes with a set."""
    names, ids = modlist.installed_and_ids(config)
    off = modlist.disabled(config)
    return [(name, ids.get(name, "")) for name in names if name not in off]


def _outside(config, listed: list[tuple[str, str]], workshop_ids: list[str]) -> list[str]:
    """The names of listed mods that are not in *workshop_ids* or their dependencies."""
    allowed = {*workshop_ids, *(dep_id for dep_id, _name in _deps_of(config, workshop_ids))}
    return [name for name, wid in listed if wid not in allowed]


def _set_payload(config, chosen: ModSet) -> dict:
    mods = []
    for wid in chosen.mods:
        mod = recommended_mods.get_mod(wid)
        mods.append({
            "workshop_id": wid, "name": mod.name, "blurb": mod.blurb,
            "url": f"{_WORKSHOP_URL}{wid}",
        })
    deps = [
        {"workshop_id": wid, "name": mod_ops.ensure_at_prefix(name), "url": f"{_WORKSHOP_URL}{wid}"}
        for wid, name in _deps_of(config, list(chosen.mods))
    ]
    return {
        "mods": mods, "dependencies": deps,
        "dayz_version": chosen.dayz_version, "tested_on": chosen.tested_on,
    }


def _mods_payload(config, seed: int, previous: list[str], chosen_map: str | None = None) -> dict:
    """What the mods part of the setup card shows: the rolled set, or the set a stopped Apply left.
    *chosen_map* is the "Your server" card's map, for the sets tested on it."""
    from app.web.editors.server_forms import env_locked_notes

    map_name = _target_map(config, chosen_map)
    listed = _listed_mods(config)
    resume = None
    if listed:
        resume = next(
            (s for s in recommended_mods.sets_for_map(map_name) if not _outside(config, listed, list(s.mods))),
            None,
        )
    chosen = resume or recommended_mods.roll(
        seed, map_name, previous=recommended_mods.find_set(previous, map_name),
    )
    return {
        "map": map_name,
        "seed": seed,
        "resume": resume is not None,
        "set": _set_payload(config, chosen) if chosen else None,
        "outside": _outside(config, listed, list(chosen.mods)) if chosen else [],
        # Apply without mods refuses while this is not empty (`_vanilla_refusal`).
        "vanilla_refusal": _vanilla_refusal([name for name, _wid in listed]) if listed else "",
        # The "Your server" card: its map list, and the fields a container's environment sets.
        "maps": _map_choices(),
        "locked": env_locked_notes(config),
        # What "Turn them on again" turns on (`turned_off_mods`).
        "turned_off": turned_off_mods(config),
    }


#: Seconds between the FPS lines DayZ writes to the .RPT. Quick setup turns
#: the line on, or a new server's FPS tile and history stay empty (night test
#: 2026-11-22). The default Run command already has -doLogs.
FPS_LOG_SECONDS = 60


def server_values(config, server: QuickSetupServer) -> dict:
    """The card's answers as serverDZ.cfg keys. A key the container's
    environment sets is left out, since the environment writes it.
    ``logAverageFps`` is added only when serverDZ.cfg does not set it, so an
    admin's own value, 0 included, stays."""
    from app.parsers.servercfg import read_cfg_values
    from app.web.editors.server_forms import env_locked_notes

    values = {
        "hostname": server.hostname.strip(),
        "password": server.password,
        "passwordAdmin": server.password_admin,
        "maxPlayers": server.max_players,
    }
    if not (read_cfg_values(config.server_cfg_path, ["logAverageFps"]).get("logAverageFps") or "").strip():
        values["logAverageFps"] = FPS_LOG_SECONDS
    locked = env_locked_notes(config)
    return {k: v for k, v in values.items() if k not in locked}


def _server_refusal(config, server: QuickSetupServer) -> str:
    """Why the card's answers cannot be written, or an empty string."""
    from app.web.editors.server_forms import check_serverdz_values

    if server.map not in quick_maps():
        return f"Quick setup cannot set up the map {server.map!r}. Pick one of: {', '.join(quick_maps())}."
    values = server_values(config, server)
    if "hostname" in values and not values["hostname"]:
        return "Give the server a name."
    if not server.password_admin:
        return "Set an in-game admin password."
    try:
        check_serverdz_values(values)
    except ValueError as e:
        return str(e)
    return ""


def setup_offered(config) -> bool:
    """Whether Quick setup is offered: no Quick setup finished on this instance,
    or the server is gone since (a Docker recreate deletes it). An install from
    the Dashboard, the Doctor page or the CLI writes no marker, so the page stays
    offered for the difficulty and the mods. A server that is set up gets a new
    instance for a second server, not a second run."""
    from app.commands.doctor import server_is_installed

    return not config.paths.quick_setup_done.exists() or not server_is_installed(config)


_SET_UP_ALREADY = (
    "This server is set up already. Change its settings on My server. "
    "To set up another server, add an instance on the Instances page."
)


def _vanilla_refusal(listed: list[str]) -> str:
    """Why Apply without mods cannot run on a server that loads *listed*."""
    return (
        f"Your server loads {', '.join(listed)}, so it is not vanilla. "
        "Turn these mods off first. They stay installed, and you can turn them on again on the Mods page."
    )


def _refusal(config, body: QuickSetupApplyRequest) -> tuple[int, str, ModSet | None] | None:
    """Why this Apply cannot start, as ``(status, message, None)``. Otherwise it
    returns ``(0, "", set)``, with a None set for a server without mods."""
    from app.presets.presets import BUILTIN_PRESETS

    if service_control.is_server_running(config):
        return 409, "The server is running — stop it before you apply a setup.", None
    if not setup_offered(config):
        return 409, _SET_UP_ALREADY, None
    unwritable = server_dir_unwritable(config.server_dir_path)
    if unwritable:
        return 409, unwritable, None
    if body.install_only:
        return 0, "", None
    if body.server is not None:
        problem = _server_refusal(config, body.server)
        if problem:
            return 400, problem, None
    if body.preset not in BUILTIN_PRESETS:
        return 400, f"Unknown preset: {body.preset!r}", None
    if not body.mod_ids:
        # Vanilla means no mods, so a list that holds mods would make its promise false.
        listed = [name for name, _wid in _listed_mods(config)]
        if listed:
            return 409, _vanilla_refusal(listed), None
        return 0, "", None
    unknown = [wid for wid in body.mod_ids if recommended_mods.get_mod(wid) is None]
    if unknown:
        return 400, f"Not a recommended mod: {', '.join(unknown)}", None
    outside = _outside(config, _listed_mods(config), body.mod_ids)
    if outside:
        return 409, (
            "Your mod list already holds mods that this setup does not include: "
            f"{', '.join(outside)}. Use the Mods page to add mods to this server."
        ), None
    chosen_map = body.server.map if body.server else None
    chosen = recommended_mods.find_set(body.mod_ids, _target_map(config, chosen_map))
    if chosen is None:
        return 400, f"These mods are not a tested set for {chosen_map or 'the active map'}.", None
    return 0, "", chosen


async def _part(job: Job, label: str, work: Awaitable, failed: Callable[[Any], str | None] | None = None):
    """Run one part of the job. A failure names the part, and a stop ends the job.
    A part that ends well logs a "✓" line, so a new admin sees each step land.
    `failed` reads a result that does not stop the job (a server that did not
    boot) and returns why, which logs a "✗" line in place of the "✓"."""
    job.log_line(f"--- {label} ---")
    try:
        result = await work
    except HTTPException as e:
        raise RuntimeError(f"{label}: {e.detail}") from e
    except Exception as e:
        raise RuntimeError(f"{label}: {e}") from e
    if job.cancel_event.is_set():
        raise RuntimeError(f"Stopped during {label}.")
    reason = failed(result) if failed else None
    job.log_line(f"✗ {label}: {reason}" if reason else f"✓ {label}: done")
    return result


# The boot outcomes the page reads as a failed start (BOOT_FAILED in QuickSetupPage.jsx).
_BOOT_FAILED = (post_update.CRASHED, post_update.NOT_JOINABLE, "not_started")


def _boot_failure(boot: dict | None) -> str | None:
    """Why a start-and-check result is a failed boot, or None when it booted."""
    if boot and boot.get("outcome") in _BOOT_FAILED:
        return boot["outcome"].replace("_", " ")
    return None


@router.get("/state")
async def quick_setup_state(state: WebState = Depends(get_state)) -> dict:
    """Whether the sidebar offers Quick setup, and why not when it does not."""
    offered = await asyncio.to_thread(setup_offered, state.config)
    return {"offered": offered, "reason": "" if offered else _SET_UP_ALREADY}


@router.get("/mods")
async def quick_setup_mods(
    seed: int | None = None,
    previous: list[str] = Query(default=[]),
    map: str | None = None,
    state: WebState = Depends(get_state),
) -> dict:
    """The set the setup card offers, for *map* when the card names one. It
    writes nothing and installs nothing."""
    if map is not None and map not in quick_maps():
        raise HTTPException(status_code=400, detail=f"Not a map Quick setup offers: {map!r}")
    used = random.randrange(2**31) if seed is None else seed
    try:
        return await asyncio.to_thread(_mods_payload, state.config, used, previous, map)
    except modlist.ModListError as e:
        raise HTTPException(status_code=409, detail=str(e))


_MODS_OFF_NOTE = "Before Quick setup turned off the mods"


def turned_off_mods(config) -> list[str]:
    """The mods "Turn off these mods" turned off that are still listed and still
    off, so "Turn them on again" has something to do. Empty when the file is
    missing or unreadable: the Mods page still turns each mod on."""
    import json

    try:
        names = json.loads(config.paths.quick_setup_mods_off.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(names, list):
        return []
    wanted, off = {n for n in names if isinstance(n, str)}, modlist.disabled(config)
    # In mod-list order, so the mods load in the order the admin set.
    return [n for n in modlist.installed(config) if n in wanted and n in off]


async def _set_mods(job: Job, state: WebState, names: list[str], *, on: bool) -> None:
    """Turn *names* on or off, as the Mods page switch does for one mod: the mod
    list, the mission XML, then the unit's -mod= list once at the end."""
    for name in names:
        if job.cancel_event.is_set():
            raise RuntimeError("Stopped during Mods.")
        mark = mods.mark_enabled if on else mods.mark_disabled
        current = await state.mutate_config(lambda cfg, name=name: mark(cfg, name))
        await asyncio.to_thread(_toggle_mod_economy, current, name, enable=on)
        job.log_line(f"Turned {'on' if on else 'off'} {name}")
    await _regenerate_service(state.config)
    await state.hub.broadcast({"type": "mods_changed"})


@router.post("/mods-off")
async def quick_setup_mods_off(state: WebState = Depends(get_state)):
    """Turn off every mod the server loads, after a quick backup of the player
    data: a mod's items leave the world at the next start without the mod. The
    mods stay installed, so the Mods page turns them on again."""
    from app.ops import backup_ops

    config = state.config
    if await asyncio.to_thread(service_control.is_server_running, config):
        raise HTTPException(status_code=409, detail="The server is running — stop it before you turn off the mods.")
    try:
        names = [name for name, _wid in await asyncio.to_thread(_listed_mods, config)]
    except modlist.ModListError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not names:
        raise HTTPException(status_code=409, detail="The server loads no mods.")

    async def _job_coro(job: Job) -> dict:
        job.log_line("--- Backup ---")
        problem = await asyncio.to_thread(backup_ops.quick_backup_problem, config)
        backup = None
        if problem:
            job.log_line(problem)  # no player data yet, so nothing to lose
        else:
            backup = await asyncio.to_thread(
                backup_ops.create_backup, config, backup_ops.BackupContents(),
                _MODS_OFF_NOTE, job.log_line_threadsafe,
            )
            if backup is None:
                raise RuntimeError("Backup: the quick backup did not write a file, so no mod was turned off.")
            await state.hub.broadcast({"type": "backups_changed"})
        job.log_line("--- Mods ---")
        # Written first, so a stop halfway still lets "Turn them on again" find them.
        import json

        await asyncio.to_thread(
            atomic_write_text, config.paths.quick_setup_mods_off, json.dumps(sorted({*names, *turned_off_mods(config)}))
        )
        await _set_mods(job, state, names, on=False)
        return {"turned_off": names, "backup": backup.name if backup else None}

    try:
        job = state.jobs.start("Turn off mods", "mods_off", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}


@router.post("/mods-on")
async def quick_setup_mods_on(state: WebState = Depends(get_state)):
    """Turn on again the mods "Turn off these mods" turned off."""
    config = state.config
    if await asyncio.to_thread(service_control.is_server_running, config):
        raise HTTPException(status_code=409, detail="The server is running — stop it before you turn on the mods.")
    try:
        names = await asyncio.to_thread(turned_off_mods, config)
    except modlist.ModListError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not names:
        raise HTTPException(status_code=409, detail="Quick setup turned off no mod that is still off.")

    async def _job_coro(job: Job) -> dict:
        await _set_mods(job, state, names, on=True)
        await asyncio.to_thread(config.paths.quick_setup_mods_off.unlink, missing_ok=True)
        return {"turned_on": names}

    try:
        job = state.jobs.start("Turn on mods", "mods_on", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}


@router.post("/apply")
async def quick_setup_apply(body: QuickSetupApplyRequest, state: WebState = Depends(get_state)):
    try:
        status, message, chosen = await asyncio.to_thread(_refusal, state.config, body)
    except modlist.ModListError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if status:
        raise HTTPException(status_code=status, detail=message)

    async def _install_server(job: Job) -> None:
        from app.commands.doctor import server_is_installed

        config = state.config
        if await asyncio.to_thread(server_is_installed, config):
            job.log_line("The server is installed already.")
            return
        # finalize installs the server's unit, the same pair as Doctor's Install
        # server (fixActions.js). Without it a fresh Docker box has no unit to start.
        ctx = _make_context(job, config, lambda key: key in ("update_server", "finalize"))
        results, _undoable = await pipeline.run_pipeline(pipeline.build_update_steps(config), ctx)
        await state.hub.broadcast({"type": "settings_changed"})
        failed = [r for r in results if r.status == "failed"]
        if failed:
            raise RuntimeError(failed[0].error or failed[0].label)

    async def _switch_map(job: Job, map_id: str) -> None:
        """Switch to the card's map, the Maps page's vanilla switch. Before the
        mods and the preset, which both write into the active mission."""
        from app.maps.map_ops import get_active_map_name, vanilla_mission

        config = state.config
        if await asyncio.to_thread(get_active_map_name, config) == map_id:
            job.log_line(f"The server runs {map_id} already.")
            return
        template = await asyncio.to_thread(vanilla_mission, config, map_id)
        if template is None:
            raise RuntimeError(f"The server files hold no mission for {map_id}.")
        await _switch_vanilla_map(job, config, map_id, template)
        await state.hub.broadcast({"type": "settings_changed"})
        await state.hub.broadcast({"type": "configs_changed"})
        await reload_map_editor(state)

    async def _write_server(values: dict) -> None:
        from app.web.editors.server_forms import save_serverdz

        await asyncio.to_thread(save_serverdz, state.config, values)
        await state.hub.broadcast({"type": "configs_changed"})

    async def _install_mods(job: Job, mods: ModSet) -> dict:
        result = await run_mod_install(
            job, state,
            requested_ids=list(mods.mods),
            id_names={wid: recommended_mods.get_mod(wid).name for wid in mods.mods},
            skip_installed=True,
        )
        if result["skipped"]:
            raise RuntimeError("These mods did not install: " + ", ".join(result["skipped"]))
        return result

    async def _apply_preset() -> list[str]:
        modified = await asyncio.to_thread(apply_preset_to_mission, state.config, body.preset)
        await state.hub.broadcast({"type": "configs_changed"})
        return modified

    async def _job_coro(job: Job) -> dict:
        before = await asyncio.to_thread(post_update.snapshot, state.config)
        out: dict = {"mods": None, "modified": [], "boot": None}
        await _part(job, "Server install", _install_server(job))
        if body.install_only:
            return out  # no done marker: the preset and the mods are still to come
        if body.server is not None:
            await _part(job, "Map", _switch_map(job, body.server.map))
        if chosen is not None:
            out["mods"] = await _part(job, "Mods", _install_mods(job, chosen))
        out["modified"] = await _part(job, "Difficulty preset", _apply_preset())
        if body.server is not None:
            # After the preset: every built-in preset sets maxPlayers, and the
            # player count the admin typed must win.
            await _part(job, "Server name and passwords", _write_server(server_values(state.config, body.server)))
        if body.start:
            config = state.config
            if await asyncio.to_thread(service_control.is_server_running, config):
                job.log_line("The server is running already.")
            else:
                out["boot"] = await _part(
                    job, "Start and boot check",
                    post_update.start_and_check(config, before, emit=job.log_line_threadsafe),
                    failed=_boot_failure,
                )
                await state.hub.broadcast({"type": "update_triage_changed"})
        # A failed boot still ends the setup: the cause is in the mods, not in a missing step.
        await asyncio.to_thread(atomic_write_text, state.config.paths.quick_setup_done, "")
        # The setup ran without them, so the way back is the Mods page from here.
        await asyncio.to_thread(state.config.paths.quick_setup_mods_off.unlink, missing_ok=True)
        return out

    try:
        if body.install_only:
            # An update-kind job, so the page never reads it as a finished setup.
            job = state.jobs.start("Install DayZ server", "update", _job_coro)
        else:
            job = state.jobs.start("Quick setup", "quick_setup", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}
