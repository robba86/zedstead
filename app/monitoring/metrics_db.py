"""Server performance metrics — SQLite time-series storage.

Records server FPS, memory, player count, CPU, and uptime at configurable
intervals. Provides query helpers for recent data, hourly averages, peaks,
and sparkline generation. Retention-based cleanup keeps DB size bounded.

Storage estimate: ~12 MB for 30 days at 1-minute intervals.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

# The database belongs to one instance: callers pass config.paths.metrics_db
# (plans/44). Merged across servers, every graph would be wrong.


@dataclass
class MetricSnapshot:
    """A single point-in-time metric sample."""

    timestamp: float  # Unix timestamp
    fps: float | None = None
    memory_mb: float | None = None
    players: int | None = None
    cpu_pct: float | None = None
    uptime_hours: float | None = None


@dataclass
class MetricSummary:
    """Aggregated metric statistics for a time range."""

    period_label: str  # "last 1h", "last 24h", etc.
    samples: int
    fps_avg: float | None = None
    fps_min: float | None = None
    fps_max: float | None = None
    memory_avg: float | None = None
    memory_max: float | None = None
    players_avg: float | None = None
    players_peak: int | None = None
    cpu_avg: float | None = None


# ---------------------------------------------------------------------------
# Database management
# ---------------------------------------------------------------------------


_initialized: set[Path] = set()


def _ensure_db(path: Path) -> None:
    """Run init_db once per path per process."""
    if path not in _initialized:
        init_db(path)
        _initialized.add(path)


def init_db(db_path: Path) -> Path:
    """Create the metrics table if it doesn't exist. Returns the DB path."""
    path = db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS metrics (
                ts INTEGER NOT NULL,
                fps REAL,
                memory_mb REAL,
                players INTEGER,
                cpu_pct REAL,
                uptime_hours REAL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(ts)")
        conn.commit()
    finally:
        conn.close()
    return path


def record_snapshot(snap: MetricSnapshot, *, db_path: Path) -> None:
    """Insert a metric snapshot into the database."""
    path = db_path
    _ensure_db(path)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO metrics (ts, fps, memory_mb, players, cpu_pct, uptime_hours) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                int(snap.timestamp),
                snap.fps,
                snap.memory_mb,
                snap.players,
                snap.cpu_pct,
                snap.uptime_hours,
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def query_recent(
    hours: int = 1, *, db_path: Path
) -> list[MetricSnapshot]:
    """Get all snapshots from the last N hours, oldest first."""
    path = db_path
    if not path.is_file():
        return []
    _ensure_db(path)
    cutoff = int(time.time()) - (hours * 3600)
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT ts, fps, memory_mb, players, cpu_pct, uptime_hours "
            "FROM metrics WHERE ts > ? ORDER BY ts",
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()
    return [
        MetricSnapshot(
            timestamp=r[0], fps=r[1], memory_mb=r[2],
            players=r[3], cpu_pct=r[4], uptime_hours=r[5],
        )
        for r in rows
    ]


def query_summary(
    hours: int = 1, *, db_path: Path
) -> MetricSummary:
    """Get aggregated stats for the last N hours."""
    path = db_path
    if not path.is_file():
        return MetricSummary(period_label=f"last {hours}h", samples=0)
    _ensure_db(path)
    cutoff = int(time.time()) - (hours * 3600)
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT COUNT(*), "
            "AVG(fps), MIN(fps), MAX(fps), "
            "AVG(memory_mb), MAX(memory_mb), "
            "AVG(players), MAX(players), "
            "AVG(cpu_pct) "
            "FROM metrics WHERE ts > ?",
            (cutoff,),
        ).fetchone()
    finally:
        conn.close()
    if not row or row[0] == 0:
        return MetricSummary(period_label=f"last {hours}h", samples=0)
    return MetricSummary(
        period_label=f"last {hours}h",
        samples=row[0],
        fps_avg=round(row[1], 1) if row[1] is not None else None,
        fps_min=round(row[2], 1) if row[2] is not None else None,
        fps_max=round(row[3], 1) if row[3] is not None else None,
        memory_avg=round(row[4], 1) if row[4] is not None else None,
        memory_max=round(row[5], 1) if row[5] is not None else None,
        players_avg=round(row[6], 1) if row[6] is not None else None,
        players_peak=int(row[7]) if row[7] is not None else None,
        cpu_avg=round(row[8], 1) if row[8] is not None else None,
    )


def query_hourly_averages(
    days: int = 7, *, db_path: Path
) -> list[dict]:
    """Get per-hour averages for the last N days."""
    path = db_path
    if not path.is_file():
        return []
    _ensure_db(path)
    cutoff = int(time.time()) - (days * 86400)
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT (ts / 3600) * 3600 AS hour, "
            "AVG(fps), AVG(memory_mb), AVG(players), MAX(players), AVG(cpu_pct) "
            "FROM metrics WHERE ts > ? GROUP BY hour ORDER BY hour",
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "hour": r[0],
            "fps_avg": round(r[1], 1) if r[1] is not None else None,
            "memory_avg": round(r[2], 1) if r[2] is not None else None,
            "players_avg": round(r[3], 1) if r[3] is not None else None,
            "players_peak": int(r[4]) if r[4] is not None else None,
            "cpu_avg": round(r[5], 1) if r[5] is not None else None,
        }
        for r in rows
    ]


def query_peaks(
    days: int = 30, *, db_path: Path
) -> dict:
    """Get all-time peaks for the last N days."""
    path = db_path
    if not path.is_file():
        return {}
    _ensure_db(path)
    cutoff = int(time.time()) - (days * 86400)
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT MAX(players), MIN(fps), MAX(memory_mb), MAX(cpu_pct) "
            "FROM metrics WHERE ts > ?",
            (cutoff,),
        ).fetchone()
    finally:
        conn.close()
    if not row or all(v is None for v in row):
        return {}
    return {
        "players_peak": int(row[0]) if row[0] is not None else None,
        "fps_min": round(row[1], 1) if row[1] is not None else None,
        "memory_peak_mb": round(row[2], 1) if row[2] is not None else None,
        "cpu_peak_pct": round(row[3], 1) if row[3] is not None else None,
    }


# ---------------------------------------------------------------------------
# Sparkline generation
# ---------------------------------------------------------------------------

_SPARK_CHARS = "▁▂▃▄▅▆▇█"


def sparkline(values: list[float | None], width: int = 20) -> str:
    """Generate a Unicode sparkline from a list of values.

    None values are rendered as a space. The output is capped at ``width``
    characters by sampling evenly from the input.
    """
    clean = [v for v in values if v is not None]
    if not clean:
        return " " * width

    lo, hi = min(clean), max(clean)
    span = hi - lo if hi != lo else 1.0

    # Sample evenly if more values than width
    if len(values) > width:
        step = len(values) / width
        sampled = [values[int(i * step)] for i in range(width)]
    else:
        sampled = values

    chars = []
    for v in sampled:
        if v is None:
            chars.append(" ")
        else:
            idx = int((v - lo) / span * (len(_SPARK_CHARS) - 1))
            idx = max(0, min(idx, len(_SPARK_CHARS) - 1))
            chars.append(_SPARK_CHARS[idx])
    return "".join(chars)


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def cleanup_old(
    retention_days: int = 30, *, db_path: Path
) -> int:
    """Delete metrics older than retention_days. Returns count removed."""
    path = db_path
    if not path.is_file():
        return 0
    _ensure_db(path)
    cutoff = int(time.time()) - (retention_days * 86400)
    conn = sqlite3.connect(path)
    try:
        cursor = conn.execute("DELETE FROM metrics WHERE ts < ?", (cutoff,))
        removed = cursor.rowcount
        conn.commit()
    finally:
        conn.close()
    return removed

