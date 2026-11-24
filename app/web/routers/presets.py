"""GET /api/presets, POST /api/presets/{id}/preview, POST /api/presets/{id}/apply,
GET /api/tweaks, POST /api/tweaks/{id}."""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.presets.preset_engine import snapshot_preset_targets
from app.web.deps import get_state, require_session
from app.web.editors._paths import mission_dir
from app.web.multipliers import check_multipliers
from app.web.schemas import TweakApplyRequest, TweakResetRequest
from app.web.state import WebState

router = APIRouter(prefix="/api", tags=["presets"], dependencies=[Depends(require_session)])

_CUSTOM_PREFIX = "custom:"


def _preset_summary(preset_id: str, preset) -> dict:
    return {
        "id": preset_id,
        "name": preset.name,
        "description": preset.description,
        "icon": preset.icon,
        "is_builtin": preset.is_builtin,
        "recommended_mods": preset.recommended_mods,
    }


def _list_presets(config) -> list[dict]:
    from app.presets.presets import BUILTIN_PRESETS
    from app.presets.preset_storage import list_custom_presets, load_custom_preset

    out = [_preset_summary(pid, preset) for pid, preset in BUILTIN_PRESETS.items()]
    for name, path in list_custom_presets(config.paths.presets_dir):
        try:
            preset = load_custom_preset(path)
        except Exception:
            continue
        out.append(_preset_summary(f"{_CUSTOM_PREFIX}{path.stem}", preset))
    return out


def _resolve_preset(config, preset_id: str):
    from app.presets.presets import BUILTIN_PRESETS
    from app.presets.preset_storage import load_custom_preset

    if preset_id.startswith(_CUSTOM_PREFIX):
        stem = preset_id[len(_CUSTOM_PREFIX):]
        # stem is attacker-controlled (URL path segment) — reject anything
        # that isn't a bare filename component before joining it onto
        # the presets dir, or "custom:../../etc/passwd" style ids could read
        # files outside the presets directory.
        if not stem or Path(stem).name != stem:
            return None
        path = config.paths.presets_dir / f"{stem}.json"
        if not path.is_file():
            return None
        try:
            return load_custom_preset(path)
        except Exception:
            return None
    return BUILTIN_PRESETS.get(preset_id)


@router.get("/presets")
async def list_presets(state: WebState = Depends(get_state)) -> list[dict]:
    return await asyncio.to_thread(_list_presets, state.config)


def _require_mission_dir(config) -> Path:
    m_dir = mission_dir(config)
    if m_dir is None or not m_dir.is_dir():
        raise HTTPException(status_code=409, detail="No active mission found — set a map first.")
    return m_dir


@router.post("/presets/{preset_id}/preview")
async def preview_preset_endpoint(preset_id: str, state: WebState = Depends(get_state)) -> dict:
    from app.presets.preset_engine import preview_preset

    config = state.config
    preset = await asyncio.to_thread(_resolve_preset, config, preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail=f"Unknown preset: {preset_id!r}")

    def _run() -> dict:
        m_dir = _require_mission_dir(config)
        result = preview_preset(config, preset, m_dir)
        return {
            "changes": [dataclasses.asdict(c) for c in result.changes],
            "files_affected": sorted(result.files_affected),
            "warnings": result.warnings,
        }

    try:
        return await asyncio.to_thread(_run)
    except ValueError as e:  # a file the preset reads can't be read (cfggameplay.json)
        raise HTTPException(status_code=400, detail=str(e))


def _apply_to_mission(config, preset, preset_id: str) -> list[str]:
    """Snapshot the files a preset changes, then apply it. It returns the changed files."""
    from app.presets.preset_engine import apply_preset

    m_dir = _require_mission_dir(config)
    snapshot_preset_targets(config, m_dir, trigger=f"preset_apply:{preset_id}")
    return apply_preset(config, preset, m_dir)


def apply_preset_to_mission(config, preset_id: str) -> list[str]:
    """Apply a known preset by its ID, for a job that holds no preset object."""
    return _apply_to_mission(config, _resolve_preset(config, preset_id), preset_id)


@router.post("/presets/{preset_id}/apply")
async def apply_preset_endpoint(preset_id: str, state: WebState = Depends(get_state)) -> dict:
    config = state.config
    preset = await asyncio.to_thread(_resolve_preset, config, preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail=f"Unknown preset: {preset_id!r}")

    try:
        modified = await asyncio.to_thread(_apply_to_mission, config, preset, preset_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    await state.hub.broadcast({"type": "configs_changed"})
    return {"modified": modified}


@router.post("/presets/custom")
async def save_custom_preset_endpoint(
    body: dict, state: WebState = Depends(get_state)
) -> dict:
    """Snapshot the current server config as a new custom preset."""
    from app.presets.preset_storage import (
        PresetNameClash,
        save_custom_preset,
        snapshot_current_config,
    )

    name = str(body.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=422, detail="Preset name is required.")
    description = str(body.get("description", "")).strip()

    config = state.config

    def _run() -> str:
        preset = snapshot_current_config(config, name)
        if description:
            preset.description = description
        path = save_custom_preset(config.paths.presets_dir, preset)
        return path.stem

    try:
        stem = await asyncio.to_thread(_run)
    except PresetNameClash as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"id": f"{_CUSTOM_PREFIX}{stem}", "name": name}


@router.delete("/presets/custom/{stem}")
async def delete_custom_preset_endpoint(stem: str, state: WebState = Depends(get_state)) -> dict:
    from app.presets.preset_storage import delete_custom_preset

    # stem is attacker-controlled (URL path segment) — same traversal guard
    # as _resolve_preset.
    if not stem or Path(stem).name != stem:
        raise HTTPException(status_code=404, detail="Unknown custom preset.")
    path = state.config.paths.presets_dir / f"{stem}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Unknown custom preset.")
    await asyncio.to_thread(delete_custom_preset, path)
    return {"ok": True}


# A mission file that cannot be read or parsed (ET.ParseError is a SyntaxError).
_UNREADABLE = (OSError, ValueError, SyntaxError)


def _mission_event_names(config) -> set[str]:
    """The event names in the mission's db/events.xml; empty without a readable one.

    A variant's Map Editor link shows only for an event the mission has, so an
    older or community mission never gets a link to an empty layer.
    """
    from app.xml.events_xml import get_events_path, parse_events_file

    m_dir = mission_dir(config)
    path = get_events_path(m_dir) if m_dir is not None and m_dir.is_dir() else None
    if path is None:
        return set()
    try:
        return {e.name for e in parse_events_file(path).entries}
    except _UNREADABLE:
        return set()


def _tweak_summary(tweak_id: str, tweak, event_names: set[str] = frozenset()) -> dict:
    """What the Quick Tweaks card shows for *tweak_id*; *event_names* gates each variant's map link."""
    from app.presets.quick_tweaks import variant_spec

    # has_events: the stack scales events.xml spawns, so a tier of 0 leaves the
    # ones already on the map in place — the UI shows zero_warning for it.
    def has_events(variant: str | None) -> bool:
        spec = variant_spec(tweak_id, variant)
        return spec is not None and spec.has_events

    return {
        "id": tweak_id,
        "name": tweak.name,
        "description": tweak.description,
        "tiers": [dataclasses.asdict(t) for t in tweak.tiers],
        "variants": [
            {
                "key": v.key, "name": v.name, "description": v.description, "has_events": has_events(v.key),
                "map_event": v.map_event if v.map_event in event_names else "",
            }
            for v in tweak.variants
        ],
        "has_events": has_events(None),
        "zero_warning": tweak.zero_warning,
    }


def _tweak_current(config, tweak_id: str, tweak) -> dict:
    """``{variant key: multiplier}``, with "" for a tweak without variants and
    null for Custom. It is empty when the server has no mission to read."""
    from app.presets.quick_tweaks import current_multiplier

    m_dir = mission_dir(config)
    if m_dir is None or not m_dir.is_dir():
        return {}
    keys = [v.key for v in tweak.variants] or [None]
    try:
        return {key or "": current_multiplier(tweak_id, m_dir, key) for key in keys}
    except _UNREADABLE:  # an unreadable mission file: the card keeps its 1x start
        return {}


@router.get("/tweaks")
async def list_tweaks(state: WebState = Depends(get_state)) -> list[dict]:
    from app.presets.quick_tweaks import QUICK_TWEAKS

    def _load() -> list[dict]:
        event_names = _mission_event_names(state.config)
        return [
            _tweak_summary(tid, t, event_names) | {"current": _tweak_current(state.config, tid, t)}
            for tid, t in QUICK_TWEAKS.items()
        ]

    return await asyncio.to_thread(_load)


async def _write_tweak(
    tweak_id: str, variant: str | None, state: WebState, trigger: str,
    op: Callable[[Any, Path, str | None], list[str]],
) -> dict:
    """Shared body of the apply and reset routes: validate, snapshot, run, notify."""
    from app.presets.quick_tweaks import QUICK_TWEAKS, resolve_variant

    if tweak_id not in QUICK_TWEAKS:
        raise HTTPException(status_code=404, detail=f"Unknown tweak: {tweak_id!r}")
    try:
        resolved = resolve_variant(tweak_id, variant)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=e.args[0])

    config = state.config

    def _run() -> list[str]:
        m_dir = _require_mission_dir(config)
        snapshot_preset_targets(config, m_dir, trigger=f"{trigger}:{tweak_id}")
        return op(config, m_dir, resolved)

    try:
        modified = await asyncio.to_thread(_run)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    await state.hub.broadcast({"type": "configs_changed"})
    return {"modified": modified}


@router.post("/tweaks/{tweak_id}")
async def apply_tweak_endpoint(
    tweak_id: str, body: TweakApplyRequest, state: WebState = Depends(get_state)
) -> dict:
    """Apply quick tweak *tweak_id* at the multiplier of the tier the page picked."""
    from app.presets.quick_tweaks import apply_tweak

    check_multipliers(allow_zero=True, tweak=body.multiplier)
    return await _write_tweak(
        tweak_id, body.variant, state, "tweak_apply",
        lambda config, m_dir, variant: apply_tweak(tweak_id, config, m_dir, body.multiplier, variant),
    )


@router.post("/tweaks/{tweak_id}/reset")
async def reset_tweak_endpoint(
    tweak_id: str, body: TweakResetRequest, state: WebState = Depends(get_state)
) -> dict:
    """Put one tweak's values back to what they were before the first tweak."""
    from app.presets.quick_tweaks import reset_tweak

    return await _write_tweak(
        tweak_id, body.variant, state, "tweak_reset",
        lambda config, m_dir, variant: reset_tweak(tweak_id, config, m_dir, variant),
    )
