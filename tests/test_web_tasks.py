"""Tests for app/web/tasks.py — fire-and-forget tasks that cannot be collected
mid-flight (plans/14)."""

from __future__ import annotations

import asyncio
import contextlib
import logging

import pytest

from app.web.tasks import pending_count, spawn


async def test_spawn_retains_and_releases():
    async def work():
        await asyncio.sleep(0)

    before = pending_count()
    task = spawn(work(), name="t")
    assert pending_count() == before + 1
    await task
    await asyncio.sleep(0)
    assert pending_count() == before


async def test_spawn_logs_failure_without_raising(caplog):
    caplog.set_level(logging.WARNING, logger="app.web.tasks")

    async def boom():
        raise RuntimeError("boom")

    before = pending_count()
    task = spawn(boom(), name="boom-task")
    with pytest.raises(RuntimeError):
        await task
    await asyncio.sleep(0)
    assert pending_count() == before
    assert any(
        r.levelno == logging.WARNING and "Background task" in r.getMessage()
        for r in caplog.records
    )


async def test_spawn_cancelled_task_is_not_logged(caplog):
    caplog.set_level(logging.WARNING, logger="app.web.tasks")

    async def forever():
        await asyncio.Event().wait()

    task = spawn(forever(), name="cancelled")
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
