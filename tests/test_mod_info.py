"""meta.cpp timestamp parsing + formatting (app/modmgr/mod_info.py)."""

import time

import pytest

from app.modmgr.mod_info import (
    ModInfo,
    format_epoch,
    format_mod_info,
    parse_mod_timestamp,
    read_mod_info,
)

# @CF's meta.cpp as SteamCMD downloads it (CRLF, packed 2026-10-12 16:22:39 UTC).
CF_META_CPP = (
    'protocol = 1;\r\npublishedid = 1559212036;\r\nname = "CF";\r\n'
    "timestamp = 5250960208020560000;\r\n"
)
CF_BUILT = 1791822159


def test_parse_filetime_is_converted_to_unix_epoch():
    # 2024-01-01 00:00:00 UTC as a bare Windows FILETIME (other tooling).
    filetime = (1704067200 + 11644473600) * 10_000_000
    assert parse_mod_timestamp(str(filetime)) == 1704067200


@pytest.mark.parametrize(
    ("raw", "epoch"),
    [
        ("5250960208020560000", CF_BUILT),  # @CF, 2026-10-12 16:22:39 UTC
        ("5250545203567616336", 1750321714),  # @TraderPlus, 2025-06-19 08:28:34 UTC
    ],
)
def test_parse_reads_the_datetime_tobinary_value_dayz_writes(raw, epoch):
    """Every Workshop meta.cpp holds .NET DateTime.ToBinary(): DateTimeKind.Utc
    in the top two bits, 100 ns ticks since 0001-01-01 in the low 62. Read as
    a FILETIME it landed past the year 18000 and was dropped."""
    assert parse_mod_timestamp(raw) == epoch


@pytest.mark.parametrize("kind", [0, 1, 2])
def test_parse_ignores_the_datetime_kind_bits(kind):
    ticks = 621355968000000000 + 1704067200 * 10_000_000
    assert parse_mod_timestamp(str(ticks | kind << 62)) == 1704067200


def test_parse_accepts_epoch_seconds_and_milliseconds():
    assert parse_mod_timestamp("1704067200") == 1704067200
    assert parse_mod_timestamp("1704067200123") == 1704067200


@pytest.mark.parametrize("raw", ["", None, "abc", "0", "999999999999999999999"])
def test_parse_returns_none_for_junk(raw):
    assert parse_mod_timestamp(raw) is None


@pytest.mark.parametrize("raw", ["\u00b2", "1704067200\u00b2", "\u2460", "7" * 5000])
def test_parse_returns_none_for_digits_int_refuses(raw):
    """meta.cpp is the mod author's file. Each of these raised ValueError out
    of `GET /api/mods/<mod>`: `str.isdigit` passes a superscript that `int`
    cannot read, and `int` refuses a string of more than 4300 digits."""
    assert parse_mod_timestamp(raw) in (None, 1704067200)


def test_format_epoch_is_local_time_and_empty_for_unknown():
    assert format_epoch(0) == ""
    assert format_epoch(None) == ""
    epoch = 1704067200
    assert format_epoch(epoch) == time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch))


def test_tooltip_shows_a_readable_built_date():
    filetime = (1704067200 + 11644473600) * 10_000_000
    _title, body = format_mod_info(ModInfo(name="@CF", timestamp=str(filetime)))
    assert f"Built: {format_epoch(1704067200)}" in body


def test_a_real_meta_cpp_gets_a_built_line_not_the_raw_number(tmp_path):
    (tmp_path / "meta.cpp").write_bytes(CF_META_CPP.encode())
    _title, body = format_mod_info(read_mod_info(tmp_path))
    assert f"Built: {format_epoch(CF_BUILT)}" in body
    assert "Timestamp:" not in body


def test_tooltip_falls_back_to_the_raw_value_when_unparseable():
    _title, body = format_mod_info(ModInfo(name="@CF", timestamp="not-a-date"))
    assert "Timestamp: not-a-date" in body
