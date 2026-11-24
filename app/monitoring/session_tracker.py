"""Session tracker utilities — JSONL read/write for player session events.

The actual session tracking is now handled by RconManager (event-driven).
This module keeps the read/cleanup helpers. The log belongs to one instance:
callers pass ``config.paths.session_log`` (plans/44).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from app.shared.json_utils import atomic_write_lines


def read_session_events(max_days: int = 30, *, path: Path) -> list[dict]:
    """Read JSONL file, filter by age, return sorted events."""
    if not path.exists():
        return []

    cutoff = datetime.now() - timedelta(days=max_days)
    events: list[dict] = []

    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts_str = entry.get("timestamp", "")
                try:
                    ts = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S")
                    if ts < cutoff:
                        continue
                except (ValueError, TypeError):
                    continue
                events.append(entry)
    except (OSError, json.JSONDecodeError):
        return []

    events.sort(key=lambda e: e.get("timestamp", ""))
    return events


def cleanup_session_log(max_days: int = 30, *, path: Path) -> None:
    """Rewrite JSONL keeping only recent events."""
    if not path.exists():
        return

    events = read_session_events(max_days, path=path)
    try:
        atomic_write_lines(
            path, (json.dumps(entry) for entry in events),
        )
    except OSError:
        pass
