"""TraderPlus SafeZoneConfig JSON parsing, editing, and backup/restore."""

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
    "is_hideout_active": "Enable the per-player secret stash inside safe zones (tied to TraderPlus banking account). Players must remove their armband to access it — see the Armband warning message below. Off = stash UI hidden inside safe zones.",
    "enable_afk_disconnect": "Enable the AFK kicker that monitors players inside safe zones. Off = players can stand in a safe zone indefinitely without being kicked. Use the AFK kick delay below to set the threshold.",
    "kick_after_delay": "Minutes of no movement before an AFK player is kicked. Default 30 (= 30 min). Players are notified before each AFK position check so they can move and reset the timer.",
    "cleanup_timer": "Seconds between cleanup passes for items in the Objects to Delete list (below). Empty list = no items are deleted regardless of this timer. Default 600 (= 10 min).",
    "msg_enter_zone": "Notification shown when a player enters a safe zone. Empty = use TraderPlus built-in default (e.g. \"Welcome! Don't worry, you're in our safe zone now.\").",
    "msg_exit_zone": "Notification shown after the leaving countdown finishes — protections are now off. Empty = use TraderPlus built-in default (e.g. \"Good luck, have fun and don't die! You've left our safe zone.\").",
    "msg_on_leaving_zone": "Notification shown while the leaving countdown is ticking — concatenated live with the remaining seconds. Default: \"Heads up, safe zone expires in:\" + countdown.",
    "must_remove_armband": "Notification shown when a player tries to access the secret stash with an armband equipped. Default: \"Please remove armbands to see your secret stash!\". Only relevant when Hideout stash active is on.",
    "safe_zone_statut": "Name identifier for this safe zone — must be unique across all zones (the editor enforces this). Shown as the title in the Edit Safe Zone child section. The misspelling 'Statut' (instead of 'Status') is the BI key name and is preserved as-is.",
    "x": "Map X coordinate (west-east) for the center of the safe zone. Use izurvive.com to look up coordinates by clicking on the map.",
    "y": "Map Y coordinate (south-north) for the center of the safe zone. Use izurvive.com to look up coordinates by clicking on the map.",
    "radius": "Radius in meters from the X/Y center to the safe zone perimeter. Vanilla defaults: 80 m for general traders, 40 m for tighter military traders. Make it large enough to cover all NPC positions plus walking room.",
    "countdown": "Seconds the safe-zone protections keep applying after a player crosses the perimeter. The Leaving zone message + live timer is shown during this grace period; once it expires, normal PvP and blocked actions resume.",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SafeAreaLocation:
    """One safe zone area definition."""

    safe_zone_statut: str = ""
    x: float = 0.0
    y: float = 0.0
    #: A float in the mod (``SafeZoneLocation``: ``float X, Y, Radius``).
    radius: float = 80
    countdown: int = 15
    #: The entry as the file had it (``over_raw``).
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> SafeAreaLocation:
        return SafeAreaLocation(
            raw=d,
            safe_zone_statut=str(d.get("SafeZoneStatut", "")),
            x=float(d.get("X", 0.0)),
            y=float(d.get("Y", 0.0)),
            radius=float(d.get("Radius", 80)),
            countdown=int(d.get("Countdown", 15)),
        )

    def known(self) -> dict[str, Any]:
        return {
            "SafeZoneStatut": self.safe_zone_statut,
            "X": self.x,
            "Y": self.y,
            "Radius": self.radius,
            "Countdown": self.countdown,
        }

    def to_dict(self) -> dict[str, Any]:
        return entry_over_raw(self)


@dataclass
class TraderPlusSafeZoneConfig:
    """Parsed TraderPlusSafeZoneConfig.json file.

    Uses ``_raw`` to preserve the full original JSON.
    """

    path: Path = field(default_factory=Path)
    _raw: dict[str, Any] = field(default_factory=dict)
    #: The edited keys as parsed (``over_raw``): a key the file lacks is saved only once it changes.
    _read: dict[str, Any] | None = field(default=None, repr=False, compare=False)

    version: str = ""

    # Toggles / scalars
    is_hideout_active: int = 1
    enable_afk_disconnect: int = 0
    kick_after_delay: int = 30
    cleanup_timer: int = 600

    # Messages
    msg_enter_zone: str = ""
    msg_exit_zone: str = ""
    msg_on_leaving_zone: str = ""
    must_remove_armband: str = ""

    # Safe area locations
    safe_area_locations: list[SafeAreaLocation] = field(default_factory=list)

    # String arrays
    whitelist_entities: list[str] = field(default_factory=list)
    objects_to_delete: list[str] = field(default_factory=list)
    sz_steam_uids: list[str] = field(default_factory=list)
    blacklisted_item_in_stash: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_safezone_config_path(config: DayzConfig) -> Path | None:
    """Return TraderPlusSafeZoneConfig.json path if it exists."""
    cfg_dir = get_traderplus_config_dir(config)
    if not cfg_dir:
        return None
    p = cfg_dir / "TraderPlusSafeZoneConfig.json"
    return p if p.is_file() else None


# ---------------------------------------------------------------------------
# Parse & save
# ---------------------------------------------------------------------------

def parse_safezone_config(path: Path) -> TraderPlusSafeZoneConfig:
    """Parse TraderPlusSafeZoneConfig.json."""
    data = load_json(path)
    try:
        lists = {
            key: json_list(data, key)
            for key in ("WhitelistEntities", "ObjectsToDelete", "SZSteamUIDs", "BlackListedItemInStash")
        }
    except ValueError as exc:
        raise refuse_in(path, exc) from exc

    cfg = TraderPlusSafeZoneConfig(
        path=path,
        _raw=data,
        version=str(data.get("Version", "")),
        is_hideout_active=int(data.get("IsHideOutActive", 1)),
        enable_afk_disconnect=int(data.get("EnableAfkDisconnect", 0)),
        kick_after_delay=int(data.get("KickAfterDelay", 30)),
        cleanup_timer=int(data.get("CleanUpTimer", 600)),
        msg_enter_zone=str(data.get("MsgEnterZone", "")),
        msg_exit_zone=str(data.get("MsgExitZone", "")),
        msg_on_leaving_zone=str(data.get("MsgOnLeavingZone", "")),
        must_remove_armband=str(data.get("MustRemoveArmband", "")),
        safe_area_locations=[
            SafeAreaLocation.from_dict(s)
            for s in data.get("SafeAreaLocation", [])
        ],
        whitelist_entities=lists["WhitelistEntities"],
        objects_to_delete=lists["ObjectsToDelete"],
        sz_steam_uids=lists["SZSteamUIDs"],
        blacklisted_item_in_stash=lists["BlackListedItemInStash"],
    )
    cfg._read = _known(cfg)
    return cfg


def _known(cfg: TraderPlusSafeZoneConfig) -> dict[str, Any]:
    """The keys the web editor edits, as *cfg* holds them."""
    return {
        "IsHideOutActive": cfg.is_hideout_active,
        "EnableAfkDisconnect": cfg.enable_afk_disconnect,
        "KickAfterDelay": cfg.kick_after_delay,
        "CleanUpTimer": cfg.cleanup_timer,
        "MsgEnterZone": cfg.msg_enter_zone,
        "MsgExitZone": cfg.msg_exit_zone,
        "MsgOnLeavingZone": cfg.msg_on_leaving_zone,
        "MustRemoveArmband": cfg.must_remove_armband,
        "SafeAreaLocation": [s.to_dict() for s in cfg.safe_area_locations],
        "WhitelistEntities": list(cfg.whitelist_entities),
        "ObjectsToDelete": list(cfg.objects_to_delete),
        "SZSteamUIDs": list(cfg.sz_steam_uids),
        "BlackListedItemInStash": list(cfg.blacklisted_item_in_stash),
    }


def save_safezone_config(cfg: TraderPlusSafeZoneConfig) -> None:
    """Write TraderPlusSafeZoneConfig.json, patching only the web editor's fields."""
    data = over_raw(cfg._raw, _known(cfg), cfg._read)
    save_json_patched(cfg.path, data)
    cfg._raw = data


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def backup_safezone_config(path: Path) -> Path:
    """Create a .backup copy of the safezone config."""
    return backup_file(path)


def restore_safezone_config(path: Path) -> bool:
    """Restore safezone config from .backup. Returns True on success."""
    return restore_file(path)



# ---------------------------------------------------------------------------
# CRUD — safe area locations
# ---------------------------------------------------------------------------

def add_safe_area(
    cfg: TraderPlusSafeZoneConfig,
    name: str,
    x: float,
    y: float,
    radius: float = 80,
    countdown: int = 15,
) -> SafeAreaLocation:
    """Add a new safe area location."""
    area = SafeAreaLocation(
        safe_zone_statut=name,
        x=x, y=y,
        radius=radius,
        countdown=countdown,
    )
    cfg.safe_area_locations.append(area)
    return area


def remove_safe_area(cfg: TraderPlusSafeZoneConfig, index: int) -> None:
    """Remove a safe area by index."""
    if 0 <= index < len(cfg.safe_area_locations):
        cfg.safe_area_locations.pop(index)


def update_safe_area(
    cfg: TraderPlusSafeZoneConfig,
    index: int,
    name: str | None = None,
    x: float | None = None,
    y: float | None = None,
    radius: float | None = None,
    countdown: int | None = None,
) -> None:
    """Update a safe area at index (only provided fields)."""
    if 0 <= index < len(cfg.safe_area_locations):
        area = cfg.safe_area_locations[index]
        if name is not None:
            area.safe_zone_statut = name
        if x is not None:
            area.x = x
        if y is not None:
            area.y = y
        if radius is not None:
            area.radius = radius
        if countdown is not None:
            area.countdown = countdown


# ---------------------------------------------------------------------------
# CRUD — string arrays
# ---------------------------------------------------------------------------

def add_to_list(lst: list[str], value: str) -> None:
    """Add a value to a string list if not already present."""
    if value not in lst:
        lst.append(value)


def remove_from_list(lst: list[str], index: int) -> None:
    """Remove a value from a string list by index."""
    if 0 <= index < len(lst):
        lst.pop(index)
