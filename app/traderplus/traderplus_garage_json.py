"""TraderPlus GarageConfig JSON parsing, editing, and backup/restore."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.shared.config import DayzConfig
from app.shared.json_patch import save_json_patched
from app.shared.json_utils import load_json
from app.traderplus.trader_common import entry_over_raw, get_traderplus_config_dir, json_list, over_raw, refuse_in
from app.xml.xml_utils import backup_file, restore_file


# ---------------------------------------------------------------------------
# Field descriptions (shown as help in the web editor)
# ---------------------------------------------------------------------------

FIELD_DESCRIPTIONS: dict[str, str] = {
    "use_garage_only_to_trade": "On = garage is a time-limited holding pen for vehicles being sold (vehicles auto-leave after the Trade-only duration below). Off = full storage mode — vehicles stay until sold or retrieved by the player. Default off.",
    "vehicle_must_have_lock": "Require a lock from a compatible mod (TraderPlus CarLock, Trader Keys, or MuchCarKey) before a vehicle can be stored. Off = any vehicle can be parked regardless of locks.",
    "save_vehicle_cargo": "Persist vehicle cargo (everything inside seats/trunk) into the garage database when stored. Off = cargo is dropped on park-in. ⚠ Cargo is saved into the JSON DB — a busy server with hoarder players will see the garage DB grow significantly.",
    "save_vehicle_health": "Persist the vehicle's damage state (HP per part) when stored. Off = vehicle is restored to pristine on retrieve.",
    "save_vehicle_fuel": "Persist the vehicle's fuel level when stored. Off = tank is empty on retrieve.",
    "pay_with_bank_account": "Charge garage fees against the player's TraderPlus bank account instead of currency carried in inventory. Off = player must hold the cash items on their character to park-in/park-out.",
    "saved_vehicle_in_garage_for_trade_in_hour": "Hours a vehicle stays in the garage when the 'Use garage only to trade' toggle above is on. Ignored when that toggle is off (vehicles stay indefinitely). Default 1 (= 1 hour).",
    "max_vehicle_stored": "Maximum vehicles each player can store across all garage NPCs. Default 5. Hits the 'Garage full' UI when exceeded.",
    "park_in_cost": "Integer fee charged on park-in. Currency used is whichever currency the player holds (or their bank balance if 'Pay with bank account' is on) — same currency pool as bank/ATM transactions. Default 0 = free.",
    "park_out_cost": "Integer fee charged on park-out (vehicle retrieval). Currency used is whichever currency the player holds (or their bank balance if 'Pay with bank account' is on) — same currency pool as bank/ATM transactions. Default 0 = free.",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class GarageNPC:
    """One garage NPC with position and parking spot."""

    class_name: str = ""
    position: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    orientation: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    parking_position: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    parking_orientation: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    clothes: list[str] = field(default_factory=list)
    #: The entry as the file had it (``over_raw``).
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> GarageNPC:
        return GarageNPC(
            raw=d,
            class_name=str(d.get("ClassName", "")),
            position=json_list(d, "Position", [0.0, 0.0, 0.0]),
            orientation=json_list(d, "Orientation", [0.0, 0.0, 0.0]),
            parking_position=json_list(d, "ParkingPosition", [0.0, 0.0, 0.0]),
            parking_orientation=json_list(d, "ParkingOrientation", [0.0, 0.0, 0.0]),
            clothes=json_list(d, "Clothes"),
        )

    def known(self) -> dict[str, Any]:
        return {
            "ClassName": self.class_name,
            "Position": list(self.position),
            "Orientation": list(self.orientation),
            "ParkingPosition": list(self.parking_position),
            "ParkingOrientation": list(self.parking_orientation),
            "Clothes": list(self.clothes),
        }

    def to_dict(self) -> dict[str, Any]:
        return entry_over_raw(self)


@dataclass
class TraderPlusGarageConfig:
    """Parsed TraderPlusGarageConfig.json file.

    Uses ``_raw`` to preserve the full original JSON.
    """

    path: Path = field(default_factory=Path)
    _raw: dict[str, Any] = field(default_factory=dict)
    #: The edited keys as parsed (``over_raw``): a key the file lacks is saved only once it changes.
    _read: dict[str, Any] | None = field(default=None, repr=False, compare=False)

    version: str = ""

    # Boolean toggles (0/1)
    use_garage_only_to_trade: int = 0
    vehicle_must_have_lock: int = 0
    save_vehicle_cargo: int = 0
    save_vehicle_health: int = 0
    save_vehicle_fuel: int = 0
    pay_with_bank_account: int = 0

    # Scalars
    saved_vehicle_in_garage_for_trade_in_hour: int = 1
    max_vehicle_stored: int = 5
    park_in_cost: int = 0
    park_out_cost: int = 0

    # Lists
    whitelisted_objects: list[str] = field(default_factory=list)
    npcs: list[GarageNPC] = field(default_factory=list)


# ---------------------------------------------------------------------------
# JSON key <-> dataclass field mapping (toggles only)
# ---------------------------------------------------------------------------

_TOGGLE_MAP: list[tuple[str, str]] = [
    ("UseGarageOnlyToTrade", "use_garage_only_to_trade"),
    ("VehicleMustHaveLock", "vehicle_must_have_lock"),
    ("SaveVehicleCargo", "save_vehicle_cargo"),
    ("SaveVehicleHealth", "save_vehicle_health"),
    ("SaveVehicleFuel", "save_vehicle_fuel"),
    ("PayWithBankAccount", "pay_with_bank_account"),
]


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_garage_config_path(config: DayzConfig) -> Path | None:
    """Return TraderPlusGarageConfig.json path if it exists."""
    cfg_dir = get_traderplus_config_dir(config)
    if not cfg_dir:
        return None
    p = cfg_dir / "TraderPlusGarageConfig.json"
    return p if p.is_file() else None


# ---------------------------------------------------------------------------
# Parse & save
# ---------------------------------------------------------------------------

def parse_garage_config(path: Path) -> TraderPlusGarageConfig:
    """Parse TraderPlusGarageConfig.json."""
    data = load_json(path)
    try:
        whitelisted = json_list(data, "WhitelistedObjects")
    except ValueError as exc:
        raise refuse_in(path, exc) from exc
    npcs = []
    for i, n in enumerate(data.get("NPCs", [])):
        try:
            npcs.append(GarageNPC.from_dict(n))
        except ValueError as exc:
            raise refuse_in(path, exc, f"NPC {i + 1}") from exc

    cfg = TraderPlusGarageConfig(
        path=path,
        _raw=data,
        version=str(data.get("Version", "")),
        saved_vehicle_in_garage_for_trade_in_hour=int(data.get("SavedVehicleInGarageForTradeInHour", 1)),
        max_vehicle_stored=int(data.get("MaxVehicleStored", 5)),
        park_in_cost=int(data.get("ParkInCost", 0)),
        park_out_cost=int(data.get("ParkOutCost", 0)),
        whitelisted_objects=whitelisted,
        npcs=npcs,
    )

    for json_key, attr in _TOGGLE_MAP:
        setattr(cfg, attr, int(data.get(json_key, 0)))

    cfg._read = _known(cfg)
    return cfg


def _known(cfg: TraderPlusGarageConfig) -> dict[str, Any]:
    """The keys the web editor edits, as *cfg* holds them."""
    known: dict[str, Any] = {json_key: getattr(cfg, attr) for json_key, attr in _TOGGLE_MAP}
    known.update({
        "SavedVehicleInGarageForTradeInHour": cfg.saved_vehicle_in_garage_for_trade_in_hour,
        "MaxVehicleStored": cfg.max_vehicle_stored,
        "ParkInCost": cfg.park_in_cost,
        "ParkOutCost": cfg.park_out_cost,
        "WhitelistedObjects": list(cfg.whitelisted_objects),
        "NPCs": [n.to_dict() for n in cfg.npcs],
    })
    return known


def save_garage_config(cfg: TraderPlusGarageConfig) -> None:
    """Write TraderPlusGarageConfig.json, patching only the web editor's fields."""
    data = over_raw(cfg._raw, _known(cfg), cfg._read)
    save_json_patched(cfg.path, data)
    cfg._raw = data


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def backup_garage_config(path: Path) -> Path:
    """Create a .backup copy of the garage config."""
    return backup_file(path)


def restore_garage_config(path: Path) -> bool:
    """Restore garage config from .backup. Returns True on success."""
    return restore_file(path)



# ---------------------------------------------------------------------------
# CRUD — NPCs
# ---------------------------------------------------------------------------

def add_garage_npc(cfg: TraderPlusGarageConfig, class_name: str = "SurvivorM_Peter") -> GarageNPC:
    """Add a new garage NPC."""
    npc = GarageNPC(class_name=class_name)
    cfg.npcs.append(npc)
    return npc


def remove_garage_npc(cfg: TraderPlusGarageConfig, index: int) -> None:
    """Remove a garage NPC by index."""
    if 0 <= index < len(cfg.npcs):
        cfg.npcs.pop(index)


def update_garage_npc(cfg: TraderPlusGarageConfig, index: int, **kwargs: Any) -> None:
    """Update a garage NPC at index with provided keyword arguments."""
    if 0 <= index < len(cfg.npcs):
        npc = cfg.npcs[index]
        for key, value in kwargs.items():
            if hasattr(npc, key):
                setattr(npc, key, value)


# ---------------------------------------------------------------------------
# CRUD — whitelisted objects
# ---------------------------------------------------------------------------

def add_whitelisted_object(cfg: TraderPlusGarageConfig, name: str) -> None:
    """Add an object to the whitelist."""
    if name not in cfg.whitelisted_objects:
        cfg.whitelisted_objects.append(name)


def remove_whitelisted_object(cfg: TraderPlusGarageConfig, index: int) -> None:
    """Remove a whitelisted object by index."""
    if 0 <= index < len(cfg.whitelisted_objects):
        cfg.whitelisted_objects.pop(index)
