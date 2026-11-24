"""GET /api/types/files, GET /api/types, PUT /api/types/entry,
POST /api/types/multiplier, POST /api/types/restore, GET /api/types/meta."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.shared.config_versions import snapshot
from app.web.deps import get_state, require_session
from app.web.multipliers import check_multipliers
from app.web.editors._nested import NESTED, NESTED_BASE
from app.web.editors._paths import mission_dir
from app.web.editors._xml_nested import apply_parts, nested_of
from app.web.editors.ce_tables import (
    FLAG_PARTS,
    limits_choices,
    mission_limits,
    nested_help,
    type_parts,
    undefined_names_problem,
)
from app.web.schemas import (
    TypesEntryUpdateRequest,
    TypesFillRequest,
    TypesFilterRequest,
    TypesMultiplierRequest,
    TypesRestoreRequest,
)
from app.web.state import WebState
from app.xml.types_xml import (
    FIELD_DESCRIPTIONS,
    MultiplierConfig,
    TypesFile,
    apply_multiplier,
    collect_all_categories,
    collect_usages,
    collect_values,
    entry_numbers,
    fill_changes,
    fill_problems,
    filter_entries,
    limit_names_of,
    multiplier_problems,
    parse_types_file,
    restore_types_file,
    save_types_files,
    scan_types_files,
    set_category,
    set_flag,
    set_numeric_field,
    set_tag,
    set_usages,
    set_values,
    type_number_problems,
    write_multiplier,
    FLAG_NAMES,
)

router = APIRouter(prefix="/api/types", tags=["types"], dependencies=[Depends(require_session)])

# The row's tree on this page: Category is already a column here.
PAGE_PARTS = type_parts("usage", "value", "tag", "flags")

# Cache parsed TypesFile per path, invalidated on mtime change — a mission's
# types.xml + custom/*.xml commonly total several thousand entries combined,
# too slow to re-parse on every request.
_CACHE: dict[Path, tuple[float, TypesFile]] = {}


def _load_cached(path: Path) -> TypesFile:
    mtime = path.stat().st_mtime
    cached = _CACHE.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    # Comments kept: this tree is what an edit saves.
    tf = parse_types_file(path, preserve_comments=True)
    _CACHE[path] = (mtime, tf)
    return tf


def _invalidate(path: Path) -> None:
    _CACHE.pop(path, None)


# One write at a time: an edit changes the cached tree in place before its
# last check, so another write saving meanwhile wrote that half-applied edit
# to disk even when it was then refused.
_WRITE_LOCK = threading.Lock()


def _one_write(fn):
    with _WRITE_LOCK:
        return fn()


def _load_files(config, manual: Path | None = None) -> list[TypesFile]:
    """The mission's types files, or only *manual* (a manual XML file's own
    XML, from `_manual_path`) when it is given."""
    if manual is not None:
        return [_load_cached(manual)]
    m_dir = mission_dir(config)
    if m_dir is None:
        return []
    return [_load_cached(p) for p in scan_types_files(m_dir)]


def _manual_path(state: WebState, manual: str | None, write: bool = False) -> Path | None:
    """The XML path of the manual types file *manual* names (`?manual=<id>`,
    the Form tab on Custom config), or None for the mission's files. A write
    is refused while a job runs, as every manual file write is."""
    if not manual:
        return None
    from app.web.routers.modxml import manual_xml_of

    if write:
        state.jobs.refuse_while_busy()
    return manual_xml_of(state, manual, kind="types")[1]


def _entry_dict(entry) -> dict:
    return {
        "id": f"{entry.source_file}::{entry.name}",
        "name": entry.name,
        "nominal": entry.nominal,
        "min": entry.min,
        "lifetime": entry.lifetime,
        "restock": entry.restock,
        "quantmin": entry.quantmin,
        "quantmax": entry.quantmax,
        "cost": entry.cost,
        "category": entry.category,
        "tag": entry.tag,
        "flags": entry.flags,
        "usages": entry.usages,
        "values": entry.values,
        "source_file": str(entry.source_file),
        "nested": nested_of(entry.element, PAGE_PARTS),
    }


@router.get("/files")
async def list_files(manual: str | None = None, state: WebState = Depends(get_state)) -> dict:
    manual_path = _manual_path(state, manual)

    def _load() -> list[str]:
        if manual_path is not None:
            return [str(manual_path)]
        m_dir = mission_dir(state.config)
        if m_dir is None:
            return []
        return [str(p) for p in scan_types_files(m_dir)]

    return {"files": await asyncio.to_thread(_load)}


def _matching(
    types_files: list,
    category: str | None,
    q: str,
    *,
    usage: str | None = None,
    value: str | None = None,
    nominal_min: int | None = None,
    nominal_max: int | None = None,
) -> list:
    """The rows the page lists for its category, name search and Smart filters.

    The list, the multiplier preview and the multiplier all read this, so the
    count the page shows is the set a multiplier writes. The multiplier used
    to drop the search: the dialog said 18 entries and the save multiplied
    all 2382 in the file. `usage` and `value` keep the rows that list that
    name, and `nominal_min`/`nominal_max` the rows whose nominal lies in that
    range: the Smart view's rarity band (SmartType.jsx RARITY).
    """
    entries = filter_entries(types_files, [category] if category else None)
    if q:
        needle = q.lower()
        entries = [e for e in entries if needle in e.name.lower()]
    if usage:
        entries = [e for e in entries if usage in e.usages]
    if value:
        entries = [e for e in entries if value in e.values]
    if nominal_min is not None:
        entries = [e for e in entries if e.nominal >= nominal_min]
    if nominal_max is not None:
        entries = [e for e in entries if e.nominal <= nominal_max]
    return entries


def _facets(
    types_files: list,
    category: str | None,
    q: str,
    *,
    usage: str | None,
    value: str | None,
    nominal_min: int | None,
    nominal_max: int | None,
) -> dict:
    """Per filter, the choices that still leave rows when every other filter stays.

    A web shop's facets: the page greys out a chip that would list nothing.
    Each group drops only its own filter, so its active chip and its
    neighbours stay reachable. Rarity bands live in the frontend
    (SmartType.jsx RARITY), so that group answers with the nominals present.
    """
    smart = {"usage": usage, "value": value, "nominal_min": nominal_min, "nominal_max": nominal_max}
    by_category = _matching(types_files, None, q, **smart)
    by_usage = _matching(types_files, category, q, **{**smart, "usage": None})
    by_value = _matching(types_files, category, q, **{**smart, "value": None})
    by_rarity = _matching(types_files, category, q, **{**smart, "nominal_min": None, "nominal_max": None})
    return {
        # filter_entries matches a row without a category as "(uncategorized)".
        "categories": sorted({e.category or "(uncategorized)" for e in by_category}),
        "usages": sorted({u for e in by_usage for u in e.usages}),
        "values": sorted({v for e in by_value for v in e.values}),
        "nominals": sorted({e.nominal for e in by_rarity}),
    }


@router.get("")
async def list_types(
    file: str | None = None,
    q: str = "",
    category: str | None = None,
    usage: str | None = None,
    value: str | None = None,
    nominal_min: int | None = None,
    nominal_max: int | None = None,
    offset: int = 0,
    limit: int = 100,
    manual: str | None = None,
    state: WebState = Depends(get_state),
) -> dict:
    manual_path = _manual_path(state, manual)

    def _load() -> dict:
        types_files = _load_files(state.config, manual_path)
        if file:
            types_files = [tf for tf in types_files if str(tf.path) == file]
        smart = {"usage": usage, "value": value, "nominal_min": nominal_min, "nominal_max": nominal_max}
        entries = _matching(types_files, category, q, **smart)
        total = len(entries)
        page = entries[offset : offset + limit]
        return {
            "entries": [_entry_dict(e) for e in page],
            "total": total,
            "facets": _facets(types_files, category, q, **smart),
        }

    return await asyncio.to_thread(_load)


@router.get("/categories")
async def item_categories(state: WebState = Depends(get_state)) -> dict:
    """Every types.xml item, lowercased, with the category its icon shows.

    The web UI puts an icon before an item name on any page, also where the
    row has no CE category of its own (TraderPlus prices). The category is
    the economy analyzer's (`icon_category`), so an item without one is
    still placed by its name, and medicine shows as medical, not as tools.
    A later file wins for a name in two files, as DayZ loads them.
    """
    from app.ops.economy_analyzer import icon_category

    def _load() -> dict[str, str]:
        return {
            entry.name.lower(): icon_category(entry)
            for tf in _load_files(state.config)
            for entry in tf.entries
        }

    return {"categories": await asyncio.to_thread(_load)}


@router.get("/meta")
async def types_meta(state: WebState = Depends(get_state)) -> dict:
    def _load() -> dict:
        types_files = _load_files(state.config)
        m_dir = mission_dir(state.config)
        limits = limits_choices(m_dir)
        if mission_limits(m_dir) is not None:
            # The mission's own names, and only those: a save refuses a
            # name its cfglimitsdefinition.xml does not define.
            in_use = {"category": [], "usage": [], "value": [], "tag": []}
        else:
            in_use = {
                "category": collect_all_categories(types_files),
                "usage": collect_usages(types_files),
                "value": collect_values(types_files),
                "tag": [e.tag for tf in types_files for e in tf.entries if e.tag],
            }
        return {
            "categories": sorted(set(in_use["category"]) | set(limits["category"])),
            "usages": sorted(set(in_use["usage"]) | set(limits["usage"])),
            "values": sorted(set(in_use["value"]) | set(limits["value"])),
            "tags": sorted(set(in_use["tag"]) | set(limits["tag"])),
            "flags": FLAG_NAMES,
            "nested_switches": FLAG_PARTS,
            "nested_help": nested_help(FIELD_DESCRIPTIONS),
        }

    return await asyncio.to_thread(_load)


def _find_entry(types_files: list[TypesFile], source_file: str, name: str):
    for tf in types_files:
        if str(tf.path) != source_file:
            continue
        for entry in tf.entries:
            if entry.name == name:
                return tf, entry
    return None, None


NUMERIC_FIELDS = ("nominal", "min", "lifetime", "restock", "quantmin", "quantmax", "cost")


def _type_number_problems(entry, data: dict) -> list[str]:
    """What is wrong with *data* applied to *entry*; empty when nothing is.

    `types_xml.type_number_problems` decides, on the row as it would be saved,
    so raising ``min`` alone past the current nominal is refused too; a value
    the request sends back unchanged is not judged.
    """
    changes = {k: data[k] for k in NUMERIC_FIELDS if data.get(k) is not None}
    return type_number_problems(entry_numbers(entry), changes)


def _custom_warning(path: Path) -> str | None:
    """The sentence for a write into the mission's custom/ folder, or None.

    Every merge (a mod install or update, Re-apply all, a map switch) wipes
    and rebuilds custom/ from the mods, so such an edit was lost without a
    word. The save still happens: refusing it would
    remove what the page does today, which needs the maintainer's approval
    (app/xml/CLAUDE.md wants custom/ read-only in every editor).
    """
    if path.parent.name == "custom":
        return (
            f"Saved into {path.name}, a mod's copy in custom/. Every merge (a mod install or "
            "update, Re-apply all) rebuilds that folder from the mod, which undoes this edit. "
            "To keep it, take the file over on the Custom config page."
        )
    return None


@router.put("/entry")
async def update_entry(
    body: TypesEntryUpdateRequest, manual: str | None = None, state: WebState = Depends(get_state)
) -> dict:
    manual_path = _manual_path(state, manual, write=True)
    data = body.model_dump(exclude_unset=True)
    source_file = data.pop("source_file")
    name = data.pop("name")
    tree_edit = {NESTED: data.pop("nested", None), NESTED_BASE: data.pop("nested_base", None)}

    def _do() -> dict:
        types_files = _load_files(state.config, manual_path)
        tf, entry = _find_entry(types_files, source_file, name)
        if entry is None:
            raise RuntimeError(f"No type entry named {name!r} in {source_file}")
        # Before the first set_*: the cached entry is mutated in place.
        problems = _type_number_problems(entry, data)
        if problems:
            raise ValueError("; ".join(problems))

        before = limit_names_of(entry.element)
        try:
            # First: a stale tree is refused before anything else is set.
            apply_parts(entry.element, PAGE_PARTS, tree_edit, where=name)
            # Only what changed: an override in custom/ that leaves out
            # <lifetime> must not gain one because another cell was edited
            # (central-economy-mission-files-modding.wiki:38).
            for field_name in NUMERIC_FIELDS:
                value = data.get(field_name)
                if value is not None and int(value) != getattr(entry, field_name):
                    set_numeric_field(entry, field_name, int(value))
            if "category" in data:
                set_category(entry, data["category"] or None)
            if "tag" in data:
                set_tag(entry, data["tag"] or None)
            if data.get("usages") is not None:
                set_usages(entry, list(data["usages"]))
            if data.get("values") is not None:
                set_values(entry, list(data["values"]))
            flags = data.get("flags") or {}
            for flag_name in FLAG_NAMES:
                if flag_name in flags:
                    set_flag(entry, flag_name, int(flags[flag_name]))
            problem = undefined_names_problem(
                name, entry.element, before, mission_limits(mission_dir(state.config))
            )
            if problem:
                raise ValueError(problem)

            snapshot(tf.path, trigger="editor")
            save_types_files([tf])
        except BaseException:
            # The cached TypesFile was mutated in place — a failure between
            # mutation and save must not leave half-applied edits in the cache
            # (GETs would serve them and the next save would persist them).
            _invalidate(tf.path)
            raise
        _invalidate(tf.path)
        row = _entry_dict(entry)
        warning = _custom_warning(tf.path)
        return {**row, "warning": warning} if warning else row

    try:
        row = await asyncio.to_thread(_one_write, _do)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    await state.hub.broadcast({"type": "configs_changed"})
    return row


@router.get("/multiplier/preview")
async def preview_multiplier(
    file: str,
    category: str | None = None,
    q: str = "",
    usage: str | None = None,
    value: str | None = None,
    nominal_min: int | None = None,
    nominal_max: int | None = None,
    nominal: float = 1.0,
    min: float = 1.0,  # noqa: A002
    lifetime: float = 1.0,
    restock: float = 1.0,
    manual: str | None = None,
    state: WebState = Depends(get_state),
) -> dict:
    check_multipliers(nominal=nominal, min=min, lifetime=lifetime, restock=restock)
    manual_path = _manual_path(state, manual)

    def _do() -> dict:
        types_files = [tf for tf in _load_files(state.config, manual_path) if str(tf.path) == file]
        entries = _matching(
            types_files, category, q,
            usage=usage, value=value, nominal_min=nominal_min, nominal_max=nominal_max,
        )
        mult = MultiplierConfig(nominal=nominal, min=min, lifetime=lifetime, restock=restock)
        return {
            "changes": [
                {"name": e.name, **apply_multiplier(e, mult)} for e in entries
            ]
        }

    return await asyncio.to_thread(_do)


async def _bulk_write(
    state: WebState, body: TypesFilterRequest, problems, write, hint: str, manual: str | None = None,
) -> dict:
    """Write one change to every row the page's filters match, in one save.

    `problems(entries)` names the rows the change would leave with min above
    nominal, and refuses the whole write before the first change, with
    `hint` as the way out. `write(entry)` changes one row. The multiplier and the Filler share it.
    *manual* pins it to one manual types file (`_manual_path`).
    """
    manual_path = _manual_path(state, manual, write=True)

    def _do() -> tuple[int, str | None]:
        types_files = [tf for tf in _load_files(state.config, manual_path) if str(tf.path) == body.file]
        if not types_files:
            raise RuntimeError(f"Unknown types file: {body.file}")
        tf = types_files[0]
        # The rows the page counted, its filters included, and refused
        # when that is no longer the count its dialog showed.
        entries = _matching(
            types_files, body.category, body.q,
            usage=body.usage, value=body.value,
            nominal_min=body.nominal_min, nominal_max=body.nominal_max,
        )
        if body.expected is not None and body.expected != len(entries):
            raise ValueError(
                f"The filter now matches {len(entries)} entries, not the {body.expected} "
                "the dialog showed. Nothing was changed; check the list and apply again."
            )
        # Before the first write: a nominal below its min is refused here as
        # it is for one row (types_xml.type_number_problems).
        bad = problems(entries)
        if bad:
            shown = ", ".join(bad[:5]) + (f" and {len(bad) - 5} more" if len(bad) > 5 else "")
            raise ValueError(
                f"This would put min above nominal for {len(bad)} "
                f"entries ({shown}). Nothing was changed: {hint}"
            )
        try:
            for entry in entries:
                write(entry)
            snapshot(tf.path, trigger="editor")
            save_types_files([tf])
        except BaseException:
            # Same guard as `update_entry`: the cached tree is edited in place,
            # so a failure half way through left the first entries multiplied
            # in the cache, and the next unrelated save wrote them to disk
            # (test pass 2, N28).
            _invalidate(tf.path)
            raise
        _invalidate(tf.path)
        return len(entries), _custom_warning(tf.path)

    try:
        count, warning = await asyncio.to_thread(_one_write, _do)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    await state.hub.broadcast({"type": "configs_changed"})
    return {"updated": count, **({"warning": warning} if warning else {})}


@router.post("/multiplier")
async def apply_multiplier_endpoint(
    body: TypesMultiplierRequest, manual: str | None = None, state: WebState = Depends(get_state)
) -> dict:
    """Multiply the filtered rows' numbers."""
    check_multipliers(
        nominal=body.nominal, min=body.min, lifetime=body.lifetime, restock=body.restock
    )
    mult = MultiplierConfig(
        nominal=body.nominal,
        min=body.min,
        lifetime=body.lifetime,
        restock=body.restock,
    )
    return await _bulk_write(
        state, body, lambda entries: multiplier_problems(entries, mult),
        lambda entry: write_multiplier(entry, mult),
        "scale min no more than nominal.", manual,
    )


@router.post("/fill")
async def fill_endpoint(
    body: TypesFillRequest, manual: str | None = None, state: WebState = Depends(get_state)
) -> dict:
    """The Filler: give the filtered rows a number where they have 0."""
    fill = {
        name: getattr(body, name)
        for name in ("nominal", "min", "lifetime", "restock")
        if getattr(body, name) is not None
    }
    if not fill:
        raise HTTPException(status_code=400, detail="Give at least one value to fill.")

    def write(entry) -> None:
        for name, value in fill_changes(entry, fill).items():
            set_numeric_field(entry, name, value)

    return await _bulk_write(
        state, body, lambda entries: fill_problems(entries, fill), write,
        "fill nominal too, or fill min with a smaller number.", manual,
    )


@router.post("/restore")
async def restore_types(
    body: TypesRestoreRequest, state: WebState = Depends(get_state)
) -> dict:
    file = body.file

    def _do() -> bool:
        # Only files under the active mission may be restored — mirrors the
        # entry/multiplier endpoints, which operate solely on scanned files.
        m_dir = mission_dir(state.config)
        allowed = set(scan_types_files(m_dir)) if m_dir else set()
        path = Path(file)
        if path not in allowed:
            raise RuntimeError(f"Unknown types file: {file}")
        from app.ops.mission_stock import shipped_copy

        restored = restore_types_file(path, shipped_copy(state.config, path))
        if restored:
            _invalidate(path)
        return restored

    try:
        restored = await asyncio.to_thread(_one_write, _do)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    if restored:
        await state.hub.broadcast({"type": "configs_changed"})
    return {"restored": restored}
