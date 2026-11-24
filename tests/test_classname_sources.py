"""Tests for app.spawn_editor.classname_sources — the Create Event name suggestions."""

from pathlib import Path

import pytest

from app.spawn_editor import classname_sources
from app.spawn_editor.classname_sources import get_mod_xml_names, get_types_xml_names

GOOD_TYPES = '<types><type name="GoodItem"><nominal>1</nominal></type></types>'

_CP1251 = '<?xml version="1.0" encoding="windows-1251"?><types><type name="Нож"/></types>'

UNREADABLE = {
    "cp1251": _CP1251.encode("cp1251"),
    "utf16": GOOD_TYPES.encode("utf-16"),
    "cp1252_comment": b'<types><!-- Rev\xe9 --><type name="Bad"/></types>',
    "malformed": b"<types><type",
}


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    monkeypatch.setattr(classname_sources, "_types_cache", {})
    monkeypatch.setattr(classname_sources, "_mod_xml_cache", {})


def _names(source: dict) -> list[str]:
    return [entry["name"] for entry in source["names"]]


@pytest.mark.parametrize("content", UNREADABLE.values(), ids=UNREADABLE.keys())
def test_a_mod_xml_that_cannot_be_read_is_skipped(tmp_path: Path, content: bytes):
    mod = tmp_path / "@Mod"
    mod.mkdir()
    (mod / "bad.xml").write_bytes(content)
    (mod / "types.xml").write_text(GOOD_TYPES, encoding="utf-8")

    assert _names(get_mod_xml_names(tmp_path)) == ["GoodItem"]


def test_a_folder_named_like_an_xml_file_is_skipped(tmp_path: Path):
    mod = tmp_path / "@Mod"
    (mod / "notes.xml").mkdir(parents=True)
    (mod / "types.xml").write_text(GOOD_TYPES, encoding="utf-8")

    assert _names(get_mod_xml_names(tmp_path)) == ["GoodItem"]


@pytest.mark.parametrize("content", UNREADABLE.values(), ids=UNREADABLE.keys())
def test_a_types_file_that_cannot_be_read_is_skipped(tmp_path: Path, content: bytes):
    (tmp_path / "db").mkdir()
    (tmp_path / "db" / "types.xml").write_text(GOOD_TYPES, encoding="utf-8")
    bad = tmp_path / "bad_types.xml"
    bad.write_bytes(content)

    assert classname_sources._parse_type_names(bad, tmp_path) == []
    assert _names(get_types_xml_names(tmp_path)) == ["GoodItem"]
