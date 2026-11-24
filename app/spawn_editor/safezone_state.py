"""SafeZone state for the spawn editor.

Loads TraderPlusSafeZoneConfig.json and provides CRUD for safe zone circles
displayed on the map.
"""

from __future__ import annotations

import asyncio

import logging
from pathlib import Path
from typing import Any, TYPE_CHECKING

from app.spawn_editor.converters import game_to_latlng, get_map_size, keep_or_round, latlng_to_game
from app.spawn_editor import versioning
from app.spawn_editor.tp_document import read_document, refuse_unreadable

if TYPE_CHECKING:
    from app.shared.config import DayzConfig
    from app.spawn_editor.state import Broadcaster

log = logging.getLogger(__name__)


def _countdown(value: object) -> int:
    """SafeZone declares ``int Countdown`` (``SafeZoneLocation.c``), so a
    fraction is refused rather than cut to the whole number below it."""
    number = float(value)
    if not number.is_integer():
        raise ValueError(f"Countdown must be a whole number of seconds, not {value}")
    return int(number)


class SafeZoneState:
    """Manages TraderPlus safe zones for map display and editing."""

    def __init__(self, config: DayzConfig, terrain: str, broadcaster: Broadcaster) -> None:
        self.config = config
        self.terrain = terrain
        self._broadcaster = broadcaster
        self._lock = asyncio.Lock()
        self._raw: dict[str, Any] = {}
        self._config_path: Path | None = None
        # The file is there and could not be read (see tp_document).
        self._unreadable = False

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load TraderPlusSafeZoneConfig.json from disk."""
        from app.traderplus.trader_common import get_traderplus_config_dir

        self._unreadable = False
        cfg_dir = get_traderplus_config_dir(self.config)
        if not cfg_dir:
            self._raw = {}
            self._config_path = None
            return

        path = cfg_dir / "TraderPlusSafeZoneConfig.json"
        if not path.is_file():
            self._raw = {}
            self._config_path = None
            return

        self._config_path = path
        raw = read_document(path, "SafeAreaLocation")
        if raw is None:
            self._raw = {}
            self._unreadable = True
            return
        self._raw = raw

        zones = self._raw.get("SafeAreaLocation", [])
        log.info("SafeZones loaded: %d zones from %s", len(zones), path)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def get_safezones_json(self) -> dict:
        """Serialize safe zones for API response."""
        zones_out: list[dict] = []
        map_size = get_map_size(self.terrain)

        for idx, zone in enumerate(self._raw.get("SafeAreaLocation", [])):
            # SafeZone X = game x, SafeZone Y = game z
            game_x = float(zone.get("X", 0))
            game_z = float(zone.get("Y", 0))
            radius = round(float(zone.get("Radius", 80)), 2)

            lat, lng = game_to_latlng(game_x, game_z, self.terrain)

            zones_out.append({
                "index": idx,
                "name": zone.get("SafeZoneStatut", ""),
                "lat": round(lat, 6),
                "lng": round(lng, 6),
                "game_x": round(game_x, 2),
                "game_z": round(game_z, 2),
                "radius": radius,
                "countdown": int(zone.get("Countdown", 15)),
                "map_size": map_size,
            })

        return {"safezones": zones_out}

    # ------------------------------------------------------------------
    # Save helper
    # ------------------------------------------------------------------

    def _save(self) -> None:
        """Write current _raw to disk.

        Atomic: the editor rewrites this config on every drag/edit, so a bare
        write_text truncated by a crash mid-write would wipe the whole config.
        """
        if not self._config_path:
            return
        versioning.write_json(self._config_path, self._raw)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def add_safezone(self, data: dict) -> dict | None:
        """Add a new safe zone."""
        async with self._lock:
            refuse_unreadable(self._unreadable, "TraderPlusSafeZoneConfig.json")
            zones = self._raw.setdefault("SafeAreaLocation", [])

            # Convert lat/lng to game coords if provided
            if "lat" in data and "lng" in data:
                gx, gz = latlng_to_game(data["lat"], data["lng"], self.terrain)
            else:
                gx = data.get("game_x", 0.0)
                gz = data.get("game_z", 0.0)

            zone = {
                "SafeZoneStatut": data.get("name", "New Safe Zone"),
                "X": round(gx, 2),
                "Y": round(gz, 2),
                "Radius": round(float(data.get("radius", 80)), 2),
                "Countdown": _countdown(data.get("countdown", 15)),
            }
            zones.append(zone)
            self._save()

        await self._broadcast()
        return zone

    async def update_safezone(self, index: int, data: dict) -> dict | None:
        """Update safe zone fields by index."""
        async with self._lock:
            zones = self._raw.get("SafeAreaLocation", [])
            if index < 0 or index >= len(zones):
                return None

            zone = zones[index]
            # Checked before anything changes, so a refusal leaves the zone as it was.
            countdown = _countdown(data["countdown"]) if "countdown" in data else None
            if "name" in data:
                zone["SafeZoneStatut"] = data["name"]

            # Position: accept either lat/lng (from drag) or game coords
            if "lat" in data and "lng" in data:
                gx, gz = latlng_to_game(data["lat"], data["lng"], self.terrain)
                zone["X"] = round(gx, 2)
                zone["Y"] = round(gz, 2)
            elif "game_x" in data or "game_z" in data:
                if "game_x" in data:
                    zone["X"] = keep_or_round(zone.get("X"), data["game_x"])
                if "game_z" in data:
                    zone["Y"] = keep_or_round(zone.get("Y"), data["game_z"])

            if "radius" in data:
                zone["Radius"] = keep_or_round(zone.get("Radius"), data["radius"])
            if countdown is not None:
                zone["Countdown"] = countdown

            self._save()

        await self._broadcast()
        return zone

    async def delete_safezone(self, index: int) -> bool:
        """Delete a safe zone by index."""
        async with self._lock:
            zones = self._raw.get("SafeAreaLocation", [])
            if index < 0 or index >= len(zones):
                return False
            zones.pop(index)
            self._save()

        await self._broadcast()
        return True

    # ------------------------------------------------------------------
    # Reload (for file watcher)
    # ------------------------------------------------------------------

    async def reload_and_broadcast(self) -> None:
        """Reload from disk and notify clients."""
        async with self._lock:
            self.load()
            await self._broadcast()

    async def _broadcast(self) -> None:
        """Send safezones_changed to all WebSocket clients."""
        await self._broadcaster.broadcast({"type": "safezones_changed"})
