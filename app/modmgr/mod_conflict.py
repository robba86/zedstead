"""Mod conflict detection — scan for common mod issues.

Used by the web Mods page's Conflicts tab (``app/web/routers/mods.py``) and
by ``zsm doctor``.
All functions take explicit config: DayzConfig — no CWD assumptions.
"""

from __future__ import annotations

import json
import re
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from app.shared.rap import (
    RAP_ROOT_OFFSET as _RAP_ROOT_OFFSET,
    class_entries as _rap_class_entries,
    read_compressed_int as _read_rap_compressed_int,
    skip_array as _rap_skip_array,
)
from app.shared.zip_utils import is_bare_name

if TYPE_CHECKING:
    from app.shared.config import DayzConfig


# ── Data structures ──────────────────────────────────────────────────


@dataclass
class ConflictItem:
    """A single detected issue."""

    severity: str  # "error", "warning", "info"
    category: str  # "rpt_error", "missing_dep", "missing_bikey", "required_addon", "load_order", "no_logs"
    message: str
    mod_name: str = ""
    detail: str = ""
    # Only set for "missing_dep": the installable mod that is missing. Keeps the
    # dep addressable (web Conflicts tab installs it by id) instead of leaving it
    # buried in the message string.
    dep_id: str = ""
    dep_name: str = ""


@dataclass
class ScanResult:
    """Aggregated output from a full conflict scan."""

    items: list[ConflictItem] = field(default_factory=list)
    rpt_file: str = ""
    mods_scanned: int = 0
    mod_list: list[str] = field(default_factory=list)
    scan_duration_ms: int = 0
    pbo_debug: PboScanDebug | None = None
    # Per-check status: {check_name: (passed: bool, detail: str)}
    check_statuses: dict[str, tuple[bool, str]] = field(default_factory=dict)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.items if i.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.items if i.severity == "warning")


# ── RPT error patterns ──────────────────────────────────────────────

_RPT_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"Script module '([^']+)' is not registered"), "Unregistered script module", "error"),
    (re.compile(r"Cannot compile"), "Script compile error", "error"),
    (re.compile(r"cannot create type"), "Class conflict", "error"),
    (re.compile(r"Object\s+(\S+)\s+not found"), "Missing object reference", "error"),
    (re.compile(r"Addon '([^']+)' requires addon '([^']+)'"), "Missing addon dependency", "error"),
    (re.compile(r"Duplicate addon"), "Duplicate addon conflict", "warning"),
    (re.compile(r"Cannot open file"), "Missing mod file", "warning"),
    (re.compile(r"C\d+:\s*can't find variable"), "Script variable error", "error"),
]

_MAX_RPT_BYTES = 10_000_000  # 10 MB cap
_MAX_ITEMS = 50  # stop after this many unique issues
_MOD_PATH_RE = re.compile(r"@([^/\\]+)")


# ── Helpers ──────────────────────────────────────────────────────────


def newest_rpt(profiles_dir: Path) -> Path | None:
    """Return the most recently modified .RPT file, or None."""
    if not profiles_dir.is_dir():
        return None
    rpts = [f for f in profiles_dir.iterdir() if f.is_file() and f.suffix.upper() == ".RPT"]
    if not rpts:
        return None
    rpts.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return rpts[0]


def _extract_mod_from_context(lines: list[str], line_idx: int) -> str:
    """Look back up to 5 lines for an @ModName path reference."""
    start = max(0, line_idx - 5)
    for i in range(line_idx, start - 1, -1):
        m = _MOD_PATH_RE.search(lines[i])
        if m:
            return f"@{m.group(1)}"
    return ""


# ── Check 1: RPT log errors ─────────────────────────────────────────


def check_rpt_errors(profiles_dir: Path) -> tuple[list[ConflictItem], str]:
    """Scan the most recent .RPT file for known error patterns.

    Returns (items, rpt_filename). The rpt_filename is "" if no RPT exists.
    """
    rpt = newest_rpt(profiles_dir)
    if rpt is None:
        return [], ""

    items: list[ConflictItem] = []
    seen: set[tuple[str, str]] = set()  # (category, mod_name) dedup

    try:
        with open(rpt, "r", errors="ignore") as f:
            lines: list[str] = []
            bytes_read = 0
            for raw_line in f:
                bytes_read += len(raw_line)
                lines.append(raw_line)
                if bytes_read > _MAX_RPT_BYTES:
                    break

        for idx, line in enumerate(lines):
            if len(items) >= _MAX_ITEMS:
                break
            for pattern, category, severity in _RPT_PATTERNS:
                m = pattern.search(line)
                if not m:
                    continue
                mod_name = _extract_mod_from_context(lines, idx)
                key = (category, mod_name)
                if key in seen:
                    continue
                seen.add(key)
                items.append(ConflictItem(
                    severity=severity,
                    category="rpt_error",
                    message=category,
                    mod_name=mod_name,
                    detail=line.strip()[:200],
                ))
    except OSError:
        pass

    return items, rpt.name


# ── Check 2: Missing dependencies ───────────────────────────────────


def check_missing_deps(config: DayzConfig, *, fetch_workshop: bool = False) -> list[ConflictItem]:
    """Check active (enabled) mods for missing required dependencies.

    Two sources: ``MOD_DEPS``, and the Workshop "Required Items" Steam listed
    for each mod, read from ``workshop_deps.json``. Many authors declare a
    dependency only there, not in requiredAddons (@DayZ-Bicycle needs
    @Survivor Animations and segfaults the server without it). The cache is
    refreshed only with ``fetch_workshop`` and a Steam API key: this check runs
    before every CLI command and in the notifier, which must not wait on Steam.
    """
    from app.modmgr.mod_ops import (
        cached_workshop_requirements,
        get_missing_deps,
        resolve_names_to_ids,
        resolve_workshop_deps,
        workshop_deps_cache,
    )
    from app.modmgr import modlist
    from app.shared.seed_data import MOD_DEPS

    mods = modlist.active(config)
    if not mods:
        return []

    cached_ids = modlist.workshop_ids(config)
    name_to_id = resolve_names_to_ids(mods, cached_ids)
    installed_ids = set(name_to_id.values())
    missing = get_missing_deps(list(installed_ids), installed_ids)

    items: list[ConflictItem] = []
    for dep_id, dep_name in missing:
        # Find which parent mod needs this dep
        parent = ""
        for wid, entry in MOD_DEPS.items():
            if dep_id in entry.get("deps", {}):
                parent = entry.get("name", "")
                break
        items.append(ConflictItem(
            severity="error",
            category="missing_dep",
            message=f"{dep_name} required by {parent}" if parent else f"{dep_name} is missing",
            mod_name=parent,
            dep_id=dep_id,
            dep_name=dep_name,
        ))

    api_key = getattr(config, "steam_api_key", "") or ""
    if fetch_workshop and api_key:
        try:
            resolve_workshop_deps(sorted(installed_ids), api_key, cache_path=workshop_deps_cache())
        except Exception:
            pass  # offline: the cached answers below still count
    id_to_name = {wid: name for name, wid in name_to_id.items()}
    reported = {item.dep_id for item in items}
    for parent_id, dep_id, dep_name in cached_workshop_requirements(sorted(installed_ids)):
        if dep_id in installed_ids or dep_id in reported or dep_name in mods:
            continue
        reported.add(dep_id)
        parent = id_to_name.get(parent_id, "")
        items.append(ConflictItem(
            severity="error",
            category="missing_dep",
            message=f"{dep_name} required by {parent} (Workshop)",
            mod_name=parent,
            dep_id=dep_id,
            dep_name=dep_name,
        ))
    return items


# ── Check 2b: Manual load order ─────────────────────────────────────


def check_load_order(config: DayzConfig) -> list[ConflictItem]:
    """One warning per mod that a manual load order puts before a mod it
    requires (`mods.load_order_breaks`). The automatic order rules it out."""
    from app.modmgr import modlist
    from app.modmgr.mods import load_order_breaks

    if not modlist.load(config).manual:
        return []
    return [
        ConflictItem(
            severity="warning",
            category="load_order",
            message=f"{mod} loads before {dep}, which it requires",
            mod_name=mod,
        )
        for mod, dep in load_order_breaks(config)
    ]


# ── Check 3: Missing bikeys ─────────────────────────────────────────


def check_missing_bikeys(config: DayzConfig) -> list[ConflictItem]:
    """Check that each active (enabled) mod has its .bikey files in the keys directory."""
    from app.modmgr import modlist

    mods = modlist.active(config)
    keys_dir = config.keys_dir
    server_dir = config.server_dir_path
    items: list[ConflictItem] = []

    installed_bikeys: set[str] = set()
    if keys_dir.is_dir():
        installed_bikeys = {f.name.lower() for f in keys_dir.glob("*.bikey")}

    for mod_name in mods:
        mod_dir = server_dir / mod_name
        if not mod_dir.is_dir():
            continue
        mod_bikeys = list(mod_dir.rglob("*.bikey"))
        if not mod_bikeys:
            continue  # mod ships no bikeys
        missing = [b.name for b in mod_bikeys if b.name.lower() not in installed_bikeys]
        if missing:
            items.append(ConflictItem(
                severity="warning",
                category="missing_bikey",
                message=f"Missing bikey(s): {', '.join(missing[:3])}"
                + (f" (+{len(missing) - 3} more)" if len(missing) > 3 else ""),
                mod_name=mod_name,
            ))
    return items


# ── Check 4: requiredAddons from PBO config.bin ─────────────────────

# Vanilla DayZ addon prefixes — always available, skip these. CF's addons
# (``JM_CF_*``) and Expansion's (``DayZExpansion_*``) start the same way, so
# the checks and the load order below never see that a mod needs one of them;
# `_build_mod_dependency_graph(every_provider=True)` is for a caller that must.
_VANILLA_PREFIXES = ("DZ_", "JM_", "DayZ")

_CACHE_FILE = ".pbo_addon_cache.json"


def _get_mod_pbos(server_dir: Path, mod_name: str) -> list[Path]:
    """Find all PBO files in a mod directory.

    Checks addons/ and Addons/ first (fast), falls back to rglob. A name that
    is not one folder has none: before plans/77 mods.txt arrived verbatim in a
    bundle, and an absolute or ``..`` line joined onto *server_dir* was read
    outside it, its whole directory walked on every request. mods.json refuses
    such a name, and this check stays as the last line.
    """
    if not is_bare_name(mod_name):
        return []
    mod_dir = server_dir / mod_name
    if not mod_dir.is_dir():
        return []
    # Try common directory names first
    for name in ("addons", "Addons"):
        candidate = mod_dir / name
        if candidate.is_dir():
            pbos = list(candidate.glob("*.pbo"))
            if pbos:
                return pbos
    # Fallback: search entire mod directory
    return list(mod_dir.rglob("*.pbo"))


def _get_mod_pbo_mtime(server_dir: Path, mod_name: str) -> float:
    """Get the latest mtime of any PBO file in a mod directory."""
    latest = 0.0
    for pbo in _get_mod_pbos(server_dir, mod_name):
        try:
            latest = max(latest, pbo.stat().st_mtime)
        except OSError:
            continue
    return latest


def _load_pbo_cache(server_dir: Path, name: str = _CACHE_FILE) -> dict:
    """Load a PBO scan cache from disk. Returns {} on any error."""
    cache_path = server_dir / name
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_pbo_cache(server_dir: Path, cache: dict, name: str = _CACHE_FILE) -> None:
    """Save a PBO scan cache to disk."""
    cache_path = server_dir / name
    try:
        cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    except OSError:
        pass


def _scan_mod_pbos(
    server_dir: Path, mod_name: str,
) -> tuple[dict[str, str], list[str], int, int]:
    """Scan a single mod's PBOs.

    Returns (provided_addons, required_addons, pbo_count, config_count).
    """
    provided: dict[str, str] = {}
    required: list[str] = []
    pbos = _get_mod_pbos(server_dir, mod_name)
    if not pbos:
        return provided, required, 0, 0

    pbo_count = 0
    config_count = 0
    for pbo_path in pbos:
        pbo_count += 1
        data = _read_pbo_entry(pbo_path, "config.bin")
        if data is None:
            data = _read_pbo_entry(pbo_path, "config.cpp")
        if data is None:
            provided[pbo_path.stem.lower()] = mod_name
            continue

        config_count += 1
        addon_name = _extract_addon_name_binary(data)
        if addon_name:
            provided[addon_name.lower()] = mod_name
        provided[pbo_path.stem.lower()] = mod_name

        for req in _extract_required_addons_binary(data):
            required.append(req)

    return provided, required, pbo_count, config_count


def scan_mod_pbos_cached(
    server_dir: Path, mod_list: list[str],
) -> tuple[dict[str, str], dict[str, list[str]], int, int]:
    """Scan PBOs for all mods using cache. Returns:

    (provided_addons, mod_requirements, pbos_total, configs_total)
    Only re-scans mods whose PBOs have changed since last cache.
    """
    cache = _load_pbo_cache(server_dir)
    updated = False

    all_provided: dict[str, str] = {}
    all_requirements: dict[str, list[str]] = {}
    pbos_total = 0
    configs_total = 0

    for mod_name in mod_list:
        current_mtime = _get_mod_pbo_mtime(server_dir, mod_name)
        cached_entry = cache.get(mod_name)

        if cached_entry and cached_entry.get("mtime") == current_mtime and current_mtime > 0:
            # Use cached data
            for addon, provider in cached_entry.get("provided", {}).items():
                all_provided[addon] = provider
            reqs = cached_entry.get("requires", [])
            if reqs:
                all_requirements[mod_name] = reqs
            pbos_total += cached_entry.get("pbo_count", 0)
            configs_total += cached_entry.get("config_count", 0)
        else:
            # Scan fresh
            provided, required, pbo_count, config_count = _scan_mod_pbos(
                server_dir, mod_name,
            )
            all_provided.update(provided)
            if required:
                all_requirements[mod_name] = required
            pbos_total += pbo_count
            configs_total += config_count

            cache[mod_name] = {
                "mtime": current_mtime,
                "provided": dict(provided),
                "requires": required,
                "pbo_count": pbo_count,
                "config_count": config_count,
            }
            updated = True

    # Remove stale cache entries for mods no longer installed
    mod_set = set(mod_list)
    stale = [k for k in cache if k not in mod_set]
    for k in stale:
        del cache[k]
        updated = True

    if updated:
        _save_pbo_cache(server_dir, cache)

    return all_provided, all_requirements, pbos_total, configs_total


# ── Expansion AI factions registered by the installed mods ──────────

_FACTION_CACHE_FILE = ".pbo_faction_cache.json"
# Bump when the scan changes what an unchanged PBO yields (same rule as the
# classname cache below), so an older entry is re-read rather than trusted.
_FACTION_CACHE_VERSION = 1
# ``eAIRegisterFaction(eAIFactionMercenaries)`` registers "Mercenaries", the name
# Expansion AI's settings and Dynamic AI's ``Spatial_Faction`` refer to. Expansion
# ships 16 in ai_scripts.pbo, and other mods add their own, so the valid set is
# read from the install rather than tabled (plans/47 B).
_RE_FACTION = re.compile(rb"eAIRegisterFaction\(\s*eAIFaction(\w+)\s*\)")
_FACTION_CHUNK = 8 * 1024 * 1024


def _scan_pbo_factions(pbo_path: Path) -> set[str]:
    """Faction names one PBO registers. Read in chunks: asset PBOs run to a GB."""
    found: set[str] = set()
    tail = b""
    try:
        with open(pbo_path, "rb") as f:
            while chunk := f.read(_FACTION_CHUNK):
                data = tail + chunk
                if b"eAIRegisterFaction" in data:
                    found.update(m.decode("ascii", "ignore") for m in _RE_FACTION.findall(data))
                tail = data[-128:]
    except OSError:
        pass
    return found


def _per_mod_cached(
    server_dir: Path,
    mod_list: list[str],
    *,
    cache_file: str,
    version: int,
    key: str,
    scan: Callable[[Path], set[str]],
) -> dict[str, list[str]]:
    """``{mod: sorted values}``, where a mod's values are the union of *scan*
    over its PBOs.

    Cached per mod by PBO mtime in the same way as :func:`scan_mod_pbos_cached`,
    so only a changed mod is read again. Bump *version* when *scan* changes
    what an unchanged PBO yields.
    """
    cache = _load_pbo_cache(server_dir, cache_file)
    updated = False
    result: dict[str, list[str]] = {}
    for mod_name in mod_list:
        mtime = _get_mod_pbo_mtime(server_dir, mod_name)
        entry = cache.get(mod_name)
        if entry and entry.get("mtime") == mtime and mtime > 0 and entry.get("version") == version:
            result[mod_name] = list(entry.get(key, []))
            continue
        found: set[str] = set()
        for pbo in _get_mod_pbos(server_dir, mod_name):
            found |= scan(pbo)
        result[mod_name] = sorted(found)
        cache[mod_name] = {"mtime": mtime, "version": version, key: result[mod_name]}
        updated = True
    for stale in [k for k in cache if k not in set(mod_list)]:
        del cache[stale]
        updated = True
    if updated:
        _save_pbo_cache(server_dir, cache, cache_file)
    return result


def registered_factions(server_dir: Path, mod_list: list[str]) -> set[str]:
    """Every Expansion AI faction the given mods register.

    An empty result means nothing could be read, and callers must treat it as
    "unknown", not as "no faction is valid".
    """
    per_mod = _per_mod_cached(
        server_dir, mod_list, cache_file=_FACTION_CACHE_FILE, version=_FACTION_CACHE_VERSION,
        key="factions", scan=_scan_pbo_factions,
    )
    return {faction for values in per_mod.values() for faction in values}


# ── PBO prefixes: the path a script error names (plans/73) ──────────

_PREFIX_CACHE_FILE = ".pbo_prefix_cache.json"
_PREFIX_CACHE_VERSION = 1


def _read_pbo_prefix(pbo_path: Path) -> str:
    """The root path the engine mounts *pbo_path* under, ``/``-separated.

    That is the header's ``prefix`` property. A script error names its file
    under it, never under the ``@folder`` (measured, plans/73 step 0:
    ``@"Plan73Broken/Scripts/4_World/plan73_broken.c,5"`` from a PBO in
    ``@Plan73BrokenScript``). A PBO without the property is mounted under its
    own file name, which is the engine's rule, not a guess. "" when the file
    cannot be read as a PBO.
    """
    props: list[str] = []
    try:
        with open(pbo_path, "rb") as f:
            header = _PboHeaderReader(f)
            name = header.cstring()
            fields = header.take(20)
            if name is None or fields is None:
                return ""
            if not name and struct.unpack("<5I", fields)[0] == 0x56657273:
                while (value := header.cstring()) is not None and value:
                    props.append(value.decode("utf-8", errors="replace"))
    except OSError:
        return ""
    pairs = {k.lower(): v for k, v in zip(props[::2], props[1::2])}
    prefix = pairs.get("prefix", "").strip() or pbo_path.stem
    return prefix.replace("\\", "/").strip("/")


def mod_pbo_prefixes(server_dir: Path, mod_list: list[str]) -> dict[str, list[str]]:
    """``{mod: [prefix, ...]}``, one per PBO of each mod. Cached like the
    faction scan, so only a changed mod's headers are read again.

    Only single ``@`` folder names are read: the list can come from a server
    log's command line, and an absolute or ``..`` entry joined onto
    *server_dir* would read outside it.
    """
    from app.modmgr.mods import is_safe_mod_dir_name

    return _per_mod_cached(
        server_dir, [m for m in mod_list if is_safe_mod_dir_name(m)],
        cache_file=_PREFIX_CACHE_FILE, version=_PREFIX_CACHE_VERSION,
        key="prefixes", scan=lambda pbo: {p} if (p := _read_pbo_prefix(pbo)) else set(),
    )


# How much of a PBO's header is read at a time. A header is the file list
# only (a name and 20 bytes per entry), so one chunk usually holds all of it.
_PBO_HEADER_CHUNK = 64 * 1024
# Longer than any entry name or extension value a real PBO carries; a file
# whose "name" runs past it is not a PBO and is not read on.
_PBO_MAX_STRING = 64 * 1024


class _PboHeaderReader:
    """The bytes of a PBO's header, read on demand from an open file.

    Keeps only what has not been parsed yet, so memory follows the longest
    single string in the header, never the size of the file (plans/68 CON-10).
    """

    def __init__(self, f) -> None:
        self._f = f
        self._buf = bytearray()
        self._base = 0  # file offset of self._buf[0]
        self._eof = False

    def _fill(self) -> bool:
        if self._eof:
            return False
        chunk = self._f.read(_PBO_HEADER_CHUNK)
        if not chunk:
            self._eof = True
            return False
        self._buf += chunk
        return True

    def _drop(self, n: int) -> None:
        del self._buf[:n]
        self._base += n

    @property
    def offset(self) -> int:
        """File offset of the next unparsed byte."""
        return self._base

    def cstring(self) -> bytes | None:
        """The next null-terminated string, or None at end of file or when it
        would be longer than ``_PBO_MAX_STRING``."""
        start = 0
        while True:
            end = self._buf.find(b"\x00", start)
            if end != -1:
                value = bytes(self._buf[:end])
                self._drop(end + 1)
                return value
            start = len(self._buf)
            if start > _PBO_MAX_STRING or not self._fill():
                return None

    def take(self, n: int) -> bytes | None:
        """The next *n* bytes, or None when the file ends first."""
        while len(self._buf) < n:
            if not self._fill():
                return None
        value = bytes(self._buf[:n])
        self._drop(n)
        return value


def _locate_pbo_entry(f, target_lower: str) -> tuple[int, int] | None:
    """``(file offset, size)`` of the first entry named *target_lower*.

    PBO format: header entries (null-terminated name + 5x uint32), then
    an empty-name entry marking end of headers, followed by raw data blocks.
    """
    header = _PboHeaderReader(f)
    found: tuple[int, int] | None = None  # (offset into the data section, size)
    data_offset = 0
    while True:
        raw = header.cstring()
        if raw is None:
            break
        name = raw.decode("ascii", errors="ignore")
        # 5x uint32: packing_method, original_size, reserved, timestamp, data_size
        fields = header.take(20)
        if fields is None:
            break
        packing, _original_size, _reserved, _timestamp, data_size = struct.unpack("<5I", fields)
        if not name:
            if packing == 0x56657273:
                # Version entry (first in PBO): skip the extension headers,
                # null-terminated strings ending with an empty one.
                while (value := header.cstring()) is not None and value:
                    pass
                continue
            # Empty name + not version = end of header
            break
        norm = name.replace("\\", "/").lower()
        if found is None and (norm == target_lower or norm.endswith("/" + target_lower)):
            found = (data_offset, data_size)
        data_offset += data_size
    if found is None:
        return None
    # The data section starts right after the header.
    return header.offset + found[0], found[1]


def _read_pbo_entry(pbo_path: Path, target: str) -> bytes | None:
    """Read a file entry from a PBO archive by name. Returns bytes or None.

    Parses the header, then seeks to the one entry and reads only that, so a
    map PBO of a GB or more costs the size of its config, not of the file
    (plans/68 CON-10). `_scan_pbo_factions` reads in chunks for the same reason.
    """
    try:
        with open(pbo_path, "rb") as f:
            located = _locate_pbo_entry(f, target.lower())
            if located is None:
                return None
            offset, size = located
            f.seek(offset)
            return f.read(size)
    except OSError:
        return None


def _extract_required_addons_binary(data: bytes) -> list[str]:
    """Extract requiredAddons strings from binarized config.bin (raP format).

    raP array format after property name:
      compressed_int(element_count) + for each: type_byte(0x00=string) + string\\x00
    """
    results: list[str] = []
    marker = b"requiredAddons\x00"
    pos = 0
    while True:
        idx = data.find(marker, pos)
        if idx == -1:
            break
        arr_pos = idx + len(marker)

        # Read element count (compressed int)
        count, arr_pos = _read_rap_compressed_int(data, arr_pos)
        if count <= 0 or count > 200:
            pos = idx + 1
            continue

        # Read exactly `count` string elements
        for _ in range(count):
            if arr_pos >= len(data):
                break
            elem_type = data[arr_pos]
            arr_pos += 1
            if elem_type != 0x00:
                # Not a string element — skip (float=4 bytes, int=4 bytes)
                arr_pos += 4
                continue
            # Read null-terminated string
            end = data.find(b"\x00", arr_pos)
            if end == -1:
                break
            try:
                name = data[arr_pos:end].decode("ascii")
            except (UnicodeDecodeError, ValueError):
                arr_pos = end + 1
                continue
            if len(name) >= 2:
                results.append(name)
            arr_pos = end + 1

        pos = idx + 1
    return results


def _extract_class_names_binary(data: bytes, section: str = "CfgVehicles") -> list[str]:
    """Extract the classes a raP config.bin defines directly under *section*.

    The section is found by walking the root class case-insensitively, as the
    engine resolves it — a byte search for the name also matched inside longer
    names. Forward declarations (``class CarScript;``) and deletes are skipped,
    not treated as the end of the section: nearly every mod opens CfgVehicles
    with a run of them, and stopping there reported no classes for any mod.
    Truncated or unrecognised data returns whatever was read before it.
    """
    if not data.startswith(b"\x00raP"):
        return []
    wanted = section.lower()
    names: list[str] = []
    try:
        body = next(
            (
                offset
                for kind, name, offset in _rap_class_entries(data, _RAP_ROOT_OFFSET)
                if kind == 0 and name.lower() == wanted
            ),
            None,
        )
        if body is None:
            return []
        for kind, name, _offset in _rap_class_entries(data, body):
            if kind == 0 and len(name) >= 2:
                names.append(name)
    except (IndexError, ValueError, struct.error):
        pass
    return names


def _extract_class_names_text(data: bytes, section: str = "CfgVehicles") -> list[str]:
    """Extract class names from text config.cpp within a given section."""
    import re
    try:
        text = data.decode("utf-8", errors="ignore")
    except UnicodeDecodeError:
        return []

    # Find the section block
    pattern = re.compile(
        r'\bclass\s+' + re.escape(section) + r'\s*\{', re.IGNORECASE
    )
    match = pattern.search(text)
    if not match:
        return []

    # Find matching braces for the section
    start = match.end()
    depth = 1
    pos = start
    while pos < len(text) and depth > 0:
        if text[pos] == '{':
            depth += 1
        elif text[pos] == '}':
            depth -= 1
        pos += 1
    section_text = text[start:pos - 1]

    # Extract top-level class names (depth 0 only) using incremental
    # brace tracking so we don't re-scan from the start each iteration.
    names: list[str] = []
    depth = 0
    scan_from = 0
    for m in re.finditer(r'\bclass\s+(\w+)', section_text):
        # Update depth for braces between previous position and this match
        for ch in section_text[scan_from:m.start()]:
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
        scan_from = m.start()
        if depth == 0:
            name = m.group(1)
            if len(name) >= 2:
                names.append(name)
    return names


_CLASSNAME_CACHE_FILE = ".pbo_classname_cache.json"
# Bump when extraction changes what an unchanged PBO yields, so an entry written
# by an older parser is re-scanned instead of trusted on its matching mtime.
# 2: forward declarations no longer end the scan (every list cached before was empty).
# 3: entries hold one list per config section, so a caller asking for CfgWeapons
#    is not answered with the CfgVehicles list an older entry happened to cache.
_CLASSNAME_CACHE_VERSION = 3

# What `types.xml` may name. CfgVehicles alone is right for "spawn one of these"
# (the spawn test, the map editor's class lists); the central economy also
# carries weapons and magazines, and filtering a types file on vehicles alone
# would drop every gun a mod ships as if nobody defined it.
DEFAULT_CLASS_SECTIONS: tuple[str, ...] = ("CfgVehicles",)
TYPE_CLASS_SECTIONS: tuple[str, ...] = ("CfgVehicles", "CfgWeapons", "CfgMagazines")


def _load_classname_cache(server_dir: Path) -> dict:
    """Load PBO classname cache from disk."""
    path = server_dir / _CLASSNAME_CACHE_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_classname_cache(server_dir: Path, cache: dict) -> None:
    """Save PBO classname cache to disk."""
    path = server_dir / _CLASSNAME_CACHE_FILE
    try:
        path.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    except OSError:
        pass


def extract_mod_classnames(
    server_dir: Path,
    mod_name: str,
    sections: tuple[str, ...] = DEFAULT_CLASS_SECTIONS,
) -> list[str]:
    """Extract the class names a mod's PBOs define under *sections*."""
    pbos = _get_mod_pbos(server_dir, mod_name)
    if not pbos:
        return []

    all_names: list[str] = []
    for pbo_path in pbos:
        data = _read_pbo_entry(pbo_path, "config.bin")
        is_binary = data is not None
        if data is None:
            data = _read_pbo_entry(pbo_path, "config.cpp")
        if data is None:
            continue

        for section in sections:
            if is_binary:
                names = _extract_class_names_binary(data, section)
            else:
                names = _extract_class_names_text(data, section)
            all_names.extend(names)

    return all_names


@dataclass
class ClassInfo:
    """One class a mod's config defines: its parent and its own ``scope``
    (None when the class inherits it)."""

    name: str
    parent: str
    scope: int | None
    section: str


def _class_info_binary(data: bytes, section: str) -> list[ClassInfo]:
    """`ClassInfo` for the classes a raP config.bin defines under *section*.
    Forward declarations and deletes are skipped, as in
    `_extract_class_names_binary`. Truncated data returns what was read."""
    from app.shared.rap import class_parent, class_scope, find_class

    out: list[ClassInfo] = []
    if not data.startswith(b"\x00raP"):
        return out
    try:
        body = find_class(data, [section])
        if body is None:
            return out
        for kind, name, offset in _rap_class_entries(data, body):
            if kind == 0 and len(name) >= 2:
                out.append(ClassInfo(name, class_parent(data, offset), class_scope(data, offset), section))
    except (IndexError, ValueError, struct.error):
        pass
    return out


def _body_end(text: str, start: int) -> int:
    """The index just past the ``}`` that closes the brace block opened before *start*."""
    depth = 1
    pos = start
    while pos < len(text) and depth > 0:
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
        pos += 1
    return pos


def _class_info_text(data: bytes, section: str) -> list[ClassInfo]:
    """`ClassInfo` from a text config.cpp: ``class X : Parent { scope = 2; }``
    at the top level of *section*. A forward declaration has no body and is
    skipped. Only the body's own top level is read for ``scope``."""
    text = data.decode("utf-8", errors="ignore")
    match = re.compile(r"\bclass\s+" + re.escape(section) + r"\s*\{", re.IGNORECASE).search(text)
    if not match:
        return []
    section_text = text[match.end():_body_end(text, match.end()) - 1]
    head = re.compile(r"\bclass\s+(\w+)\s*(?::\s*(\w+))?\s*\{")
    out: list[ClassInfo] = []
    pos = 0
    while (m := head.search(section_text, pos)) is not None:
        end = _body_end(section_text, m.end())
        body = section_text[m.end():end - 1]
        # Nested classes removed, so a sub-class's scope is not taken for this one's.
        top = body
        while (inner := head.search(top)) is not None:
            top = top[: inner.start()] + top[_body_end(top, inner.end()):]
        scope = re.search(r"\bscope\s*=\s*(\d+)\s*;", top, re.IGNORECASE)
        if len(m.group(1)) >= 2:
            out.append(ClassInfo(m.group(1), m.group(2) or "", int(scope.group(1)) if scope else None, section))
        pos = end
    return out


def extract_mod_class_info(
    server_dir: Path,
    mod_name: str,
    sections: tuple[str, ...] = TYPE_CLASS_SECTIONS,
) -> list[ClassInfo]:
    """Every class a mod's PBOs define under *sections*, with parent and scope.

    `extract_mod_classnames` gives names only. This one is for building
    types.xml entries (`app/ops/mod_autoconfig.py`), which needs to tell an
    item (scope 2) from a base class and to find the vanilla class it extends.
    """
    out: list[ClassInfo] = []
    for pbo_path in _get_mod_pbos(server_dir, mod_name):
        data = _read_pbo_entry(pbo_path, "config.bin")
        if data is not None:
            for section in sections:
                out.extend(_class_info_binary(data, section))
            continue
        data = _read_pbo_entry(pbo_path, "config.cpp")
        if data is not None:
            for section in sections:
                out.extend(_class_info_text(data, section))
    return out


def extract_mod_classnames_cached(
    server_dir: Path,
    mod_list: list[str],
    sections: tuple[str, ...] = DEFAULT_CLASS_SECTIONS,
    *,
    prune: bool = True,
) -> dict[str, list[str]]:
    """Extract classnames from all mods using cache.

    *prune* drops the cache entries of every mod not in *mod_list*. A caller
    that asks about a few mods passes False, or it throws away the other mods'
    parsed PBOs and the next full read parses them all again.

    Returns {mod_name: [classname, ...]}, the sections concatenated in the order
    asked for. The cache holds one list per section, so asking for a section a
    previous call didn't re-reads only that section's PBO entries.
    """
    cache = _load_classname_cache(server_dir)
    updated = False
    result: dict[str, list[str]] = {}

    for mod_name in mod_list:
        current_mtime = _get_mod_pbo_mtime(server_dir, mod_name)
        cached_entry = cache.get(mod_name)

        fresh = (
            cached_entry
            and cached_entry.get("mtime") == current_mtime
            and current_mtime > 0
            and cached_entry.get("version") == _CLASSNAME_CACHE_VERSION
        )
        by_section: dict[str, list[str]] = dict(cached_entry.get("sections", {})) if fresh else {}
        missing = [s for s in sections if s not in by_section]
        if missing:
            for section in missing:
                by_section[section] = extract_mod_classnames(server_dir, mod_name, (section,))
            cache[mod_name] = {
                "mtime": current_mtime,
                "version": _CLASSNAME_CACHE_VERSION,
                "sections": by_section,
            }
            updated = True

        classes = [name for section in sections for name in by_section.get(section, [])]
        if classes:
            result[mod_name] = classes

    # Remove stale entries
    mod_set = set(mod_list)
    stale = [k for k in cache if k not in mod_set] if prune else []
    for k in stale:
        del cache[k]
        updated = True

    if updated:
        _save_classname_cache(server_dir, cache)

    return result


def _extract_addon_name_binary(data: bytes) -> str:
    """Extract the CfgPatches child class name (addon name this PBO provides).

    In raP format: root entries list CfgPatches with a body offset.
    At that offset: inherited_class\\x00 + num_entries + entries.
    The first type-0 (class) entry name is the addon name.
    """
    marker = b"CfgPatches\x00"
    idx = data.find(marker)
    if idx == -1:
        return ""

    # Read CfgPatches body offset (4 bytes after the name)
    offset_pos = idx + len(marker)
    if offset_pos + 4 > len(data):
        return ""
    body_offset = struct.unpack_from("<I", data, offset_pos)[0]

    if body_offset == 0 or body_offset >= len(data):
        return ""

    pos = body_offset
    # Skip inherited class name (null-terminated)
    end = data.find(b"\x00", pos)
    if end == -1:
        return ""
    pos = end + 1

    # Read num_entries (compressed int)
    num_entries, pos = _read_rap_compressed_int(data, pos)
    if num_entries <= 0:
        return ""

    # First class entry (type=0) is the addon name
    for _ in range(num_entries):
        if pos >= len(data):
            break
        entry_type = data[pos]
        pos += 1
        if entry_type == 0:  # class entry
            # Read class name (null-terminated)
            end = data.find(b"\x00", pos)
            if end == -1:
                break
            try:
                name = data[pos:end].decode("ascii")
            except (UnicodeDecodeError, ValueError):
                break
            if len(name) >= 2:
                return name
            break
        else:
            # Skip non-class entries — read property name then skip value
            end = data.find(b"\x00", pos)
            if end == -1:
                break
            pos = end + 1 + 4  # name + skip 4 bytes (approximate)

    return ""


@dataclass
class DepEdge:
    """A dependency relationship between two mods."""

    mod: str  # the dependent mod
    depends_on: str  # the mod it depends on
    source: str  # "PBO" or "MOD_DEPS"


@dataclass
class PboScanDebug:
    """Debug info from PBO scanning."""

    pbos_scanned: int = 0
    configs_found: int = 0
    provided_addons: dict[str, str] = field(default_factory=dict)
    mod_requirements: dict[str, list[str]] = field(default_factory=dict)
    dep_edges: list[DepEdge] = field(default_factory=list)


def check_required_addons(
    config: DayzConfig,
) -> tuple[list[ConflictItem], PboScanDebug]:
    """Check requiredAddons from PBO config.bin against installed mods.

    Returns (items, debug_info). Uses cached PBO scan results.
    """
    from app.modmgr import modlist

    debug = PboScanDebug()
    mods = modlist.active(config)
    server_dir = config.server_dir_path
    if not mods:
        return [], debug

    provided, requirements, pbos, configs = scan_mod_pbos_cached(server_dir, mods)
    debug.pbos_scanned = pbos
    debug.configs_found = configs
    debug.provided_addons = provided
    debug.mod_requirements = requirements

    mod_set = set(mods)

    # Build dependency edges from PBO data
    items: list[ConflictItem] = []
    seen: set[tuple[str, str]] = set()
    edge_seen: set[tuple[str, str]] = set()

    for mod_name, required_list in debug.mod_requirements.items():
        for addon in required_list:
            addon_lower = addon.lower()
            if any(addon.startswith(p) for p in _VANILLA_PREFIXES):
                continue
            provider = debug.provided_addons.get(addon_lower)
            if provider and provider != mod_name and provider in mod_set:
                edge_key = (mod_name, provider)
                if edge_key not in edge_seen:
                    edge_seen.add(edge_key)
                    debug.dep_edges.append(DepEdge(mod_name, provider, "PBO"))
            elif not provider:
                key = (mod_name, addon_lower)
                if key not in seen:
                    seen.add(key)
                    items.append(ConflictItem(
                        severity="warning",
                        category="required_addon",
                        message=f"Requires addon '{addon}' — not found in installed mods",
                        mod_name=mod_name,
                    ))

    # Build dependency edges from MOD_DEPS
    from app.modmgr.mod_ops import resolve_names_to_ids
    from app.modmgr import modlist
    from app.shared.seed_data import MOD_DEPS, parse_workshop_mod_ids

    cached_ids = modlist.workshop_ids(config)
    name_to_id = resolve_names_to_ids(mods, cached_ids)
    seed_id_to_name = parse_workshop_mod_ids()

    for mod_name, wid in name_to_id.items():
        entry = MOD_DEPS.get(wid)
        if not entry:
            continue
        for dep_id, dep_name in entry.get("deps", {}).items():
            dep_mod = seed_id_to_name.get(dep_id)
            if dep_mod and dep_mod in mod_set and dep_mod != mod_name:
                edge_key = (mod_name, dep_mod)
                if edge_key not in edge_seen:
                    edge_seen.add(edge_key)
                    debug.dep_edges.append(DepEdge(mod_name, dep_mod, "MOD_DEPS"))

    return items, debug


# ── Orchestrator ─────────────────────────────────────────────────────


def scan_conflicts(config: DayzConfig, *, fetch_workshop: bool = False) -> ScanResult:
    """Run all conflict checks and return aggregated results."""
    t0 = time.monotonic()

    profiles_dir = config.server_dir_path / config.profiles_path
    all_items: list[ConflictItem] = []
    statuses: dict[str, tuple[bool, str]] = {}

    # Check 1: RPT log errors
    rpt_items, rpt_file = check_rpt_errors(profiles_dir)
    all_items.extend(rpt_items)
    if not rpt_file:
        detail = ""
        if config.log_cleanup_days > 0 or config.log_cleanup_keep > 0:
            detail = "Log cleanup is enabled — ensure 'Keep per type' is >= 1 to preserve the latest log"
        all_items.append(ConflictItem(
            severity="info", category="no_logs",
            message="No .RPT log files found in profiles directory",
            detail=detail,
        ))
        statuses["RPT log scan"] = (True, "No log files found")
    elif rpt_items:
        statuses["RPT log scan"] = (False, f"{len(rpt_items)} issue(s) in {rpt_file}")
    else:
        statuses["RPT log scan"] = (True, f"Clean — {rpt_file}")

    # Check 2: Missing dependencies (MOD_DEPS)
    dep_items = check_missing_deps(config, fetch_workshop=fetch_workshop)
    all_items.extend(dep_items)
    if dep_items:
        statuses["Mod dependencies"] = (False, f"{len(dep_items)} missing")
    else:
        statuses["Mod dependencies"] = (True, "All satisfied")

    # Check 3: Missing bikeys
    bikey_items = check_missing_bikeys(config)
    all_items.extend(bikey_items)
    if bikey_items:
        statuses["Bikey signatures"] = (False, f"{len(bikey_items)} mod(s) missing bikeys")
    else:
        statuses["Bikey signatures"] = (True, "All present")

    # Check 4: requiredAddons from PBOs
    addon_items, pbo_debug = check_required_addons(config)
    all_items.extend(addon_items)
    pbo_detail = f"{pbo_debug.pbos_scanned} PBOs scanned"
    if addon_items:
        statuses["requiredAddons"] = (False, f"{len(addon_items)} unsatisfied — {pbo_detail}")
    else:
        statuses["requiredAddons"] = (True, f"All satisfied — {pbo_detail}")

    # Check 5: a manual load order that puts a mod before a mod it requires.
    # No status row: the Doctor has its own "Mod load order" row for it.
    all_items.extend(check_load_order(config))

    from app.modmgr import modlist

    mod_list = modlist.active(config)
    duration = int((time.monotonic() - t0) * 1000)

    return ScanResult(
        items=all_items,
        rpt_file=rpt_file,
        mods_scanned=len(mod_list),
        mod_list=mod_list,
        scan_duration_ms=duration,
        pbo_debug=pbo_debug,
        check_statuses=statuses,
    )


# ── Shared rendering helpers ───────────────────────────────────────────
#
# Rich renderables (Table, Tree) for `zsm doctor`'s console output.


def _build_dep_graph_data(
    result: ScanResult,
) -> tuple[dict[str, list[tuple[str, str]]], list[str], list[str]]:
    """Build (children, roots, standalone) from scan result edges."""
    from collections import defaultdict

    children: dict[str, list[tuple[str, str]]] = defaultdict(list)
    all_dependents: set[str] = set()

    if result.pbo_debug and result.pbo_debug.dep_edges:
        for e in result.pbo_debug.dep_edges:
            children[e.depends_on].append((e.mod, e.source))
            all_dependents.add(e.mod)

    roots = sorted(set(children.keys()) - all_dependents)
    all_in_edges = set(children.keys()) | all_dependents
    standalone = [m for m in result.mod_list if m not in all_in_edges]

    return children, roots, standalone


def build_issues_table(result: ScanResult):
    """Build a Rich Table of conflict issues. Returns None if no issues."""
    from rich.table import Table

    issues = [i for i in result.items if i.severity in ("error", "warning")]
    if not issues:
        return None

    table = Table(title="Issues Found", expand=False)
    table.add_column("Sev", style="bold", width=3)
    table.add_column("Category")
    table.add_column("Mod", style="cyan")
    table.add_column("Details")

    for item in issues:
        sev = "[red]ERR[/red]" if item.severity == "error" else "[yellow]WRN[/yellow]"
        table.add_row(sev, item.message, item.mod_name or "-", item.detail or "")

    return table


def build_dep_tree(result: ScanResult):
    """Build a Rich Tree of mod dependencies. Returns None if no edges."""
    from rich.tree import Tree

    if not result.pbo_debug or not result.pbo_debug.dep_edges:
        return None

    children, roots, standalone = _build_dep_graph_data(result)

    tree = Tree("[bold]Mod Dependencies[/bold]")
    for root in roots:
        root_node = tree.add(f"[cyan]{root}[/cyan]")
        for dep_mod, source in sorted(children[root]):
            dep_node = root_node.add(
                f"[green]{dep_mod}[/green] [dim]({source})[/dim]"
            )
            if dep_mod in children:
                for sub_mod, sub_src in sorted(children[dep_mod]):
                    dep_node.add(
                        f"[green]{sub_mod}[/green] [dim]({sub_src})[/dim]"
                    )

    for mod in standalone:
        tree.add(f"[dim]{mod}[/dim]")

    return tree


# ── PBO-based mod load ordering ─────────────────────────────────────


def _build_mod_dependency_graph(
    mod_list: list[str],
    server_dir: Path,
    cached_ids: dict[str, str] | None = None,
    *,
    every_provider: bool = False,
) -> dict[str, set[str]]:
    """Build mod-level dependency graph from PBO requiredAddons + MOD_DEPS.

    Returns ``{mod_name: {set of mod names it depends on}}``.

    With *every_provider*, an addon named like a vanilla one
    (`_VANILLA_PREFIXES`) is followed to the mod in *mod_list* that provides
    it; the default skips it, as the load order always has.
    """
    provided_by, mod_requirements, _, _ = scan_mod_pbos_cached(server_dir, mod_list)
    mod_set = set(mod_list)

    requires: dict[str, set[str]] = {}
    for mod_name, req_list in mod_requirements.items():
        deps: set[str] = set()
        for addon in req_list:
            if not every_provider and any(addon.startswith(p) for p in _VANILLA_PREFIXES):
                continue
            provider = provided_by.get(addon.lower())
            if provider and provider != mod_name and provider in mod_set:
                deps.add(provider)
        if deps:
            requires[mod_name] = deps

    from app.shared.seed_data import MOD_DEPS, parse_workshop_mod_ids

    if cached_ids is None:
        cached_ids = {}
    seed_id_to_name = parse_workshop_mod_ids()
    seed_name_to_id = {v: k for k, v in seed_id_to_name.items()}
    name_to_id: dict[str, str] = {}
    for m in mod_list:
        wid = (
            cached_ids.get(m)
            or cached_ids.get(m.lstrip("@"))
            or seed_name_to_id.get(m)
        )
        if wid:
            name_to_id[m] = wid

    for mod_name, wid in name_to_id.items():
        entry = MOD_DEPS.get(wid)
        if not entry:
            continue
        for dep_id in entry.get("deps", {}):
            dep_mod = seed_id_to_name.get(dep_id)
            if dep_mod and dep_mod in mod_set and dep_mod != mod_name:
                requires.setdefault(mod_name, set()).add(dep_mod)

    return requires


def get_mod_dependencies(
    mod_list: list[str],
    server_dir: Path,
    cached_ids: dict[str, str] | None = None,
    *,
    every_provider: bool = False,
) -> dict[str, set[str]]:
    """Return mod dependency constraints for the given mod list.

    Returns ``{mod_name: {set of mod names that must load before it}}``.
    *every_provider* as in `_build_mod_dependency_graph`.
    """
    return _build_mod_dependency_graph(mod_list, server_dir, cached_ids, every_provider=every_provider)


def resolve_mod_load_order(
    mod_list: list[str],
    server_dir: Path,
    registry_order: list[str] | None = None,
    cached_ids: dict[str, str] | None = None,
) -> list[str]:
    """Order mods using requiredAddons from PBOs + MOD_DEPS (topological sort).

    Uses cached PBO scan results plus ``MOD_DEPS`` from seed_data for
    runtime dependencies that PBOs don't declare. Falls back to
    *registry_order* for mods with no dependency data.
    """
    if not mod_list:
        return []

    requires = _build_mod_dependency_graph(mod_list, server_dir, cached_ids)

    # Phase 3 — topological sort (Kahn's algorithm)
    in_degree: dict[str, int] = {m: 0 for m in mod_list}
    dependents: dict[str, list[str]] = {m: [] for m in mod_list}
    for mod_name, deps in requires.items():
        for dep in deps:
            if dep in in_degree:
                in_degree[mod_name] = in_degree.get(mod_name, 0) + 1
                dependents[dep].append(mod_name)

    # Seed queue with mods that have no dependencies
    # Use registry order (or original order) as tiebreaker for stable output
    if registry_order:
        reg_index = {name: i for i, name in enumerate(registry_order)}
    else:
        reg_index = {}
    orig_index = {name: i for i, name in enumerate(mod_list)}

    def sort_key(m: str) -> int:
        return reg_index.get(m, 10000 + orig_index.get(m, 20000))

    queue = sorted([m for m in mod_list if in_degree[m] == 0], key=sort_key)
    result: list[str] = []

    while queue:
        mod = queue.pop(0)
        result.append(mod)
        for dependent in dependents.get(mod, []):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                # Insert in sorted position for stable ordering
                queue.append(dependent)
                queue.sort(key=sort_key)

    # Circular dependency fallback — append remaining mods
    remaining = [m for m in mod_list if m not in set(result)]
    if remaining:
        remaining.sort(key=sort_key)
        result.extend(remaining)

    return result
