"""Custom preset persistence — save/load/delete user presets.

Presets belong to an instance (plans/44 §2 Q2): callers pass
``config.paths.presets_dir``.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

from app.presets.presets import (
    EventsLayer,
    GameplayJsonLayer,
    GlobalsLayer,
    PresetDefinition,
    ServerCfgLayer,
    TypesLayer,
)
from app.shared.json_utils import atomic_write_text

# ---------------------------------------------------------------------------
# List / delete
# ---------------------------------------------------------------------------

def list_custom_presets(presets_dir: Path) -> list[tuple[str, Path]]:
    """Return list of (display_name, path) for saved custom presets."""
    if not presets_dir.is_dir():
        return []

    results: list[tuple[str, Path]] = []
    for p in sorted(presets_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            name = data.get("name", p.stem)
        except (OSError, ValueError, AttributeError):
            # Not JSON, not UTF-8 or not an object: listed under its file name.
            name = p.stem
        results.append((name, p))

    return results


def delete_custom_preset(path: Path) -> None:
    """Delete a custom preset file."""
    if path.is_file():
        path.unlink()


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def _sanitize_filename(name: str) -> str:
    """Convert a preset name to a safe filename."""
    safe = re.sub(r"[^\w\s-]", "", name).strip().lower()
    safe = re.sub(r"[\s]+", "_", safe)
    return safe or "custom_preset"


class PresetNameClash(ValueError):
    """Another preset already owns the file this name maps to."""

    def __init__(self, existing: str) -> None:
        self.existing = existing
        super().__init__(
            f"A preset called {existing!r} already uses that file name; pick another name."
        )


def save_custom_preset(
    presets_dir: Path, preset: PresetDefinition, filename: str | None = None
) -> Path:
    """Serialize a PresetDefinition to JSON on disk. Returns the saved path.

    Saving under the same name again updates that preset. A *different* name
    that sanitizes to the same file ("Zz Test" and "zz test!", or "!!!" and
    "???") raises ``PresetNameClash`` instead of silently replacing the other
    preset (test pass 2, N26). A file that cannot be read is not overwritten
    either: there is no telling whose it is.
    """
    presets_dir.mkdir(parents=True, exist_ok=True)

    if not filename:
        filename = _sanitize_filename(preset.name)
    if not filename.endswith(".json"):
        filename += ".json"

    path = presets_dir / filename
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8")).get("name")
        except (OSError, ValueError, AttributeError):
            existing = path.stem
        if existing != preset.name:
            raise PresetNameClash(str(existing))
    data = _preset_to_dict(preset)
    # Not write_text: it empties the file before it encodes, so text that
    # cannot be encoded would leave the preset being updated at 0 bytes.
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return path


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load_custom_preset(path: Path) -> PresetDefinition:
    """Deserialize a PresetDefinition from a JSON file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return _dict_to_preset(data)


# ---------------------------------------------------------------------------
# Snapshot current config
# ---------------------------------------------------------------------------

def snapshot_current_config(config: Any, name: str) -> PresetDefinition:
    """Read current server state and package it as a PresetDefinition."""
    from app.parsers.cfggameplay import get_cfggameplay_path, read_cfggameplay
    from app.xml.events_xml import get_events_path
    from app.xml.globals_xml import get_globals_path, parse_globals_file
    from app.modmgr.mods import get_current_mission
    from app.parsers.servercfg import read_cfg_values

    mission = get_current_mission(config.server_cfg_path)
    mission_dir = config.server_dir_path / "mpmissions" / mission if mission else None

    # --- serverDZ.cfg ---
    cfg_keys = ["maxPlayers", "thirdPersonView", "disable3rdPerson", "disableCrosshair",
                "enableCfgGameplayFile", "respawnTime"]
    cfg_vals = read_cfg_values(config.server_cfg_path, cfg_keys)

    server_cfg = ServerCfgLayer(
        maxPlayers=_safe_int(cfg_vals.get("maxPlayers")),
        thirdPersonView=_third_person_view(cfg_vals),
        disableCrosshair=_safe_int(cfg_vals.get("disableCrosshair")),
        # Absent means DayZ's default, 0. Saved as None, a later preset's 1
        # stayed when this preset was applied again (test pass 2, P11).
        enableCfgGameplayFile=_safe_int(cfg_vals.get("enableCfgGameplayFile") or "0"),
        respawnTime=_safe_int(cfg_vals.get("respawnTime")),
    )

    # --- globals.xml ---
    globals_layer = GlobalsLayer()
    if mission_dir and mission_dir.is_dir():
        gpath = get_globals_path(mission_dir)
        if gpath:
            gf = parse_globals_file(gpath)
            globals_layer.values = {e.name: e.value for e in gf.entries}

    # --- cfggameplay.json ---
    gameplay_layer = GameplayJsonLayer()
    if mission_dir and mission_dir.is_dir():
        gp_path = get_cfggameplay_path(mission_dir)
        if gp_path.is_file():
            data = read_cfggameplay(gp_path)
            gameplay_layer.PlayerData = data.get("PlayerData")
            gameplay_layer.WorldsData = data.get("WorldsData") or data.get("WorldData")
            gameplay_layer.GeneralData = data.get("GeneralData")
            gameplay_layer.UIData = data.get("UIData")
            gameplay_layer.MapData = data.get("MapData")

    return PresetDefinition(
        name=name,
        description=f"Snapshot of current server configuration",
        icon="[dim]●[/dim]",
        server_cfg=server_cfg,
        gameplay_json=gameplay_layer,
        events=EventsLayer(),       # multipliers can't be snapshotted meaningfully
        types=TypesLayer(),         # same — would need to read all types files
        globals=globals_layer,
        recommended_mods=[],
        is_builtin=False,
    )


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _safe_int(val: str | None) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except ValueError:
        return None


def _preset_to_dict(preset: PresetDefinition) -> dict[str, Any]:
    """Convert a PresetDefinition to a JSON-serializable dict."""
    d: dict[str, Any] = {
        "name": preset.name,
        "description": preset.description,
        "icon": preset.icon,
        "is_builtin": preset.is_builtin,
        "recommended_mods": preset.recommended_mods,
    }

    # Server cfg
    scfg: dict[str, Any] = {}
    for f in fields(preset.server_cfg):
        val = getattr(preset.server_cfg, f.name)
        if val is not None:
            scfg[f.name] = val
    d["server_cfg"] = scfg

    # Gameplay JSON
    gpj: dict[str, Any] = {}
    for f in fields(preset.gameplay_json):
        val = getattr(preset.gameplay_json, f.name)
        if val is not None:
            gpj[f.name] = val
    d["gameplay_json"] = gpj

    # Events
    ev: dict[str, Any] = {}
    for f in fields(preset.events):
        val = getattr(preset.events, f.name)
        if val is not None:
            ev[f.name] = val
    d["events"] = ev

    # Types
    ty: dict[str, Any] = {}
    for f in fields(preset.types):
        val = getattr(preset.types, f.name)
        if val is not None:
            ty[f.name] = val
    d["types"] = ty

    # Globals
    d["globals"] = {"values": preset.globals.values}

    return d


def _third_person_view(cfg_vals: dict) -> int | None:
    """thirdPersonView for a preset saved from a serverDZ.cfg. A file from
    before DayZ 1.30 holds only disable3rdPerson, whose 1 (third person off)
    is thirdPersonView's 0."""
    new = _safe_int(cfg_vals.get("thirdPersonView"))
    if new is not None:
        return new
    old = _safe_int(cfg_vals.get("disable3rdPerson"))
    return None if old is None else 0 if old else 1


def _dict_to_preset(data: dict[str, Any]) -> PresetDefinition:
    """Convert a dict from JSON to a PresetDefinition."""
    scfg_data = data.get("server_cfg", {})
    server_cfg = ServerCfgLayer(
        maxPlayers=scfg_data.get("maxPlayers"),
        thirdPersonView=scfg_data.get("thirdPersonView"),
        disableCrosshair=scfg_data.get("disableCrosshair"),
        enableCfgGameplayFile=scfg_data.get("enableCfgGameplayFile"),
        respawnTime=scfg_data.get("respawnTime"),
    )

    gpj_data = data.get("gameplay_json", {})
    gameplay_json = GameplayJsonLayer(
        PlayerData=gpj_data.get("PlayerData"),
        WorldsData=gpj_data.get("WorldsData") or gpj_data.get("WorldData"),
        GeneralData=gpj_data.get("GeneralData"),
        UIData=gpj_data.get("UIData"),
        MapData=gpj_data.get("MapData"),
    )

    ev_data = data.get("events", {})
    events = EventsLayer(
        infected_nominal=ev_data.get("infected_nominal"),
        wildlife_nominal=ev_data.get("wildlife_nominal"),
    )

    ty_data = data.get("types", {})
    types = TypesLayer(
        loot_nominal=ty_data.get("loot_nominal"),
        loot_min=ty_data.get("loot_min"),
        loot_lifetime=ty_data.get("loot_lifetime"),
        loot_restock=ty_data.get("loot_restock"),
    )

    gl_data = data.get("globals", {})
    # Coerce to the declared dict[str, str] contract: a hand-edited preset may
    # carry JSON numbers, but the apply path treats every value as a string
    # (e.g. `"." in value` to pick int/float type) and would crash on an int.
    raw_values = gl_data.get("values", {})
    globals_layer = GlobalsLayer(
        values={str(k): str(v) for k, v in raw_values.items()}
        if isinstance(raw_values, dict)
        else {},
    )

    return PresetDefinition(
        name=data.get("name", "Custom Preset"),
        description=data.get("description", ""),
        icon=data.get("icon", "[dim]●[/dim]"),
        server_cfg=server_cfg,
        gameplay_json=gameplay_json,
        events=events,
        types=types,
        globals=globals_layer,
        recommended_mods=data.get("recommended_mods", []),
        is_builtin=False,
    )
