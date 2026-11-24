"""Tests for app.shared.file_views.overflow_link_line."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def patched_config_dir(tmp_path: Path, monkeypatch) -> Path:
    """Redirect CONFIG_DIR (and the views dir derived from it) into tmp_path."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setattr("app.shared.paths.CONFIG_DIR", config_dir)
    return config_dir


def test_overflow_link_line_writes_file_and_points_at_it(patched_config_dir: Path):
    from app.shared.file_views import overflow_link_line

    line = overflow_link_line(
        views_dir=patched_config_dir / "views",
        indent="  ",
        hidden=42,
        full_lines=["[bold]alpha[/bold]", "[dim]beta[/dim]", "gamma"],
        file_label="test-label",
    )

    # File is created at CONFIG_DIR/views/<label>.txt with markup stripped.
    out = patched_config_dir / "views" / "test-label.txt"
    assert out.exists()
    assert out.read_text(encoding="utf-8") == "alpha\nbeta\ngamma"

    # Returned line carries the count, suffix, and the path to the full list.
    assert "... and 42 more" in line
    assert str(out) in line


def test_overflow_link_line_emits_no_click_action_markup(patched_config_dir: Path):
    """The line reaches the web API (GET /api/changes), so it must not carry a
    ``@click`` action — nothing in the CLI or the web resolves one."""
    from app.shared.file_views import overflow_link_line

    line = overflow_link_line(
        views_dir=patched_config_dir / "views",
        indent="  ",
        hidden=3,
        full_lines=["a"],
        file_label="no-action",
    )

    assert "@click" not in line


def test_overflow_link_line_creates_views_dir_lazily(patched_config_dir: Path):
    from app.shared.file_views import overflow_link_line

    views_dir = patched_config_dir / "views"
    assert not views_dir.exists()

    overflow_link_line(
        views_dir=patched_config_dir / "views",
        indent="",
        hidden=1,
        full_lines=["one"],
        file_label="lazy",
    )

    assert views_dir.is_dir()


def test_overflow_link_line_custom_suffix(patched_config_dir: Path):
    from app.shared.file_views import overflow_link_line

    line = overflow_link_line(
        views_dir=patched_config_dir / "views",
        indent="    ",
        hidden=7,
        full_lines=["x"],
        file_label="custom",
        suffix="changes",
    )

    assert "... and 7 changes" in line
    assert line.startswith("    ")


def test_overflow_link_line_handles_malformed_markup(patched_config_dir: Path):
    from app.shared.file_views import overflow_link_line

    # An unclosed/invalid tag should fall back to the raw line, not crash.
    overflow_link_line(
        views_dir=patched_config_dir / "views",
        indent="",
        hidden=1,
        full_lines=["[unclosed", "ok"],
        file_label="bad-markup",
    )

    out = patched_config_dir / "views" / "bad-markup.txt"
    text = out.read_text(encoding="utf-8")
    # Either the raw line or a stripped form is acceptable; both must contain "ok"
    # and the broken line must not crash the helper.
    assert "ok" in text
