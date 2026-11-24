"""GET/POST/DELETE /api/backups, GET /api/backups/download,
POST /api/backups/restore, POST /api/bundle/export, POST /api/bundle/import,
GET /api/bundle/download."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from app.ops import backup_ops, bundle_ops, offsite_ops
from app.parsers.battleye_cfg import BeserverCfgInUse
from app.server import service_control
from app.web.deps import get_state, require_session
from app.web.downloads import file_attachment
from app.web.jobs import Job, JobConflictError, conflict_response
from app.web.schemas import (
    BackupCreateRequest,
    BackupDeleteRequest,
    BackupRestoreRequest,
    BundleDeleteRequest,
    BundleImportRequest,
)
from app.shared.json_utils import atomic_write_bytes
from app.shared.zip_utils import is_bare_name
from app.web.state import WebState
from app.modmgr import modlist

router = APIRouter(prefix="/api", tags=["backups"], dependencies=[Depends(require_session)])


def _size_on_disk(zip_path) -> int | None:
    """The zip's own size: what it takes on disk and what Download sends.

    The metadata's ``total_size`` is the files before compression, and the
    Size column showed it (3835.5 MB for a 1.7 GB zip). None for
    a zip gone between the listing and the stat.
    """
    try:
        return zip_path.stat().st_size
    except OSError:
        return None


def _quick_dict(b: backup_ops.BackupInfo) -> dict:
    m = b.metadata
    return {
        "type": "quick",
        "name": b.zip_path.stem,
        "timestamp": m.timestamp,
        "mission": m.mission,
        "map_name": m.map_name,
        "contents": m.contents,
        "total_size": m.total_size,
        "size": _size_on_disk(b.zip_path),
        "note": m.note,
    }


def _full_dict(b: backup_ops.FullBackupInfo) -> dict:
    m = b.metadata
    return {
        "type": "full",
        "name": b.zip_path.stem,
        "timestamp": m.timestamp,
        "include_mods": m.include_mods,
        "include_logs": m.include_logs,
        "file_count": m.file_count,
        "total_size": m.total_size,
        "compressed_size": m.compressed_size,
        "size": _size_on_disk(b.zip_path),
        "note": m.note,
    }


def _find_backup(config, backup_type: str, name: str):
    if backup_type == "quick":
        return next((b for b in backup_ops.list_backups(config) if b.zip_path.stem == name), None)
    if backup_type == "full":
        return next((b for b in backup_ops.list_full_backups(config) if b.zip_path.stem == name), None)
    raise ValueError(f"Unknown backup type: {backup_type!r}")


@router.get("/backups")
async def list_backups(state: WebState = Depends(get_state)) -> dict:
    config = state.config

    def _load() -> tuple[list, list, str | None]:
        return (
            backup_ops.list_backups(config),
            backup_ops.list_full_backups(config),
            backup_ops.quick_backup_problem(config),
        )

    quick, full, quick_problem = await asyncio.to_thread(_load)
    return {
        # Why Create is disabled on the Quick tab, or null.
        "quick_problem": quick_problem,
        # `dir` for the same reason /api/bundle carries it: the download button
        # gives you the zip, and the .meta.json sidecar it leaves behind is only
        # findable if the page says where it is.
        "dir": str(backup_ops.get_backup_dir(config)),
        "quick": [_quick_dict(b) for b in quick],
        "full": [_full_dict(b) for b in full],
    }


@router.get("/backups/download")
async def download_backup(
    backup_type: str = Query(alias="type"),
    name: str = Query(...),
    state: WebState = Depends(get_state),
):
    """Stream one backup zip to the browser.

    Only the zip — the ``.meta.json`` sidecar beside it is what makes zsm
    *list* a backup (``list_backups`` globs the sidecars), so restoring this
    file on another machine means copying that one over as well. The page
    says so under the table; ``restore_backup`` itself reads nothing but the
    zip.
    """
    config = state.config
    if backup_type not in ("quick", "full"):
        raise HTTPException(status_code=422, detail=f"Unknown backup type: {backup_type!r}")
    backup = await asyncio.to_thread(_find_backup, config, backup_type, name)
    if backup is None:
        raise HTTPException(status_code=404, detail=f"No {backup_type} backup named {name!r}")
    return file_attachment(backup.zip_path)


@router.post("/backups")
async def create_backup(body: BackupCreateRequest, state: WebState = Depends(get_state)):
    config = state.config
    if body.type not in ("quick", "full"):
        raise HTTPException(status_code=422, detail=f"Unknown backup type: {body.type!r}")
    if body.type == "quick":
        problem = await asyncio.to_thread(backup_ops.quick_backup_problem, config)
        if problem:
            raise HTTPException(status_code=409, detail=problem)
    elif not config.server_dir_path.is_dir():
        raise HTTPException(
            status_code=409,
            detail=f"There is nothing to back up: the server directory {config.server_dir_path} "
            "does not exist yet.",
        )

    async def _job_coro(job: Job) -> dict:
        if body.type == "quick":
            contents = backup_ops.BackupContents(
                players=body.players,
                vehicles=body.vehicles,
                world=body.world,
                mod_data=body.mod_data,
            )
            path = await asyncio.to_thread(
                backup_ops.create_backup, config, contents, body.note, job.log_line_threadsafe
            )
        else:
            options = backup_ops.FullBackupOptions(
                include_mods=body.include_mods, include_logs=body.include_logs, note=body.note
            )
            path = await asyncio.to_thread(
                backup_ops.create_full_backup, config, options, job.log_line_threadsafe
            )
        if path is None:
            # Both writers log why and return None. The job used to end as a
            # green Success, toast "finished" and list no backup:
            # a job that wrote no backup is a failed backup.
            raise RuntimeError("No backup was written; the log above says why.")
        # Offsite copy, when one is configured. `auto_push` never raises: the
        # backup is already on disk, so a remote that is down must not report
        # the job as failed — the error rides in the result instead, and the
        # page warns on it.
        offsite = await asyncio.to_thread(
            offsite_ops.auto_push,
            config,
            path,
            log=job.log_line_threadsafe,
            should_cancel=job.cancel_event.is_set,
        )
        await state.hub.broadcast({"type": "backups_changed"})
        return {
            "path": str(path) if path else None,
            "offsite": offsite["uploaded"],
            "offsite_error": offsite["error"],
        }

    try:
        job = state.jobs.start(f"Create {body.type} backup", "backup_create", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}


async def _redownload_mods(job: Job, config) -> None:
    """Re-download every mod in the mod list and copy them to the server
    (after a full restore, when the admin asks for it)."""
    from app.ops.fileops import copy_all_mods_to_server, copy_bikey_files
    from app.shared.steam import download_mod_async, resolve_mod_id
    from app.web.jobs import JobRunner

    runner = JobRunner(job)
    mod_list, cached_ids = await asyncio.to_thread(modlist.installed_and_ids, config)
    if not mod_list:
        job.log_line("No mods in the mod list.")
        return

    cached_names = set(cached_ids.keys())
    total = len(mod_list)
    job.log_line(f"Downloading {total} mod(s)...")

    failed = 0
    for i, mod_name in enumerate(mod_list, 1):
        if job.cancel_event.is_set():
            return
        mod_id = cached_ids.get(mod_name)
        if not mod_id:
            mod_id = await asyncio.to_thread(resolve_mod_id, mod_name, cached_names)
            if mod_id:
                await asyncio.to_thread(modlist.remember_id, config, mod_name, mod_id
                )
                cached_names.add(mod_name)
        if not mod_id:
            job.log_line(f"  Skipped {mod_name} — no workshop ID")
            continue

        job.log_line(f"  [{i}/{total}] Downloading {mod_name}...")
        if not await download_mod_async(runner, config, mod_id, mod_name):
            job.log_line(f"  [{i}/{total}] {mod_name} — failed")
            failed += 1

    job.log_line("Copying mods to server...")
    await asyncio.to_thread(copy_all_mods_to_server, config)
    await asyncio.to_thread(copy_bikey_files, config)
    if failed:
        job.log_line(f"Mods updated ({failed} failed).")
    else:
        job.log_line(f"All {total} mod(s) downloaded and copied.")


# Kept at their current value by every restore, lock or no lock, with the
# reason the log gives. A backup's web_allowed_hosts lists the names the panel
# answered to then, and the browser running the restore may reach it by
# another name now.
RESTORE_KEEPS = {"web_allowed_hosts": "the host names this app answers to"}


async def _restore_app_config(state: WebState, zip_path: Path, job: Job) -> None:
    """Restore config.json from a full backup, honouring the path lock (plans/58 A6).

    Unlocked, and with nothing in `RESTORE_KEEPS` to keep, this is
    byte-for-byte the old behaviour: a wholesale ``extract_app_config`` write.
    Locked, a backup taken before the lock was switched on would otherwise
    lift it (and repoint every path), so the backup is applied through
    ``mutate_config`` with every locked field — and any path-bearing launch
    flag — kept at its current value. A field in `RESTORE_KEEPS` whose value
    differs takes the same path, lock or not. The restore keeps, it does not
    fail: refusing a whole restore because one field is locked would be worse
    than the leak it prevents.
    """
    from app.shared import settings_registry
    from app.shared.config import TRANSIENT_FIELDS, DayzConfig
    from app.shared.server_args import path_flags_in

    fields = DayzConfig.__dataclass_fields__
    paths_locked = state.paths_locked()
    backup_cfg = await asyncio.to_thread(backup_ops.read_app_config, zip_path)

    def _held(cfg) -> list[str]:
        # A backup without the field would restore its default.
        return [
            key for key in RESTORE_KEEPS
            if backup_cfg.get(key, fields[key].default) != getattr(cfg, key)
        ]

    if not paths_locked and not (backup_cfg and _held(state.config)):
        await asyncio.to_thread(
            backup_ops.extract_app_config, zip_path, state.config.paths.config_file,
        )
        await state.adopt_config_file()
        job.log_line("Restored app config (config.json) from backup.")
        return

    if not backup_cfg:
        job.log_line("No app config (config.json) in the backup.")
        return

    locked = settings_registry.web_locked_fields() if paths_locked else frozenset()
    kept: list[str] = []

    def _apply(cfg) -> None:
        kept.extend(f"kept {key} ({RESTORE_KEEPS[key]})" for key in _held(cfg))
        for key, value in backup_cfg.items():
            # instance_id says which file a config is written to; a restored
            # value would move this instance's settings into another's.
            if key not in fields or key in TRANSIENT_FIELDS or key in RESTORE_KEEPS:
                continue
            if key in locked:
                if getattr(cfg, key) != value:
                    kept.append(f"kept {key} (locked)")
                continue
            if (
                paths_locked and key == "server_args"
                and path_flags_in(cfg.server_args) != path_flags_in(str(value))
            ):
                kept.append("kept server_args (launch-flag paths) (locked)")
                continue
            setattr(cfg, key, value)

    await state.mutate_config(_apply)
    job.log_line("Restored app config (config.json) from backup.")
    for line in kept:
        job.log_line(line)


@router.post("/backups/restore")
async def restore_backup(body: BackupRestoreRequest, state: WebState = Depends(get_state)):
    if await asyncio.to_thread(service_control.is_server_running, state.config):
        raise HTTPException(status_code=409, detail="Server is running. Stop it first.")

    config = state.config
    if body.type not in ("quick", "full"):
        raise HTTPException(status_code=422, detail=f"Unknown backup type: {body.type!r}")

    backup = await asyncio.to_thread(_find_backup, config, body.type, body.name)
    if backup is None:
        raise HTTPException(status_code=404, detail=f"No {body.type} backup named {body.name!r}")

    async def _job_coro(job: Job) -> dict:
        if body.type == "quick":
            contents = backup_ops.BackupContents()
            count = await asyncio.to_thread(
                backup_ops.restore_backup, config, backup, contents, job.log_line_threadsafe
            )
        else:
            count = await asyncio.to_thread(
                backup_ops.restore_full_backup, config, backup, job.log_line_threadsafe
            )
            if body.restore_app_config:
                await _restore_app_config(state, backup.zip_path, job)
                await state.hub.broadcast({"type": "settings_changed"})
            if body.redownload_mods and await asyncio.to_thread(modlist.installed, config):
                await _redownload_mods(job, config)
        await state.hub.broadcast({"type": "backups_changed"})
        return {"restored_files": count}

    try:
        job = state.jobs.start(f"Restore {body.type} backup", "backup_restore", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}


@router.delete("/backups")
async def delete_backups(body: BackupDeleteRequest, state: WebState = Depends(get_state)) -> dict:
    config = state.config
    if body.type not in ("quick", "full"):
        raise HTTPException(status_code=422, detail=f"Unknown backup type: {body.type!r}")

    def _delete() -> int:
        all_backups = (
            backup_ops.list_backups(config) if body.type == "quick" else backup_ops.list_full_backups(config)
        )
        matched = [b for b in all_backups if b.zip_path.stem in body.names]
        return backup_ops.delete_backups(matched)

    count = await asyncio.to_thread(_delete)
    await state.hub.broadcast({"type": "backups_changed"})
    return {"deleted": count}


# ---------------------------------------------------------------------------
# Offsite target (TODO C1)
# ---------------------------------------------------------------------------


@router.get("/backups/remote")
async def get_backup_remote(state: WebState = Depends(get_state)) -> dict:
    """The offsite target's shape — never its credentials.

    `remote_status` reads the kind out of rclone.conf and reports whether a
    binary exists and which fields hold a value (`set_fields`, names only);
    no value in that file has a route out of the process. A save that leaves
    a field blank keeps the stored value, so nothing has to be re-entered.
    """
    return await asyncio.to_thread(offsite_ops.remote_status, state.config)


@router.post("/backups/remote")
async def set_backup_remote(body: dict, state: WebState = Depends(get_state)) -> dict:
    kind = str(body.get("kind", "")).strip()
    if kind not in offsite_ops.SUPPORTED_KINDS:
        raise HTTPException(status_code=422, detail=f"Unsupported remote kind: {kind!r}")
    fields = body.get("fields") or {}
    if not isinstance(fields, dict):
        raise HTTPException(status_code=422, detail="fields must be an object")
    clear = body.get("clear") or []
    if not isinstance(clear, list):
        raise HTTPException(status_code=422, detail="clear must be a list of field names")
    # A form that misses a required field or has a malformed one saves nothing.
    # The detail names each field, so the card can mark it.
    problems = offsite_ops.form_problems(kind, fields, clear, str(body.get("path", "")))
    if problems:
        raise HTTPException(
            status_code=422,
            detail={"message": "Fill in the marked fields.", "fields": problems},
        )

    config = state.config
    try:
        # write_remote filters fields and clear to REMOTE_FIELDS[kind], so an
        # extra key in the payload cannot become an arbitrary rclone option.
        # A blank field keeps the stored value; a different kind replaces it.
        await asyncio.to_thread(
            lambda: offsite_ops.write_remote(config, kind, fields, clear=clear)
        )
    except offsite_ops.RcloneError as e:
        raise HTTPException(status_code=400, detail=str(e))

    def _apply(cfg) -> None:
        cfg.backup_remote_kind = kind
        cfg.backup_remote_path = str(body.get("path", "")).strip()
        cfg.backup_remote_auto = bool(body.get("auto", True))

    await state.mutate_config(_apply)
    await state.hub.broadcast({"type": "backups_changed"})
    return await asyncio.to_thread(offsite_ops.remote_status, state.config)


@router.delete("/backups/remote")
async def clear_backup_remote(state: WebState = Depends(get_state)) -> dict:
    removed = await asyncio.to_thread(offsite_ops.clear_remote)

    def _apply(cfg) -> None:
        cfg.backup_remote_kind = ""

    await state.mutate_config(_apply)
    await state.hub.broadcast({"type": "backups_changed"})
    return {"removed": removed}


@router.post("/backups/remote/test")
async def test_backup_remote(state: WebState = Depends(get_state)) -> dict:
    """Probe the remote. A failure is a 200 with ``ok: false``, not an error
    status — "your password is wrong" is an answer, not a broken request.

    For SFTP the answer carries ``host_key``: on first contact its
    ``state`` is ``unknown`` and it lists the fingerprints to confirm through
    ``/backups/remote/host-key``; ``changed`` is refused, never replaced."""
    return await asyncio.to_thread(offsite_ops.probe_remote, state.config)


@router.post("/backups/remote/host-key")
async def trust_backup_remote_host_key(
    body: dict, state: WebState = Depends(get_state)
) -> dict:
    """Save the SFTP host's key once the admin has compared its fingerprint.

    ``fingerprints`` must be exactly the ones the last Test showed: the host
    is scanned again here, and a key that differs from what was confirmed is
    refused rather than saved. Then the remote is probed again."""
    fps = body.get("fingerprints")
    if not isinstance(fps, list) or not fps or not all(isinstance(f, str) for f in fps):
        raise HTTPException(status_code=422, detail="fingerprints must be a non-empty list of strings")
    try:
        await asyncio.to_thread(offsite_ops.trust_host_keys, fps)
    except offsite_ops.RcloneError as exc:
        return {"ok": False, "message": str(exc)}
    await state.hub.broadcast({"type": "backups_changed"})
    return await asyncio.to_thread(offsite_ops.probe_remote, state.config)


@router.post("/backups/push")
async def push_backup_offsite(body: dict, state: WebState = Depends(get_state)):
    """Upload an existing backup. The zip's sidecar goes with it."""
    config = state.config
    backup_type = str(body.get("type", ""))
    name = str(body.get("name", ""))
    if backup_type not in ("quick", "full"):
        raise HTTPException(status_code=422, detail=f"Unknown backup type: {backup_type!r}")
    backup = await asyncio.to_thread(_find_backup, config, backup_type, name)
    if backup is None:
        raise HTTPException(status_code=404, detail=f"No {backup_type} backup named {name!r}")
    if not await asyncio.to_thread(offsite_ops.read_remote_kind):
        raise HTTPException(status_code=409, detail="No offsite target configured.")

    async def _job_coro(job: Job) -> dict:
        destinations = await asyncio.to_thread(
            offsite_ops.push_backup,
            config,
            backup.zip_path,
            log=job.log_line_threadsafe,
            should_cancel=job.cancel_event.is_set,
        )
        return {"uploaded": destinations}

    try:
        job = state.jobs.start(f"Push {name} offsite", "backup_push", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}


# ---------------------------------------------------------------------------
# Bundles
# ---------------------------------------------------------------------------


@router.post("/bundle/export")
async def export_bundle(state: WebState = Depends(get_state)):
    config = state.config

    async def _job_coro(job: Job) -> dict:
        # bundle_ops.export_bundle, not the CLI's _bundle_export: the CLI wrapper
        # writes its account of the zip to the Rich console (the server's stdout),
        # which left the job log empty in the browser.
        path = await asyncio.to_thread(
            bundle_ops.export_bundle,
            config,
            job.log_line_threadsafe,
            job.cancel_event.is_set,
        )
        # Same event the delete route fires — every open Bundles tab re-lists.
        await state.hub.broadcast({"type": "backups_changed"})
        return {"path": str(path) if path else None}

    try:
        job = state.jobs.start("Export bundle", "bundle_export", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}


def _import_bundle_sync(config, zip_path: Path, target_mission: str | None, log) -> dict:
    import json as _json
    import zipfile as _zipfile

    if not _zipfile.is_zipfile(zip_path):
        raise ValueError(f"Not a valid zip file: {zip_path}")

    imported: list[str] = []
    skipped: list[str] = []

    from app.shared.config_versions import snapshot

    emit = log or (lambda _line: None)
    emit(f"Importing bundle: {zip_path}")

    def _write_entry(dest: Path, data: bytes, label: str, *, owner_only: bool = False) -> None:
        if dest.is_file():
            try:
                snapshot(dest, trigger="bundle_import")
            except Exception:
                pass
        atomic_write_bytes(dest, data, owner_only=owner_only)
        imported.append(label)
        emit(f"  + {label} -> {dest}")

    config_updates: dict = {}

    with _zipfile.ZipFile(zip_path, "r") as zf:
        names = bundle_ops.file_entries(zf)

        mods_data = zf.read(bundle_ops.MODS_ARC_NAME) if bundle_ops.MODS_ARC_NAME in names else None
        line = bundle_ops.import_mod_list(config, names, mods_data)
        if line:
            emit(f"  {line}")
            entry = bundle_ops.MODS_ARC_NAME if mods_data is not None else "mods.txt"
            (imported if line.startswith("+") else skipped).append(entry)

        if "serverDZ.cfg" in names:
            _write_entry(config.server_cfg_path, zf.read("serverDZ.cfg"), "serverDZ.cfg")

        if "config.json" in names:
            try:
                bundle_cfg = _json.loads(zf.read("config.json"))
            except (OSError, _json.JSONDecodeError):
                bundle_cfg = {}
            # Returned to the caller, which applies it via mutate_config —
            # writing config.json from here would bypass the config lock.
            config_updates, refused = bundle_ops.importable_config(bundle_cfg, config)
            for line in refused:
                emit(f"  - {line}")
            if config_updates:
                emit(f"  + config.json ({len(config_updates)} field(s))")

        for be_name in bundle_ops.battleye_entries(config, names):
            _write_entry(config.beserver_cfg_path, zf.read(be_name), be_name, owner_only=True)
        for be_name in bundle_ops.refused_battleye_entries(config, names):
            skipped.append(be_name)
            emit(f"  - {be_name} (not the BattlEye cfg)")

        for arc_name in bundle_ops.profiles_entries(config, names):
            try:
                dest = bundle_ops.profiles_entry_dest(config, arc_name)
            except ValueError:
                skipped.append(arc_name)
                emit(f"  - {arc_name} (unsafe path)")
                continue
            atomic_write_bytes(dest, zf.read(arc_name))
            imported.append(arc_name)
            emit(f"  + {arc_name}")

        mission_files = [n for n in names if n.startswith("mpmissions/")]
        if mission_files:
            source_mission = bundle_ops.detect_source_mission(names)
            dest_mission = target_mission or source_mission
            if dest_mission and source_mission and dest_mission != source_mission:
                emit(f"Mission {source_mission} -> {dest_mission}")
            for arc_name in mission_files:
                dest_name = bundle_ops.remap_mission_entry(arc_name, source_mission, dest_mission)
                try:
                    dest_name, dest = bundle_ops.mission_entry_dest(config.server_dir_path, dest_name)
                except ValueError:
                    skipped.append(dest_name)
                    emit(f"  - {dest_name} (unsafe path)")
                    continue
                atomic_write_bytes(dest, zf.read(arc_name))
                imported.append(dest_name)
                emit(f"  + {dest_name}")

    emit(f"Imported {len(imported)} entry(ies), skipped {len(skipped)}.")
    return {"imported": imported, "skipped": skipped, "config_updates": config_updates}


def _refuse_battleye_while_running(config, zip_path: Path) -> None:
    """Refused before the job starts, so nothing in the bundle is written. A
    file that is not a zip is left to the job, which reports it."""
    import zipfile as _zipfile

    if _zipfile.is_zipfile(zip_path):
        with _zipfile.ZipFile(zip_path) as zf:
            bundle_ops.refuse_battleye_import_while_running(config, bundle_ops.file_entries(zf))


@router.post("/bundle/import")
async def import_bundle(body: BundleImportRequest, state: WebState = Depends(get_state)):
    config = state.config
    bundle_dir = bundle_ops.get_bundle_dir(config).resolve()
    zip_path = (bundle_dir / body.filename).resolve()
    if zip_path != bundle_dir and bundle_dir not in zip_path.parents:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not zip_path.is_file():
        raise HTTPException(status_code=404, detail=f"Bundle not found: {body.filename}")
    if body.target_mission and not is_bare_name(body.target_mission):
        raise HTTPException(status_code=400, detail="Invalid target mission")
    try:
        await asyncio.to_thread(_refuse_battleye_while_running, config, zip_path)
    except BeserverCfgInUse as e:
        raise HTTPException(status_code=409, detail=str(e))

    async def _job_coro(job: Job) -> dict:
        result = await asyncio.to_thread(
            _import_bundle_sync, config, zip_path, body.target_mission, job.log_line_threadsafe
        )
        config_updates = result.pop("config_updates", {})
        if config_updates and state.paths_locked():
            # Path lock on (plans/58 A7): keep locked fields at their current
            # value rather than let an imported bundle repoint them, and drop a
            # server_args that would change a path-bearing launch flag. Dropping
            # here (not raising in mutate_config) keeps the rest of the import.
            from app.shared import settings_registry
            from app.shared.server_args import path_flags_in

            locked = settings_registry.web_locked_fields()
            for k in [k for k in config_updates if k in locked]:
                del config_updates[k]
                job.log_line(f"kept {k} (locked)")
            if "server_args" in config_updates and path_flags_in(
                config.server_args
            ) != path_flags_in(str(config_updates["server_args"])):
                del config_updates["server_args"]
                job.log_line("kept server_args (launch-flag paths) (locked)")
        if config_updates:

            def _apply(cfg) -> None:
                for k, v in config_updates.items():
                    setattr(cfg, k, v)

            await state.mutate_config(_apply)
            result["imported"].append("config.json")
        await state.hub.broadcast({"type": "settings_changed"})
        return result

    try:
        job = state.jobs.start("Import bundle", "bundle_import", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}


@router.get("/bundle")
async def list_bundles(state: WebState = Depends(get_state)) -> dict:
    """Saved bundles + the directory they live in + the missions an import can
    target (the Bundles tab picks one instead of asking for a typed name)."""
    from app.parsers.servercfg import get_available_missions

    config = state.config

    def _load() -> dict:
        bundle_dir = bundle_ops.get_bundle_dir(config)
        bundles = []
        for path in bundle_ops.list_available_bundles(config):
            stat = path.stat()
            bundles.append({"name": path.name, "size": stat.st_size, "modified": stat.st_mtime})
        try:
            missions = get_available_missions(config.server_dir_path)
        except OSError:
            missions = []
        return {
            "dir": str(bundle_dir),
            "bundles": bundles,
            "missions": missions,
            # Rides along here rather than on its own endpoint, for the same
            # reason `enabled` travels with the whitelist: a page that has to
            # remember to ask separately is one that eventually won't, and
            # this is the line to read before sending a bundle to anyone.
            "sensitive_note": bundle_ops.sensitive_warning(config),
        }

    return await asyncio.to_thread(_load)


@router.get("/bundle/download")
async def download_bundle(name: str = Query(...), state: WebState = Depends(get_state)):
    """Stream one bundle zip to the browser.

    A bundle is self-contained (`collect_export_manifest` puts mods.json,
    serverDZ.cfg, config.json and the mission files in the one file), so
    unlike a backup this needs no sidecar to be usable elsewhere.
    """
    config = state.config

    def _find() -> Path | None:
        return next(
            (b for b in bundle_ops.list_available_bundles(config) if b.name == name), None
        )

    path = await asyncio.to_thread(_find)
    if path is None:
        raise HTTPException(status_code=404, detail=f"Bundle not found: {name}")
    return file_attachment(path)


@router.delete("/bundle")
async def delete_bundles(body: BundleDeleteRequest, state: WebState = Depends(get_state)) -> dict:
    """Delete bundle zips by name. Names are matched against the actual
    bundle listing — never joined onto the directory directly. A list, never
    a string, whose characters would each be matched as a name."""
    names = set(body.names)
    if not names:
        raise HTTPException(status_code=422, detail="names is required")
    config = state.config

    def _delete() -> int:
        deleted = 0
        for bundle in bundle_ops.list_available_bundles(config):
            if bundle.name in names:
                bundle.unlink(missing_ok=True)
                deleted += 1
        return deleted

    deleted = await asyncio.to_thread(_delete)
    await state.hub.broadcast({"type": "backups_changed"})
    return {"deleted": deleted}
