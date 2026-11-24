"""ADM log parser — extract kill, hit, and death events from DayZ admin logs.

DayZ writes ``DayZServer_x64.ADM`` files into the profiles directory
when launched with ``-adminlog``. This module parses those files into
structured event dataclasses for the kill feed, Discord notifications,
and player tracking.

**What the game writes** (DayZ 1.29.163709). Connect and disconnect lines come
from the executable; the rest from Bohemia's script PluginAdminLog
(``scripts/4_world/plugins/pluginbase/pluginadminlog.c``), whose
``GetPlayerPrefix`` opens every line about a player::

    Player "Name"[ (DEAD)] (id=<id> pos=<x, z, height>)

The id is the 44-character DayZ GUID (URL-safe base64), or the Steam64 id on a
disconnect written after the identity is gone. Bohemia's wiki
(dayz-docs/wiki/pages/administration-logs.wiki, written for 1.02) shows the
same prefix with no space before ``(id=``, a comma before ``pos=``, and a
hitter or killer without the word ``Player``; `_player` takes both.

- ``<prefix> is connected`` (the wiki: ``Player "Name" is connected (id=<id>)``)
  and ``<prefix> has been disconnected``: a `ConnectionEvent` with the position.
- ``<prefix> killed by <prefix> with <weapon>[ from <d> meters ]``: a
  `KillEvent`, or a `DeathEvent` when both ids are the same player.
- ``<prefix> died.``, ``<prefix> drowned.`` and ``<prefix> killed by
  <anything not a player>`` (infected, animal, explosive, an Expansion AI): a
  `DeathEvent`. ``PlayerKilled`` writes exactly one of these lines per death.
  ``bled out``, ``committed suicide`` and ``has drowned while unconscious`` are
  written beside it, so they are no event: reading them would count one death
  twice.
- ``<prefix>[HP: n] hit by <prefix> into <zone>(<n>) for <n> damage (<ammo>)
  [ with <weapon>][ from <d> meters ]``: a `HitEvent`. A hit by anything else
  is no event.
- An Expansion AI writes ``AI "Name" (group=...)`` where a player has the
  prefix; a line about an AI is no player's event.

The text after the last name on a line is matched with ``[^"]``. A name is
written between quotes, so the tail of a real line holds no quote, and a name
holding one cannot turn its own death into a kill or a kill into a death.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Event dataclasses
# ---------------------------------------------------------------------------


@dataclass
class KillEvent:
    """A player killed another player."""

    timestamp: str
    killer: str
    killer_uid: str
    victim: str
    victim_uid: str
    weapon: str = ""
    distance: float = 0.0

    @property
    def summary(self) -> str:
        parts = [f"{self.killer} killed {self.victim}"]
        if self.weapon:
            parts.append(f"with {self.weapon}")
        if self.distance > 0:
            parts.append(f"({self.distance:.0f}m)")
        return " ".join(parts)


@dataclass
class HitEvent:
    """A player hit another player."""

    timestamp: str
    attacker: str
    attacker_uid: str
    victim: str
    victim_uid: str
    weapon: str = ""
    body_part: str = ""

    @property
    def summary(self) -> str:
        parts = [f"{self.attacker} hit {self.victim}"]
        if self.weapon:
            parts.append(f"with {self.weapon}")
        if self.body_part:
            parts.append(f"in {self.body_part}")
        return " ".join(parts)


@dataclass
class DeathEvent:
    """A player died (non-PvP: fall, bleed, zombie, etc.)."""

    timestamp: str
    player: str
    player_uid: str
    cause: str = ""

    @property
    def summary(self) -> str:
        if self.cause:
            return f"{self.player} died ({self.cause})"
        return f"{self.player} died"


@dataclass
class ConnectionEvent:
    """A player connected or disconnected."""

    timestamp: str
    player: str
    player_uid: str
    event_type: str  # "connect" or "disconnect"
    position: tuple[float, float, float] | None = None

    @property
    def summary(self) -> str:
        action = "connected" if self.event_type == "connect" else "disconnected"
        return f"{self.player} {action}"


# Type alias for any parsed event
Event = KillEvent | HitEvent | DeathEvent | ConnectionEvent


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Timestamp prefix: "HH:MM:SS" or "YYYY/MM/DD, HH:MM:SS". Bohemia's wiki says
# HH:MM:SS; the hour also takes one digit, as the .RPT writes it before 10:00.
_TS = r"(\d{1,2}:\d{2}:\d{2}|\d{4}/\d{2}/\d{2},?\s*\d{1,2}:\d{2}:\d{2})"

# Every pattern is anchored at the start of the line: the timestamp, " | ",
# then the event's own opening. The .ADM also holds chat and player reports
# (`Chat("Name"(id=...)): text`, `PLAYER REPORT: ...`), whose text a player
# wrote; unanchored, a chat message carrying a timestamp and kill-shaped text
# was read as a kill and reached the kill feed, K/D and the watchlist.
_START = r"^" + _TS + r"\s+\|\s+"

# A DayZ GUID is URL-safe base64 ("-" and "_"); a Steam64 id is digits.
_ID = r"[\w=-]+"


def _player(tag: str, word: str = "Player ") -> str:
    """One `GetPlayerPrefix`, or the wiki's form of it, with the name, id and
    position in the groups *tag*, *tag*_id and *tag*_pos. *word* opens it."""
    return (
        rf'{word}"(?P<{tag}>.*?)"(?: \(DEAD\))? ?'
        rf"\(id=(?P<{tag}_id>{_ID})(?:,? pos=<(?P<{tag}_pos>[^>]*)>)?\)"
    )


_VICTIM = _player("v")
_OTHER = _player("k", word="(?:Player )?")
_WEAPON_TAIL = r'(?: +with (?P<weapon>[^"]+?))?(?: from (?P<dist>[^\s"]+) meters)?\s*$'

# Player killed by player, as Bohemia's script writes it (see the docstring).
PLAYER_KILLED_PATTERN = re.compile(
    _START + _VICTIM + " killed by " + _OTHER + r' with (?P<weapon>[^"]+?)'
    r'(?: from (?P<dist>[^\s"]+) meters)?\s*$'
)

# Killed by an Expansion AI, which writes its own prefix where a player's goes.
AI_KILLED_PATTERN = re.compile(
    _START + _VICTIM + r' killed by AI "(?P<ai>[^"]*)" \(group=[^)]*\)' + _WEAPON_TAIL
)

# Killed by something that is not a player: infected, animal, explosive.
KILLED_BY_PATTERN = re.compile(_START + _VICTIM + r' killed by (?P<cause>[^"]+?)\s*$')

# Died of no one's doing: starvation, a bleed-out, the suicide emote, drowning.
DIED_PATTERN = re.compile(_START + _VICTIM + r' (?P<how>died|drowned)\.[^"]*$')

PLAYER_HIT_PATTERN = re.compile(
    _START + _VICTIM + r"\[HP: [^\]]*\] hit by " + _OTHER
    + r' into (?P<zone>[^"(]*)\(-?\d+\) for [^\s"]+ damage ?(?:\([^")]*\))?' + _WEAPON_TAIL
)

# Player killed by player
# e.g.: Player "Victim"(id=abc123...) has been killed by player "Killer"(id=def456...)
#
# The line ends there. Without the `$`, a player named
# `Bob"(id=1) has been killed by player "Alice"(id=2)` made every line the
# game writes about them (connect, emote, hit) open with this whole pattern.
KILL_PATTERN = re.compile(
    _START + rf'Player "(.+?)"\(id=({_ID})\)'
    rf' has been killed by player "(.+?)"\(id=({_ID})\)\s*$'
)

# Alternate kill with weapon/distance
# e.g.: "Killer(uid=abc) KILLED Victim(uid=def) with AKM from 150.3 meters"
#
# The trailing `"?` is load-bearing and was missing until 2026 Q3: the line
# opens with a quote, so it closes with one, and without somewhere for that
# quote to go `$` could not be reached with the distance group taken. The
# optional group was therefore *always* skipped and the lazy weapon group ate
# the rest — `weapon="AKM from 150.3 meters\""`, `distance=0.0`, for the
# module's own documented example. HIT_PATTERN and DEATH_PATTERN both already
# tolerated it; this one did not.
KILL_WEAPON_PATTERN = re.compile(
    _START + rf'"(.+?)\(uid=({_ID})\)\s+KILLED\s+(.+?)\(uid=({_ID})\)'
    r'\s+with\s+(.+?)(?:\s+from\s+([\d.]+)\s+meters)?"?\s*$'
)

# Hit event
# e.g.: "Attacker(uid=abc) SHOT Victim(uid=def) by AKM into Head."
HIT_PATTERN = re.compile(
    _START + rf'"(.+?)\(uid=({_ID})\)\s+(?:SHOT|HIT)\s+(.+?)\(uid=({_ID})\)'
    r'\s+by\s+(.+?)\s+into\s+(.+?)\."?'
)

# Non-PvP death
# e.g.: "PlayerName(uid=abc) DIED Blood <= 0"
DEATH_PATTERN = re.compile(
    _START + rf'"(.+?)\(uid=({_ID})\)\s+DIED\s*(.*)"?'
)

# Connection events. The position is in the prefix on a real line; the
# trailing "(pos=<...>)" is an older form kept as it was.
CONNECT_PATTERN = re.compile(
    _START + _VICTIM + r"\s+is connected(?:\s+\(pos=<(?P<after_pos>[^>]*)>\))?\s*$"
)

# The wiki's connect line: the id after the event, no position.
CONNECT_WIKI_PATTERN = re.compile(
    _START + rf'Player "(?P<v>.*?)" is connected \(id=(?P<v_id>{_ID})\)\s*$'
)

DISCONNECT_PATTERN = re.compile(_START + _VICTIM + r"\s+has been disconnected\s*$")

# -FLT_MAX in every coordinate is what a player whose body is not in the world
# is written with (seen on a disconnect): no position at all.
_NO_POSITION = 1e30


def _position(text: str | None) -> tuple[float, float, float] | None:
    """``x, z, height`` as written inside ``pos=<...>``, or None."""
    try:
        x, y, z = (float(part) for part in (text or "").split(","))
    except ValueError:
        return None
    if not all(math.isfinite(v) and abs(v) < _NO_POSITION for v in (x, y, z)):
        return None
    return (x, y, z)


def _distance(text: str | None) -> float:
    try:
        return float(text) if text else 0.0
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_prefixed(line: str) -> Event | None:
    """A line that opens with Bohemia's player prefix (see the docstring)."""
    m = CONNECT_PATTERN.search(line) or CONNECT_WIKI_PATTERN.search(line)
    if m:
        groups = m.groupdict()
        return ConnectionEvent(
            timestamp=m.group(1), player=m["v"], player_uid=m["v_id"], event_type="connect",
            position=_position(groups.get("v_pos") or groups.get("after_pos")),
        )

    m = DISCONNECT_PATTERN.search(line)
    if m:
        return ConnectionEvent(
            timestamp=m.group(1), player=m["v"], player_uid=m["v_id"],
            event_type="disconnect", position=_position(m["v_pos"]),
        )

    # Tried before the kills: a death line's tail holds no quote, so a name
    # shaped like "killed by Player ..." cannot make it read as one.
    m = DIED_PATTERN.search(line)
    if m:
        return DeathEvent(
            timestamp=m.group(1), player=m["v"], player_uid=m["v_id"],
            cause="drowned" if m["how"] == "drowned" else "",
        )

    m = PLAYER_KILLED_PATTERN.search(line)
    if m:
        weapon = m["weapon"].strip()
        if m["k_id"] == m["v_id"]:
            # A shot of one's own: the suicide emote with a gun.
            return DeathEvent(
                timestamp=m.group(1), player=m["v"], player_uid=m["v_id"],
                cause=f"killed self with {weapon}",
            )
        return KillEvent(
            timestamp=m.group(1), killer=m["k"], killer_uid=m["k_id"],
            victim=m["v"], victim_uid=m["v_id"], weapon=weapon, distance=_distance(m["dist"]),
        )

    m = AI_KILLED_PATTERN.search(line)
    if m:
        cause = f"killed by AI {m['ai']}"
        if m["weapon"]:
            cause += f" with {m['weapon'].strip()}"
        return DeathEvent(timestamp=m.group(1), player=m["v"], player_uid=m["v_id"], cause=cause)

    m = KILLED_BY_PATTERN.search(line)
    if m:
        return DeathEvent(
            timestamp=m.group(1), player=m["v"], player_uid=m["v_id"],
            cause=f"killed by {m['cause'].strip()}",
        )

    m = PLAYER_HIT_PATTERN.search(line)
    if m:
        return HitEvent(
            timestamp=m.group(1), attacker=m["k"], attacker_uid=m["k_id"],
            victim=m["v"], victim_uid=m["v_id"],
            weapon=(m["weapon"] or "").strip(), body_part=m["zone"].strip(),
        )

    return None


def parse_line(line: str) -> Event | None:
    """Parse a single ADM log line into an event, or None if not recognized.

    *line* is one line of the file as split at "\n"; an event is recognised
    only at its start (see `_START`)."""
    # A UTF-8 byte-order mark, if the file has one, is not whitespace.
    line = line.removeprefix("\ufeff").strip()
    if not line:
        return None

    # What the game writes today comes first: its patterns run to the end of
    # the line, so a name cannot make one of the older forms below out of it.
    event = _parse_prefixed(line)
    if event:
        return event

    m = KILL_WEAPON_PATTERN.search(line)
    if m:
        distance = float(m.group(7)) if m.group(7) else 0.0
        return KillEvent(
            timestamp=m.group(1),
            killer=m.group(2),
            killer_uid=m.group(3),
            victim=m.group(4),
            victim_uid=m.group(5),
            weapon=m.group(6).strip(),
            distance=distance,
        )

    m = KILL_PATTERN.search(line)
    if m:
        return KillEvent(
            timestamp=m.group(1),
            victim=m.group(2),
            victim_uid=m.group(3),
            killer=m.group(4),
            killer_uid=m.group(5),
        )

    m = HIT_PATTERN.search(line)
    if m:
        return HitEvent(
            timestamp=m.group(1),
            attacker=m.group(2),
            attacker_uid=m.group(3),
            victim=m.group(4),
            victim_uid=m.group(5),
            weapon=m.group(6).strip(),
            body_part=m.group(7).strip().rstrip("."),
        )

    m = DEATH_PATTERN.search(line)
    if m:
        return DeathEvent(
            timestamp=m.group(1),
            player=m.group(2),
            player_uid=m.group(3),
            cause=m.group(4).strip().strip('"'),
        )

    return None


def parse_adm_file(adm_path: Path) -> list[Event]:
    """Parse an entire ADM log file. Returns list of events in order."""
    if not adm_path.is_file():
        return []

    # Split at "\n" only, as `tail_adm_file` does: `str.splitlines` (and a
    # text-mode read, which turns a lone "\r" into one) also breaks at
    # characters a player name or chat text can carry, and the text after one
    # would be read as a line of its own. `parse_line` strips the "\r" of CRLF.
    text = adm_path.read_bytes().decode("utf-8", errors="replace")
    events: list[Event] = []
    for line in text.split("\n"):
        event = parse_line(line)
        if event:
            events.append(event)
    return events


def tail_adm_file(
    adm_path: Path, last_position: int = 0
) -> tuple[list[Event], int]:
    """Read new events since ``last_position`` (file byte offset).

    Returns ``(events, new_position)`` for incremental polling.
    """
    if not adm_path.is_file():
        return [], 0

    size = adm_path.stat().st_size
    if size < last_position:
        # File was rotated/truncated — read from start
        last_position = 0

    if size == last_position:
        return [], last_position

    events: list[Event] = []
    with adm_path.open("rb") as f:
        f.seek(last_position)
        for raw_line in f:
            line = raw_line.decode("utf-8", errors="replace")
            event = parse_line(line)
            if event:
                events.append(event)
        new_position = f.tell()

    return events, new_position


# ---------------------------------------------------------------------------
# Filtering helpers
# ---------------------------------------------------------------------------


def filter_kills(events: list[Event]) -> list[KillEvent]:
    """Extract only kill events."""
    return [e for e in events if isinstance(e, KillEvent)]


def filter_deaths(events: list[Event]) -> list[DeathEvent]:
    """Extract only death events (non-PvP)."""
    return [e for e in events if isinstance(e, DeathEvent)]


def filter_hits(events: list[Event]) -> list[HitEvent]:
    """Extract only hit events."""
    return [e for e in events if isinstance(e, HitEvent)]


def filter_by_player(
    events: list[Event], uid: str
) -> list[Event]:
    """Return events involving a specific player UID."""
    result: list[Event] = []
    for e in events:
        if isinstance(e, KillEvent) and (e.killer_uid == uid or e.victim_uid == uid):
            result.append(e)
        elif isinstance(e, HitEvent) and (e.attacker_uid == uid or e.victim_uid == uid):
            result.append(e)
        elif isinstance(e, DeathEvent) and e.player_uid == uid:
            result.append(e)
        elif isinstance(e, ConnectionEvent) and e.player_uid == uid:
            result.append(e)
    return result


def find_adm_files(profiles_dir: Path) -> list[Path]:
    """Find ADM log files in the profiles directory, newest first."""
    if not profiles_dir.is_dir():
        return []
    files = sorted(
        profiles_dir.glob("*.ADM"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return files
