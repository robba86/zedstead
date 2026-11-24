"""Tests for app.ops.priority_ops — priority.txt parsing, CRUD, validator."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ops.priority_ops import (
    add_priority_id,
    is_valid_steam64,
    read_priority_list,
    remove_priority_id,
    write_priority_list,
)


VALID_A = "76561198000000001"
VALID_B = "76561198000000002"
VALID_C = "76561199000000003"


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


class TestSteam64Validator:
    @pytest.mark.parametrize(
        "good",
        [
            "76561198000000001",
            "76561197960265728",  # canonical Steam64 base
            "76561199999999999",
            "76561190000000000",
        ],
    )
    def test_accepts_valid(self, good: str):
        assert is_valid_steam64(good)

    @pytest.mark.parametrize(
        "bad",
        [
            "",                    # empty
            "   ",                 # whitespace only
            "1234567890123456",    # 16 digits — too short
            "765611980000000012",  # 18 digits — too long
            "12345678901234567",   # right length, wrong prefix
            "76561198abcdefghi",   # contains letters
            "76561198 0000001",    # contains a space
            "STEAM_0:0:12345",     # Steam2 form
            "[U:1:12345]",         # Steam3 form
            "76561198_00000001",   # underscore
            "00000000000000000",   # all zeros
            "76561198000000001\n",  # "$" matched before it (plans/68 LB-8)
            "7656119800000000\u0661",  # an Arabic-Indic digit: \d matched it
        ],
    )
    def test_rejects_invalid(self, bad: str):
        assert not is_valid_steam64(bad)

    def test_an_id_with_a_trailing_newline_is_never_written(self, tmp_path: Path):
        path = tmp_path / "priority.txt"
        with pytest.raises(ValueError):
            add_priority_id(path, "76561198000000001\n")
        assert not path.exists()


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


class TestReadPriorityList:
    def test_missing_file(self, tmp_path: Path):
        assert read_priority_list(tmp_path / "missing.txt") == []

    def test_empty_file(self, tmp_path: Path):
        p = tmp_path / "priority.txt"
        p.write_text("", encoding="utf-8")
        assert read_priority_list(p) == []

    def test_whitespace_only(self, tmp_path: Path):
        p = tmp_path / "priority.txt"
        p.write_text("   \n\n  \t  \n", encoding="utf-8")
        assert read_priority_list(p) == []

    def test_single_id(self, tmp_path: Path):
        p = tmp_path / "priority.txt"
        p.write_text(VALID_A + "\n", encoding="utf-8")
        assert read_priority_list(p) == [VALID_A]

    def test_multiple_ids_in_order(self, tmp_path: Path):
        p = tmp_path / "priority.txt"
        p.write_text(f"{VALID_A};{VALID_B};{VALID_C}\n", encoding="utf-8")
        assert read_priority_list(p) == [VALID_A, VALID_B, VALID_C]

    def test_trailing_semicolon(self, tmp_path: Path):
        p = tmp_path / "priority.txt"
        p.write_text(f"{VALID_A};{VALID_B};\n", encoding="utf-8")
        assert read_priority_list(p) == [VALID_A, VALID_B]

    def test_leading_semicolon(self, tmp_path: Path):
        p = tmp_path / "priority.txt"
        p.write_text(f";{VALID_A};{VALID_B}\n", encoding="utf-8")
        assert read_priority_list(p) == [VALID_A, VALID_B]

    def test_double_semicolons(self, tmp_path: Path):
        p = tmp_path / "priority.txt"
        p.write_text(f"{VALID_A};;{VALID_B}\n", encoding="utf-8")
        assert read_priority_list(p) == [VALID_A, VALID_B]

    def test_whitespace_around_tokens(self, tmp_path: Path):
        p = tmp_path / "priority.txt"
        p.write_text(f"  {VALID_A} ;  {VALID_B}  \n", encoding="utf-8")
        assert read_priority_list(p) == [VALID_A, VALID_B]

    def test_one_id_per_line(self, tmp_path: Path):
        p = tmp_path / "priority.txt"
        p.write_text(f"{VALID_A}\r\n{VALID_B}\n", encoding="utf-8")
        assert read_priority_list(p) == [VALID_A, VALID_B]

    def test_an_add_keeps_a_file_written_one_id_per_line(self, tmp_path: Path):
        """It read as no IDs, so the add wrote the new one over all of them."""
        p = tmp_path / "priority.txt"
        p.write_text(f"{VALID_A}\n{VALID_B}\n", encoding="utf-8")
        assert add_priority_id(p, VALID_C) is True
        assert read_priority_list(p) == [VALID_A, VALID_B, VALID_C]

    def test_bom_tolerated(self, tmp_path: Path):
        p = tmp_path / "priority.txt"
        p.write_bytes(b"\xef\xbb\xbf" + f"{VALID_A};{VALID_B}".encode("utf-8"))
        assert read_priority_list(p) == [VALID_A, VALID_B]

    def test_invalid_tokens_silently_dropped(self, tmp_path: Path):
        p = tmp_path / "priority.txt"
        p.write_text(f"{VALID_A};not_a_steamid;{VALID_B};123\n", encoding="utf-8")
        assert read_priority_list(p) == [VALID_A, VALID_B]


# ---------------------------------------------------------------------------
# Write / round-trip
# ---------------------------------------------------------------------------


class TestWritePriorityList:
    def test_roundtrip_one(self, tmp_path: Path):
        p = tmp_path / "priority.txt"
        write_priority_list(p, [VALID_A])
        assert read_priority_list(p) == [VALID_A]

    def test_roundtrip_many(self, tmp_path: Path):
        p = tmp_path / "priority.txt"
        write_priority_list(p, [VALID_A, VALID_B, VALID_C])
        assert read_priority_list(p) == [VALID_A, VALID_B, VALID_C]

    def test_no_trailing_semicolon(self, tmp_path: Path):
        p = tmp_path / "priority.txt"
        write_priority_list(p, [VALID_A, VALID_B])
        text = p.read_text(encoding="utf-8")
        assert not text.rstrip("\n").endswith(";")

    def test_single_trailing_newline(self, tmp_path: Path):
        p = tmp_path / "priority.txt"
        write_priority_list(p, [VALID_A, VALID_B])
        text = p.read_text(encoding="utf-8")
        assert text.endswith("\n")
        assert not text.endswith("\n\n")

    def test_no_spaces(self, tmp_path: Path):
        p = tmp_path / "priority.txt"
        write_priority_list(p, [VALID_A, VALID_B])
        body = p.read_text(encoding="utf-8").rstrip("\n")
        assert " " not in body

    def test_empty_list(self, tmp_path: Path):
        p = tmp_path / "priority.txt"
        write_priority_list(p, [])
        assert read_priority_list(p) == []

    def test_creates_parent_dirs(self, tmp_path: Path):
        p = tmp_path / "sub" / "dir" / "priority.txt"
        write_priority_list(p, [VALID_A])
        assert p.is_file()

    def test_write_leaves_no_temp_file(self, tmp_path: Path):
        p = tmp_path / "priority.txt"
        write_priority_list(p, [VALID_A, VALID_B])
        leftovers = [f.name for f in tmp_path.iterdir() if f.name != "priority.txt"]
        assert leftovers == []

    def test_interrupted_write_preserves_existing_list(self, tmp_path: Path, monkeypatch):
        # A crash mid-write must not truncate priority.txt — partial content
        # would drop VIP/admin queue-skip entries.
        p = tmp_path / "priority.txt"
        write_priority_list(p, [VALID_A, VALID_B])

        monkeypatch.setattr(
            "app.ops.priority_ops.atomic_write_text",
            lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
        )
        with pytest.raises(OSError):
            write_priority_list(p, [VALID_C])

        assert read_priority_list(p) == [VALID_A, VALID_B]
        leftovers = [f.name for f in tmp_path.iterdir() if f.name != "priority.txt"]
        assert leftovers == []


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestAddPriorityId:
    def test_add_to_nonexistent_file(self, tmp_path: Path):
        p = tmp_path / "priority.txt"
        assert add_priority_id(p, VALID_A) is True
        assert p.is_file()
        assert read_priority_list(p) == [VALID_A]

    def test_add_to_existing_file(self, tmp_path: Path):
        p = tmp_path / "priority.txt"
        write_priority_list(p, [VALID_A])
        assert add_priority_id(p, VALID_B) is True
        assert read_priority_list(p) == [VALID_A, VALID_B]

    def test_duplicate_returns_false(self, tmp_path: Path):
        p = tmp_path / "priority.txt"
        write_priority_list(p, [VALID_A])
        assert add_priority_id(p, VALID_A) is False
        assert read_priority_list(p) == [VALID_A]

    def test_invalid_id_raises(self, tmp_path: Path):
        p = tmp_path / "priority.txt"
        with pytest.raises(ValueError, match="Steam64"):
            add_priority_id(p, "not_valid")
        assert not p.exists()


class TestRemovePriorityId:
    def test_remove_existing(self, tmp_path: Path):
        p = tmp_path / "priority.txt"
        write_priority_list(p, [VALID_A, VALID_B, VALID_C])
        assert remove_priority_id(p, VALID_B) is True
        assert read_priority_list(p) == [VALID_A, VALID_C]

    def test_remove_nonexistent(self, tmp_path: Path):
        p = tmp_path / "priority.txt"
        write_priority_list(p, [VALID_A])
        assert remove_priority_id(p, VALID_B) is False
        assert read_priority_list(p) == [VALID_A]

    def test_remove_from_missing_file(self, tmp_path: Path):
        p = tmp_path / "priority.txt"
        assert remove_priority_id(p, VALID_A) is False
        assert not p.exists()

    def test_remove_last_leaves_empty_file(self, tmp_path: Path):
        p = tmp_path / "priority.txt"
        write_priority_list(p, [VALID_A])
        assert remove_priority_id(p, VALID_A) is True
        assert p.is_file()
        assert read_priority_list(p) == []


class TestEditsKeepTheFile:
    """An add or a remove rewrote priority.txt from the IDs it could parse,
    so a comment, a token that is not a Steam64 ID and the line endings went
    with every edit."""

    def test_an_add_and_a_remove_keep_comments_and_unknown_tokens(self, tmp_path: Path):
        p = tmp_path / "priority.txt"
        original = f"// VIPs\r\n{VALID_A};SteamId;{VALID_B}\t// clan\r\n".encode()
        p.write_bytes(original)

        assert add_priority_id(p, VALID_C) is True
        assert p.read_bytes() == f"// VIPs\r\n{VALID_A};SteamId;{VALID_B};{VALID_C}\t// clan\r\n".encode()
        assert read_priority_list(p) == [VALID_A, VALID_B, VALID_C]

        assert remove_priority_id(p, VALID_C) is True
        assert p.read_bytes() == original

    def test_a_steam64_id_in_a_comment_is_not_listed(self, tmp_path: Path):
        p = tmp_path / "priority.txt"
        p.write_text(f"{VALID_A}  // was {VALID_B}\n", encoding="utf-8")
        assert read_priority_list(p) == [VALID_A]

    def test_a_file_without_a_final_newline_keeps_none(self, tmp_path: Path):
        p = tmp_path / "priority.txt"
        p.write_bytes(f"// queue\r\n{VALID_A}".encode())
        add_priority_id(p, VALID_B)
        assert p.read_bytes() == f"// queue\r\n{VALID_A};{VALID_B}".encode()
        remove_priority_id(p, VALID_A)
        remove_priority_id(p, VALID_B)
        assert p.read_bytes() == b"// queue"
