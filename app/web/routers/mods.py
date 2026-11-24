"""GET/POST /api/mods, /api/mods/search, /api/mods/cached, /api/mods/known,
/api/mods/expansion, /api/mods/install, /api/mods/uninstall,
/api/mods/{name}/enable|disable,
/api/mods/updates, /api/mods/check-updates, /api/mods/conflicts,
/api/mods/load-order, /api/mods/missing-deps, /api/mods/xml-status,
/api/mods/xml/merge, /api/mods/{name}/xml/merge, /api/mods/{name}/spawn-test,
/api/mods/spawn-test/end, /api/mods/rollback, /api/mods/{ref}/files,
/api/mods/{ref}/file, /api/mods/{ref} (per-mod detail, declared last — see the
comment above it)."""

from __future__ import annotations

import asyncio
import functools
import dataclasses
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.modmgr import expansion, mod_conflict, mod_ops, modlist, mods, workshop_api
from app.modmgr.mod_info import build_mod_info_map
from app.ops import pipeline
from app.server import service_control
from app.shared.steam import download_mod_async
from app.shared.seed_data import MOD_CREDITS
from app.xml.xml_merge import apply_failed_message
from app.web.deps import get_state, require_session
from app.web.jobs import Job, JobConflictError, JobRunner, conflict_response
from app.web.schemas import (
    ModAddRequest,
    ModAutoConfigRequest,
    ModInstallRequest,
    ModLoadOrderRequest,
    ModServerSideRequest,
    ModUninstallRequest,
    SpawnTestStartRequest,
)
from app.web.state import WebState

router = APIRouter(prefix="/api/mods", tags=["mods"], dependencies=[Depends(require_session)])


class _QuietRunner:
    """Runner duck-type for a service regen outside a job: subprocess output
    is discarded, write() lines are dropped."""

    async def run_command(self, cmd: list[str]) -> int:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return await proc.wait()

    def write(self, text: str) -> None:
        pass


async def _regenerate_service(config) -> bool:
    """Regenerate + install service files after the active mod set changed —
    the unit's -mod= list is built from the enabled mods, so skipping this
    leaves a disabled mod loading on the next server start."""
    from app.server.service_ops import generate_service_files_async

    return await generate_service_files_async(_QuietRunner(), config)


def _toggle_mod_economy(config, name: str, *, enable: bool) -> None:
    """Toggle CE entries **and** rebuild the direct-merge mission files — see
    `mods.apply_mod_state_to_mission_xml`; the CE half alone isn't enough."""
    from app.modmgr.mods import apply_mod_state_to_mission_xml

    apply_mod_state_to_mission_xml(config, name, enable=enable)


def _update_to_dict(u: workshop_api.ModUpdateInfo) -> dict:
    return {
        "mod_id": u.mod_id,
        "title": u.title,
        "remote_updated": u.remote_updated,
        "local_updated": u.local_updated,
        "age_seconds": u.age_seconds,
        "age_display": u.age_display,
    }


def _check_to_dict(check: workshop_api.UpdateCheck) -> dict:
    return {
        "updates": [_update_to_dict(u) for u in check.updates],
        "checked": check.checked,
        "total": check.total,
        "checked_at": check.checked_at,
    }


def _dir_time(path) -> int:
    """Newest mtime of a directory and its immediate children (0 if unusable).

    The directory's own mtime is set when a copy creates it, but re-copying
    over an existing mod dir only touches the children — so take the max of
    both rather than trusting the top node alone.
    """
    if path is None:
        return 0
    try:
        newest = path.stat().st_mtime
        for entry in path.iterdir():
            newest = max(newest, entry.stat().st_mtime)
    except OSError:
        return 0
    return int(newest)


def _mod_timestamps(config, mod, installed_dir, meta) -> dict:
    """The four dates the mod page shows, as Unix epochs (0 = unknown).

    ``built`` is the mod's own meta.cpp stamp; ``workshop_updated`` is the
    revision SteamCMD recorded for the downloaded copy (workshop ACF, falling
    back to zsm's stored timestamps — same pair `mod_ops.detect_mod_updates`
    reads); ``downloaded``/``installed`` are filesystem times for the workshop
    cache copy and the server copy.
    """
    from app.modmgr.mod_info import parse_mod_timestamp
    from app.modmgr.workshop_api import load_stored_timestamps
    from app.parsers.acf_parser import get_installed_timestamps, parse_acf_file
    from app.shared.paths import CONFIG_DIR

    workshop_updated = 0
    if mod.workshop_id:
        local_ts: dict[str, int] = {}
        acf_path = config.workshop_acf_path
        if acf_path.is_file():
            try:
                local_ts = get_installed_timestamps(parse_acf_file(acf_path))
            except Exception:
                local_ts = {}
        if not local_ts:
            local_ts = load_stored_timestamps(CONFIG_DIR / "mod_timestamps.json")
        workshop_updated = int(local_ts.get(mod.workshop_id, 0) or 0)

    return {
        "built": (parse_mod_timestamp(meta.timestamp) or 0) if meta else 0,
        "workshop_updated": workshop_updated,
        "downloaded": _dir_time(mod.workshop_path) if mod.cached else 0,
        "installed": _dir_time(installed_dir),
    }


def _info_text(pair: tuple[str, str] | None) -> str:
    """Flatten build_mod_info_map's (title, body) tooltip into one string ("" if
    the mod's files couldn't be read)."""
    if not pair:
        return ""
    return "\n\n".join(p for p in pair if p)


def _list_mods_sync(config) -> list[dict]:
    infos = mod_ops.get_mod_info_list(config)
    disabled = modlist.disabled(config)
    server_side = set(modlist.server_only(config))
    cached_ids = modlist.workshop_ids(config)
    # Per-mod tooltip read from the mod's files — the installed copy if present,
    # else the downloaded workshop copy (built by mod_info.build_mod_info_map).
    mod_dirs = {}
    for i in infos:
        installed_dir = config.server_dir_path / i.name
        if installed_dir.is_dir():
            mod_dirs[i.name] = installed_dir
        elif i.workshop_id:
            mod_dirs[i.name] = config.workshop_dir / i.workshop_id
    info_map = build_mod_info_map(mod_dirs, cached_ids)
    # Set aside after a failed boot (plans/73): {build, at, updated_at, ...}.
    from app.ops.post_update import triage_entries

    triage = {e["mod"]: e for e in triage_entries(config)}
    return [
        {
            "name": i.name,
            "id": i.workshop_id,
            "installed": i.installed,
            "enabled": i.name not in disabled,
            "server_side": i.name in server_side,
            "info": _info_text(info_map.get(i.name)),
            "set_aside": triage.get(i.name),
        }
        for i in infos
    ]


@router.get("")
async def list_mods(state: WebState = Depends(get_state)) -> dict:
    """The mod list, plus the active spawn test (`null` when there is none).

    `spawn_test` rides along here rather than on its own endpoint so the
    dashboard's Mods tile — which already polls this and refreshes on
    `mods_changed` — can surface a running test without a second request.
    """
    from app.ops.spawn_test_ops import active_test

    return {
        "mods": await asyncio.to_thread(_list_mods_sync, state.config),
        "spawn_test": active_test(state.config),
    }


@router.get("/help")
async def mods_help() -> dict:
    """Plain-text hints for the Mods page tabs/actions (descriptions from
    app.shared.mods_help, stripped of Rich markup)."""
    return mods_help_payload()


def mods_help_payload() -> dict:
    """The body of GET /api/mods/help. The search index (routers/search.py)
    reads its `tabs` too."""
    from app.shared.mods_help import ITEM_DESCRIPTIONS
    from app.web.editors._help import strip_markup

    m = {k: strip_markup(v) for k, v in ITEM_DESCRIPTIONS.items()}
    return {
        "tabs": {
            "Installed": "The mods in the mod list, in the order the server loads them. Turn a "
            "mod on or off, choose whether players need it too, change the load order, "
            "update it or uninstall it.",
            "Install": "Add a mod to the server: one Zedstead has editors for, one you name or "
            "know the Workshop ID of, one already downloaded, or DayZ-Expansion module by "
            "module. " + m.get("Install", ""),
            "Supported": "The mods Zedstead has editors for: TraderPlus, DayZ-Expansion, "
            "Dynamic AI, RadZone and ToxicZone. Each gets its own tab under Configs → Mods.",
            "By Name": "Install mods by name. Zedstead looks the Workshop ID up in what it "
            "already knows, and otherwise searches the Steam Workshop for a mod with exactly "
            "that title.",
            "By ID": "Install a mod by its Steam Workshop ID, the number at the end of the "
            "mod's Workshop page address.",
            # Installing one runs SteamCMD for it first (POST /api/mods/install
            # always downloads; Update relies on that), so the old "without
            # downloading again" was untrue.
            "Workshop": "Mods SteamCMD has already downloaded but the server does not have "
            "yet. Installing one runs SteamCMD for it first, so it needs the Steam login and "
            "brings the mod up to date, then copies it in.",
            "Search": "Searches the mods Zedstead already knows by name and ID (its own list, "
            "IDs looked up before, and the downloaded ones). It does not search the Steam "
            "Workshop.",
            "Expansion": "DayZ-Expansion is 19 separate Workshop mods. Tick the modules you "
            "want; the ones they need (Core, @CF, @Dabs Framework, and Licensed for "
            "Vehicles) are ticked for you. Below, the Expansion mission files for your map.",
            "Conflicts": m.get("Scan for Conflicts", ""),
            "Updates": m.get("Available Updates", ""),
            "Workshop Cache": "Mods SteamCMD has downloaded, kept so an install or update "
            "does not download them again. Removing them frees disk space and does not touch "
            "the mods installed on the server.",
        },
        "actions": {
            "uninstall": m.get("Uninstall", ""),
            "update": m.get("Update", ""),
            "enable_disable": m.get("Enable/Disable", ""),
            "reorder": "Load order: the server loads mods from 1 downward. Drag a row from "
            "anywhere but its buttons and links (on a touch screen, by its ⠿ grip), or with a keyboard tick rows and press the ↑/↓ arrow keys, then "
            "click Save load order, which shows once the order changed; the new "
            "order applies from the next server start. Automatic: a mod always loads after "
            "the mods it requires, so a locked mod can't move past them. Manual: any mod "
            "can move anywhere, and a mod placed before one it requires is marked.",
            "scan": m.get("Scan for Conflicts", ""),
            "lock": "Locked by a hard dependency — it can't move past the mods it requires "
            "or that require it.",
            "lock_open": "Has dependencies, but manual load order lets it move past them. "
            "Load it after the mods it requires, or it may error.",
            "manual": "Automatic sorts every mod after the mods it requires; your order only "
            "breaks ties. Manual uses your order exactly, even where a mod loads before one "
            "it requires.",
            # Written for the web page, not taken from ITEM_DESCRIPTIONS.
            "xml": "Mission XML files this mod ships: linked = copied to custom/ and registered "
            "in cfgeconomycore.xml, merged = spliced into a mission file, not applied yet = "
            "recognised but never merged, skipped = another map's variant or not a "
            "central-economy file.",
            "reconfigure": "Re-apply just this mod's mission XML — its custom/ files, its "
            "cfgeconomycore entries and its merged blocks are rebuilt, every other mod's "
            "merged XML is left untouched. Enable the mod first; a disabled mod's XML is "
            "deliberately parked.",
            "install": "Download this mod from the Steam Workshop, copy it into the server, "
            "apply its XML to the mission and rebuild the launch command.",
            "reinstall": "Delete this mod's workshop cache copy, download it again from the Steam Workshop and replace the server copy. Use it when a mod is broken or half downloaded: an update can keep a cached copy that SteamCMD thinks is current.",
            "remove_cache": "Delete this mod's downloaded copy from the SteamCMD workshop cache. "
            "The mod installed on the server is not touched.",
            # The Mods page's buttons act on several mods; ModPage's on one.
            "install_selected": m.get("Install", ""),
            "remove_cache_selected": "Deletes the ticked mods' downloaded copies from the "
            "SteamCMD workshop cache to free disk space. The mods installed on the server are "
            "not touched. A removed mod that is still in the mod list is downloaded again on "
            "the next update.",
        },
    }


@router.get("/search")
async def search_mods(q: str, state: WebState = Depends(get_state)) -> dict:
    results = await asyncio.to_thread(mod_ops.search_mod_workshop, state.config, q)
    return {"results": [{"id": wid, "title": title} for wid, title in results]}


def _refuse_unsafe_mod_refs(names: list[str], workshop_ids: list[str]) -> None:
    """400 for a mod name that is not one ``@Name`` folder, or a workshop ID
    that is not a number. Both become a path: ``server_dir / name`` and
    ``workshop_dir / id``, and the mod list carries the name to every later run."""
    bad = [n for n in names if not mods.is_safe_mod_dir_name(n)]
    bad += [i for i in workshop_ids if not i.isdigit()]
    if bad:
        raise HTTPException(
            status_code=400,
            detail="Not a mod folder name or workshop ID: " + ", ".join(repr(b) for b in bad),
        )


@router.post("")
async def add_mod(body: ModAddRequest, state: WebState = Depends(get_state)) -> dict:
    state.jobs.refuse_while_busy()
    config = state.config
    name = mod_ops.ensure_at_prefix(body.name)
    _refuse_unsafe_mod_refs([name], [])

    def _add() -> tuple[bool, str | None, bool]:
        added = modlist.add(config, name)
        mod_id, was_cached = mod_ops.resolve_mod_workshop_id(config, name)
        return added, mod_id, was_cached

    added, mod_id, was_cached = await asyncio.to_thread(_add)
    await state.hub.broadcast({"type": "mods_changed"})
    return {"added": added, "name": name, "id": mod_id, "was_cached": was_cached}


@router.get("/updates")
async def get_updates(state: WebState = Depends(get_state)) -> dict:
    check = await asyncio.to_thread(workshop_api.load_update_check, state.config.paths.mod_updates)
    # Whether a key is set, never the key: the tab shows why it can't check.
    return {**_check_to_dict(check), "api_key_set": bool(state.config.steam_api_key)}


@router.post("/check-updates")
async def check_updates(state: WebState = Depends(get_state)) -> dict:
    """Run the update check. A check that could not run is an error, never an
    empty list: no key is 409 (not configured), anything Steam did is 502, and
    neither touches the last good answer on disk (plans/50 B)."""
    config = state.config
    path = config.paths.mod_updates

    def _check() -> workshop_api.UpdateCheck:
        check = mod_ops.detect_mod_updates(config)
        workshop_api.save_detected_updates(path, check)
        return check

    try:
        check = await asyncio.to_thread(_check)
    except workshop_api.UpdateCheckError as exc:
        status = 409 if exc.reason == workshop_api.NO_API_KEY else 502
        raise HTTPException(status_code=status, detail=exc.message) from None
    return _check_to_dict(check)


@router.get("/conflicts")
async def get_conflicts(state: WebState = Depends(get_state)) -> dict:
    from app.ops.fix_hints import conflict_fix

    # A person opened the tab, so this scan may ask Steam for Required Items
    # (with an API key); every other scan reads only the cache.
    result = await asyncio.to_thread(mod_conflict.scan_conflicts, state.config, fetch_workshop=True)
    body = dataclasses.asdict(result)
    # Same remediation payload the Doctor report attaches to the same findings
    # (app/ops/fix_hints.py) — the two Conflicts tables render one component.
    # asdict preserves order, so the dicts still pair with their dataclasses.
    for row, item in zip(body["items"], result.items):
        row["fix"] = conflict_fix(item)
    return body


@router.post("/copy-bikeys")
async def copy_bikeys(state: WebState = Depends(get_state)) -> dict:
    """Re-sync the server's keys/ folder from the enabled mods.

    The "Copy bikeys" fix behind a ``missing_bikey`` finding. Same
    ``fileops.copy_bikey_files`` the install job and ``zsm run`` call — a plain
    file copy over a handful of small files, so it answers inline instead of
    going through the job runner (nothing to stream, and it must stay usable
    while the Doctor page is polling).
    """
    state.jobs.refuse_while_busy()
    from app.ops.fileops import copy_bikey_files

    removed, copied = await asyncio.to_thread(copy_bikey_files, state.config, log=lambda _s: None)
    await state.hub.broadcast({"type": "mods_changed"})
    return {"removed": removed, "copied": copied}


def _load_order_sync(config) -> tuple[list[str], dict[str, list[str]]]:
    """Resolved load order + the dependency map behind it (shared by the
    load-order endpoint and the per-mod detail page)."""
    order = mods.get_resolved_mod_order(config)
    deps = mods.get_mod_load_deps(config)
    return order, {k: sorted(v) for k, v in deps.items()}


@router.get("/load-order")
async def get_load_order(state: WebState = Depends(get_state)) -> dict:
    def _load() -> tuple[list[str], dict[str, list[str]], list[str]]:
        order, deps = _load_order_sync(state.config)
        return order, deps, mods.get_resolved_mod_order(state.config, include_disabled=True)

    order, deps, rows = await asyncio.to_thread(_load)
    # `rows` is the order the Installed table shows: disabled mods included,
    # each where it would load if it were on (F78). `order` stays the real one.
    # `deps` is sent in manual mode too: the page marks what it no longer enforces.
    return {"order": order, "deps": deps, "rows": rows, "manual": modlist.load(state.config).manual}


@router.put("/load-order")
async def set_load_order(body: ModLoadOrderRequest, state: WebState = Depends(get_state)) -> dict:
    if body.order is None and body.manual is None:
        raise HTTPException(status_code=422, detail="Send an order, the manual switch, or both.")
    state.jobs.refuse_while_busy()
    config = state.config
    await asyncio.to_thread(mods.apply_load_order, config, order=body.order, manual=body.manual)
    await _regenerate_service(config)
    await state.hub.broadcast({"type": "mods_changed"})
    return {"ok": True}


@router.get("/missing-deps")
async def get_missing_deps(names: str, state: WebState = Depends(get_state)) -> dict:
    config = state.config
    requested = [n.strip() for n in names.split(",") if n.strip()]

    def _compute() -> list[tuple[str, str]]:
        cached_ids = modlist.workshop_ids(config)
        installing_ids = list(mod_ops.resolve_names_to_ids(requested, cached_ids).values())
        installed_mods = set(modlist.installed(config))
        installed_ids = set(mod_ops.resolve_names_to_ids(list(installed_mods), cached_ids).values())
        return mod_ops.get_missing_deps_ex(config, installing_ids, installed_ids)

    missing = await asyncio.to_thread(_compute)
    return {"missing": [{"id": wid, "name": name} for wid, name in missing]}


def _require_listed_mod(config, name: str) -> None:
    """404 unless `name` is a mod in the mod list.

    The name reaches these routes from a URL segment (and, since the mod page,
    from one a user can type), and disable persists it into mods.json — so an unknown name must not get that far.
    """
    if name not in {i.name for i in mod_ops.get_mod_info_list(config)}:
        raise HTTPException(status_code=404, detail=f"Unknown mod: {name}")


@router.post("/{name}/enable")
async def enable_mod(name: str, state: WebState = Depends(get_state)) -> dict:
    state.jobs.refuse_while_busy()
    await asyncio.to_thread(_require_listed_mod, state.config, name)

    from app.modmgr.mods import mark_enabled

    config = await state.mutate_config(lambda cfg: mark_enabled(cfg, name))
    await asyncio.to_thread(_toggle_mod_economy, config, name, enable=True)
    await _regenerate_service(config)
    await state.hub.broadcast({"type": "mods_changed"})
    return {"ok": True}


@router.post("/{name}/disable")
async def disable_mod(name: str, state: WebState = Depends(get_state)) -> dict:
    state.jobs.refuse_while_busy()
    await asyncio.to_thread(_require_listed_mod, state.config, name)

    from app.modmgr.mods import mark_disabled

    config = await state.mutate_config(lambda cfg: mark_disabled(cfg, name))
    await asyncio.to_thread(_toggle_mod_economy, config, name, enable=False)
    await _regenerate_service(config)
    await state.hub.broadcast({"type": "mods_changed"})
    return {"ok": True}


@router.post("/{name}/server-side")
async def set_server_side(
    name: str, body: ModServerSideRequest, state: WebState = Depends(get_state)
) -> dict:
    """Move a mod between -mod= and -serverMod= (plans/47 A) and rebuild the
    unit, which is where that list lives."""
    state.jobs.refuse_while_busy()
    await asyncio.to_thread(_require_listed_mod, state.config, name)
    from app.modmgr.mods import mark_server_side

    config = await state.mutate_config(lambda cfg: mark_server_side(cfg, name, body.server_side))
    await _regenerate_service(config)
    await state.hub.broadcast({"type": "mods_changed"})
    return {"ok": True, "name": name, "server_side": name in modlist.server_only(config)}


def _xml_status_sync(config) -> dict[str, dict]:
    """Per-mod XML tallies, reduced from the same report the Mod XML page uses."""
    from app.xml.xml_report import (
        STATUS_DISABLED,
        STATUS_LINKED,
        STATUS_MERGED,
        STATUS_NO_TARGET,
        build_mod_xml_report,
    )

    # Statuses a re-configure would act on. STATUS_NO_TARGET ("recognised kind,
    # but not applied to the mission yet") is the case the button exists for, so
    # it counts. Another map's variants, non-economy files and
    # STATUS_NOT_AUTO_LINKED don't — the last one is a mod's messages.xml, which
    # no merge will ever apply, so a "pending" badge for it could never clear.
    counted = {
        STATUS_LINKED: "linked",
        STATUS_MERGED: "merged",
        STATUS_DISABLED: "disabled",
        STATUS_NO_TARGET: "pending",
    }
    report = build_mod_xml_report(config)

    out: dict[str, dict] = {}

    def entry_for(mod: str) -> dict:
        return out.setdefault(
            mod,
            {
                "files": 0,
                "linked": 0,
                "merged": 0,
                "disabled": 0,
                "pending": 0,
                "off": 0,
                "manual": 0,
                "skipped": 0,
                "actionable": 0,
            },
        )

    # Every file lands in exactly one bucket, so linked + merged + disabled +
    # pending + off + skipped == files. A turned-off file's status ("excluded",
    # "deleted") is not in `counted`, so it used to land in "skipped" as well as
    # in "off".
    tiles_off = {tile["name"] for tile in report["custom"] if tile["excluded"]}
    for row in report["files"]:
        entry = entry_for(row["mod"])
        entry["files"] += 1
        # Files the admin turned off, counted off the row/tile *flags* rather
        # than the status, which a disabled mod's file loses. Reported so the
        # page can say so and offer the bulk restore, but deliberately NOT
        # actionable: a re-configure honours these records, so counting them
        # would leave a "pending" badge lit that no button can clear.
        #
        # A file is off by its source (the row's own flag: a direct-merge row,
        # or a central-economy file after Adopt's "Keep mine", which writes
        # `set_source_state`) or, for a central-economy file, by its custom/
        # tile's state. Off both ways, it is still one file, counted once.
        if row["excluded"] or row.get("custom_name") in tiles_off:
            entry["off"] += 1
            continue
        key = counted.get(row["status"])
        if key:
            entry[key] += 1
            entry["actionable"] += 1
        else:
            entry["skipped"] += 1

    # Manual XML files the admin bound to a mod. `mod_name` is the *resolved*
    # mod (the stored value may be a workshop id), so this joins on one
    # spelling. Reported separately from `files`: it is the admin's own XML, not
    # the mod's, and nothing on this page acts on it.
    # A mod that ships no XML of its own can still have one of these, so the
    # entry is created rather than looked up.
    from app.ops.mod_autoconfig import file_id_for

    # `auto_file` is the id of the mod's Auto configure file, when it has one,
    # so the row menu can open that file's form on Custom config.
    for row in report.get("manual") or []:
        if row.get("mod_name"):
            entry = entry_for(row["mod_name"])
            entry["manual"] += 1
            if row.get("id") == file_id_for(row["mod_name"]):
                entry["auto_file"] = row["id"]

    # `no_items` marks a mod with no XML whose PBOs define no item class (a
    # framework mod such as @CF), so the page disables its Auto configure
    # button instead of letting the click fail (`mod_autoconfig.build`).
    # The scan is cached per mod by PBO mtime, so a poll re-reads nothing.
    from app.modmgr.mod_conflict import TYPE_CLASS_SECTIONS, extract_mod_classnames_cached

    bare = [
        m for m in modlist.installed(config)
        if not (out.get(m, {}).get("files") or out.get(m, {}).get("manual"))
    ]
    if bare:
        classes = extract_mod_classnames_cached(
            config.server_dir_path, bare, TYPE_CLASS_SECTIONS, prune=False,
        )
        for m in bare:
            if not classes.get(m):
                entry_for(m)["no_items"] = True
    return out


@router.get("/xml-status")
async def mods_xml_status(state: WebState = Depends(get_state)) -> dict:
    """Per-mod XML counts driving the Installed table's XML column and whether
    the per-row Re-configure button is offered."""
    return {"mods": await asyncio.to_thread(_xml_status_sync, state.config)}


def _merge_all_ctx(job: Job, config):
    return pipeline.PipelineContext(
        config=config,
        emit=job.log_line_threadsafe,
        runner=JobRunner(job),
        should_run=lambda key: True,
    )


@router.post("/xml/merge")
async def merge_all_mod_xml(state: WebState = Depends(get_state)):
    """Re-apply every mod's mission XML: the update pipeline's "Apply mod XML"
    step on its own, for when it needs running again without a whole update
    run."""
    config = state.config

    async def _job_coro(job: Job) -> dict:
        job.log_line("Re-configuring mission XML for all mods...")
        mission = await asyncio.to_thread(pipeline._merge_xml_sync, _merge_all_ctx(job, config))
        await state.hub.broadcast({"type": "mods_changed"})
        await state.hub.broadcast({"type": "configs_changed"})
        return {"mission": mission}

    try:
        job = state.jobs.start("Re-configure mod XML", "mods_xml_merge", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}


@router.post("/{name}/xml/restore")
async def restore_one_mod_xml(name: str, state: WebState = Depends(get_state)) -> dict:
    """Turn every one of a mod's XML files back on, then re-apply them.

    The bulk undo for the Mod XML page's per-file Disable/Delete. It is a
    separate action from `POST /{name}/xml/merge` on purpose: a merge that
    dropped these records would undo them on every install, mod toggle and
    re-configure, which is exactly what the records exist to survive.

    Runs synchronously — clearing two JSON files and a scoped merge is not job
    work, and the caller reloads the report straight after.
    """
    state.jobs.refuse_while_busy()
    from app.modmgr import mods as mods_mod
    from app.xml import xml_manual

    config = state.config
    _require_listed_mod(config, name)

    def _restore() -> dict:
        cleared = xml_manual.clear_mod_file_states(
            config.server_dir_path, name, manual_dir=config.paths.custom_xml_dir,
        )
        # A disabled mod's `<ce>` entries are parked in DISABLED wrappers that a
        # scoped merge deletes — same refusal the merge route makes. The records
        # are cleared either way; enabling the mod applies them.
        if name in modlist.disabled(config):
            return {"mod": name, "restored": cleared, "applied": False, "reason": "mod disabled"}
        result = mods_mod.reconfigure_mod_xml(config, name)
        return {"mod": name, "restored": cleared, "applied": True, **result}

    result = await asyncio.to_thread(_restore)
    await state.hub.broadcast({"type": "mods_changed"})
    await state.hub.broadcast({"type": "configs_changed"})
    return result


@router.post("/{name}/xml/merge")
async def merge_one_mod_xml(name: str, state: WebState = Depends(get_state)):
    """Re-apply a single mod's mission XML, leaving other mods' merged XML
    untouched. Refused for disabled mods: their `<ce>` entries are parked in
    `<!-- DISABLED ... -->` wrappers that a scoped re-merge would delete."""
    config = state.config

    _require_listed_mod(config, name)
    if name in modlist.disabled(config):
        raise HTTPException(
            status_code=400,
            detail=f"{name} is disabled — enable it first to re-apply its XML.",
        )

    async def _job_coro(job: Job) -> dict:
        job.log_line(f"Re-configuring mission XML for {name}...")
        result = await asyncio.to_thread(
            mods.reconfigure_mod_xml, config, name, job.log_line_threadsafe,
        )
        if result["mission"] is None:
            raise RuntimeError("Could not determine the current mission from serverDZ.cfg.")
        if result["failed"]:
            job.log_line(f"Warning: {apply_failed_message(result['failed'])}")
        if not result["linked"] and not result["failed"]:
            job.log_line(f"No cfgeconomycore-linked XML found for {name}.")
        await state.hub.broadcast({"type": "mods_changed"})
        await state.hub.broadcast({"type": "configs_changed"})
        return {"mod": name, **result}

    try:
        job = state.jobs.start(f"Re-configure {name} XML", "mods_xml_merge", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}


def _autoconfig_target(config, name: str) -> None:
    """The checks both Auto configure routes make: a listed mod that is on.
    A disabled mod's XML is parked, so a scoped apply would delete it."""
    _require_listed_mod(config, name)
    if name in modlist.disabled(config):
        raise HTTPException(status_code=400, detail=f"{name} is disabled — enable it first.")


def _autoconfig_options(body: ModAutoConfigRequest | None):
    """The dialog's switches as `AutoConfigOptions`; no body means all on."""
    from app.ops.mod_autoconfig import AutoConfigOptions

    return AutoConfigOptions.from_dict(body.model_dump() if body else None)


@router.post("/{name}/xml/auto-config/preview")
async def autoconfig_preview(
    name: str, body: ModAutoConfigRequest | None = None, state: WebState = Depends(get_state)
) -> dict:
    """What Auto configure would add for a mod that ships no XML
    (`app/ops/mod_autoconfig.py`). A POST: it asks Steam for the description."""
    from app.ops import mod_autoconfig

    config = state.config
    _autoconfig_target(config, name)
    try:
        preview = await asyncio.to_thread(
            functools.partial(mod_autoconfig.build, config, name, options=_autoconfig_options(body))
        )
    except mod_autoconfig.AutoConfigError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return preview.to_dict()


@router.post("/{name}/xml/auto-config")
async def autoconfig_apply(
    name: str, body: ModAutoConfigRequest | None = None, state: WebState = Depends(get_state)
):
    """Run Auto configure as a job. The entries are built again in the job,
    never taken from the request, so only what `build` allows is written."""
    from app.ops import mod_autoconfig

    config = state.config
    _autoconfig_target(config, name)
    options = _autoconfig_options(body)

    async def _job_coro(job: Job) -> dict:
        job.log_line(f"Building types entries for {name}...")
        try:
            preview = await asyncio.to_thread(functools.partial(mod_autoconfig.build, config, name, options=options))
            job.log_line(f"{preview.count} entries ({preview.source}), {len(preview.skipped)} classes skipped.")
            result = await asyncio.to_thread(mod_autoconfig.apply, config, preview)
        except mod_autoconfig.AutoConfigError as e:
            raise RuntimeError(str(e)) from None
        if not result["applied"]:
            job.log_line(f"Warning: {result['file_id']} was stored but not applied to the mission.")
        await state.hub.broadcast({"type": "mods_changed"})
        await state.hub.broadcast({"type": "configs_changed"})
        return {"mod": name, **result}

    try:
        job = state.jobs.start(f"Auto configure {name}", "mods_xml_merge", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}


# ---------------------------------------------------------------------------
# Spawn test (plans/37) — N of a mod's class next to the first player (or at a
# map location, the player moved there), undone
# by "End test & restore". The logic lives in app/ops/spawn_test_ops.py.
# ---------------------------------------------------------------------------

# Above this, copying the storage folder could outlast a request, so the action runs as a job.
_SPAWN_TEST_JOB_BYTES = 200 * 1024 * 1024


def _spawn_test_storage_bytes(config) -> int:
    """Size of the storage folder the action is about to copy: the session's copy
    when ending a test, the current mission's own when starting one."""
    from app.modmgr.mod_backups import dir_size
    from app.ops import cleanup_ops, spawn_test_ops

    active = spawn_test_ops.active_test(config)
    if active:
        path = Path(active.get("session_dir") or "") / spawn_test_ops.STORAGE_COPY
    else:
        info = cleanup_ops.find_storage(config)
        if not info:
            return 0
        path = info.path
    return dir_size(path) if path.is_dir() else 0


async def _run_spawn_test_action(state: WebState, title: str, kind: str, action) -> dict:
    """Run ``action(log) -> dict``: inline when the storage folder is small, as a job when not.

    Inline, a refusal is an HTTP status and the log lines ride along in the
    response; as a job, both land in the job log.
    """
    from app.ops.spawn_test_ops import SpawnTestError

    if await asyncio.to_thread(service_control.is_server_running, state.config):
        raise HTTPException(status_code=409, detail="Server is running. Stop it first.")

    size = await asyncio.to_thread(_spawn_test_storage_bytes, state.config)
    if size <= _SPAWN_TEST_JOB_BYTES:
        lines: list[str] = []
        try:
            result = await asyncio.to_thread(action, lines.append)
        except SpawnTestError as e:
            raise HTTPException(status_code=e.status, detail=e.message)
        await state.hub.broadcast({"type": "mods_changed"})
        return {**result, "log": lines}

    async def _job_coro(job: Job) -> dict:
        try:
            result = await asyncio.to_thread(action, job.log_line_threadsafe)
        except SpawnTestError as e:
            raise RuntimeError(e.message) from e
        await state.hub.broadcast({"type": "mods_changed"})
        return result

    try:
        job = state.jobs.start(title, kind, _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}


# Declared before the /{name}/spawn-test routes on purpose — the active test may
# belong to a mod that has been uninstalled since, so ending it is not per-mod.
@router.post("/spawn-test/end")
async def end_spawn_test(state: WebState = Depends(get_state)) -> dict:
    from app.ops import spawn_test_ops

    config = state.config
    return await _run_spawn_test_action(
        state,
        "End spawn test",
        "spawn_test_end",
        lambda log: spawn_test_ops.end_test(config, log),
    )


@router.get("/spawn-test/class-counts")
async def spawn_test_class_counts(state: WebState = Depends(get_state)) -> dict:
    """``{counts: {mod: n}}``: how many item classes each installed mod adds."""
    from app.modmgr.modlist import ModListError
    from app.ops import spawn_test_ops

    try:
        counts = await asyncio.to_thread(spawn_test_ops.class_counts, state.config)
    except ModListError as e:
        raise HTTPException(409, str(e))
    return {"counts": counts}


@router.get("/{name}/spawn-test")
async def spawn_test_info(name: str, state: WebState = Depends(get_state)) -> dict:
    """The class list, the RAM-based warning threshold, the active test (if any)
    and the storage folder a new test would copy."""
    from app.ops import cleanup_ops, spawn_test_ops

    config = state.config
    _require_listed_mod(config, name)
    # The first call per mod parses its PBOs; later ones hit the classname cache.
    classes = await asyncio.to_thread(spawn_test_ops.mod_classes, config, name)
    warn_above, warn_reason = await asyncio.to_thread(spawn_test_ops.spawn_count_warning)
    locations, map_size = await asyncio.to_thread(spawn_test_ops.mission_locations, config)
    return {
        "classes": classes,
        "warn_above": warn_above,
        "warn_reason": warn_reason,
        "active": spawn_test_ops.active_test(config),
        "storage_name": await asyncio.to_thread(cleanup_ops.storage_name, config),
        "locations": locations,
        "map_size": map_size,
    }


@router.post("/{name}/spawn-test")
async def start_spawn_test(
    name: str, body: SpawnTestStartRequest, state: WebState = Depends(get_state)
) -> dict:
    from app.ops import spawn_test_ops

    config = state.config
    _require_listed_mod(config, name)
    if body.mode not in ("near_player", "at_location", "only_x"):
        raise HTTPException(status_code=422, detail=f"Unknown spawn test mode: {body.mode!r}")
    if body.mode == "at_location" and (body.x is None or body.z is None):
        raise HTTPException(
            status_code=422, detail="A spawn test at a location needs both X and Z."
        )
    if body.mode == "only_x" and not body.scope:
        raise HTTPException(
            status_code=422,
            detail='An "Only X" spawn test needs a scope: '
            f"{', '.join(spawn_test_ops.ONLY_X_SCOPES)}.",
        )

    def _start(log) -> dict:
        warning = spawn_test_ops.count_warning_text(
            body.count, *spawn_test_ops.spawn_count_warning()
        )
        if warning:
            log(warning)
        if body.mode == "only_x":
            result = spawn_test_ops.start_only_x(
                config, name, body.classname, body.count, body.scope, log
            )
        elif body.mode == "at_location":
            result = spawn_test_ops.start_at_location(
                config, name, body.classname, body.count, body.x, body.z, body.location, log
            )
        else:
            result = spawn_test_ops.start_near_player(
                config, name, body.classname, body.count, log
            )
        return {**result, "warning": warning}

    return await _run_spawn_test_action(
        state, f"Start spawn test for {name}", "spawn_test_start", _start
    )


@router.get("/cached")
async def cached_mods(state: WebState = Depends(get_state)) -> dict:
    """Mods downloaded to the SteamCMD workshop folder but not yet installed
    (Cached tab). Scans the workshop content dir — NOT the learned Workshop IDs (workshop-ids.json) — so it
    reflects what's physically on disk and ready to copy in."""
    config = state.config

    def _load() -> list[dict]:
        installed = set(modlist.installed(config))
        cached_ids = modlist.workshop_ids(config)
        mod_dirs = {}
        out = []
        for name, wid in mod_ops.get_workshop_mods(config):
            display = mod_ops.ensure_at_prefix(name or f"[{wid}]")
            if display in installed:
                continue
            mod_dirs[display] = config.workshop_dir / wid
            out.append({"name": display, "id": wid})
        info_map = build_mod_info_map(mod_dirs, cached_ids)
        for m in out:
            m["info"] = _info_text(info_map.get(m["name"]))
        return out

    return {"mods": await asyncio.to_thread(_load)}


@router.get("/known")
async def known_mods(state: WebState = Depends(get_state)) -> dict:
    """All known mod names (seed + cached + workshop folder) not yet installed
    (Search tab)."""
    config = state.config

    def _load() -> list[dict]:
        installed = set(modlist.installed(config))
        return [
            {"name": name, "id": wid}
            for name, wid in mod_ops.list_known_mods(config)
            if name not in installed
        ]

    return {"mods": await asyncio.to_thread(_load)}


@router.get("/supported")
async def supported(state: WebState = Depends(get_state)) -> dict:
    """The mods zsm has Config and Map editors for (Install -> Supported)."""
    from collections import Counter

    from app.ops.supported_mods import supported_mods
    from app.web import editors

    config = state.config
    counts = Counter(spec.group for spec in editors.list_editors())
    return {"mods": await asyncio.to_thread(supported_mods, config, counts)}


@router.get("/expansion")
async def expansion_modules(state: WebState = Depends(get_state)) -> dict:
    """The DayZ-Expansion module picker (Install -> Expansion sub-tab).

    Catalog + install state only: no network, and no install endpoint of its own
    — the picker posts the closure it computes to ``/api/mods/install``.
    """
    config = state.config

    def _load() -> dict:
        # The mod list alone decides "installed": a folder nobody listed is not
        # loaded by the server, and offering to install it again is right.
        listed = modlist.installed(config)
        cached_ids = modlist.workshop_ids(config)
        installed_ids = set(mod_ops.resolve_names_to_ids(listed, cached_ids).values())

        def _entry(m: expansion.ExpansionModule) -> dict:
            entry = {
                "id": m.id,
                "name": expansion.module_name(m.id),
                "label": m.label,
                "blurb": m.blurb,
                # The full closure, not the direct edges: the picker locks rows
                # off this list, so the graph walk stays in the catalog.
                "requires": expansion.requires(m.id),
                "classes": m.classes,
                "installed": m.id in installed_ids,
            }
            # A *hint*, not a dependency: Expansion's own installation
            # cheatsheet packages these together, and the picker says so
            # without ticking anything. Kept as a separate key so the two
            # kinds of claim can never be read as one.
            pair = expansion.pairing(m.id)
            if pair:
                entry["paired_with"] = [
                    {"id": dep_id, "label": (expansion.get(dep_id) or m).label}
                    for dep_id in pair.with_ids
                ]
                entry["pairing_note"] = pair.note
            return entry

        frameworks = [_entry(m) for m in expansion.FRAMEWORKS]
        return {
            "modules": [_entry(m) for m in expansion.MODULES],
            "frameworks": frameworks,
            "installed": sorted(i for i in installed_ids if expansion.get(i)),
            "missing_deps": [
                {"id": f["id"], "name": f["name"]} for f in frameworks if not f["installed"]
            ],
        }

    return await asyncio.to_thread(_load)


def _drop_cached_copy(config, wid: str, log) -> None:
    """Delete one mod's workshop cache folder and its manifest entry, for a
    reinstall. A mod with no cached copy is left as it is."""
    from app.ops import cleanup_ops

    if not mods.is_workshop_id(wid):
        return
    cached = config.workshop_dir / wid
    if cached.is_dir():
        cleanup_ops.remove_workshop_mods(config, [cached], log=log)


async def run_mod_install(
    job: Job,
    state: WebState,
    *,
    named: list[str] = (),
    requested_ids: list[str] = (),
    id_names: dict[str, str] | None = None,
    install_deps: bool = True,
    configure_xml: bool = True,
    skip_installed: bool = False,
    reinstall: bool = False,
) -> dict:
    """The body of an install job: download and copy each mod, then the bikeys,
    the mod XML, the service files and the required-addon check.

    ``POST /api/mods/install`` and Quick setup's Apply both run it inside their
    own job. With *skip_installed*, a mod already in the mod list is not
    downloaded again, and the steps after the downloads still run.

    With *reinstall*, each requested mod's workshop cache copy is deleted
    before its download, so SteamCMD fetches every file again instead of
    trusting a copy it thinks is current. Dependencies added here keep their
    cache. The removal goes through ``cleanup_ops.remove_workshop_mods``,
    which also drops the item from the workshop manifest.
    """
    from app.ops import mod_rollback
    from app.ops.fileops import copy_bikey_files, copy_mod_to_server
    from app.server.service_ops import generate_service_files_async
    from app.traderplus.trader_common import (
        copy_traderplus_default_config,
        get_traderplus_config_dir,
    )

    config = state.config
    id_names = id_names or {}
    cached_ids = await asyncio.to_thread(modlist.workshop_ids, config)
    id_to_name = {wid: n for n, wid in cached_ids.items()}

    # (name, workshop_id) work list — id may be None until resolved.
    targets: list[tuple[str, str | None]] = []
    seen: set[str] = set()

    def _add_target(name: str, wid: str | None) -> None:
        if name not in seen:
            seen.add(name)
            targets.append((name, wid))

    # By-name → resolve id (cache/network).
    for name in named:
        job.log_line(f"Resolving workshop ID for {name}...")
        wid, _cached = await asyncio.to_thread(mod_ops.resolve_mod_workshop_id, config, name)
        _add_target(name, wid)

    # By-id → resolve a name (server cache / zsm's registry / id_names / @<id>).
    for wid in requested_ids:
        name = mod_ops.name_for_workshop_id(wid, id_to_name, hint=id_names.get(wid))
        if id_to_name.get(wid) != name:
            await asyncio.to_thread(modlist.remember_id, config, name, wid)
            cached_ids[name] = wid
            id_to_name[wid] = name
        _add_target(name, wid)

    # Only the mods the caller named are reinstalled, never the deps added below.
    reinstall_ids = {wid for _n, wid in targets if wid} if reinstall else set()

    # Missing dependencies (e.g. @CF for @TraderPlus).
    if install_deps:
        installing_ids = [wid for _n, wid in targets if wid]
        installed_names = list(await asyncio.to_thread(modlist.installed, config))
        installed_ids = set(mod_ops.resolve_names_to_ids(installed_names, cached_ids).values())
        for dep_id, dep_name in mod_ops.get_missing_deps_ex(config, installing_ids, installed_ids):
            dep_name = mod_ops.ensure_at_prefix(dep_name)
            job.log_line(f"Adding dependency {dep_name} ({dep_id}).")
            await asyncio.to_thread(modlist.remember_id, config, dep_name, dep_id)
            cached_ids[dep_name] = dep_id
            _add_target(dep_name, dep_id)

    # Quick setup's second attempt after a stop: a listed mod needs no new
    # download, but the steps after the loop still run for it.
    already: list[str] = []
    if skip_installed:
        listed = set(await asyncio.to_thread(modlist.installed, config))
        already = [name for name, _wid in targets if name in listed]
        targets[:] = [t for t in targets if t[0] not in listed]

    installed: list[str] = []
    skipped: list[str] = []
    # Re-installing an installed mod sets the old one aside; one
    # rollback generation for the whole job.
    generation = mod_rollback.Generation(config, log=job.log_line_threadsafe)
    for name, wid in targets:
        if job.cancel_event.is_set():
            job.log_line("Cancelled — skipping the remaining mods.")
            break
        if not wid:
            job.log_line(f"No workshop ID found for {name} — skipping.")
            skipped.append(name)
            continue
        if wid in reinstall_ids:
            await asyncio.to_thread(_drop_cached_copy, config, wid, job.log_line_threadsafe)
        job.log_line(f"Downloading {name} ({wid})...")
        ok = await download_mod_async(JobRunner(job), config, wid, name)
        if not ok:
            job.log_line(f"Failed to download {name}.")
            skipped.append(name)
            continue
        fresh_cached = await asyncio.to_thread(modlist.workshop_ids, config)
        copied = await asyncio.to_thread(
            copy_mod_to_server, config, name, fresh_cached,
            log=job.log_line_threadsafe, generation=generation,
        )
        if not copied:
            # Copy refuses an empty/absent workshop dir — recording the mod
            # as installed anyway leaves a mod folder that ships nothing.
            job.log_line(f"Not installing {name} — nothing was copied to the server.")
            skipped.append(name)
            continue
        await asyncio.to_thread(modlist.add, config, name)
        installed.append(name)

    done = [*installed, *already]

    # A job that installed nothing is a failed install, whatever its log says.
    # It used to finish "success" with an empty list, so a page full of
    # "No workshop ID found for @X — skipping." read as a completed install
    # and the mods looked present until someone listed the folders.
    if skipped and not done:
        raise RuntimeError("Nothing was installed. Skipped: " + ", ".join(skipped))

    svc_config = config
    if done:
        # Mods the catalog knows are server-only go to -serverMod=. Marked
        # through mutate_config (the only config write the web app has), and
        # the unit below is built from the config that returns.
        server_only = mod_ops.known_server_side(done, cached_ids)
        if server_only:
            from app.modmgr.mods import mark_server_side

            def _mark(cfg) -> None:
                for n in server_only:
                    mark_server_side(cfg, n, True)

            svc_config = await state.mutate_config(_mark)
            for n in server_only:
                job.log_line(
                    f"{n} is a server-side mod: it loads through -serverMod=, so players "
                    "don't need it. Change that on the Installed tab."
                )
        await asyncio.to_thread(
            copy_bikey_files, config, log=job.log_line_threadsafe,
        )
        # The merge below copies missing TraderPlus defaults itself
        # (`ensure_upstream_defaults`), so "already present" after it is only
        # news when the config was there before this job.
        tp_config_before = get_traderplus_config_dir(config) is not None
        if configure_xml:
            await asyncio.to_thread(pipeline._merge_xml_sync, _merge_all_ctx(job, config))
        else:
            job.log_line(
                "Skipping mod XML configuration (auto config off) — the mods' types/events/"
                "spawnabletypes are NOT in the mission yet. Run 'Re-configure all mod XML' on "
                "the Mods page (or `zsm run`) when you want them applied."
            )
        ok = await generate_service_files_async(JobRunner(job), svc_config)
        if not ok:
            job.log_line("Warning: service file install failed — run `zsm service install`.")

        if mod_ops.is_traderplus_installed(done, cached_ids):
            result = await asyncio.to_thread(copy_traderplus_default_config, config)
            if result == "copied":
                job.log_line("Copied TraderPlus default config to the profiles folder.")
            elif result == "exists" and tp_config_before:
                job.log_line("TraderPlus config already present — left as-is.")
            elif result == "failed":
                job.log_line(
                    "Warning: this TraderPlus version ships no default configs and the "
                    "download from GitHub failed. Use Reset TraderPlus JSON on the "
                    "Cleanup page to try again."
                )

    # Post-install: surface any required addons the newly-installed mods
    # declare in their PBOs but that aren't present. The scanner reports
    # addon class names (not workshop IDs) so we can't auto-install them —
    # we flag them for the user to resolve via the Conflicts tab.
    missing_addons: list[str] = []
    if done:
        try:
            items, _debug = await asyncio.to_thread(mod_conflict.check_required_addons, config)
            installed_set = set(done)
            missing_addons = [it.message for it in items if it.mod_name in installed_set]
            for msg in missing_addons:
                job.log_line(f"⚠ {msg}")
        except Exception as e:  # a scan hiccup must not fail the install
            job.log_line(f"Required-addon check skipped: {e}")

    await state.hub.broadcast({"type": "mods_changed"})
    result = {
        "installed": installed, "already": already, "skipped": skipped,
        "missing_addons": missing_addons,
    }
    # A running server loads its mods only at start, and nothing said so
    # (night test 2026-11-22). The completion toast shows `notice`.
    if installed and await asyncio.to_thread(service_control.is_server_running, config):
        result["notice"] = "The server is running: restart it to load the new mods."
        job.log_line(result["notice"])
    return result


@router.post("/install")
async def install_mods(body: ModInstallRequest, state: WebState = Depends(get_state)):
    named = [mod_ops.ensure_at_prefix(n) for n in body.names]
    requested_ids = [str(i).strip() for i in body.ids if str(i).strip()]
    id_names = {str(k): mod_ops.ensure_at_prefix(v) for k, v in body.id_names.items()}
    _refuse_unsafe_mod_refs([*named, *id_names.values()], requested_ids)

    async def _job_coro(job: Job) -> dict:
        return await run_mod_install(
            job, state,
            named=named, requested_ids=requested_ids, id_names=id_names,
            install_deps=body.install_deps,
            configure_xml=body.configure_xml,
            reinstall=body.reinstall,
        )

    try:
        job = state.jobs.start(
            "Reinstall mods" if body.reinstall else "Install mods", "mods_install", _job_coro
        )
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}


@router.post("/uninstall")
async def uninstall_mods_endpoint(body: ModUninstallRequest, state: WebState = Depends(get_state)):
    from app.server.service_ops import generate_service_files_async

    config = state.config
    names = list(body.names)
    remove_deps = body.remove_deps

    async def _job_coro(job: Job) -> dict:
        def _resolve_extra() -> list[str]:
            cached_ids = modlist.workshop_ids(config)
            removing_ids = list(mod_ops.resolve_names_to_ids(names, cached_ids).values())
            all_installed = modlist.installed(config)
            all_name_to_id = mod_ops.resolve_names_to_ids(all_installed, cached_ids)
            id_to_name = {wid: n for n, wid in all_name_to_id.items()}
            removable = mod_ops.get_removable_deps(removing_ids, set(all_name_to_id.values()))
            return [id_to_name[wid] for wid, _dep_name in removable if wid in id_to_name]

        extra = await asyncio.to_thread(_resolve_extra) if remove_deps else []
        to_remove = list(dict.fromkeys(names + extra))

        def _on_mod(mod_name: str) -> None:
            job.log_line_threadsafe(f"Removing {mod_name}...")

        await asyncio.to_thread(
            mod_ops.uninstall_mods,
            config,
            to_remove,
            on_mod=_on_mod,
            log=job.log_line_threadsafe,
        )
        ok = await generate_service_files_async(JobRunner(job), config)
        if not ok:
            job.log_line("Warning: service file install failed — run `zsm service install`.")
        await state.hub.broadcast({"type": "mods_changed"})
        return {"removed": to_remove}

    try:
        job = state.jobs.start("Uninstall mods", "mods_uninstall", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}


# ── Per-mod detail ───────────────────────────────────────────────────
#
# KEEP `GET /{ref}` LAST IN THIS FILE. FastAPI matches routes in declaration
# order, so a single-segment catch-all declared any earlier swallows /help,
# /search, /updates, /conflicts, /load-order, /missing-deps, /xml-status,
# /cached and /known. Same ordering rule /xml/merge vs /{name}/xml/merge relies
# on; tests/test_web_mods.py::test_detail_route_does_not_shadow_static_routes
# guards it.


def _detail_sync(config, ref: str) -> dict | None:
    """Everything the web mod page shows for one mod, in one payload.

    Composed from the same sources the Mods page joins client-side (list, XML
    status, load order, cached updates) plus the mod's own mod.cpp/meta.cpp.
    """
    from app.modmgr.mod_backups import dir_size
    from app.modmgr.mod_info import read_mod_info, workshop_url

    mod = mod_ops.resolve_mod_ref(config, ref)
    if mod is None:
        return None

    name = mod.name
    enabled = bool(name) and name not in modlist.disabled(config)
    installed_dir = config.server_dir_path / name if name else None
    if installed_dir is not None and not installed_dir.is_dir():
        installed_dir = None

    # mod.cpp/meta.cpp from the installed copy, falling back to the downloaded
    # workshop copy (same fallback as _list_mods_sync).
    meta_dir = installed_dir or mod.workshop_path
    meta = read_mod_info(meta_dir) if meta_dir else None

    cached_ids = modlist.workshop_ids(config)
    info_map = build_mod_info_map({name: meta_dir}, cached_ids) if (name and meta_dir) else {}

    xml = _xml_status_sync(config).get(name) if name else None
    order, deps = _load_order_sync(config)
    requires = deps.get(name, []) if name else []
    required_by = sorted(m for m, d in deps.items() if name and name in d)

    update = None
    if mod.workshop_id:
        for u in workshop_api.load_detected_updates(config.paths.mod_updates):
            if u.mod_id == mod.workshop_id:
                update = _update_to_dict(u)
                break

    actionable = bool(xml and xml.get("actionable"))
    return {
        "name": name,
        "id": mod.workshop_id,
        "workshop_url": workshop_url(mod.workshop_id) if mod.workshop_id else "",
        "listed": mod.listed,
        "installed": mod.installed,
        "enabled": enabled,
        "cached": mod.cached,
        "workshop_path": str(mod.workshop_path) if mod.workshop_path else "",
        "size_bytes": dir_size(installed_dir) if installed_dir else 0,
        "cached_size_bytes": dir_size(mod.workshop_path) if mod.workshop_path else 0,
        "meta": dataclasses.asdict(meta) if meta else None,
        "timestamps": _mod_timestamps(config, mod, installed_dir, meta),
        "info": _info_text(info_map.get(name)),
        "order": order.index(name) if name and name in order else None,
        "order_total": len(order),
        "requires": requires,
        "required_by": required_by,
        "xml": xml,
        "update": update,
        "traderplus": mod_ops.is_traderplus_installed([name], cached_ids) if name else False,
        "credit": MOD_CREDITS.get(mod.workshop_id or ""),
        # Which buttons the page offers — derived server-side so the frontend
        # can't disagree with what the endpoints actually allow (e.g. a scoped
        # XML merge is refused for a disabled mod).
        "actions": {
            "enable": mod.listed and not enabled,
            "disable": mod.listed and enabled,
            "uninstall": mod.listed,
            "reconfigure": mod.installed and enabled and actionable,
            "install": (not mod.listed) and bool(mod.workshop_id),
            "reinstall": mod.listed and bool(mod.workshop_id),
            "remove_cache": mod.cached,
        },
    }


def _mod_files_base(config, mod) -> tuple[Path | None, str]:
    """The folder the mod page browses: the installed copy, else the cache.

    Same fallback `_detail_sync` uses for mod.cpp/meta.cpp — an uninstalled but
    downloaded mod still has files worth looking at.
    """
    if mod.name:
        installed = config.server_dir_path / mod.name
        if installed.is_dir():
            return installed, "installed"
    if mod.workshop_path and mod.workshop_path.is_dir():
        return mod.workshop_path, "cache"
    return None, ""


def _mod_files_sync(config, ref: str) -> dict | None:
    """One mod's folder listing, with the XML report joined onto its XML files."""
    from app.xml.xml_report import build_mod_xml_report

    mod = mod_ops.resolve_mod_ref(config, ref)
    if mod is None:
        return None

    base, source = _mod_files_base(config, mod)
    if base is None:
        return {"mod": mod.name, "root": "", "source": "", "files": [], "truncated": False}

    files, truncated = mod_ops.list_mod_files(base)

    # Only the installed copy is what the merge reads, and the report is not
    # cheap (it parses every mod's XML) — so join only when there is something
    # to join onto.
    if source == "installed" and mod.name and any(f["language"] == "xml" for f in files):
        by_rel: dict[str, dict] = {}
        for row in build_mod_xml_report(config)["files"]:
            if row["mod"] != mod.name:
                continue
            rel = row["source"].split("/", 1)[-1] if "/" in row["source"] else row["source"]
            by_rel[rel] = {
                "status": row["status"],
                "kind": row["kind"],
                "target": row["target"],
                "custom_file": row["custom_file"],
            }
        for f in files:
            hit = by_rel.get(f["path"])
            if hit:
                f["xml"] = hit

    return {
        "mod": mod.name,
        "root": str(base),
        "source": source,
        "files": files,
        "truncated": truncated,
    }


# ---------------------------------------------------------------------------
# Rollback — the mod versions the last update replaced (TODO C5)
#
# Declared before the /{ref} catch-all below, like every other fixed path here.
# ---------------------------------------------------------------------------


@router.get("/rollback")
async def get_mod_rollback(state: WebState = Depends(get_state)) -> dict:
    """``{available, taken_at, mods, size_bytes, offer, reason}``.

    `offer` is the half that matters: it is true only when a stash exists
    **and** the latest run never became joinable. A rollback button on a
    healthy server is an invitation to undo an update that worked.
    """
    from app.ops import mod_rollback

    return await asyncio.to_thread(mod_rollback.should_offer_rollback, state.config)


@router.post("/rollback/restore")
async def restore_mod_rollback(state: WebState = Depends(get_state)) -> dict:
    state.jobs.refuse_while_busy()
    from app.ops import mod_rollback

    if await asyncio.to_thread(service_control.is_server_running, state.config):
        raise HTTPException(status_code=409, detail="Server is running. Stop it first.")

    lines: list[str] = []
    result = await asyncio.to_thread(mod_rollback.restore, state.config, log=lines.append)
    if not result.ok:
        raise HTTPException(status_code=409, detail=result.message)
    await state.hub.broadcast({"type": "mods_changed"})
    return {"ok": True, "restored": result.restored, "message": result.message, "log": lines}


@router.post("/rollback/discard")
async def discard_mod_rollback(state: WebState = Depends(get_state)) -> dict:
    state.jobs.refuse_while_busy()
    from app.ops import mod_rollback

    discarded = await asyncio.to_thread(mod_rollback.discard, state.config)
    await state.hub.broadcast({"type": "mods_changed"})
    return {"ok": True, "discarded": discarded}


@router.get("/{ref}/files")
async def mod_files(ref: str, state: WebState = Depends(get_state)) -> dict:
    """List the files in a mod's folder (installed copy, else workshop cache)."""
    data = await asyncio.to_thread(_mod_files_sync, state.config, ref)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Unknown mod: {ref}")
    return data


@router.get("/{ref}/file")
async def mod_file(ref: str, path: str, state: WebState = Depends(get_state)) -> dict:
    """Read one text file out of a mod's folder — read-only, always.

    A mod dir belongs to SteamCMD and the next workshop update overwrites it,
    so nothing here is editable (same rule as the Mod XML page's source
    viewer). Only the extensions in `VIEWABLE_MOD_FILE_LANGUAGES` are served.
    """

    def _load() -> dict:
        mod = mod_ops.resolve_mod_ref(state.config, ref)
        if mod is None:
            raise HTTPException(status_code=404, detail=f"Unknown mod: {ref}")
        base, _source = _mod_files_base(state.config, mod)
        resolved = mod_ops.mod_file_path(base, path) if base else None
        if resolved is None:
            raise HTTPException(status_code=404, detail=f"Unknown file: {path}")

        language = mod_ops.mod_file_language(resolved.name)
        if not language:
            raise HTTPException(status_code=400, detail=f"Not a viewable file: {resolved.name}")

        size = resolved.stat().st_size
        # Only what is shown is read: a mod can ship a .txt of any size, and
        # reading it whole to cut it cost that much per request (plans/68 CON-14).
        with resolved.open("rb") as f:
            data = f.read(mod_ops.MAX_MOD_FILE_BYTES)
        return {
            "path": path,
            "name": resolved.name,
            "language": language,
            "size": size,
            "shown": len(data),
            "truncated": size > mod_ops.MAX_MOD_FILE_BYTES,
            "content": data.decode("utf-8", errors="replace"),
        }

    return await asyncio.to_thread(_load)


@router.get("/{ref}")
async def mod_detail(ref: str, state: WebState = Depends(get_state)) -> dict:
    """One mod, by name (`@CF`) or workshop ID (`1559212036`) — the mod page."""
    detail = await asyncio.to_thread(_detail_sync, state.config, ref)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Unknown mod: {ref}")
    return detail
