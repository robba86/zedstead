"""TraderPlus InsuranceConfig JSON parsing, editing, and backup/restore."""

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
    "vehicle_name": "Vehicle classname this insurance entry applies to (e.g. `OffroadHatchback`, `CivilianSedan`, `Hatchback_02`). Must match a vehicle class known to the engine — typically the same classnames used in TraderPlusVehiclesConfig and PriceConfig.",
    "insurance_price_coefficient": "Multiplier applied to the vehicle's base price to compute the insurance premium charged at purchase. 1.0 = insurance equals vehicle price; 1.25 (default) = 125% of vehicle price; 0.5 = half price. Premium is paid once when buying the vehicle (no recurring fee).",
    "collateral_money_coefficient": "Reimbursement coefficient — how much of the vehicle's base price the player gets back when an insured vehicle is destroyed. 1.0 = full price refunded, 0.75 (default) = 75%, 0.5 = 50%, etc. The 'collateral' name is misleading — this is the payout, not a deposit.",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class InsuranceEntry:
    """One vehicle insurance definition."""

    vehicle_name: str = ""
    insurance_price_coefficient: float = 1.25
    collateral_money_coefficient: float = 0.75
    #: The entry as the file had it (``over_raw``).
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> InsuranceEntry:
        return InsuranceEntry(
            raw=d,
            vehicle_name=str(d.get("VehicleName", "")),
            insurance_price_coefficient=float(d.get("InsurancePriceCoefficient", 1.25)),
            collateral_money_coefficient=float(d.get("CollateralMoneyCoefficient", 0.75)),
        )

    def known(self) -> dict[str, Any]:
        return {
            "VehicleName": self.vehicle_name,
            "InsurancePriceCoefficient": self.insurance_price_coefficient,
            "CollateralMoneyCoefficient": self.collateral_money_coefficient,
        }

    def to_dict(self) -> dict[str, Any]:
        return entry_over_raw(self)


@dataclass
class TraderPlusInsuranceConfig:
    """Parsed TraderPlusInsuranceConfig.json file.

    Uses ``_raw`` to preserve the full original JSON.
    """

    path: Path = field(default_factory=Path)
    _raw: dict[str, Any] = field(default_factory=dict)
    #: The edited keys as parsed (``over_raw``): a key the file lacks is saved only once it changes.
    _read: dict[str, Any] | None = field(default=None, repr=False, compare=False)

    version: str = ""
    authorized_id_insurance: list[int] = field(default_factory=list)
    insurances: list[InsuranceEntry] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_insurance_config_path(config: DayzConfig) -> Path | None:
    """Return TraderPlusInsuranceConfig.json path if it exists."""
    cfg_dir = get_traderplus_config_dir(config)
    if not cfg_dir:
        return None
    p = cfg_dir / "TraderPlusInsuranceConfig.json"
    return p if p.is_file() else None


# ---------------------------------------------------------------------------
# Parse & save
# ---------------------------------------------------------------------------

def parse_insurance_config(path: Path) -> TraderPlusInsuranceConfig:
    """Parse TraderPlusInsuranceConfig.json."""
    data = load_json(path)
    try:
        authorized = [int(x) for x in json_list(data, "AuthorizedIDInsurance")]
        entries = json_list(data, "Insurances")
    except ValueError as exc:
        raise refuse_in(path, exc) from exc

    cfg = TraderPlusInsuranceConfig(
        path=path,
        _raw=data,
        version=str(data.get("Version", "")),
        authorized_id_insurance=authorized,
        insurances=[InsuranceEntry.from_dict(e) for e in entries],
    )
    cfg._read = _known(cfg)
    return cfg


def _known(cfg: TraderPlusInsuranceConfig) -> dict[str, Any]:
    """The keys the web editor edits, as *cfg* holds them."""
    return {
        "AuthorizedIDInsurance": list(cfg.authorized_id_insurance),
        "Insurances": [e.to_dict() for e in cfg.insurances],
    }


def save_insurance_config(cfg: TraderPlusInsuranceConfig) -> None:
    """Write TraderPlusInsuranceConfig.json, patching only the web editor's fields."""
    data = over_raw(cfg._raw, _known(cfg), cfg._read)
    save_json_patched(cfg.path, data)
    cfg._raw = data


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def backup_insurance_config(path: Path) -> Path:
    """Create a .backup copy of the insurance config."""
    return backup_file(path)


def restore_insurance_config(path: Path) -> bool:
    """Restore insurance config from .backup. Returns True on success."""
    return restore_file(path)



# ---------------------------------------------------------------------------
# CRUD — insurances
# ---------------------------------------------------------------------------

def add_insurance(
    cfg: TraderPlusInsuranceConfig,
    vehicle_name: str,
    price_coeff: float = 1.25,
    collateral_coeff: float = 0.75,
) -> InsuranceEntry:
    """Add a new insurance entry."""
    entry = InsuranceEntry(
        vehicle_name=vehicle_name,
        insurance_price_coefficient=price_coeff,
        collateral_money_coefficient=collateral_coeff,
    )
    cfg.insurances.append(entry)
    return entry


def remove_insurance(cfg: TraderPlusInsuranceConfig, index: int) -> None:
    """Remove an insurance entry by index."""
    if 0 <= index < len(cfg.insurances):
        cfg.insurances.pop(index)


def update_insurance(
    cfg: TraderPlusInsuranceConfig,
    index: int,
    vehicle_name: str,
    price_coeff: float,
    collateral_coeff: float,
) -> None:
    """Update an insurance entry at index."""
    if 0 <= index < len(cfg.insurances):
        cfg.insurances[index].vehicle_name = vehicle_name
        cfg.insurances[index].insurance_price_coefficient = price_coeff
        cfg.insurances[index].collateral_money_coefficient = collateral_coeff


# ---------------------------------------------------------------------------
# CRUD — authorized IDs
# ---------------------------------------------------------------------------

def add_authorized_id(cfg: TraderPlusInsuranceConfig, trader_id: int) -> None:
    """Add a trader ID to the authorized list."""
    if trader_id not in cfg.authorized_id_insurance:
        cfg.authorized_id_insurance.append(trader_id)


def remove_authorized_id(cfg: TraderPlusInsuranceConfig, trader_id: int) -> None:
    """Remove a trader ID from the authorized list."""
    if trader_id in cfg.authorized_id_insurance:
        cfg.authorized_id_insurance.remove(trader_id)
