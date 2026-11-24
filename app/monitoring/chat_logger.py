"""In-game chat logging to JSONL file.

Logs chat messages received via berconpy's on_player_message event.
Each line is a JSON object with timestamp, player name, GUID, channel,
and message text. The log belongs to one instance: callers pass
``config.paths.chat_log`` (plans/44).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.shared.json_utils import atomic_write_lines


def write_chat_event(
    name: str,
    guid: str,
    channel: str,
    message: str,
    *,
    path: Path,
) -> None:
    """Append a chat event to the JSONL log file."""
    dest = path
    dest.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "name": name,
        "guid": guid,
        "channel": channel,
        "message": message,
    }
    with dest.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def read_chat_events(
    max_hours: int = 24,
    channel: str | None = None,
    *,
    path: Path,
) -> list[dict]:
    """Read chat events from JSONL, filtered by age and optional channel."""
    src = path
    if not src.is_file():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_hours)
    events: list[dict] = []

    try:
        for line in src.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_str = entry.get("timestamp", "")
            try:
                ts = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S").replace(
                    tzinfo=timezone.utc
                )
                if ts < cutoff:
                    continue
            except (ValueError, TypeError):
                continue
            if channel and entry.get("channel", "").lower() != channel.lower():
                continue
            events.append(entry)
    except OSError:
        return []

    return events


def cleanup_chat_log(max_hours: int = 72, *, path: Path) -> int:
    """Remove old chat entries. Returns count removed."""
    src = path
    if not src.is_file():
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_hours)
    keep: list[str] = []
    total = 0

    try:
        for line in src.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            total += 1
            try:
                entry = json.loads(stripped)
                ts_str = entry.get("timestamp", "")
                ts = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S").replace(
                    tzinfo=timezone.utc
                )
                if ts >= cutoff:
                    keep.append(stripped)
            except (json.JSONDecodeError, ValueError, TypeError):
                keep.append(stripped)
    except OSError:
        return 0

    removed = total - len(keep)
    if removed > 0:
        try:
            atomic_write_lines(src, keep)
        except OSError:
            pass

    return removed


def search_chat(
    query: str,
    max_hours: int = 24,
    *,
    path: Path,
) -> list[dict]:
    """Search chat events by message content or player name."""
    events = read_chat_events(max_hours=max_hours, path=path)
    q = query.lower()
    return [
        e for e in events
        if q in e.get("message", "").lower() or q in e.get("name", "").lower()
    ]
