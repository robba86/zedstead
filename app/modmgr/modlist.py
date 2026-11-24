"""The mod list: which mods the server has, in what order, and their Workshop IDs.

The one way the rest of zsm reads and changes it (plans/77). Two files hold it:

- ``mods.json`` in the instance's config dir (`InstancePaths.mods_json`): every
  installed mod in list order, each with its Workshop ID when known and its
  flags (``enabled``; ``server_only``, loaded through ``-serverMod=``;
  ``local``, a folder the admin put there that zsm never downloads, written
  only when true; plans/78 D1), plus the load order (``manual``, and the
  ``pinned`` names it keeps).
- ``workshop-ids.json`` in the config dir (`paths.workshop_ids_file`): the
  name -> ID pairs zsm learned from a search or an install, for every
  instance. The IDs zsm ships with (`mod_ops.registry_mod_ids`) are read from
  code and never written.

DayZ reads neither; the server only sees ``-mod=`` and the ``@Name`` folders.
Both replace the server directory's mods.txt and cached-mod-ids.txt, which
zsm no longer reads at all (D4, D6).

A write goes through `update`: it holds a lock on the file while it reads,
changes and writes, since the panel and the CLI can both change the list.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from app.modmgr.mods import breaks_a_line, is_placeholder_mod_name, is_safe_mod_dir_name, is_workshop_id
from app.shared.json_utils import atomic_write_text

try:
    import fcntl
except ImportError:  # Windows: written unlocked, as steamcmd_lock does there
    fcntl = None

if TYPE_CHECKING:
    from app.shared.config import DayzConfig

VERSION = 1
T = TypeVar("T")


class ModListError(ValueError):
    """mods.json or workshop-ids.json cannot be read. The message names the file.

    Never answered by treating the list as empty: an update copies back only
    the listed mods, so an empty list would take every mod off the server.
    """


@dataclass
class Mod:
    name: str
    id: str = ""
    enabled: bool = True
    server_only: bool = False
    #: The admin's own folder, with no Workshop copy: zsm never downloads,
    #: replaces or removes it (plans/78 D1).
    local: bool = False


@dataclass
class ModList:
    mods: list[Mod] = field(default_factory=list)
    manual: bool = False
    pinned: list[str] = field(default_factory=list)

    # --- reading ----------------------------------------------------------

    def names(self) -> list[str]:
        return [m.name for m in self.mods]

    def active(self) -> list[str]:
        return [m.name for m in self.mods if m.enabled]

    def disabled(self) -> set[str]:
        return {m.name for m in self.mods if not m.enabled}

    def server_only(self) -> list[str]:
        return [m.name for m in self.mods if m.server_only]

    def local(self) -> set[str]:
        return {m.name for m in self.mods if m.local}

    def get(self, name: str) -> Mod | None:
        return next((m for m in self.mods if m.name == name), None)

    # --- changing ---------------------------------------------------------

    def add(self, name: str, mod_id: str = "") -> bool:
        if self.get(name):
            return False
        self.mods.append(Mod(name, mod_id if is_workshop_id(mod_id) else ""))
        return True

    def remove(self, names) -> list[str]:
        gone = set(names)
        removed = [m.name for m in self.mods if m.name in gone]
        self.mods = [m for m in self.mods if m.name not in gone]
        return removed

    def set_names(self, names: list[str]) -> None:
        """Make *names* the list, in that order. A mod that stays keeps its flags."""
        old = {m.name: m for m in self.mods}
        self.mods = [old.get(n) or Mod(n) for n in dict.fromkeys(names)]

    def set_enabled(self, name: str, on: bool) -> bool:
        mod = self.get(name)
        if mod is None or mod.enabled == on:
            return False
        mod.enabled = on
        return True

    def set_server_only(self, name: str, on: bool) -> bool:
        mod = self.get(name)
        if mod is None or mod.server_only == on:
            return False
        mod.server_only = on
        return True


# --- the files ---------------------------------------------------------------


def _path(config: DayzConfig) -> Path:
    return config.paths.mods_json


def _ids_path() -> Path:
    from app.shared.paths import workshop_ids_file

    return workshop_ids_file()


def _read_json(path: Path) -> object:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise ModListError(f"{path} is not UTF-8 text; fix or remove it.") from e
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ModListError(f"{path} is not valid JSON ({e.msg}, line {e.lineno}); fix or remove it.") from e


def name_problem(name) -> str | None:
    """What is wrong with *name* as a mod in the list, or None.

    One server folder (``@Name``), one line, and one ``-mod=`` entry: the
    launch command's own check, and no ``;``, which separates its entries.
    """
    from app.server.service_control import mod_name_problem

    if not isinstance(name, str) or not is_safe_mod_dir_name(name) or name != name.strip():
        return f"{name!r} is not a mod folder name (one @Name folder)"
    if ";" in name:
        return f"{name!r} holds ';', which separates the launch command's mods"
    return mod_name_problem(name)


def parse(data: object, source: str) -> ModList:
    """A `ModList` from mods.json's content. Raises `ModListError` naming
    *source* and what is wrong; nothing in it is dropped quietly."""
    if not isinstance(data, dict):
        raise ModListError(f"{source} is not a JSON object.")
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ModListError(f"{source} has no version number.")
    if version > VERSION:
        raise ModListError(
            f"{source} was written by a newer Zedstead (version {version}; this one reads {VERSION}). "
            "Update zsm."
        )
    raw_mods = data.get("mods", [])
    if not isinstance(raw_mods, list):
        raise ModListError(f"{source}: 'mods' is not a list.")
    mods: list[Mod] = []
    seen: set[str] = set()
    for raw in raw_mods:
        if not isinstance(raw, dict):
            raise ModListError(f"{source}: an entry of 'mods' is not an object.")
        name = raw.get("name")
        problem = name_problem(name)
        if problem:
            raise ModListError(f"{source}: {problem}.")
        if name in seen:
            raise ModListError(f"{source}: {name} is listed twice.")
        seen.add(name)
        mod_id = raw.get("id", "")
        if mod_id and not is_workshop_id(mod_id):
            raise ModListError(f"{source}: {name}'s id {mod_id!r} is not a Workshop ID.")
        flags = {k: raw.get(k, d) for k, d in (("enabled", True), ("server_only", False), ("local", False))}
        for key, value in flags.items():
            if not isinstance(value, bool):
                raise ModListError(f"{source}: {name}'s {key} is not true or false.")
        if flags["local"] and mod_id:
            raise ModListError(f"{source}: {name} is local and has a Workshop ID.")
        mods.append(Mod(name, mod_id, flags["enabled"], flags["server_only"], flags["local"]))
    order = data.get("load_order", {})
    if not isinstance(order, dict):
        raise ModListError(f"{source}: 'load_order' is not an object.")
    manual = order.get("manual", False)
    pinned = order.get("pinned", [])
    if not isinstance(manual, bool):
        raise ModListError(f"{source}: load_order.manual is not true or false.")
    if not isinstance(pinned, list) or not all(isinstance(n, str) for n in pinned):
        raise ModListError(f"{source}: load_order.pinned is not a list of names.")
    return ModList(mods, manual, list(pinned))


def replace_from(config: DayzConfig, data: bytes, source: str) -> int:
    """Make the mods.json content *data* (a bundle's, a backup's) this
    instance's list. Returns how many mods it holds. Raises `ModListError`
    naming *source*, and then changes nothing: a list is never taken in part."""
    try:
        imported = parse(json.loads(data.decode("utf-8")), source)
    except UnicodeDecodeError as e:
        raise ModListError(f"{source} is not UTF-8 text.") from e
    except json.JSONDecodeError as e:
        raise ModListError(f"{source} is not valid JSON ({e.msg}, line {e.lineno}).") from e

    def take(ml: ModList) -> None:
        ml.mods, ml.manual, ml.pinned = imported.mods, imported.manual, imported.pinned

    update(config, take)
    return len(imported.mods)


def to_json(mod_list: ModList) -> dict:
    return {
        "version": VERSION,
        "mods": [
            {
                "name": m.name, **({"id": m.id} if m.id else {}),
                "enabled": m.enabled, "server_only": m.server_only, **({"local": True} if m.local else {}),
            }
            for m in mod_list.mods
        ],
        "load_order": {"manual": mod_list.manual, "pinned": list(mod_list.pinned)},
    }


def load(config: DayzConfig) -> ModList:
    """The mod list. Empty when there is no mods.json yet (a new install)."""
    path = _path(config)
    if not path.is_file():
        return ModList()
    return parse(_read_json(path), str(path))


def save(config: DayzConfig, mod_list: ModList) -> None:
    _fill_ids(mod_list)
    path = _path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(to_json(mod_list), indent=2, ensure_ascii=False) + "\n")


def _fill_ids(mod_list: ModList) -> None:
    """Give each mod without an ID the one zsm knows, so the list carries its
    own IDs into a bundle or onto another machine."""
    missing = [m for m in mod_list.mods if not m.id and not m.local]
    if not missing:
        return
    known = _known_ids()
    for m in missing:
        m.id = known.get(m.name, "")


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    if fcntl is None:
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def update(config: DayzConfig, change: Callable[[ModList], T]) -> T:
    """Read the list, apply *change*, write it if anything changed, under the
    file's lock. Returns what *change* returned."""
    path = _path(config)
    with _locked(path):
        mod_list = load(config)
        before = to_json(mod_list)
        result = change(mod_list)
        if to_json(mod_list) != before:
            save(config, mod_list)
    return result


# --- Workshop IDs ------------------------------------------------------------


def _learned() -> dict[str, str]:
    path = _ids_path()
    if not path.is_file():
        return {}
    data = _read_json(path)
    if not isinstance(data, dict):
        raise ModListError(f"{path} is not a JSON object.")
    # An entry that is not a name and a Workshop ID is skipped: the ID is
    # passed to SteamCMD and joined onto the workshop directory.
    return {
        n: i for n, i in data.items()
        if isinstance(n, str) and not breaks_a_line(n) and is_workshop_id(i)
    }


def _known_ids() -> dict[str, str]:
    from app.modmgr.mod_ops import registry_mod_ids

    return {**registry_mod_ids(), **_learned()}


def known_ids() -> dict[str, str]:
    """``{name: Workshop ID}`` from the registry and what zsm learned, for a
    caller with no config (`xml_manual.mod_is_off`)."""
    return _known_ids()


def workshop_ids(config: DayzConfig) -> dict[str, str]:
    """``{name: Workshop ID}`` for every mod zsm knows the ID of: what it
    ships with, what it learned, and the IDs the list itself carries."""
    ids = _known_ids()
    ids.update({m.name: m.id for m in load(config).mods if m.id})
    return ids


def remember_id(config: DayzConfig, name: str, mod_id: str) -> None:
    """Keep *name*'s Workshop ID for later lookups. A name already known keeps its ID.

    A placeholder name (``@<id>``, what an install writes when nothing could
    name the mod) is not kept once the ID has a real name, and a real name
    replaces the ID's placeholder unless the list or a server folder uses
    that placeholder (F18).
    """
    if breaks_a_line(name) or not is_workshop_id(mod_id):
        return
    path = _ids_path()
    with _locked(path):
        learned = _learned()
        known = {**_known_ids(), **{m.name: m.id for m in load(config).mods if m.id}}
        if name in known:
            return
        if is_placeholder_mod_name(name):
            if any(wid == mod_id and not is_placeholder_mod_name(n) for n, wid in known.items()):
                return
        else:
            placeholders = {n for n, wid in learned.items() if wid == mod_id and is_placeholder_mod_name(n)}
            listed = {"@" + n.lstrip("@") for n in load(config).names()}
            used = any(
                ("@" + p.lstrip("@")) in listed or (config.server_dir_path / ("@" + p.lstrip("@"))).is_dir()
                for p in placeholders
            )
            if not used:
                learned = {n: i for n, i in learned.items() if n not in placeholders}
        learned[name] = mod_id
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(learned, indent=2, ensure_ascii=False) + "\n")


# --- the callers' shorthand --------------------------------------------------


def installed(config: DayzConfig) -> list[str]:
    """Every mod in the list, disabled ones included, in list order."""
    return load(config).names()


def active(config: DayzConfig) -> list[str]:
    """The mods the server loads: the list minus the disabled ones."""
    return load(config).active()


def disabled(config: DayzConfig) -> set[str]:
    return load(config).disabled()


def server_only(config: DayzConfig) -> list[str]:
    """The mods that load through ``-serverMod=``, which players never download."""
    return load(config).server_only()


def local(config: DayzConfig) -> set[str]:
    """The admin's own folders, which zsm never downloads, replaces or removes."""
    return load(config).local()


def installed_and_ids(config: DayzConfig) -> tuple[list[str], dict[str, str]]:
    """`installed` and `workshop_ids` together, for the many callers that need both."""
    return installed(config), workshop_ids(config)


def save_installed(config: DayzConfig, names: list[str]) -> None:
    """Make *names* the list, in that order. A mod that stays keeps its flags."""
    update(config, lambda ml: ml.set_names(names))


def add(config: DayzConfig, name: str, mod_id: str = "") -> bool:
    """Put *name* at the end of the list. Returns False when it was there already."""
    return update(config, lambda ml: ml.add(name, mod_id))


def remove(config: DayzConfig, names) -> list[str]:
    """Take *names* off the list, with their flags. Returns the ones that were on it."""
    return update(config, lambda ml: ml.remove(names))


def clear(config: DayzConfig) -> None:
    """An empty list, as after a server reinstall."""
    update(config, lambda ml: ml.set_names([]) or setattr(ml, "pinned", []) or setattr(ml, "manual", False))


def set_enabled(config: DayzConfig, name: str, on: bool) -> bool:
    return update(config, lambda ml: ml.set_enabled(name, on))


def set_server_only(config: DayzConfig, name: str, on: bool) -> bool:
    return update(config, lambda ml: ml.set_server_only(name, on))
