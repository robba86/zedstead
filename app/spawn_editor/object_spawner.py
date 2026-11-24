"""The Map Editor's Object Spawner file: objects placed for good from the Items tab.

DayZ's Object Spawner reads the JSON files that `cfggameplay.json` lists in
`WorldsData.objectSpawnersArr`, paths relative to the mission folder, and
spawns each object at every start. It is never respawned or cleaned up by the
Central Economy. Each file is ``{"Objects": [{"name", "pos": [x, y, z],
"ypr": [yaw, pitch, roll]}, ...]}``. DayZ reads the list only while
serverDZ.cfg has ``enableCfgGameplayFile = 1``, so the payload says when it
is off.

Zedstead writes one file of its own, `OWN_FILE` in the mission root, and lists it
in `objectSpawnersArr` on the first placement. Another file the admin lists is
never read or written here. A write patches the document it read
(`versioning.write_json`), so an entry keeps keys Zedstead does not write
(``scale``, ``enableCEPersistency``, ``customString``). An entry is addressed
by its index, with the name it showed as ``expect``, so a stale list answers
409 instead of changing another object.

The map is flat, so Y comes from the terrain's heights
(`terrain_map.height_at`), or from the caller.

A disabled object moves, whole, to `DISABLED_FILE` beside it, which is never
listed: DayZ has no off switch for one entry, so a file it does not read is
the only way to keep an object without spawning it. Enable moves it back.
Every call names its file with ``disabled``.
"""

from __future__ import annotations

import asyncio
import math
import re
from pathlib import Path
from typing import Any

from app.parsers.cfggameplay import get_cfggameplay_path
from app.shared.json_utils import load_json_preserving
from app.spawn_editor import versioning
from app.spawn_editor.converters import game_to_latlng

#: Zedstead's own Object Spawner file, relative to the mission folder.
OWN_FILE = "zsm-objects.json"

#: Disabled objects, in the same format, never listed in cfggameplay.json.
DISABLED_FILE = "zsm-objects-disabled.json"

#: A class name as DayZ writes one; the same rule as the spawn test's.
_CLASSNAME_RE = re.compile(r"^[A-Za-z0-9_]+$")

#: One write at a time: two placements read the same list otherwise.
_LOCK = asyncio.Lock()


class ObjectSpawnerError(Exception):
    """A refused Object Spawner write, with the HTTP status that says why."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def own_path(mission_dir: Path, disabled: bool = False) -> Path:
    """Zedstead's Object Spawner file in *mission_dir*, or its disabled objects' file."""
    return mission_dir / (DISABLED_FILE if disabled else OWN_FILE)


def _read_doc(path: Path) -> dict:
    """The file as read, or an empty document when it does not exist yet.

    A file that exists and cannot be read is refused, never replaced.
    """
    if not path.is_file():
        return {"Objects": []}
    try:
        doc = load_json_preserving(path)
    except (OSError, ValueError) as e:
        raise ObjectSpawnerError(409, f"{path.name} cannot be read ({e}). Fix or remove it first.")
    if not isinstance(doc, dict) or not isinstance(doc.get("Objects", []), list):
        raise ObjectSpawnerError(409, f"{path.name} is not an Object Spawner file. Fix or remove it first.")
    doc.setdefault("Objects", [])
    return doc


def _listed(mission_dir: Path) -> bool | None:
    """Whether cfggameplay.json lists `OWN_FILE`; None when it cannot be read."""
    path = get_cfggameplay_path(mission_dir)
    try:
        doc = load_json_preserving(path)
    except (OSError, ValueError):
        return None
    worlds = doc.get("WorldsData") if isinstance(doc, dict) else None
    paths = worlds.get("objectSpawnersArr") if isinstance(worlds, dict) else None
    return isinstance(paths, list) and any(_same_path(p, OWN_FILE) for p in paths)


def _same_path(listed: Any, own: str) -> bool:
    """A listed path names *own*: slashes either way, any case, no leading ./."""
    if not isinstance(listed, str):
        return False
    norm = listed.replace("\\", "/").strip().removeprefix("./").lower()
    return norm == own.lower()


def _list_own_file(mission_dir: Path) -> None:
    """Add `OWN_FILE` to cfggameplay.json's `objectSpawnersArr` when it is missing."""
    path = get_cfggameplay_path(mission_dir)
    if not path.is_file():
        raise ObjectSpawnerError(
            409,
            "This mission has no cfggameplay.json, so DayZ reads no Object Spawner file. "
            "Restore the stock file first.",
        )
    try:
        doc = load_json_preserving(path)
    except (OSError, ValueError) as e:
        raise ObjectSpawnerError(409, f"cfggameplay.json cannot be read ({e}). Fix it first.")
    if not isinstance(doc, dict):
        raise ObjectSpawnerError(409, "cfggameplay.json is not a JSON object. Fix it first.")
    worlds = doc.setdefault("WorldsData", {})
    if not isinstance(worlds, dict):
        raise ObjectSpawnerError(409, "cfggameplay.json's WorldsData is not an object. Fix it first.")
    paths = worlds.setdefault("objectSpawnersArr", [])
    if not isinstance(paths, list):
        raise ObjectSpawnerError(409, "cfggameplay.json's objectSpawnersArr is not a list. Fix it first.")
    if any(_same_path(p, OWN_FILE) for p in paths):
        return
    paths.append(OWN_FILE)
    versioning.write_json(path, doc)


def gameplay_file_on(server_cfg: Path) -> bool | None:
    """serverDZ.cfg's ``enableCfgGameplayFile`` is 1; None when it cannot be read."""
    from app.parsers.servercfg import read_cfg_value

    try:
        value = read_cfg_value(server_cfg, "enableCfgGameplayFile")
    except OSError:
        return None
    if value is None:
        return False
    return value.strip().strip('"') == "1"


def _number(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _entry_json(index: int, entry: Any, terrain: str, disabled: bool = False) -> dict | None:
    """One object as the editor shows it, or None for an entry it cannot place."""
    if not isinstance(entry, dict) or not isinstance(entry.get("pos"), list) or len(entry["pos"]) < 3:
        return None
    x, y, z = (_number(v) for v in entry["pos"][:3])
    ypr = entry.get("ypr") if isinstance(entry.get("ypr"), list) else []
    lat, lng = game_to_latlng(x, z, terrain)
    return {
        "index": index,
        "disabled": disabled,
        "name": str(entry.get("name", "")),
        "x": x,
        "y": y,
        "z": z,
        "yaw": _number(ypr[0]) if ypr else 0.0,
        "pitch": _number(ypr[1]) if len(ypr) > 1 else 0.0,
        "roll": _number(ypr[2]) if len(ypr) > 2 else 0.0,
        "lat": lat,
        "lng": lng,
    }


def read_objects(mission_dir: Path, terrain: str, server_cfg: Path) -> dict:
    """GET /object-spawner: Zedstead's objects, and whether DayZ reads them.

    ``listed`` says cfggameplay.json names the file and ``gameplay_on`` that
    serverDZ.cfg turns cfggameplay.json on; either False means DayZ spawns
    none of them. An unreadable file answers ``error`` with no objects.
    """
    path = own_path(mission_dir)
    payload: dict[str, Any] = {
        "file": OWN_FILE,
        "exists": path.is_file(),
        "listed": _listed(mission_dir),
        "gameplay_on": gameplay_file_on(server_cfg),
        "objects": [],
    }
    try:
        doc = _read_doc(path)
    except ObjectSpawnerError as e:
        return {**payload, "error": e.message}
    payload["objects"] = [
        row for i, entry in enumerate(doc["Objects"]) if (row := _entry_json(i, entry, terrain))
    ]
    try:
        off = _read_doc(own_path(mission_dir, disabled=True))
    except ObjectSpawnerError as e:
        return {**payload, "error": e.message}
    payload["objects"] += [
        row for i, entry in enumerate(off["Objects"]) if (row := _entry_json(i, entry, terrain, disabled=True))
    ]
    return payload


def check_classname(name: Any) -> str:
    """*name* stripped, or a 400 for one that is not a DayZ class name."""
    text = str(name or "").strip()
    if not _CLASSNAME_RE.match(text):
        raise ObjectSpawnerError(400, f"Not a class name: {text!r}. Use letters, digits and _ only.")
    return text


async def add_object(
    mission_dir: Path, terrain: str, name: str, x: float, y: float, z: float, yaw: float = 0.0
) -> dict:
    """Append one object to `OWN_FILE` and list the file in cfggameplay.json."""
    name = check_classname(name)
    async with _LOCK:
        path = own_path(mission_dir)
        doc = await asyncio.to_thread(_read_doc, path)
        await asyncio.to_thread(_list_own_file, mission_dir)
        entry = {"name": name, "pos": [round(x, 3), round(y, 3), round(z, 3)], "ypr": [round(yaw, 3), 0.0, 0.0]}
        doc["Objects"].append(entry)
        await asyncio.to_thread(versioning.write_json, path, doc, inline_scalar_lists=True)
        return _entry_json(len(doc["Objects"]) - 1, entry, terrain)


def _take(doc: dict, path: Path, index: int, expect: str | None) -> Any:
    """Entry *index* of *doc*, removed; 404 when there is none, 409 when it no longer shows *expect*."""
    objects = doc["Objects"]
    if not 0 <= index < len(objects):
        raise ObjectSpawnerError(404, f"No object {index} in {path.name}")
    current = objects[index]
    shown = str(current.get("name", "")) if isinstance(current, dict) else ""
    if expect is not None and shown != expect:
        raise ObjectSpawnerError(409, f"{path.name} changed since it was loaded. Reload and try again.")
    return objects.pop(index)


async def remove_object(mission_dir: Path, index: int, expect: str | None, disabled: bool = False) -> None:
    """Remove object *index* from its file; 409 when it no longer shows *expect*."""
    async with _LOCK:
        path = own_path(mission_dir, disabled)
        doc = await asyncio.to_thread(_read_doc, path)
        _take(doc, path, index, expect)
        await asyncio.to_thread(versioning.write_json, path, doc, inline_scalar_lists=True)


async def edit_object(
    mission_dir: Path,
    terrain: str,
    index: int,
    expect: str | None,
    disabled: bool,
    pos: tuple[float, float, float],
    ypr: tuple[float, float, float],
) -> dict:
    """Set object *index*'s ``pos`` and ``ypr``; every other key of the entry stays."""
    async with _LOCK:
        path = own_path(mission_dir, disabled)
        doc = await asyncio.to_thread(_read_doc, path)
        entry = _take(doc, path, index, expect)
        if not isinstance(entry, dict):
            raise ObjectSpawnerError(409, f"Object {index} in {path.name} is not an object entry.")
        entry["pos"] = [round(v, 3) for v in pos]
        entry["ypr"] = [round(v, 3) for v in ypr]
        doc["Objects"].insert(index, entry)
        await asyncio.to_thread(versioning.write_json, path, doc, inline_scalar_lists=True)
        return _entry_json(index, entry, terrain, disabled)


async def move_object(
    mission_dir: Path, terrain: str, index: int, expect: str | None, disabled: bool
) -> dict:
    """Disable object *index* of `OWN_FILE` (*disabled* False), or enable one of `DISABLED_FILE`.

    The entry moves whole, keys Zedstead does not write included. The target is
    written first: a failure in between leaves the object in both files, never
    in neither. Enabling lists `OWN_FILE` in cfggameplay.json, as a placement does.
    """
    async with _LOCK:
        source, target = own_path(mission_dir, disabled), own_path(mission_dir, not disabled)
        src_doc = await asyncio.to_thread(_read_doc, source)
        dst_doc = await asyncio.to_thread(_read_doc, target)
        if disabled:
            await asyncio.to_thread(_list_own_file, mission_dir)
        entry = _take(src_doc, source, index, expect)
        dst_doc["Objects"].append(entry)
        await asyncio.to_thread(versioning.write_json, target, dst_doc, inline_scalar_lists=True)
        await asyncio.to_thread(versioning.write_json, source, src_doc, inline_scalar_lists=True)
        return _entry_json(len(dst_doc["Objects"]) - 1, entry, terrain, disabled=not disabled)
