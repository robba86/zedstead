"""Tests for cli.adm_parser — ADM log file parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.modmgr.mods import breaks_a_line
from app.parsers.adm_parser import (
    ConnectionEvent,
    DeathEvent,
    HitEvent,
    KillEvent,
    filter_by_player,
    filter_deaths,
    filter_hits,
    filter_kills,
    find_adm_files,
    parse_adm_file,
    parse_line,
    tail_adm_file,
)
from tests.conftest import private_fixture


# ---------------------------------------------------------------------------
# parse_line — Kill events
# ---------------------------------------------------------------------------


class TestParseKill:
    def test_standard_kill(self):
        line = '12:34:56 | Player "VictimGuy"(id=abc123) has been killed by player "KillerDude"(id=def456)'
        e = parse_line(line)
        assert isinstance(e, KillEvent)
        assert e.killer == "KillerDude"
        assert e.killer_uid == "def456"
        assert e.victim == "VictimGuy"
        assert e.victim_uid == "abc123"
        assert e.timestamp == "12:34:56"

    def test_kill_with_weapon_and_distance(self):
        line = '14:22:01 | "ShooterMan(uid=aaa111) KILLED TargetGuy(uid=bbb222) with AKM from 150.3 meters'
        e = parse_line(line)
        assert isinstance(e, KillEvent)
        assert e.killer == "ShooterMan"
        assert e.victim == "TargetGuy"
        assert e.weapon == "AKM"
        assert e.distance == pytest.approx(150.3)

    def test_kill_with_weapon_no_distance(self):
        line = '14:22:01 | "ShooterMan(uid=aaa111) KILLED TargetGuy(uid=bbb222) with Fists'
        e = parse_line(line)
        assert isinstance(e, KillEvent)
        assert e.weapon == "Fists"
        assert e.distance == 0.0

    def test_kill_summary(self):
        e = KillEvent(
            timestamp="12:00:00", killer="A", killer_uid="1",
            victim="B", victim_uid="2", weapon="M4A1", distance=200,
        )
        assert e.summary == "A killed B with M4A1 (200m)"


# ---------------------------------------------------------------------------
# parse_line — Hit events
# ---------------------------------------------------------------------------


class TestParseHit:
    def test_shot_hit(self):
        line = '09:15:30 | "Attacker(uid=xxx) SHOT Victim(uid=yyy) by AKM into Head."'
        e = parse_line(line)
        assert isinstance(e, HitEvent)
        assert e.attacker == "Attacker"
        assert e.victim == "Victim"
        assert e.weapon == "AKM"
        assert e.body_part == "Head"

    def test_hit_event(self):
        line = '09:15:30 | "Attacker(uid=xxx) HIT Victim(uid=yyy) by Fists into Torso."'
        e = parse_line(line)
        assert isinstance(e, HitEvent)
        assert e.weapon == "Fists"
        assert e.body_part == "Torso"

    def test_hit_summary(self):
        e = HitEvent(
            timestamp="", attacker="A", attacker_uid="1",
            victim="B", victim_uid="2", weapon="Knife", body_part="LeftArm",
        )
        assert e.summary == "A hit B with Knife in LeftArm"


# ---------------------------------------------------------------------------
# parse_line — Death events
# ---------------------------------------------------------------------------


class TestParseDeath:
    def test_bleedout(self):
        line = '10:00:00 | "PlayerName(uid=abc) DIED Blood <= 0"'
        e = parse_line(line)
        assert isinstance(e, DeathEvent)
        assert e.player == "PlayerName"
        assert e.player_uid == "abc"
        assert "Blood" in e.cause

    def test_fall_death(self):
        line = '11:30:00 | "FallingGuy(uid=xyz) DIED FallDamage"'
        e = parse_line(line)
        assert isinstance(e, DeathEvent)
        assert "FallDamage" in e.cause

    def test_death_summary_with_cause(self):
        e = DeathEvent(timestamp="", player="Bob", player_uid="1", cause="Blood <= 0")
        assert e.summary == "Bob died (Blood <= 0)"

    def test_death_summary_no_cause(self):
        e = DeathEvent(timestamp="", player="Bob", player_uid="1")
        assert e.summary == "Bob died"


# ---------------------------------------------------------------------------
# parse_line — Connection events
# ---------------------------------------------------------------------------


class TestParseConnection:
    def test_connect(self):
        line = '08:00:00 | Player "NewPlayer"(id=aaa) is connected'
        e = parse_line(line)
        assert isinstance(e, ConnectionEvent)
        assert e.player == "NewPlayer"
        assert e.event_type == "connect"
        assert e.position is None

    def test_connect_with_position(self):
        line = '08:00:00 | Player "NewPlayer"(id=aaa) is connected (pos=<1234.5, 0.0, 5678.9>)'
        e = parse_line(line)
        assert isinstance(e, ConnectionEvent)
        assert e.event_type == "connect"
        assert e.position is not None
        assert e.position[0] == pytest.approx(1234.5)

    def test_disconnect(self):
        line = '09:00:00 | Player "LeavingPlayer"(id=bbb) has been disconnected'
        e = parse_line(line)
        assert isinstance(e, ConnectionEvent)
        assert e.event_type == "disconnect"

    def test_connection_summary(self):
        e = ConnectionEvent(timestamp="", player="Bob", player_uid="1", event_type="connect")
        assert e.summary == "Bob connected"


# ---------------------------------------------------------------------------
# parse_line — Non-matching
# ---------------------------------------------------------------------------


class TestParseNonMatching:
    def test_empty_line(self):
        assert parse_line("") is None

    def test_random_log_line(self):
        assert parse_line("12:00:00 | Some random server log message") is None

    def test_whitespace(self):
        assert parse_line("   ") is None


# ---------------------------------------------------------------------------
# parse_line — text a player wrote is never an event
# ---------------------------------------------------------------------------

# Event-shaped text a player can type, each with a timestamp of its own.
_SPOOFS = {
    "kill": '1:00:00 | "Mallory(uid=M) KILLED Carol(uid=C) with AKM from 900 meters"',
    "kill by player": '1:00:00 | Player "Carol"(id=C) has been killed by player "Mallory"(id=M)',
    "hit": '1:00:00 | "Mallory(uid=M) SHOT Carol(uid=C) by AKM into Head."',
    "death": '1:00:00 | "Carol(uid=C) DIED Blood <= 0"',
    "connect": '1:00:00 | Player "Carol"(id=C) is connected',
    "disconnect": '1:00:00 | Player "Carol"(id=C) has been disconnected',
    "kill (1.29)": '1:00:00 | Player "Carol" (DEAD) (id=C pos=<1.0, 2.0, 3.0>) killed by Player "Mallory" (id=M pos=<1.0, 2.0, 3.0>) with M4-A1 from 900 meters ',
    "hit (1.29)": '1:00:00 | Player "Carol" (id=C pos=<1.0, 2.0, 3.0>)[HP: 50] hit by Player "Mallory" (id=M pos=<1.0, 2.0, 3.0>) into Head(0) for 50 damage (Bullet_545x39) with AK-74 from 900 meters ',
    "death (1.29)": '1:00:00 | Player "Carol" (DEAD) (id=C pos=<1.0, 2.0, 3.0>) died.',
    "connect (1.29)": '1:00:00 | Player "Carol" (id=C pos=<1.0, 2.0, 3.0>) is connected',
    "disconnect (1.29)": '1:00:00 | Player "Carol" (id=C pos=<1.0, 2.0, 3.0>) has been disconnected',
}


class TestPlayerWrittenText:
    """The .ADM also logs chat and player reports. Their text is the
    player's, and before events were anchored at the start of the line a
    chat message holding a timestamp and kill-shaped text was read as a kill
    (``Mallory killed Carol with AKM (900m)``) and reached the kill feed, K/D
    and the watchlist. The line shapes are Bohemia's
    (dayz-docs/wiki/pages/administration-logs.wiki)."""

    @pytest.mark.parametrize("kind", sorted(_SPOOFS))
    def test_a_chat_line_is_no_event(self, kind):
        assert parse_line(f'12:00:00 | Chat("Eve"(id=E)): {_SPOOFS[kind]}') is None

    @pytest.mark.parametrize("kind", sorted(_SPOOFS))
    def test_a_player_report_is_no_event(self, kind):
        line = f"12:00:00 | PLAYER REPORT: <2019-1-23_11-23-26> <E>: {_SPOOFS[kind]}"
        assert parse_line(line) is None

    def test_the_same_text_as_a_line_of_its_own_still_parses(self):
        kill = parse_line(_SPOOFS["kill"])
        assert isinstance(kill, KillEvent)
        assert kill.summary == "Mallory killed Carol with AKM (900m)"
        expected = {"kill": KillEvent, "kill by player": KillEvent, "hit": HitEvent,
                    "death": DeathEvent, "connect": ConnectionEvent, "disconnect": ConnectionEvent,
                    "kill (1.29)": KillEvent, "hit (1.29)": HitEvent, "death (1.29)": DeathEvent,
                    "connect (1.29)": ConnectionEvent, "disconnect (1.29)": ConnectionEvent}
        assert set(expected) == set(_SPOOFS)
        for kind, cls in expected.items():
            assert isinstance(parse_line(_SPOOFS[kind]), cls), kind

    def test_a_one_digit_hour_still_parses(self):
        """Bohemia documents HH:MM:SS; the .RPT pads the hour with a space
        before 10:00, so both are accepted."""
        for line in (
            ' 8:41:33 | Player "A"(id=1) is connected',
            '8:41:33 | Player "A"(id=1) is connected',
            '2026/10/03, 8:41:33 | Player "A"(id=1) is connected',
        ):
            assert isinstance(parse_line(line), ConnectionEvent), line

    def test_a_file_reads_chat_as_chat(self, tmp_path: Path):
        adm = tmp_path / "test.ADM"
        adm.write_text(
            '12:00:00 | Player "Mallory"(id=M) is connected\n'
            f'12:00:30 | Chat("Mallory"(id=M)): {_SPOOFS["kill"]}\n'
            '12:01:00 | "Mallory(uid=M) KILLED Carol(uid=C) with Fists"\n',
            encoding="utf-8",
        )
        kills = filter_kills(parse_adm_file(adm))
        assert [(k.killer, k.victim, k.weapon) for k in kills] == [("Mallory", "Carol", "Fists")]


class TestANameShapedLikeAKill:
    """A player's name is the player's text too. The old `has been killed by
    player` pattern had no end and was tried first, so a player named like
    that line made every line the game wrote about them a kill by someone
    else: 23 forged kills from one connect, 20 emotes and a disconnect."""

    MAL, ALICE, BOB = "M" * 43 + "=", "A" * 43 + "=", "B" * 43 + "="
    NAMES = [
        f'Bob"(id={BOB}) has been killed by player "Alice"(id={ALICE})',
        'Bob"(id=1) has been killed by player "Alice"(id=2)',
    ]

    def _prefix(self, name: str, dead: bool = False) -> str:
        return f'Player "{name}"{" (DEAD)" if dead else ""} (id={self.MAL} pos=<1132.0, 2427.6, 5.9>)'

    @pytest.mark.parametrize("name", NAMES)
    @pytest.mark.parametrize("tail", [
        " is connected", " has been disconnected", " performed EmoteSitA", " placed Fireplace",
        "[HP: 74] hit by FallDamage", " died. Stats> Water: 1 Energy: 2 Bleed sources: 0",
    ])
    def test_no_line_about_that_player_is_a_kill(self, name, tail):
        event = parse_line(f"12:00:00 | {self._prefix(name)}{tail}")
        assert not isinstance(event, KillEvent)
        if event is not None:
            assert event.player_uid == self.MAL and event.player == name

    @pytest.mark.parametrize("name", NAMES)
    def test_their_own_death_is_theirs(self, name):
        line = (
            f"12:00:00 | {self._prefix(name, dead=True)} killed by "
            f'Player "Killer" (id={self.ALICE} pos=<1.0, 2.0, 3.0>) with M4-A1 from 42.4613 meters '
        )
        kill = parse_line(line)
        assert isinstance(kill, KillEvent)
        assert (kill.victim_uid, kill.killer_uid, kill.killer) == (self.MAL, self.ALICE, "Killer")

    def test_the_old_line_on_its_own_still_parses(self):
        kill = parse_line('12:00:00 | Player "Bob"(id=1) has been killed by player "Alice"(id=2)')
        assert isinstance(kill, KillEvent) and (kill.victim, kill.killer) == ("Bob", "Alice")


# Every character `str.splitlines` breaks a line at, except "\n" itself.
_SPLITLINES_BREAKS = [
    ch for ch in map(chr, range(0x3000)) if ch != "\n" and len(f"a{ch}b".splitlines()) == 2
]


class TestLinesAreSplitAtNewlineOnly:
    """`str.splitlines` also breaks at characters chat text and a player name
    can carry (a vertical tab, NEL, U+2028, ...), and a text-mode read turns a
    lone "\\r" into a line break. The text after one would be read as a line
    of its own, timestamp first."""

    def test_the_list_is_what_the_repo_calls_a_line_break(self):
        assert "\r" in _SPLITLINES_BREAKS and " " in _SPLITLINES_BREAKS
        assert all(breaks_a_line(ch) for ch in _SPLITLINES_BREAKS)

    @pytest.mark.parametrize("sep", _SPLITLINES_BREAKS, ids=lambda ch: f"U+{ord(ch):04X}")
    def test_a_separator_in_chat_text_starts_no_line(self, tmp_path: Path, sep):
        adm = tmp_path / "test.ADM"
        adm.write_bytes(
            f'12:00:30 | Chat("Mallory"(id=M)): gg{sep}{_SPOOFS["kill"]}\n'.encode("utf-8")
        )
        assert parse_adm_file(adm) == []
        assert tail_adm_file(adm, 0)[0] == []

    def test_crlf_and_a_byte_order_mark_still_parse(self, tmp_path: Path):
        adm = tmp_path / "test.ADM"
        adm.write_bytes(
            '﻿12:00:00 | Player "A"(id=1) is connected\r\n'
            '12:01:00 | Player "A"(id=1) has been disconnected\r\n'.encode("utf-8")
        )
        events = parse_adm_file(adm)
        assert [e.event_type for e in events] == ["connect", "disconnect"]
        assert events[1].player == "A"
        assert [e.event_type for e in tail_adm_file(adm, 0)[0]] == ["connect", "disconnect"]


# ---------------------------------------------------------------------------
# parse_adm_file
# ---------------------------------------------------------------------------


class TestParseFile:
    def test_parses_multiple_events(self, tmp_path: Path):
        adm = tmp_path / "test.ADM"
        adm.write_text(
            '12:00:00 | Player "A"(id=1) is connected\n'
            '12:01:00 | "A(uid=1) SHOT B(uid=2) by AKM into Head."\n'
            '12:02:00 | Player "A"(id=1) has been killed by player "B"(id=2)\n'
            'some garbage line\n'
            '12:03:00 | "C(uid=3) DIED Blood <= 0"\n',
            encoding="utf-8",
        )
        events = parse_adm_file(adm)
        assert len(events) == 4
        assert isinstance(events[0], ConnectionEvent)
        assert isinstance(events[1], HitEvent)
        assert isinstance(events[2], KillEvent)
        assert isinstance(events[3], DeathEvent)

    def test_missing_file(self, tmp_path: Path):
        assert parse_adm_file(tmp_path / "missing.ADM") == []


# ---------------------------------------------------------------------------
# What the game writes (DayZ 1.29)
# ---------------------------------------------------------------------------

# Two real 1.29/1.30 ADMs with names and ids replaced, in the private fixtures
# (adm/reconnect.ADM, adm/expansion-ai.ADM; plans/86 D2): the player is
# Survivor1, the DayZ GUID and the Steam64 id are placeholders of the same
# length. Every other byte is the file's own, the trailing space after "meters"
# included; the second leaves out lines whose shape another line already shows.
# Its AI lines are Expansion's (Dynamic AI fighting itself).
_GUID = "Guid1_placeholder_xxxxxxxxxxxxxxxxxxxxxxxxx="
_STEAM64 = "76561190000000001"

# The prefix Bohemia's GetPlayerPrefix writes (pluginadminlog.c, 1.29.163709).
_V = f'Player "Survivor1" (DEAD) (id={_GUID} pos=<1132.0, 2427.6, 5.9>)'
_K = 'Player "Survivor2" (id=Guid2-placeholder-xxxxxxxxxxxxxxxxxxxxxxxxx= pos=<1139.3, 2425.2, 5.9>)'
_ALIVE = f'Player "Survivor1" (id={_GUID} pos=<1132.0, 2427.6, 5.9>)'
_AI = 'AI "Kaito" (group=56:"Elektrozavodsk North-West" faction="Lunatics" pos=<10242.1, 2270.6, 17.7>)'


class TestRealAdm:
    """Before the parser knew Bohemia's prefix, a real .ADM gave no event at
    all: every line has a space before "(id=" and the position inside it."""

    def _events(self, tmp_path: Path, text: str):
        adm = tmp_path / "DayZServer_x64.ADM"
        adm.write_bytes(text.encode("utf-8"))
        return parse_adm_file(adm)

    def test_a_reconnect_gives_its_connects_and_disconnects(self, tmp_path: Path):
        events = self._events(tmp_path, private_fixture("adm/reconnect.ADM"))
        assert [(e.timestamp, e.event_type, e.player, e.player_uid, e.position) for e in events] == [
            ("13:26:59", "disconnect", "Survivor1", _STEAM64, None),
            ("13:27:31", "connect", "Survivor1", _GUID, (1132.0, 2427.6, 5.9)),
            ("13:28:03", "disconnect", "Survivor1", _GUID, (1139.4, 2425.2, 5.9)),
        ]

    def test_ai_lines_are_no_players_event(self, tmp_path: Path):
        events = self._events(tmp_path, private_fixture("adm/expansion-ai.ADM"))
        assert [(e.timestamp, e.event_type, e.position) for e in events] == [
            ("20:18:30", "connect", (13329.6, 7030.3, 2.1)),
            ("20:25:51", "disconnect", (1121.1, 2418.2, 5.6)),
        ]
        assert tail_adm_file(tmp_path / "DayZServer_x64.ADM", 0)[0] == events


# Invented, in the shapes of the wiki's examples (administration-logs.wiki):
# a space before `(id=` or none, a comma before `pos=` or none, the wiki's
# "Bleeding Sources", and two ids where the wiki has one.
_WIKI_SHAPED = {
    "connect": ('Player "Wanderer" is connected (id=TESTGUID1)', ConnectionEvent),
    "disconnect": ('Player "Wanderer"(id=TESTGUID1) has been disconnected', ConnectionEvent),
    "hit": ('Player "Wanderer A"(id=TESTGUID1 pos=<2210.4, 7815.2, 140.3>)[HP: 61] hit by "Wanderer B"(id=TESTGUID2 '
            'pos=<2212.0, 7811.9, 140.1>) into LeftLeg(22) for 39 damage (Bullet_TestRound) with TestPistol '
            'from 3.48127 meters', HitEvent),
    "kill": ('Player "Wanderer A"(id=TESTGUID1, pos=<6020.5, 4410.7, 12.0>) killed by "Wanderer B"(id=TESTGUID2, '
             'pos=<6020.5, 4410.7, 12.0>) with TestRifle-7 from 57 meters', KillEvent),
    "killed by infected": ('Player "Wanderer"(id=TESTGUID1, pos=<6020.5, 4410.7, 12.0>) killed by Infected', DeathEvent),
    "died": ('Player "Wanderer"(id=TESTGUID1, pos=<6020.5, 4410.7, 12.0>) died. Stats> Water: 312.5 Energy: 811.25 '
             'Bleeding Sources: 1', DeathEvent),
    "hit by bear": ('Player "Wanderer" (id=TESTGUID1 pos=<2210.4, 7815.2, 140.3>)[HP: 55.5] hit by Bear into RightArm(19) '
                    'for 15 damage (MeleeBear)', None),
    "fall": ('Player "Wanderer" (id=TESTGUID1, pos=<6020.5, 4410.7, 12.0>)[HP: 88.1] hit by FallDamage', None),
    "fireplace": ('Player "Wanderer" (id=TESTGUID1 pos=<2210.4, 7815.2, 140.3>)[HP: 90.2] hit by FireplaceIndoor with FireDamage', None),
    "suicide": ('Player "Wanderer"(id=TESTGUID1, pos=<6020.5, 4410.7, 12.0>) committed suicide', None),
    "bled out": ('Player "Wanderer"(id=TESTGUID1, pos=<6020.5, 4410.7, 12.0>) bled out', None),
    "unconscious": ('Player "Wanderer"(id=TESTGUID1, pos=<6020.5, 4410.7, 12.0>) is unconscious', None),
    "conscious": ('Player "Wanderer"(id=TESTGUID1, pos=<6020.5, 4410.7, 12.0>) regained consciousness', None),
    "placed": ('Player "Wanderer"(id=TESTGUID1, pos=<6020.5, 4410.7, 12.0>) placed Tripwire', None),
    "built": ('Player "Wanderer" (id=TESTGUID1 pos=<2210.4, 7815.2, 140.3>) built Watchtower with Hammer', None),
    "player list": ('Player "Wanderer A" (id=TESTGUID1 pos=<1840.6, 3012.9, 4.2>)', None),
}


class TestBohemiasShapes:
    @pytest.mark.parametrize("kind", sorted(_WIKI_SHAPED))
    def test_each_wiki_example_gives_its_event(self, kind):
        line, cls = _WIKI_SHAPED[kind]
        event = parse_line(f"12:00:00 | {line}")
        assert (type(event) if event else None) is cls

    def test_the_wiki_kill_and_hit_carry_their_details(self):
        kill = parse_line(f"12:00:00 | {_WIKI_SHAPED['kill'][0]}")
        assert (kill.killer, kill.killer_uid, kill.victim, kill.victim_uid) == (
            "Wanderer B", "TESTGUID2", "Wanderer A", "TESTGUID1")
        assert (kill.weapon, kill.distance) == ("TestRifle-7", 57.0)
        hit = parse_line(f"12:00:00 | {_WIKI_SHAPED['hit'][0]}")
        assert (hit.attacker, hit.victim, hit.weapon, hit.body_part) == (
            "Wanderer B", "Wanderer A", "TestPistol", "LeftLeg")

    @pytest.mark.parametrize(("tail", "weapon", "distance"), [
        (" with M4-A1 from 42.4613 meters ", "M4-A1", 42.4613),
        (" with Sickle", "Sickle", 0.0),
        (" with (MeleeFist)", "(MeleeFist)", 0.0),
    ])
    def test_a_player_killed_by_a_player(self, tail, weapon, distance):
        kill = parse_line(f"12:00:00 | {_V} killed by {_K}{tail}")
        assert isinstance(kill, KillEvent)
        assert (kill.victim, kill.victim_uid, kill.killer) == ("Survivor1", _GUID, "Survivor2")
        assert kill.killer_uid == "Guid2-placeholder-xxxxxxxxxxxxxxxxxxxxxxxxx="
        assert (kill.weapon, kill.distance) == (weapon, distance)

    def test_a_shot_of_ones_own_is_a_death_not_a_kill(self):
        line = f"12:00:00 | {_V} killed by {_V} with Mosin 91/30 from 0 meters "
        death = parse_line(line)
        assert isinstance(death, DeathEvent)
        assert (death.player_uid, death.cause) == (_GUID, "killed self with Mosin 91/30")

    @pytest.mark.parametrize(("tail", "cause"), [
        (" died. Stats> Water: 159.865 Energy: 286.497 Bleed sources: 3", ""),
        (" died.", ""),
        (" drowned. Stats> Water: 2810 Energy: 1720.5 Bleed sources: 0", "drowned"),
        (" killed by ZmbM_HermitSkinny_Beige", "killed by ZmbM_HermitSkinny_Beige"),
        (" killed by Land Mine", "killed by Land Mine"),
        (f" killed by {_AI} with IJ-70 from 5.35612 meters ", "killed by AI Kaito with IJ-70"),
    ])
    def test_a_death_of_no_players_doing(self, tail, cause):
        death = parse_line(f"12:00:00 | {_V}{tail}")
        assert isinstance(death, DeathEvent)
        assert (death.player, death.player_uid, death.cause) == ("Survivor1", _GUID, cause)

    @pytest.mark.parametrize(("hit", "weapon", "zone"), [
        (" into Torso(16) for 26.6 damage (Bullet_380) with IJ-70 from 1.63938 meters ", "IJ-70", "Torso"),
        (" into Head(0) for 10 damage (MeleeFist)", "", "Head"),
        (" into Block(16) for 0 damage  with Sickle", "Sickle", "Block"),
    ])
    def test_a_player_hit_by_a_player(self, hit, weapon, zone):
        event = parse_line(f"12:00:00 | {_ALIVE}[HP: 73.4] hit by {_K}{hit}")
        assert isinstance(event, HitEvent)
        assert (event.attacker, event.victim, event.weapon, event.body_part) == (
            "Survivor2", "Survivor1", weapon, zone)

    @pytest.mark.parametrize("source", [
        "ZmbF_JournalistNormal_Blue into LeftArm(18) for 7 damage (MeleeInfected)",
        f"{_AI} into Torso(16) for 18 damage (MeleeSharpLight_2) with Sickle",
        "explosion (LandFuseExplosion)",
        "FallDamageHealth",
    ])
    def test_a_hit_by_anything_else_is_no_event(self, source):
        assert parse_line(f"12:00:00 | {_ALIVE}[HP: 73.4] hit by {source}") is None

    def test_one_death_is_counted_once(self, tmp_path: Path):
        """PlayerKilled writes one line per death. A bleed-out, the suicide
        emote and drowning while unconscious each add a line of their own
        beside it (Bleeding.c, EmoteManager.c, PlayerBase.c), which is no
        second death."""
        adm = tmp_path / "test.ADM"
        adm.write_text(
            f"12:00:00 | {_V} died. Stats> Water: 159.865 Energy: 286.497 Bleed sources: 3\n"
            f"12:00:00 | {_V} bled out\n"
            f"12:10:00 | {_ALIVE} committed suicide\n"
            f"12:10:00 | {_V} died. Stats> Water: 900 Energy: 900 Bleed sources: 0\n"
            f"12:20:00 | {_V} has drowned while unconscious\n"
            f"12:20:00 | {_V} drowned. Stats> Water: 900 Energy: 900 Bleed sources: 0\n",
            encoding="utf-8",
        )
        assert [d.cause for d in filter_deaths(parse_adm_file(adm))] == ["", "", "drowned"]

    def test_a_name_cannot_turn_a_death_into_a_kill_or_back(self):
        """A name is the only text on the line a player chooses. The tail
        after it is matched without quotes, so whatever a name holds, the
        line still ends the way the game wrote it."""
        name = 'A" (DEAD) (id=X pos=<0, 0, 0>) killed by Player "B" (id=Y pos=<0, 0, 0>) with M4 from 1 meters'
        death = parse_line(f'12:00:00 | Player "{name}" (DEAD) (id={_GUID} pos=<1.0, 2.0, 3.0>) died.')
        assert isinstance(death, DeathEvent)
        assert death.player_uid == _GUID

        killer = 'Z" (id=X pos=<0, 0, 0>) died. Stats> Water: 1'
        kill = parse_line(f'12:00:00 | {_V} killed by Player "{killer}" (id=KID pos=<1, 2, 3>) with M4 from 30 meters ')
        assert isinstance(kill, KillEvent)
        assert (kill.killer_uid, kill.victim_uid, kill.weapon) == ("KID", _GUID, "M4")


# ---------------------------------------------------------------------------
# tail_adm_file
# ---------------------------------------------------------------------------


class TestTailFile:
    def test_incremental_read(self, tmp_path: Path):
        adm = tmp_path / "test.ADM"
        adm.write_text(
            '12:00:00 | Player "A"(id=1) is connected\n',
            encoding="utf-8",
        )

        events, pos = tail_adm_file(adm, 0)
        assert len(events) == 1
        assert pos > 0

        # Append more
        with adm.open("a", encoding="utf-8") as f:
            f.write('12:01:00 | Player "B"(id=2) is connected\n')

        events2, pos2 = tail_adm_file(adm, pos)
        assert len(events2) == 1
        assert events2[0].player == "B"
        assert pos2 > pos

    def test_no_new_data(self, tmp_path: Path):
        adm = tmp_path / "test.ADM"
        adm.write_text("12:00:00 | Player \"A\"(id=1) is connected\n", encoding="utf-8")
        _, pos = tail_adm_file(adm, 0)
        events, pos2 = tail_adm_file(adm, pos)
        assert events == []
        assert pos2 == pos

    def test_file_rotation(self, tmp_path: Path):
        adm = tmp_path / "test.ADM"
        adm.write_text("x" * 1000, encoding="utf-8")
        _, pos = tail_adm_file(adm, 0)

        # Simulate rotation — file is smaller now
        adm.write_text('12:00:00 | Player "A"(id=1) is connected\n', encoding="utf-8")
        events, new_pos = tail_adm_file(adm, pos)
        assert len(events) == 1  # Reads from start

    def test_missing_file(self, tmp_path: Path):
        events, pos = tail_adm_file(tmp_path / "missing.ADM", 100)
        assert events == []
        assert pos == 0


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


class TestFilters:
    @pytest.fixture()
    def mixed_events(self) -> list:
        return [
            KillEvent("", "A", "1", "B", "2", "AKM", 100),
            HitEvent("", "A", "1", "B", "2", "AKM", "Head"),
            DeathEvent("", "C", "3", "Blood"),
            ConnectionEvent("", "A", "1", "connect"),
        ]

    def test_filter_kills(self, mixed_events):
        assert len(filter_kills(mixed_events)) == 1

    def test_filter_deaths(self, mixed_events):
        assert len(filter_deaths(mixed_events)) == 1

    def test_filter_hits(self, mixed_events):
        assert len(filter_hits(mixed_events)) == 1

    def test_filter_by_player(self, mixed_events):
        result = filter_by_player(mixed_events, "1")
        assert len(result) == 3  # kill, hit, connection

    def test_filter_by_player_no_match(self, mixed_events):
        assert filter_by_player(mixed_events, "999") == []


# ---------------------------------------------------------------------------
# find_adm_files
# ---------------------------------------------------------------------------


class TestFindAdmFiles:
    def test_finds_adm_files(self, tmp_path: Path):
        (tmp_path / "DayZServer_x64.ADM").write_text("log1", encoding="utf-8")
        (tmp_path / "DayZServer_x64_2024-01-01.ADM").write_text("log2", encoding="utf-8")
        (tmp_path / "other.log").write_text("not adm", encoding="utf-8")
        files = find_adm_files(tmp_path)
        assert len(files) == 2
        assert all(f.suffix == ".ADM" for f in files)

    def test_empty_dir(self, tmp_path: Path):
        assert find_adm_files(tmp_path) == []

    def test_missing_dir(self, tmp_path: Path):
        assert find_adm_files(tmp_path / "nonexistent") == []
