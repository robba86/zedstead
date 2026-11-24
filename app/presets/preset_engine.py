"""Preset engine — plan, preview and apply presets across the server's config files.

One function decides what a preset changes: :func:`plan_preset`. It edits the
files in memory and returns, per file, the change rows and a writer. The
preview renders the rows; apply runs the writers. So the preview *is* the
apply without the write, and cannot list less than apply writes (plans/50 C,
finding F27: it used to omit the ``enableCfgGameplayFile = 1`` apply wrote).

**Built-in presets are absolute** (:func:`app.presets.presets.absolute`). Each
writes every key any built-in manages, stock values for the ones it doesn't
name, so applying Vanilla after Hardcore no longer leaves first person, a
1200-zombie cap or half the loot behind (F26). A key is written **only where
it differs** from the file, and a file with no change is not rewritten.

**Multipliers scale from per-field baselines**, in ``<mission>/.zsm-presets.json``
(the Quick Tweaks machinery in :mod:`app.presets.field_baselines`, its own
sidecar), never from ``types.xml.backup`` / ``events.xml.backup``: a
``.backup`` holds the file as it was first backed up, and restoring either file
from its ``.backup`` threw away every edit made since. Only ``db/types.xml`` is
scaled; the mods' ``custom/*types*.xml`` are rebuilt by every merge, so scaling
them never lasted (decision 3). Those ``.backup`` files are left alone: the
editors' *Restore from backup* and the mod merge use them.

How presets and Quick Tweaks interact follows from the baseline rule — *any
value on disk other than what was last written becomes the new base*:

- a tweak made after a preset scales the preset's value;
- a preset applied after a tweak takes the tweaked value as its base;
- Vanilla (every multiplier 1.0) puts back exactly what the last preset
  changed, and nothing else.

That is the behaviour, not an accident. For a plain key, by contrast, Vanilla
writes the **stock** value: a hand-set ``ZombieMaxCount 1500`` becomes 1000.
No single stock ``types.xml`` exists for a community map or a modded mission,
which is why multipliers go back to the pre-preset baseline instead.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.presets.field_baselines import (
    Compute,
    entry_records,
    file_records,
    load_baselines,
    save_baselines,
    scale_field,
    write_elements,
)
from app.presets.presets import (
    PARAMETER_SCALES,
    EventsLayer,
    GameplayJsonLayer,
    GlobalsLayer,
    PresetDefinition,
    ServerCfgLayer,
    TypesLayer,
    absolute,
    label_for_value,
)

# The presets' own baseline sidecar; see the module docstring.
PRESET_BASELINES = ".zsm-presets.json"
_TYPES_KEY = "db/types.xml"
_EVENTS_KEY = "db/events.xml"


# ---------------------------------------------------------------------------
# Infected / wildlife event name patterns
# ---------------------------------------------------------------------------

_INFECTED_PATTERN = re.compile(r"(?:infected|zombie)", re.IGNORECASE)
_WILDLIFE_PATTERN = re.compile(
    r"(?:animal|wolf|bear|deer|goat|sheep|pig|cow|hen|hare|fox|boar|stag)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# cfggameplay.json routing: a preset names these flat under PlayerData, the
# file keeps them in a sub-object.
# ---------------------------------------------------------------------------

_GAMEPLAY_SECTIONS = ("PlayerData", "WorldsData", "WorldData", "GeneralData", "UIData", "MapData")
_STAMINA_KEYS = frozenset({
    "sprintStaminaModifierErc", "sprintStaminaModifierCro",
    "staminaWeightLimitThreshold", "staminaMax",
    "staminaKgToStaminaPercentPenalty", "staminaMinCap",
    "sprintSwimmingStaminaModifier", "meleeStaminaModifier",
    "obstacleTraversalStaminaModifier", "holdBreathStaminaModifier",
    "sprintLadderStaminaModifier",
})
_DROWNING_KEYS = frozenset({
    "staminaDepletionSpeed", "healthDepletionSpeed", "shockDepletionSpeed",
})

_SERVER_CFG_KEYS = ("maxPlayers", "thirdPersonView", "disableCrosshair",
                    "enableCfgGameplayFile", "respawnTime")

# types.xml field -> (multiplier attribute on TypesLayer, row name, label scale)
_TYPES_FIELDS = (
    ("nominal", "loot_nominal", "Loot nominal", "loot_nominal"),
    ("min", "loot_min", "Loot min", "loot_nominal"),
    ("lifetime", "loot_lifetime", "Loot lifetime", "loot_lifetime"),
    ("restock", "loot_restock", "Loot restock", "loot_lifetime"),
)

_NOT_SET = "(not set)"


# ---------------------------------------------------------------------------
# Change tracking
# ---------------------------------------------------------------------------

@dataclass
class ChangeEntry:
    """One parameter change within a preset application."""
    file: str           # e.g. "serverDZ.cfg", "globals.xml", "events.xml"
    parameter: str
    old_value: str
    new_value: str
    label: str = ""     # PresetLabel name or ""


@dataclass
class PresetPreview:
    """Computed diff of what a preset would change."""
    changes: list[ChangeEntry] = field(default_factory=list)
    files_affected: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)


@dataclass
class FileWrite:
    """One file the preset changes: its rows, and the writer that saves the
    edit already made in memory."""
    file: str
    path: Path
    changes: list[ChangeEntry]
    write: Callable[[], None]


@dataclass
class PresetPlan:
    writes: list[FileWrite] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Sidecar record pruning that changes no value, so is reported nowhere.
    housekeeping: list[Callable[[], None]] = field(default_factory=list)

    @property
    def changes(self) -> list[ChangeEntry]:
        return [c for w in self.writes for c in w.changes]

    @property
    def files_affected(self) -> set[str]:
        return {w.file for w in self.writes}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def plan_preset(config: Any, preset: PresetDefinition, mission_dir: Path) -> PresetPlan:
    """Everything *preset* would change, edited in memory, nothing written."""
    preset = absolute(preset)
    plan = PresetPlan()

    gameplay = _plan_gameplay(mission_dir, preset.gameplay_json)
    server_cfg = _plan_server_cfg(config, preset.server_cfg, enable_gameplay=gameplay is not None)
    globals_ = _plan_globals(mission_dir, preset.globals, plan.warnings)
    fields = load_baselines(mission_dir, PRESET_BASELINES)
    events = _plan_events(mission_dir, preset.events, fields, plan)
    types = _plan_types(mission_dir, preset.types, fields, plan)
    plan.writes = [w for w in (server_cfg, gameplay, globals_, events, types) if w is not None]

    _warn_about_pre_sidecar_scaling(mission_dir, preset, plan)
    return plan


def preview_preset(config: Any, preset: PresetDefinition, mission_dir: Path) -> PresetPreview:
    """Compute what changes a preset would make without modifying any files."""
    plan = plan_preset(config, preset, mission_dir)
    return PresetPreview(
        changes=plan.changes,
        files_affected=plan.files_affected,
        warnings=plan.warnings,
    )


def apply_preset(config: Any, preset: PresetDefinition, mission_dir: Path) -> list[str]:
    """Apply a preset to the server. Returns the paths of the files written."""
    plan = plan_preset(config, preset, mission_dir)
    for w in plan.writes:
        w.write()
    for tidy in plan.housekeeping:
        tidy()
    return [str(w.path) for w in plan.writes]


# ---------------------------------------------------------------------------
# serverDZ.cfg
# ---------------------------------------------------------------------------

def _plan_server_cfg(config: Any, layer: ServerCfgLayer, *, enable_gameplay: bool) -> FileWrite | None:
    from app.parsers.servercfg import read_cfg_values, remove_cfg_key, replaced_keys, write_cfg_value

    wanted = {k: str(getattr(layer, k)) for k in _SERVER_CFG_KEYS if getattr(layer, k) is not None}
    if enable_gameplay:
        # The server ignores cfggameplay.json without it, so writing the file
        # sets it; before plans/50 C this write was missing from the preview.
        wanted["enableCfgGameplayFile"] = "1"
    if not wanted:
        return None

    path = Path(config.server_cfg_path)
    # The key a written one replaced (disable3rdPerson) leaves the file, and
    # the preview says so with "(not set)" as its new value.
    for old in replaced_keys(wanted):
        wanted[old] = _NOT_SET
    current = read_cfg_values(path, list(wanted))
    changes = [
        ChangeEntry(file="serverDZ.cfg", parameter=key, old_value=current.get(key) or _NOT_SET, new_value=new)
        for key, new in wanted.items()
        if (current.get(key) or _NOT_SET) != new
    ]
    if not changes:
        return None

    def write() -> None:
        for c in changes:
            if c.new_value == _NOT_SET:
                remove_cfg_key(path, c.parameter)
            else:
                write_cfg_value(path, c.parameter, c.new_value)

    return FileWrite("serverDZ.cfg", path, changes, write)


# ---------------------------------------------------------------------------
# cfggameplay.json
# ---------------------------------------------------------------------------

def _route_gameplay(section: str, data: dict[str, Any]) -> dict[str, Any]:
    """Put the keys a preset names flat under PlayerData where the file keeps them."""
    routed: dict[str, Any] = {}
    for key, value in data.items():
        if section == "PlayerData" and key in _STAMINA_KEYS:
            routed.setdefault("StaminaData", {})[key] = value
        elif section == "PlayerData" and key in _DROWNING_KEYS:
            routed.setdefault("DrowningData", {})[key] = value
        else:
            routed[key] = value
    return routed


_MISSING = object()


def _gameplay_diff(current: Any, wanted: dict[str, Any], prefix: str, out: list[ChangeEntry]) -> None:
    for key, value in wanted.items():
        path = f"{prefix}.{key}" if prefix else key
        cur = current.get(key, _MISSING) if isinstance(current, dict) else _MISSING
        if isinstance(value, dict):
            _gameplay_diff(cur if isinstance(cur, dict) else {}, value, path, out)
        elif cur is _MISSING or cur != value:
            out.append(ChangeEntry(
                file="cfggameplay.json",
                parameter=path,
                old_value=_NOT_SET if cur is _MISSING else json.dumps(cur),
                new_value=json.dumps(value),
            ))


def _plan_gameplay(mission_dir: Path, layer: GameplayJsonLayer) -> FileWrite | None:
    from app.parsers.cfggameplay import (
        backup_cfggameplay,
        get_cfggameplay_path,
        merge_sections,
        read_cfggameplay,
        write_cfggameplay,
    )

    wanted = {
        section: _route_gameplay(section, data)
        for section in _GAMEPLAY_SECTIONS
        if (data := getattr(layer, section, None))
    }
    if not wanted:
        return None

    path = get_cfggameplay_path(mission_dir)
    current = read_cfggameplay(path)
    changes: list[ChangeEntry] = []
    _gameplay_diff(current, wanted, "", changes)
    if not changes:
        return None
    merged = merge_sections(current, wanted)

    def write() -> None:
        backup_cfggameplay(path)
        write_cfggameplay(path, merged)

    return FileWrite("cfggameplay.json", path, changes, write)


# ---------------------------------------------------------------------------
# globals.xml
# ---------------------------------------------------------------------------

def _label(scale_key: str, value: Any) -> str:
    scale = PARAMETER_SCALES.get(scale_key)
    if not scale:
        return ""
    try:
        result = label_for_value(scale, float(value))
    except (TypeError, ValueError):
        return ""
    return result.value if result else ""


def _plan_globals(mission_dir: Path, layer: GlobalsLayer, warnings: list[str]) -> FileWrite | None:
    if not layer.values:
        return None

    from app.xml.globals_xml import (
        add_var,
        backup_globals_file,
        get_globals_path,
        parse_globals_file,
        save_globals_file,
        set_var_value,
    )

    path = get_globals_path(mission_dir)
    if not path:
        warnings.append("globals.xml not found — globals layer will be skipped")
        return None

    gf = parse_globals_file(path)
    entry_map = {e.name: e for e in gf.entries}
    changes: list[ChangeEntry] = []
    for name, new in layer.values.items():
        entry = entry_map.get(name)
        old = entry.value if entry else _NOT_SET
        if old == new:
            continue
        changes.append(ChangeEntry(
            file="globals.xml", parameter=name, old_value=old, new_value=new, label=_label(name, new),
        ))
        if entry:
            set_var_value(entry, new)
        else:
            add_var(gf, name, 1 if "." in new else 0, new)  # type: int=0, float=1
    if not changes:
        return None

    def write() -> None:
        save_globals_file(gf)
        # Backup AFTER saving so preset values become the baseline for the
        # Quick Tweaks zombie and animal cards, which read globals.xml.backup.
        backup_globals_file(path)

    return FileWrite("globals.xml", path, changes, write)


# ---------------------------------------------------------------------------
# events.xml / types.xml multipliers
# ---------------------------------------------------------------------------

def _scaler(multiplier: float) -> Compute:
    """Scale from the recorded baseline; leave a field alone when that changes
    nothing, and forget a record once the field is back at its base."""
    def compute(records: dict, key: str, on_disk: int) -> int | None:
        new = scale_field(records, key, on_disk, multiplier)
        if records[key]["base"] == new:
            del records[key]  # at its baseline: nothing left to remember
        return None if new == on_disk else new
    return compute


def _multiplied_file(
    mission_dir: Path, file: str, path: Path, fields: dict, file_key: str,
    records: dict, rows: list[ChangeEntry], save: Callable[[], None], plan: PresetPlan,
) -> FileWrite | None:
    """The write for a multiplied file, or only a sidecar tidy when no value changed."""
    if not rows:
        # Entries visited but never scaled leave empty maps behind, which the
        # sidecar doesn't store; compare without them, or every apply would
        # rewrite it for nothing.
        live = {name: rec for name, rec in records.items() if rec}
        if live != (fields.get(file_key) or {}):
            def tidy() -> None:
                fields[file_key] = records
                save_baselines(mission_dir, fields, PRESET_BASELINES)
            plan.housekeeping.append(tidy)
        return None

    def write() -> None:
        save()
        fields[file_key] = records
        save_baselines(mission_dir, fields, PRESET_BASELINES)

    return FileWrite(file, path, rows, write)


def _multiplier_row(file: str, what: str, count: int, noun: str, multiplier: float, scale_key: str) -> ChangeEntry:
    return ChangeEntry(
        file=file,
        parameter=f"{what} ({count} {noun}{'' if count == 1 else 's'})",
        old_value="current",
        new_value=f"{multiplier}x",
        label=_label(scale_key, multiplier),
    )


def _plan_events(mission_dir: Path, layer: EventsLayer, fields: dict, plan: PresetPlan) -> FileWrite | None:
    groups = [
        (pattern, mult, what, scale_key)
        for pattern, mult, what, scale_key in (
            (_INFECTED_PATTERN, layer.infected_nominal, "Infected spawns", "infected_nominal"),
            (_WILDLIFE_PATTERN, layer.wildlife_nominal, "Wildlife spawns", "wildlife_nominal"),
        )
        if mult is not None
    ]
    if not groups:
        return None

    from app.xml.events_xml import get_events_path, parse_events_file, save_events_file, set_event_numeric

    path = get_events_path(mission_dir)
    if path is None:
        return None

    ef = parse_events_file(path, preserve_comments=True)
    records = file_records(fields, _EVENTS_KEY)
    counts = [0] * len(groups)
    for entry in ef.entries:
        for i, (pattern, mult, _what, _scale) in enumerate(groups):
            if not pattern.search(entry.name):
                continue
            if write_elements(entry, entry_records(records, entry.name), ("nominal", "min"),
                              set_event_numeric, _scaler(mult)):
                counts[i] += 1
            break  # an event is infected or wildlife, never both

    rows = [
        _multiplier_row("events.xml", what, n, "event", mult, scale_key)
        for (_p, mult, what, scale_key), n in zip(groups, counts) if n
    ]
    return _multiplied_file(mission_dir, "events.xml", path, fields, _EVENTS_KEY, records, rows,
                            lambda: save_events_file(ef), plan)


def _plan_types(mission_dir: Path, layer: TypesLayer, fields: dict, plan: PresetPlan) -> FileWrite | None:
    scaled = [
        (name, getattr(layer, attr), what, scale_key)
        for name, attr, what, scale_key in _TYPES_FIELDS
        if getattr(layer, attr) is not None
    ]
    path = mission_dir / "db" / "types.xml"
    if not scaled or not path.is_file():
        return None

    from app.xml.types_xml import parse_types_file, save_types_files, set_numeric_field

    tf = parse_types_file(path, preserve_comments=True)
    records = file_records(fields, _TYPES_KEY)
    counts = [0] * len(scaled)
    for entry in tf.entries:
        er = entry_records(records, entry.name)
        for i, (name, mult, _what, _scale) in enumerate(scaled):
            if write_elements(entry, er, (name,), set_numeric_field, _scaler(mult)):
                counts[i] += 1

    rows = [
        _multiplier_row("types.xml", what, n, "item", mult, scale_key)
        for (_n, mult, what, scale_key), n in zip(scaled, counts) if n
    ]
    return _multiplied_file(mission_dir, "types.xml", path, fields, _TYPES_KEY, records, rows,
                            lambda: save_types_files([tf]), plan)


def _warn_about_pre_sidecar_scaling(mission_dir: Path, preset: PresetDefinition, plan: PresetPlan) -> None:
    """With no presets sidecar yet, values on disk are the baseline. A preset
    applied by an older zsm scaled them from .backup, so say how to start
    from the file as shipped instead. No automatic migration: seeding the
    sidecar from .backup would bring back the drift the sidecar removes."""
    if (mission_dir / PRESET_BASELINES).exists():
        return
    if not any(getattr(preset.types, a) is not None for _n, a, _w, _s in _TYPES_FIELDS) and (
        preset.events.infected_nominal is None and preset.events.wildlife_nominal is None
    ):
        return
    backups = [
        name for name in ("types.xml", "events.xml")
        if (mission_dir / "db" / f"{name}.backup").is_file()
    ]
    if not backups:
        return
    plan.warnings.append(
        f"Loot and spawn multipliers start from the values in db/{' and db/'.join(backups)} "
        "as they are now. If a preset was applied with an older Zedstead, those values are "
        "already scaled: to start from the file as shipped, use Configs → Types → "
        "Restore from backup first."
    )


def preset_candidate_paths(config, m_dir: Path) -> list[Path]:
    """Every file the preset/tweak engines could touch — used to snapshot
    versions before an apply (mirrors presets_panel._snapshot_preset_targets,
    repurposed for config_versions instead of an in-memory diff)."""
    from app.parsers.cfggameplay import get_cfggameplay_path
    from app.xml.events_xml import get_events_path
    from app.xml.globals_xml import get_globals_path
    from app.xml.types_xml import scan_types_files
    from app.xml.zombie_territories_xml import get_territories_path, get_zombie_territories_path

    candidates: list[Path] = []
    try:
        candidates.append(Path(config.server_cfg_path))
    except Exception:
        pass
    for fn in (get_cfggameplay_path, get_globals_path, get_events_path, get_zombie_territories_path):
        try:
            p = fn(m_dir)
            if p:
                candidates.append(Path(p))
        except Exception:
            pass
    try:
        for terrain_file in ("PredatorTerritories.xml", "WildlifeTerritories.xml", "FarmAnimalTerritories.xml"):
            p = get_territories_path(m_dir, terrain_file)
            if p:
                candidates.append(Path(p))
    except Exception:
        pass
    try:
        candidates.extend(scan_types_files(m_dir))
    except Exception:
        pass
    return candidates


def snapshot_preset_targets(config, m_dir: Path, trigger: str) -> None:
    """Keep every file a preset or tweak can touch as a version first, under
    *trigger* (``preset_apply:<id>``, ``tweak_apply:<id>``), so the version
    and the Timeline name the preset. The web and the CLI both call it: a CLI
    apply recorded only the save's plain ``auto_save`` (2026-11-03)."""
    from app.shared.config_versions import snapshot

    for path in preset_candidate_paths(config, m_dir):
        if path.is_file():
            try:
                snapshot(path, trigger=trigger)
            except Exception:
                pass
