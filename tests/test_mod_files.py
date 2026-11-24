"""Tests for app/ops/mod_files.py."""

from __future__ import annotations

from app.ops.mod_files import read_json


def test_a_missing_file_is_neither_document_nor_error(tmp_path):
    assert read_json(tmp_path / "nope.json") == (None, None)


def test_a_syntax_error_names_its_position(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"a": 1,\n}', encoding="utf-8")
    doc, err = read_json(path)
    assert doc is None and err.startswith("line 2, column 1:")


def test_a_file_that_is_not_utf8_is_reported_not_raised(tmp_path):
    """plans/68 CON-11: Notepad's ANSI (Windows-1252) save of a mod's JSON."""
    path = tmp_path / "ToxicZoneSettingsV5.json"
    path.write_bytes('{"Name": "Café"}'.encode("cp1252"))
    doc, err = read_json(path)
    assert doc is None
    assert "not UTF-8" in err


def test_a_file_nested_too_deeply_is_reported_not_raised(tmp_path):
    """plans/68 CON-11: RecursionError is not a ValueError, and escaped."""
    path = tmp_path / "deep.json"
    path.write_text('{"a": ' * 200_000 + "1" + "}" * 200_000, encoding="utf-8")
    doc, err = read_json(path)
    assert doc is None
    assert err == "nested too deeply to read"


def test_a_utf8_bom_is_accepted(tmp_path):
    path = tmp_path / "bom.json"
    path.write_bytes(b"\xef\xbb\xbf" + b'{"a": 1}')
    assert read_json(path) == ({"a": 1}, None)
