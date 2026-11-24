"""Shared business logic for mod operations.

Used by both app/commands/mods.py (Click CLI) and
app/web/routers/mods.py (web UI).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.shared.config import DayzConfig
from app.ops.fileops import remove_mod_from_server
from app.modmgr.mods import (
    get_current_mission,
    is_placeholder_mod_name,
    is_workshop_id,
)
from app.modmgr import modlist
from app.xml.xml_economy import remove_disabled_economy_entries, remove_mod_economy_entries
from app.xml.xml_merge import remove_mod_xml_blocks


# ── Naming helpers ─────────────────────────────────────────────────

# TraderPlus workshop ID — used to detect a TraderPlus install so its default
# configs / @CF dependency can be offered. Used by the web install job and the
# supported-mods list.
TRADERPLUS_WORKSHOP_ID = "2458896948"


def ensure_at_prefix(name: str) -> str:
    """Ensure mod name starts with '@'."""
    return name if name.startswith("@") else f"@{name}"


# What a new mod name is written into uses as separators: `;` ends an entry
# in the launch command's -mod=, `=` split the `name=id` lines of the
# mods.txt and cached-mod-ids.txt zsm kept before plans/77 (still refused,
# so a name stays usable in a `name=id` pair), and `--` cannot stand inside
# the XML comment that parks a disabled mod's CE entries. Refused in a Workshop
# title only; a folder already named with one is left as it is.
TITLE_SEPARATORS = (";", "=", "--")


def title_mod_name(title: str) -> str | None:
    """The ``@Name`` a Workshop title gives a mod, or None when it cannot be one.

    The title is the mod author's. The name has to be one mod folder and one
    line (`is_safe_mod_dir_name`), one entry the launch command accepts
    (`service_control.mod_name_problem`): a name that fails the second installs,
    and then the server's unit refuses to render, and free of
    `TITLE_SEPARATORS`, which split or break the files the name is written to.
    """
    from app.modmgr.mods import is_safe_mod_dir_name
    from app.server.service_control import mod_name_problem

    name = ensure_at_prefix(title)
    if any(sep in name for sep in TITLE_SEPARATORS):
        return None
    if is_safe_mod_dir_name(name) and mod_name_problem(name) is None:
        return name
    return None


def is_traderplus_installed(
    installed_names: list[str],
    cached_ids: dict[str, str] | None = None,
) -> bool:
    """True if any name is @TraderPlus (by name or resolved workshop ID)."""
    if any(n.lower().lstrip("@") == "traderplus" for n in installed_names):
        return True
    if cached_ids:
        return any(
            cached_ids.get(n) == TRADERPLUS_WORKSHOP_ID
            or cached_ids.get(n.lstrip("@")) == TRADERPLUS_WORKSHOP_ID
            for n in installed_names
        )
    return False


def list_known_mods(config: DayzConfig) -> list[tuple[str, str]]:
    """Return sorted ``(@name, workshop_id)`` for every known mod.

    Union of the seed registry, the learned Workshop IDs (workshop-ids.json),
    and the workshop download
    folder — the candidate set for the web "Search" install tab. Deduped by
    ``@name`` (first source wins).
    """
    from app.shared.seed_data import parse_workshop_mod_ids

    by_name: dict[str, str] = {}
    for wid, name in parse_workshop_mod_ids().items():
        by_name.setdefault(ensure_at_prefix(name), wid)
    for name, wid in modlist.workshop_ids(config).items():
        by_name.setdefault(ensure_at_prefix(name), wid)
    for name, wid in get_workshop_mods(config):
        if name:
            by_name.setdefault(ensure_at_prefix(name), wid)
    # A `@<digits>` placeholder for an id that also has a real name is the
    # same mod, and was offered again under its number (F18). Existing
    # cache files keep such lines, so the reader drops them too.
    named = {wid for name, wid in by_name.items() if not is_placeholder_mod_name(name)}
    return sorted(
        (name, wid) for name, wid in by_name.items()
        if not (is_placeholder_mod_name(name) and wid in named)
    )


# ── Workshop directory scanning ────────────────────────────────────


def get_workshop_mods(config: DayzConfig) -> list[tuple[str, str]]:
    """Scan workshop dir and return [(display_name, workshop_id), ...].

    Names are resolved from seed data and cached IDs.
    """
    from app.modmgr import modlist
    from app.shared.seed_data import parse_workshop_mod_ids

    workshop_dir = config.workshop_dir
    if not workshop_dir.is_dir():
        return []

    cached_ids = modlist.workshop_ids(config)
    seed_ids = dict(parse_workshop_mod_ids())
    id_to_name: dict[str, str] = dict(seed_ids)
    for name_key, wid in cached_ids.items():
        id_to_name[wid] = name_key

    result: list[tuple[str, str]] = []
    for entry in sorted(workshop_dir.iterdir()):
        if entry.is_dir() and entry.name.isdigit():
            wid = entry.name
            name = id_to_name.get(wid, "")
            result.append((name, wid))
    return result


# ── Resolve Workshop ID ─────────────────────────────────────────────


def resolve_mod_workshop_id(config: DayzConfig, name: str) -> tuple[str | None, bool]:
    """Look up or resolve workshop ID for a mod.

    Returns (mod_id, was_cached).  Writes to cache if newly resolved.
    """
    from app.shared.steam import resolve_mod_id

    cached_ids = modlist.workshop_ids(config)
    mod_id = cached_ids.get(name) or cached_ids.get(name.lstrip("@"))
    if mod_id:
        return mod_id, True

    # What zsm ships, before the network. @DayZ-Expansion-AI was in the registry
    # while a search page zsm could no longer read titles from turned it into
    # "No workshop ID found — skipping", and a mod we know the ID of must never
    # depend on scraping to be installable.
    known = registry_mod_ids().get(ensure_at_prefix(name))
    if known:
        modlist.remember_id(config, ensure_at_prefix(name), known)
        return known, True

    mod_id = resolve_mod_id(name, set(cached_ids.keys()))
    if mod_id:
        modlist.remember_id(config, name, mod_id)
    return mod_id, False


def search_mod_workshop(config: DayzConfig, query: str) -> list[tuple[str, str]]:
    """Search Steam Workshop for mods matching query.

    Returns [(workshop_id, title), ...]. Caches all found results
    in workshop-ids.json for future lookups.
    """
    from app.shared.steam import search_workshop

    results = search_workshop(query)
    for mod_id, title in results:
        if not title or title.startswith("Unknown"):
            continue
        # The title is the author's: only one that can name a mod is cached.
        name = title_mod_name(title)
        if name:
            modlist.remember_id(config, name, mod_id)
    return results


def known_server_side(names: list[str], cached_ids: dict[str, str]) -> list[str]:
    """Which of *names* are mods the catalog knows belong in ``-serverMod=``.

    Keyed by workshop ID (:data:`app.shared.seed_data.SERVER_SIDE_MOD_IDS`), so a
    mod installed under another folder name is still recognised.
    """
    from app.shared.seed_data import SERVER_SIDE_MOD_IDS

    by_name = resolve_names_to_ids(list(names), cached_ids)
    return [n for n in names if by_name.get(n) in SERVER_SIDE_MOD_IDS]


# ── Uninstall ────────────────────────────────────────────────────────


def uninstall_mods(
    config: DayzConfig,
    mods_to_remove: list[str],
    on_mod: callable | None = None,
    log: callable | None = None,
) -> None:
    """Remove mods from the mod list and clean up server files.

    Calls on_mod(mod_name) for each mod being removed, if provided.

    A mod's own `<profiles>/<Name>/` directory is deliberately NOT deleted —
    it holds live server data (TraderPlus stock/banking, garage vehicles) that
    has to survive an uninstall/reinstall round trip. It is reported through
    *log* instead, since an unexplained leftover reads as a bug; Cleanup's
    "Leftover mod data" card is where it gets deleted.
    """
    from app.ops.cleanup_ops import find_mod_profile_data

    # The flags go with the entry; a pinned load-order name is left and
    # filtered out wherever the order is resolved.
    modlist.remove(config, mods_to_remove)

    mission = get_current_mission(config.server_cfg_path)

    for mod in mods_to_remove:
        if on_mod:
            on_mod(mod)
        remove_mod_from_server(config, mod)
        if mission:
            remove_mod_xml_blocks(config.server_dir_path, mission, mod)
            remove_mod_economy_entries(config.server_dir_path, mission, mod)
            remove_disabled_economy_entries(config.server_dir_path, mission, mod)
        if log:
            leftover = find_mod_profile_data(config, mod)
            if leftover:
                log(
                    f"Kept {leftover.path} ({leftover.files} file(s)) — server data "
                    f"written by {mod}, not deleted with it. Remove it from "
                    f"Cleanup > Leftover mod data if you don't need it."
                )


# ── Dependency helpers ───────────────────────────────────────────────


def resolve_names_to_ids(
    names: list[str],
    cached_ids: dict[str, str],
) -> dict[str, str]:
    """Map mod names to workshop IDs using cache + seed data.

    Returns {name: workshop_id} for every name that could be resolved.
    """
    from app.shared.seed_data import parse_workshop_mod_ids

    seed_name_to_id = {name: wid for wid, name in parse_workshop_mod_ids().items()}

    result: dict[str, str] = {}
    for name in names:
        wid = (
            cached_ids.get(name)
            or cached_ids.get(name.lstrip("@"))
            or seed_name_to_id.get(name)
            or seed_name_to_id.get(name.lstrip("@"))
        )
        if wid:
            result[name] = wid
    return result


def get_missing_deps(
    installing_ids: list[str],
    installed_ids: set[str],
) -> list[tuple[str, str]]:
    """Return ``(workshop_id, dep_name)`` pairs for deps not yet installed.

    Checks ``MOD_DEPS`` for every mod in *installing_ids* whose deps
    are neither already installed nor part of this install batch.
    """
    from app.shared.seed_data import MOD_DEPS

    missing: list[tuple[str, str]] = []
    seen: set[str] = set()
    all_ids = installed_ids | set(installing_ids)

    for wid in installing_ids:
        entry = MOD_DEPS.get(wid)
        if not entry:
            continue
        for dep_id, dep_name in entry["deps"].items():
            if dep_id not in all_ids and dep_id not in seen:
                missing.append((dep_id, dep_name))
                seen.add(dep_id)

    return missing


def get_removable_deps(
    removing_ids: list[str],
    all_installed_ids: set[str],
) -> list[tuple[str, str]]:
    """Return ``(workshop_id, dep_name)`` pairs for deps safe to remove.

    A dep is removable when it is currently installed, freed by this
    removal, and not required by any mod that will remain.
    """
    from app.shared.seed_data import MOD_DEPS

    remaining = all_installed_ids - set(removing_ids)

    # Collect deps of the mods being removed that are currently installed
    candidate_deps: dict[str, str] = {}
    for wid in removing_ids:
        entry = MOD_DEPS.get(wid)
        if not entry:
            continue
        for dep_id, dep_name in entry["deps"].items():
            if dep_id in remaining:
                candidate_deps[dep_id] = dep_name

    # Keep only deps not still needed by a remaining mod
    removable: list[tuple[str, str]] = []
    for dep_id, dep_name in candidate_deps.items():
        still_needed = any(
            dep_id in MOD_DEPS.get(rwid, {}).get("deps", {})
            for rwid in remaining
            if rwid != dep_id
        )
        if not still_needed:
            removable.append((dep_id, dep_name))

    return removable


def resolve_workshop_deps(
    mod_ids: list[str],
    api_key: str,
    *,
    cache_path: Path | None = None,
    timeout: int = 30,
    max_depth: int = 6,
    ttl: int = 7 * 86400,
) -> dict[str, str]:
    """Resolve the transitive Workshop dependency tree of *mod_ids*.

    Returns ``{dep_id: "@Title"}`` for every dependency reachable through the
    Steam "Required Items" graph, excluding the input ids themselves. Results
    are cached to *cache_path* (``{id: {children, title, fetched_at}}``).
    Returns ``{}`` with no api_key or on network failure (offline-safe).
    """
    import json
    import time

    from app.modmgr import workshop_api

    if not api_key or not mod_ids:
        return {}

    cache = _load_deps_cache(cache_path)
    now = int(time.time())

    def _fresh(cid: str) -> bool:
        entry = cache.get(cid)
        # `answered`: Steam really replied for this id. Entries written before
        # plans/50 B lack it and are re-fetched once: every lookup then was a
        # POST, which Steam answers 405, and each was cached as "no
        # dependencies" for the whole TTL.
        return (
            bool(entry)
            and bool(entry.get("answered"))
            and "children" in entry
            and (now - entry.get("fetched_at", 0)) <= ttl
        )

    def _children_of(ids: list[str]) -> dict[str, list[str]]:
        misses = [i for i in ids if not _fresh(i)]
        if misses:
            fetched = workshop_api.get_workshop_children(misses, api_key, timeout)
            for i in misses:
                if i not in fetched:
                    # No answer (offline, an error, a hidden item): cache
                    # nothing rather than a week of "no dependencies". An older
                    # entry, if any, still answers below.
                    continue
                entry = cache.setdefault(i, {})
                entry["children"] = fetched[i]
                entry["fetched_at"] = now
                entry["answered"] = True
                entry.setdefault("title", "")
        return {i: cache.get(i, {}).get("children", []) for i in ids}

    visited: set[str] = set(mod_ids)
    discovered: set[str] = set()
    frontier: list[str] = list(mod_ids)
    for _ in range(max_depth):
        if not frontier:
            break
        cmap = _children_of(frontier)
        nxt: list[str] = []
        for kids in cmap.values():
            for k in kids:
                if k not in visited:
                    visited.add(k)
                    discovered.add(k)
                    nxt.append(k)
        frontier = nxt

    # Name any discovered dependency we don't already have a title for.
    need_titles = [d for d in discovered if not cache.get(d, {}).get("title")]
    if need_titles:
        infos = workshop_api.get_workshop_details(need_titles, api_key, timeout)
        for d in need_titles:
            entry = cache.setdefault(d, {"children": [], "fetched_at": now})
            entry["title"] = infos[d].title if d in infos else ""

    if cache_path:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cache, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass

    return {d: _dep_name(cache, d) for d in discovered}


def workshop_deps_cache() -> Path:
    """Where :func:`resolve_workshop_deps` keeps Steam's answers (machine-wide)."""
    from app.shared import paths

    return paths.CONFIG_DIR / "workshop_deps.json"


def _load_deps_cache(cache_path: Path | None) -> dict[str, dict]:
    import json

    if not cache_path or not cache_path.is_file():
        return {}
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return cache if isinstance(cache, dict) else {}


def _dep_name(cache: dict[str, dict], dep_id: str) -> str:
    # The title is the author's and becomes the dependency's folder name, an
    # entry in mods.json and workshop-ids.json, and an entry in the launch
    # command. One that cannot be all of them counts as no title.
    title = cache.get(dep_id, {}).get("title")
    return (title and title_mod_name(title)) or f"@Mod {dep_id}"


def cached_workshop_requirements(
    mod_ids: list[str], cache_path: Path | None = None,
) -> list[tuple[str, str, str]]:
    """``(parent_id, dep_id, dep_name)`` for each Required Item Steam listed
    for *mod_ids*, read from the cache only: no request is made.

    The conflict scan runs before every CLI command and in the notifier, so it
    must not wait on Steam; :func:`resolve_workshop_deps` fills the cache.
    """
    cache = _load_deps_cache(cache_path or workshop_deps_cache())
    out: list[tuple[str, str, str]] = []
    for parent in mod_ids:
        for dep in cache.get(parent, {}).get("children", []) or []:
            if is_workshop_id(str(dep)):
                out.append((parent, str(dep), _dep_name(cache, str(dep))))
    return out


def get_missing_deps_ex(
    config: DayzConfig,
    installing_ids: list[str],
    installed_ids: set[str],
) -> list[tuple[str, str]]:
    """Missing deps from the seed map ∪ the Expansion catalog ∪ the Workshop graph.

    Superset of :func:`get_missing_deps`: adds the DayZ-Expansion module graph
    (measured from the PBOs — ``MOD_DEPS`` has no Expansion entries, so installing
    ``-Vehicles`` by ID alone would otherwise pull nothing), then author-declared
    Workshop "Required Items" when a Steam API key is configured. Falls back to
    the offline sources alone with no key. Same ``(workshop_id, dep_name)`` shape.
    """
    from app.modmgr import expansion

    missing: dict[str, str] = dict(get_missing_deps(installing_ids, installed_ids))

    have = set(installed_ids) | set(installing_ids)
    for dep_id in expansion.required_for(installing_ids):
        dep_name = expansion.module_name(dep_id)
        if dep_name and dep_id not in have and dep_id not in missing:
            missing[dep_id] = dep_name

    api_key = getattr(config, "steam_api_key", "") or ""
    if api_key:
        try:
            dep_map = resolve_workshop_deps(
                list(installing_ids), api_key, cache_path=workshop_deps_cache()
            )
        except Exception:
            dep_map = {}
        have = set(installed_ids) | set(installing_ids)
        for dep_id, dep_name in dep_map.items():
            if dep_id not in have and dep_id not in missing:
                missing[dep_id] = dep_name

    return list(missing.items())


# ── Mod Info ─────────────────────────────────────────────────────────


@dataclass
class ModInfo:
    name: str
    workshop_id: str
    installed: bool


def get_mod_info_list(config: DayzConfig) -> list[ModInfo]:
    """Get mod list with workshop IDs and install status."""
    mod_list = modlist.installed(config)
    cached_ids = modlist.workshop_ids(config)

    result = []
    for mod in mod_list:
        mod_id = cached_ids.get(mod.lstrip("@"), cached_ids.get(mod, ""))
        mod_dir = config.server_dir_path / mod
        result.append(ModInfo(
            name=mod,
            workshop_id=mod_id,
            installed=mod_dir.is_dir(),
        ))
    return result


@dataclass
class ModRef:
    """One mod, resolved from a user-supplied reference.

    ``listed``/``installed`` mirror ``get_mod_info_list`` (in the mod list /
    in the mod list *and* the folder exists); ``cached`` means the SteamCMD workshop
    item dir is on disk. A mod can be any combination of the three.
    """

    name: str = ""
    workshop_id: str = ""
    listed: bool = False
    installed: bool = False
    cached: bool = False
    workshop_path: Path | None = None


def registry_mod_ids() -> dict[str, str]:
    """Every ``@name -> workshop_id`` zsm ships knowledge of.

    The workshop-mod-ids registry plus the per-map mod/dependency lists — a few
    map mods (e.g. @BanovFrost) only appear in the latter, and the mod page
    links to them from the Maps page.
    """
    from app.shared.seed_data import WORKSHOP_MAPS, parse_workshop_mod_ids

    by_name: dict[str, str] = {}
    for wid, name in parse_workshop_mod_ids().items():
        by_name.setdefault(ensure_at_prefix(name), wid)
    for data in WORKSHOP_MAPS.values():
        for name, wid in {**data.get("mods", {}), **data.get("deps", {})}.items():
            by_name.setdefault(ensure_at_prefix(name), wid)
    return by_name


def name_for_workshop_id(
    workshop_id: str,
    id_to_name: dict[str, str],
    *,
    hint: str | None = None,
) -> str:
    """Best ``@Name`` for *workshop_id*: server cache, then registry, then *hint*.

    Falls back to ``@<id>``, which is what the mod folder is called when nobody
    knows better. Placeholder names are skipped in the cache, so a server that
    installed a mod before zsm shipped its ID heals on the next install.
    """
    cached = id_to_name.get(workshop_id, "")
    if cached and not is_placeholder_mod_name(cached):
        return ensure_at_prefix(cached)
    known = next((n for n, wid in registry_mod_ids().items() if wid == workshop_id), "")
    return ensure_at_prefix(known or hint or workshop_id)


def resolve_mod_ref(config: DayzConfig, ref: str) -> ModRef | None:
    """Resolve a mod name (``@CF``) *or* a workshop ID (``1559212036``).

    The web mod page's URL segment accepts either, so this normalizes both to
    the same record. Offline only — name→ID goes through the cache and the seed
    registry, never ``resolve_mod_workshop_id`` (a page load must not hit the
    Steam network). Returns ``None`` when the reference names nothing zsm
    knows about, or when it isn't safe to join onto a directory.
    """
    from app.modmgr.mods import is_safe_mod_dir_name

    ref = (ref or "").strip()
    if not ref:
        return None

    cached_ids = modlist.workshop_ids(config)
    registry = registry_mod_ids()
    name = ""
    workshop_id = ""

    if ref.isdigit():
        workshop_id = ref
        # Reverse lookup: the learned IDs first, then what zsm ships.
        for cname, cwid in cached_ids.items():
            if cwid == workshop_id:
                name = ensure_at_prefix(cname)
                break
        if not name:
            name = next((n for n, wid in registry.items() if wid == workshop_id), "")
    else:
        name = ensure_at_prefix(ref)
        workshop_id = resolve_names_to_ids([name], cached_ids).get(name, "") or registry.get(name, "")

    # Guard before any path join — `ref` arrives from a URL path segment.
    if name and not is_safe_mod_dir_name(name):
        return None
    if workshop_id and not workshop_id.isdigit():
        return None
    if not name and not workshop_id:
        return None

    listed = False
    installed = False
    if name:
        for info in get_mod_info_list(config):
            if info.name == name:
                listed = True
                installed = info.installed
                if not workshop_id:
                    workshop_id = info.workshop_id
                break

    workshop_path = None
    if workshop_id:
        candidate = config.workshop_dir / workshop_id
        if candidate.is_dir():
            workshop_path = candidate

    # Known = in the mod list, downloaded, or a name/ID pair the registry knows.
    # A bare name zsm has never heard of (or a random numeric id that isn't on
    # disk) is not a mod — the caller 404s rather than rendering an empty page.
    if not listed and workshop_path is None and not (name and workshop_id):
        return None

    return ModRef(
        name=name,
        workshop_id=workshop_id,
        listed=listed,
        installed=installed,
        cached=workshop_path is not None,
        workshop_path=workshop_path,
    )


# ── Mod folder browsing (read-only) ────────────────────────────────

# Extensions the mod page will render inline. Everything else is listed by
# name only — a .pbo/.bikey is binary and a mod dir is SteamCMD's anyway, so
# nothing here is ever editable.
VIEWABLE_MOD_FILE_LANGUAGES = {
    ".xml": "xml",
    ".json": "json",
    ".txt": "txt",
    ".md": "txt",
}
# A mod dir is arbitrary third-party content: cap both the walk and the read so
# a pathological mod can't stall a page load or push a megabyte into a toast.
MAX_MOD_FILES = 2000
MAX_MOD_FILE_BYTES = 1_000_000


def mod_file_language(name: str) -> str:
    """Highlight language for a mod file, or "" if it isn't viewable."""
    return VIEWABLE_MOD_FILE_LANGUAGES.get(Path(name).suffix.lower(), "")


def list_mod_files(base: Path, limit: int = MAX_MOD_FILES) -> tuple[list[dict], bool]:
    """Every file under *base*, as ``{path, size, language}`` rows + truncated.

    ``path`` is POSIX-relative to *base* (the key the read endpoint takes back).
    ``language`` is "" for the files the UI only names. Sorted by path so the
    listing is stable; returns ``truncated=True`` once *limit* rows are in.
    """
    if not base or not base.is_dir():
        return [], False

    rows: list[dict] = []
    truncated = False
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if len(rows) >= limit:
            truncated = True
            break
        rel = path.relative_to(base).as_posix()
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        rows.append({"path": rel, "size": size, "language": mod_file_language(path.name)})
    return rows, truncated


def mod_file_path(base: Path, rel: str) -> Path | None:
    """Resolve a listing row's ``path`` back to a file under *base*, or None.

    ``rel`` arrives from a query string, so it is re-derived rather than
    trusted: no absolute path, no ``..``, and the resolved file must still sit
    inside *base* (which also rules out a symlink pointing out of the mod dir).
    """
    if not base or not rel:
        return None
    candidate = Path(rel.replace("\\", "/"))
    if candidate.is_absolute() or ".." in candidate.parts:
        return None

    root = base.resolve()
    path = (root / candidate).resolve()
    if not path.is_file():
        return None
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


def detect_mod_updates(config: DayzConfig) -> "UpdateCheck":
    """Check which mods have a Workshop version newer than the installed copy.

    Single source of truth for the update check shared by the
    ``mods check-updates`` CLI command, the web Updates tab, and the background
    notification loop. Reads mod IDs from the mod list, resolves local timestamps
    from the Workshop ACF (falling back to stored timestamps), then queries the
    Steam API. Returns an ``UpdateCheck``: an empty ``updates`` means
    "checked, and nothing is newer". A check that could not run (no API key,
    key rejected, Steam unreachable) raises ``UpdateCheckError`` instead.
    """
    from app.parsers.acf_parser import get_installed_timestamps, parse_acf_file
    from app.modmgr.workshop_api import check_for_updates, load_stored_timestamps
    from app.shared.paths import CONFIG_DIR

    mod_ids = list(dict.fromkeys(
        m.workshop_id for m in get_mod_info_list(config) if m.workshop_id
    ))

    local_ts: dict[str, int] = {}
    acf_path = config.workshop_acf_path
    if mod_ids and acf_path.is_file():
        try:
            local_ts = get_installed_timestamps(parse_acf_file(acf_path))
        except Exception:
            pass
    if mod_ids and not local_ts:
        local_ts = load_stored_timestamps(CONFIG_DIR / "mod_timestamps.json")

    return check_for_updates(mod_ids, local_ts, config.steam_api_key)
