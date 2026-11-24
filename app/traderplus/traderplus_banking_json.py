"""TraderPlus BankingConfig JSON parsing, editing, and backup/restore."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.shared.config import DayzConfig
from app.shared.json_patch import save_json_patched
from app.shared.json_utils import load_json
from app.traderplus.trader_common import get_traderplus_config_dir, json_list, over_raw, refuse_in
from app.xml.xml_utils import backup_file, restore_file


# ---------------------------------------------------------------------------
# Field descriptions (shown as help in the web editor)
# ---------------------------------------------------------------------------

FIELD_DESCRIPTIONS: dict[str, str] = {
    "banking_logs": "Log every deposit/withdraw/transfer to the server profile log for admin review. Adds load on busy servers but is the only audit trail for currency disputes.",
    "is_credit_car_needed": "Require players to carry a TraderPlus_CreditCard item in inventory to access banking from any bank trader. Off = bank UI is always available at bank traders.",
    "transaction_fees": "Percentage fee on transactions (e.g., 0.023 = 2.3%). Set to 0 for no fees.",
    "default_start_currency": "One-time starting balance granted when a player first connects to the server with TraderPlus loaded. Not granted again on death/respawn — only when their Account_<SteamID64>.json is first created.",
    "default_max_currency": "Default cap on bank balance for new accounts. Hard ceiling is 2,147,483,647 (32-bit int). Per-account MaxAmount can be edited later in Account_<SteamID64>.json files for VIP players.",
    "transfer_message": "Chat line shown to the recipient after a successful player-to-player bank transfer. BI default ends with a literal exclamation + space. Empty = use TraderPlus built-in default.",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TraderPlusBankingConfig:
    """Parsed TraderPlusBankingConfig.json file.

    Uses ``_raw`` to preserve the full original JSON.  On save only the fields
    the web editor edits are patched back into ``_raw``.
    """

    path: Path = field(default_factory=Path)
    _raw: dict[str, Any] = field(default_factory=dict)
    #: The edited keys as parsed (``over_raw``): a key the file lacks is saved only once it changes.
    _read: dict[str, Any] | None = field(default=None, repr=False, compare=False)

    version: str = ""
    banking_logs: int = 1
    is_credit_car_needed: int = 1
    transaction_fees: float = 0.0
    default_start_currency: int = 500
    default_max_currency: int = 100000
    currencies_accepted: list[str] = field(default_factory=list)
    transfer_message: str = ""


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_banking_config_path(config: DayzConfig) -> Path | None:
    """Return TraderPlusBankingConfig.json path if it exists."""
    cfg_dir = get_traderplus_config_dir(config)
    if not cfg_dir:
        return None
    p = cfg_dir / "TraderPlusBankingConfig.json"
    return p if p.is_file() else None


# ---------------------------------------------------------------------------
# Parse & save
# ---------------------------------------------------------------------------

def parse_banking_config(path: Path) -> TraderPlusBankingConfig:
    """Parse TraderPlusBankingConfig.json."""
    data = load_json(path)
    try:
        currencies = json_list(data, "CurrenciesAccepted")
    except ValueError as exc:
        raise refuse_in(path, exc) from exc

    cfg = TraderPlusBankingConfig(
        path=path,
        _raw=data,
        version=str(data.get("Version", "")),
        banking_logs=int(data.get("BankingLogs", 1)),
        is_credit_car_needed=int(data.get("IsCreditCarNeededForTransaction", 1)),
        transaction_fees=float(data.get("TransactionFees", 0.0)),
        default_start_currency=int(data.get("DefaultStartCurrency", 500)),
        default_max_currency=int(data.get("DefaultMaxCurrency", 100000)),
        currencies_accepted=currencies,
        transfer_message=str(data.get(
            "TheAmountHasBeenTransferedToTheAccount",
            "The Amount has been successfully send to the account of destination !",
        )),
    )
    cfg._read = _known(cfg)
    return cfg


def _known(cfg: TraderPlusBankingConfig) -> dict[str, Any]:
    """The keys the web editor edits, as *cfg* holds them."""
    return {
        "BankingLogs": cfg.banking_logs,
        "IsCreditCarNeededForTransaction": cfg.is_credit_car_needed,
        "TransactionFees": cfg.transaction_fees,
        "DefaultStartCurrency": cfg.default_start_currency,
        "DefaultMaxCurrency": cfg.default_max_currency,
        "CurrenciesAccepted": list(cfg.currencies_accepted),
        "TheAmountHasBeenTransferedToTheAccount": cfg.transfer_message,
    }


def save_banking_config(cfg: TraderPlusBankingConfig) -> None:
    """Write TraderPlusBankingConfig.json, patching only the web editor's fields."""
    data = over_raw(cfg._raw, _known(cfg), cfg._read)
    save_json_patched(cfg.path, data)
    cfg._raw = data


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def backup_banking_config(path: Path) -> Path:
    """Create a .backup copy of the banking config."""
    return backup_file(path)


def restore_banking_config(path: Path) -> bool:
    """Restore banking config from .backup. Returns True on success."""
    return restore_file(path)


# ---------------------------------------------------------------------------
# CRUD — currencies accepted
# ---------------------------------------------------------------------------

def add_accepted_currency(cfg: TraderPlusBankingConfig, classname: str) -> None:
    """Add a currency classname to the accepted list."""
    cfg.currencies_accepted.append(classname)


def remove_accepted_currency(cfg: TraderPlusBankingConfig, index: int) -> None:
    """Remove a currency by index."""
    if 0 <= index < len(cfg.currencies_accepted):
        cfg.currencies_accepted.pop(index)
