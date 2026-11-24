"""Trader state for the spawn editor.

Loads TraderPlusGeneralConfig.json and provides CRUD for Traders and
TraderObjects displayed on the map.
"""

from __future__ import annotations

import asyncio

import logging
from pathlib import Path
from typing import Any, TYPE_CHECKING

from app.spawn_editor.converters import game_to_latlng, keep_or_round, latlng_to_game
from app.spawn_editor import versioning
from app.spawn_editor.tp_document import read_document, refuse_unreadable

if TYPE_CHECKING:
    from app.shared.config import DayzConfig
    from app.spawn_editor.state import Broadcaster

log = logging.getLogger(__name__)


class TradersState:
    """Manages TraderPlus trader NPCs and objects for map display and editing."""

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
        """Load TraderPlusGeneralConfig.json from disk."""
        from app.traderplus.trader_common import get_traderplus_config_dir

        self._unreadable = False
        cfg_dir = get_traderplus_config_dir(self.config)
        if not cfg_dir:
            self._raw = {}
            self._config_path = None
            return

        path = cfg_dir / "TraderPlusGeneralConfig.json"
        if not path.is_file():
            self._raw = {}
            self._config_path = None
            return

        self._config_path = path
        raw = read_document(path, "Traders", "TraderObjects")
        if raw is None:
            self._raw = {}
            self._unreadable = True
            return
        self._raw = raw

        traders = self._raw.get("Traders", [])
        objects = self._raw.get("TraderObjects", [])
        log.warning(
            "Traders loaded: %d NPCs, %d objects from %s",
            len(traders), len(objects), path,
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def get_traders_json(self) -> dict:
        """Serialize traders + trader objects for API response."""
        traders_out: list[dict] = []
        for idx, trader in enumerate(self._raw.get("Traders", [])):
            pos = trader.get("Position", [0, 0, 0])
            orient = trader.get("Orientation", [0, 0, 0])
            x, y, z = float(pos[0]), float(pos[1]), float(pos[2])
            lat, lng = game_to_latlng(x, z, self.terrain)
            traders_out.append({
                "index": idx,
                "id": trader.get("Id", 0),
                "name": trader.get("Name", ""),
                "given_name": trader.get("GivenName", ""),
                "role": trader.get("Role", ""),
                "lat": round(lat, 6),
                "lng": round(lng, 6),
                "game_x": round(x, 2),
                "game_y": round(y, 2),
                "game_z": round(z, 2),
                "orientation": round(float(orient[0]), 1),
                "clothes": trader.get("Clothes", []),
            })

        objects_out: list[dict] = []
        for idx, obj in enumerate(self._raw.get("TraderObjects", [])):
            pos = obj.get("Position", [0, 0, 0])
            orient = obj.get("Orientation", [0, 0, 0])
            x, y, z = float(pos[0]), float(pos[1]), float(pos[2])
            lat, lng = game_to_latlng(x, z, self.terrain)
            objects_out.append({
                "index": idx,
                "object_name": obj.get("ObjectName", ""),
                "lat": round(lat, 6),
                "lng": round(lng, 6),
                "game_x": round(x, 2),
                "game_y": round(y, 2),
                "game_z": round(z, 2),
                "orientation": round(float(orient[0]), 1),
            })

        return {"traders": traders_out, "trader_objects": objects_out}

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
    # Trader NPC CRUD
    # ------------------------------------------------------------------

    def _find_trader_index(self, trader_id: int) -> int | None:
        """Find index of trader by Id. Returns None if not found."""
        for i, t in enumerate(self._raw.get("Traders", [])):
            if t.get("Id") == trader_id:
                return i
        return None

    def _next_trader_id(self) -> int:
        """Return the next available positive trader Id."""
        existing = [t.get("Id", 0) for t in self._raw.get("Traders", [])]
        positive = [i for i in existing if i >= 0]
        return max(positive, default=-1) + 1

    async def add_trader(self, data: dict) -> dict | None:
        """Add a new trader NPC. Returns serialized trader or None."""
        async with self._lock:
            refuse_unreadable(self._unreadable, "TraderPlusGeneralConfig.json")
            traders = self._raw.setdefault("Traders", [])
            new_id = data.get("id", self._next_trader_id())

            # Convert lat/lng to game coords if provided
            if "lat" in data and "lng" in data:
                gx, gz = latlng_to_game(data["lat"], data["lng"], self.terrain)
            else:
                gx = data.get("game_x", 0.0)
                gz = data.get("game_z", 0.0)
            gy = data.get("game_y", 0.0)

            trader = {
                "Id": new_id,
                "Name": data.get("name", "SurvivorM_Peter"),
                "GivenName": data.get("given_name", "Trader"),
                "Role": data.get("role", "Trader"),
                "Position": [round(gx, 2), round(gy, 2), round(gz, 2)],
                "Orientation": [round(data.get("orientation", 0.0), 1), 0.0, 0.0],
                "Clothes": data.get("clothes", []),
            }
            traders.append(trader)
            self._save()

        await self._broadcast()
        return trader

    async def update_trader(self, index: int, data: dict) -> dict | None:
        """Update trader fields by array index."""
        async with self._lock:
            traders = self._raw.get("Traders", [])
            if index < 0 or index >= len(traders):
                return None

            trader = traders[index]
            if "id" in data:
                trader["Id"] = int(data["id"])
            if "name" in data:
                trader["Name"] = data["name"]
            if "given_name" in data:
                trader["GivenName"] = data["given_name"]
            if "role" in data:
                trader["Role"] = data["role"]
            if "clothes" in data:
                trader["Clothes"] = data["clothes"]

            # Position: accept either lat/lng (from drag) or game coords
            if "lat" in data and "lng" in data:
                gx, gz = latlng_to_game(data["lat"], data["lng"], self.terrain)
                pos = trader.get("Position", [0, 0, 0])
                gy = keep_or_round(pos[1], data.get("game_y", pos[1]))
                trader["Position"] = [round(gx, 2), gy, round(gz, 2)]
            elif "game_x" in data or "game_y" in data or "game_z" in data:
                pos = trader.get("Position", [0, 0, 0])
                trader["Position"] = [
                    keep_or_round(pos[0], data.get("game_x", pos[0])),
                    keep_or_round(pos[1], data.get("game_y", pos[1])),
                    keep_or_round(pos[2], data.get("game_z", pos[2])),
                ]
            if "orientation" in data:
                trader["Orientation"] = [keep_or_round((trader.get("Orientation") or [0.0])[0], data["orientation"], 1), 0.0, 0.0]
            self._save()

        await self._broadcast()
        return trader

    async def update_trader_position_game(
        self, index: int, x: float, y: float, z: float, orientation: float | None = None,
    ) -> bool:
        """Update trader position using game coordinates."""
        async with self._lock:
            traders = self._raw.get("Traders", [])
            if index < 0 or index >= len(traders):
                return False

            trader = traders[index]
            trader["Position"] = [round(x, 2), round(y, 2), round(z, 2)]
            if orientation is not None:
                trader["Orientation"] = [round(orientation, 1), 0.0, 0.0]
            self._save()

        await self._broadcast()
        return True

    async def delete_trader(self, index: int) -> bool:
        """Delete a trader NPC by array index."""
        async with self._lock:
            traders = self._raw.get("Traders", [])
            if index < 0 or index >= len(traders):
                return False
            traders.pop(index)
            self._save()

        await self._broadcast()
        return True

    # ------------------------------------------------------------------
    # Trader Object CRUD
    # ------------------------------------------------------------------

    async def add_trader_object(self, data: dict) -> dict | None:
        """Add a new trader object. Returns raw dict or None."""
        async with self._lock:
            refuse_unreadable(self._unreadable, "TraderPlusGeneralConfig.json")
            objects = self._raw.setdefault("TraderObjects", [])

            if "lat" in data and "lng" in data:
                gx, gz = latlng_to_game(data["lat"], data["lng"], self.terrain)
            else:
                gx = data.get("game_x", 0.0)
                gz = data.get("game_z", 0.0)
            gy = data.get("game_y", 0.0)

            obj = {
                "ObjectName": data.get("object_name", "Land_City_Stand_FastFood"),
                "Position": [round(gx, 2), round(gy, 2), round(gz, 2)],
                "Orientation": [round(data.get("orientation", 0.0), 1), 0.0, 0.0],
            }
            objects.append(obj)
            self._save()

        await self._broadcast()
        return obj

    async def update_trader_object(self, index: int, data: dict) -> dict | None:
        """Update a trader object by array index."""
        async with self._lock:
            objects = self._raw.get("TraderObjects", [])
            if index < 0 or index >= len(objects):
                return None

            obj = objects[index]
            if "object_name" in data:
                obj["ObjectName"] = data["object_name"]

            # Position: accept either lat/lng (from drag) or game coords
            if "lat" in data and "lng" in data:
                gx, gz = latlng_to_game(data["lat"], data["lng"], self.terrain)
                pos = obj.get("Position", [0, 0, 0])
                gy = keep_or_round(pos[1], data.get("game_y", pos[1]))
                obj["Position"] = [round(gx, 2), gy, round(gz, 2)]
            elif "game_x" in data or "game_y" in data or "game_z" in data:
                pos = obj.get("Position", [0, 0, 0])
                obj["Position"] = [
                    keep_or_round(pos[0], data.get("game_x", pos[0])),
                    keep_or_round(pos[1], data.get("game_y", pos[1])),
                    keep_or_round(pos[2], data.get("game_z", pos[2])),
                ]
            if "orientation" in data:
                obj["Orientation"] = [keep_or_round((obj.get("Orientation") or [0.0])[0], data["orientation"], 1), 0.0, 0.0]
            self._save()

        await self._broadcast()
        return obj

    async def update_trader_object_position_game(
        self, index: int, x: float, y: float, z: float, orientation: float | None = None,
    ) -> bool:
        """Update trader object position using game coordinates."""
        async with self._lock:
            objects = self._raw.get("TraderObjects", [])
            if index < 0 or index >= len(objects):
                return False

            obj = objects[index]
            obj["Position"] = [round(x, 2), round(y, 2), round(z, 2)]
            if orientation is not None:
                obj["Orientation"] = [round(orientation, 1), 0.0, 0.0]
            self._save()

        await self._broadcast()
        return True

    async def delete_trader_object(self, index: int) -> bool:
        """Delete a trader object by array index."""
        async with self._lock:
            objects = self._raw.get("TraderObjects", [])
            if index < 0 or index >= len(objects):
                return False
            objects.pop(index)
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
        """Send traders_changed to all WebSocket clients."""
        await self._broadcaster.broadcast({"type": "traders_changed"})
