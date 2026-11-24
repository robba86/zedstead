"""Place names from an official map's own world config.

Zedstead ships no label file (plans/86): a map's place names are its makers' work.
What the admin's server already has is the map's world config: the
``config.bin`` in ``<server>/addons/worlds_<terrain>.pbo`` holds
``CfgWorlds >> <World> >> Names``, one class per named place, with ``name``,
``position[] = {x, z}`` and ``type`` (``NameCity``, ``NameVillage``, ...).
`locations.load_locations` reads it when the admin has put no label file of
their own in place.

Only a terrain on `registry.TERRAIN_LAYER_MAPS` (the official maps) is read,
as for the terrain layer, and only the server's own stock PBO. The file is
untrusted input: the entry is read within `MAX_CONFIG_BYTES`, the raP walk is
`app.shared.rap`'s, and anything unreadable is no names, never an exception.

Not yet checked against a real world PBO (the tests build an invented
config.bin): the layout is the one Bohemia's engine uses for world configs,
taken on trust, so `TYPE_MAP` keeps only the types the map labels already
know and drops the rest (vegetation, flat areas).
"""

from __future__ import annotations

import logging
import re
import struct
import threading
from pathlib import Path

from app.maps.registry import terrain_layer_allowed
from app.shared import rap

log = logging.getLogger(__name__)

#: A world config.bin larger than this is not read.
MAX_CONFIG_BYTES = 32 << 20

#: The world config's place types, folded into `locations.LOCATION_TYPES`.
TYPE_MAP = {
    "namecitycapital": "Capital",
    "namecapital": "Capital",
    "namecity": "City",
    "namevillage": "Village",
    "namelocal": "Local",
    "namemarine": "Marine",
    "hill": "Hill",
    "mount": "Hill",
    "camp": "Camp",
    "ruin": "Ruin",
    "railroadstation": "RailroadStation",
    "strongpointarea": "Military",
    "military": "Military",
    "airport": "Airfield",
    "airfield": "Airfield",
}

_TERRAIN_NAME = re.compile(r"^[a-z0-9_]{1,40}$")

_cache: dict[Path, tuple[tuple[int, int], list[dict]]] = {}
_cache_lock = threading.Lock()


def _stock_pbo(server_dir: Path, terrain: str) -> Path | None:
    want = f"worlds_{terrain}.pbo"
    try:
        return next((p for p in (server_dir / "addons").iterdir() if p.name.lower() == want), None)
    except OSError:
        return None


def _config_bin(pbo: Path) -> bytes | None:
    """The PBO's root ``config.bin``, or None (missing, packed, too large)."""
    from app.maps.terrain_map import _pbo_entries

    entry = next((e for e in _pbo_entries(pbo) if e[0].replace("\\", "/").lower() == "config.bin"), None)
    if entry is None or entry[2] > MAX_CONFIG_BYTES:
        return None
    _name, offset, size = entry
    with open(pbo, "rb") as f:
        f.seek(offset)
        data = f.read(size)
    return data if data.startswith(rap.RAP_MAGIC) else None


def _place_name(class_name: str, name) -> str:
    """The place's own ``name``, else its class name less a ``Settlement_``-like prefix.

    A ``$STR_`` key is a string-table reference this reader cannot resolve.
    """
    if isinstance(name, str) and name.strip() and not name.startswith("$"):
        return name.strip()
    head, _, rest = class_name.partition("_")
    return (rest or head).replace("_", " ")


def parse_names(data: bytes, terrain: str) -> list[dict]:
    """``[{name, x, z, type}]`` from a world config.bin's ``CfgWorlds >> <terrain> >> Names``.

    A raP walk that fails part-way returns what it read before.
    """
    out: list[dict] = []
    try:
        names = rap.find_class(data, ["CfgWorlds", terrain, "Names"])
        if names is None:
            return []
        for kind, class_name, body, _value in rap.class_values(data, names):
            if kind != 0 or body is None:
                continue
            fields = {}
            for f_kind, f_name, _o, f_value in rap.class_values(data, body):
                key = f_name.lower()
                if f_kind == 1 and key in ("name", "type"):
                    fields[key] = f_value
                elif f_kind == 2 and key == "position":
                    fields["position"] = rap.flat_array(data, f_value)
            place_type = TYPE_MAP.get(str(fields.get("type", "")).lower())
            pos = fields.get("position")
            if place_type is None or not pos or len(pos) < 2:
                continue
            x, z = pos[0], pos[1]
            if not all(isinstance(v, (int, float)) and v == v and abs(v) < 1e6 for v in (x, z)):
                continue
            out.append({
                "name": _place_name(class_name, fields.get("name")),
                "x": round(float(x), 2),
                "z": round(float(z), 2),
                "type": place_type,
            })
    except (IndexError, ValueError, struct.error) as exc:
        log.info("The world config's Names for %s stopped being readable: %s", terrain, exc)
    return out


def world_names(server_dir: Path | str | None, terrain: str) -> list[dict]:
    """The official map's place names from the server's own world PBO, cached
    until the PBO changes. Empty for a community map, a missing or encrypted
    world file, or one that cannot be read."""
    terrain = (terrain or "").lower()
    if server_dir is None or not _TERRAIN_NAME.match(terrain) or not terrain_layer_allowed(terrain):
        return []
    pbo = _stock_pbo(Path(server_dir), terrain)
    if pbo is None:
        return []
    try:
        st = pbo.stat()
        stamp = (st.st_mtime_ns, st.st_size)
        with _cache_lock:
            hit = _cache.get(pbo)
        if hit and hit[0] == stamp:
            return hit[1]
        data = _config_bin(pbo)
    except OSError as exc:
        log.warning("Could not read %s: %s", pbo, exc)
        return []
    found = parse_names(data, terrain) if data else []
    with _cache_lock:
        _cache[pbo] = (stamp, found)
    return found
