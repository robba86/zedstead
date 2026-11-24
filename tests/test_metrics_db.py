"""Tests for cli.metrics_db — SQLite time-series metrics storage."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.monitoring.metrics_db import (
    MetricSnapshot,
    cleanup_old,
    init_db,
    query_hourly_averages,
    query_peaks,
    query_recent,
    query_summary,
    record_snapshot,
    sparkline,
)


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "metrics.sqlite"
    init_db(p)
    return p


def _snap(ts_offset: int = 0, fps: float = 30.0, mem: float = 4000.0,
          players: int = 20, cpu: float = 35.0) -> MetricSnapshot:
    return MetricSnapshot(
        timestamp=time.time() - ts_offset,
        fps=fps, memory_mb=mem, players=players, cpu_pct=cpu, uptime_hours=1.0,
    )


class TestInitAndRecord:
    def test_init_creates_db(self, tmp_path: Path):
        p = tmp_path / "new.sqlite"
        init_db(p)
        assert p.is_file()

    def test_init_idempotent(self, db: Path):
        init_db(db)  # should not raise
        init_db(db)

    def test_record_and_query(self, db: Path):
        record_snapshot(_snap(fps=25.0, players=10), db_path=db)
        recent = query_recent(hours=1, db_path=db)
        assert len(recent) == 1
        assert recent[0].fps == 25.0
        assert recent[0].players == 10

    def test_record_multiple(self, db: Path):
        for i in range(5):
            record_snapshot(_snap(ts_offset=i * 60, fps=20.0 + i), db_path=db)
        recent = query_recent(hours=1, db_path=db)
        assert len(recent) == 5

    def test_record_with_none_values(self, db: Path):
        snap = MetricSnapshot(timestamp=time.time(), fps=None, memory_mb=None,
                              players=None, cpu_pct=None, uptime_hours=None)
        record_snapshot(snap, db_path=db)
        recent = query_recent(hours=1, db_path=db)
        assert len(recent) == 1
        assert recent[0].fps is None


class TestQueryRecent:
    def test_filters_by_time(self, db: Path):
        record_snapshot(_snap(ts_offset=0), db_path=db)          # now
        record_snapshot(_snap(ts_offset=7200), db_path=db)        # 2h ago
        recent = query_recent(hours=1, db_path=db)
        assert len(recent) == 1  # only the recent one

    def test_empty_db(self, db: Path):
        assert query_recent(hours=1, db_path=db) == []

    def test_missing_db(self, tmp_path: Path):
        assert query_recent(hours=1, db_path=tmp_path / "nope.sqlite") == []

    def test_order_oldest_first(self, db: Path):
        record_snapshot(_snap(ts_offset=120, fps=10.0), db_path=db)
        record_snapshot(_snap(ts_offset=60, fps=20.0), db_path=db)
        record_snapshot(_snap(ts_offset=0, fps=30.0), db_path=db)
        recent = query_recent(hours=1, db_path=db)
        assert recent[0].fps == 10.0
        assert recent[-1].fps == 30.0


class TestQuerySummary:
    def test_summary(self, db: Path):
        for fps in [20.0, 25.0, 30.0, 35.0]:
            record_snapshot(_snap(fps=fps, players=int(fps)), db_path=db)
        s = query_summary(hours=1, db_path=db)
        assert s.samples == 4
        assert s.fps_avg == 27.5
        assert s.fps_min == 20.0
        assert s.fps_max == 35.0
        assert s.players_peak == 35

    def test_empty_summary(self, db: Path):
        s = query_summary(hours=1, db_path=db)
        assert s.samples == 0
        assert s.fps_avg is None


class TestQueryHourlyAverages:
    def test_groups_by_hour(self, db: Path):
        # Insert data at 2 different hours
        now = time.time()
        record_snapshot(MetricSnapshot(timestamp=now, fps=30.0, players=20), db_path=db)
        record_snapshot(MetricSnapshot(timestamp=now - 3700, fps=20.0, players=10), db_path=db)
        avgs = query_hourly_averages(days=1, db_path=db)
        assert len(avgs) >= 1  # at least 1 hour bucket


class TestQueryPeaks:
    def test_peaks(self, db: Path):
        record_snapshot(_snap(fps=10.0, mem=8000.0, players=50, cpu=90.0), db_path=db)
        record_snapshot(_snap(fps=30.0, mem=4000.0, players=20, cpu=30.0), db_path=db)
        peaks = query_peaks(days=30, db_path=db)
        assert peaks["players_peak"] == 50
        assert peaks["fps_min"] == 10.0
        assert peaks["memory_peak_mb"] == 8000.0
        assert peaks["cpu_peak_pct"] == 90.0

    def test_empty_db(self, db: Path):
        assert query_peaks(days=30, db_path=db) == {}


class TestSparkline:
    def test_basic(self):
        result = sparkline([1.0, 2.0, 3.0, 4.0, 5.0], width=5)
        assert len(result) == 5
        assert result[0] == "▁"
        assert result[-1] == "█"

    def test_all_same(self):
        result = sparkline([5.0, 5.0, 5.0], width=3)
        assert len(result) == 3

    def test_with_nones(self):
        result = sparkline([1.0, None, 3.0], width=3)
        assert " " in result

    def test_all_nones(self):
        result = sparkline([None, None, None], width=5)
        assert result == "     "

    def test_empty(self):
        result = sparkline([], width=5)
        assert result == "     "

    def test_downsampling(self):
        values = list(range(100))
        result = sparkline([float(v) for v in values], width=10)
        assert len(result) == 10


class TestCleanup:
    def test_removes_old(self, db: Path):
        record_snapshot(_snap(ts_offset=0), db_path=db)               # now
        record_snapshot(_snap(ts_offset=86400 * 40), db_path=db)       # 40 days ago
        removed = cleanup_old(retention_days=30, db_path=db)
        assert removed == 1
        assert len(query_recent(hours=999999, db_path=db)) == 1

    def test_nothing_to_remove(self, db: Path):
        record_snapshot(_snap(ts_offset=0), db_path=db)
        removed = cleanup_old(retention_days=30, db_path=db)
        assert removed == 0

    def test_missing_db(self, tmp_path: Path):
        assert cleanup_old(db_path=tmp_path / "nope.sqlite") == 0
