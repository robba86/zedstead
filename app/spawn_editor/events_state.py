"""Events state manager for the spawn editor.

Handles events.xml editing with CRUD operations and WebSocket broadcasting.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from app.web.editors._help import described_choices
from app.xml.events_xml import (
    EVENT_FLAG_NAMES,
    LIMIT_HELP,
    LIMIT_VALUES,
    NUMERIC_FIELDS,
    POSITION_HELP,
    POSITION_VALUES,
    EventEntry,
    EventsFile,
    add_event_child,
    backup_events_file,
    create_event,
    get_events_path,
    parse_events_file,
    save_events_file,
    set_event_active,
    set_event_flag,
    set_event_limit,
    set_event_numeric,
    set_event_position,
    set_event_secondary,
)

from app.spawn_editor import versioning

if TYPE_CHECKING:
    from app.spawn_editor.state import Broadcaster

log = logging.getLogger(__name__)


class EventsState:
    """Manages events.xml data for browser-based editing."""

    def __init__(self, mission_dir: Path, broadcaster: Broadcaster) -> None:
        self.mission_dir = mission_dir
        self._broadcaster = broadcaster
        self._events: EventsFile | None = None
        self._event_map: dict[str, EventEntry] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load/reload events.xml from disk."""
        ev_path = get_events_path(self.mission_dir)
        if ev_path:
            # Comments kept: every edit saves this tree.
            self._events = parse_events_file(ev_path, preserve_comments=True)
            self._event_map = {e.name: e for e in self._events.entries}
        else:
            self._events = None
            self._event_map = {}

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def _save(self) -> None:
        """Back up and write events.xml, after this session's one snapshot."""
        backup_events_file(self._events.path)
        versioning.snapshot_once(self._events.path)
        save_events_file(self._events, snapshot=False)

    def get_all_events(self, spawn_counts: dict[str, int] | None = None) -> list[dict]:
        """Serialize all events for API response."""
        if not self._events:
            return []
        counts = spawn_counts or {}
        return [
            {
                "name": e.name,
                "nominal": e.nominal,
                "min": e.min,
                "max": e.max,
                "lifetime": e.lifetime,
                "restock": e.restock,
                "saferadius": e.saferadius,
                "distanceradius": e.distanceradius,
                "cleanupradius": e.cleanupradius,
                "active": e.active,
                "flags": e.flags,
                "position": e.position,
                "limit": e.limit,
                "spawn_count": counts.get(e.name, 0),
            }
            for e in self._events.entries
        ]

    def get_entry(self, name: str) -> EventEntry | None:
        """Return the EventEntry for *name*, or None."""
        return self._event_map.get(name)

    def get_event_detail(self, name: str, spawn_count: int = 0) -> dict | None:
        """Get full detail for a single event."""
        entry = self._event_map.get(name)
        if not entry:
            return None
        return {
            "name": entry.name,
            "nominal": entry.nominal,
            "min": entry.min,
            "max": entry.max,
            "lifetime": entry.lifetime,
            "restock": entry.restock,
            "saferadius": entry.saferadius,
            "distanceradius": entry.distanceradius,
            "cleanupradius": entry.cleanupradius,
            "secondary": entry.secondary,
            "flags": dict(entry.flags),
            "position": entry.position,
            "limit": entry.limit,
            # The pickers' options, each with the line that says what it means.
            "position_choices": described_choices(POSITION_VALUES, POSITION_HELP),
            "limit_choices": described_choices(LIMIT_VALUES, LIMIT_HELP),
            "active": entry.active,
            "spawn_count": spawn_count,
            "children": [
                {
                    "type": c.type,
                    "min": c.min,
                    "max": c.max,
                    "lootmin": c.lootmin,
                    "lootmax": c.lootmax,
                }
                for c in entry.children
            ],
        }

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    async def update_event(self, name: str, updates: dict) -> dict | None:
        """Apply partial updates to an event. Returns updated detail or None."""
        async with self._lock:
            entry = self._event_map.get(name)
            if not entry or not self._events:
                return None

            # Numeric fields
            for field_name in NUMERIC_FIELDS:
                if field_name in updates:
                    set_event_numeric(entry, field_name, int(updates[field_name]))

            # Flags
            for flag_name in EVENT_FLAG_NAMES:
                if flag_name in updates:
                    set_event_flag(entry, flag_name, int(updates[flag_name]))

            # Position / limit
            if "position" in updates:
                set_event_position(entry, updates["position"])
            if "limit" in updates:
                set_event_limit(entry, updates["limit"])

            # Active
            if "active" in updates:
                set_event_active(entry, bool(updates["active"]))

            # Secondary
            if "secondary" in updates:
                set_event_secondary(entry, updates["secondary"])

            self._save()

            await self._broadcaster.broadcast({"type": "events_changed"})
            return self.get_event_detail(name)

    def events_with_child(self, classname: str) -> list[str]:
        """The events.xml events with a ``<child type=classname>``, any case.

        The Map Editor's Items tab lists them, so a class that already has an
        event gets positions added to it instead of a second event.
        """
        want = classname.lower()
        return [
            entry.name
            for entry in self._event_map.values()
            if any(c.type.lower() == want for c in entry.children)
        ]

    async def create_new_event(self, name: str, child: str | None = None) -> dict | None:
        """Create a new event with default values. Returns detail or None if exists.

        *child* adds one ``<child type=...>`` with the event's own min and max
        (1), so the event spawns that class (the Items tab's lasting placement).
        """
        async with self._lock:
            if not self._events:
                return None
            if name in self._event_map:
                return None  # already exists

            entry = create_event(self._events, name)
            if child:
                add_event_child(entry, child, entry.min, entry.max, 0, 0)
            self._event_map[name] = entry
            self._save()

            await self._broadcaster.broadcast({"type": "events_changed"})
            return self.get_event_detail(name)

    async def toggle_active(self, name: str) -> dict | None:
        """Toggle event active state. Returns updated detail or None."""
        async with self._lock:
            entry = self._event_map.get(name)
            if not entry or not self._events:
                return None

            set_event_active(entry, not entry.active)
            self._save()

            await self._broadcaster.broadcast({"type": "events_changed"})
            return self.get_event_detail(name)

    # ------------------------------------------------------------------
    # Reload (for file watcher)
    # ------------------------------------------------------------------

    async def reload_and_broadcast(self) -> None:
        """Reload from disk and notify clients."""
        async with self._lock:
            self.load()
            await self._broadcaster.broadcast({"type": "events_changed"})
