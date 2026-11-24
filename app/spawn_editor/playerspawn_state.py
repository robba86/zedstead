"""Player spawn points state for the spawn editor.

Loads cfgPlayerSpawnPoints.xml and provides full CRUD for map editing.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from app.xml.playerspawnpoints_xml import (
    GENERATOR_PARAM_DESCRIPTIONS,
    GENERATOR_PARAM_FIELDS,
    GROUP_PARAM_DESCRIPTIONS,
    GROUP_PARAM_FIELDS,
    SECTION_DESCRIPTIONS,
    SPAWN_PARAM_DESCRIPTIONS,
    SPAWN_PARAM_FIELDS,
    PlayerSpawnPointsFile,
    add_group as xml_add_group,
    add_pos_to_group,
    backup_spawnpoints_file,
    get_spawnpoints_path,
    parse_spawnpoints_file,
    remove_group as xml_remove_group,
    remove_pos_from_group,
    save_spawnpoints_file,
    set_generator_param,
    set_group_param,
    set_spawn_param,
    update_pos_in_group,
)
from app.web.editors.playerspawnpoints import check_grid, param_text
from app.spawn_editor.converters import game_to_latlng, keep_or_round, latlng_to_game
from app.spawn_editor import versioning

if TYPE_CHECKING:
    from app.spawn_editor.state import Broadcaster

log = logging.getLogger(__name__)

# Param type → (setter, field list, descriptions)
_PARAM_MAP = {
    "spawn": (set_spawn_param, SPAWN_PARAM_FIELDS, SPAWN_PARAM_DESCRIPTIONS),
    "generator": (set_generator_param, GENERATOR_PARAM_FIELDS, GENERATOR_PARAM_DESCRIPTIONS),
    "group": (set_group_param, GROUP_PARAM_FIELDS, GROUP_PARAM_DESCRIPTIONS),
}


def _coord_text(current: str, new: float) -> str:
    """The text to store for a coordinate a request sets to *new*.

    The popup sends back the 2-decimal value it showed for a coordinate
    nobody edited, so that keeps the file's own text.
    """
    try:
        value = float(current)
    except ValueError:
        value = None
    kept = keep_or_round(value, new)
    return current if kept is value else f"{kept:.6f}"


class PlayerSpawnState:
    """Manages player spawn points data for map display and editing."""

    def __init__(self, mission_dir: Path, terrain: str, broadcaster: Broadcaster) -> None:
        self.mission_dir = mission_dir
        self.terrain = terrain
        self._broadcaster = broadcaster
        self._lock = asyncio.Lock()
        self._spawnpoints: PlayerSpawnPointsFile | None = None

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load cfgPlayerSpawnPoints.xml from disk."""
        path = get_spawnpoints_path(self.mission_dir)
        if path:
            self._spawnpoints = parse_spawnpoints_file(path)
            total_groups = sum(len(s.groups) for s in self._spawnpoints.sections.values())
            total_positions = sum(
                len(g.positions)
                for s in self._spawnpoints.sections.values()
                for g in s.groups
            )
            log.warning(
                "Player spawns loaded: %d sections, %d groups (%d positions) from %s",
                len(self._spawnpoints.sections), total_groups, total_positions, path,
            )
        else:
            self._spawnpoints = None
            log.warning("cfgPlayerSpawnPoints.xml not found in %s", self.mission_dir)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def get_json(self) -> list[dict]:
        """Serialize player spawn data for API response (rich format with params)."""
        if not self._spawnpoints:
            return []

        result: list[dict] = []

        for section in self._spawnpoints.sections.values():
            groups_out: list[dict] = []
            for gi, group in enumerate(section.groups):
                positions: list[dict] = []
                for pi, pos in enumerate(group.positions):
                    try:
                        x, z = float(pos.x), float(pos.z)
                    except ValueError:
                        continue
                    lat, lng = game_to_latlng(x, z, self.terrain)
                    positions.append({
                        "index": pi,
                        "lat": round(lat, 6),
                        "lng": round(lng, 6),
                        "game_x": round(x, 2),
                        "game_z": round(z, 2),
                    })
                groups_out.append({
                    "index": gi,
                    "name": group.name,
                    "positions": positions,
                })

            # Serialize params
            sp = section.spawn_params
            gp = section.generator_params
            grp = section.group_params

            result.append({
                "section": section.name,
                "description": SECTION_DESCRIPTIONS.get(section.name, ""),
                "groups": groups_out,
                "spawn_params": {f: getattr(sp, f) for f in SPAWN_PARAM_FIELDS},
                "generator_params": {f: getattr(gp, f) for f in GENERATOR_PARAM_FIELDS},
                "group_params": {f: getattr(grp, f) for f in GROUP_PARAM_FIELDS},
            })

        return result

    def get_param_metadata(self) -> dict:
        """Return field lists and descriptions for frontend form building."""
        return {
            "spawn": {
                "fields": SPAWN_PARAM_FIELDS,
                "descriptions": SPAWN_PARAM_DESCRIPTIONS,
            },
            "generator": {
                "fields": GENERATOR_PARAM_FIELDS,
                "descriptions": GENERATOR_PARAM_DESCRIPTIONS,
            },
            "group": {
                "fields": GROUP_PARAM_FIELDS,
                "descriptions": GROUP_PARAM_DESCRIPTIONS,
            },
        }

    # ------------------------------------------------------------------
    # Save helper
    # ------------------------------------------------------------------

    def _save(self) -> None:
        """Backup and write XML to disk."""
        if not self._spawnpoints:
            return
        backup_spawnpoints_file(self._spawnpoints.path)
        versioning.snapshot_once(self._spawnpoints.path)
        save_spawnpoints_file(self._spawnpoints, snapshot=False)

    # ------------------------------------------------------------------
    # Position CRUD
    # ------------------------------------------------------------------

    async def add_position(
        self, section_name: str, group_idx: int, x: float, z: float,
    ) -> dict | None:
        """Add a position to a group using game coordinates."""
        async with self._lock:
            section = self._get_section(section_name)
            if not section or group_idx < 0 or group_idx >= len(section.groups):
                return None
            group = section.groups[group_idx]
            xs, zs = f"{x:.6f}", f"{z:.6f}"
            pos = add_pos_to_group(group, xs, zs)
            self._save()

        await self._broadcast()
        lat, lng = game_to_latlng(x, z, self.terrain)
        return {
            "index": len(group.positions) - 1,
            "lat": round(lat, 6),
            "lng": round(lng, 6),
            "game_x": round(x, 2),
            "game_z": round(z, 2),
        }

    async def update_position(
        self, section_name: str, group_idx: int, pos_idx: int, x: float, z: float,
    ) -> dict | None:
        """Update a position's game coordinates."""
        async with self._lock:
            pos = self._get_pos(section_name, group_idx, pos_idx)
            if not pos:
                return None
            update_pos_in_group(pos, _coord_text(pos.x, x), _coord_text(pos.z, z))
            self._save()

        await self._broadcast()
        lat, lng = game_to_latlng(x, z, self.terrain)
        return {
            "index": pos_idx,
            "lat": round(lat, 6),
            "lng": round(lng, 6),
            "game_x": round(x, 2),
            "game_z": round(z, 2),
        }

    async def update_position_latlng(
        self, section_name: str, group_idx: int, pos_idx: int,
        lat: float, lng: float,
    ) -> dict | None:
        """Update a position using display lat/lng (converts to game coords)."""
        x, z = latlng_to_game(lat, lng, self.terrain)
        return await self.update_position(section_name, group_idx, pos_idx, x, z)

    async def delete_position(
        self, section_name: str, group_idx: int, pos_idx: int,
    ) -> bool:
        """Delete a position from a group."""
        async with self._lock:
            section = self._get_section(section_name)
            if not section or group_idx < 0 or group_idx >= len(section.groups):
                return False
            group = section.groups[group_idx]
            if pos_idx < 0 or pos_idx >= len(group.positions):
                return False
            remove_pos_from_group(group, group.positions[pos_idx])
            self._save()

        await self._broadcast()
        return True

    # ------------------------------------------------------------------
    # Group CRUD
    # ------------------------------------------------------------------

    async def add_group(self, section_name: str, name: str) -> dict | None:
        """Add a new group to a section."""
        async with self._lock:
            section = self._get_section(section_name)
            if not section:
                return None
            group = xml_add_group(section, name)
            self._save()

        await self._broadcast()
        return {
            "index": len(section.groups) - 1,
            "name": group.name,
            "positions": [],
        }

    async def remove_group(self, section_name: str, group_idx: int) -> bool:
        """Remove a group from a section."""
        async with self._lock:
            section = self._get_section(section_name)
            if not section or group_idx < 0 or group_idx >= len(section.groups):
                return False
            xml_remove_group(section, section.groups[group_idx])
            self._save()

        await self._broadcast()
        return True

    # ------------------------------------------------------------------
    # Section params
    # ------------------------------------------------------------------

    async def update_params(
        self, section_name: str, param_type: str, updates: dict[str, str],
    ) -> dict | None:
        """Update section parameters (spawn/generator/group).

        Each value goes through the Configs table's own rules (`param_text`):
        one equal to the file's is not written, an empty one never adds a
        param the file lacks, and one DayZ cannot read raises ``ValueError``
        (the route's 400) before anything is changed. Nothing changed: no
        save and no version."""
        if param_type not in _PARAM_MAP:
            return None

        async with self._lock:
            section = self._get_section(section_name)
            if not section:
                return None

            setter, fields, _ = _PARAM_MAP[param_type]
            block = f"{param_type}_params"
            writes: dict[str, str] = {}
            if section.element is not None:
                for field_name, value in updates.items():
                    if field_name not in fields:
                        continue
                    text = param_text(
                        section.element, f"{block}.{field_name}", value,
                        where=f"{section_name} {field_name}",
                    )
                    if text is not None:
                        writes[field_name] = text
                if block == "generator_params":
                    check_grid(section.element, writes)
            for field_name, text in writes.items():
                setter(section, field_name, text)
            if writes:
                self._save()

        if writes:
            await self._broadcast()
        # Return updated params
        params_obj = {
            "spawn": section.spawn_params,
            "generator": section.generator_params,
            "group": section.group_params,
        }[param_type]
        _, field_list, _ = _PARAM_MAP[param_type]
        return {f: getattr(params_obj, f) for f in field_list}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_section(self, name: str):
        if not self._spawnpoints:
            return None
        return self._spawnpoints.sections.get(name)

    def _get_pos(self, section_name: str, group_idx: int, pos_idx: int):
        section = self._get_section(section_name)
        if not section or group_idx < 0 or group_idx >= len(section.groups):
            return None
        group = section.groups[group_idx]
        if pos_idx < 0 or pos_idx >= len(group.positions):
            return None
        return group.positions[pos_idx]

    # ------------------------------------------------------------------
    # Reload (for file watcher)
    # ------------------------------------------------------------------

    async def reload_and_broadcast(self) -> None:
        """Reload from disk and notify clients."""
        async with self._lock:
            self.load()
            await self._broadcast()

    async def _broadcast(self) -> None:
        """Send player_spawns_changed to all WebSocket clients."""
        await self._broadcaster.broadcast({"type": "player_spawns_changed"})
