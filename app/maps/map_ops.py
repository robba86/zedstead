"""Shared business logic for map operations.

Used by both app/commands/maps.py (Click CLI) and
app/web/routers/maps.py (web UI).
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from app.shared.config import DayzConfig
from app.shared.json_utils import copy_file
from app.shared.zip_utils import (
    ArchiveLimits,
    check_unpack_size,
    copytree_without_links,
    safe_extract_path,
    save_capped,
)
from app.modmgr.mods import (
    get_current_mission,
    is_safe_mod_dir_name,
)
from app.modmgr import modlist
from app.maps.registry import MAPS_BY_TERRAIN, terrain_from_mission
from app.shared.seed_data import VANILLA_MAPS, WORKSHOP_MAPS, map_is_released


# ── Old map mod cleanup ────────────────────────────────────────────


def partition_old_map_mods(
    config: DayzConfig, new_map_data: dict | None
) -> tuple[list[str], list[str]]:
    """Split the mod list into (removable, deps kept on purpose) for a map switch.

    **A map's own mods and a map's deps are not the same thing, and the
    difference deletes people's servers.** `@DeerIsle` is a map: removing it is
    the whole point of switching away. `@CF` is Deer Isle's *dependency* — and
    also every Expansion module's. `@Dabs Framework` is TakistanPlus' and
    Expansion-Core's. The old code unioned `mods` and `deps` across every
    workshop map and treated the result as "map mods", so switching to a
    **vanilla** map deleted `@CF` and `@Dabs Framework` from a 21-mod Expansion
    install — directories, bikeys and mods.txt lines (the mod list then) — leaving a server that
    could not load. Measured in 2026 Q3 on the test container, from a plain
    `zsm maps switch Livonia`.

    The registry cannot tell a map's private dependency (`@Namalsk Survival`)
    from a framework the rest of the list is built on (`@CF`); both are just
    names under `deps`. So this does not guess. A dep is removed **only when
    nothing else is left that might want it** — the mod list holding nothing but
    the old map, its deps, and the new map's own entries. One mod the admin
    installed for its own sake is enough to keep every dep, and the caller says
    so rather than leaving an unexplained leftover. Same discipline as
    `cleanup_ops.find_leftover_mod_data`: the action behind this is an
    `rmtree`, so what cannot be known is not assumed.
    """
    map_mods = get_all_map_mod_names()
    map_deps = get_all_map_dep_names()
    current_mods = modlist.installed(config)

    new_map_mod_names: set[str] = set()
    if new_map_data:
        new_map_mod_names.update(new_map_data.get("mods", {}))
        new_map_mod_names.update(new_map_data.get("deps", {}))

    removable = [m for m in current_mods if m in map_mods and m not in new_map_mod_names]
    stale_deps = [
        m
        for m in current_mods
        if m in map_deps and m not in new_map_mod_names and m not in removable
    ]
    others = [
        m
        for m in current_mods
        if m not in map_mods and m not in map_deps and m not in new_map_mod_names
    ]

    if others:
        # With no map mod leaving, no map brought these deps: a switch from
        # Chernarus said it left @CF behind, which Expansion had installed
        # (browser test 2026-11-17, F3).
        return removable, stale_deps if removable else []
    return removable + stale_deps, []


def get_old_map_mods(config: DayzConfig, new_map_data: dict | None) -> list[str]:
    """Map mod names in the mod list that this switch may remove.

    See `partition_old_map_mods` for why a dep is not automatically one of
    them.
    """
    return partition_old_map_mods(config, new_map_data)[0]


def remove_old_map_mods_from_server(
    config: DayzConfig, mod_names: list[str]
) -> list[str]:
    """Remove old map mod directories and bikeys from server.

    Returns list of mod names that were actually removed.
    """
    from app.ops.fileops import bikeys_only_this_mod_ships

    removed: list[str] = []
    keys_dir = config.keys_dir

    for mod_name in mod_names:
        # Skip a malformed name that would resolve to (or above) the server
        # root — an unguarded rmtree there would wipe the whole server.
        if not is_safe_mod_dir_name(mod_name):
            continue
        mod_dir = config.server_dir_path / mod_name
        if not mod_dir.is_dir():
            continue

        # Collect bikeys before removal: a key another mod or the game still
        # needs stays, as in fileops.remove_mod_from_server.
        bikeys = bikeys_only_this_mod_ships(config, mod_dir)
        shutil.rmtree(mod_dir)

        # Remove matching bikeys from server keys/
        if bikeys and keys_dir.is_dir():
            for bikey_name in bikeys:
                bikey_path = keys_dir / bikey_name
                if bikey_path.exists():
                    bikey_path.unlink()

        removed.append(mod_name)

    return removed


# ── Map identification ───────────────────────────────────────────────


def _map_registry_names(key: str) -> set[str]:
    names: set[str] = set()
    for map_data in WORKSHOP_MAPS.values():
        names.update(map_data.get(key, {}))
    return names


def get_all_map_mod_names() -> set[str]:
    """Every workshop map's **own** mod names — `@DeerIsle`, `@Banov`.

    Deps are deliberately not in here; they are a different kind of thing and
    a different removal rule. See `partition_old_map_mods`.
    """
    return _map_registry_names("mods")


def get_all_map_dep_names() -> set[str]:
    """Every name a workshop map lists under `deps`.

    A mixed bag by nature: `@Namalsk Survival` is Namalsk's and nothing
    else's, while `@CF` and `@Dabs Framework` are frameworks half the workshop
    builds on. Nothing here distinguishes them, which is the whole reason
    `partition_old_map_mods` refuses to delete on the strength of this set
    alone.
    """
    return _map_registry_names("deps")


def find_shipped_mission(mpmissions: Path, terrain: str) -> str | None:
    """The mission folder in *mpmissions* for *terrain*, or None.

    A folder counts when its name ends in ``.<terrain>`` (any case) and it has
    ``db/types.xml``, so a half-copied folder is not offered. ``dayzOffline.*``
    wins over any other prefix, since that is how Bohemia names theirs.
    """
    try:
        found = [
            d.name for d in mpmissions.iterdir()
            if d.is_dir()
            and "." in d.name
            and terrain_from_mission(d.name) == terrain
            and (d / "db" / "types.xml").is_file()
        ]
    except OSError:
        return None
    found.sort(key=lambda n: (not n.lower().startswith("dayzoffline."), n))
    return found[0] if found else None


def vanilla_mission(config: DayzConfig, name: str) -> str | None:
    """The mission folder of official map *name*: the catalog's, or for a map
    the catalog knows only by terrain (announced, before a zsm release names
    its mission), the one a server update put in mpmissions/."""
    data = VANILLA_MAPS.get(name) or {}
    if data.get("mission"):
        return data["mission"]
    if not data.get("terrain"):
        return None
    return find_shipped_mission(config.server_dir_path / "mpmissions", data["terrain"])


def map_available(config: DayzConfig, name: str) -> bool:
    """`map_is_released`, plus an announced official map whose mission this
    server already has."""
    if map_is_released(name):
        return True
    return name in VANILLA_MAPS and vanilla_mission(config, name) is not None


def get_active_map_name(config: DayzConfig) -> str | None:
    """Match current serverDZ.cfg template against all known maps."""
    current = get_current_mission(config.server_cfg_path)
    if not current:
        return None
    for name, data in VANILLA_MAPS.items():
        if current == data["mission"]:
            return name
        if data.get("terrain") and terrain_from_mission(current) == data["terrain"]:
            return name
    for name, data in WORKSHOP_MAPS.items():
        if current == data["mission"]["template"]:
            return name
    return None


# ── Mods list management ────────────────────────────────────────────


def update_mods_list_for_map(config: DayzConfig, map_data: dict | None) -> None:
    """Swap map mods in the mod list — remove the outgoing map's, add the new one's.

    Removes exactly what `partition_old_map_mods` says is removable, so the
    entry that disappears from the mod list and the directory that disappears from
    the server dir are always decided by one rule. Before 2026 Q3 this used
    the union of every map's mods *and* deps, which is how `@CF` left a
    21-mod Expansion install.
    """
    removable = set(partition_old_map_mods(config, map_data)[0])
    mod_list = modlist.installed(config)
    mod_list = [m for m in mod_list if m not in removable]
    if map_data:
        for mod_name in map_data.get("mods", {}):
            if mod_name not in mod_list:
                mod_list.append(mod_name)
        for mod_name in map_data.get("deps", {}):
            if mod_name not in mod_list:
                mod_list.append(mod_name)
    modlist.save_installed(config, mod_list)


def map_mod_items(map_data: dict) -> dict[str, str]:
    """A workshop map's mods and deps, name -> Workshop id: what a switch downloads."""
    return {**map_data.get("mods", {}), **map_data.get("deps", {})}


def ensure_map_ids_cached(config: DayzConfig, map_data: dict) -> dict[str, str]:
    """Write all map mod IDs to cache and return full cached IDs."""
    for mod_name, mod_id in map_mod_items(map_data).items():
        modlist.remember_id(config, mod_name, mod_id)
    return modlist.workshop_ids(config)


# ── Download only ────────────────────────────────────────────────────
#
# The Maps page's "Download only" (POST /api/maps/download) fetches a workshop
# map's mods and mission without switching to it, so the map editor can show
# it. The mods stay in the Workshop cache: the XML merge reads every `@` folder
# in the server dir, listed or not, and not every map mod or dep is in
# `EXCLUDED_MOD_KEYWORDS` (Bitterroot, @CF), so a copy there would put an
# inactive map's XML into the running mission.


def mission_installed(mission_dir: Path) -> bool:
    """Whether a mission folder exists with anything in it; a switch keeps such a folder as it is."""
    try:
        return mission_dir.is_dir() and any(mission_dir.iterdir())
    except OSError:
        return False


def missing_map_mods(config: DayzConfig, map_data: dict) -> dict[str, str]:
    """A workshop map's mods and deps that are not on this machine, name -> Workshop id.

    A mod counts in the server dir or in the Workshop cache. What Download
    only still has to fetch, which is what its button's size adds up.
    """
    from app.shared.steam import is_mod_downloaded

    server_dir = config.server_dir_path
    return {
        name: mod_id
        for name, mod_id in map_mod_items(map_data).items()
        if not ((server_dir / name).is_dir() or is_mod_downloaded(config, mod_id))
    }


def is_map_downloaded(config: DayzConfig, map_data: dict) -> bool:
    """Whether a workshop map's mission and every mod and dep are on this machine.

    True for the active map and for one fetched with "Download only".
    """
    template = map_data.get("mission", {}).get("template")
    if not template or not mission_installed(config.server_dir_path / "mpmissions" / template):
        return False
    return not missing_map_mods(config, map_data)


@dataclass
class MapRemoval:
    """What removing a downloaded community map deletes and what it keeps.

    *mods* maps each mod or dep to delete to its Workshop id. *kept* maps each
    one that stays to the reason, for the job log.
    """

    mission_dir: Path | None
    mods: dict[str, str]
    kept: dict[str, str]


def map_removal_plan(config: DayzConfig, map_name: str) -> MapRemoval:
    """What the Maps page's Remove deletes for the workshop map *map_name*.

    A mod or dep stays when the mod list names it (enabled or not), since the
    server or another mod can load it, or when another downloaded workshop
    map lists it, such as `@CF`. Same rule as `partition_old_map_mods`: the
    action behind this is an `rmtree`, so a shared folder is never guessed
    unused. An unreadable mod list raises `ModListError`, so nothing goes.
    The caller refuses the active map: its mission is the one the server runs.
    """
    from app.shared.zip_utils import is_bare_name

    map_data = WORKSHOP_MAPS[map_name]
    listed = set(modlist.installed(config))
    others: set[str] = set()
    for name, data in WORKSHOP_MAPS.items():
        if name != map_name and is_map_downloaded(config, data):
            others.update(map_mod_items(data))

    mods: dict[str, str] = {}
    kept: dict[str, str] = {}
    for name, mod_id in map_mod_items(map_data).items():
        if name in listed:
            kept[name] = "it is in your mod list"
        elif name in others:
            kept[name] = "another downloaded map uses it"
        else:
            mods[name] = mod_id

    template = map_data.get("mission", {}).get("template") or ""
    mission_dir = None
    if is_bare_name(template):
        candidate = config.server_dir_path / "mpmissions" / template
        if candidate.is_dir():
            mission_dir = candidate
    return MapRemoval(mission_dir=mission_dir, mods=mods, kept=kept)


def remove_downloaded_map(
    config: DayzConfig, map_name: str, log: Callable[[str], None]
) -> MapRemoval:
    """Delete a downloaded workshop map's mission folder and the mods
    `map_removal_plan` frees, from the server dir and the Workshop cache.

    The mission folder goes with its player storage. A cached copy goes
    through `cleanup_ops.remove_workshop_mods`, which also drops it from
    SteamCMD's workshop manifest.
    """
    from app.modmgr.mods import is_workshop_id
    from app.ops.cleanup_ops import remove_workshop_mods

    plan = map_removal_plan(config, map_name)
    for name, reason in plan.kept.items():
        log(f"  Kept {name}: {reason}.")
    for name in remove_old_map_mods_from_server(config, list(plan.mods)):
        log(f"  Removed {name} and its bikeys from the server folder.")
    cached = [
        config.workshop_dir / mod_id
        for mod_id in plan.mods.values()
        if is_workshop_id(mod_id) and (config.workshop_dir / mod_id).is_dir()
    ]
    if cached:
        remove_workshop_mods(config, cached, log=lambda line: log(f"  {line}"))
    if plan.mission_dir is not None:
        shutil.rmtree(plan.mission_dir)
        log(f"  Removed the mission folder {plan.mission_dir.name}.")
    return plan


def downloaded_map_mod_dirs(config: DayzConfig, terrain: str) -> list[Path]:
    """The Workshop cache's folder for each mod that carries *terrain*.

    Where `terrain_map.world_file` looks after the server dir, so the map
    editor draws a map that was downloaded but not switched to. Only the map's
    own `mods`: a dependency never carries the terrain. Not checked to exist;
    the search skips a folder it cannot list.
    """
    entry = MAPS_BY_TERRAIN.get(terrain)
    if entry is None:
        return []
    workshop = config.workshop_dir
    dirs: list[Path] = []
    for name in entry.variants:
        for mod_id in WORKSHOP_MAPS.get(name, {}).get("mods", {}).values():
            if workshop / mod_id not in dirs:
                dirs.append(workshop / mod_id)
    return dirs


# ── Mission extraction ───────────────────────────────────────────────

_MB = 1024 * 1024

#: Ceilings for a mission archive and what is unpacked from it (plans/68
#: NEW-7). The archive follows its repository's branch head, so its size is
#: whatever upstream holds today. Set well above any real one, measured by
#: estimate rather than download: a DayZ mission folder is tens of MB of XML
#: (a large map's mapgrouppos.xml is the biggest file, around 10-20 MB) and
#: some hundreds of files, a community repository holds one or two of them,
#: and the largest archive zsm fetches, the Expansion missions repository
#: with a mission per map, was assumed to be under 300 MB zipped. GitHub's
#: repository sizes in 2026 Q4 (history included, so an upper bound for a
#: branch zip): Banov 155 MB, the largest, then Deer Isle 57, Expansion
#: missions 51, Esseker 44, TakistanPlus 41, Bitterroot 17, Namalsk 6. Don't
#: tighten the download limit below Banov's. A limit is a refusal with a
#: message, never a partial mission.
MISSION_ZIP_LIMITS = ArchiveLimits(
    download_bytes=1024 * _MB,
    total_bytes=2048 * _MB,
    member_bytes=512 * _MB,
    members=50_000,
)


def _download_archive(zip_url: str, zip_path: Path) -> None:
    """The archive at *zip_url* into *zip_path*, within `MISSION_ZIP_LIMITS`."""
    import requests

    with requests.get(zip_url, timeout=120, stream=True) as resp:
        resp.raise_for_status()
        save_capped(
            resp.iter_content(chunk_size=64 * 1024), zip_path,
            MISSION_ZIP_LIMITS.download_bytes, source=f"The archive at {zip_url}",
        )


#: What `download_and_extract_mission` records in a mission it downloaded:
#: the archive, the folder it took and upstream's commit, which
#: `mission_update_status` compares with the branch head today.
MISSION_SOURCE_FILE = ".zsm-source.json"

_COMMIT_RE = re.compile(r"[0-9a-f]{40}")


def _version_key(name: str) -> list:
    """Natural order, so ``V5.10`` sorts after ``V5.9``."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def _in_version_folder(folder: PurePosixPath) -> bool:
    """Whether *folder* sits in a folder named for a version, such as ``V5.9``."""
    return folder.parent != PurePosixPath(".") and any(c.isdigit() for c in folder.parent.name)


def _highest_version(folders: list[PurePosixPath]) -> PurePosixPath:
    return max(folders, key=lambda f: _version_key(f.parent.name))


def resolve_mission_path(files: list[str], mission_path: str, *, source: str) -> str:
    """The folder of an archive to take for the catalog's *mission_path*.

    *files* are the archive's file names below its top folder. The archive
    follows upstream's branch head, so upstream can move the folder. A
    catalog folder in a version folder (Deer Isle's ``V5.9/empty.deerisle``)
    resolves to the highest version folder beside it that holds the same
    name, even while the old one is still there: Deer Isle kept ``V5.3/``
    when it added ``V5.9/``. Any other catalog folder wins while it holds a
    file, and a missing one is looked for by its name anywhere in the
    archive. Raises ValueError when nothing matches, or several folders that
    are not versions do.
    """
    wanted = PurePosixPath(mission_path)
    found = sorted({
        PurePosixPath(*parts[: i + 1])
        for parts in (PurePosixPath(f).parts for f in files)
        for i, part in enumerate(parts[:-1])
        if part == wanted.name
    })
    if _in_version_folder(wanted):
        versions = [
            f for f in found
            if _in_version_folder(f) and f.parent.parent == wanted.parent.parent
        ]
        if versions:
            return str(_highest_version(versions))
    if wanted in found:
        return str(wanted)
    if len(found) == 1:
        return str(found[0])
    if found and all(_in_version_folder(f) for f in found):
        return str(_highest_version(found))
    same_name = f" Folders with that name: {', '.join(map(str, found))}." if found else ""
    raise ValueError(
        f"Mission folder not found in the archive: {mission_path}\n"
        f"Nothing was extracted from {source}; the mission_path is "
        f"probably out of date.{same_name}"
    )


def _replace_folder(src: Path, dest: Path, keep_storage: bool) -> None:
    """*dest* gets *src*'s content in place of its own.

    With *keep_storage*, every ``storage_<n>`` folder of *dest* stays where
    it is and wins over the archive's copy. A world folder is never moved,
    so nothing that fails here can lose it.
    """
    from app.ops.cleanup_ops import clear_mission_dir

    if not dest.exists():
        src.rename(dest)
        return
    clear_mission_dir(dest, preserve_storage=keep_storage)
    for item in src.iterdir():
        target = dest / item.name
        if not target.exists():
            item.rename(target)


def download_and_extract_mission(
    zip_url: str,
    mission_path: str,
    dest_dir: Path,
    log: Callable[[str], None] | None = None,
    *,
    keep_storage: bool = False,
    record_source: bool = False,
) -> int:
    """Download zip and extract mission folder. Returns count of extracted files.

    Raises ValueError, before *dest_dir* is touched, if the archive holds no
    folder for *mission_path* (`resolve_mission_path`). An upstream rename
    used to extract nothing and report success, leaving a map switch with an
    empty mission folder (Banov's ``empty.banov``, plans/60 G). Raises
    `ArchiveTooLarge` (a ValueError) past `MISSION_ZIP_LIMITS`, also before
    *dest_dir* is touched.

    The folder is unpacked beside *dest_dir* and replaces its content only
    once every file is out (`_replace_folder`), so a download or unpack that
    fails leaves *dest_dir* as it was. Reset mission used to empty the
    mission first, and a failed download left a server that could not start.
    With *record_source*, the mission gets `MISSION_SOURCE_FILE`.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        zip_path = tmp_path / "repo.zip"

        if log:
            log("  Downloading archive...")
        _download_archive(zip_url, zip_path)

        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            top_dirs = {n.split("/")[0] for n in names if "/" in n}
            prefix = f"{top_dirs.pop()}/" if len(top_dirs) == 1 else ""
            folder = resolve_mission_path(
                [n[len(prefix):] for n in names if n.startswith(prefix) and not n.endswith("/")],
                mission_path, source=zip_url,
            )
            if log and folder != str(PurePosixPath(mission_path)):
                log(f"  Taking {folder}/ from the archive (the catalog names {mission_path}/).")
            source_prefix = f"{prefix}{folder}/"
            members = [m for m in names if m.startswith(source_prefix) and m != source_prefix]
            check_unpack_size(
                [zf.getinfo(m) for m in members], MISSION_ZIP_LIMITS,
                source=f"The archive at {zip_url}",
            )

            dest_dir.parent.mkdir(parents=True, exist_ok=True)
            # A dot name, so `get_available_missions` never lists it.
            staging = Path(tempfile.mkdtemp(prefix=f".{dest_dir.name}.download-", dir=dest_dir.parent))
            try:
                extracted = 0
                for member in members:
                    target = safe_extract_path(staging, member[len(source_prefix):])
                    if member.endswith("/"):
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(member) as src, open(target, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        extracted += 1
                if record_source:
                    commit = zf.comment.decode("ascii", "replace").strip()
                    (staging / MISSION_SOURCE_FILE).write_text(json.dumps({
                        "zip_url": zip_url,
                        "mission_path": folder,
                        # GitHub writes the commit as the archive's comment.
                        "commit": commit if _COMMIT_RE.fullmatch(commit) else None,
                    }, indent=2) + "\n", encoding="utf-8")
                _replace_folder(staging, dest_dir, keep_storage)
            finally:
                shutil.rmtree(staging, ignore_errors=True)

            if log:
                log(f"  Extracted {extracted} file(s) to {dest_dir.name}/")
            return extracted


def read_mission_source(mission_dir: Path) -> dict | None:
    """The `MISSION_SOURCE_FILE` record of *mission_dir*, or None."""
    try:
        data = json.loads((mission_dir / MISSION_SOURCE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class MissionCheckError(Exception):
    """GitHub could not answer `mission_update_status`."""


@dataclass
class MissionUpdate:
    newer: bool | None  # None: Zedstead cannot tell
    detail: str
    changed: int = 0


_GITHUB_ARCHIVE = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+)/archive/refs/heads/([\w.-]+)\.zip")

#: GitHub's compare API lists at most this many files.
_COMPARE_FILE_LIMIT = 300


def mission_update_status(mission_dir: Path, zip_url: str) -> MissionUpdate:
    """Whether upstream changed a community mission after Zedstead downloaded it.

    One request to GitHub's compare API, from the commit in the mission's
    `MISSION_SOURCE_FILE` to the branch head that the catalog's *zip_url*
    follows. A changed file below any folder with the mission's name counts,
    so a moved folder and a new version folder count too
    (`resolve_mission_path`). Raises `MissionCheckError` when GitHub cannot
    answer.
    """
    import requests

    record = read_mission_source(mission_dir) or {}
    commit = record.get("commit")
    folder = record.get("mission_path")
    match = _GITHUB_ARCHIVE.fullmatch(zip_url)
    if not match:
        return MissionUpdate(None, "This mission does not come from a GitHub branch, so Zedstead cannot check it.")
    if not (isinstance(commit, str) and _COMMIT_RE.fullmatch(commit) and isinstance(folder, str)):
        return MissionUpdate(
            None,
            "Zedstead did not record which upstream version this mission came from. "
            "Reset mission records it.",
        )
    owner, repo, branch = match.groups()
    url = f"https://api.github.com/repos/{owner}/{repo}/compare/{commit}...{branch}"
    try:
        resp = requests.get(url, timeout=20, headers={"Accept": "application/vnd.github+json"})
    except requests.RequestException as e:
        raise MissionCheckError(f"GitHub did not answer: {e}") from e
    if resp.status_code == 404:
        return MissionUpdate(
            None,
            f"GitHub no longer knows the commit this mission came from ({commit[:7]}). "
            "Upstream rewrote its history. Reset mission takes upstream's current files.",
        )
    if resp.status_code in (403, 429):
        raise MissionCheckError(
            "GitHub refused the check. It allows 60 checks an hour from one address, "
            "so try again later."
        )
    try:
        resp.raise_for_status()
        data = resp.json()
        files = [f["filename"] for f in data.get("files") or []]
        ahead = int(data.get("ahead_by") or 0)
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        raise MissionCheckError(f"GitHub's answer could not be read: {e}") from e

    name = PurePosixPath(folder).name
    changed = sum(1 for f in files if name in PurePosixPath(f).parts[:-1])
    if ahead == 0:
        return MissionUpdate(False, f"The mission is upstream's current version ({commit[:7]}).")
    if changed:
        return MissionUpdate(
            True,
            f"Upstream changed {changed} mission file(s) in {ahead} commit(s) after {commit[:7]}.",
            changed,
        )
    if len(files) >= _COMPARE_FILE_LIMIT:
        return MissionUpdate(
            True,
            f"Upstream changed more files than GitHub lists in {ahead} commit(s) after "
            f"{commit[:7]}, so mission files can be among them.",
        )
    return MissionUpdate(
        False,
        f"Upstream has {ahead} new commit(s) after {commit[:7]}, but none changes the mission.",
    )


def extract_server_cfg_from_zip(
    zip_url: str,
    server_cfg_path: str,
    dest_path: Path,
) -> bool:
    """Download zip and extract server cfg. Returns True on success.

    The same `MISSION_ZIP_LIMITS` as the mission: it is the same archive.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        zip_path = tmp_path / "repo.zip"

        _download_archive(zip_url, zip_path)

        with zipfile.ZipFile(zip_path, "r") as zf:
            top_dirs = {n.split("/")[0] for n in zf.namelist() if "/" in n}
            prefix = top_dirs.pop() if len(top_dirs) == 1 else ""
            full_path = f"{prefix}/{server_cfg_path}" if prefix else server_cfg_path

            if full_path in zf.namelist():
                check_unpack_size(
                    [zf.getinfo(full_path)], MISSION_ZIP_LIMITS,
                    source=f"The archive at {zip_url}",
                )
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(full_path) as src, open(dest_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                return True

    return False


def copy_mission_from_mod(
    config: DayzConfig,
    mod_name: str,
    mission_path: str,
    dest_dir: Path,
    cached_ids: dict[str, str],
) -> int:
    """Copy mission folder from a downloaded workshop mod.

    Returns count of files copied.
    Raises ValueError if mod ID not found or source dir missing.
    """
    mod_id = cached_ids.get(mod_name)
    if not mod_id:
        raise ValueError(f"No cached workshop ID for {mod_name}")
    source = config.workshop_dir / mod_id / mission_path
    if not source.is_dir():
        raise ValueError(
            f"Mission folder not found: {source}\n"
            f"Make sure {mod_name} is downloaded and the mission_path is correct."
        )
    dest_dir.mkdir(parents=True, exist_ok=True)
    copytree_without_links(source, dest_dir, dirs_exist_ok=True)
    return sum(1 for f in dest_dir.rglob("*") if f.is_file())


# ── Config backup / restore ─────────────────────────────────────────


def backup_map_cfg(config: DayzConfig) -> Path | None:
    """Save current serverDZ.cfg as serverDZ.cfg.<mapname>.backup. Returns backup path."""
    cfg = config.server_cfg_path
    if not cfg.exists():
        return None
    active_map = get_active_map_name(config)
    suffix = active_map.lower() if active_map else "unknown"
    backup = cfg.parent / f"serverDZ.cfg.{suffix}.backup"
    # copy, not copy2: the copy's mtime is when it was saved, which the map
    # switch dialog shows. copy2 kept serverDZ.cfg's own last edit, so a copy
    # made today read as saved days earlier.
    shutil.copy(cfg, backup)
    return backup


def get_map_cfg_backup(config: DayzConfig, map_name: str) -> Path | None:
    """Return the backup path for a map config if it exists, else None."""
    cfg = config.server_cfg_path
    target = cfg.parent / f"serverDZ.cfg.{map_name.lower()}.backup"
    return target if target.exists() else None


# The serverDZ.cfg keys a backup restore carries over from the cfg it replaces.
# `passwordAdmin` is DayZ's own name (dayz-docs/wiki/pages/server-configuration.wiki).
PRESERVED_CFG_KEYS = ("password", "passwordAdmin")


def _replace_cfg_keeping_passwords(
    cfg: Path, template: str, replace: Callable[[], bool],
) -> bool:
    """Run *replace* (which overwrites *cfg*), then put the mission template and
    the old cfg's passwords into the result.

    A key the old cfg did not set is written empty: having no password is a
    setting too, and the file put in its place must not bring its own.
    """
    from app.parsers.servercfg import read_cfg_values, set_mission_template, write_cfg_value

    preserved = read_cfg_values(cfg, list(PRESERVED_CFG_KEYS))
    if not replace():
        return False
    set_mission_template(cfg, template)
    for key, val in preserved.items():
        write_cfg_value(cfg, key, val or "", quoted=True)
    return True


def restore_map_cfg(config: DayzConfig, map_name: str, template: str) -> None:
    """Restore a map-specific config backup, preserving passwords."""
    cfg = config.server_cfg_path
    target_backup = cfg.parent / f"serverDZ.cfg.{map_name.lower()}.backup"

    def _copy() -> bool:
        copy_file(target_backup, cfg)
        return True

    _replace_cfg_keeping_passwords(cfg, template, _copy)


def _whole_number(value: str) -> bool:
    return value.isascii() and value.isdigit()


def _zero_or_one(value: str) -> bool:
    return value in ("0", "1")


# The only serverDZ.cfg keys a map's recommended cfg sets on the admin's cfg, each
# with the values taken from it and how the log names them. The rest of that file
# is the map author's own server (hostname, passwords, ports, verifySignatures,
# instanceId, BattlEye ...) and stays the admin's. So do the gameplay choices a
# map's cfg also makes: third person (disable3rdPerson), the crosshair
# (disableCrosshair) and the time keys (serverTime, serverTimeAcceleration,
# serverNightTimeAcceleration, serverTimePersistent). The mission `template` comes
# from the map catalog, not from the file.
MAP_CFG_KEYS: dict[str, tuple[Callable[[str], bool], str]] = {
    # Night lighting is per terrain. Stock DayZ has 0 brighter, 1 darker and 2
    # Sakhal's own (dayz-docs/wiki/pages/server-configuration.wiki:121), and a
    # terrain mod brings its own: Namalsk's recommended cfgs set 223 (Regular) and
    # 222 (Hardcore). So any whole number.
    "lightingConfig": (_whole_number, "a whole number"),
    # Whether players get a personal light at night goes with the map's lighting:
    # Namalsk's Regular cfg sets 1 and its Hardcore cfg 0.
    "disablePersonalLight": (_zero_or_one, "0 or 1"),
    # A map's mission folder may ship its own cfggameplay.json, which the server
    # reads only with this set (dayz-docs/wiki/pages/gameplay-settings.wiki:12).
    # Namalsk's cfgs do not set it; a map whose cfg does means it.
    "enableCfgGameplayFile": (_zero_or_one, "0 or 1"),
}


def merge_map_cfg_keys(
    cfg: Path, source: Path, *, log: Callable[[str], None] | None = None,
) -> list[str]:
    """Copy the keys in MAP_CFG_KEYS from *source* into *cfg*. Returns the keys
    it changed, and logs one line per key it changed or left alone.

    A value the key does not take is not written, and is not repeated in the
    log: it comes from a third-party file.
    """
    from app.parsers.servercfg import read_cfg_values, write_cfg_value

    def _log(line: str) -> None:
        if log:
            log(line)

    if not cfg.is_file():
        _log(f"  No serverDZ.cfg at {cfg}; the map's settings were not applied.")
        return []
    keys = list(MAP_CFG_KEYS)
    try:
        theirs = read_cfg_values(source, keys)
    except (OSError, UnicodeDecodeError):
        _log("  The map's recommended config is not a UTF-8 text file; nothing applied.")
        return []
    ours = read_cfg_values(cfg, keys)

    changed: list[str] = []
    for key, (accepts, what) in MAP_CFG_KEYS.items():
        if theirs[key] is None:
            continue
        new = theirs[key].strip()
        if not accepts(new):
            _log(f"  {key}: kept yours; the map's value is not {what}.")
            continue
        old = (ours[key] or "").strip()
        if old == new:
            continue
        write_cfg_value(cfg, key, new)
        changed.append(key)
        _log(f"  {key}: {old or '(not set)'} -> {new}")
    if not changed:
        _log("  serverDZ.cfg already has the map's recommended settings.")
    return changed


def apply_recommended_cfg(
    config: DayzConfig,
    zip_url: str,
    server_cfg_path_in_zip: str,
    template: str,
    *,
    log: Callable[[str], None] | None = None,
) -> bool:
    """Download a map's recommended serverDZ.cfg and copy only the keys in
    MAP_CFG_KEYS from it into the admin's cfg, then set the mission template.

    Returns False when the archive has no cfg at *server_cfg_path_in_zip*.
    """
    from app.parsers.servercfg import set_mission_template

    cfg = config.server_cfg_path
    with tempfile.TemporaryDirectory() as tmp:
        theirs = Path(tmp) / "serverDZ.cfg"
        if not extract_server_cfg_from_zip(zip_url, server_cfg_path_in_zip, theirs):
            return False
        if log:
            log("  Kept your current config and applied the map's recommended keys:")
        merge_map_cfg_keys(cfg, theirs, log=log)
    set_mission_template(cfg, template)
    return True


def apply_mod_xml_to_new_mission(config: DayzConfig, log: Callable[[str], None]) -> bool:
    """Apply every enabled mod's XML to the mission a switch just selected.

    The new mission may never have been the configured one during a merge, so
    its custom/ folder and its mod blocks are missing, and a plain Start does not
    merge: the server ran every mod with none of their types. This is the
    update pipeline's merge step, the same one "Re-apply all" runs. A failure is
    logged, not raised, because the switch itself has already been made.
    Returns False when the merge failed.
    """
    from app.ops import pipeline

    if not modlist.installed(config):
        return True
    log("Applying mod XML to the new mission")
    ctx = pipeline.PipelineContext(
        config=config, emit=log, runner=None, should_run=lambda _key: True,
    )
    try:
        pipeline._merge_xml_sync(ctx)
    except Exception as exc:  # noqa: BLE001 - reported, the switch stands
        log(f"  Could not apply mod XML: {exc}")
        log("  Run Re-apply all on the Mods page, or `zsm run`, before starting the server.")
        return False
    # The merge logs only the files it links, so a mission with none ended the
    # step on its heading and read as cut off.
    log("  Mod XML applied.")
    return True


def switch_map_cfg(
    config: DayzConfig,
    map_name: str,
    template: str,
    mission: dict | None = None,
    *,
    restore_previous: bool = True,
    apply_recommended: bool = True,
    log: Callable[[str], None] | None = None,
    warn: Callable[[str], None] | None = None,
) -> str:
    """Settle serverDZ.cfg for a switch to *map_name*, once the current cfg has
    been backed up and the mission template set. The CLI and the web both call
    this, so the two cannot disagree about which cfg a switch leaves.

    Returns what happened:

    - ``"restored"``: *restore_previous* is on and a saved config for the map
      exists. It replaces the cfg whole, keeping the current passwords.
    - ``"recommended"``: otherwise, when *apply_recommended* is on and the
      map's *mission* names a recommended cfg, that cfg's MAP_CFG_KEYS go onto
      the current one.
    - ``"kept"``: otherwise the current cfg stays as it is, mission aside. An
      archive without the cfg where the catalog says ends here too, through
      *warn* (default: an indented *log* line).

    Lines are markup-free; a job log renders them raw.
    """
    _log = log or (lambda _line: None)
    _warn = warn or (lambda text: _log(f"  {text}"))

    if restore_previous and get_map_cfg_backup(config, map_name):
        restore_map_cfg(config, map_name, template)
        _log(f"  Restored your saved config for {map_name} (passwords kept).")
        return "restored"

    mission = mission or {}
    zip_url = mission.get("zip_url")
    server_cfg_path_in_zip = mission.get("server_cfg_path")
    if apply_recommended and zip_url and server_cfg_path_in_zip:
        if apply_recommended_cfg(config, zip_url, server_cfg_path_in_zip, template, log=log):
            return "recommended"
        _warn("Could not find the recommended config in the map's archive; kept your current config.")
        return "kept"

    _log("  Kept your current config; only the mission changed.")
    return "kept"


# ── What goes to the new map: the switch dialog's choices ───────────
#
# A switch used to take every enabled mod, the mod XML and every manual XML
# file along without asking (plans/83). The dialog now shows what the switch
# will do and lets the admin leave mods and manual files behind. The CLI's
# flags and the web route both go through these four, so neither surface
# holds a rule of its own. With no choices made a switch does exactly what it
# did before: every mod, the mod XML, every manual file, the saved cfg.

#: Logged by both surfaces when a switch is told not to apply the mod XML.
MOD_XML_SKIPPED = (
    "Mod XML was not applied to the new mission: your mods load there without"
    " their types, events and spawns until you run Re-apply all on the Mods"
    " page, or `zsm run`."
)


@dataclass
class SwitchChoices:
    """What a switch takes to the new map. The defaults are a plain switch."""

    restore_previous: bool = True
    apply_recommended: bool = True
    apply_mod_xml: bool = True
    disable_mods: tuple[str, ...] | list[str] = ()
    leave_manual_files: tuple[str, ...] | list[str] = ()


def _target_template(config: DayzConfig, map_id: str) -> str | None:
    if map_id in WORKSHOP_MAPS:
        return WORKSHOP_MAPS[map_id]["mission"]["template"]
    return vanilla_mission(config, map_id)


def _terrain_known(terrain: str | None) -> bool:
    from app.xml.xml_manual import known_terrain_tokens

    return bool(terrain) and terrain in known_terrain_tokens()


def _manual_file_rows(config: DayzConfig, from_terrain: str | None, to_terrain: str | None) -> list[dict]:
    """The enabled manual XML files and where each applies after the switch.

    ``scope``: ``every`` (no terrain: it follows unless left behind), ``to``
    (scoped to the new map), ``from`` (scoped to the old one, so it stays
    there anyway) or ``other``. A map-agnostic file can stay behind only when
    scoping it to the old terrain stops it on the new one: not when the old
    terrain is unknown, nor when the two share a token (Banov and Banov Frost).
    """
    from app.xml.xml_manual import applies_to, load_files

    can_scope = _terrain_known(from_terrain) and not applies_to(
        {"terrain": from_terrain}, to_terrain,
    )
    rows = []
    for row in load_files(manual_dir=config.paths.custom_xml_dir):
        if not row.get("id") or not row.get("enabled", True):
            continue
        terrain = (row.get("terrain") or "").strip()
        if not terrain:
            scope = "every"
        elif applies_to(row, to_terrain):
            scope = "to"
        elif from_terrain and applies_to(row, from_terrain):
            scope = "from"
        else:
            scope = "other"
        rows.append({
            "id": row["id"],
            "kind": row.get("kind", ""),
            "mod": row.get("mod", ""),
            "note": row.get("note", ""),
            "terrain": terrain,
            "scope": scope,
            "can_leave": scope == "every" and can_scope,
        })
    return rows


def switch_plan(config: DayzConfig, map_id: str) -> dict:
    """What a switch to *map_id* takes along, leaves and could leave behind.

    Reads only. Raises KeyError for an unknown map and ValueError for one not
    released yet. The new map's own mods and deps always follow (``map_mods``;
    ``turn_on`` marks one that is installed but off, which the switch turns
    on); the old map's mods the switch removes are in ``leaving``; ``mods`` are
    the enabled mods the admin may leave behind, each with the enabled mods
    that require it.
    """
    from app.modmgr.mods import get_mod_load_deps

    if map_id not in WORKSHOP_MAPS and map_id not in VANILLA_MAPS:
        raise KeyError(map_id)
    if not map_available(config, map_id):
        raise ValueError(f"{map_id} has not been released yet.")

    map_data = WORKSHOP_MAPS.get(map_id)
    current = get_current_mission(config.server_cfg_path)
    template = _target_template(config, map_id)
    from_terrain = terrain_from_mission(current) if current else None
    to_terrain = terrain_from_mission(template) if template else None

    state = modlist.load(config)
    ids = modlist.workshop_ids(config)
    by_name = {m.name: m for m in state.mods}

    map_mods = []
    own: set[str] = set()
    if map_data:
        for key, dep in (("mods", False), ("deps", True)):
            for name, mod_id in map_data.get(key, {}).items():
                own.add(name)
                mod = by_name.get(name)
                map_mods.append({
                    "name": name,
                    "id": mod_id,
                    "dep": dep,
                    "on_disk": (config.server_dir_path / name).is_dir(),
                    "turn_on": bool(mod and not mod.enabled),
                })

    removed, kept_deps = partition_old_map_mods(config, map_data)
    gone = set(removed)

    try:
        deps = get_mod_load_deps(config)
    except Exception:  # noqa: BLE001 - a warning's source, never a reason to refuse
        deps = {}
    choosable = [
        m for m in state.mods if m.enabled and m.name not in own and m.name not in gone
    ]
    mods = [
        {
            "name": m.name,
            "id": m.id or ids.get(m.name, ""),
            "server_only": m.server_only,
            "local": m.local,
            "required_by": sorted(
                other for other, needs in deps.items() if m.name in needs and other != m.name
            ),
        }
        for m in choosable
    ]
    off_mods = [
        m.name for m in state.mods if not m.enabled and m.name not in own and m.name not in gone
    ]

    backup = get_map_cfg_backup(config, map_id)
    try:
        backup_info = {"modified": backup.stat().st_mtime} if backup else None
    except OSError:
        backup_info = None
    mission = (map_data or {}).get("mission", {})

    return {
        "map_id": map_id,
        "from": get_active_map_name(config),
        "from_terrain": from_terrain,
        "to_terrain": to_terrain,
        "map_mods": map_mods,
        "leaving": {"removed": removed, "kept_deps": kept_deps},
        "mods": mods,
        "off_mods": off_mods,
        "cfg": {
            "backup": backup_info,
            "recommended": bool(mission.get("zip_url") and mission.get("server_cfg_path")),
            "keys": list(MAP_CFG_KEYS),
        },
        "manual_files": _manual_file_rows(config, from_terrain, to_terrain),
    }


def _chosen_mods(choices: SwitchChoices) -> list[str]:
    from app.modmgr.mod_ops import ensure_at_prefix

    return list(dict.fromkeys(ensure_at_prefix(n.strip()) for n in choices.disable_mods if n.strip()))


def _old_map_label(plan: dict) -> str:
    return plan.get("from") or plan.get("from_terrain") or "the current map"


def check_switch_choices(plan: dict, choices: SwitchChoices) -> str | None:
    """A sentence saying why *choices* cannot be carried out, or None."""
    own = {m["name"]: m for m in plan["map_mods"]}
    choosable = {m["name"] for m in plan["mods"]}
    removed = set(plan["leaving"]["removed"])
    off = set(plan["off_mods"])
    target = plan["map_id"]
    for name in _chosen_mods(choices):
        if name in own:
            what = "dependency" if own[name]["dep"] else "own mod"
            return f"{name} is {target}'s {what} and always goes with it."
        if name in removed:
            return (
                f"{name} leaves with {_old_map_label(plan)}: the switch removes it,"
                " so there is nothing to turn off."
            )
        if name in off:
            return f"{name} is already turned off."
        if name not in choosable:
            return f"{name} is not in the mod list."
    files = {f["id"]: f for f in plan["manual_files"]}
    for file_id in dict.fromkeys(choices.leave_manual_files):
        row = files.get(file_id)
        if row is None:
            return f"There is no enabled manual XML file {file_id!r}."
        if not row["can_leave"]:
            if row["scope"] != "every":
                return f"Manual XML file {file_id} applies to one map only, so it is not taken along anyway."
            return (
                f"Manual XML file {file_id} cannot stay with {_old_map_label(plan)}:"
                f" scoped to that map it would still apply on {target}."
            )
    return None


def leave_behind(
    config: DayzConfig,
    map_id: str,
    choices: SwitchChoices,
    log: Callable[[str], None],
    *,
    mark_enabled: Callable[[str], object] | None = None,
) -> dict:
    """Carry out the dialog's choices on the mod list and the manual files.

    Runs at the start of a switch's mod-list step, while the old mission is
    still the configured one: a mod turned off here has its CE entries parked
    in the old mission and its direct blocks stripped, the way the Mods page's
    Off does, and the new mission's merge skips it. Mods are server-wide, so
    it stays off on every map until it is turned on again.

    Also turns on the new map's own mods and deps that are installed but off
    (plans/83 Q4), through the enable path.
    *mark_enabled* is how the web saves that through ``WebState.mutate_config``;
    without it ``mods.set_mod_enabled`` saves the config itself.

    Returns ``{"left_mods", "left_manual_files", "turned_on"}``. Lines are
    markup-free.
    """
    from app.modmgr import mods as mods_mod
    from app.xml import xml_manual

    plan = switch_plan(config, map_id)
    turned_on: list[str] = []
    for row in plan["map_mods"]:
        if not row["turn_on"]:
            continue
        name = row["name"]
        if mark_enabled is None:
            mods_mod.set_mod_enabled(config, name, log=log)
        else:
            mark_enabled(name)
            mods_mod.apply_mod_state_to_mission_xml(config, name, enable=True, log=log)
        turned_on.append(name)
        log(f"  Turned on {name}: {map_id} needs it.")

    active = set(modlist.active(config))
    left_mods: list[str] = []
    for name in _chosen_mods(choices):
        if name not in active:
            log(f"  {name} is already off; nothing to turn off.")
            continue
        mods_mod.set_mod_disabled(config, name, log=log)
        left_mods.append(name)
        log(
            f"  Turned off {name}: still installed, off on every map until you turn it on"
            f" (Mods page or `zsm mods enable {name}`)."
        )

    left_files: list[str] = []
    from_terrain = plan["from_terrain"]
    files = {f["id"]: f for f in plan["manual_files"]}
    for file_id in dict.fromkeys(choices.leave_manual_files):
        row = files.get(file_id)
        if row is None or not row["can_leave"]:
            log(f"  Manual XML file {file_id} changed since the check; it was left as it is.")
            continue
        try:
            xml_manual.update_file(
                file_id, manual_dir=config.paths.custom_xml_dir, terrain=from_terrain,
            )
        except xml_manual.ManualFileError as exc:
            log(f"  Manual XML file {file_id} was left as it is: {exc}")
            continue
        left_files.append(file_id)
        log(f"  Manual XML file {file_id} stays with {_old_map_label(plan)}.")

    return {"left_mods": left_mods, "left_manual_files": left_files, "turned_on": turned_on}
