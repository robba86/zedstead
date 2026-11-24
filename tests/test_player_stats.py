"""Kill feed, K/D, playtime leaderboard and the watchlist (TODO D3).

zsm already had both halves and never joined them: `adm_parser` reads the
.ADM, `player_ops` reads the session tracker. These pin the join, and the two
judgement calls inside it — what K/D means for somebody who has never died,
and what the leaderboard sorts on.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.ops import player_stats

PASSWORD = "testpass123"

# Verbatim shapes from app/parsers/adm_parser.py's own patterns.
ADM = """12:00:01 | "Alice(uid=AAA) KILLED Bob(uid=BBB) with AKM from 120.5 meters"
12:05:00 | "Alice(uid=AAA) KILLED Carol(uid=CCC) with M4A1 from 12 meters"
12:10:00 | "Bob(uid=BBB) DIED Blood <= 0"
12:11:00 | "Dave(uid=DDD) DIED Falling"
"""


@pytest.fixture()
def adm(sample_config):
    profiles = sample_config.server_dir_path / sample_config.profiles_path
    profiles.mkdir(parents=True, exist_ok=True)
    path = profiles / "DayZServer_x64.ADM"
    path.write_text(ADM, encoding="utf-8")
    return path


@pytest.fixture()
def no_sessions(monkeypatch):
    """Keeps the leaderboard's playtime half out of the way of the .ADM half."""
    from app.ops import player_ops

    monkeypatch.setattr(player_ops, "parse_session_events", lambda config, max_days=30: [])


def test_kills_and_deaths_are_counted_per_player(sample_config, adm, no_sessions):
    stats = player_stats.build_player_stats(sample_config)
    rows = {r.name: r for r in stats.leaderboard}

    assert rows["Alice"].kills == 2
    assert rows["Alice"].deaths == 0
    # Bob was killed once by Alice and bled out once: both end a life.
    assert rows["Bob"].deaths == 2
    assert rows["Carol"].deaths == 1
    assert rows["Dave"].deaths == 1


def test_kd_from_the_lines_the_game_writes(sample_config, no_sessions):
    """The shapes of Bohemia's pluginadminlog.c (1.29). A bleed-out writes
    "died." and "bled out", one death; a shot of one's own is no kill."""
    a = 'Player "Survivor1" (id=Guid1 pos=<1.0, 2.0, 3.0>)'
    a_dead = 'Player "Survivor1" (DEAD) (id=Guid1 pos=<1.0, 2.0, 3.0>)'
    b_dead = 'Player "Survivor2" (DEAD) (id=Guid2 pos=<4.0, 5.0, 6.0>)'
    profiles = sample_config.server_dir_path / sample_config.profiles_path
    profiles.mkdir(parents=True, exist_ok=True)
    (profiles / "DayZServer_x64.ADM").write_text(
        f"12:00:00 | {b_dead} killed by {a} with M4-A1 from 42.4613 meters \n"
        f"12:05:00 | {a_dead} died. Stats> Water: 159.865 Energy: 286.497 Bleed sources: 3\n"
        f"12:05:00 | {a_dead} bled out\n"
        f"12:10:00 | {b_dead} killed by {b_dead} with Mosin 91/30 from 0 meters \n",
        encoding="utf-8",
    )
    stats = player_stats.build_player_stats(sample_config)
    rows = {r.uid: r for r in stats.leaderboard}
    assert (rows["Guid1"].kills, rows["Guid1"].deaths) == (1, 1)
    assert (rows["Guid2"].kills, rows["Guid2"].deaths) == (0, 2)
    assert [(k["killer"], k["weapon"], k["distance"]) for k in stats.killfeed] == [
        ("Survivor1", "M4-A1", 42.4613)
    ]


def test_a_player_who_has_never_died_is_not_scored_zero(sample_config, adm, no_sessions):
    """Dividing by zero is the obvious trap. Returning 0 for someone with two
    kills and no deaths is the less obvious one, and it is worse — it puts the
    best player at the bottom."""
    rows = {r.name: r for r in player_stats.build_player_stats(sample_config).leaderboard}
    assert rows["Alice"].kd == 2.0
    assert rows["Bob"].kd == 0.0


def test_the_killfeed_is_newest_first_and_carries_the_details(sample_config, adm, no_sessions):
    feed = player_stats.build_player_stats(sample_config).killfeed
    assert [k["victim"] for k in feed] == ["Carol", "Bob"]
    assert feed[-1]["weapon"] == "AKM"
    assert feed[-1]["distance"] == 120.5


def test_no_adm_log_is_not_an_error(sample_config, no_sessions):
    """The .ADM only exists with -adminLog, and a server without it is not
    broken — it just has nothing to say here."""
    stats = player_stats.build_player_stats(sample_config)
    assert stats.adm_found is False
    assert stats.killfeed == []
    assert stats.totals["kills"] == 0


def test_playtime_and_combat_are_joined_on_the_same_row(sample_config, adm, monkeypatch):
    """A player can be in one source and not the other. Both sides create a
    row; the missing half stays zero rather than dropping the player."""
    from datetime import datetime, timezone

    from app.ops import player_ops

    monkeypatch.setattr(player_ops, "parse_session_events", lambda config, max_days=30: [])
    monkeypatch.setattr(
        player_ops,
        "compute_player_summaries",
        lambda sessions, online_guids=None: [
            player_ops.PlayerSummary(
                guid="AAA",
                name="Alice",
                total_sessions=3,
                total_playtime_seconds=7200,
                first_seen=datetime(2026, 9, 1, tzinfo=timezone.utc),
                last_seen=datetime(2026, 9, 21, tzinfo=timezone.utc),
                is_online=False,
            ),
            player_ops.PlayerSummary(
                guid="ZZZ",
                name="Lurker",
                total_sessions=9,
                total_playtime_seconds=99999,
                first_seen=datetime(2026, 9, 1, tzinfo=timezone.utc),
                last_seen=datetime(2026, 9, 21, tzinfo=timezone.utc),
                is_online=False,
            ),
        ],
    )

    stats = player_stats.build_player_stats(sample_config)
    rows = {r.name: r for r in stats.leaderboard}

    assert rows["Alice"].kills == 2 and rows["Alice"].playtime_seconds == 7200
    # Never fought, still listed.
    assert rows["Lurker"].kills == 0 and rows["Lurker"].sessions == 9
    # Never played a tracked session, still listed.
    assert rows["Carol"].playtime_seconds == 0

    # Playtime first: sorting by K/D puts whoever got one lucky kill at the top
    # of an otherwise empty list.
    assert stats.leaderboard[0].name == "Lurker"


# ---------------------------------------------------------------------------
# watchlist
# ---------------------------------------------------------------------------


def test_the_watchlist_matches_a_uid_or_a_name(sample_config, adm, no_sessions):
    sample_config.player_watchlist = ["BBB", "carol"]
    stats = player_stats.build_player_stats(sample_config)
    rows = {r.name: r for r in stats.leaderboard}

    assert rows["Bob"].watched is True  # by UID
    assert rows["Carol"].watched is True  # by name, case-insensitively
    assert rows["Alice"].watched is False
    assert stats.totals["watched"] == 2


def test_a_killfeed_line_is_flagged_if_either_side_is_watched(sample_config, adm, no_sessions):
    sample_config.player_watchlist = ["Carol"]
    feed = player_stats.build_player_stats(sample_config).killfeed
    flagged = [k for k in feed if k["watched"]]
    assert [k["victim"] for k in flagged] == ["Carol"]


def test_the_watchlist_is_trimmed_and_deduplicated_in_order():
    assert player_stats.normalise_watchlist(
        ["  Alice ", "", "Alice", "BBB", "   "]
    ) == ["Alice", "BBB"]


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(sample_config):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    tclient = TestClient(server.create_app(sample_config))
    tclient.post("/api/auth/login", json={"password": PASSWORD})
    return tclient


def test_stats_needs_a_session(sample_config):
    from app.web import server

    app = server.create_app(sample_config)
    assert TestClient(app).get("/api/players/stats").status_code == 401


def test_the_stats_endpoint_serialises_kd(client, adm, no_sessions):
    body = client.get("/api/players/stats").json()
    alice = next(r for r in body["leaderboard"] if r["name"] == "Alice")
    # A property, so it only reaches the browser if the route adds it.
    assert alice["kd"] == 2.0
    assert body["adm_found"] is True


def test_the_watchlist_round_trips_through_the_endpoints(client, sample_config):
    assert client.get("/api/players/watchlist").json()["entries"] == []

    resp = client.post("/api/players/watchlist", json={"entry": "  Griefer  "})
    assert resp.status_code == 200
    assert resp.json()["entries"] == ["Griefer"]

    # Adding it twice does not duplicate it.
    assert client.post("/api/players/watchlist", json={"entry": "Griefer"}).json()[
        "entries"
    ] == ["Griefer"]

    assert client.request("DELETE", "/api/players/watchlist?entry=Griefer").status_code == 200
    assert client.get("/api/players/watchlist").json()["entries"] == []


def test_an_empty_watchlist_entry_is_refused(client):
    assert client.post("/api/players/watchlist", json={"entry": "   "}).status_code == 400


def test_removing_something_not_on_it_is_a_404(client):
    assert client.request("DELETE", "/api/players/watchlist?entry=nobody").status_code == 404


# ---------------------------------------------------------------------------
# a parser bug this feature found
# ---------------------------------------------------------------------------


def test_the_modules_own_documented_kill_example_parses():
    """`KILL_WEAPON_PATTERN`'s comment shows a line ending in a quote — the
    line opens with one — and until 2026 Q3 the pattern had nowhere to put
    it. `$` could not be reached with the distance group taken, so the group
    was always skipped and the lazy weapon group ate the rest: every quoted
    kill line reported `weapon="AKM from 150.3 meters\"" and `distance=0.0`.
    """
    from app.parsers.adm_parser import parse_line

    event = parse_line(
        '12:00:01 | "Killer(uid=abc) KILLED Victim(uid=def) with AKM from 150.3 meters"'
    )
    assert event.weapon == "AKM"
    assert event.distance == 150.3


def test_an_unquoted_kill_line_without_a_distance_still_parses():
    """The quote and the distance are both optional; neither may become
    required by fixing the other."""
    from app.parsers.adm_parser import parse_line

    event = parse_line('12:00:01 | "Killer(uid=abc) KILLED Victim(uid=def) with Fists')
    assert event.weapon == "Fists"
    assert event.distance == 0.0
