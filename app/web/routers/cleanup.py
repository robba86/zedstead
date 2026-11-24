"""GET /api/cleanup/overview, POST /api/cleanup/{logs,xml-backups,workshop,
storage,mission-reset}, POST /api/maintenance/{reinstall-server,reinstall-steamcmd}."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.ops import cleanup_ops
from app.server import service_control
from app.web.deps import get_state, require_session
from app.web.jobs import Job, JobConflictError, JobRunner, conflict_response, strip_markup
from app.web.schemas import (
    CleanupMissionResetRequest,
    CleanupModDataRequest,
    CleanupStorageRequest,
    CleanupWorkshopRequest,
    ConfirmRequest,
)
from app.web.state import WebState
from app.modmgr import modlist

router = APIRouter(prefix="/api", tags=["cleanup"], dependencies=[Depends(require_session)])


async def _refuse_while_running(state: WebState) -> None:
    """409 for a change to files the running server holds."""
    if await asyncio.to_thread(service_control.is_server_running, state.config):
        raise HTTPException(status_code=409, detail="Server is running. Stop it first.")


def _overview_sync(config) -> dict:
    """Everything the Cleanup page lists, read in one pass for its first paint."""
    from app.traderplus import trader_common

    storage = cleanup_ops.find_storage(config)
    log_files = cleanup_ops.find_log_files(config)
    workshop_mods = cleanup_ops.list_workshop_mods(config)
    xml_backups = cleanup_ops.find_xml_backups(config)
    resettable = cleanup_ops.list_resettable_missions(config)
    leftover_mod_data = cleanup_ops.find_leftover_mod_data(config)

    return {
        "leftover_mod_data": [
            {
                "mod": d.mod,
                "name": d.name,
                "path": str(d.path),
                "size": d.size,
                "files": d.files,
            }
            for d in leftover_mod_data
        ],
        "traderplus_reset": trader_common.reset_sources(config),
        "storage": {"mission": storage.mission, "path": str(storage.path)} if storage else None,
        "storage_name": cleanup_ops.storage_name(config),
        "log_files": (
            {
                "files": [str(f) for f in log_files.files],
                "total_size": log_files.total_size,
                "profiles_dir": str(log_files.profiles_dir),
            }
            if log_files
            else None
        ),
        "workshop_mods": (
            [
                {"path": str(m.path), "workshop_id": m.workshop_id, "mod_name": m.mod_name}
                for m in workshop_mods
            ]
            if workshop_mods is not None
            else None
        ),
        "xml_backups": (
            {
                "files": [str(f) for f in xml_backups.files],
                "total_size": xml_backups.total_size,
                "mission": xml_backups.mission,
                "mission_dir": str(xml_backups.mission_dir),
            }
            if xml_backups
            else None
        ),
        "resettable_missions": [
            {
                "template": m.template,
                "path": str(m.path),
                "map_name": m.map_name,
                "source_type": m.source_type,
                "has_storage": m.has_storage,
            }
            for m in resettable
        ],
    }


@router.get("/cleanup/overview")
async def cleanup_overview(state: WebState = Depends(get_state)) -> dict:
    """The Cleanup page's overview (``_overview_sync``)."""
    return await asyncio.to_thread(_overview_sync, state.config)


@router.post("/cleanup/logs")
async def cleanup_logs(body: ConfirmRequest, state: WebState = Depends(get_state)) -> dict:
    if not body.confirm:
        raise HTTPException(status_code=422, detail="Pass confirm: true")

    config = state.config

    def _do() -> tuple[int, int]:
        from app.server.service_control import is_server_process_alive

        info = cleanup_ops.find_log_files(config)
        if not info:
            return 0, 0
        return cleanup_ops.clear_log_files(info.files, running=is_server_process_alive(config))

    deleted, kept = await asyncio.to_thread(_do)
    return {"deleted": deleted, "kept": kept}


@router.post("/cleanup/xml-backups")
async def cleanup_xml_backups(body: ConfirmRequest, state: WebState = Depends(get_state)) -> dict:
    if not body.confirm:
        raise HTTPException(status_code=422, detail="Pass confirm: true")

    config = state.config

    def _do() -> int:
        info = cleanup_ops.find_xml_backups(config)
        if not info:
            return 0
        return cleanup_ops.delete_backup_files(info.files)

    deleted = await asyncio.to_thread(_do)
    return {"deleted": deleted}


@router.post("/cleanup/mod-data")
async def cleanup_mod_data(body: CleanupModDataRequest, state: WebState = Depends(get_state)) -> dict:
    """Delete `<profiles>/<Name>/` dirs left behind by uninstalled mods.

    Uninstall keeps them on purpose (they hold live server data), so this is
    the only place that removes them. `remove_mod_data_dirs` re-derives the
    list, so a name that isn't a current leftover is ignored rather than
    joined onto the profiles path.
    """
    if not body.confirm:
        raise HTTPException(status_code=422, detail="Pass confirm: true")

    config = state.config

    def _do() -> list[str]:
        names = body.names
        if names is None:
            names = [d.name for d in cleanup_ops.find_leftover_mod_data(config)]
        return cleanup_ops.remove_mod_data_dirs(config, names)

    removed = await asyncio.to_thread(_do)
    return {"removed": removed}


@router.post("/cleanup/repair-workshop")
async def cleanup_repair_workshop(state: WebState = Depends(get_state)) -> dict:
    """Drop workshop-manifest entries whose mod folder is gone.

    A stale entry makes SteamCMD abort every later workshop download with
    ``Missing game files`` while still exiting 0, which surfaces as
    ``ERROR! Download item <id> failed (Failure).``
    """
    removed = await asyncio.to_thread(cleanup_ops.repair_workshop_manifest, state.config)
    return {"removed": removed}


@router.post("/cleanup/workshop")
async def cleanup_workshop(body: CleanupWorkshopRequest, state: WebState = Depends(get_state)):
    if not body.confirm:
        raise HTTPException(status_code=422, detail="Pass confirm: true")

    config = state.config

    async def _job_coro(job: Job) -> dict:
        def _do() -> int:
            mods = cleanup_ops.list_workshop_mods(config) or []
            if body.paths is None:
                to_remove = [m.path for m in mods]
            else:
                wanted = set(body.paths)
                to_remove = [m.path for m in mods if str(m.path) in wanted]
            return cleanup_ops.remove_workshop_mods(
                config, to_remove, log=job.log_line_threadsafe
            )

        removed = await asyncio.to_thread(_do)
        return {"removed": removed}

    try:
        job = state.jobs.start("Remove workshop mods", "cleanup_workshop", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}


@router.post("/cleanup/storage")
async def cleanup_storage(body: CleanupStorageRequest, state: WebState = Depends(get_state)):
    if not body.confirm:
        raise HTTPException(status_code=422, detail="Pass confirm: true")
    await _refuse_while_running(state)

    config = state.config

    async def _job_coro(job: Job) -> dict:
        from app.ops.backup_ops import BackupContents, create_backup

        def _do() -> dict:
            info = cleanup_ops.find_storage(config)
            if not info:
                raise RuntimeError(
                    f"Could not determine current mission or no {cleanup_ops.storage_name(config)} found."
                )

            backup_path = None
            if body.backup_before:
                backup_path = create_backup(
                    config,
                    BackupContents(players=True, vehicles=True, world=True),
                    note="pre-wipe auto-backup",
                    log=job.log_line_threadsafe,
                )
            shutil.rmtree(info.path)
            return {"mission": info.mission, "backup": str(backup_path) if backup_path else None}

        result = await asyncio.to_thread(_do)

        try:
            from app.monitoring.discord_hooks import notify_wipe

            await asyncio.to_thread(notify_wipe, config, mission=result["mission"])
        except Exception:
            pass

        await state.hub.broadcast({"type": "backups_changed"})
        return result

    try:
        job = state.jobs.start("Wipe server storage", "cleanup_storage", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}


@router.post("/cleanup/mission-reset")
async def cleanup_mission_reset(body: CleanupMissionResetRequest, state: WebState = Depends(get_state)):
    if not body.confirm:
        raise HTTPException(status_code=422, detail="Pass confirm: true")
    await _refuse_while_running(state)

    config = state.config
    # A vanilla mission is restored by SteamCMD, which refuses to write the other
    # branch's files into this server (plans/66). Say so now, not as a failed job.
    from app.shared.steam import branch_mismatch

    missions = await asyncio.to_thread(cleanup_ops.list_resettable_missions, config)
    if any(m.template == body.mission and m.source_type != "zip" for m in missions):
        mismatch = await asyncio.to_thread(branch_mismatch, config)
        if mismatch:
            raise HTTPException(status_code=409, detail=mismatch)

    async def _job_coro(job: Job) -> dict:
        missions = await asyncio.to_thread(cleanup_ops.list_resettable_missions, config)
        mission = next((m for m in missions if m.template == body.mission), None)
        if mission is None:
            raise RuntimeError(f"No resettable mission named {body.mission!r}")

        if mission.source_type == "zip":
            count = await asyncio.to_thread(
                cleanup_ops.reset_mission_zip,
                mission.zip_url,
                mission.mission_path,
                mission.path,
                body.preserve_storage,
                job.log_line_threadsafe,
            )
            await asyncio.to_thread(
                cleanup_ops.remerge_reset_mission, config, mission.template, job.log_line_threadsafe,
            )
        else:
            from app.shared.steam import build_server_update_cmd, runner_no_subscription_error

            from app.ops import mission_stock

            # Built before the clear, so a refused build leaves the mission as it was.
            cmd = build_server_update_cmd(config)
            await asyncio.to_thread(cleanup_ops.clear_mission_dir, mission.path, body.preserve_storage)
            runner = JobRunner(job)
            # The validate resets the other stock missions too; keep their edits (plans/67).
            returncode = await mission_stock.run_download(
                runner, config, cmd, resetting=mission.template, log=job.log_line_threadsafe,
            )
            if returncode != 0:
                raise RuntimeError(
                    runner_no_subscription_error(runner, config)
                    or f"SteamCMD validate exited with code {returncode}."
                )
            count = 0

        await state.hub.broadcast({"type": "configs_changed"})
        return {"mission": mission.template, "files_extracted": count}

    try:
        job = state.jobs.start("Reset mission", "cleanup_mission_reset", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}


# ---------------------------------------------------------------------------
# Maintenance — reinstall server / SteamCMD
# ---------------------------------------------------------------------------


@router.post("/maintenance/reinstall-server")
async def reinstall_server(body: ConfirmRequest, state: WebState = Depends(get_state)):
    if not body.confirm:
        raise HTTPException(status_code=422, detail="Pass confirm: true")
    await _refuse_while_running(state)

    config = state.config
    refusal = await asyncio.to_thread(cleanup_ops.reinstall_refusal, config)
    if refusal:
        raise HTTPException(status_code=409, detail=refusal)

    async def _job_coro(job: Job) -> dict:
        from app.shared.container import sync_battleye_cfg, sync_server_cfg
        from app.shared.steam import (
            build_server_update_cmd,
            runner_no_subscription_error,
            steamcmd_username,
        )
        from app.server.service_ops import generate_service_files_async

        from app.ops import mission_stock

        server_dir = config.server_dir_path
        # Asked before the delete: a username SteamCMD would refuse leaves no server.
        steamcmd_username(config)
        await asyncio.to_thread(cleanup_ops.rmtree_mount_safe, server_dir)

        cmd = build_server_update_cmd(config)
        runner = JobRunner(job)
        # Nothing is left to keep; this records the fresh stock files (plans/67).
        returncode = await mission_stock.run_download(runner, config, cmd, log=job.log_line_threadsafe)
        if returncode != 0:
            raise RuntimeError(
                runner_no_subscription_error(runner, config)
                or f"SteamCMD exited with code {returncode} while reinstalling the server."
            )

        # A reinstalled server has no mods, so the list starts empty again.
        await asyncio.to_thread(modlist.clear, config)
        notes: list[str] = []
        await asyncio.to_thread(sync_battleye_cfg, config, notes)
        await asyncio.to_thread(sync_server_cfg, config, notes)
        for note in notes:
            job.log_line(f"Warning: {note}")
        ok = await generate_service_files_async(JobRunner(job), config)
        if not ok:
            job.log_line("Warning: service file install failed — run `zsm service install`.")

        await state.hub.broadcast({"type": "service_changed"})
        return {"ok": True}

    try:
        job = state.jobs.start("Reinstall server", "reinstall_server", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}


@router.post("/maintenance/reinstall-steamcmd")
async def reinstall_steamcmd(body: ConfirmRequest, state: WebState = Depends(get_state)):
    from app.shared.os_packages import missing_packages, steamcmd_packages
    from app.shared.platform import IS_WINDOWS, sudo_available_noninteractive

    if not body.confirm:
        raise HTTPException(status_code=422, detail="Pass confirm: true")
    # Root is only needed for the apt step, and since plan 26 the common case —
    # every dependency already present — runs no apt command at all. Gating on
    # sudo up front made the route refuse the case it could serve. Ask what is
    # missing first; the 409 stays for the case that genuinely needs root.
    if not IS_WINDOWS:
        missing = await asyncio.to_thread(missing_packages, steamcmd_packages())
        if missing and not await asyncio.to_thread(sudo_available_noninteractive):
            raise HTTPException(
                status_code=409,
                detail=f"Installing {' '.join(missing)} needs root, and sudo is not "
                "available non-interactively. Run `zsm doctor` fixes from a "
                "terminal, or configure passwordless sudo.",
            )
    await _refuse_while_running(state)

    config = state.config
    if not config.steamcmd_dir:
        raise HTTPException(status_code=400, detail="steamcmd_dir is not configured.")

    async def _job_coro(job: Job) -> dict:
        from app.shared.steam import install_steamcmd

        # Not through stream_subprocess, but it replaces the directory that
        # holds the cached login, so the login is re-measured afterwards.
        job.ran_steamcmd = True

        def _log(line: str) -> None:
            job.log_line_threadsafe(strip_markup(line))

        steamcmd_dir = Path(config.steamcmd_dir)
        # Move the old install aside instead of deleting it outright, so a
        # failed download/extract doesn't leave the box with no SteamCMD.
        stash = steamcmd_dir.with_name(steamcmd_dir.name + ".old")
        stashed = False
        if steamcmd_dir.is_dir():
            job.log_line(f"Moving existing SteamCMD aside: {stash}")
            if stash.exists():
                await asyncio.to_thread(cleanup_ops.rmtree_mount_safe, stash)
            try:
                await asyncio.to_thread(shutil.move, str(steamcmd_dir), str(stash))
                stashed = True
            except OSError:
                # Bind mount or cross-device: fall back to emptying in place.
                job.log_line("Could not move it (mount point?) — emptying in place.")
                await asyncio.to_thread(cleanup_ops.rmtree_mount_safe, steamcmd_dir)

        try:
            ok = await asyncio.to_thread(install_steamcmd, steamcmd_dir, log=_log)
        except Exception as e:
            ok = False
            job.log_line(f"SteamCMD install raised: {e}")

        if not ok:
            if stashed:
                job.log_line("Install failed — restoring the previous SteamCMD.")
                if steamcmd_dir.is_dir():
                    await asyncio.to_thread(cleanup_ops.rmtree_mount_safe, steamcmd_dir)
                await asyncio.to_thread(shutil.move, str(stash), str(steamcmd_dir))
            raise RuntimeError("SteamCMD install failed — see the log above.")

        if stashed:
            await asyncio.to_thread(cleanup_ops.rmtree_mount_safe, stash)
        return {"ok": True}

    try:
        job = state.jobs.start("Reinstall SteamCMD", "reinstall_steamcmd", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}


# ---------------------------------------------------------------------------
# Migrate players.db between missions
# ---------------------------------------------------------------------------


@router.get("/cleanup/missions")
async def list_missions(state: WebState = Depends(get_state)) -> dict:
    """Available mpmissions with players.db presence (for migrate-players)."""
    from app.parsers.servercfg import get_available_missions

    config = state.config

    def _load() -> list[dict]:
        out = []
        for mission in get_available_missions(config.server_dir_path):
            db = cleanup_ops.get_players_db_path(config, mission)
            out.append(
                {
                    "mission": mission,
                    "has_players_db": db.is_file(),
                    "players_db_size": db.stat().st_size if db.is_file() else 0,
                }
            )
        return out

    return {"missions": await asyncio.to_thread(_load)}


@router.post("/cleanup/migrate-players")
async def migrate_players(body: dict, state: WebState = Depends(get_state)) -> dict:
    """Copy players.db from one mission's storage folder to another's."""
    from app.parsers.servercfg import get_available_missions

    source = str(body.get("source", ""))
    target = str(body.get("target", ""))
    backup_target = bool(body.get("backup_target", True))
    if not source or not target or source == target:
        raise HTTPException(status_code=422, detail="Distinct source and target missions are required.")

    # The running server keeps players.db open and writes it back over the copy.
    await _refuse_while_running(state)
    config = state.config

    def _run() -> dict:
        # Validate against the real mission list — never join raw input onto paths.
        available = set(get_available_missions(config.server_dir_path))
        if source not in available or target not in available:
            raise RuntimeError("Unknown source or target mission.")

        source_db = cleanup_ops.get_players_db_path(config, source)
        if not source_db.is_file():
            raise RuntimeError(f"No players.db found in {source}/{source_db.parent.name}/")

        target_db = cleanup_ops.get_players_db_path(config, target)
        backed_up = None
        if target_db.is_file() and backup_target:
            backed_up = cleanup_ops.backup_players_db(target_db).name
        cleanup_ops.copy_players_db(source_db, target_db)
        return {"source": source, "target": target, "backed_up": backed_up}

    try:
        result = await asyncio.to_thread(_run)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


# ---------------------------------------------------------------------------
# Reset TraderPlus configs from the mod's defaults
# ---------------------------------------------------------------------------


@router.post("/cleanup/reset-traderplus")
async def reset_traderplus(body: dict, state: WebState = Depends(get_state)) -> dict:
    """Reset TraderPlus's JSON configs or its mission XMLs from the mod's defaults (400 when the mod ships none)."""
    from app.traderplus import trader_common

    kind = str(body.get("kind", ""))
    if kind not in ("json", "xml"):
        raise HTTPException(status_code=422, detail='kind must be "json" or "xml"')

    config = state.config
    fn = (
        trader_common.reset_traderplus_json
        if kind == "json"
        else trader_common.reset_traderplus_xml
    )
    ok, messages = await asyncio.to_thread(fn, config)
    if not ok:
        raise HTTPException(status_code=400, detail="; ".join(messages) or "Reset failed.")
    await state.hub.broadcast({"type": "configs_changed"})
    return {"ok": True, "messages": messages}
