"""Helpers for the types XML editor — imported by the web app's editors."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.xml.types_xml import MultiplierConfig


def _write_log(
    mission_dir: Path,
    mult: MultiplierConfig,
    affected_files: list[Path],
    all_changes: list[tuple[str, dict[str, tuple[int, int]]]],
) -> Path:
    """Write a log file with all changed values, affected files, and multipliers."""
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_path = mission_dir / f"multiplied.types.{ts}.log"

    lines: list[str] = []
    lines.append(f"Bulk edit log — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    lines.append("Multipliers applied:")
    lines.append(f"  nominal multiplied by: {mult.nominal}")
    lines.append(f"  min multiplied by: {mult.min}")
    lines.append(f"  lifetime multiplied by: {mult.lifetime}")
    lines.append(f"  restock multiplied by: {mult.restock}")
    lines.append("")

    lines.append(f"Affected files ({len(affected_files)}):")
    for f in affected_files:
        lines.append(f"  {f.relative_to(mission_dir)}")
    lines.append("")

    lines.append(f"Changed items ({len(all_changes)}):")
    lines.append(f"{'Item':<40} {'nominal':>16} {'min':>16} {'lifetime':>16} {'restock':>16}")
    lines.append("-" * 104)
    for name, changes in all_changes:
        cols: list[str] = [f"{name:<40}"]
        for field in ("nominal", "min", "lifetime", "restock"):
            old, new = changes[field]
            if old != new:
                cols.append(f"{old} -> {new:>6}".rjust(16))
            else:
                cols.append(str(old).rjust(16))
        lines.append("".join(cols))

    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log_path
