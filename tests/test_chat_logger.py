"""Tests for cli.chat_logger — JSONL chat logging and search."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.monitoring.chat_logger import (
    cleanup_chat_log,
    read_chat_events,
    search_chat,
    write_chat_event,
)


@pytest.fixture()
def chat_log(tmp_path: Path) -> Path:
    return tmp_path / "chat_log.jsonl"


class TestWriteAndRead:
    def test_write_creates_file(self, chat_log: Path):
        write_chat_event("Player1", "guid1", "Global", "Hello", path=chat_log)
        assert chat_log.is_file()
        lines = chat_log.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1

    def test_write_appends(self, chat_log: Path):
        write_chat_event("P1", "g1", "Global", "msg1", path=chat_log)
        write_chat_event("P2", "g2", "Side", "msg2", path=chat_log)
        lines = chat_log.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2

    def test_read_events(self, chat_log: Path):
        write_chat_event("Player1", "guid1", "Global", "Hello world", path=chat_log)
        events = read_chat_events(max_hours=1, path=chat_log)
        assert len(events) == 1
        assert events[0]["name"] == "Player1"
        assert events[0]["channel"] == "Global"
        assert events[0]["message"] == "Hello world"

    def test_read_filters_old(self, chat_log: Path):
        # Write an event with an old timestamp
        old_entry = {
            "timestamp": "2020-01-01T00:00:00",
            "name": "OldPlayer",
            "guid": "g",
            "channel": "Global",
            "message": "old",
        }
        chat_log.parent.mkdir(parents=True, exist_ok=True)
        chat_log.write_text(json.dumps(old_entry) + "\n", encoding="utf-8")

        events = read_chat_events(max_hours=1, path=chat_log)
        assert len(events) == 0

    def test_read_filters_channel(self, chat_log: Path):
        write_chat_event("P1", "g1", "Global", "public", path=chat_log)
        write_chat_event("P2", "g2", "Side", "private", path=chat_log)

        global_events = read_chat_events(max_hours=1, channel="Global", path=chat_log)
        assert len(global_events) == 1
        assert global_events[0]["channel"] == "Global"

    def test_read_missing_file(self, tmp_path: Path):
        events = read_chat_events(path=tmp_path / "missing.jsonl")
        assert events == []

    def test_read_empty_file(self, chat_log: Path):
        chat_log.parent.mkdir(parents=True, exist_ok=True)
        chat_log.write_text("", encoding="utf-8")
        events = read_chat_events(path=chat_log)
        assert events == []

    def test_read_malformed_lines(self, chat_log: Path):
        chat_log.parent.mkdir(parents=True, exist_ok=True)
        chat_log.write_text("not json\n{bad json}\n", encoding="utf-8")
        events = read_chat_events(path=chat_log)
        assert events == []


class TestSearch:
    def test_search_by_message(self, chat_log: Path):
        write_chat_event("P1", "g1", "Global", "hello world", path=chat_log)
        write_chat_event("P2", "g2", "Global", "goodbye", path=chat_log)

        results = search_chat("hello", path=chat_log)
        assert len(results) == 1
        assert results[0]["message"] == "hello world"

    def test_search_by_name(self, chat_log: Path):
        write_chat_event("TargetPlayer", "g1", "Global", "something", path=chat_log)
        write_chat_event("Other", "g2", "Global", "else", path=chat_log)

        results = search_chat("target", path=chat_log)
        assert len(results) == 1

    def test_search_case_insensitive(self, chat_log: Path):
        write_chat_event("P1", "g1", "Global", "LOUD MESSAGE", path=chat_log)
        results = search_chat("loud", path=chat_log)
        assert len(results) == 1

    def test_search_no_results(self, chat_log: Path):
        write_chat_event("P1", "g1", "Global", "hello", path=chat_log)
        results = search_chat("zzzzz", path=chat_log)
        assert results == []


class TestCleanup:
    def test_removes_old_entries(self, chat_log: Path):
        # Write old entry directly
        old_entry = {
            "timestamp": "2020-01-01T00:00:00",
            "name": "Old", "guid": "g", "channel": "Global", "message": "old",
        }
        chat_log.parent.mkdir(parents=True, exist_ok=True)
        chat_log.write_text(json.dumps(old_entry) + "\n", encoding="utf-8")
        # Write recent entry
        write_chat_event("New", "g2", "Global", "recent", path=chat_log)

        removed = cleanup_chat_log(max_hours=1, path=chat_log)
        assert removed == 1

        remaining = read_chat_events(max_hours=999999, path=chat_log)
        assert len(remaining) == 1
        assert remaining[0]["name"] == "New"

        # Atomic rewrite must not leave a temp file beside the log.
        leftovers = [
            f.name for f in chat_log.parent.iterdir() if f.name != chat_log.name
        ]
        assert leftovers == []

    def test_cleanup_missing_file(self, tmp_path: Path):
        removed = cleanup_chat_log(path=tmp_path / "missing.jsonl")
        assert removed == 0
