"""Mod metadata parser for mod.cpp and meta.cpp files."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

# meta.cpp's `timestamp` is a .NET `DateTime.ToBinary()`: the top two bits are
# the DateTimeKind (1 = UTC in every Workshop mod seen) and the low 62 bits are
# 100 ns ticks since 0001-01-01. A bare Windows FILETIME (100 ns ticks since
# 1601-01-01) is still read, for a mod packed by other tooling.
_DOTNET_TICKS_MASK = (1 << 62) - 1
_DOTNET_EPOCH_TICKS = 621355968000000000  # 1970-01-01 in .NET ticks
_FILETIME_EPOCH_DELTA = 116444736000000000
_MAX_SANE_EPOCH = 4102444800  # 2100-01-01; anything later is junk, not a date
# ToBinary has 19 digits, a FILETIME 18; `int` refuses a string past 4300.
_MAX_TIMESTAMP_DIGITS = 20


@dataclass
class ModInfo:
    """Parsed mod metadata from mod.cpp and meta.cpp."""

    name: str = ""
    author: str = ""
    version: str = ""
    overview: str = ""
    action: str = ""
    workshop_id: str = ""
    timestamp: str = ""


def workshop_url(mod_id: str) -> str:
    """Steam Workshop page for a workshop item ID.

    Single source for the URL — the mod tooltip body and the web mod page both
    build it from here.
    """
    return f"https://steamcommunity.com/sharedfiles/filedetails/?id={mod_id}"


def parse_mod_timestamp(raw: str) -> int | None:
    """meta.cpp's ``timestamp`` as a Unix epoch in seconds, or ``None``.

    DayZ's publisher writes a .NET ``DateTime.ToBinary()``, a 19-digit number
    (``timestamp = 5250960208020560000;`` is @CF packed 2026-10-12 16:22 UTC)
    that is useless shown raw. Read as a FILETIME it landed past the year
    18000 and every real mod lost its Built date. Its
    kind bits are dropped, and a tick count below .NET's 1970 is read as a
    bare FILETIME (ticks since 1601); the two ranges cannot overlap before
    2100. Smaller values are taken as epoch milliseconds or epoch seconds so a
    mod packed by other tooling still reads as a date. Single source: the
    tooltip and the web mod page both use it.
    """
    # ASCII digits only: `str.isdigit` is also true for "²", which `int`
    # refuses, and meta.cpp is the mod author's file.
    digits = "".join(ch for ch in str(raw or "") if ch.isascii() and ch.isdigit())
    if not digits or len(digits) > _MAX_TIMESTAMP_DIGITS:
        return None
    value = int(digits)
    if value >= 1 << 64:  # not a 64-bit value, so its kind bits mean nothing
        return None
    if value > 10**16:
        ticks = value & _DOTNET_TICKS_MASK  # drop ToBinary's DateTimeKind bits
        if ticks >= _DOTNET_EPOCH_TICKS:  # .NET ticks since 0001-01-01
            value = (ticks - _DOTNET_EPOCH_TICKS) // 10_000_000
        else:  # a bare FILETIME, ticks since 1601-01-01
            value = (ticks - _FILETIME_EPOCH_DELTA) // 10_000_000
    elif value > 10**12:
        value //= 1000
    if value <= 0 or value > _MAX_SANE_EPOCH:
        return None
    return value


def format_epoch(epoch: int | float | None) -> str:
    """Local-time ``YYYY-MM-DD HH:MM`` for a Unix epoch ("" when unknown)."""
    if not epoch:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch))
    except (OSError, OverflowError, ValueError):
        return ""


def parse_mod_file(path: Path) -> dict[str, str]:
    """Parse key=value pairs from a mod.cpp or meta.cpp file.

    Handles quoted strings, bare numbers, inline ``//`` comments,
    and skips string-table references (``$STR_...``).
    """
    if not path.is_file():
        return {}

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue

        key, _, rest = line.partition("=")
        key = key.strip()
        rest = rest.strip()

        # Remove trailing semicolon
        if rest.endswith(";"):
            rest = rest[:-1].rstrip()

        # Extract value: quoted string or bare token
        if rest.startswith('"'):
            end_quote = rest.find('"', 1)
            value = rest[1:end_quote] if end_quote != -1 else rest[1:]
        else:
            # Bare value — strip inline comment
            if "//" in rest:
                rest = rest[: rest.index("//")]
            value = rest.strip()

        if not value or value.startswith("$"):
            continue

        result[key] = value

    return result


def read_mod_info(mod_dir: Path) -> ModInfo | None:
    """Read mod.cpp and meta.cpp from a mod directory."""
    if not mod_dir.is_dir():
        return None

    mod_data = parse_mod_file(mod_dir / "mod.cpp")
    meta_data = parse_mod_file(mod_dir / "meta.cpp")

    if not mod_data and not meta_data:
        return None

    return ModInfo(
        name=mod_data.get("name", ""),
        author=mod_data.get("author", ""),
        version=mod_data.get("version", ""),
        overview=mod_data.get("overview", ""),
        action=mod_data.get("action", ""),
        workshop_id=meta_data.get("publishedid", ""),
        timestamp=meta_data.get("timestamp", ""),
    )


def format_mod_info(info: ModInfo) -> tuple[str, str]:
    """Format ModInfo as (title, body) for the Mods page's per-mod tooltip.

    Title is shown bold by the tooltip; body is the detail text.
    """
    title = info.name or ""
    lines: list[str] = []
    if info.author:
        lines.append(f"by {info.author}")
    if info.version:
        lines.append(f"Version: {info.version}")
    if info.workshop_id:
        lines.append(workshop_url(info.workshop_id))
    if info.timestamp:
        built = format_epoch(parse_mod_timestamp(info.timestamp))
        lines.append(f"Built: {built}" if built else f"Timestamp: {info.timestamp}")
    if info.overview:
        if lines:
            lines.append("")
        text = info.overview
        if len(text) > 100:
            text = text[:97] + "..."
        lines.append(text)
    if info.action and "steamcommunity.com" not in info.action:
        if lines:
            lines.append("")
        url = info.action
        if not url.startswith("http"):
            url = f"https://{url}"
        lines.append(url)
    return title, "\n".join(lines)


def build_mod_info_map(
    mod_dirs: dict[str, Path],
    cached_ids: dict[str, str] | None = None,
) -> dict[str, tuple[str, str]]:
    """Build the Mods page's per-mod tooltips from mod directories.

    Args:
        mod_dirs: Mapping of choice value to mod directory path.
        cached_ids: Optional name→workshop-ID cache for fallback lookup.

    Returns:
        Mapping of choice value to ``(title, body)`` tooltip tuples.
    """
    # Build fallback workshop ID lookup from seed data + cached IDs
    _id_lookup: dict[str, str] | None = None

    def _resolve_workshop_id(name: str) -> str:
        nonlocal _id_lookup
        if _id_lookup is None:
            from app.shared.seed_data import parse_workshop_mod_ids
            _id_lookup = {}
            # seed data: {workshop_id: @Name}
            for wid, sname in parse_workshop_mod_ids().items():
                _id_lookup[sname] = wid
                _id_lookup[sname.lstrip("@")] = wid
            if cached_ids:
                for cname, cwid in cached_ids.items():
                    _id_lookup[cname] = cwid
                    _id_lookup[cname.lstrip("@")] = cwid
        return (
            _id_lookup.get(name, "")
            or _id_lookup.get(name.lstrip("@"), "")
        )

    info_map: dict[str, tuple[str, str]] = {}
    for key, mod_dir in mod_dirs.items():
        info = read_mod_info(mod_dir)
        if info:
            if not info.name:
                info.name = key
            if not info.workshop_id:
                info.workshop_id = _resolve_workshop_id(key)
            info_map[key] = format_mod_info(info)
    return info_map
