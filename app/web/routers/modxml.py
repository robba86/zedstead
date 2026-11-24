"""GET /api/modxml — where every installed mod's XML fragments ended up, plus
structured (table) and raw read/write for the generated
``mpmissions/<mission>/custom/`` files.

The custom files are *generated*: a full merge rewrites the folder from the
mods' originals, so an edit here is a tuning knob for the current run (event
nominals, spawn chances), not a permanent one. The frontend says so.

Structured editing reuses ``editors/ce_tables.py`` — the same columns and
save path the mission's own events.xml / cfgspawnabletypes.xml editors use,
addressed by file path since a generated file has no EditorSpec."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException

from app.web.deps import get_state, require_session
from app.web.state import WebState
from app.modmgr import modlist
from app.ops.mod_xml_takeover import apply_source_change, take_over_mod_source

router = APIRouter(prefix="/api/modxml", tags=["modxml"], dependencies=[Depends(require_session)])


@router.get("")
async def mod_xml_report(state: WebState = Depends(get_state)) -> dict:
    from app.xml.xml_report import build_mod_xml_report

    return await asyncio.to_thread(build_mod_xml_report, state.config)


def _mission_or_400(state: WebState) -> str:
    from app.modmgr.mods import get_current_mission

    mission = get_current_mission(state.config.server_cfg_path)
    if not mission:
        raise HTTPException(status_code=400, detail="No mission detected from serverDZ.cfg.")
    return mission


@router.get("/files")
async def list_links(state: WebState = Depends(get_state)) -> dict:
    """Manual XML links + the vocabulary the Add form needs.

    The form has exactly two modes and they are not interchangeable: **Link**
    can only offer what cfgeconomycore is able to reference (`ce_kinds`), and
    **Merge** offers mission *files* (`targets`) because everything else reaches
    the mission by being spliced into one. `kind_targets` lets the table name
    the file a link landed in without re-deriving it client-side.
    """
    from app.maps.registry import map_choices, terrain_labels
    from app.modmgr.mods import get_current_mission
    from app.xml import xml_manual
    from app.xml.xml_report import installed_mod_ids

    def _load() -> dict:
        mission = get_current_mission(state.config.server_cfg_path) or ""
        server_dir = state.config.server_dir_path
        manual_dir = state.config.paths.custom_xml_dir
        return {
            # `upstream` is hashes only — no diff, which would mean parsing and
            # normalizing two XML files per row on every page load.
            "files": [
                {
                    **row,
                    "content": _manual_content(row["id"], manual_dir),
                    "upstream": xml_manual.upstream_status(row, server_dir),
                }
                for row in xml_manual.load_files(manual_dir=manual_dir)
            ],
            "kinds": sorted(xml_manual.MANUAL_KINDS),
            "ce_kinds": sorted(xml_manual.CE_KINDS),
            # Map scope options + a label for every *accepted* spelling: the
            # picker offers one row per map (`app.maps.registry`, the same
            # catalog the Maps page installs from), while a scope stored as an
            # alias (`livonia`) still has to render as its map's name.
            "terrains": map_choices(),
            "terrain_names": terrain_labels(),
            # Merges pick their own target; the form offers exactly the files
            # this mission has.
            "targets": xml_manual.list_merge_targets(state.config.server_dir_path, mission)
            if mission
            else [],
            "kind_targets": {
                kind: xml_manual.target_for_kind(kind)
                for kind in sorted(xml_manual.MANUAL_KINDS)
                if xml_manual.target_for_kind(kind)
            },
            "merge_kind": xml_manual.MERGE_KIND,
            # The kinds whose files get a Form tab (their editor, plans/92).
            # types has its own page, pinned to the file by GET /api/types?manual=.
            "form_kinds": sorted([*MANUAL_KIND_EDITORS, "types"]),
            # Suggestions for the "For mod" field. Not a closed list — the field
            # stays free text so a mod that isn't installed yet can be named.
            "mods": [
                {"name": name, "id": wid}
                for name, wid in sorted(installed_mod_ids(state.config).items())
            ],
        }

    return await asyncio.to_thread(_load)


@router.post("/files/classify")
async def classify_link_content(body: dict = Body(...)) -> dict:
    """Guess what a pasted XML fragment is, so the form can propose a mode.

    A proposal only — the answer is never applied on the user's behalf. `mode`
    is `link` when cfgeconomycore can reference the content (types / events /
    spawnabletypes) and `merge` otherwise, with the file that merge would go
    into; an unrecognised fragment returns `kind: null` and the user picks.
    """
    from app.xml import xml_manual
    from app.xml.xml_classify import CE_KINDS, classify_xml_text

    content = str(body.get("content", ""))
    kind = classify_xml_text(content, str(body.get("filename", "")))
    if kind is None:
        return {"kind": None, "mode": None, "target": ""}
    return {
        "kind": kind,
        "mode": "link" if kind in CE_KINDS else "merge",
        "target": xml_manual.target_for_kind(kind),
    }


@router.get("/files/starters")
async def link_starters(
    kind: str = "", target: str = "", state: WebState = Depends(get_state),
) -> dict:
    """Starter XML for the builder buttons above the link form's content box.

    Declared before the ``/links/{file_id}`` routes, like ``/links/classify`` —
    the file's convention, so a literal path can never be swallowed by the
    parameterised one.

    The caller sends whichever of ``kind`` / ``target`` its form mode has, and
    `normalize_kind_target` decides the rest — the same resolution the save
    path uses, so the buttons cannot offer a starter the save would then
    reclassify. (This is the third place that has to know which kind owns which
    mission file; it must stay derived, never a second hardcoded map — and that
    goes for the webui too.)

    A free-form target additionally gets `sample` (one real element out of the
    admin's own file) and `empty` (that file's root, empty) — which is how a
    mission XML zsm ships no template for still gets a correct starting point.
    """
    from app.xml import xml_manual
    from app.xml.xml_templates import templates_for_kind, templates_for_target
    from app.xml.xml_text import XML_DECLARATION

    kind, target = xml_manual.normalize_kind_target(
        kind.strip() or xml_manual.MERGE_KIND, target.strip(),
    )
    if kind != xml_manual.MERGE_KIND:
        return {
            "kind": kind,
            "target": xml_manual.target_for_kind(kind),
            "root_tag": xml_manual.root_tag_for(kind),
            "templates": [_starter(t) for t in templates_for_kind(kind)],
            "sample": None,
            "empty": None,
            "reason": "",
        }

    if not target:
        # Merge mode before a file is picked — nothing to offer, and no reason
        # to require a mission just to say so.
        return {
            "kind": kind, "target": "", "root_tag": "",
            "templates": [], "sample": None, "empty": None, "reason": "",
        }
    if not xml_manual.is_safe_target(target):
        raise HTTPException(status_code=400, detail=f"Not a mission XML file: {target!r}")

    mission = _mission_or_400(state)
    sample = await asyncio.to_thread(
        xml_manual.sample_merge_element, state.config.server_dir_path, mission, target,
    )
    root_tag = sample.root_tag if sample else ""
    empty = (
        {
            "label": f"Empty <{root_tag}>",
            "content": (
                f"{XML_DECLARATION}\n<{root_tag}>\n"
                f"\t<!-- Write the elements that belong in {target} here. -->\n"
                f"</{root_tag}>\n"
            ),
        }
        if root_tag
        else None
    )
    return {
        "kind": kind,
        "target": target,
        "root_tag": root_tag,
        "templates": [_starter(t) for t in templates_for_target(target)],
        "sample": (
            {
                "content": sample.content,
                "from": sample.target,
                "skipped_mod_blocks": sample.skipped_mod_blocks,
            }
            if sample and sample.content
            else None
        ),
        "empty": empty,
        "reason": sample.reason if sample else "",
    }


def _starter(template) -> dict:
    return {
        "id": template.id,
        "label": template.label,
        "summary": template.summary,
        "content": template.content,
    }


def _manual_content(file_id: str, manual_dir: Path) -> str:
    """A manual file's XML for display, lossy, so one file saved in another
    encoding does not break the whole list."""
    from app.shared.json_utils import read_text_lossy
    from app.xml import xml_manual

    path = xml_manual.file_content_path(file_id, manual_dir=manual_dir)
    return read_text_lossy(path) if path.is_file() else ""


def _canonical_mod(config, value: str) -> str:
    """Store the mod dir name whenever the typed value resolves to one.

    The field accepts a name (`@CF`, `@` optional) or a workshop id, because a
    user pre-configuring XML for a mod they haven't installed yet only has the
    id. Resolving here means every consumer downstream compares one spelling.
    An unresolvable value is kept verbatim — that's the pre-configure case, and
    the report flags it as orphaned until the mod shows up.
    """
    from app.modmgr.mod_ops import resolve_mod_ref

    value = (value or "").strip()
    if not value:
        return ""
    ref = resolve_mod_ref(config, value)
    return ref.name if ref and ref.name else value


@router.post("/files")
async def create_file(body: dict = Body(...), state: WebState = Depends(get_state)) -> dict:
    state.jobs.refuse_while_busy()  # an update's merge writes the same files
    from app.xml import xml_manual

    def _create() -> dict:
        try:
            return xml_manual.create_file(
                str(body.get("id", "")).strip(),
                str(body.get("kind", "")).strip(),
                body.get("content", ""),
                manual_dir=state.config.paths.custom_xml_dir,
                mod=_canonical_mod(state.config, str(body.get("mod", ""))),
                terrain=str(body.get("terrain", "")).strip(),
                note=str(body.get("note", "")).strip(),
                target=str(body.get("target", "")).strip(),
            )
        except xml_manual.ManualFileError as e:
            raise HTTPException(status_code=400, detail=str(e))

    link = await asyncio.to_thread(_create)
    await state.hub.broadcast({"type": "configs_changed"})
    return link


@router.put("/files/{file_id}")
async def update_file(
    file_id: str, body: dict = Body(...), state: WebState = Depends(get_state)
) -> dict:
    state.jobs.refuse_while_busy()  # an update's merge writes the same files
    from app.xml import xml_manual

    fields = {
        k: body[k] for k in ("kind", "mod", "terrain", "enabled", "note", "target") if k in body
    }
    if "mod" in fields:
        fields["mod"] = _canonical_mod(state.config, str(fields["mod"]))

    def _update() -> dict:
        try:
            return xml_manual.update_file(
                file_id,
                manual_dir=state.config.paths.custom_xml_dir,
                content=body.get("content"),
                **fields,
            )
        except xml_manual.ManualFileError as e:
            raise HTTPException(status_code=400, detail=str(e))

    link = await asyncio.to_thread(_update)
    await state.hub.broadcast({"type": "configs_changed"})
    return link


@router.delete("/files/{file_id}")
async def delete_file(file_id: str, state: WebState = Depends(get_state)) -> dict:
    """Forget a link and undo what it applied to the current mission.

    Deleting the record alone would leave the merged block and the custom/ copy
    serving content the user just removed until the next full merge.
    """
    state.jobs.refuse_while_busy()  # an update's merge writes the same files
    from app.modmgr.mods import get_current_mission
    from app.xml import xml_manual

    def _delete() -> bool:
        mission = get_current_mission(state.config.server_cfg_path) or ""
        try:
            return xml_manual.delete_file(
                file_id,
                manual_dir=state.config.paths.custom_xml_dir,
                server_dir=state.config.server_dir_path,
                mission=mission,
            )
        except xml_manual.ManualFileError as e:
            raise HTTPException(status_code=400, detail=str(e))

    if not await asyncio.to_thread(_delete):
        raise HTTPException(status_code=404, detail=f"No such link: {file_id!r}")
    await state.hub.broadcast({"type": "configs_changed"})
    return {"ok": True}


# --- the Form tab: a manual file in its kind's own editor (plans/92) --------
#
# The same editor the mission file has (its columns, help and checks), run on
# the manual file's own XML through `editor_file_override`. Keyed by the
# manual file's kind; the pairs match webui crossLinks.js MODXML_KIND_SLUGS.
# A free-form merge into another mission file has no editor and no Form tab.

MANUAL_KIND_EDITORS = {
    "events": "events",
    "spawnabletypes": "spawnabletypes",
    "globals": "globals",
    "economy": "economy",
    "messages": "messages",
    "cfgeventspawns": "eventspawns",
    "cfgeventgroups": "eventgroups",
    "cfgrandompresets": "randompresets",
    "cfglimitsdefinition": "limitsdefinition",
    "cfglimitsdefinitionuser": "limitsdefinitionuser",
}


def _form_target(state: WebState, file_id: str):
    """(editor spec, the manual file's XML path) for a file's Form tab."""
    from app.web import editors
    from app.xml import xml_manual

    link, path = manual_xml_of(state, file_id)
    editor_id = MANUAL_KIND_EDITORS.get(link.get("kind", ""))
    spec = editors.get_editor(editor_id) if editor_id else None
    if spec is None:
        raise HTTPException(status_code=422, detail="This file has no form. Edit it on the XML tab.")
    return spec, path


def manual_xml_of(state: WebState, file_id: str, kind: str | None = None) -> tuple[dict, Path]:
    """(the manual file's entry, its own XML path): 404 for an unknown file or
    a missing XML, 422 when *kind* is given and the file is another kind.
    The Form tab and the Types editor's `?manual=` read a file through it."""
    from app.xml import xml_manual

    link = _upstream_or_404(state, file_id)
    if kind is not None and link.get("kind") != kind:
        raise HTTPException(status_code=422, detail=f"{file_id!r} is not a {kind} file.")
    path = xml_manual.file_content_path(file_id, manual_dir=state.config.paths.custom_xml_dir)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"The XML of {file_id!r} is missing.")
    return link, path


@router.get("/files/{file_id}/form")
async def get_file_form(file_id: str, state: WebState = Depends(get_state)) -> dict:
    """A manual file's content as its kind's editor shows it (`editor` names it)."""
    from app.web.editors._paths import editor_file_override

    def _load() -> dict:
        spec, path = _form_target(state, file_id)
        try:
            with editor_file_override(spec.id, path):
                return {"editor": spec.id, **spec.load(state.config)}
        except Exception as e:  # noqa: BLE001 - an unreadable file is the user's to fix
            raise HTTPException(status_code=400, detail=str(e))

    return await asyncio.to_thread(_load)


@router.put("/files/{file_id}/form")
async def save_file_form(
    file_id: str, body: dict = Body(...), state: WebState = Depends(get_state)
) -> dict:
    """Save a manual file through its kind's editor. Like every manual write,
    the page then runs the apply job, so the mission gets the change."""
    state.jobs.refuse_while_busy()  # an update's merge writes the same files
    from app.web.editors._paths import editor_file_override

    def _save() -> None:
        spec, path = _form_target(state, file_id)
        payload = body.get("values", {}) if spec.kind == "form" else body.get("rows", [])
        try:
            with editor_file_override(spec.id, path):
                spec.save(state.config, payload)
        except Exception as e:  # noqa: BLE001 - the editor's own refusal, as on /api/configs
            raise HTTPException(status_code=400, detail=str(e))

    await asyncio.to_thread(_save)
    await state.hub.broadcast({"type": "configs_changed"})
    return {"ok": True}


# --- upstream drift on a file taken over from a mod ------------------------
#
# Taking a mod's file over stops it tracking the mod, silently and for good.
# These three say what the mod has done since, and let the admin either keep
# their version or take the mod's. Unlike `/files/classify` & co these carry an
# extra path segment, so `/files/{file_id}` cannot shadow them wherever they are
# declared — the declaration-order rule applies to the *two*-segment literals.


def _upstream_or_404(state: WebState, file_id: str) -> dict:
    from app.xml import xml_manual

    link = xml_manual.get_file(file_id, manual_dir=state.config.paths.custom_xml_dir)
    if link is None:
        raise HTTPException(status_code=404, detail=f"No such file: {file_id!r}")
    return link


@router.get("/files/{file_id}/upstream")
async def get_upstream(file_id: str, state: WebState = Depends(get_state)) -> dict:
    """What the mod has changed in this file since it was taken over.

    The diff is the mod's own before/after — never a comparison against the
    admin's edits. The question it answers is "what did the mod do, and do I
    want it too".
    """
    from app.xml import xml_manual

    def _load() -> dict:
        link = _upstream_or_404(state, file_id)
        return xml_manual.upstream_info(
            link,
            state.config.server_dir_path,
            manual_dir=state.config.paths.custom_xml_dir,
            diff=True,
        )

    return await asyncio.to_thread(_load)


@router.post("/files/{file_id}/upstream/ack")
async def ack_upstream(file_id: str, state: WebState = Depends(get_state)) -> dict:
    """"Seen it, keeping mine" — re-baseline without touching the admin's file."""
    state.jobs.refuse_while_busy()  # an update's merge writes the same files
    from app.xml import xml_manual

    def _ack() -> dict:
        _upstream_or_404(state, file_id)
        try:
            return xml_manual.ack_upstream(
                file_id, state.config.server_dir_path, manual_dir=state.config.paths.custom_xml_dir,
            )
        except xml_manual.ManualFileError as e:
            raise HTTPException(status_code=400, detail=str(e))

    result = await asyncio.to_thread(_ack)
    await state.hub.broadcast({"type": "configs_changed"})
    return result


@router.post("/files/{file_id}/upstream/adopt")
async def adopt_upstream(file_id: str, state: WebState = Depends(get_state)) -> dict:
    """Replace the admin's file with the mod's current version, and re-baseline.

    Destructive, so `xml_manual.adopt_upstream` snapshots the manual file first —
    it shows up in Analysis → Change history like every other overwrite. It does NOT
    re-apply: the file is applied by the next merge, same as any other edit.
    """
    state.jobs.refuse_while_busy()  # an update's merge writes the same files
    from app.xml import xml_manual

    def _adopt() -> dict:
        _upstream_or_404(state, file_id)
        try:
            return xml_manual.adopt_upstream(
                file_id, state.config.server_dir_path, manual_dir=state.config.paths.custom_xml_dir,
            )
        except xml_manual.ManualFileError as e:
            raise HTTPException(status_code=400, detail=str(e))

    result = await asyncio.to_thread(_adopt)
    await state.hub.broadcast({"type": "configs_changed"})
    return result


@router.post("/files/from-event")
async def link_from_event(body: dict = Body(...), state: WebState = Depends(get_state)) -> dict:
    """Capture an event's hand-placed spawn positions as a manual link.

    The map editor's "make this permanent" action: positions dragged onto the
    map are written into the mission's cfgeventspawns.xml, which the next merge
    restores from its `.backup`.
    """
    state.jobs.refuse_while_busy()  # an update's merge writes the same files
    from app.xml import xml_manual

    mission = _mission_or_400(state)
    event = str(body.get("event", "")).strip()

    def _capture() -> dict:
        try:
            return xml_manual.capture_event_positions(
                state.config.server_dir_path,
                mission,
                event,
                manual_dir=state.config.paths.custom_xml_dir,
                file_id=str(body.get("id", "")).strip(),
            )
        except xml_manual.ManualFileError as e:
            raise HTTPException(status_code=400, detail=str(e))

    link = await asyncio.to_thread(_capture)
    await state.hub.broadcast({"type": "configs_changed"})
    return link


@router.post("/files/from-file")
async def file_from_mission_file(
    body: dict = Body(...), state: WebState = Depends(get_state)
) -> dict:
    """Move a mission file's own entries into a manual XML file.

    The whole-file counterpart to `/files/from-event`. Since a merge stopped
    wiping these files (2026 Q3) this keeps nothing that wasn't already kept
    — what it buys is surviving a map switch or a server reinstall, and being
    applied after every mod so it wins DayZ's last-definition rule.

    The target is validated by `capture_unowned`, which only accepts a file one
    of the merge configs owns: cfgeconomycore.xml is deliberately not one of
    them (its `<ce folder="custom">` block is rewritten every merge, and a
    `@user:` marker block inside it would be a second, competing mechanism).
    """
    state.jobs.refuse_while_busy()  # an update's merge writes the same files
    from app.xml import xml_manual

    from app.ops import mission_stock

    mission = _mission_or_400(state)
    target = str(body.get("target", "")).strip()

    def _capture() -> dict:
        try:
            return xml_manual.capture_unowned(
                state.config.server_dir_path,
                mission,
                target,
                manual_dir=state.config.paths.custom_xml_dir,
                file_id=str(body.get("id", "")).strip(),
                note=str(body.get("note", "")).strip(),
                # Bohemia's own entries stay in the mission.
                stock=mission_stock.stock_copy(state.config, mission, target) if target else None,
            )
        except xml_manual.ManualFileError as e:
            raise HTTPException(status_code=400, detail=str(e))

    link = await asyncio.to_thread(_capture)
    await state.hub.broadcast({"type": "configs_changed"})
    return link


@router.post("/files/apply")
async def apply_links(state: WebState = Depends(get_state)) -> dict:
    """Apply every link to the current mission, as a job under the merge lock.

    Same kind as the mod XML merges (`mods_xml_merge`) on purpose — it writes
    the same mission files, so it must not run beside one.
    """
    from app.web.jobs import Job, JobConflictError, conflict_response

    config = state.config
    mission = _mission_or_400(state)

    async def _job_coro(job: Job) -> dict:
        def _sync() -> list[str]:
            from app.xml.xml_economy import merge_economy_core
            from app.xml.xml_manual import apply_direct_files

            # The CE half only reaches the mission through cfgeconomycore, which
            # merge_economy_core rewrites wholesale — there is no scoped way to
            # register one custom/ file, so both halves run.
            manual_dir = config.paths.custom_xml_dir
            merge_economy_core(
                config.server_dir_path,
                mission,
                manual_dir=manual_dir,
                disabled_mods=modlist.disabled(config),
                log=job.log_line_threadsafe,
            )
            return apply_direct_files(
                config.server_dir_path, mission, manual_dir=manual_dir,
                disabled_mods=modlist.disabled(config), log=job.log_line_threadsafe,
            )

        failed = await asyncio.to_thread(_sync)
        await state.hub.broadcast({"type": "configs_changed"})
        if failed:
            raise RuntimeError(f"Manual links failed: {', '.join(failed)}")
        return {"mission": mission}

    try:
        job = state.jobs.start("Apply manual XML links", "mods_xml_merge", _job_coro)
    except JobConflictError as e:
        return conflict_response(e)
    return {"job_id": job.id}


@router.get("/source")
async def get_mod_source(path: str, state: WebState = Depends(get_state)) -> dict:
    """Read-only view of one mod's own XML file (a report row's `source`).

    Read-only on purpose: a mod dir is SteamCMD's, and the next workshop update
    overwrites whatever was typed here. What ships to the mission is the copy in
    `custom/` (editable above) or the merged mission file.
    """
    from app.web.editors._raw import load_raw
    from app.xml.xml_report import mod_source_path

    def _load() -> dict:
        resolved = mod_source_path(state.config, path)
        if resolved is None:
            raise HTTPException(status_code=404, detail=f"Unknown mod XML: {path!r}")
        return {"path": path, "name": resolved.name, "language": "xml", "content": load_raw(resolved)}

    return await asyncio.to_thread(_load)


def _source_key(state: WebState, path: str) -> str:
    """Validate a report row's `source` and return its exclusion-list key.

    Re-derived through `mod_source_path` (the same guard the read-only viewer
    uses) rather than trusted: it arrives from the browser and is joined onto
    the server dir.
    """
    from app.xml.xml_manual import normalize_source
    from app.xml.xml_report import mod_source_path

    resolved = mod_source_path(state.config, path)
    if resolved is None:
        raise HTTPException(status_code=404, detail=f"Unknown mod XML: {path!r}")
    server_dir = state.config.server_dir_path.resolve()
    return normalize_source(resolved.relative_to(server_dir))


def _state_from_body(body: dict) -> str:
    """The requested state: ``""`` (on), ``"excluded"`` or ``"deleted"``.

    Accepts the older `{"excluded": bool}` shape too — the two states are one
    field to the merge, and an older tab shouldn't fail on Save.
    """
    from app.xml import xml_manual

    if "state" in body:
        value = str(body.get("state") or "")
        if value not in (xml_manual.STATE_ON, xml_manual.STATE_EXCLUDED, xml_manual.STATE_DELETED):
            raise HTTPException(status_code=400, detail=f"Unknown state: {value!r}")
        return value
    if bool(body.get("excluded", True)):
        return xml_manual.STATE_EXCLUDED
    return xml_manual.STATE_ON


_apply_source_change = apply_source_change


@router.put("/source/state")
async def set_source_state(body: dict = Body(...), state: WebState = Depends(get_state)) -> dict:
    """Turn one mod's XML file off (or back on).

    Durable by design: every merge re-applies a mod's XML from the mod dir, so
    the only way to keep a file out is to have the merge skip it. Covers the
    files that never become a `custom/` tile — a direct merge into
    cfgeventspawns.xml had no off switch at all before this. `deleted` is the
    same suppression; it only differs in whether the page keeps listing the row.
    """
    state.jobs.refuse_while_busy()  # an update's merge writes the same files
    from app.xml import xml_manual

    key = _source_key(state, str(body.get("path", "")))
    new_state = _state_from_body(body)
    config = state.config

    def _apply() -> dict:
        try:
            xml_manual.set_source_state(key, new_state, manual_dir=config.paths.custom_xml_dir)
        except xml_manual.ManualFileError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {
            "source": key,
            "state": new_state,
            "excluded": bool(new_state),
            **_apply_source_change(config, key),
        }

    result = await asyncio.to_thread(_apply)
    await state.hub.broadcast({"type": "configs_changed"})
    return result


@router.delete("/source")
async def delete_source_blocks(path: str, state: WebState = Depends(get_state)) -> dict:
    """Delete one mod XML's contribution from the mission, permanently.

    The direct-merge twin of `DELETE /custom/{name}`, and permanent for the same
    reason: the mod file itself is SteamCMD's and cannot be deleted, so the only
    delete that survives is a recorded one — otherwise the next merge splices it
    straight back in. It differs from `PUT /source/state`'s "excluded" only in
    that the page stops listing the row (behind a "show deleted" toggle).

    Applied as a re-merge of the owning mod: the mod's other files share the same
    START/END block, so removing the block alone would take them with it.
    """
    state.jobs.refuse_while_busy()  # an update's merge writes the same files
    from app.xml import xml_manual
    from app.xml.xml_classify import CE_KINDS, classify_mod_xml

    key = _source_key(state, path)
    config = state.config

    def _delete() -> dict:
        source = config.server_dir_path / Path(*key.split("/"))
        if classify_mod_xml(source) in CE_KINDS:
            raise HTTPException(
                status_code=400,
                detail="This is a central-economy file — delete it from its custom/ tile instead.",
            )
        try:
            xml_manual.set_source_state(
                key, xml_manual.STATE_DELETED, manual_dir=config.paths.custom_xml_dir,
            )
        except xml_manual.ManualFileError as e:
            raise HTTPException(status_code=400, detail=str(e))
        # Not an error when the mission can't be updated right now (no mission, or
        # a disabled mod whose parked entries a scoped merge would eat): the
        # record is what makes the delete stick, and the next enable applies it.
        result = _apply_source_change(config, key)
        return {"source": key, "state": xml_manual.STATE_DELETED, "permanent": True, **result}

    result = await asyncio.to_thread(_delete)
    await state.hub.broadcast({"type": "configs_changed"})
    return result


@router.post("/source/fork")
async def fork_source(body: dict = Body(...), state: WebState = Depends(get_state)) -> dict:
    """Copy a mod's XML file into a manual link, disabling the original.

    Same trade as forking a generated file: the mod's copy is overwritten by the
    next workshop update and re-merged from there on every run, the link is
    yours. Both live at once would contribute the content twice, so the fork
    excludes the source in the same step.

    Created **enabled and applied**, like `POST /custom/{name}/fork`: the
    exclusion takes the mod's content out of the mission immediately, so leaving
    the copy unapplied would leave the mission with neither until the next
    merge. `mod_xml_takeover.take_over_mod_source` does both, and is what the
    map editor's "Copy into a manual file" calls too.
    """
    state.jobs.refuse_while_busy()  # an update's merge writes the same files
    from app.xml import xml_manual

    key = _source_key(state, str(body.get("path", "")))
    config = state.config

    def _fork() -> dict:
        try:
            return take_over_mod_source(config, key, file_id=str(body.get("id", "")).strip())
        except xml_manual.ManualFileError as e:
            raise HTTPException(status_code=400, detail=str(e))

    link = await asyncio.to_thread(_fork)
    await state.hub.broadcast({"type": "configs_changed"})
    return link


def _resolve(state: WebState, name: str):
    from app.xml.xml_report import custom_file_path

    path = custom_file_path(state.config, name)
    if path is None:
        raise HTTPException(status_code=404, detail=f"Unknown custom XML: {name!r}")
    return path


def _ce_kind(path, fallback: str | None = None) -> str | None:
    """Central-economy kind of a custom file — the table view depends on it."""
    from app.web.editors.ce_tables import CE_TABLE_KINDS
    from app.xml.xml_classify import classify_mod_xml

    kind = classify_mod_xml(path) or fallback
    return kind if kind in CE_TABLE_KINDS else None


@router.get("/custom/{name}/table")
async def get_custom_table(
    name: str, kind: str | None = None, state: WebState = Depends(get_state)
) -> dict:
    """Structured table view of a custom file (types/events/spawnabletypes).

    `kind` is an optional hint from the report's cfgeconomycore entry, used
    when the file itself doesn't classify.
    """
    from app.web.editors.ce_tables import ce_table_load

    def _load() -> dict:
        path = _resolve(state, name)
        detected = _ce_kind(path, kind)
        if detected is None:
            raise HTTPException(
                status_code=422, detail="No structured view for this file — use the Raw tab."
            )
        return {"name": name, "ce_kind": detected, **ce_table_load(path, detected)}

    return await asyncio.to_thread(_load)


def _valid_custom_name(name: str) -> str:
    """A bare `*.xml` filename — checked *without* requiring the file to exist.

    `_resolve` can't be used for the disable/enable toggle: an excluded file has
    already been removed from the mission, and re-enabling it must still work.
    """
    # "\\" is checked explicitly: it is not a separator to PurePosixPath, so a
    # name like `sub\dir.xml` passes `Path(name).name == name` on Linux and only
    # becomes a path once it reaches a Windows server.
    if (
        not name
        or Path(name).name != name
        or "\\" in name
        or "/" in name
        or not name.lower().endswith(".xml")
    ):
        raise HTTPException(status_code=404, detail=f"Unknown custom XML: {name!r}")
    return name


@router.put("/custom/{name}/state")
async def set_custom_state(
    name: str, body: dict = Body(...), state: WebState = Depends(get_state)
) -> dict:
    """Turn one generated custom/ file off (or back on).

    Has to be durable, not a delete: custom/ is rebuilt from the mods on every
    merge, so a file removed by hand reappears. The exclusion list is what the
    copy step consults, and the mission is brought in line immediately —
    removing the file + its `<ce>` entry, or re-running the owning mod's scoped
    CE merge to put it back. `deleted` suppresses it identically and only tells
    the page to stop listing the tile.
    """
    state.jobs.refuse_while_busy()  # an update's merge writes the same files
    return await _write_custom_state(name, _state_from_body(body), state)


async def _write_custom_state(name: str, new_state: str, state: WebState) -> dict:
    """Shared by the state toggle and the delete — both only set a state."""
    from app.modmgr.mods import get_current_mission
    from app.xml import xml_manual
    from app.xml.xml_economy import merge_mod_economy_entries, unregister_custom_file

    _valid_custom_name(name)
    config = state.config

    def _apply() -> dict:
        try:
            xml_manual.set_custom_state(name, new_state, manual_dir=config.paths.custom_xml_dir)
        except xml_manual.ManualFileError as e:
            raise HTTPException(status_code=400, detail=str(e))

        mission = get_current_mission(config.server_cfg_path)
        mod = xml_manual.owning_mod(config.server_dir_path, name)
        result = {"state": new_state, "excluded": bool(new_state), "mod": mod}
        if not mission:
            return {**result, "applied": False, "reason": "no mission"}

        mission_dir = config.server_dir_path / "mpmissions" / mission
        if new_state:
            path = mission_dir / "custom" / name
            if path.is_file():
                path.unlink()
            unregister_custom_file(mission_dir, name)
            return {**result, "applied": True}
        if not mod:
            return {**result, "applied": False, "reason": "no owning mod"}
        # Same refusal the /source routes make: a disabled mod's CE entries are
        # parked in DISABLED wrappers that a scoped merge deletes outright, so
        # re-enabling a file here would quietly lose the rest of them. The state
        # is recorded; enabling the mod applies it.
        if mod in modlist.disabled(config):
            return {**result, "applied": False, "reason": "mod disabled"}
        merge_mod_economy_entries(
            config.server_dir_path, mission, mod, manual_dir=config.paths.custom_xml_dir,
        )
        return {**result, "applied": True}

    result = await asyncio.to_thread(_apply)
    await state.hub.broadcast({"type": "configs_changed"})
    return result


@router.delete("/custom/{name}")
async def delete_custom_file(name: str, state: WebState = Depends(get_state)) -> dict:
    """Delete one generated file — permanently.

    A generated file cannot be deleted in the ordinary sense: custom/ is rebuilt
    from the mods on every merge, so the only delete that survives one is a
    recorded one. This is `PUT /custom/{name}/state` with `deleted`, which
    suppresses the file exactly like `excluded` does and additionally drops the
    tile from the grid (restorable behind the page's "show deleted" toggle).

    Deliberately works when the file isn't there: a disabled mod's files are
    never copied into custom/, and those tiles must still be removable.
    """
    state.jobs.refuse_while_busy()  # an update's merge writes the same files
    from app.xml import xml_manual

    return {
        **await _write_custom_state(name, xml_manual.STATE_DELETED, state),
        "deleted": name,
        "permanent": True,
    }


@router.post("/custom/{name}/fork")
async def fork_custom(
    name: str, body: dict = Body(default={}), state: WebState = Depends(get_state)
) -> dict:
    """Take a generated file over as a manual XML file — the only way to edit it.

    `custom/` is regenerated from the mods on every merge, so nothing written
    into it survives; this hands the file to the admin instead. The generated
    original is turned off in the same step, because two CE files declaring the
    same classnames is a duplicate-warning log and a last-one-wins race.

    The new file is created **enabled and applied**, so the mission keeps serving
    the same content across the swap — it used to be created disabled, from when
    a fork was the first half of editing the generated file in place.

    `source` (a report row's mod XML path) is the fallback content when custom/
    holds nothing to copy: the owning mod is disabled, or the file was already
    turned off. Without it, "Set as manual" would be dead on exactly the tiles
    the user most wants to take over.
    """
    state.jobs.refuse_while_busy()  # an update's merge writes the same files
    from app.modmgr.mods import get_current_mission
    from app.xml import xml_manual
    from app.xml.xml_economy import unregister_custom_file
    from app.xml.xml_report import mod_source_path

    _valid_custom_name(name)
    config = state.config
    source = str(body.get("source", "")).strip()

    def _fork() -> dict:
        mission = get_current_mission(config.server_cfg_path)
        if not mission:
            raise HTTPException(status_code=400, detail="No mission detected from serverDZ.cfg.")
        # Re-derived through the same guard the read-only viewer uses — it
        # arrives from the browser and is joined onto the server dir.
        fallback = mod_source_path(config, source) if source else None
        if source and fallback is None:
            raise HTTPException(status_code=404, detail=f"Unknown mod XML: {source!r}")
        try:
            link = xml_manual.fork_custom_file(
                config.server_dir_path,
                mission,
                name,
                manual_dir=config.paths.custom_xml_dir,
                file_id=str(body.get("id", "")).strip(),
                source_path=fallback,
            )
        except xml_manual.ManualFileError as e:
            raise HTTPException(status_code=400, detail=str(e))

        mission_dir = config.server_dir_path / "mpmissions" / mission
        path = mission_dir / "custom" / name
        if path.is_file():
            path.unlink()
        unregister_custom_file(mission_dir, name)

        # Same refusal every other write here makes: a disabled mod's CE entries
        # are parked in DISABLED wrappers, and its custom/ files aren't in the
        # mission to begin with, so there is nothing to keep serving. The record
        # is written either way; enabling the mod applies it.
        mod = xml_manual.owning_mod(config.server_dir_path, name)
        if mod and mod in modlist.disabled(config):
            return {**link, "applied": False, "reason": "mod disabled"}
        applied = xml_manual.apply_manual_file(
            config.server_dir_path, mission, link["id"], manual_dir=config.paths.custom_xml_dir,
        )
        return {**link, "applied": applied, **({} if applied else {"reason": "nothing to apply"})}

    link = await asyncio.to_thread(_fork)
    await state.hub.broadcast({"type": "configs_changed"})
    return link


@router.get("/custom/{name}")
async def get_custom_file(name: str, state: WebState = Depends(get_state)) -> dict:
    """Read one generated file. There is deliberately no PUT counterpart.

    `custom/` is wiped and rebuilt from the mods on every merge, so a save here
    lasted until the next install, toggle or `zsm run` and then vanished — the
    editor was fully functional and the content it wrote was never durable.
    Taking the file over (`POST /custom/{name}/fork`) is the supported way to
    edit it, and the only one.
    """
    from app.web.editors._raw import load_raw

    def _load() -> dict:
        path = _resolve(state, name)
        return {"name": name, "language": "xml", "content": load_raw(path)}

    return await asyncio.to_thread(_load)
