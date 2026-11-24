"""The Scheduler page's task-type picker mirrors app/server/scheduler.py.

A type added only to the page is a 422 when saved, so that mistake shows.
A type added only to the backend is silent: the picker never offers it. That
is how the Announcement and RCon Command types went unreachable from the web
UI after 881aab0 added them (plans/50 A, finding F41). This test is the only
thing that notices.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.server import scheduler

PAGE = Path(__file__).resolve().parent.parent / "webui" / "src" / "pages" / "SchedulerPage.jsx"


@pytest.fixture(scope="module")
def source() -> str:
    return PAGE.read_text(encoding="utf-8")


def _object_block(source: str, name: str) -> str:
    block = re.search(rf"const {name} = \{{(.*?)\n\}}", source, re.S)
    assert block, f"{name} not found in SchedulerPage.jsx — did it get renamed?"
    return block.group(1)


def test_the_picker_offers_every_backend_type(source):
    listed = re.search(r"const TASK_TYPES = \[(.*?)\]", source, re.S)
    assert listed, "TASK_TYPES not found in SchedulerPage.jsx"
    types = re.findall(r"'([\w-]+)'", listed.group(1))
    assert set(types) == set(scheduler.TASK_COMMANDS)
    assert len(types) == len(set(types))


def test_labels_match_the_backend_word_for_word(source):
    pairs = re.findall(r"^\s*([\w-]+):\s*'([^']*)'", _object_block(source, "TASK_TYPE_LABELS"), re.M)
    assert dict(pairs) == scheduler.TASK_TYPE_LABELS


def test_payload_fields_read_the_key_the_backend_reads(source):
    rows = re.findall(r"^\s*([\w-]+):\s*\{\s*key:\s*'([^']+)'", _object_block(source, "PAYLOAD_FIELDS"), re.M)
    assert dict(rows) == scheduler.PAYLOAD_TASK_TYPES
