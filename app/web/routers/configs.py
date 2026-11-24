"""GET /api/configs, GET/PUT /api/configs/{id}, POST /api/configs/{id}/restore,
GET /api/configs/{id}/download, POST /api/configs/{id}/upload,
GET /api/tp-price* (TraderPlus price DB — paginated, too big for the generic
table contract), and /api/tp-price/zone-offers (a zone mod's items as a
category, plans/48 D)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.parsers.battleye_cfg import BeserverCfgInUse
from app.shared.config import DayzConfig
from app.web import editors, redact
from app.web.deps import get_state, require_session
from app.web.multipliers import check_multipliers
from app.web.downloads import file_attachment
from app.web.editors._raw import RawTarget
from app.web.schemas import TpPriceMultiplierRequest, TpPriceUpdateRequest, TpZoneOfferRequest
from app.web.state import ConfigLocked, WebState
from app.web.uploads import MAX_UPLOAD_BYTES, read_bounded

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["configs"], dependencies=[Depends(require_session)])


# origin (from service_control.build_launch_command_info) -> web nav target.
_RUNCOMMAND_LINKS: dict[str, dict] = {
    "mods": {"route": "/mods", "label": "Mods"},
    "config_path": {"route": "/configs/runcommand", "label": "Run Command"},
    "paths": {"route": "/configs/runcommand", "label": "Run Command"},
    "flags": {"route": "/configs/runcommand", "label": "Run Command"},
    "port": {"route": "/configs/runcommand", "label": "Run Command"},
}


@router.get("/runcommand")
async def get_runcommand(state: WebState = Depends(get_state)) -> dict:
    """Generated DayZServer launch command + where each piece is changed.

    Shared by the Dashboard card and the Run Command editor view."""
    from app.server.service_control import build_launch_command_info

    def _build() -> dict:
        info = build_launch_command_info(state.config)
        sources: list[dict] = []
        for part in info["parts"]:
            link = _RUNCOMMAND_LINKS.get(part.get("origin"))
            part["link"] = link
            if link and link not in sources:
                sources.append(link)
        info["sources"] = sources
        return info

    return await asyncio.to_thread(_build)


def _raw_targets(spec, config) -> list:
    return spec.raw_targets(config) if spec.raw_targets else []


def _capture_target(targets: list) -> str:
    """The mission file behind this editor, when a merge config owns it.

    Drives the editor's "Save as manual XML file" button. Derived here rather
    than listed in the webui so the two can't disagree about which files a mod
    merges into: `kind_for_target` is the same map `xml_manual` uses, and it is
    what leaves cfgeconomycore.xml out (its `<ce folder="custom">` block is
    generated, so a `@user:` marker block in it would be a second mechanism).
    """
    from app.xml.xml_manual import MERGE_KIND, kind_for_target

    if len(targets) != 1:
        return ""
    name = targets[0].name
    return name if kind_for_target(name) != MERGE_KIND else ""


def _pick_target(targets: list, name: str | None):
    """Match `name` against the editor's advertised target names only — never
    used to build a path, so it can't be abused for traversal. Default to the
    first target when no name is given."""
    if not targets:
        return None
    if not name:
        return targets[0]
    return next((t for t in targets if t.name == name), None)


def _display_path(path: Path, config: DayzConfig) -> str:
    """A target's path for display: server-dir-relative when it is under the
    server dir, else absolute. Separators are normalized to `/` the way
    `xml_manual.normalize_source` does — a bind-mounted server dir is shared
    between Windows and Linux, so a path written on one is read on the other.
    """
    try:
        rel = Path(path).relative_to(config.server_dir_path)
    except (TypeError, ValueError):
        rel = Path(path)
    return PurePosixPath(str(rel).replace("\\", "/")).as_posix()


def _files_payload(targets: list, config: DayzConfig) -> list[dict]:
    """`targets` with the path and language attached — what the Configs page's
    cards name, and what its preview drawer picks a target from."""
    return [
        {"name": t.name, "path": _display_path(t.path, config), "language": t.language}
        for t in targets
    ]


# ---------------------------------------------------------------------------
# Bespoke pages
# ---------------------------------------------------------------------------
#
# Mod XML, Types.xml and TraderPlus Prices own their own route + endpoints
# instead of an EditorSpec — there is no form/table/text payload to register for
# them. They are still `/configs/<id>` URLs (matched by earlier declarations in
# webui/src/App.jsx), so the Configs page renders them as cards like everything
# else; `kind: "custom"` is what tells it they have no Basic/Raw/Interactive
# tabs, just one Edit button.
#
# `targets` is populated only where a SINGLE file backs the page, which is what
# the card's preview eye needs. types.xml is many files (its own page is the
# editor over them) and Mod XML is a report over a whole mod tree, so both get
# no eye rather than an arbitrary first file.
#
# `raw_targets` are the files the drawer's Raw and Versions tabs open and
# write (BespokeEditorDrawer.jsx). Types lists every types file, Mod XML none.


def _tp_price_targets(config: DayzConfig) -> list[RawTarget]:
    from app.traderplus.traderplus_json import get_price_config_path

    path = get_price_config_path(config)
    return [RawTarget(name=path.name, path=path, language="json")] if path else []


def _types_targets(config: DayzConfig) -> list[RawTarget]:
    """Every file the Types editor reads, labelled by its path in the mission
    (`db/types.xml`, `custom/<file>.xml`), since two folders can hold the same
    file name. The label is only matched, never joined onto a path."""
    from app.web.editors._paths import mission_dir
    from app.xml.types_xml import scan_types_files

    m_dir = mission_dir(config)
    if not m_dir:
        return []
    return [
        RawTarget(name=path.relative_to(m_dir).as_posix(), path=path, language="xml")
        for path in scan_types_files(m_dir)
    ]


def _types_exists(config: DayzConfig) -> bool:
    from app.web.editors._paths import mission_dir
    from app.xml.types_xml import scan_types_files

    m_dir = mission_dir(config)
    return bool(m_dir and scan_types_files(m_dir))


def _mission_exists(config: DayzConfig) -> bool:
    from app.web.editors._paths import mission_dir

    return mission_dir(config) is not None


# `files_note` stands in for the path line on a page no single file backs — the
# card would otherwise have nothing to say about what it covers.
_BESPOKE_EDITORS: list[dict] = [
    {
        "id": "mod-xml",
        "title": "Custom",
        "group": "Mission",
        "exists": _mission_exists,
        "targets": None,
        "files_note": "Auto configured XML and/or your own defined XML",
    },
    {
        "id": "types",
        "title": "Types",
        "group": "Mission",
        "exists": _types_exists,
        "targets": None,
        "raw_targets": _types_targets,
        "files_note": "Includes types.xml + custom xml's",
    },
    {
        "id": "tp-price",
        "title": "TraderPlus Prices",
        "group": "TraderPlus",
        "exists": lambda config: bool(_tp_price_targets(config)),
        "targets": _tp_price_targets,
        "raw_targets": _tp_price_targets,
    },
]

_BESPOKE_BY_ID: dict[str, dict] = {entry["id"]: entry for entry in _BESPOKE_EDITORS}


def _bespoke_payload(entry: dict, config: DayzConfig) -> dict:
    """A bespoke editor's card on the Configs page. Its preview eye and file
    list come from `targets`, never from the drawer's `raw_targets`."""
    targets = entry["targets"](config) if entry["targets"] else []
    return {
        "id": entry["id"],
        "title": entry["title"],
        "group": entry["group"],
        "kind": "custom",
        "exists": bool(entry["exists"](config)),
        "raw": bool(targets),
        "targets": [t.name for t in targets],
        "files": _files_payload(targets, config),
        "files_note": entry.get("files_note", ""),
    }


def _targets_for(editor_id: str, config: DayzConfig) -> list | None:
    """Raw targets for a registry editor OR a bespoke page: what the Raw tab
    reads and writes, a Download serves, an Upload replaces and Versions lists.

    None means the id has no raw view at all: an unknown id, a registry editor
    with no `raw_targets` (`runcommand`, whose fields live in zsm's own
    config.json rather than a server file), or Mod XML, a report over a whole
    mod tree. An empty *list* is the other case — the editor has a file, but
    the path doesn't resolve on this install yet.
    """
    spec = editors.get_editor(editor_id)
    if spec is not None:
        return _raw_targets(spec, config) if spec.raw_targets else None
    entry = _BESPOKE_BY_ID.get(editor_id)
    if entry is None or entry.get("raw_targets") is None:
        return None
    return entry["raw_targets"](config)


@router.get("/configs")
async def list_configs(state: WebState = Depends(get_state)) -> list[dict]:
    config = state.config

    def _load() -> list[dict]:
        out = []
        for s in editors.list_editors():
            targets = _raw_targets(s, config)
            out.append(
                {
                    "id": s.id,
                    "title": s.title,
                    "group": s.group,
                    "kind": s.kind,
                    "exists": s.exists(config),
                    "raw": bool(targets),
                    "targets": [t.name for t in targets],
                    "files": _files_payload(targets, config),
                }
            )
        out.extend(_bespoke_payload(entry, config) for entry in _BESPOKE_EDITORS)
        return out

    return await asyncio.to_thread(_load)


@router.get("/configs/{editor_id}/meta")
async def get_config_meta(editor_id: str, state: WebState = Depends(get_state)) -> dict:
    """Tab metadata for the editor shell — cheap (no spec.load), so the Raw
    tab stays reachable even when the structured load would fail on a broken
    file."""
    spec = editors.get_editor(editor_id)
    bespoke = _BESPOKE_BY_ID.get(editor_id)
    if spec is None and bespoke is None:
        raise HTTPException(status_code=404, detail=f"Unknown config: {editor_id!r}")

    def _bespoke_meta() -> dict:
        # The bespoke drawer's tabs: its own Edit, then Raw and Versions.
        targets = _targets_for(editor_id, state.config) or []
        return {
            "id": editor_id,
            "title": bespoke["title"],
            "group": bespoke["group"],
            "kind": "custom",
            "raw": bool(targets),
            "exists": bool(bespoke["exists"](state.config)),
            "targets": [t.name for t in targets],
        }

    if spec is None:
        return await asyncio.to_thread(_bespoke_meta)

    def _meta() -> dict:
        targets = _raw_targets(spec, state.config)
        return {
            "id": spec.id,
            "title": spec.title,
            "group": spec.group,
            "kind": spec.kind,
            "raw": bool(targets),
            # False when the file is not on the server yet (most often a mod
            # that is not installed): the shell turns Raw and Versions off.
            "exists": bool(spec.exists(state.config)),
            "targets": [t.name for t in targets],
            "capture_target": _capture_target(targets),
            # Only the files zsm backs up during a merge have one to restore
            # from. The button stays rendered where this is False (removing it
            # would be removing UI) but disabled, with the reason on hover.
            "restore": spec.restore is not None,
            # A list editor with a server-side "Recently removed" list.
            "removed": spec.entry_names is not None,
        }

    return await asyncio.to_thread(_meta)


@router.get("/configs/{editor_id}/raw")
async def get_config_raw(
    editor_id: str, target: str | None = None, state: WebState = Depends(get_state)
) -> dict:
    """Read a config file's text.

    Serves the bespoke pages too (via `_targets_for`), so the Configs page's
    preview eye and the bespoke drawers' Raw tab need no second endpoint.
    """
    from app.web.editors._raw import load_raw

    def _load() -> dict:
        # Resolved in the thread, not before it: a target getter reads
        # serverDZ.cfg to find the mission, and the 404 is worth no less than
        # the read that follows it.
        targets = _targets_for(editor_id, state.config)
        if targets is None:
            raise HTTPException(status_code=404, detail=f"{editor_id!r} has no raw editor")
        tgt = _pick_target(targets, target)
        if tgt is None:
            raise HTTPException(status_code=400, detail=f"Unknown raw target: {target!r}")
        return {
            "name": tgt.name,
            "language": tgt.language,
            "content": load_raw(tgt.source),
            "targets": [t.name for t in targets],
        }

    return await asyncio.to_thread(_load)


@router.put("/configs/{editor_id}/raw")
async def update_config_raw(
    editor_id: str, body: dict = Body(...), state: WebState = Depends(get_state)
) -> dict:
    target_name = body.get("target")
    content = body.get("content")
    # A missing content is a bad request, never an empty file: a cfg target
    # with no line to check would be written with nothing in it.
    if not isinstance(content, str):
        raise HTTPException(status_code=422, detail="Send the file's text as content.")

    await _save_raw_target(editor_id, target_name, content, state)
    await state.hub.broadcast({"type": "configs_changed"})
    return {"ok": True}


async def _save_raw_target(editor_id: str, target_name: str | None, content: str, state: WebState) -> str:
    """The Raw tab's save and an Upload, for a registry or a bespoke editor:
    check, snapshot and write the target, and save what it carries into zsm's
    own config (``RawTarget.adopt``) in the same ``mutate_config``, so a
    refused file changes neither. Returns the target's name. 404 for an
    editor with no raw targets."""
    from app.web.editors._raw import check_raw, save_raw, write_raw

    def _resolve() -> RawTarget:
        targets = _targets_for(editor_id, state.config)
        if targets is None:
            raise HTTPException(status_code=404, detail=f"{editor_id!r} has no raw editor")
        tgt = _pick_target(targets, target_name)
        if tgt is None:
            raise HTTPException(status_code=400, detail=f"Unknown raw target: {target_name!r}")
        return tgt

    def _adopt_and_write(cfg: DayzConfig):
        check_raw(tgt.language, content, validate=tgt.validate, guard=tgt.guard)
        tgt.adopt(cfg, content)
        return lambda: write_raw(tgt.path, content)

    try:
        tgt = await asyncio.to_thread(_resolve)
        if tgt.adopt is None:
            await asyncio.to_thread(
                save_raw, tgt.path, tgt.language, content, validate=tgt.validate, guard=tgt.guard,
            )
        else:
            await state.mutate_config(_adopt_and_write)
    except HTTPException:
        raise
    except BeserverCfgInUse as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return tgt.name


@router.get("/configs/{editor_id}/download")
async def download_config_raw(
    editor_id: str, target: str | None = None, state: WebState = Depends(get_state)
):
    """Hand a config file to the browser as a download (TODO G1c).

    Resolves through `_targets_for` — the same lookup the raw *read* uses, so
    the bespoke pages get it too and, more importantly, **the path comes from
    the registry and never from the query string**. `target` is matched
    against a target's `name`, which is a path-free label; it is never joined
    onto a directory. Same discipline as the backup and bundle downloads, and
    the reason `downloads.py` deliberately offers no join-a-name helper.
    """
    def _resolve() -> RawTarget:
        targets = _targets_for(editor_id, state.config)
        if targets is None:
            raise HTTPException(status_code=404, detail=f"{editor_id!r} has no raw editor")
        tgt = _pick_target(targets, target)
        if tgt is None:
            raise HTTPException(status_code=400, detail=f"Unknown raw target: {target!r}")
        return tgt

    tgt = await asyncio.to_thread(_resolve)
    return file_attachment(tgt.source, tgt.name)


@router.post("/configs/{editor_id}/upload")
async def upload_config_raw(
    editor_id: str,
    request: Request,
    target: str | None = None,
    state: WebState = Depends(get_state),
) -> dict:
    """Replace a config file with an uploaded one (TODO G1c).

    The body is the file itself, not a multipart form. One file and one
    already-named destination need no form, and multipart would mean adding
    `python-multipart` to every install for a single button. It also means no
    client-supplied filename exists anywhere near a path: the destination
    comes from the registry, the upload only carries bytes.

    Resolved through `_targets_for`, like the raw PUT, so the bespoke drawers'
    Raw tab can upload too.

    Goes through `save_raw`, so a malformed file is rejected **before**
    anything is written and the version it would have replaced is snapshotted
    into `config_versions` first. That is the whole reason to route an upload
    through the editor rather than treat it as a file drop.
    """
    if await asyncio.to_thread(_targets_for, editor_id, state.config) is None:
        raise HTTPException(status_code=404, detail=f"{editor_id!r} has no raw editor")

    raw = await read_bounded(request, MAX_UPLOAD_BYTES)
    if not raw.strip():
        raise HTTPException(status_code=400, detail="That file is empty.")
    try:
        # utf-8-**sig**: this file has probably been round-tripped through an
        # FTP client and a Windows editor, which is exactly what G1c is for,
        # and Notepad adds a BOM. Dropping it writes the same encoding zsm's
        # own editor produces.
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400, detail="That file is not UTF-8 text — config files must be."
        )

    name = await _save_raw_target(editor_id, target, content, state)
    await state.hub.broadcast({"type": "configs_changed"})
    return {"ok": True, "name": name, "bytes": len(raw)}


@router.get("/configs/{editor_id}")
async def get_config(
    editor_id: str, request: Request, state: WebState = Depends(get_state)
) -> dict:
    spec = editors.get_editor(editor_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Unknown config: {editor_id!r}")
    try:
        body = await asyncio.to_thread(spec.load, state.config)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Viewer-level route; the passwords in serverDZ.cfg and beserver_x64.cfg
    # are admin-only. See app/web/redact.py.
    redact.mark_secrets(body)
    return body if redact.is_admin(request) else redact.redact_form(body)


@router.put("/configs/{editor_id}")
async def update_config(editor_id: str, body: dict = Body(...), state: WebState = Depends(get_state)) -> dict:
    """Save one config editor's form, table or text, then keep the file it
    replaced as a version and tell every tab that configs changed."""
    spec = editors.get_editor(editor_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Unknown config: {editor_id!r}")

    if spec.kind == "form":
        payload = body.get("values", {})
    elif spec.kind == "table":
        payload = body.get("rows", [])
    else:
        payload = body.get("content", "")

    # Every editor save keeps the file it replaced as a version, so a row
    # removed by mistake can be restored from the editor's Versions tab. Most
    # savers snapshot on their own; this is the one place that guarantees it
    # for all of them, and it runs only after the save succeeded, so a refused
    # save still leaves no version (config_versions.keep_replaced).
    from app.shared.config_versions import contents_of, keep_replaced

    # A failure to read the old files never blocks the save itself: the save
    # then reports its own error, and only the extra version is missing.
    def _read_before() -> dict:
        try:
            return contents_of(t.path for t in _raw_targets(spec, state.config))
        except Exception:  # noqa: BLE001
            log.warning("could not read %s before its save", editor_id, exc_info=True)
            return {}

    before = await asyncio.to_thread(_read_before)
    try:
        if spec.mutates_app_config:
            # config.json-backed editor: field mutation must go through the
            # single config-save path (fresh load under lock + broadcast).
            await state.mutate_config(lambda cfg: spec.save(cfg, payload))
        else:
            await asyncio.to_thread(spec.save, state.config, payload)
    except ConfigLocked as e:
        # The Run Command editor writes locked path fields; refuse with 403
        # before the generic 400 below turns it into a bad-request.
        raise HTTPException(status_code=403, detail=str(e))
    except BeserverCfgInUse as e:
        # The BattlEye editor while the server runs: a conflict with the
        # server's state, like a restore while it runs, not a bad request.
        raise HTTPException(status_code=409, detail=str(e))
    except editors.SaveElsewhere as e:
        # Still a 400; `go` lets the editor offer the page where it belongs.
        return JSONResponse(status_code=400, content={"detail": str(e), "go": e.go})
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # The file is written by now, so a failed extra version is logged, never
    # reported as a failed save the admin would retry.
    try:
        await asyncio.to_thread(keep_replaced, before)
    except Exception:  # noqa: BLE001
        log.warning("could not keep the version %s replaced", editor_id, exc_info=True)
    await state.hub.broadcast({"type": "configs_changed"})
    return {"ok": True}


def _editor_target(editor_id: str, target: str | None, config: DayzConfig) -> RawTarget:
    """The file an editor's Versions tab or Recently removed list is about:
    one of its own raw targets, matched by name (never joined onto a path).
    A bespoke editor's targets count too (`_targets_for`)."""
    targets = _targets_for(editor_id, config)
    if targets is None:
        if editors.get_editor(editor_id) is None:
            raise HTTPException(status_code=404, detail=f"Unknown config: {editor_id!r}")
        targets = []
    tgt = _pick_target(targets, target)
    if tgt is None:
        raise HTTPException(status_code=404, detail=f"{editor_id!r} has no file here yet")
    return tgt


@router.get("/configs/{editor_id}/versions")
async def config_versions_list(
    editor_id: str, target: str | None = None, state: WebState = Depends(get_state)
) -> dict:
    """The saved versions of one editor's file, newest first: the editor's own
    Versions tab. Diff and restore go through /api/versions/diff and
    /api/versions/restore with the entry's ``file_name`` and ``source_path``."""
    from app.web.routers.analysis import version_entries

    def _run() -> dict:
        tgt = _editor_target(editor_id, target, state.config)
        return {"file": tgt.name, "entries": version_entries(state.config, tgt.path.name, str(tgt.path))}

    return await asyncio.to_thread(_run)


@router.get("/configs/{editor_id}/removed")
async def config_removed_entries(
    editor_id: str, target: str | None = None, state: WebState = Depends(get_state)
) -> dict:
    """A list editor's "Recently removed": names its saved versions list that
    the live file does not, newest removal first (config_versions.removed_names).
    Putting one back is the editor's own add and save."""
    from app.shared.config_versions import removed_names

    spec = editors.get_editor(editor_id)
    if spec is None or spec.entry_names is None:
        raise HTTPException(status_code=404, detail=f"{editor_id!r} keeps no removed list")

    def _run() -> dict:
        tgt = _editor_target(editor_id, target, state.config)
        return {"items": removed_names(tgt.path, spec.entry_names, store=state.config.paths.versions_dir)}

    try:
        return await asyncio.to_thread(_run)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/configs/ignorelist/common-items")
async def ignorelist_common_items() -> dict:
    """Quick-add pool for the Ignore List interactive view — common containers
    and base-building pieces. The frontend filters out items already in the
    list."""
    from app.xml.ignorelist_xml import COMMON_BASE_BUILDING_ITEMS

    return {"items": list(COMMON_BASE_BUILDING_ITEMS)}


@router.get("/configs/weather/presets")
async def weather_presets() -> dict:
    """Pre-tuned weather profiles for the cfgweather.xml interactive view."""
    from app.xml.weather_xml import WEATHER_PRESETS

    return {
        "presets": [
            {"name": name, "description": info.get("description", "")}
            for name, info in WEATHER_PRESETS.items()
        ]
    }


@router.post("/configs/weather/apply-preset")
async def weather_apply_preset(body: dict = Body(...), state: WebState = Depends(get_state)) -> dict:
    """Rebuild cfgweather.xml from a named preset (generates the file from
    scratch; a version snapshot is taken first if the file exists)."""
    from app.shared.config_versions import snapshot
    from app.web.editors._paths import mission_dir
    from app.xml.weather_xml import WEATHER_PRESETS, apply_preset

    name = body.get("name")
    if name not in WEATHER_PRESETS:
        raise HTTPException(status_code=400, detail=f"Unknown weather preset: {name!r}")

    def _apply() -> None:
        m_dir = mission_dir(state.config)
        if m_dir is None:
            raise HTTPException(status_code=400, detail="Could not determine mission folder from serverDZ.cfg")
        path = m_dir / "cfgweather.xml"
        if path.is_file():
            snapshot(path, trigger="editor")
        path.parent.mkdir(parents=True, exist_ok=True)
        apply_preset(path, name)

    try:
        await asyncio.to_thread(_apply)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    await state.hub.broadcast({"type": "configs_changed"})
    return {"ok": True}


@router.post("/configs/{editor_id}/restore")
async def restore_config(editor_id: str, state: WebState = Depends(get_state)) -> dict:
    spec = editors.get_editor(editor_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Unknown config: {editor_id!r}")
    if spec.restore is None:
        raise HTTPException(status_code=404, detail=f"{editor_id} has no restore support")

    restored = await asyncio.to_thread(spec.restore, state.config)
    if restored:
        await state.hub.broadcast({"type": "configs_changed"})
    return {"restored": bool(restored)}


# ---------------------------------------------------------------------------
# TraderPlus price DB — bespoke, paginated (can be thousands of rows)
# ---------------------------------------------------------------------------


def _product_row(cat_idx: int, cat_name: str, prod_idx: int, product) -> dict:
    row = {
        "id": f"{cat_idx}:{prod_idx}",
        "category": cat_name,
        "classname": product.classname,
        "coefficient": product.coefficient,
        "max_stock": product.max_stock,
        "trade_quantity": product.trade_quantity,
        "buy_price": product.buy_price,
        "sell_price": product.sell_price,
    }
    if product.unreadable:
        # `classname` is the line as the file has it; the numbers are no one's.
        row["unreadable"] = True
    return row


# The page's Trade filter: which directions a product is open in. A price of -1
# turns that direction off (TraderPlus), so "buy" is every product a player can buy.
_TP_TRADE_FILTERS = {
    "buy": lambda p: p.buy_price != -1,
    "sell": lambda p: p.sell_price != -1,
    "buy_only": lambda p: p.buy_price != -1 and p.sell_price == -1,
    "sell_only": lambda p: p.buy_price == -1 and p.sell_price != -1,
    "off": lambda p: p.buy_price == -1 and p.sell_price == -1,
}


@router.get("/tp-price")
async def tp_price_list(
    q: str = "",
    category: str = "",
    trade: str = "",
    # A negative offset or limit sliced from the end of the list (422 now).
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1),
    state: WebState = Depends(get_state),
) -> dict:
    """One page of the TraderPlus price list, matched by classname (*q*), by
    category name and by trade direction (``_TP_TRADE_FILTERS``). A line Zedstead
    cannot read has no prices, so a trade filter leaves it out."""
    from app.traderplus.traderplus_json import get_price_config_path, parse_price_config

    if trade and trade not in _TP_TRADE_FILTERS:
        raise HTTPException(status_code=400, detail=f"Unknown trade filter: {trade}")
    trade_ok = _TP_TRADE_FILTERS.get(trade)

    def _load() -> dict:
        path = get_price_config_path(state.config)
        if path is None:
            return {"categories": [], "rows": [], "total": 0, "unreadable": 0}

        cfg = parse_price_config(path)
        categories = [c.name for c in cfg.categories]
        # Counted over the whole file, whatever the search shows.
        unreadable = sum(p.unreadable for c in cfg.categories for p in c.products)
        rows = []
        needle = q.lower().strip()
        for cat_idx, cat in enumerate(cfg.categories):
            if category and cat.name != category:
                continue
            for prod_idx, product in enumerate(cat.products):
                if needle and needle not in product.classname.lower():
                    continue
                if trade_ok and (product.unreadable or not trade_ok(product)):
                    continue
                rows.append(_product_row(cat_idx, cat.name, prod_idx, product))

        total = len(rows)
        page = rows[offset : offset + limit]
        return {"categories": categories, "rows": page, "total": total, "unreadable": unreadable}

    return await asyncio.to_thread(_load)


@router.put("/tp-price/{cat_idx}/{prod_idx}")
async def tp_price_update(
    cat_idx: int,
    prod_idx: int,
    body: TpPriceUpdateRequest,
    state: WebState = Depends(get_state),
) -> dict:
    import dataclasses

    from app.shared.config_versions import snapshot
    from app.traderplus.traderplus_json import get_price_config_path, parse_price_config, save_price_config, update_product

    config = state.config

    def _do() -> dict:
        path = get_price_config_path(config)
        if path is None:
            raise RuntimeError("TraderPlusPriceConfig.json not found")
        cfg = parse_price_config(path)
        # Both bounds: a negative index would wrap around via Python list
        # indexing and silently edit the wrong row.
        if not 0 <= cat_idx < len(cfg.categories):
            raise RuntimeError(f"Unknown category index: {cat_idx}")
        cat = cfg.categories[cat_idx]
        if not 0 <= prod_idx < len(cat.products):
            raise RuntimeError(f"Unknown product index: {prod_idx}")
        if cat.products[prod_idx].unreadable:
            # The numbers shown for it are defaults; saving them would replace
            # whatever the line says with a product it never was.
            raise RuntimeError(
                f"{path.name}: line {prod_idx + 1} of the category {cat.name!r} is not a product "
                "line Zedstead can read, so it is kept as it is and cannot be edited here. "
                "Correct it in the file itself."
            )

        snapshot(path, trigger="editor")
        changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
        updated = dataclasses.replace(cat.products[prod_idx], **changes)
        update_product(cat, prod_idx, updated)
        save_price_config(cfg)
        return _product_row(cat_idx, cat.name, prod_idx, cat.products[prod_idx])

    try:
        row = await asyncio.to_thread(_do)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    await state.hub.broadcast({"type": "configs_changed"})
    return row


@router.get("/tp-price/zone-offers")
async def tp_price_zone_offers(state: WebState = Depends(get_state)) -> dict:
    """The installed zone mods' items that a TraderPlus category could hold."""
    from app.ops import zone_trader

    return await asyncio.to_thread(zone_trader.offers, state.config)


@router.post("/tp-price/zone-offers")
async def tp_price_add_zone_offer(body: TpZoneOfferRequest, state: WebState = Depends(get_state)) -> dict:
    """Add one zone mod's items as a category, unpriced unless *use_prices*,
    and give it to traders."""
    from app.ops import zone_trader

    try:
        result = await asyncio.to_thread(
            zone_trader.add_to_traderplus, state.config, body.mod, body.category, body.trader_ids,
            body.use_prices,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await state.hub.broadcast({"type": "configs_changed"})
    return result


@router.post("/tp-price/{cat_idx}/multiplier")
async def tp_price_multiplier(
    cat_idx: int, body: TpPriceMultiplierRequest, state: WebState = Depends(get_state)
) -> dict:
    from app.shared.config_versions import snapshot
    from app.traderplus.traderplus_json import (
        apply_category_multiplier,
        get_price_config_path,
        parse_price_config,
        save_price_config,
    )

    # Before anything is read or kept: an emptied box arrived as 0, which
    # apply_price_multiplier clamps to 1, and every price became 1.
    check_multipliers(price=body.multiplier)
    config = state.config
    multiplier = body.multiplier
    columns = set(body.columns) if body.columns else None

    def _do() -> list:
        path = get_price_config_path(config)
        if path is None:
            raise RuntimeError("TraderPlusPriceConfig.json not found")
        cfg = parse_price_config(path)
        if not 0 <= cat_idx < len(cfg.categories):
            raise RuntimeError(f"Unknown category index: {cat_idx}")
        snapshot(path, trigger="editor")  # after every check: a refusal keeps no version
        changes = apply_category_multiplier(cfg, cat_idx, multiplier, columns=columns)
        save_price_config(cfg)
        return changes

    try:
        changes = await asyncio.to_thread(_do)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    await state.hub.broadcast({"type": "configs_changed"})
    return {"changes": len(changes)}
