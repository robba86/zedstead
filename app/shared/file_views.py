"""Write full-content views to disk and point at them from a truncated preview.

Used by ops that show the first N entries of a long list but want the full
list available — without flooding the CLI output or the web job log.
"""

from __future__ import annotations

from pathlib import Path

from rich.errors import MarkupError
from rich.text import Text


def _strip_markup(line: str) -> str:
    try:
        return Text.from_markup(line).plain
    except MarkupError:
        return line


def overflow_link_line(
    *,
    views_dir: Path,
    indent: str,
    hidden: int,
    full_lines: list[str],
    file_label: str,
    suffix: str = "more",
) -> str:
    """Write *full_lines* to ``<views_dir>/<file_label>.txt`` and return
    a Rich-markup line ``"{indent}... and {hidden} {suffix} — full list: <path>"``.

    Markup is stripped from each line before writing so the on-disk view
    is plain readable text. The returned line carries only ``[dim]`` styling —
    consumers that are not a Rich console (the web API) strip it.
    *views_dir* is the instance's own (``config.paths.views_dir``, plans/44).
    """
    views_dir.mkdir(parents=True, exist_ok=True)
    path = views_dir / f"{file_label}.txt"
    path.write_text("\n".join(_strip_markup(line) for line in full_lines), encoding="utf-8")
    return f"{indent}[dim]... and {hidden} {suffix} — full list: {path}[/dim]"
