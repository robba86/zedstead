"""Kill feed, K/D and a playtime leaderboard from the .ADM log (TODO D3).

zsm already had the two halves and never joined them: `adm_parser` turns
`DayZServer_x64.ADM` into kill/hit/death events, and `player_ops` turns the
session tracker's JSONL into playtime. This is the join, plus a watchlist so a
name worth keeping an eye on is marked wherever it appears.

**The death heatmap in D3 is not here yet.** Every player line in the .ADM
carries a position (Bohemia's prefix, see `adm_parser`), but the events keep it
only for connect and disconnect lines, the only player lines a real .ADM here
has shown so far.

Everything here is read-only and derived: no new file, no new state. Delete the
`.ADM` and the numbers go back to zero, which is the correct behaviour for a
statistic whose whole source is a log the admin can rotate.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.parsers import adm_parser
from app.shared.config import DayzConfig

__all__ = ["PlayerStat", "build_player_stats", "is_watched", "normalise_watchlist"]

# A kill feed is read, not scrolled. More than this and it is a log file with
# extra steps — which is what Analysis → Server logs already is.
KILLFEED_LIMIT = 100


@dataclass
class PlayerStat:
    """One player's line in the leaderboard."""

    uid: str
    name: str
    kills: int = 0
    deaths: int = 0
    playtime_seconds: int = 0
    sessions: int = 0
    last_seen: str = ""
    watched: bool = False

    @property
    def kd(self) -> float:
        """Kills per death, with a death-less player scoring their kills.

        Dividing by zero is the obvious trap; returning 0 for someone who has
        killed four people and never died is the less obvious one, and it is
        worse — it puts the best player at the bottom.
        """
        if self.deaths:
            return round(self.kills / self.deaths, 2)
        return float(self.kills)


@dataclass
class PlayerStats:
    killfeed: list[dict] = field(default_factory=list)
    leaderboard: list[PlayerStat] = field(default_factory=list)
    totals: dict = field(default_factory=dict)
    adm_found: bool = False
    adm_file: str = ""


def normalise_watchlist(values: list[str]) -> list[str]:
    """Trim, drop blanks, de-duplicate, keep order."""
    out: list[str] = []
    for value in values or []:
        cleaned = (value or "").strip()
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


def is_watched(watchlist: list[str], uid: str, name: str) -> bool:
    """Match on UID or name, case-insensitively for the name.

    Both, because an admin adds whichever they have: a UID from a ban, a name
    from a chat log. A watchlist that only accepted UIDs would mostly be empty.
    """
    if not watchlist:
        return False
    lowered = {w.lower() for w in watchlist}
    return (uid or "").lower() in lowered or (name or "").lower() in lowered


def _profiles_dir(config: DayzConfig):
    return config.server_dir_path / config.profiles_path


def build_player_stats(config: DayzConfig, days: int = 30) -> PlayerStats:
    """Join the .ADM's combat events to the session tracker's playtime.

    A player can appear in one source and not the other — killed on a server
    whose session tracking was off, or a lurker who never fought — so both
    sides create rows and the missing half stays zero rather than dropping
    the player.
    """
    from app.ops import player_ops

    watchlist = normalise_watchlist(getattr(config, "player_watchlist", []))
    stats = PlayerStats()

    by_uid: dict[str, PlayerStat] = {}

    def _row(uid: str, name: str) -> PlayerStat:
        key = uid or f"name:{name.lower()}"
        row = by_uid.get(key)
        if row is None:
            row = PlayerStat(uid=uid, name=name)
            by_uid[key] = row
        elif name and not row.name:
            row.name = name
        return row

    adm_files = adm_parser.find_adm_files(_profiles_dir(config))
    if adm_files:
        stats.adm_found = True
        stats.adm_file = adm_files[0].name
        events = adm_parser.parse_adm_file(adm_files[0])

        kills = adm_parser.filter_kills(events)
        for kill in kills:
            _row(kill.killer_uid, kill.killer).kills += 1
            _row(kill.victim_uid, kill.victim).deaths += 1
        # A non-PvP death still ends a life and still belongs in K/D; a player
        # who bleeds out twelve times is not a 12-0 killer.
        for death in adm_parser.filter_deaths(events):
            _row(death.player_uid, death.player).deaths += 1

        stats.killfeed = [
            {
                "at": k.timestamp,
                "killer": k.killer,
                "killer_uid": k.killer_uid,
                "victim": k.victim,
                "victim_uid": k.victim_uid,
                "weapon": k.weapon,
                "distance": k.distance,
                "summary": k.summary,
                "watched": (
                    is_watched(watchlist, k.killer_uid, k.killer)
                    or is_watched(watchlist, k.victim_uid, k.victim)
                ),
            }
            for k in kills[-KILLFEED_LIMIT:]
        ]
        stats.killfeed.reverse()  # newest first

    sessions = player_ops.compute_sessions(player_ops.parse_session_events(config, max_days=days))
    for summary in player_ops.compute_player_summaries(sessions):
        row = _row(summary.guid, summary.name)
        row.playtime_seconds = summary.total_playtime_seconds
        row.sessions = summary.total_sessions
        row.last_seen = summary.last_seen.isoformat() if summary.last_seen else ""

    for row in by_uid.values():
        row.watched = is_watched(watchlist, row.uid, row.name)

    # Playtime first: it is the one number every player has, and a leaderboard
    # sorted by K/D puts whoever got one lucky kill at the top of an otherwise
    # empty list.
    stats.leaderboard = sorted(
        by_uid.values(), key=lambda r: (r.playtime_seconds, r.kills), reverse=True
    )
    stats.totals = {
        "players": len(stats.leaderboard),
        "kills": sum(r.kills for r in stats.leaderboard),
        "deaths": sum(r.deaths for r in stats.leaderboard),
        "playtime_seconds": sum(r.playtime_seconds for r in stats.leaderboard),
        "watched": sum(1 for r in stats.leaderboard if r.watched),
    }
    return stats
