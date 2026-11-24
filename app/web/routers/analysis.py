"""GET /api/analysis/{economy,collisions,logs}, GET/POST /api/changes*,
GET /api/versions + POST /api/versions/restore, GET /api/metrics/*, GET /api/a2s."""

from __future__ import annotations

import asyncio
import dataclasses

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app.parsers.battleye_cfg import BeserverCfgInUse, live_file, refuse_while_running
from app.web import redact
from app.web.deps import get_state, require_session
from app.web.downloads import file_attachment
from app.web.editors._help import strip_markup
from app.web.editors._paths import mission_dir
from app.web.state import WebState

router = APIRouter(prefix="/api", tags=["analysis"], dependencies=[Depends(require_session)])


def _require_mission_dir(config):
    m_dir = mission_dir(config)
    if m_dir is None or not m_dir.is_dir():
        raise HTTPException(status_code=409, detail="No active mission found — set a map first.")
    return m_dir


def _load_all_types(m_dir, where: dict | None = None) -> list:
    """Every <type> in the mission's types files. *where*, when given, gets
    each classname (lower case) mapped to the file that holds it first, so a
    report row can link to the Types page on that file."""
    from app.xml.types_xml import parse_types_file, scan_types_files

    entries = []
    for path in scan_types_files(m_dir):
        found = parse_types_file(path).entries
        entries.extend(found)
        if where is not None:
            for entry in found:
                where.setdefault(entry.name.lower(), str(path))
    return entries


@router.get("/analysis/economy")
async def economy_analysis(state: WebState = Depends(get_state)) -> dict:
    """The types.xml economy score, with what the Economy tab's charts need:
    per category, per rule, per rarity tier and per severity."""
    from app.ops.economy_analyzer import RARITY_LABELS, RULE_DESCRIPTIONS, analyze_economy

    config = state.config

    def _run() -> dict:
        from app.ops.mission_stock import stock_copy
        from app.xml.types_xml import parse_types_file

        m_dir = _require_mission_dir(config)
        where: dict = {}
        types = _load_all_types(m_dir, where)
        # Vanilla is the baseline when Zedstead holds the stock copy of this
        # mission's types.xml (kept at each server download, mission_stock).
        stock_path = stock_copy(config, m_dir.name, "db/types.xml")
        stock = None
        if stock_path:
            try:
                stock = parse_types_file(stock_path).entries
            except Exception:  # noqa: BLE001 — an unreadable copy means rules only
                stock = None
        report = analyze_economy(types, stock=stock)
        # The charts' names and tips come from the analyzer, never a second
        # copy in the UI.
        data = dataclasses.asdict(report)
        # Each item's types file, for its link to the Types page.
        for item in data["item_scores"] + ((data.get("vanilla") or {}).get("changed") or []):
            item["file"] = where.get(item["classname"].lower())
        return {**data, "rarity_labels": RARITY_LABELS, "rule_descriptions": RULE_DESCRIPTIONS}

    return await asyncio.to_thread(_run)


@router.get("/analysis/logs")
async def list_server_logs(state: WebState = Depends(get_state)) -> dict:
    """The .RPT files available to analyse, plus any crash dumps beside them."""
    from app.ops.log_analysis import crash_dumps, list_logs, profiles_dir

    config = state.config

    def _run() -> dict:
        directory = profiles_dir(config)
        return {
            "dir": str(directory),
            "profiles_found": directory.is_dir(),
            "logs": list_logs(config),
            "crash_dumps": crash_dumps(config),
        }

    return await asyncio.to_thread(_run)


@router.get("/analysis/logs/report")
async def server_log_report(file: str = "", state: WebState = Depends(get_state)) -> dict:
    """Analyse one .RPT — the newest when *file* is omitted.

    Reading a multi-hundred-MB log is squarely blocking work, so like every
    other analysis endpoint it runs in a thread rather than on the event loop.
    """
    from app.ops.log_analysis import (
        analyze_rpt,
        build_log_report,
        mod_aliases,
        previous_run_mods,
        resolve_log,
    )

    config = state.config

    def _run() -> dict:
        # The whole-install report (service -mod= validation, crash dumps,
        # config cross-checks) is only meaningful for the newest log; for an
        # older one the parse alone is what the admin asked for.
        if not file:
            return build_log_report(config)
        path = resolve_log(config, file)
        if path is None:
            raise HTTPException(status_code=404, detail=f"No such log: {file}")
        # The run before is still the logs' own fact, so a mod's first-boot
        # losses read as the mod's here too.
        return analyze_rpt(
            path, previous_mods=previous_run_mods(config, path), mod_aliases=mod_aliases(config),
        )

    return await asyncio.to_thread(_run)


@router.get("/analysis/logs/download")
async def download_server_log(file: str = "", state: WebState = Depends(get_state)):
    """Send one whole .RPT as a file, the newest when *file* is omitted.

    The name is matched against the discovered logs (``resolve_log``), never
    joined onto a path. Moderators and admins only (``permissions.py``).
    """
    from app.ops.log_analysis import resolve_log

    path = await asyncio.to_thread(resolve_log, state.config, file)
    if path is None:
        raise HTTPException(status_code=404, detail=f"No such log: {file or 'no .RPT yet'}")
    return file_attachment(path)


@router.get("/analysis/collisions")
async def collision_analysis(
    kind: str = "all",
    types: str = "",
    check_radius: bool = False,
    check_overlap: bool = False,
    state: WebState = Depends(get_state),
) -> dict:
    """Collision scan. kind = all | eventspawns | playerspawns | territories.

    For eventspawns: *types* is an optional comma-separated event-name filter;
    *check_radius* / *check_overlap* enable the optional distance checks.
    """
    from app.ops.collision_ops import (
        build_collision_query,
        scan_all_collisions,
        scan_eventspawn_collisions_from_paths,
        scan_playerspawn_collisions_from_path,
        scan_territory_collisions_from_dir,
    )

    if kind not in ("all", "eventspawns", "playerspawns", "territories"):
        raise HTTPException(status_code=422, detail=f"Unknown scan kind: {kind!r}")

    config = state.config
    type_list = [t for t in (s.strip() for s in types.split(",")) if t]

    def _run() -> dict:
        m_dir = _require_mission_dir(config)
        if kind == "eventspawns":
            from app.xml.events_xml import get_events_path
            from app.xml.eventspawns_xml import get_eventspawns_path

            spawns_path = get_eventspawns_path(m_dir)
            if spawns_path is None:
                raise HTTPException(status_code=404, detail="cfgeventspawns.xml not found.")
            report = scan_eventspawn_collisions_from_paths(
                spawns_path,
                get_events_path(m_dir),
                event_names=set(type_list) or None,
                check_radius=check_radius,
                check_overlap=check_overlap,
            )
        elif kind == "playerspawns":
            from app.xml.playerspawnpoints_xml import get_spawnpoints_path

            sp_path = get_spawnpoints_path(m_dir)
            if sp_path is None:
                raise HTTPException(status_code=404, detail="cfgplayerspawnpoints.xml not found.")
            report = scan_playerspawn_collisions_from_path(sp_path)
        elif kind == "territories":
            report = scan_territory_collisions_from_dir(m_dir)
        else:
            report = scan_all_collisions(m_dir, config=config)
        data = dataclasses.asdict(report)
        data["map_editor_query"] = build_collision_query(
            kind, type_list, check_radius=check_radius, check_overlap=check_overlap
        )
        return data

    return await asyncio.to_thread(_run)


@router.get("/analysis/collision-event-types")
async def collision_event_types(state: WebState = Depends(get_state)) -> dict:
    """Event names present in cfgeventspawns.xml (for the scan-type filter)."""
    from app.xml.eventspawns_xml import get_eventspawns_path, parse_eventspawns_file

    config = state.config

    def _run() -> list[str]:
        m_dir = _require_mission_dir(config)
        path = get_eventspawns_path(m_dir)
        if path is None:
            return []
        spawns = parse_eventspawns_file(path)
        return sorted({e.name for e in spawns.entries})

    return {"types": await asyncio.to_thread(_run)}


# ---------------------------------------------------------------------------
# Changes (last-start snapshot + baseline)
# ---------------------------------------------------------------------------


def _with_offset(timestamp: str | None) -> str | None:
    """A start or baseline snapshot's time with this machine's UTC offset.

    ``changeset_ops`` stamps snapshots in naive local time, which it compares
    with the naive server start time. A browser reads a naive ISO string as
    its own local time, so a panel opened from another time zone (or a UTC
    container) drew the markers hours off. The offset makes the time exact.
    """
    from datetime import datetime

    if not timestamp:
        return timestamp
    try:
        moment = datetime.fromisoformat(timestamp)
    except ValueError:
        return timestamp
    if moment.tzinfo is None:
        moment = moment.astimezone()  # a naive time is this machine's local time
    return moment.isoformat(timespec="seconds")


def _serialize_change_report(report, request: Request) -> dict:
    """``asdict`` the report, minus the Rich markup the CLI renderer wants.

    ``changeset_ops`` builds each ``description`` for a Rich console (``[dim]``),
    so the raw string would reach the browser as literal ``[...]`` tags — the
    page renders it as plain text.

    The report prints serverDZ.cfg's old and new ``password`` and
    ``passwordAdmin``, so a non-admin gets them hidden (app/web/redact.py).
    """
    data = dataclasses.asdict(report)
    for change in data.get("changes", []):
        change["description"] = strip_markup(change.get("description", ""))
    data = {"available": True, **data, "snapshot_time": _with_offset(data.get("snapshot_time"))}
    return data if redact.is_admin(request) else redact.redact_changes(data)


_EMPTY_CHANGES = {"available": False, "snapshot_time": None, "changes": [], "unchanged_count": 0}


@router.get("/changes")
async def get_changes(request: Request, state: WebState = Depends(get_state)) -> dict:
    from app.ops.changeset_ops import compute_changes

    config = state.config
    report = await asyncio.to_thread(compute_changes, config)
    if report is None:
        return dict(_EMPTY_CHANGES)
    return _serialize_change_report(report, request)


@router.get("/changes/baseline")
async def get_baseline_changes(request: Request, state: WebState = Depends(get_state)) -> dict:
    from app.ops.changeset_ops import compute_baseline_changes

    config = state.config
    report = await asyncio.to_thread(compute_baseline_changes, config)
    if report is None:
        return dict(_EMPTY_CHANGES)
    return _serialize_change_report(report, request)


@router.post("/changes/baseline")
async def set_baseline(state: WebState = Depends(get_state)) -> dict:
    from app.ops.changeset_ops import take_baseline

    await asyncio.to_thread(take_baseline, state.config)
    return {"ok": True}


@router.delete("/changes/baseline")
async def clear_baseline(state: WebState = Depends(get_state)) -> dict:
    from app.ops.changeset_ops import reset_baseline

    await asyncio.to_thread(reset_baseline, state.config)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Config versions
# ---------------------------------------------------------------------------


def version_entries(config, file: str, source_path: str = "") -> list[dict]:
    """The stored versions of *file*, newest first, each with ``identical``.

    *source_path* narrows them to one file's path, the way a config editor
    asks for its own file (``GET /api/configs/{id}/versions``); without it
    every file of that name is listed, as the Analysis page's Versions tab
    shows them.

    `identical` lets the UI hide versions whose "Diff vs current" would come
    back empty (XML re-serialization makes a snapshot's bytes differ while its
    normalized content doesn't). The shared cache reads + re-indents each live
    file once for the whole list. "Current" is BattlEye's active copy while the
    server runs; the cfg itself is missing then, which read as identical and
    hid every version (live_file).
    """
    from pathlib import Path

    from app.shared.config_versions import is_identical_to_current, load_index

    store = config.paths.versions_dir
    cache: dict = {}
    entries = []
    for e in load_index(store).get_versions(file, source_path):
        data = dataclasses.asdict(e)
        current = live_file(config, Path(e.source_path)) if e.source_path else None
        data["identical"] = is_identical_to_current(e, cache, current=current, store=store)
        entries.append(data)
    return entries


@router.get("/versions")
async def list_versions(file: str = "", state: WebState = Depends(get_state)) -> dict:
    from app.shared.config_versions import load_index

    def _run() -> dict:
        if file:
            return {"entries": version_entries(state.config, file)}
        index = load_index(state.config.paths.versions_dir)
        return {"files": [{"file_name": fn, "count": count} for fn, count in index.versioned_files()]}

    return await asyncio.to_thread(_run)


@router.post("/versions/restore")
async def restore_version(body: dict = Body(...), state: WebState = Depends(get_state)) -> dict:
    from app.shared.config_versions import load_index

    file_name = body.get("file")
    version = body.get("version")
    source_path = body.get("source_path", "")
    if not file_name or version is None:
        raise HTTPException(status_code=422, detail="file and version are required")

    store = state.config.paths.versions_dir

    def _run() -> bool:
        from app.shared.config_versions import restore

        index = load_index(store)
        match = next(
            (e for e in index.get_versions(file_name, source_path) if e.version == version), None
        )
        if match is None or not match.source_path:
            raise RuntimeError(f"No stored version {version} for {file_name!r}")
        from pathlib import Path

        target = Path(match.source_path)
        if target == state.config.beserver_cfg_path:
            # BattlEye would put its own copy back over it at the next stop.
            refuse_while_running(state.config)
        return restore(target, version, store=store)

    try:
        restored = await asyncio.to_thread(_run)
    except BeserverCfgInUse as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if restored:
        await state.hub.broadcast({"type": "configs_changed"})
    return {"restored": bool(restored)}


async def _version_diff(request: Request, state: WebState, file: str, version: int | None, source_path: str, differ) -> dict:
    """Run *differ* (get_diff_current or get_diff_change) for one stored
    version, with the live file BattlEye may have renamed, and redact the
    result below admin: context lines print whatever sits beside a change,
    serverDZ.cfg's passwords included (app/web/redact.py)."""
    from pathlib import Path

    from app.shared.config_versions import find_version, load_index

    store = state.config.paths.versions_dir

    def _run() -> str:
        if version is None:
            # A change outside Zedstead that no version holds yet: the path must
            # be one Zedstead recorded a write of, never any path the query names.
            if source_path not in load_index(store).written:
                raise RuntimeError(f"No recorded write of {file!r}")
            target = Path(source_path)
            return differ(target, current=live_file(state.config, target), store=store)
        # A version folded into a newer copy (a repeat) still finds its file.
        match, _ = find_version(load_index(store), file, version, source_path)
        if match is None or not match.source_path:
            raise RuntimeError(f"No stored version {version} for {file!r}")
        target = Path(match.source_path)
        return differ(target, version, current=live_file(state.config, target), store=store)

    try:
        diff = await asyncio.to_thread(_run)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"diff": diff if redact.is_admin(request) else redact.redact_diff(diff)}


@router.get("/versions/diff")
async def diff_version(
    file: str,
    version: int,
    request: Request,
    source_path: str = "",
    state: WebState = Depends(get_state),
) -> dict:
    """Unified diff between a stored version and the file's current content
    (BattlEye's active copy of beserver_x64.cfg while the server runs)."""
    from app.shared.config_versions import get_diff_current

    return await _version_diff(request, state, file, version, source_path, get_diff_current)


@router.get("/versions/change-diff")
async def diff_change(
    file: str,
    request: Request,
    version: int | None = None,
    source_path: str = "",
    state: WebState = Depends(get_state),
) -> dict:
    """What the write after a stored version changed: the Timeline tab's
    "What changed" (config_versions.get_diff_change). With no *version*, the
    change made outside Zedstead since its last write (get_outside_diff)."""
    from app.shared.config_versions import get_diff_change, get_outside_diff

    if version is None:
        return await _version_diff(request, state, file, None, source_path, get_outside_diff)
    return await _version_diff(request, state, file, version, source_path, get_diff_change)


@router.get("/versions/timeline")
async def versions_timeline(
    limit: int = 500,
    offset: int = 0,
    source_path: str | None = None,
    state: WebState = Depends(get_state),
) -> dict:
    """Every saved version as a change event, newest first, with the times of
    the last server start and the baseline as markers (Analysis → Change
    history). A page of *limit* after *offset*, of one file when
    *source_path* is set; ``more`` says an older page exists."""
    from app.ops.changeset_ops import load_baseline, load_snapshot
    from app.shared.config_versions import timeline

    config = state.config

    def _run() -> dict:
        start, baseline = load_snapshot(config), load_baseline(config)
        size = max(1, min(limit, 2000))
        # One past the page tells whether an older page exists.
        events = timeline(
            config.paths.versions_dir, limit=size + 1, offset=max(0, offset), source_path=source_path,
        )
        return {
            "events": events[:size],
            "more": len(events) > size,
            "markers": [
                {"kind": kind, "timestamp": _with_offset(snap.timestamp)}
                for kind, snap in (("server_start", start), ("baseline", baseline))
                if snap is not None
            ],
        }

    return await asyncio.to_thread(_run)


def _snapshot_candidates_map(config) -> dict:
    """display_name -> absolute path of every snapshotable config file
    (the manual-snapshot picker's choices)."""
    from app.xml.xml_utils import get_mission_dir

    file_map: dict = {}
    m_dir = get_mission_dir(config)
    if m_dir:
        for f in sorted(m_dir.rglob("*")):
            if f.is_file() and f.suffix in (".xml", ".json"):
                file_map[str(f.relative_to(m_dir))] = f
    profiles_dir = config.server_dir_path / config.profiles_path
    if profiles_dir.is_dir():
        for f in sorted(profiles_dir.rglob("*.json")):
            if f.is_file():
                file_map[f"profiles/{f.relative_to(profiles_dir)}"] = f
    return file_map


@router.get("/versions/candidates")
async def snapshot_candidates(state: WebState = Depends(get_state)) -> dict:
    files = await asyncio.to_thread(_snapshot_candidates_map, state.config)
    return {"files": sorted(files.keys())}


@router.post("/versions/snapshot")
async def create_snapshot(body: dict = Body(...), state: WebState = Depends(get_state)) -> dict:
    from app.shared.config_versions import snapshot

    file_name = str(body.get("file", ""))
    if not file_name:
        raise HTTPException(status_code=422, detail="file is required")

    def _run():
        # Resolve through the candidates map only — never join the raw
        # user-supplied name onto a directory.
        file_map = _snapshot_candidates_map(state.config)
        path = file_map.get(file_name)
        if path is None:
            raise RuntimeError(f"Unknown config file: {file_name!r}")
        return snapshot(path, trigger="manual", store=state.config.paths.versions_dir)

    try:
        entry = await asyncio.to_thread(_run)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if entry is None:
        return {"created": False}
    return {"created": True, "version": entry.version, "file_name": entry.file_name}


@router.post("/versions/cleanup")
async def cleanup_versions(body: dict = Body(...), state: WebState = Depends(get_state)) -> dict:
    from app.shared.config_versions import cleanup_all

    keep = body.get("keep", 5)
    try:
        keep = int(keep)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="keep must be an integer")
    if keep < 1:
        raise HTTPException(status_code=422, detail="Must keep at least 1 version")

    removed = await asyncio.to_thread(cleanup_all, keep, store=state.config.paths.versions_dir)
    return {"removed": removed, "keep": keep}


# ---------------------------------------------------------------------------
# Metrics / A2S
# ---------------------------------------------------------------------------


@router.get("/metrics/summary")
async def metrics_summary(hours: int = 1, state: WebState = Depends(get_state)) -> dict:
    from app.monitoring.metrics_db import query_summary

    summary = await asyncio.to_thread(query_summary, hours, db_path=state.config.paths.metrics_db)
    return dataclasses.asdict(summary)


@router.get("/metrics/recent")
async def metrics_recent(hours: int = 1, state: WebState = Depends(get_state)) -> dict:
    from app.monitoring.metrics_db import query_recent

    snapshots = await asyncio.to_thread(query_recent, hours, db_path=state.config.paths.metrics_db)
    return {"snapshots": [dataclasses.asdict(s) for s in snapshots]}


@router.get("/metrics/hourly")
async def metrics_hourly(hours: int = 168, state: WebState = Depends(get_state)) -> dict:
    from app.monitoring.metrics_db import query_hourly_averages

    days = max(1, hours // 24)
    hourly = await asyncio.to_thread(
        query_hourly_averages, days, db_path=state.config.paths.metrics_db,
    )
    return {"hourly": hourly}


@router.get("/metrics/peaks")
async def metrics_peaks(hours: int = 720, state: WebState = Depends(get_state)) -> dict:
    from app.monitoring.metrics_db import query_peaks

    days = max(1, hours // 24)
    peaks = await asyncio.to_thread(query_peaks, days, db_path=state.config.paths.metrics_db)
    return peaks


@router.post("/metrics/cleanup")
async def metrics_cleanup(
    body: dict = Body(default={}), state: WebState = Depends(get_state)
) -> dict:
    from app.monitoring.metrics_db import cleanup_old

    retention_days = body.get("retention_days", 30)
    try:
        retention_days = int(retention_days)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="retention_days must be an integer")
    if retention_days < 1:
        raise HTTPException(status_code=422, detail="retention_days must be >= 1")

    removed = await asyncio.to_thread(
        cleanup_old, retention_days, db_path=state.config.paths.metrics_db,
    )
    return {"removed": removed, "retention_days": retention_days}


@router.get("/a2s")
async def a2s_query(state: WebState = Depends(get_state)) -> dict:
    from app.monitoring.a2s_monitor import query_server
    from app.server.server_status import get_steam_query_port

    config = state.config
    port = await asyncio.to_thread(get_steam_query_port, config)
    result = await query_server("127.0.0.1", port)
    return dataclasses.asdict(result)
