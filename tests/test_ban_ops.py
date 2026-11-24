"""Tests for cli.ops.ban_ops — ban parsing, CRUD, search, import/export."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from tests.conftest import private_fixture

from app.ops.ban_ops import (
    BanEntry,
    BanStats,
    ImportResult,
    add_ban,
    cleanup_expired,
    enrich_with_metadata,
    enrich_with_sessions,
    export_bans,
    get_ban_stats,
    import_bans,
    read_bans,
    remove_ban,
    search_bans,
    write_bans,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_BANS = """\
// BattlEye GUID bans
abcdef01234567890abcdef012345678 -1 Cheating - aimbot
fedcba98765432100fedcba987654321 {temp_expiry} Temp ban - griefing
// IP bans
192.168.1.50 -1 IP ban - VPN abuse
"""


@pytest.fixture()
def bans_file(tmp_path: Path) -> Path:
    p = tmp_path / "bans.txt"
    # Set temp ban expiry to 1 hour from now
    content = SAMPLE_BANS.format(temp_expiry=int(time.time()) + 3600)
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture()
def expired_bans_file(tmp_path: Path) -> Path:
    p = tmp_path / "bans.txt"
    content = SAMPLE_BANS.format(temp_expiry=int(time.time()) - 3600)  # expired
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture()
def meta(tmp_path: Path) -> Path:
    return tmp_path / "ban_metadata.json"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


class TestReadBans:
    def test_parse_all_entries(self, bans_file: Path):
        bans = read_bans(bans_file)
        assert len(bans) == 3

    def test_guid_ban(self, bans_file: Path):
        bans = read_bans(bans_file)
        guid_ban = bans[0]
        assert guid_ban.ban_type == "guid"
        assert guid_ban.identifier == "abcdef01234567890abcdef012345678"
        assert guid_ban.expiry == -1
        assert guid_ban.reason == "Cheating - aimbot"
        assert guid_ban.is_permanent is True

    def test_temp_ban(self, bans_file: Path):
        bans = read_bans(bans_file)
        temp_ban = bans[1]
        assert temp_ban.ban_type == "guid"
        assert temp_ban.is_permanent is False
        assert temp_ban.is_active is True
        assert "m" in temp_ban.time_remaining or "h" in temp_ban.time_remaining

    def test_ip_ban(self, bans_file: Path):
        bans = read_bans(bans_file)
        ip_ban = bans[2]
        assert ip_ban.ban_type == "ip"
        assert ip_ban.identifier == "192.168.1.50"
        assert ip_ban.is_permanent is True

    def test_comments_skipped(self, bans_file: Path):
        bans = read_bans(bans_file)
        assert all(not b.identifier.startswith("//") for b in bans)

    def test_hash_comments_skipped(self, tmp_path: Path):
        p = tmp_path / "bans.txt"
        p.write_text(
            "# old-style comment\n"
            "abcdef01234567890abcdef012345678 -1 test\n",
            encoding="utf-8",
        )
        bans = read_bans(p)
        assert len(bans) == 1

    def test_missing_file(self, tmp_path: Path):
        assert read_bans(tmp_path / "nonexistent.txt") == []

    def test_empty_file(self, tmp_path: Path):
        p = tmp_path / "bans.txt"
        p.write_text("", encoding="utf-8")
        assert read_bans(p) == []

    def test_malformed_lines_skipped(self, tmp_path: Path):
        p = tmp_path / "bans.txt"
        p.write_text("bad_line\nabc123 not_a_number reason\n", encoding="utf-8")
        assert read_bans(p) == []


class TestWriteBans:
    def test_roundtrip(self, bans_file: Path, tmp_path: Path):
        bans = read_bans(bans_file)
        out = tmp_path / "out.txt"
        write_bans(out, bans)
        reloaded = read_bans(out)
        assert len(reloaded) == len(bans)
        for orig, loaded in zip(bans, reloaded):
            assert orig.identifier == loaded.identifier
            assert orig.expiry == loaded.expiry
            assert orig.reason == loaded.reason

    def test_creates_parent_dirs(self, tmp_path: Path):
        p = tmp_path / "sub" / "dir" / "bans.txt"
        write_bans(p, [BanEntry("abc" * 10 + "ab", "guid", 0, "test")])
        assert p.is_file()

    def test_write_leaves_no_temp_file(self, tmp_path: Path):
        # Atomic write must not leave a temp file beside bans.txt.
        p = tmp_path / "bans.txt"
        write_bans(p, [BanEntry("abc" * 10 + "ab", "guid", 0, "test")])
        leftovers = [f.name for f in tmp_path.iterdir() if f.name != "bans.txt"]
        assert leftovers == []

    def test_existing_bans_survive_when_a_rewrite_is_interrupted(self, tmp_path: Path):
        # Simulate a crash during the rewrite: bans.txt must keep the old,
        # complete ban list rather than ending up truncated (which would let
        # banned players reconnect).
        p = tmp_path / "bans.txt"
        write_bans(p, [BanEntry("abcdef01234567890abcdef012345678", "guid", 0, "orig")])
        original = p.read_text(encoding="utf-8")

        import app.ops.ban_ops as ban_ops

        def boom(*args, **kwargs):
            raise OSError("disk full")

        # Fail the atomic write partway. The original file must be untouched.
        import unittest.mock as mock

        with mock.patch.object(ban_ops, "atomic_write_lines", side_effect=boom), \
                mock.patch.object(ban_ops, "atomic_write_text", side_effect=boom):
            with pytest.raises(OSError):
                write_bans(p, [BanEntry("f" * 32, "guid", 0, "new")])

        assert p.read_text(encoding="utf-8") == original
        assert len(read_bans(p)) == 1


# ---------------------------------------------------------------------------
# BanEntry properties
# ---------------------------------------------------------------------------


class TestBanEntry:
    def test_permanent_properties(self):
        b = BanEntry("abc123", "guid", 0, "reason")
        assert b.is_permanent is True
        assert b.is_expired is False
        assert b.is_active is True
        assert b.expires_at is None
        assert b.time_remaining == "permanent"

    def test_negative_expiry_is_permanent(self):
        b = BanEntry("abc123", "guid", -1, "reason")
        assert b.is_permanent is True

    def test_active_temp_ban(self):
        future = int(time.time()) + 7200  # 2 hours
        b = BanEntry("abc123", "guid", future, "reason")
        assert b.is_permanent is False
        assert b.is_expired is False
        assert b.is_active is True
        assert b.expires_at is not None
        assert "h" in b.time_remaining

    def test_expired_temp_ban(self):
        past = int(time.time()) - 3600
        b = BanEntry("abc123", "guid", past, "reason")
        assert b.is_expired is True
        assert b.is_active is False
        assert b.time_remaining == "expired"

    def test_display_id_truncates_guid(self):
        guid = "abcdef01234567890abcdef012345678"
        b = BanEntry(guid, "guid", 0, "")
        assert len(b.display_id) < len(guid)
        assert b.display_id.startswith("abcdef01")

    def test_display_id_keeps_ip(self):
        b = BanEntry("192.168.1.50", "ip", 0, "")
        assert b.display_id == "192.168.1.50"


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestAddBan:
    def test_add_permanent(self, bans_file: Path, meta: Path):
        entry = add_ban(bans_file, "new_guid_" + "a" * 23, reason="new ban", metadata_path=meta)
        assert entry.is_permanent is True
        bans = read_bans(bans_file)
        assert len(bans) == 4

    def test_add_temporary(self, bans_file: Path, meta: Path):
        entry = add_ban(bans_file, "temp_guid" + "b" * 23, duration_minutes=60, metadata_path=meta)
        assert entry.is_permanent is False
        assert entry.is_active is True

    def test_add_with_metadata(self, bans_file: Path, meta: Path):
        entry = add_ban(
            bans_file, "meta_guid" + "c" * 23, reason="bad", added_by="admin", metadata_path=meta
        )
        assert entry.added_by == "admin"
        assert entry.added_at != ""
        assert "admin" in meta.read_text()

    def test_duplicate_raises(self, bans_file: Path, meta: Path):
        with pytest.raises(ValueError, match="already exists"):
            add_ban(bans_file, "abcdef01234567890abcdef012345678", metadata_path=meta)

    def test_add_to_nonexistent_file(self, tmp_path: Path, meta: Path):
        p = tmp_path / "new_bans.txt"
        entry = add_ban(p, "aaa" * 10 + "bb", reason="first ban", metadata_path=meta)
        assert entry is not None
        reloaded = read_bans(p)
        assert len(reloaded) == 1
        assert reloaded[0].identifier == entry.identifier
        assert reloaded[0].reason == "first ban"


class TestRemoveBan:
    def test_remove_existing(self, bans_file: Path):
        result = remove_ban(bans_file, "abcdef01234567890abcdef012345678")
        assert result is True
        assert len(read_bans(bans_file)) == 2

    def test_remove_nonexistent(self, bans_file: Path):
        result = remove_ban(bans_file, "doesnotexist")
        assert result is False
        assert len(read_bans(bans_file)) == 3


class TestCleanupExpired:
    def test_removes_expired(self, expired_bans_file: Path):
        before = read_bans(expired_bans_file)
        assert any(b.is_expired for b in before)

        removed = cleanup_expired(expired_bans_file)
        assert removed == 1

        after = read_bans(expired_bans_file)
        assert all(b.is_active for b in after)

    def test_no_expired(self, bans_file: Path):
        removed = cleanup_expired(bans_file)
        assert removed == 0


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class TestSearch:
    def test_search_by_identifier(self, bans_file: Path):
        bans = read_bans(bans_file)
        results = search_bans(bans, "192.168")
        assert len(results) == 1
        assert results[0].ban_type == "ip"

    def test_search_by_reason(self, bans_file: Path):
        bans = read_bans(bans_file)
        results = search_bans(bans, "aimbot")
        assert len(results) == 1

    def test_search_case_insensitive(self, bans_file: Path):
        bans = read_bans(bans_file)
        results = search_bans(bans, "CHEATING")
        assert len(results) == 1

    def test_search_no_results(self, bans_file: Path):
        bans = read_bans(bans_file)
        assert search_bans(bans, "zzzzz") == []

    def test_search_by_player_name(self):
        bans = [BanEntry("abc", "guid", 0, "", player_name="BadPlayer")]
        results = search_bans(bans, "badplayer")
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Import / export
# ---------------------------------------------------------------------------


class TestImportExport:
    def test_import_new_bans(self, bans_file: Path, tmp_path: Path):
        source = tmp_path / "source.txt"
        source.write_text("newguid_" + "x" * 24 + " 0 imported\n", encoding="utf-8")

        result = import_bans(bans_file, source)
        assert result.added == 1
        assert result.skipped_duplicates == 0
        assert len(read_bans(bans_file)) == 4

    def test_import_skips_duplicates(self, bans_file: Path, tmp_path: Path):
        source = tmp_path / "source.txt"
        source.write_text(
            "abcdef01234567890abcdef012345678 0 duplicate\n", encoding="utf-8"
        )

        result = import_bans(bans_file, source)
        assert result.added == 0
        assert result.skipped_duplicates == 1
        assert len(read_bans(bans_file)) == 3

    def test_export(self, bans_file: Path, tmp_path: Path):
        dest = tmp_path / "exported.txt"
        count = export_bans(bans_file, dest)
        assert count == 3
        assert dest.is_file()
        assert len(read_bans(dest)) == 3


# ---------------------------------------------------------------------------
# Metadata enrichment
# ---------------------------------------------------------------------------


class TestEnrichment:
    def test_enrich_with_metadata(self, bans_file: Path, meta: Path):
        from app.ops.ban_ops import _save_ban_metadata

        _save_ban_metadata(
            meta,
            "abcdef01234567890abcdef012345678",
            added_by="admin",
            player_name="Hacker123",
        )
        bans = read_bans(bans_file)
        enriched = enrich_with_metadata(bans, meta)
        target = [b for b in enriched if b.identifier == "abcdef01234567890abcdef012345678"][0]
        assert target.added_by == "admin"
        assert target.player_name == "Hacker123"

    def test_enrich_with_sessions(self):
        bans = [BanEntry("guid_abc", "guid", 0, "reason")]
        events = [
            {"guid": "guid_abc", "name": "OldPlayer", "timestamp": "2026-01-01T00:00:00"},
            {"guid": "guid_abc", "name": "RenamedPlayer", "timestamp": "2026-04-01T00:00:00"},
        ]
        enriched = enrich_with_sessions(bans, events)
        assert enriched[0].player_name == "RenamedPlayer"

    def test_enrich_sessions_no_match(self):
        bans = [BanEntry("unknown_guid", "guid", 0, "reason")]
        events = [{"guid": "other_guid", "name": "SomePlayer", "timestamp": "2026-01-01T00:00:00"}]
        enriched = enrich_with_sessions(bans, events)
        assert enriched[0].player_name == ""


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


class TestBanStats:
    def test_stats(self, bans_file: Path):
        bans = read_bans(bans_file)
        stats = get_ban_stats(bans)
        assert stats.total == 3
        assert stats.permanent == 2  # guid permanent + IP permanent
        assert stats.temporary == 1
        assert stats.expired == 0
        assert stats.guid_bans == 2
        assert stats.ip_bans == 1

    def test_stats_with_expired(self, expired_bans_file: Path):
        bans = read_bans(expired_bans_file)
        stats = get_ban_stats(bans)
        assert stats.expired == 1
        assert stats.temporary == 0  # expired, not active temp


# --- input that would not mean what was typed (test pass 2, N14/N15) --------

def test_a_multi_line_reason_is_refused_and_nothing_is_written(tmp_path):
    from app.ops.ban_ops import BanInputError, add_ban

    bans = tmp_path / "bans.txt"
    bans.write_text("")
    with pytest.raises(BanInputError):
        add_ban(
            bans, "2" * 32, reason="line one\n" + "3" * 32 + " -1 injected",
            metadata_path=tmp_path / "meta.json",
        )
    assert bans.read_text() == ""


# Every character str.splitlines breaks at, besides "\n" (plans/68 LB-1).
_SEPARATORS = ["\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", " ", " "]


@pytest.mark.parametrize("sep", _SEPARATORS)
def test_a_reason_with_any_line_separator_is_refused(tmp_path, sep):
    from app.ops.ban_ops import BanInputError, add_ban

    bans = tmp_path / "bans.txt"
    bans.write_text("")
    with pytest.raises(BanInputError):
        add_ban(
            bans, "2" * 32, reason="griefing" + sep + "3" * 32 + " -1 hidden",
            metadata_path=tmp_path / "meta.json",
        )
    assert bans.read_text() == ""


def test_a_tab_in_a_reason_is_still_accepted(tmp_path):
    bans = tmp_path / "bans.txt"
    add_ban(bans, "2" * 32, reason="griefing\tat NWAF", metadata_path=tmp_path / "meta.json")
    assert [b.reason for b in read_bans(bans)] == ["griefing\tat NWAF"]


def test_a_separator_inside_a_reason_stays_one_ban_through_a_rewrite(tmp_path):
    """BattlEye reads bans.txt by "\\n", so a line holding U+2028 is one ban;
    zsm reads it the same way and writes it back as one line."""
    bans = tmp_path / "bans.txt"
    line = "a" * 32 + " -1 griefing " + "b" * 32 + " -1 hidden"
    bans.write_text(line + "\r\n", encoding="utf-8")

    assert [b.identifier for b in read_bans(bans)] == ["a" * 32]
    add_ban(bans, "c" * 32, reason="spam", metadata_path=tmp_path / "meta.json")

    assert bans.read_text(encoding="utf-8").split("\n") == [line, "c" * 32 + " -1 spam", ""]
    assert [b.identifier for b in read_bans(bans)] == ["a" * 32, "c" * 32]


def test_an_imported_list_is_read_one_ban_per_line(tmp_path):
    bans = tmp_path / "bans.txt"
    source = tmp_path / "shared.txt"
    source.write_text("a" * 32 + " -1 griefing " + "b" * 32 + " -1 hidden\n", encoding="utf-8")

    result = import_bans(bans, source)

    assert (result.added, result.total_source) == (1, 1)
    assert [b.identifier for b in read_bans(bans)] == ["a" * 32]


@pytest.mark.parametrize(
    "identifier",
    [
        "a" * 32 + "\n",
        "1.2.3.4\n",
        "١.٢.٣.٤",  # Arabic-Indic digits: \d matches them
        "a" * 32 + " -1 x",
        "300.300.300.300",  # octets above 255
        "999.1.1.1",
        "192.168.01.5",  # a leading zero never equals the address BattlEye sees
    ],
)
def test_an_identifier_is_the_whole_value_or_refused(identifier):
    """``$`` matched before a trailing newline, so ``<GUID>\\n`` passed and the
    ban went into bans.txt as two lines zsm could not read (plans/68 LB-2)."""
    from app.ops.ban_ops import is_valid_ban_identifier

    assert not is_valid_ban_identifier(identifier)


@pytest.mark.parametrize(
    "identifier", ["a" * 32, "ABCDEF" + "0" * 26, "192.168.1.50", "255.255.255.255", "0.0.0.0"]
)
def test_a_plain_guid_or_ip_is_still_valid(identifier):
    from app.ops.ban_ops import is_valid_ban_identifier

    assert is_valid_ban_identifier(identifier)


def test_a_negative_duration_is_refused(tmp_path):
    from app.ops.ban_ops import BanInputError, add_ban

    bans = tmp_path / "bans.txt"
    bans.write_text("")
    with pytest.raises(BanInputError):
        add_ban(bans, "2" * 32, duration_minutes=-50, metadata_path=tmp_path / "meta.json")
    assert bans.read_text() == ""


def test_a_duration_past_the_cap_is_refused_and_nothing_is_written(tmp_path):
    """99999999999 minutes was written, and its end, past the year 9999,
    made every GET /api/bans answer 500."""
    from app.ops.ban_ops import BanInputError, max_ban_minutes

    bans = tmp_path / "bans.txt"
    bans.write_text("")
    with pytest.raises(BanInputError, match="0 means permanent"):
        add_ban(bans, "2" * 32, duration_minutes=99999999999, metadata_path=tmp_path / "meta.json")
    assert bans.read_text() == ""
    longest = add_ban(
        bans, "3" * 32, duration_minutes=max_ban_minutes() - 1, metadata_path=tmp_path / "meta.json"
    )
    assert longest.expires_at is not None


# 2026-10-17 12:00:00 UTC, so the longest ban is a known number of minutes.
_NOW = 1792238400


def test_a_temporary_ban_cannot_end_after_2038(tmp_path, monkeypatch):
    """BattlEye may read the expiry as a 32-bit Unix time, and a ban that ends
    later could then stop working: the end is capped at 2038-01-19 03:14:07
    UTC, not the 100 years first chosen (auth-02). Twenty years was inside
    that cap."""
    from types import SimpleNamespace

    from app.ops import ban_ops

    # A fraction, as time.time() has: the check and the write both drop it.
    monkeypatch.setattr(ban_ops, "time", SimpleNamespace(time=lambda: _NOW + 0.7))
    bans = tmp_path / "bans.txt"
    bans.write_text("")
    meta = tmp_path / "meta.json"
    longest = (2**31 - 1 - _NOW) // 60

    with pytest.raises(ban_ops.BanInputError) as refused:
        add_ban(bans, "2" * 32, duration_minutes=20 * 365 * 24 * 60, metadata_path=meta)
    assert str(refused.value) == (
        "A temporary ban must end by 19 January 2038, 03:14:07 UTC, because BattlEye "
        "may not read a later end date. The longest ban you can set now is "
        f"{longest} minutes. 0 means permanent."
    )
    with pytest.raises(ban_ops.BanInputError):
        add_ban(bans, "2" * 32, duration_minutes=longest + 1, metadata_path=meta)
    assert bans.read_text() == ""

    entry = add_ban(bans, "3" * 32, duration_minutes=longest, metadata_path=meta)
    assert _NOW < entry.expiry <= 2**31 - 1
    assert [b.expiry for b in read_bans(bans)] == [entry.expiry]
    assert add_ban(bans, "4" * 32, duration_minutes=0, metadata_path=meta).is_permanent


@pytest.mark.parametrize("expiry", [6001792184061, 10**18, 10**20, 10**400])
def test_an_expiry_no_date_can_hold_still_reads(expiry):
    """BattlEye and hand edits write bans.txt too: one such line must not
    take the whole list down."""
    ban = BanEntry("a" * 32, "guid", expiry, "by hand")
    assert ban.expires_at is None
    assert ban.is_active and not ban.is_permanent
    assert ban.time_remaining.endswith("h")


# ---------------------------------------------------------------------------
# DayZ's own ban.txt, read-only (test pass 1, F45)
# ---------------------------------------------------------------------------

# The container's file, in the private fixtures (ban/stock-1.29.txt; plans/86
# D2), verbatim apart from the two IDs: Bohemia's header, CRLF, and no newline
# after the last line.
def stock_ban_txt() -> str:
    return private_fixture("ban/stock-1.29.txt")


def test_the_server_banlist_skips_comments_and_blanks(tmp_path):
    from app.ops.ban_ops import read_server_banlist

    path = tmp_path / "ban.txt"
    path.write_bytes(stock_ban_txt().encode())
    assert read_server_banlist(path) == ["76561198000000011", "76561198000000012"]

    # An ID with a label after it, a 44-character ID, and a repeat.
    long_id = "A" * 43 + "="
    path.write_text(f"76561198000000011 //griefer\n{long_id}\n76561198000000011\n")
    assert read_server_banlist(path) == ["76561198000000011", long_id]


def test_a_missing_server_banlist_is_empty(tmp_path):
    from app.ops.ban_ops import read_server_banlist

    assert read_server_banlist(tmp_path / "ban.txt") == []


@pytest.mark.parametrize("line, disabled", [
    ("", False),
    ("disableBanlist = false;\n", False),
    ("disableBanlist = 0;\n", False),
    ("disableBanlist = true;\n", True),
    ("disableBanlist = 1;\n", True),
])
def test_disable_banlist_is_read_from_serverdz(sample_config, line, disabled):
    from app.ops.ban_ops import server_banlist_disabled

    cfg = sample_config.server_cfg_path
    cfg.write_text(cfg.read_text() + line)
    assert server_banlist_disabled(sample_config) is disabled


# Bohemia's ban.txt header (the server's own list beside BattlEye's), as a
# 1.29 install ships it: the shape of comment a bans.txt is given by hand. It
# is the first three lines of that file.
def ban_header() -> str:
    return "".join(stock_ban_txt().splitlines(True)[:3])


G1 = "a" * 32
G2 = "b" * 32


def test_a_rewrite_keeps_comments_odd_lines_and_the_final_newline(tmp_path):
    """add_ban, remove_ban, cleanup and import wrote bans.txt back from the
    bans read_bans could parse, so a comment, a blank line and anything else
    went with the first edit."""
    p = tmp_path / "bans.txt"
    original = ban_header() + f"{G1} -1 cheating\r\n# hand note\r\nnot a ban line\r\n{G2} -1 duping"
    p.write_bytes(original.encode())

    add_ban(p, "1.2.3.4", "spam", metadata_path=tmp_path / "meta.json")
    text = p.read_bytes().decode()
    assert text == original + "\r\n1.2.3.4 -1 spam"

    assert remove_ban(p, "1.2.3.4") is True
    assert p.read_bytes().decode() == original

    assert remove_ban(p, G1) is True
    assert p.read_bytes().decode() == ban_header() + f"# hand note\r\nnot a ban line\r\n{G2} -1 duping"


def test_an_unchanged_ban_keeps_its_own_spacing(tmp_path):
    p = tmp_path / "bans.txt"
    p.write_text(f"{G1}  0   old reason\n{G2} -1\n", encoding="utf-8")
    write_bans(p, read_bans(p))
    assert p.read_text(encoding="utf-8") == f"{G1}  0   old reason\n{G2} -1\n"


# A bans.txt saved with a byte order mark (Notepad's "UTF-8 with BOM"): the
# read kept the mark on the first ID, so the next write, which reads the file
# without it, dropped that ban's line and appended it with the mark mid-file.


def _bom_bans(tmp_path: Path) -> Path:
    p = tmp_path / "bans.txt"
    p.write_bytes(("﻿" + f"{G1} -1 Cheating\n").encode("utf-8"))
    return p


def test_a_bom_does_not_stick_to_the_first_ban(tmp_path):
    assert [b.identifier for b in read_bans(_bom_bans(tmp_path))] == [G1]


def test_adding_a_ban_to_a_bom_file_keeps_the_first_ban(tmp_path):
    p = _bom_bans(tmp_path)
    add_ban(p, G2, metadata_path=tmp_path / "meta.json")
    assert [b.identifier for b in read_bans(p)] == [G1, G2]
    assert p.read_text(encoding="utf-8-sig").count(G1) == 1


def test_the_first_ban_of_a_bom_file_can_be_removed(tmp_path):
    p = _bom_bans(tmp_path)
    assert remove_ban(p, G1) is True
    assert read_bans(p) == []
