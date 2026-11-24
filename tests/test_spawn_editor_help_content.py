"""Tests for the shared Visual Map Editor guide JSON."""

from __future__ import annotations

from app.spawn_editor import help_content


_ALLOWED_BLOCK_TYPES = {"paragraph", "bullets", "definitions"}


def test_json_lives_in_app_data():
    """The guide moved out of spawn_editor/static/ (deleted with the iframe)
    into app/data/, which pyproject's "data/*.json" glob already packages."""
    assert help_content._JSON_PATH.is_file()
    assert help_content._JSON_PATH.name == "map_editor_help.json"
    assert help_content._JSON_PATH.parent.name == "data"


def test_load_raw_schema():
    """Every section has a title + blocks; every block has a known type."""
    data = help_content.load_raw()
    assert "title" in data
    assert "lede" in data
    assert isinstance(data.get("sections"), list)
    assert data["sections"], "guide must have at least one section"

    for section in data["sections"]:
        assert "title" in section, section
        assert isinstance(section.get("blocks"), list)
        assert section["blocks"], f"section {section['title']!r} has no blocks"
        for block in section["blocks"]:
            assert block.get("type") in _ALLOWED_BLOCK_TYPES, block
            if block["type"] == "paragraph":
                assert isinstance(block.get("text"), str)
            elif block["type"] == "bullets":
                assert isinstance(block.get("items"), list)
                assert all(isinstance(it, str) for it in block["items"])
            elif block["type"] == "definitions":
                assert isinstance(block.get("items"), list)
                for pair in block["items"]:
                    assert isinstance(pair, list)
                    assert len(pair) == 2
                    assert all(isinstance(p, str) for p in pair)

