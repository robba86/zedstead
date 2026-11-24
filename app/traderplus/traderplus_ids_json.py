"""TraderPlus IDsConfig JSON parsing, editing, and backup/restore."""

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
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TraderIdEntry:
    """One trader ID entry mapping categories, licences, and currencies."""

    id: int = 0
    categories: list[str] = field(default_factory=list)
    licences_required: list[str] = field(default_factory=list)
    currencies_accepted: list[str] = field(default_factory=list)
    #: The entry as the file had it (``over_raw``).
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> TraderIdEntry:
        return TraderIdEntry(
            raw=d,
            id=int(d.get("Id", 0)),
            categories=json_list(d, "Categories"),
            licences_required=json_list(d, "LicencesRequired"),
            currencies_accepted=json_list(d, "CurrenciesAccepted"),
        )

    def known(self) -> dict[str, Any]:
        return {
            "Id": self.id,
            "Categories": list(self.categories),
            "LicencesRequired": list(self.licences_required),
            "CurrenciesAccepted": list(self.currencies_accepted),
        }

    def to_dict(self) -> dict[str, Any]:
        return entry_over_raw(self)


@dataclass
class TraderPlusIDsConfig:
    """Parsed TraderPlusIDsConfig.json file.

    Uses ``_raw`` to preserve the full original JSON.
    """

    path: Path = field(default_factory=Path)
    _raw: dict[str, Any] = field(default_factory=dict)
    #: The edited keys as parsed (``over_raw``): a key the file lacks is saved only once it changes.
    _read: dict[str, Any] | None = field(default=None, repr=False, compare=False)

    version: str = ""
    entries: list[TraderIdEntry] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_ids_config_path(config: DayzConfig) -> Path | None:
    """Return TraderPlusIDsConfig.json path if it exists."""
    cfg_dir = get_traderplus_config_dir(config)
    if not cfg_dir:
        return None
    p = cfg_dir / "TraderPlusIDsConfig.json"
    return p if p.is_file() else None


# ---------------------------------------------------------------------------
# Parse & save
# ---------------------------------------------------------------------------

def parse_ids_config(path: Path) -> TraderPlusIDsConfig:
    """Parse TraderPlusIDsConfig.json."""
    data = load_json(path)
    entries = []
    for i, e in enumerate(data.get("IDs", [])):
        try:
            entries.append(TraderIdEntry.from_dict(e))
        except ValueError as exc:
            raise refuse_in(path, exc, f"trader entry {i + 1}") from exc

    cfg = TraderPlusIDsConfig(
        path=path,
        _raw=data,
        version=str(data.get("Version", "")),
        entries=entries,
    )
    cfg._read = _known(cfg)
    return cfg


def _known(cfg: TraderPlusIDsConfig) -> dict[str, Any]:
    """The keys the web editor edits, as *cfg* holds them."""
    return {
        "IDs": [e.to_dict() for e in cfg.entries],
    }


def save_ids_config(cfg: TraderPlusIDsConfig) -> None:
    """Write TraderPlusIDsConfig.json, patching only the web editor's fields."""
    data = over_raw(cfg._raw, _known(cfg), cfg._read)
    save_json_patched(cfg.path, data)
    cfg._raw = data


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def backup_ids_config(path: Path) -> Path:
    """Create a .backup copy of the IDs config."""
    return backup_file(path)


def restore_ids_config(path: Path) -> bool:
    """Restore IDs config from .backup. Returns True on success."""
    return restore_file(path)


# ---------------------------------------------------------------------------
# CRUD — entries
# ---------------------------------------------------------------------------

def add_entry(cfg: TraderPlusIDsConfig, entry_id: int) -> TraderIdEntry:
    """Add a new trader ID entry."""
    entry = TraderIdEntry(id=entry_id)
    cfg.entries.append(entry)
    return entry


def remove_entry(cfg: TraderPlusIDsConfig, index: int) -> None:
    """Remove an entry by list index."""
    if 0 <= index < len(cfg.entries):
        cfg.entries.pop(index)


def find_entry_by_id(cfg: TraderPlusIDsConfig, entry_id: int) -> int | None:
    """Find list index of entry by trader Id. Returns None if not found."""
    for i, e in enumerate(cfg.entries):
        if e.id == entry_id:
            return i
    return None


# ---------------------------------------------------------------------------
# CRUD — per-entry lists
# ---------------------------------------------------------------------------

def add_category(entry: TraderIdEntry, category: str) -> None:
    """Add a category to a trader ID entry."""
    if category not in entry.categories:
        entry.categories.append(category)


def remove_category(entry: TraderIdEntry, category: str) -> None:
    """Remove a category from a trader ID entry."""
    if category in entry.categories:
        entry.categories.remove(category)


def set_categories(entry: TraderIdEntry, categories: list[str]) -> None:
    """Replace all categories on a trader ID entry."""
    entry.categories = list(categories)


def add_licence_required(entry: TraderIdEntry, licence: str) -> None:
    """Add a required licence to a trader ID entry."""
    if licence not in entry.licences_required:
        entry.licences_required.append(licence)


def remove_licence_required(entry: TraderIdEntry, licence: str) -> None:
    """Remove a required licence from a trader ID entry."""
    if licence in entry.licences_required:
        entry.licences_required.remove(licence)


def set_currencies_accepted(entry: TraderIdEntry, currencies: list[str]) -> None:
    """Replace all accepted currencies on a trader ID entry."""
    entry.currencies_accepted = list(currencies)



# ---------------------------------------------------------------------------
# Cross-reference helpers
# ---------------------------------------------------------------------------

def get_all_price_categories(config: DayzConfig) -> list[str]:
    """Read all category names from TraderPlusPriceConfig.json."""
    from app.traderplus.traderplus_json import get_price_config_path, parse_price_config

    path = get_price_config_path(config)
    if not path:
        return []
    try:
        pcfg = parse_price_config(path)
        return [c.name for c in pcfg.categories]
    except (OSError, ValueError):
        return []


def get_all_licences(config: DayzConfig) -> list[str]:
    """Read all licence names from TraderPlusGeneralConfig.json."""
    from app.traderplus.traderplus_general_json import get_general_config_path, parse_general_config

    path = get_general_config_path(config)
    if not path:
        return []
    try:
        gcfg = parse_general_config(path)
        return list(gcfg.licences)
    except (OSError, ValueError):
        return []


def get_trader_names(config: DayzConfig) -> dict[int, str]:
    """Read trader Id → GivenName mapping from TraderPlusGeneralConfig.json."""
    from app.traderplus.traderplus_general_json import get_general_config_path, parse_general_config

    path = get_general_config_path(config)
    if not path:
        return {}
    try:
        gcfg = parse_general_config(path)
        names: dict[int, str] = {}
        for t in gcfg._raw.get("Traders", []):
            names[int(t.get("Id", -1))] = t.get("GivenName", t.get("Name", ""))
        return names
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
