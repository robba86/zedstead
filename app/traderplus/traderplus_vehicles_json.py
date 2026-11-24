"""TraderPlus VehiclesConfig JSON parsing, editing, and backup/restore."""

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
    "vehicle_name": "Vehicle classname used as the lookup key when reconstructing the vehicle from a TraderPlus receipt (e.g. `CivilianSedan`, `OffroadHatchback`). To make this vehicle tradeable + insurable, add the SAME classname to TraderPlusPriceConfig and TraderPlusInsuranceConfig. A vehicle missing here will deploy as a bare frame with no parts.",
    "height": "Vertical offset (in meters) added to the deployment hologram when a player unpacks a vehicle from a TraderPlus receipt. 0 = default — works for all vanilla vehicles. Increase to 1, 2, 3+ if a modded vehicle's hologram appears stuck in the ground (caused by missing geometry in the modded model). Has no effect on normal vehicle spawning, only receipt deployment.",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class VehicleDefinition:
    """One vehicle definition with parts list."""

    vehicle_name: str = ""
    height: int = 0
    vehicle_parts: list[str] = field(default_factory=list)
    #: The entry as the file had it (``over_raw``).
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> VehicleDefinition:
        return VehicleDefinition(
            raw=d,
            vehicle_name=str(d.get("VehicleName", "")),
            height=int(d.get("Height", 0)),
            vehicle_parts=json_list(d, "VehicleParts"),
        )

    def known(self) -> dict[str, Any]:
        return {
            "VehicleName": self.vehicle_name,
            "Height": self.height,
            "VehicleParts": list(self.vehicle_parts),
        }

    def to_dict(self) -> dict[str, Any]:
        return entry_over_raw(self)


@dataclass
class TraderPlusVehiclesConfig:
    """Parsed TraderPlusVehiclesConfig.json file.

    Uses ``_raw`` to preserve the full original JSON.
    """

    path: Path = field(default_factory=Path)
    _raw: dict[str, Any] = field(default_factory=dict)
    #: The edited keys as parsed (``over_raw``): a key the file lacks is saved only once it changes.
    _read: dict[str, Any] | None = field(default=None, repr=False, compare=False)

    version: str = ""
    vehicles: list[VehicleDefinition] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_vehicles_config_path(config: DayzConfig) -> Path | None:
    """Return TraderPlusVehiclesConfig.json path if it exists."""
    cfg_dir = get_traderplus_config_dir(config)
    if not cfg_dir:
        return None
    p = cfg_dir / "TraderPlusVehiclesConfig.json"
    return p if p.is_file() else None


# ---------------------------------------------------------------------------
# Parse & save
# ---------------------------------------------------------------------------

def parse_vehicles_config(path: Path) -> TraderPlusVehiclesConfig:
    """Parse TraderPlusVehiclesConfig.json."""
    data = load_json(path)
    vehicles = []
    for i, v in enumerate(data.get("VehiclesParts", [])):
        try:
            vehicles.append(VehicleDefinition.from_dict(v))
        except ValueError as exc:
            raise refuse_in(path, exc, f"vehicle {i + 1}") from exc

    cfg = TraderPlusVehiclesConfig(
        path=path,
        _raw=data,
        version=str(data.get("Version", "")),
        vehicles=vehicles,
    )
    cfg._read = _known(cfg)
    return cfg


def _known(cfg: TraderPlusVehiclesConfig) -> dict[str, Any]:
    """The keys the web editor edits, as *cfg* holds them."""
    return {
        "VehiclesParts": [v.to_dict() for v in cfg.vehicles],
    }


def save_vehicles_config(cfg: TraderPlusVehiclesConfig) -> None:
    """Write TraderPlusVehiclesConfig.json, patching only the web editor's fields."""
    data = over_raw(cfg._raw, _known(cfg), cfg._read)
    save_json_patched(cfg.path, data)
    cfg._raw = data


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def backup_vehicles_config(path: Path) -> Path:
    """Create a .backup copy of the vehicles config."""
    return backup_file(path)


def restore_vehicles_config(path: Path) -> bool:
    """Restore vehicles config from .backup. Returns True on success."""
    return restore_file(path)


# ---------------------------------------------------------------------------
# CRUD — vehicles
# ---------------------------------------------------------------------------

def add_vehicle(cfg: TraderPlusVehiclesConfig, vehicle_name: str, height: int = 0) -> VehicleDefinition:
    """Add a new vehicle definition."""
    v = VehicleDefinition(vehicle_name=vehicle_name, height=height)
    cfg.vehicles.append(v)
    return v


def remove_vehicle(cfg: TraderPlusVehiclesConfig, index: int) -> None:
    """Remove a vehicle by index."""
    if 0 <= index < len(cfg.vehicles):
        cfg.vehicles.pop(index)


def update_vehicle(
    cfg: TraderPlusVehiclesConfig,
    index: int,
    vehicle_name: str,
    height: int,
) -> None:
    """Update vehicle name and height at index."""
    if 0 <= index < len(cfg.vehicles):
        cfg.vehicles[index].vehicle_name = vehicle_name
        cfg.vehicles[index].height = height



# ---------------------------------------------------------------------------
# CRUD — vehicle parts
# ---------------------------------------------------------------------------

def add_part(vehicle: VehicleDefinition, part_name: str) -> None:
    """Add a part to a vehicle."""
    vehicle.vehicle_parts.append(part_name)


def remove_part(vehicle: VehicleDefinition, index: int) -> None:
    """Remove a part from a vehicle by index."""
    if 0 <= index < len(vehicle.vehicle_parts):
        vehicle.vehicle_parts.pop(index)


def set_parts(vehicle: VehicleDefinition, parts: list[str]) -> None:
    """Replace all parts on a vehicle."""
    vehicle.vehicle_parts = list(parts)
