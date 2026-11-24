"""Ban management — parse, search, CRUD, import/export for BattlEye bans.

BattlEye bans.txt format per line::

    <GUID_or_IP> <expiry> <reason>

Where expiry is 0/-1 for permanent, or a Unix timestamp for temp bans.
"""

from __future__ import annotations

import ipaddress
import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.modmgr.mods import breaks_a_line
from app.shared.json_utils import (
    atomic_write_lines,
    atomic_write_text,
    join_edited_lines,
    lines_with_endings,
    read_text_lossy,
)

# Used with fullmatch, never match: "$" also matches before a trailing "\n",
# which would write the ban as two lines (plans/68 LB-2). [0-9], not \d, which
# also matches non-ASCII digits.
_GUID_RE = re.compile(r"[0-9a-fA-F]{32}")
_IP_RE = re.compile(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}")
# The metadata sidecar belongs to one instance — bans are per server — so
# callers pass config.paths.ban_metadata (plans/44).
# The latest end a temporary ban may have: the largest 32-bit Unix time,
# 2038-01-19 03:14:07 UTC. BattlEye may read bans.txt's expiry as a 32-bit
# number, and a later end would then stop the ban working on the game server.
# This limit replaced an earlier 100-year cap (auth-02). A longer ban is what 0 (permanent) is for.
MAX_BAN_END = 2**31 - 1
_END = datetime.fromtimestamp(MAX_BAN_END, tz=timezone.utc)
MAX_BAN_END_TEXT = f"{_END.day} {_END:%B %Y, %H:%M:%S} UTC"


def max_ban_minutes(now: int | None = None) -> int:
    """The longest temporary ban, in whole minutes from *now*, that still
    ends by MAX_BAN_END."""
    if now is None:
        now = int(time.time())
    return max(0, (MAX_BAN_END - now) // 60)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class BanEntry:
    """A single ban from bans.txt, optionally enriched with metadata."""

    identifier: str  # GUID or IP
    ban_type: str  # "guid" or "ip"
    expiry: int  # 0 = permanent, >0 = Unix timestamp
    reason: str
    # Metadata (from sidecar, not in bans.txt)
    added_at: str = ""  # ISO timestamp
    added_by: str = ""
    player_name: str = ""
    notes: str = ""

    @property
    def is_permanent(self) -> bool:
        return self.expiry <= 0

    @property
    def is_expired(self) -> bool:
        if self.is_permanent:
            return False
        return self.expiry < time.time()

    @property
    def is_active(self) -> bool:
        return not self.is_expired

    @property
    def expires_at(self) -> datetime | None:
        """The end as a date; None for a permanent ban and for an expiry no
        datetime holds. BattlEye and hand edits write bans.txt too, and one
        such line answered 500 for the whole list."""
        if self.is_permanent:
            return None
        try:
            return datetime.fromtimestamp(self.expiry, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    @property
    def time_remaining(self) -> str:
        if self.is_permanent:
            return "permanent"
        if self.is_expired:
            return "expired"
        # Int arithmetic: a float conversion overflows on a huge expiry.
        secs = self.expiry - int(time.time())
        if secs < 3600:
            return f"{secs // 60}m"
        if secs < 86400:
            return f"{secs // 3600}h {(secs % 3600) // 60}m"
        return f"{secs // 86400}d {(secs % 86400) // 3600}h"

    @property
    def display_id(self) -> str:
        """Truncated identifier for display."""
        if self.ban_type == "guid" and len(self.identifier) > 12:
            return self.identifier[:8] + "..." + self.identifier[-4:]
        return self.identifier


# ---------------------------------------------------------------------------
# Parse / write bans.txt
# ---------------------------------------------------------------------------


def is_valid_ban_identifier(identifier: str) -> bool:
    """Return True if *identifier* is a BattlEye GUID or a dotted IPv4 address.

    The shape alone took 300.300.300.300, a ban that matches nobody;
    ``ipaddress`` also refuses a leading zero, which never
    equals the address BattlEye sees. IPv6, CIDR and wildcards stay refused,
    as before. `_detect_ban_type` keeps the shape, so a bad IP already in
    bans.txt is still typed "ip" and still hidden from a viewer.
    """
    if _GUID_RE.fullmatch(identifier):
        return True
    if not _IP_RE.fullmatch(identifier):
        return False
    try:
        ipaddress.IPv4Address(identifier)
    except ValueError:
        return False
    return True


class BanInputError(ValueError):
    """A ban whose reason or duration cannot be written as asked."""


def check_ban_input(reason: str, duration_minutes: int, *, now: int | None = None) -> None:
    """Refuse a reason or duration that would not mean what was typed.

    A newline in the reason starts a second line in bans.txt, which BattlEye
    reads as a second ban for whatever GUID follows it (test pass 2, N14); an
    RCon ban writes the reason into the same file. Every other character
    ``str.splitlines`` breaks at is refused too (``breaks_a_line``): a reader
    that splits that way would see a second ban that BattlEye does not
    (plans/68 LB-1). A tab is whitespace in the reason and stays allowed.
    Refused, not stripped: a reason silently changed is worse than a message.
    A negative duration used to fall through to "permanent" (N15); 0 is how
    permanent is asked for, and a ban that would end after MAX_BAN_END is
    refused: a ban of 99999999999 minutes was written, and its end, past the
    year 9999, then made every read of the list fail.
    *now* is the time the caller counts the end from, so the check and the
    expiry `add_ban` writes cannot fall on two sides of a minute.
    """
    if breaks_a_line(reason.replace("\t", " ")):
        raise BanInputError("The ban reason must be a single line.")
    if duration_minutes < 0:
        raise BanInputError("The ban duration cannot be negative (0 means permanent).")
    longest = max_ban_minutes(now)
    if duration_minutes > longest:
        raise BanInputError(
            f"A temporary ban must end by {MAX_BAN_END_TEXT}, because BattlEye "
            f"may not read a later end date. The longest ban you can set now is "
            f"{longest} minutes. 0 means permanent."
        )


def _detect_ban_type(identifier: str) -> str:
    if _GUID_RE.fullmatch(identifier):
        return "guid"
    if _IP_RE.fullmatch(identifier):
        return "ip"
    return "guid"  # default assumption


def _parse_line(line: str) -> BanEntry | None:
    """One line of bans.txt as a ban, or None for a comment, a blank line or
    a line that is not one."""
    line = line.strip()
    if not line or line.startswith("//") or line.startswith("#"):
        return None
    parts = line.split(None, 2)
    if len(parts) < 2:
        return None
    try:
        expiry = int(parts[1])
    except ValueError:
        return None
    identifier = parts[0]
    return BanEntry(
        identifier=identifier,
        ban_type=_detect_ban_type(identifier),
        expiry=expiry,
        reason=parts[2] if len(parts) > 2 else "",
    )


def read_bans(bans_path: Path) -> list[BanEntry]:
    """Parse bans.txt into a list of BanEntry objects."""
    if not bans_path.is_file():
        return []
    # "\n" only, as BattlEye reads the file: str.splitlines would also break
    # at a Unicode separator inside a reason, and the next rewrite would turn
    # its second half into a ban of its own (plans/68 LB-1). strip() takes the
    # "\r" of a CRLF file. utf-8-sig, as `write_bans` reads it: a byte order
    # mark kept on the first ID made the next write drop that ban's line.
    text = read_text_lossy(bans_path, encoding="utf-8-sig")
    parsed = (_parse_line(line) for line in text.split("\n"))
    return [ban for ban in parsed if ban is not None]


def _ban_line(ban: BanEntry) -> str:
    expiry = -1 if ban.expiry == 0 else ban.expiry
    return f"{ban.identifier} {expiry} {ban.reason}".rstrip()


def write_bans(bans_path: Path, bans: list[BanEntry], *, keep_file: bool = True) -> None:
    """Write ban entries to bans.txt.

    The file's own lines are edited, not regenerated (2026 Q4): a comment,
    a blank line, a line that is not a ban, the line endings and the final
    newline stay; a ban still in *bans* keeps its line byte for byte unless
    its expiry or reason changed; a ban no longer in *bans* loses its line;
    a new one is appended. The rewrite this replaced dropped every line it
    could not parse. *keep_file* False writes *bans* alone (an export).

    Written atomically: a crash mid-write must not truncate bans.txt, which
    would silently drop bans and let banned players reconnect.

    *bans* comes from `read_bans`, which reads a bans.txt that is not UTF-8
    (a reason typed into an RCon tool) with replacement characters: such a
    file is refused (``NotUtf8Error``), not written back.
    """
    if not keep_file or not bans_path.is_file():
        atomic_write_lines(bans_path, [_ban_line(ban) for ban in bans])
        return
    from app.ops.priority_ops import read_raw

    text = read_raw(bans_path)  # line endings intact; NotUtf8Error as before
    eol = "\r\n" if "\r\n" in text else "\n"
    wanted: dict[str, list[BanEntry]] = {}
    for ban in bans:
        wanted.setdefault(ban.identifier, []).append(ban)
    lines = lines_with_endings(text)
    final_newline = not lines or lines[-1][1] != ""
    out = []
    for line, end in lines:
        ban = _parse_line(line)
        if ban is None:
            out.append([line, end])
            continue
        queue = wanted.get(ban.identifier)
        if not queue:
            continue  # removed
        new = queue.pop(0)
        same = (new.expiry, new.reason) == (ban.expiry, ban.reason)
        out.append([line if same else _ban_line(new), end])
    out += [[_ban_line(ban), eol] for queue in wanted.values() for ban in queue]
    atomic_write_text(bans_path, join_edited_lines(out, eol=eol, final_newline=final_newline), newline="")


# ---------------------------------------------------------------------------
# CRUD operations
# ---------------------------------------------------------------------------


def add_ban(
    bans_path: Path,
    identifier: str,
    reason: str = "",
    duration_minutes: int = 0,
    added_by: str = "",
    *,
    metadata_path: Path,
) -> BanEntry:
    """Add a ban. duration_minutes=0 means permanent."""
    now = int(time.time())
    check_ban_input(reason, duration_minutes, now=now)
    bans = read_bans(bans_path)

    # Check for duplicate
    for existing in bans:
        if existing.identifier == identifier:
            raise ValueError(f"Ban already exists for {identifier}")

    expiry = -1
    if duration_minutes > 0:
        expiry = now + (duration_minutes * 60)

    entry = BanEntry(
        identifier=identifier,
        ban_type=_detect_ban_type(identifier),
        expiry=expiry,
        reason=reason,
        added_at=datetime.now(timezone.utc).isoformat(),
        added_by=added_by,
    )
    bans.append(entry)
    write_bans(bans_path, bans)

    # Save metadata
    _save_ban_metadata(
        metadata_path,
        identifier,
        added_at=entry.added_at,
        added_by=added_by,
        notes="",
    )

    return entry


def remove_ban(bans_path: Path, identifier: str) -> bool:
    """Remove a ban by identifier. Returns True if found and removed."""
    bans = read_bans(bans_path)
    original_count = len(bans)
    bans = [b for b in bans if b.identifier != identifier]
    if len(bans) == original_count:
        return False
    write_bans(bans_path, bans)
    return True


def cleanup_expired(bans_path: Path) -> int:
    """Remove expired temporary bans. Returns count removed."""
    bans = read_bans(bans_path)
    active = [b for b in bans if b.is_active]
    removed = len(bans) - len(active)
    if removed > 0:
        write_bans(bans_path, active)
    return removed


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def search_bans(bans: list[BanEntry], query: str) -> list[BanEntry]:
    """Search bans by identifier, reason, or player name (case-insensitive)."""
    q = query.lower()
    return [
        b
        for b in bans
        if q in b.identifier.lower()
        or q in b.reason.lower()
        or q in b.player_name.lower()
    ]


# ---------------------------------------------------------------------------
# Import / export
# ---------------------------------------------------------------------------


@dataclass
class ImportResult:
    """Result of importing bans from another file."""

    added: int = 0
    skipped_duplicates: int = 0
    total_source: int = 0


def import_bans(bans_path: Path, source_path: Path) -> ImportResult:
    """Import bans from another bans.txt, skipping duplicates."""
    current = read_bans(bans_path)
    source = read_bans(source_path)
    existing_ids = {b.identifier for b in current}

    result = ImportResult(total_source=len(source))
    for ban in source:
        if ban.identifier in existing_ids:
            result.skipped_duplicates += 1
        else:
            current.append(ban)
            existing_ids.add(ban.identifier)
            result.added += 1

    if result.added > 0:
        write_bans(bans_path, current)
    return result


def export_bans(bans_path: Path, dest_path: Path) -> int:
    """Export current bans to a file. Returns count exported."""
    bans = read_bans(bans_path)
    write_bans(dest_path, bans, keep_file=False)
    return len(bans)


# ---------------------------------------------------------------------------
# Metadata sidecar
# ---------------------------------------------------------------------------


def _load_all_metadata(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_all_metadata(path: Path, data: dict[str, dict]) -> None:
    atomic_write_text(
        path,
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
    )


def _save_ban_metadata(
    path: Path,
    identifier: str,
    added_at: str = "",
    added_by: str = "",
    player_name: str = "",
    notes: str = "",
) -> None:
    all_meta = _load_all_metadata(path)
    meta = all_meta.get(identifier, {})
    if added_at:
        meta["added_at"] = added_at
    if added_by:
        meta["added_by"] = added_by
    if player_name:
        meta["player_name"] = player_name
    if notes:
        meta["notes"] = notes
    all_meta[identifier] = meta
    _save_all_metadata(path, all_meta)


def enrich_with_metadata(bans: list[BanEntry], metadata_path: Path) -> list[BanEntry]:
    """Attach sidecar metadata to ban entries."""
    all_meta = _load_all_metadata(metadata_path)
    for ban in bans:
        meta = all_meta.get(ban.identifier, {})
        ban.added_at = ban.added_at or meta.get("added_at", "")
        ban.added_by = ban.added_by or meta.get("added_by", "")
        ban.player_name = ban.player_name or meta.get("player_name", "")
        ban.notes = ban.notes or meta.get("notes", "")
    return bans


def enrich_with_sessions(
    bans: list[BanEntry], session_events: list[dict]
) -> list[BanEntry]:
    """Attach last-known player names from session history."""
    guid_to_name: dict[str, str] = {}
    for event in session_events:
        name = event.get("name", "")
        guid = event.get("guid", "")
        if name and guid:
            guid_to_name[guid] = name

    for ban in bans:
        if ban.ban_type == "guid" and not ban.player_name:
            ban.player_name = guid_to_name.get(ban.identifier, "")
    return bans


# ---------------------------------------------------------------------------
# Summary / stats
# ---------------------------------------------------------------------------


@dataclass
class BanStats:
    """Summary statistics for the ban list."""

    total: int = 0
    permanent: int = 0
    temporary: int = 0
    expired: int = 0
    guid_bans: int = 0
    ip_bans: int = 0


def get_ban_stats(bans: list[BanEntry]) -> BanStats:
    """Compute summary statistics."""
    stats = BanStats(total=len(bans))
    for b in bans:
        if b.is_permanent:
            stats.permanent += 1
        elif b.is_expired:
            stats.expired += 1
        else:
            stats.temporary += 1
        if b.ban_type == "guid":
            stats.guid_bans += 1
        else:
            stats.ip_bans += 1
    return stats


# ── The server's own ban list (ban.txt) ──────────────────────────────
#
# DayZ reads ban.txt from the root of the server install, beside whitelist.txt,
# unless serverDZ.cfg's `disableBanlist` is on. It is a second list next to
# BattlEye's bans.txt, with no reason, expiry or metadata, so it is shown on its
# own and never folded into `BanStats` (test pass 1, F45). **Read-only**: the
# panel does not edit it. Its header asks for the 44-character player ID from
# the .ADM; whether a Steam64 ID also works there is not verified, so nothing
# here claims either way.

SERVER_BANLIST_FILENAME = "ban.txt"


def server_banlist_path(config) -> Path:
    return config.server_dir_path / SERVER_BANLIST_FILENAME


def read_server_banlist(path: Path) -> list[str]:
    """IDs in ban.txt, in file order, each once.

    ``//`` comments (whole-line or after an ID) and blank lines are skipped,
    the same tolerant reading as whitelist.txt. Missing file → ``[]``.
    """
    from app.ops.priority_ops import listed_ids

    return list(dict.fromkeys(listed_ids(path, lambda tok: True)))


def server_banlist_disabled(config) -> bool:
    """serverDZ.cfg's ``disableBanlist``: when on, DayZ ignores ban.txt."""
    from app.parsers.servercfg import read_cfg_value

    value = read_cfg_value(config.server_cfg_path, "disableBanlist") or ""
    return value.strip().strip('"').lower() in ("1", "true")
