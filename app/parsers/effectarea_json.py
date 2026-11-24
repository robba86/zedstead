"""Effect area JSON parsing, editing, and backup/restore.

Reads/writes BI vanilla cfgEffectArea.json, which uses the nested
PascalCase schema documented in `ContaminatedAreaLoader.c`:

    {
        "Areas": [
            {
                "AreaName": "Ship-Bow",
                "Type": "ContaminatedArea_Static",
                "TriggerType": "ContaminatedTrigger",
                "Data": {
                    "Pos": [x, y, z],
                    "Radius": 75,
                    "PosHeight": 20,
                    "NegHeight": 3,
                    "InnerPartDist": 80,
                    "OuterOffset": 30,
                    "ParticleName": "graphics/particles/contaminated_area_gas_bigass"
                },
                "PlayerData": {
                    "AroundPartName": "graphics/particles/contaminated_area_gas_around",
                    "TinyPartName": "graphics/particles/contaminated_area_gas_around_tiny",
                    "PPERequesterType": "PPERequester_ContaminatedAreaTint"
                }
            }
        ],
        "SafePositions": [...]
    }

Dynamic contaminated zones are NOT configured here — they live in
events.xml as `StaticContaminatedArea` events.

That shape is a contaminated area's. The same file holds other kinds (Sakhal's
`GeyserArea`, `HotSpringArea` and `VolcanicArea` have no particles and no
`PlayerData`; a geyser has `EffectInterval` and `EffectDuration`), and `Data`
takes more keys than the ones named here. So an area read from a file keeps
its entry, and a save writes back that entry with only the edits put in.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.shared.json_patch import save_json_patched
from app.shared.json_utils import load_json_preserving
from app.xml.xml_utils import backup_file, restore_file


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KNOWN_TYPES = [
    "ContaminatedArea_Static",
    "ContaminatedArea_Local",
]

# Vanilla BI default for contaminated zones.
DEFAULT_TRIGGER_TYPE = "ContaminatedTrigger"
DEFAULT_PARTICLE_BIG = "graphics/particles/contaminated_area_gas_bigass"
DEFAULT_PARTICLE_AROUND = "graphics/particles/contaminated_area_gas_around"
DEFAULT_PARTICLE_TINY = "graphics/particles/contaminated_area_gas_around_tiny"
DEFAULT_PPE_REQUESTER = "PPERequester_ContaminatedAreaTint"


FIELD_DESCRIPTIONS: dict[str, str] = {
    "area_name": (
        "Friendly identifier for this zone (logs, debug). Vanilla examples: `Ship-Bow`, `Pavlovo`, `Tisy`. Not loaded into the engine — pure label."
    ),
    "type": (
        "Script class spawned for this zone. Vanilla uses `ContaminatedArea_Static` for fixed toxic zones. `ContaminatedArea_Local` is internal (e.g. chemical-gas grenades) and not normally configured here."
    ),
    "trigger_type": (
        "Trigger entity that detects players entering the radius. Vanilla = `ContaminatedTrigger`. Mods may register others."
    ),
    "pos": (
        "World coordinates [X, Y, Z] of the zone's anchor. **Y=0 snaps the zone to terrain**; non-zero Y disables snap (e.g. for elevated zones inside buildings or on ship decks)."
    ),
    "radius": (
        "Zone radius in meters. Drives both the trigger volume and the particle-emitter ring layout."
    ),
    "pos_height": (
        "Trigger volume reach above ground level, in meters. Players above this offset don't take damage."
    ),
    "neg_height": (
        "Trigger volume reach below ground level, in meters. Mostly relevant for terrain dips and basements."
    ),
    "inner_part_dist": (
        "Spacing between the particle emitters that fill the zone, in whole meters (the perceived particle size, `partSize`). Smaller fills the zone with more emitters, up to 1000 per zone. Visual only — does not affect the damage trigger."
    ),
    "outer_offset": (
        "How far beyond `Radius` the particles are still drawn, in whole meters (`outwardsBleed`). Visual only."
    ),
    "particle_name": (
        "Particle effect emitted across the zone. Vanilla uses `graphics/particles/contaminated_area_gas_bigass`; mods may swap for differently-coloured gas effects."
    ),
    "around_part_name": (
        "Particle attached to a player while inside the zone. Vanilla = `graphics/particles/contaminated_area_gas_around`."
    ),
    "tiny_part_name": (
        "Smaller particle attached to a player while inside the zone. Vanilla = `graphics/particles/contaminated_area_gas_around_tiny`."
    ),
    "ppe_requester_type": (
        "Post-process screen effect requested while a player is inside the zone (greenish toxic tint in vanilla). `PPERequester_ContaminatedAreaTint` for vanilla; clear to disable."
    ),
}


# Field tuples consumed by the editor's ConfigFormModal.
# (key, label) — the editor builds 5-tuples by joining with FIELD_DESCRIPTIONS.
AREA_FIELDS_DATA = [
    ("area_name", "Area name"),
    ("type", "Class type"),
    ("trigger_type", "Trigger type"),
    ("pos_x", "Position X"),
    ("pos_y", "Position Y (0 = snap to terrain)"),
    ("pos_z", "Position Z"),
    ("radius", "Radius (m)"),
    ("pos_height", "Pos height (m)"),
    ("neg_height", "Neg height (m)"),
    ("inner_part_dist", "Inner particle distance (m)"),
    ("outer_offset", "Outer offset (m)"),
    ("particle_name", "Particle name"),
]

AREA_FIELDS_PLAYER = [
    ("around_part_name", "Around-player particle"),
    ("tiny_part_name", "Tiny around-player particle"),
    ("ppe_requester_type", "PPE requester type"),
]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

# Where each attribute is in an Areas[] entry: the object inside the entry
# ("" for the entry itself), the key, the attribute. In written order.
_KEYS = (
    ("", "AreaName", "area_name"),
    ("", "Type", "type"),
    ("", "TriggerType", "trigger_type"),
    ("Data", "Pos", "pos"),
    ("Data", "Radius", "radius"),
    ("Data", "PosHeight", "pos_height"),
    ("Data", "NegHeight", "neg_height"),
    ("Data", "InnerPartDist", "inner_part_dist"),
    ("Data", "OuterOffset", "outer_offset"),
    ("Data", "ParticleName", "particle_name"),
    ("PlayerData", "AroundPartName", "around_part_name"),
    ("PlayerData", "TinyPartName", "tiny_part_name"),
    ("PlayerData", "PPERequesterType", "ppe_requester_type"),
)


#: The ``Data`` keys Bohemia's wiki marks ``integer``
#: (`contaminated-areas-configuration.wiki`:98, :101). A table cell for one of
#: them takes a whole number only.
INTEGER_FIELDS = frozenset({"inner_part_dist", "outer_offset"})


def cell_number(field_name: str, raw: Any) -> int | float:
    """A number cell of the table as the file will hold it.

    A value that is not a finite number is refused, and so is a fraction in
    one of :data:`INTEGER_FIELDS`."""
    try:
        value = float(str(raw).strip())
    except ValueError:
        raise ValueError(f"{field_name} must be a number") from None
    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be a finite number")
    if field_name in INTEGER_FIELDS:
        if not value.is_integer():
            raise ValueError(f"{field_name} must be a whole number")
        return int(value)
    return value


def _number(value: Any, default: int | float) -> int | float:
    """A number read from the file as the file wrote it, *default* when the
    key is missing or holds something else."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return value


def parse_pos(raw: Any) -> list[float]:
    """A position cell, ``x,y,z``: exactly three finite numbers."""
    parts = [p.strip() for p in str(raw).split(",")]
    if len(parts) != 3 or not all(parts):
        raise ValueError("Pos needs three numbers, x,y,z")
    try:
        pos = [float(p) for p in parts]
    except ValueError:
        raise ValueError("Pos needs three numbers, x,y,z") from None
    if not all(math.isfinite(v) for v in pos):
        raise ValueError("Pos must be finite numbers")
    return pos


def _like(old: Any, new: Any) -> Any:
    """*new* the way the file wrote this value: an equal number is the file's
    own, and a whole number stays whole. A whole number the file did not have
    yet is written whole too (``150``, as Bohemia writes it, not ``150.0``).

    Not ``hazard_state._like``, which rounds a drag's fraction away in a
    whole-number key: a fraction typed into the table is the edit.
    """
    if isinstance(new, list):
        olds = old if isinstance(old, list) else []
        return [_like(olds[i] if i < len(olds) else None, v) for i, v in enumerate(new)]
    if isinstance(old, bool) or not isinstance(old, (int, float)):
        return int(new) if isinstance(new, float) and new.is_integer() else new
    if not isinstance(new, float):
        return new
    if old == new:
        return old
    return int(new) if isinstance(old, int) and new.is_integer() else new


@dataclass
class EffectArea:
    """One Areas[] entry from cfgEffectArea.json.

    `raw` is the entry as the file had it, None for an area made here. The
    other attributes of an area read from a file hold a contaminated area's
    default wherever its entry has no such key.
    """

    area_name: str = ""
    type: str = "ContaminatedArea_Static"
    trigger_type: str = DEFAULT_TRIGGER_TYPE
    pos: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    radius: float = 75.0
    pos_height: float = 20.0
    neg_height: float = 3.0
    inner_part_dist: int | float = 80
    outer_offset: int | float = 30
    particle_name: str = DEFAULT_PARTICLE_BIG
    around_part_name: str = DEFAULT_PARTICLE_AROUND
    tiny_part_name: str = DEFAULT_PARTICLE_TINY
    ppe_requester_type: str = DEFAULT_PPE_REQUESTER
    raw: dict[str, Any] | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        """The entry to write: the one that was read with only the values
        changed since put in, so no key is dropped and none is added. An area
        made here is written whole, as a contaminated area."""
        was = EffectArea.from_dict(self.raw) if self.raw is not None else None
        out: dict[str, Any] = copy.deepcopy(self.raw) if self.raw is not None else {}
        for section, key, attr in _KEYS:
            value = getattr(self, attr)
            if was is not None and value == getattr(was, attr):
                continue
            target = out
            if section:
                target = out.get(section)
                if not isinstance(target, dict):
                    target = out[section] = {}
            target[key] = _like(target.get(key), value)
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EffectArea:
        data = d.get("Data") or {}
        player = d.get("PlayerData") or {}
        return cls(
            area_name=str(d.get("AreaName", "")),
            type=str(d.get("Type", "ContaminatedArea_Static")),
            trigger_type=str(d.get("TriggerType", DEFAULT_TRIGGER_TYPE)),
            pos=[float(x) for x in (data.get("Pos") or [0.0, 0.0, 0.0])],
            radius=float(data.get("Radius", 75.0)),
            pos_height=float(data.get("PosHeight", 20.0)),
            neg_height=float(data.get("NegHeight", 3.0)),
            inner_part_dist=_number(data.get("InnerPartDist"), 80),
            outer_offset=_number(data.get("OuterOffset"), 30),
            particle_name=str(data.get("ParticleName", DEFAULT_PARTICLE_BIG)),
            around_part_name=str(player.get("AroundPartName", DEFAULT_PARTICLE_AROUND)),
            tiny_part_name=str(player.get("TinyPartName", DEFAULT_PARTICLE_TINY)),
            ppe_requester_type=str(player.get("PPERequesterType", DEFAULT_PPE_REQUESTER)),
            raw=d,
        )


@dataclass
class EffectAreaFile:
    """Parsed cfgEffectArea.json file.

    `safe_positions` is preserved verbatim across round-trips — the editor
    does not currently expose it, but losing it on save would break the
    vanilla map (it's the no-spawn override list used by the loot scheduler).
    `raw` is the file's top level as read, so any other key is written back.
    """

    path: Path
    areas: list[EffectArea] = field(default_factory=list)
    safe_positions: list[Any] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


# The two spellings Bohemia ships (Chernarus, Sakhal), preferred in this order
# when a folder holds more than one.
_KNOWN_SPELLINGS = ("cfgEffectArea.json", "cfgeffectarea.json")


def get_effectarea_path(mission_dir: Path | None) -> Path | None:
    """Return cfgEffectArea.json if it exists (check root + db/).

    Matched in any case: it used to be found under the two spellings above
    only, so on Linux a mission whose file was written ``CfgEffectArea.json``
    had no contaminated areas in the editors and the collision scan, while the
    map editor's layer (``hazard_state``, which matched any case) showed them.
    """
    if not mission_dir:
        return None
    for folder in (mission_dir, mission_dir / "db"):
        if not folder.is_dir():
            continue
        found = sorted(
            (p for p in folder.iterdir() if p.name.lower() == "cfgeffectarea.json" and p.is_file()),
            key=lambda p: (_KNOWN_SPELLINGS.index(p.name) if p.name in _KNOWN_SPELLINGS else 2, p.name),
        )
        if found:
            return found[0]
    return None


# ---------------------------------------------------------------------------
# Parse & save
# ---------------------------------------------------------------------------


def parse_effectarea_file(path: Path) -> EffectAreaFile:
    """Parse cfgEffectArea.json into an EffectAreaFile."""
    data = load_json_preserving(path) or {}
    areas = [EffectArea.from_dict(d) for d in data.get("Areas", []) if isinstance(d, dict)]
    safe_positions = list(data.get("SafePositions", []) or [])
    return EffectAreaFile(path=path, areas=areas, safe_positions=safe_positions, raw=data)


def save_effectarea_file(eaf: EffectAreaFile) -> None:
    """Write the file back: its own top level, each area's own entry with the
    edits put in, and every number it did not change as the file wrote it.

    Only what changed is replaced in the file's text (``app.shared.json_patch``),
    so Bohemia's tabs, CRLF and ``"Pos": [ x, y, z ]`` stay, and a save with no
    edits writes nothing."""
    out: dict[str, Any] = dict(eaf.raw)
    out["Areas"] = [a.to_dict() for a in eaf.areas]
    if eaf.safe_positions or out.get("SafePositions"):
        out["SafePositions"] = eaf.safe_positions
    save_json_patched(eaf.path, out, inline_scalar_lists=True)


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------


def backup_effectarea_file(path: Path) -> Path:
    return backup_file(path)


def restore_effectarea_file(path: Path) -> bool:
    return restore_file(path)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def add_area(eaf: EffectAreaFile, area: EffectArea) -> None:
    eaf.areas.append(area)


def remove_area(eaf: EffectAreaFile, index: int) -> None:
    if 0 <= index < len(eaf.areas):
        eaf.areas.pop(index)


def update_area(eaf: EffectAreaFile, index: int, area: EffectArea) -> None:
    if 0 <= index < len(eaf.areas):
        eaf.areas[index] = area
