"""Shared state manager for the spawn editor.

Loads/caches XML data, handles mutations, broadcasts changes via WebSocket.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.shared.config import DayzConfig
from app.xml.eventspawns_xml import (
    EventSpawnEntry,
    position_attrs,
    EventSpawnsFile,
    add_position,
    create_event_spawn,
    get_eventspawns_path,
    parse_eventspawns_file,
    remove_position,
    save_eventspawns_file,
    update_position,
)
from app.xml.events_xml import (
    EventEntry,
    EventsFile,
    get_events_path,
    parse_events_file,
)
from app.maps.locations import get_terrain_from_mission
from app.modmgr.mods import get_current_mission
from app.spawn_editor.converters import game_to_latlng, latlng_to_game, same_as_shown
from app.spawn_editor import versioning
from app.xml.xml_manual import (
    MANUAL_MOD_PREFIX,
    ManualFileError,
    children_with_owner,
    edit_merged_event,
)

log = logging.getLogger(__name__)

# `_find_entry`'s "any owner": None already means the mission's own.
ANY_OWNER = object()


class OwnedPositionError(Exception):
    """A spawn position the map editor will not write where it is: a mod's,
    or a manual file's that cannot be written back. The message says where to
    change it instead."""


def is_manual_owner(owner: str | None) -> bool:
    """True for the ``@user:<id>`` marker a manual XML file's block carries."""
    return bool(owner) and owner.startswith(MANUAL_MOD_PREFIX)


# ---------------------------------------------------------------------------
# Broadcaster — shared WebSocket channel for all state modules
# ---------------------------------------------------------------------------

# Editor message type -> the type sent on the web app's shared /ws socket.
#
# Every `useWs` consumer in the React app sees every frame, so bare names like
# `full_reload` are namespaced before they leave the editor. `full_reload` also
# loses its payload here: the React client refetches on any ping, so one code
# path beats two, and the `data` blob is the whole cfgeventspawns dump.
MAPEDITOR_CHANNEL = "mapeditor"

_WEB_MESSAGE_TYPES: dict[str, str] = {
    "full_reload": "mapeditor_spawns_changed",
    "events_changed": "mapeditor_events_changed",
    "player_spawns_changed": "mapeditor_player_spawns_changed",
    "traders_changed": "mapeditor_traders_changed",
    "safezones_changed": "mapeditor_safezones_changed",
    "garage_changed": "mapeditor_garage_changed",
    "expansion_safezones_changed": "mapeditor_expansion_safezones_changed",
    "expansion_traderzones_changed": "mapeditor_expansion_traderzones_changed",
    "expansion_spawnpoints_changed": "mapeditor_expansion_spawnpoints_changed",
    "expansion_missions_changed": "mapeditor_expansion_missions_changed",
    "expansion_ai_patrols_changed": "mapeditor_expansion_ai_patrols_changed",
    "expansion_ai_locations_changed": "mapeditor_expansion_ai_locations_changed",
    "expansion_market_spawns_changed": "mapeditor_expansion_market_spawns_changed",
    "expansion_map_markers_changed": "mapeditor_expansion_map_markers_changed",
    "expansion_build_zones_changed": "mapeditor_expansion_build_zones_changed",
    "underground_triggers_changed": "mapeditor_underground_triggers_changed",
    "hazards_changed": "mapeditor_hazards_changed",
    "dynamic_ai_changed": "mapeditor_dynamic_ai_changed",
}


def to_web_message(message: dict) -> dict | None:
    """Namespaced, payload-free form of an editor broadcast, or None to drop."""
    web_type = _WEB_MESSAGE_TYPES.get(message.get("type", ""))
    if web_type is None:
        return None
    return {"type": web_type}


class Broadcaster:
    """Fans a state mutation out to the web app's ``WsHub``.

    Every state class only ever calls ``broadcast``; where that lands is this
    class's business. (There were six when this was written and there are
    fourteen now — `state_container.MapEditorState`'s fields are the list, and
    a count in prose here would just go stale again.) Until the React port shipped there were two
    destinations (a socket set of its own for the vanilla editor, plus the
    hub) — there is one frontend now, so the socket set is gone and the
    editor no longer owns a WebSocket endpoint at all.
    """

    def __init__(self, hub) -> None:
        self._hub = hub

    async def broadcast(self, message: dict) -> None:
        web_message = to_web_message(message)
        if web_message is not None:
            await self._hub.broadcast(web_message, channel=MAPEDITOR_CHANNEL)


# ---------------------------------------------------------------------------
# SpawnEditorState — eventspawns CRUD + event metadata for display
# ---------------------------------------------------------------------------

class SpawnEditorState:
    """Manages loaded eventspawn data and provides spawn CRUD."""

    def __init__(self, config: DayzConfig, broadcaster: Broadcaster) -> None:
        self.config = config
        self.terrain: str = ""
        self.mission_dir: Path = Path()
        self._eventspawns: EventSpawnsFile | None = None
        self._events: EventsFile | None = None
        self._event_map: dict[str, EventEntry] = {}
        self._broadcaster = broadcaster
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load/reload XML files from disk."""
        mission = get_current_mission(self.config.server_cfg_path)
        if not mission:
            log.warning("No mission detected from serverDZ.cfg")
            return

        self.mission_dir = self.config.server_dir_path / "mpmissions" / mission
        if not self.mission_dir.is_dir():
            log.warning("Mission dir not found: %s", self.mission_dir)
            return

        self.terrain = get_terrain_from_mission(self.mission_dir)

        # Parse eventspawns
        es_path = get_eventspawns_path(self.mission_dir)
        self._eventspawns = parse_eventspawns_file(es_path) if es_path else None

        # Parse events (for metadata: nominal, active, radii, etc.)
        ev_path = get_events_path(self.mission_dir)
        if ev_path:
            self._events = parse_events_file(ev_path)
            self._event_map = {e.name: e for e in self._events.entries}
        else:
            self._events = None
            self._event_map = {}

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def get_spawns_json(self) -> dict:
        """Serialize all spawn data for API response.

        Each event and position says who owns it (``owner``): None for the
        mission's own, ``@user:<id>`` for a manual XML file's block, or a mod's
        name. ``entry`` numbers the events that share a name *and* an owner, in
        file order, so a write can name the one that was dragged.
        """
        events_out: list[dict] = []

        if self._eventspawns:
            for entry, owner, n in self._entries_with_owner():
                ev_meta = self._event_map.get(entry.name)
                positions = []
                for idx, pos in enumerate(entry.positions):
                    out = self._position_json(pos, idx)
                    out["owner"] = self._position_owner(entry, owner, pos)
                    positions.append(out)

                event_data: dict = {
                    "name": entry.name,
                    "owner": owner,
                    "entry": n,
                    "position_count": len(entry.positions),
                    "positions": positions,
                }

                # Attach event metadata if available
                if ev_meta:
                    event_data["active"] = ev_meta.active
                    event_data["nominal"] = ev_meta.nominal
                    event_data["min"] = ev_meta.min
                    event_data["max"] = ev_meta.max
                    event_data["lifetime"] = ev_meta.lifetime
                    event_data["restock"] = ev_meta.restock
                    event_data["saferadius"] = ev_meta.saferadius
                    event_data["distanceradius"] = ev_meta.distanceradius
                    event_data["cleanupradius"] = ev_meta.cleanupradius

                events_out.append(event_data)

        return {"terrain": self.terrain, "events": events_out}

    def _position_json(self, pos, index: int, moved_from=None) -> dict:
        """One position as the API sends it. ``height`` is the file's ``y``
        (None when it has none); ``height_kept`` is set on a move of a
        position that has one, since the map has no heights to give it."""
        lat, lng = game_to_latlng(pos.x, pos.z, self.terrain)
        out = {
            "lat": round(lat, 6),
            "lng": round(lng, 6),
            "angle": pos.a,
            "game_x": round(pos.x, 2),
            "game_z": round(pos.z, 2),
            "height": pos.y,
            "index": index,
        }
        if moved_from is not None and pos.y is not None and moved_from != (pos.x, pos.z):
            out["height_kept"] = True
        return out

    def get_spawn_counts(self) -> dict[str, int]:
        """Get position count per event name (for cross-referencing)."""
        counts: dict[str, int] = {}
        if self._eventspawns:
            for entry in self._eventspawns.entries:
                counts[entry.name] = len(entry.positions)
        return counts

    # ------------------------------------------------------------------
    # Ownership
    # ------------------------------------------------------------------

    def _root_owners(self) -> dict:
        """Each root ``<event>`` element: ``(owner, place in the owner's block)``.

        The merge appends a mod's (or manual file's) whole ``<event>`` at the
        root between START/END MOD markers; `xml_manual.children_with_owner`
        is the one reader of those markers. *place* counts every element of
        the block, in the order the merge took them out of the file."""
        out: dict = {}
        if not self._eventspawns:
            return out
        seen: dict[str | None, int] = {}
        for owner, child in children_with_owner(self._eventspawns.tree.getroot()):
            place = seen.get(owner, 0)
            seen[owner] = place + 1
            out[child] = (owner, place)
        return out

    def _entries_with_owner(self):
        """``(entry, owner, n)`` for every event, in file order; *n* counts the
        entries with the same name and owner before it."""
        roots = self._root_owners()
        seen: dict[tuple, int] = {}
        for entry in self._eventspawns.entries if self._eventspawns else []:
            owner = roots.get(entry.element, (None, 0))[0]
            n = seen.get((entry.name, owner), 0)
            seen[(entry.name, owner)] = n + 1
            yield entry, owner, n

    @staticmethod
    def _position_owner(entry: EventSpawnEntry, owner: str | None, pos) -> str | None:
        """Who owns one position: the event's owner, or, inside the mission's
        own event, a block written around positions."""
        if owner is not None:
            return owner
        for inner, child in children_with_owner(entry.element):
            if child is pos.element:
                return inner
        return None

    def _entry_owner(self, entry: EventSpawnEntry) -> str | None:
        return self._root_owners().get(entry.element, (None, 0))[0]

    def _pos_owner(self, entry: EventSpawnEntry, pos_index: int) -> str | None:
        return self._position_owner(entry, self._entry_owner(entry), entry.positions[pos_index])

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def _find_entry(
        self, event_name: str, owner=ANY_OWNER, n: int = 0,
    ) -> EventSpawnEntry | None:
        """Find an eventspawn entry by name.

        With *owner* (None for the mission's own), the *n*-th entry of that
        name and owner, which is the one the map drew; without it, the first
        entry of that name, whoever owns it."""
        if not self._eventspawns:
            return None
        if owner is ANY_OWNER:
            return next((e for e in self._eventspawns.entries if e.name == event_name), None)
        for entry, entry_owner, entry_n in self._entries_with_owner():
            if entry.name == event_name and entry_owner == owner and entry_n == n:
                return entry
        return None

    def _entry_for_add(self, event_name: str, owner, n: int) -> EventSpawnEntry | None:
        """The entry a new position goes into. Without an owner: the mission's
        own entry first, then a manual file's, then a mod's (which refuses)."""
        if owner is not ANY_OWNER:
            return self._find_entry(event_name, owner, n)
        candidates = [
            (entry, entry_owner) for entry, entry_owner, _ in self._entries_with_owner()
            if entry.name == event_name
        ]
        for wanted in (lambda o: o is None, is_manual_owner, lambda o: True):
            for entry, entry_owner in candidates:
                if wanted(entry_owner):
                    return entry
        return None

    def has_event(self, event_name: str) -> bool:
        """True when cfgeventspawns.xml already holds an entry for *event_name*."""
        return self._find_entry(event_name) is not None

    def _save(self) -> None:
        """Write eventspawns XML to disk, after this session's one snapshot."""
        if self._eventspawns:
            versioning.snapshot_once(self._eventspawns.path)
            save_eventspawns_file(self._eventspawns, snapshot=False)

    def _write(self, entry: EventSpawnEntry, owner: str | None, edit, copy: str | None = None):
        """Apply *edit* to the file that really holds what is being changed.

        *owner* is the owner of the position (or, for an add, of the event).
        The mission's own positions are changed in cfgeventspawns.xml. A
        manual file's are changed in the manual file, which is then applied
        again (`xml_manual.edit_merged_event`), so a re-configure keeps the
        change. A mod's are refused (the next merge would put them back)
        unless *copy* is given: then the mod's file is first copied into a
        manual file (`_copy_into_manual_file`; *copy* is that file, or "" for
        the one the merge record names) and the change goes into the copy.

        *edit* takes an `EventSpawnEntry` and returns ``(result, changed)``.
        Returns ``(result, changed, entry)``; after a manual-file write the
        state is reloaded and *entry* is the reloaded one (None if gone).
        """
        if owner is None:
            result, changed = edit(entry)
            if changed:
                self._save()
            return result, changed, entry

        if not is_manual_owner(owner):
            if copy is None:
                raise OwnedPositionError(
                    f"This position of {entry.name} comes from {owner}. A re-configure "
                    f"puts it back from the mod, so the map editor does not change it "
                    f"there. Copy {owner}'s cfgeventspawns.xml into a manual XML file "
                    f"first (the map editor offers it, and so does Take over on "
                    f"Configs → Custom config); the change then goes into "
                    f"your copy."
                )
            entry, owner = self._copy_into_manual_file(entry, owner, copy)

        entry_owner, place = self._root_owners().get(entry.element, (None, 0))
        if entry_owner != owner:
            raise OwnedPositionError(
                f"This position of {entry.name} is in a block of {owner} inside the "
                f"mission's own event, which the map editor cannot write back. "
                f"Edit that manual XML file on Configs → Custom config."
            )
        if not self.mission_dir.name:
            raise OwnedPositionError("No mission is loaded.")
        n = next((k for e, _, k in self._entries_with_owner() if e is entry), 0)
        expected = position_attrs(entry)
        changed_box: list[bool] = []

        def in_file(file_entry: EventSpawnEntry):
            result, changed = edit(file_entry)
            changed_box.append(changed)
            return result

        try:
            result = edit_merged_event(
                self.config.server_dir_path,
                self.mission_dir.name,
                owner[len(MANUAL_MOD_PREFIX):],
                place,
                entry.name,
                expected,
                in_file,
                manual_dir=self.config.paths.custom_xml_dir,
                snapshot_once=versioning.snapshot_once,
            )
        except ManualFileError as exc:
            raise OwnedPositionError(str(exc)) from exc
        self.load()
        changed = bool(changed_box and changed_box[0])
        return result, changed, self._find_entry(entry.name, owner, n)

    def copy_source(self, mod: str) -> dict:
        """The mod file "Copy into a manual file" would copy for *mod*'s
        positions: ``{"source": <file or None>, "sources": [...]}`` from the
        merge record (`mod_xml_takeover.merged_sources`). ``source`` is None
        when the record does not name exactly one file; the page then asks."""
        from app.ops.mod_xml_takeover import merged_sources

        return merged_sources(self.config, mod, "cfgeventspawns")

    def _copy_into_manual_file(
        self, entry: EventSpawnEntry, mod: str, source: str,
    ) -> tuple[EventSpawnEntry, str]:
        """Take *mod*'s cfgeventspawns file over (the Mod XML page's Take over,
        `mod_xml_takeover.take_over_mod_source`) and return the same event in
        the new manual file's block, with that block's owner.

        *source* is the mod file, server-dir relative; "" takes the one the
        merge record names. The file has to hold this event with the same
        positions as the mission, or nothing is copied."""
        from app.ops.mod_xml_takeover import take_over_mod_source
        from app.xml.xml_manual import file_holds_event, normalize_source
        from app.xml.xml_report import resolve_mod_source

        if self._entry_owner(entry) != mod:
            raise OwnedPositionError(
                f"This position of {entry.name} is in a block of {mod} inside the "
                f"mission's own event, which the map editor cannot copy."
            )
        if not source:
            found = self.copy_source(mod)
            source = found["source"]
            if not source:
                listed = ", ".join(found["sources"]) or "none found"
                raise OwnedPositionError(
                    f"The merge record does not name one cfgeventspawns file of {mod} "
                    f"({listed}). Choose the file to copy."
                )
        server_dir = self.config.server_dir_path
        path = resolve_mod_source(server_dir, source)
        key = normalize_source(path.relative_to(server_dir.resolve())) if path else ""
        if not key.startswith(f"{mod}/"):
            raise OwnedPositionError(f"{source!r} is not a file of {mod}.")
        expected = position_attrs(entry)
        if not file_holds_event(path, entry.name, expected):
            raise OwnedPositionError(
                f"{key} does not hold {entry.name} with the positions the mission has, "
                f"so it is not the file these came from. Choose another file, or "
                f"re-apply {mod}'s XML first."
            )
        try:
            link = take_over_mod_source(self.config, key)
        except ManualFileError as exc:
            raise OwnedPositionError(str(exc)) from exc
        if not link.get("applied"):
            raise OwnedPositionError(
                f"{key} was copied into the manual XML file {link['id']!r}, but it "
                f"could not be applied ({link.get('reason', 'nothing to apply')}), "
                f"so nothing was moved."
            )
        self.load()
        owner = MANUAL_MOD_PREFIX + link["id"]
        for found_entry, found_owner, _ in self._entries_with_owner():
            if (
                found_owner == owner and found_entry.name == entry.name
                and position_attrs(found_entry) == expected
            ):
                return found_entry, owner
        raise OwnedPositionError(
            f"{key} was copied into the manual XML file {link['id']!r}, but "
            f"{entry.name} is not in the mission as it was, so nothing was moved."
        )

    async def _broadcast_spawns(self) -> None:
        await self._broadcaster.broadcast({
            "type": "full_reload",
            "data": self.get_spawns_json(),
        })

    async def add_spawn_position(
        self, event_name: str, lat: float, lng: float, angle: float = -1,
        owner=ANY_OWNER, n: int = 0, copy: str | None = None,
    ) -> dict | None:
        """Add a new spawn position. Returns new position data or None."""
        async with self._lock:
            entry = self._entry_for_add(event_name, owner, n)
            if not entry:
                return None

            x, z = latlng_to_game(lat, lng, self.terrain)

            def edit(target: EventSpawnEntry):
                add_position(target, x, z, angle)
                return len(target.positions) - 1, True

            index, _, entry = self._write(entry, self._entry_owner(entry), edit, copy)
            if entry is None or index >= len(entry.positions):
                return None
            result = self._position_json(entry.positions[index], index)

            await self._broadcast_spawns()
            return result

    async def update_spawn_position(
        self, event_name: str, pos_index: int, lat: float, lng: float,
        angle: float | None = None, owner=ANY_OWNER, n: int = 0,
        copy: str | None = None,
    ) -> dict | None:
        """Update an existing spawn position. Returns updated data or None.

        ``angle=None`` keeps the position's current angle (-1 is DayZ's
        "random", a value, not "unchanged")."""
        async with self._lock:
            entry = self._find_entry(event_name, owner, n)
            if not entry or pos_index < 0 or pos_index >= len(entry.positions):
                return None

            x, z = latlng_to_game(lat, lng, self.terrain)
            was = (entry.positions[pos_index].x, entry.positions[pos_index].z)

            def edit(target: EventSpawnEntry):
                # The height (y) stays as it is: the map has none to give.
                update_position(target.positions[pos_index], x, z, angle)
                return None, True

            _, _, entry = self._write(entry, self._pos_owner(entry, pos_index), edit, copy)
            if entry is None or pos_index >= len(entry.positions):
                return None
            result = self._position_json(entry.positions[pos_index], pos_index, moved_from=was)

            await self._broadcast_spawns()
            return result

    async def update_spawn_position_game(
        self, event_name: str, pos_index: int, x: float, z: float,
        angle: float | None = None, owner=ANY_OWNER, n: int = 0,
        copy: str | None = None,
    ) -> dict | None:
        """Update a spawn position using game coordinates directly.
        ``angle=None`` keeps the current angle."""
        async with self._lock:
            entry = self._find_entry(event_name, owner, n)
            if not entry or pos_index < 0 or pos_index >= len(entry.positions):
                return None

            pos = entry.positions[pos_index]
            # The popup sends back the 2-decimal value this payload showed for
            # a coordinate nobody edited; keep the file's own value for it, and
            # write nothing when nothing changed (test pass 1, F10).
            new_x = pos.x if same_as_shown(pos.x, x) else x
            new_z = pos.z if same_as_shown(pos.z, z) else z
            was = (pos.x, pos.z)

            def edit(target: EventSpawnEntry):
                return None, update_position(target.positions[pos_index], new_x, new_z, angle)

            pos_owner = self._pos_owner(entry, pos_index)
            _, changed, entry = self._write(entry, pos_owner, edit, copy)
            # A copy changed the mission even when the values did not.
            changed = changed or (pos_owner is not None and not is_manual_owner(pos_owner))
            if entry is None or pos_index >= len(entry.positions):
                return None
            result = self._position_json(entry.positions[pos_index], pos_index, moved_from=was)

            if changed:
                await self._broadcast_spawns()
            return result

    async def create_event_spawn_entry(
        self, name: str, event_entry: EventEntry | None = None,
    ) -> bool:
        """Create a new empty event entry in cfgeventspawns.xml.

        If *event_entry* is provided it is registered in the event map so
        the new event shows metadata immediately.  Returns True if created.
        """
        async with self._lock:
            if not self._eventspawns:
                return False
            # Check if already exists
            if self._find_entry(name):
                return False

            create_event_spawn(self._eventspawns, name)
            if event_entry is not None:
                self._event_map[name] = event_entry
            self._save()

            await self._broadcast_spawns()
            return True

    async def delete_spawn_position(
        self, event_name: str, pos_index: int, owner=ANY_OWNER, n: int = 0,
        copy: str | None = None,
    ) -> bool:
        """Delete a spawn position. Returns True if deleted."""
        async with self._lock:
            entry = self._find_entry(event_name, owner, n)
            if not entry or pos_index < 0 or pos_index >= len(entry.positions):
                return False

            def edit(target: EventSpawnEntry):
                remove_position(target, target.positions[pos_index])
                return None, True

            self._write(entry, self._pos_owner(entry, pos_index), edit, copy)

            await self._broadcast_spawns()
            return True

    # ------------------------------------------------------------------
    # Reload (for file watcher)
    # ------------------------------------------------------------------

    async def reload_and_broadcast(self) -> None:
        """Reload from disk and notify all WebSocket clients."""
        async with self._lock:
            self.load()
            await self._broadcaster.broadcast({
                "type": "full_reload",
                "data": self.get_spawns_json(),
            })
