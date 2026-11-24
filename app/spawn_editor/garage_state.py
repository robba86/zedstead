"""Garage state for the spawn editor.

Loads TraderPlusGarageConfig.json and provides CRUD for garage NPC and
parking markers displayed on the map.
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


class GarageState:
    """Manages TraderPlus garage NPCs for map display and editing."""

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
        """Load TraderPlusGarageConfig.json from disk."""
        from app.traderplus.trader_common import get_traderplus_config_dir

        self._unreadable = False
        cfg_dir = get_traderplus_config_dir(self.config)
        if not cfg_dir:
            self._raw = {}
            self._config_path = None
            return

        path = cfg_dir / "TraderPlusGarageConfig.json"
        if not path.is_file():
            self._raw = {}
            self._config_path = None
            return

        self._config_path = path
        raw = read_document(path, "NPCs")
        if raw is None:
            self._raw = {}
            self._unreadable = True
            return
        self._raw = raw

        npcs = self._raw.get("NPCs", [])
        log.info("Garage loaded: %d NPCs from %s", len(npcs), path)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def get_garage_json(self) -> dict:
        """Serialize garage NPCs for API response."""
        npcs_out: list[dict] = []

        for idx, npc in enumerate(self._raw.get("NPCs", [])):
            pos = npc.get("Position", [0, 0, 0])
            orient = npc.get("Orientation", [0, 0, 0])
            park_pos = npc.get("ParkingPosition", [0, 0, 0])
            park_orient = npc.get("ParkingOrientation", [0, 0, 0])

            x, y, z = float(pos[0]), float(pos[1]), float(pos[2])
            px, py, pz = float(park_pos[0]), float(park_pos[1]), float(park_pos[2])

            lat, lng = game_to_latlng(x, z, self.terrain)
            park_lat, park_lng = game_to_latlng(px, pz, self.terrain)

            npcs_out.append({
                "index": idx,
                "class_name": npc.get("ClassName", ""),
                # NPC position
                "lat": round(lat, 6),
                "lng": round(lng, 6),
                "game_x": round(x, 2),
                "game_y": round(y, 2),
                "game_z": round(z, 2),
                "orientation": round(float(orient[0]), 1),
                # Parking position
                "park_lat": round(park_lat, 6),
                "park_lng": round(park_lng, 6),
                "park_game_x": round(px, 2),
                "park_game_y": round(py, 2),
                "park_game_z": round(pz, 2),
                "park_orientation": round(float(park_orient[0]), 1),
                # Clothes
                "clothes": npc.get("Clothes", []),
            })

        return {"garage_npcs": npcs_out}

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

    async def add_garage_npc(self, data: dict) -> dict | None:
        """Add a new garage NPC."""
        async with self._lock:
            refuse_unreadable(self._unreadable, "TraderPlusGarageConfig.json")
            npcs = self._raw.setdefault("NPCs", [])

            # NPC position
            if "lat" in data and "lng" in data:
                gx, gz = latlng_to_game(data["lat"], data["lng"], self.terrain)
            else:
                gx = data.get("game_x", 0.0)
                gz = data.get("game_z", 0.0)
            gy = data.get("game_y", 0.0)

            # Parking position (defaults to same as NPC position + 5m offset)
            if "park_lat" in data and "park_lng" in data:
                px, pz = latlng_to_game(data["park_lat"], data["park_lng"], self.terrain)
            else:
                px = data.get("park_game_x", gx + 5.0)
                pz = data.get("park_game_z", gz)
            py = data.get("park_game_y", gy)

            npc = {
                "ClassName": data.get("class_name", "SurvivorM_Peter"),
                "Position": [round(gx, 2), round(gy, 2), round(gz, 2)],
                "Orientation": [round(data.get("orientation", 0.0), 1), 0.0, 0.0],
                "ParkingPosition": [round(px, 2), round(py, 2), round(pz, 2)],
                "ParkingOrientation": [round(data.get("park_orientation", 0.0), 1), 0.0, 0.0],
                "Clothes": data.get("clothes", []),
            }
            npcs.append(npc)
            self._save()

        await self._broadcast()
        return npc

    async def update_garage_npc(self, index: int, data: dict) -> dict | None:
        """Update garage NPC fields by index."""
        async with self._lock:
            npcs = self._raw.get("NPCs", [])
            if index < 0 or index >= len(npcs):
                return None

            npc = npcs[index]
            if "class_name" in data:
                npc["ClassName"] = data["class_name"]
            if "clothes" in data:
                npc["Clothes"] = data["clothes"]

            # NPC position
            if "lat" in data and "lng" in data:
                gx, gz = latlng_to_game(data["lat"], data["lng"], self.terrain)
                pos = npc.get("Position", [0, 0, 0])
                gy = keep_or_round(pos[1], data.get("game_y", pos[1]))
                npc["Position"] = [round(gx, 2), gy, round(gz, 2)]
            elif "game_x" in data or "game_y" in data or "game_z" in data:
                pos = npc.get("Position", [0, 0, 0])
                npc["Position"] = [
                    keep_or_round(pos[0], data.get("game_x", pos[0])),
                    keep_or_round(pos[1], data.get("game_y", pos[1])),
                    keep_or_round(pos[2], data.get("game_z", pos[2])),
                ]
            if "orientation" in data:
                npc["Orientation"] = [keep_or_round((npc.get("Orientation") or [0.0])[0], data["orientation"], 1), 0.0, 0.0]

            # Parking position
            if "park_lat" in data and "park_lng" in data:
                px, pz = latlng_to_game(data["park_lat"], data["park_lng"], self.terrain)
                ppos = npc.get("ParkingPosition", [0, 0, 0])
                py = keep_or_round(ppos[1], data.get("park_game_y", ppos[1]))
                npc["ParkingPosition"] = [round(px, 2), py, round(pz, 2)]
            elif "park_game_x" in data or "park_game_y" in data or "park_game_z" in data:
                ppos = npc.get("ParkingPosition", [0, 0, 0])
                npc["ParkingPosition"] = [
                    keep_or_round(ppos[0], data.get("park_game_x", ppos[0])),
                    keep_or_round(ppos[1], data.get("park_game_y", ppos[1])),
                    keep_or_round(ppos[2], data.get("park_game_z", ppos[2])),
                ]
            if "park_orientation" in data:
                npc["ParkingOrientation"] = [keep_or_round((npc.get("ParkingOrientation") or [0.0])[0], data["park_orientation"], 1), 0.0, 0.0]

            self._save()

        await self._broadcast()
        return npc

    async def delete_garage_npc(self, index: int) -> bool:
        """Delete a garage NPC by index."""
        async with self._lock:
            npcs = self._raw.get("NPCs", [])
            if index < 0 or index >= len(npcs):
                return False
            npcs.pop(index)
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
        """Send garage_changed to all WebSocket clients."""
        await self._broadcaster.broadcast({"type": "garage_changed"})
