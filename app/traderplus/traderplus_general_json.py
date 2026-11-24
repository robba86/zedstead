"""TraderPlus GeneralConfig JSON parsing, editing, and backup/restore."""

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
    "convert_trader_config": (
        "Convert DrJones TraderConfig.txt + TraderVehiclesParts.txt into TraderPlus PriceConfig/IDsConfig/Vehicles JSON on startup. "
        "One-time migration; turn off after the conversion runs."
    ),
    "convert_trader_config_stock_ce": (
        "When converting from DrJones, derive each product's max_stock from its types.xml nominal. "
        "Off = all products get infinite stock after conversion."
    ),
    "use_garage_to_trade_car": (
        "Sold cars are placed directly in the player's garage. "
        "Off = sold cars are given as a receipt to unpack later."
    ),
    "disable_height_failsafe": (
        "Disables the failsafe that prevents vehicle receipts from being deployed at extreme elevations "
        "(e.g. on rooftops). Turn on for custom maps where vehicles need to spawn at unusual heights."
    ),
    "hide_insurance_btn": "Hide the insurance button in the trader menu.",
    "hide_garage_btn": "Hide the garage button in the trader menu.",
    "hide_licence_btn": "Hide the licence button in the trader menu.",
    "enable_show_all_prices": "Show all item prices even if the trader doesn't stock them.",
    "enable_show_all_checkbox": 'Show the "show all" checkbox in the trader UI.',
    "is_receipt_trader_only": (
        "When on, car receipts can only be unpacked inside a trader safe zone. "
        "When off, players can unpack their car receipts anywhere on the map. "
        "Does not affect direct item trading."
    ),
    "is_receipt_save_lock": (
        "Preserve the vehicle's combination lock code when packing it into a receipt. "
        "Off = lock resets when unpacked."
    ),
    "is_receipt_save_attachment": (
        "Preserve the vehicle's parts (wheels, doors, battery, plug, etc.) when packing it into a receipt. "
        "Off = vehicle unpacks with default parts."
    ),
    "is_receipt_save_cargo": (
        "Preserve items inside the vehicle's inventory when packing it into a receipt. "
        "Off = cargo is wiped on pack."
    ),
    "store_only_to_pristine_state": (
        "When on, traders accept items in any condition but store them all as pristine — players selling "
        "damaged items and buying them back will get pristine versions. When off, items keep their "
        "condition through the trade."
    ),
    "lock_pick_chance": (
        "Probability (0.0-1.0) of successfully picking a TraderPlus car lock — used when the car lock system "
        "is active. Vanilla example: 0.30."
    ),
    "licence_keyword": (
        "Word that every licence item name must contain (case-sensitive). Defaults to 'Licence'. "
        "Localize for non-English servers (e.g. 'Permit', 'Certificate'). "
        "Without the keyword, the licence isn't recognised by the mod."
    ),
    "accept_worn": "Accept worn items for trading.",
    "coefficient_worn": "Price multiplier for worn items. Lower value = lower price.",
    "accept_damaged": "Accept damaged items for trading.",
    "coefficient_damaged": "Price multiplier for damaged items. Lower value = lower price.",
    "accept_badly_damaged": "Accept badly damaged items for trading at reduced price.",
    "coefficient_badly_damaged": "Price multiplier for badly damaged items. Lower value = lower price.",
    "trader_given_name": "Display name shown in the trader UI.",
    "trader_role": "Job title / role shown in the trader UI (e.g. 'Weapon Trader').",
    "trader_name": "Classname of NPC survivor model or static object.",
    "trader_id": "Trader ID (0-99) linking to TraderPlusIDsConfig.json. -2 = ATM.",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class AcceptedStates:
    """Item condition acceptance settings."""

    accept_worn: int = 1
    accept_damaged: int = 1
    accept_badly_damaged: int = 1
    coefficient_worn: float = 0.75
    coefficient_damaged: float = 0.5
    coefficient_badly_damaged: float = 0.25
    #: The entry as the file had it (``over_raw``).
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> AcceptedStates:
        return AcceptedStates(
            raw=d,
            accept_worn=int(d.get("AcceptWorn", 1)),
            accept_damaged=int(d.get("AcceptDamaged", 1)),
            accept_badly_damaged=int(d.get("AcceptBadlyDamaged", 1)),
            coefficient_worn=float(d.get("CoefficientWorn", 0.75)),
            coefficient_damaged=float(d.get("CoefficientDamaged", 0.5)),
            coefficient_badly_damaged=float(d.get("CoefficientBadlyDamaged", 0.25)),
        )

    def known(self) -> dict[str, Any]:
        return {
            "AcceptWorn": self.accept_worn,
            "AcceptDamaged": self.accept_damaged,
            "AcceptBadlyDamaged": self.accept_badly_damaged,
            "CoefficientWorn": self.coefficient_worn,
            "CoefficientDamaged": self.coefficient_damaged,
            "CoefficientBadlyDamaged": self.coefficient_badly_damaged,
        }

    def to_dict(self) -> dict[str, Any]:
        return entry_over_raw(self)


@dataclass
class Currency:
    """One currency entry."""

    classname: str = ""
    value: int = 0
    #: The entry as the file had it (``over_raw``).
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Currency:
        return Currency(
            raw=d,
            classname=str(d.get("ClassName", "")),
            value=int(d.get("Value", 0)),
        )

    def known(self) -> dict[str, Any]:
        return {"ClassName": self.classname, "Value": self.value}

    def to_dict(self) -> dict[str, Any]:
        return entry_over_raw(self)


@dataclass
class TraderNpc:
    """One trader NPC entry from the Traders array."""

    id: int = 0
    name: str = ""           # classname (SurvivorM_Peter, TraderPlusSign, etc.)
    given_name: str = ""     # display name
    role: str = ""           # role label
    clothes: list[str] = field(default_factory=list)
    # Position/orientation preserved for round-trip, not edited here
    position: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    orientation: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    #: The entry as the file had it (``over_raw``).
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> TraderNpc:
        return TraderNpc(
            raw=d,
            id=int(d.get("Id", 0)),
            name=str(d.get("Name", "")),
            given_name=str(d.get("GivenName", "")),
            role=str(d.get("Role", "")),
            clothes=json_list(d, "Clothes"),
            position=json_list(d, "Position", [0.0, 0.0, 0.0]),
            orientation=json_list(d, "Orientation", [0.0, 0.0, 0.0]),
        )

    def known(self) -> dict[str, Any]:
        return {
            "Id": self.id,
            "Name": self.name,
            "GivenName": self.given_name,
            "Role": self.role,
            "Position": list(self.position),
            "Orientation": list(self.orientation),
            "Clothes": list(self.clothes),
        }

    def to_dict(self) -> dict[str, Any]:
        return entry_over_raw(self)


@dataclass
class TraderPlusGeneralConfig:
    """Parsed TraderPlusGeneralConfig.json file.

    Uses ``_raw`` to preserve the full original JSON (including Traders,
    TraderObjects, and any unknown fields).  On save only the fields
    the web editor edits are patched back into ``_raw``.
    """

    path: Path = field(default_factory=Path)
    _raw: dict[str, Any] = field(default_factory=dict)
    #: The edited keys as parsed (``over_raw``): a key the file lacks is saved only once it changes.
    _read: dict[str, Any] | None = field(default=None, repr=False, compare=False)

    # Metadata
    version: str = ""

    # Boolean toggles (stored as 0/1 in JSON)
    convert_trader_config: int = 0
    convert_trader_config_stock_ce: int = 0
    use_garage_to_trade_car: int = 0
    disable_height_failsafe: int = 0
    hide_insurance_btn: int = 0
    hide_garage_btn: int = 0
    hide_licence_btn: int = 0
    enable_show_all_prices: int = 0
    enable_show_all_checkbox: int = 0
    is_receipt_trader_only: int = 0
    is_receipt_save_lock: int = 0
    is_receipt_save_attachment: int = 0
    is_receipt_save_cargo: int = 0
    store_only_to_pristine_state: int = 0

    # Scalars
    lock_pick_chance: float = 0.0
    licence_keyword: str = ""

    # Lists / objects
    licences: list[str] = field(default_factory=list)
    accepted_states: AcceptedStates = field(default_factory=AcceptedStates)
    currencies: list[Currency] = field(default_factory=list)
    traders: list[TraderNpc] = field(default_factory=list)

    # Read-only count (for info display — objects only have ObjectName, edited in map editor)
    trader_object_count: int = 0


# ---------------------------------------------------------------------------
# JSON key ↔ dataclass field mapping (toggles only)
# ---------------------------------------------------------------------------

_TOGGLE_MAP: list[tuple[str, str]] = [
    ("ConvertTraderConfigToTraderPlus", "convert_trader_config"),
    ("ConvertTraderConfigToTraderPlusWithStockBasedOnCE", "convert_trader_config_stock_ce"),
    ("UseGarageToTradeCar", "use_garage_to_trade_car"),
    ("DisableHeightFailSafeForReceiptDeployment", "disable_height_failsafe"),
    ("HideInsuranceBtn", "hide_insurance_btn"),
    ("HideGarageBtn", "hide_garage_btn"),
    ("HideLicenceBtn", "hide_licence_btn"),
    ("EnableShowAllPrices", "enable_show_all_prices"),
    ("EnableShowAllCheckBox", "enable_show_all_checkbox"),
    ("IsReceiptTraderOnly", "is_receipt_trader_only"),
    ("IsReceiptSaveLock", "is_receipt_save_lock"),
    ("IsReceiptSaveAttachment", "is_receipt_save_attachment"),
    ("IsReceiptSaveCargo", "is_receipt_save_cargo"),
    ("StoreOnlyToPristineState", "store_only_to_pristine_state"),
]


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_general_config_path(config: DayzConfig) -> Path | None:
    """Return TraderPlusGeneralConfig.json path if it exists."""
    cfg_dir = get_traderplus_config_dir(config)
    if not cfg_dir:
        return None
    p = cfg_dir / "TraderPlusGeneralConfig.json"
    return p if p.is_file() else None


# ---------------------------------------------------------------------------
# Parse & save
# ---------------------------------------------------------------------------

def parse_general_config(path: Path) -> TraderPlusGeneralConfig:
    """Parse TraderPlusGeneralConfig.json."""
    data = load_json(path)
    try:
        licences = json_list(data, "Licences")
    except ValueError as exc:
        raise refuse_in(path, exc) from exc
    traders = []
    for i, t in enumerate(data.get("Traders", [])):
        try:
            traders.append(TraderNpc.from_dict(t))
        except ValueError as exc:
            raise refuse_in(path, exc, f"trader {i + 1}") from exc

    cfg = TraderPlusGeneralConfig(
        path=path,
        _raw=data,
        version=str(data.get("Version", "")),
        lock_pick_chance=float(data.get("LockPickChance", 0.0)),
        licence_keyword=str(data.get("LicenceKeyWord", "")),
        licences=licences,
        accepted_states=AcceptedStates.from_dict(data.get("AcceptedStates", {})),
        currencies=[Currency.from_dict(c) for c in data.get("Currencies", [])],
        traders=traders,
        trader_object_count=len(data.get("TraderObjects", [])),
    )

    # Parse toggles
    for json_key, attr in _TOGGLE_MAP:
        setattr(cfg, attr, int(data.get(json_key, 0)))

    cfg._read = _known(cfg)
    return cfg


def _known(cfg: TraderPlusGeneralConfig) -> dict[str, Any]:
    """The keys the web editor edits, as *cfg* holds them."""
    known: dict[str, Any] = {json_key: getattr(cfg, attr) for json_key, attr in _TOGGLE_MAP}
    known.update({
        "LockPickChance": cfg.lock_pick_chance,
        "LicenceKeyWord": cfg.licence_keyword,
        "Licences": list(cfg.licences),
        "AcceptedStates": cfg.accepted_states.to_dict(),
        "Currencies": [c.to_dict() for c in cfg.currencies],
        "Traders": [t.to_dict() for t in cfg.traders],
    })
    return known


def save_general_config(cfg: TraderPlusGeneralConfig) -> None:
    """Write TraderPlusGeneralConfig.json, patching only the web editor's fields."""
    data = over_raw(cfg._raw, _known(cfg), cfg._read)
    save_json_patched(cfg.path, data)
    # Keep _raw in sync
    cfg._raw = data


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def backup_general_config(path: Path) -> Path:
    """Create a .backup copy of the general config."""
    return backup_file(path)


def restore_general_config(path: Path) -> bool:
    """Restore general config from .backup. Returns True on success."""
    return restore_file(path)



# ---------------------------------------------------------------------------
# CRUD — currencies
# ---------------------------------------------------------------------------

def add_currency(cfg: TraderPlusGeneralConfig, classname: str, value: int) -> Currency:
    """Add a new currency entry, inserted in descending value order."""
    c = Currency(classname=classname, value=value)
    # Insert so list stays sorted by value descending
    idx = 0
    for i, existing in enumerate(cfg.currencies):
        if value > existing.value:
            idx = i
            break
        idx = i + 1
    cfg.currencies.insert(idx, c)
    return c


def remove_currency(cfg: TraderPlusGeneralConfig, index: int) -> None:
    """Remove a currency by index."""
    if 0 <= index < len(cfg.currencies):
        cfg.currencies.pop(index)


def update_currency(cfg: TraderPlusGeneralConfig, index: int, classname: str, value: int) -> None:
    """Update a currency at index."""
    if 0 <= index < len(cfg.currencies):
        cfg.currencies[index].classname = classname
        cfg.currencies[index].value = value


# ---------------------------------------------------------------------------
# CRUD — licences
# ---------------------------------------------------------------------------

def add_licence(cfg: TraderPlusGeneralConfig, name: str) -> None:
    """Add a licence."""
    cfg.licences.append(name)


def remove_licence(cfg: TraderPlusGeneralConfig, index: int) -> None:
    """Remove a licence by index."""
    if 0 <= index < len(cfg.licences):
        cfg.licences.pop(index)


# ---------------------------------------------------------------------------
# CRUD — trader NPCs
# ---------------------------------------------------------------------------

def add_trader_npc(cfg: TraderPlusGeneralConfig, **kwargs: Any) -> TraderNpc:
    """Add a new trader NPC."""
    npc = TraderNpc(**{k: v for k, v in kwargs.items() if hasattr(TraderNpc, k)})
    cfg.traders.append(npc)
    return npc


def remove_trader_npc(cfg: TraderPlusGeneralConfig, index: int) -> None:
    """Remove a trader NPC by index."""
    if 0 <= index < len(cfg.traders):
        cfg.traders.pop(index)


def update_trader_npc(cfg: TraderPlusGeneralConfig, index: int, **kwargs: Any) -> None:
    """Update a trader NPC at index with provided keyword arguments."""
    if 0 <= index < len(cfg.traders):
        npc = cfg.traders[index]
        for key, value in kwargs.items():
            if hasattr(npc, key):
                setattr(npc, key, value)
