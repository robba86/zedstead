"""Fire-and-forget task helper.

The event loop holds only *weak* references to tasks, so a bare
``asyncio.create_task(coro)`` whose result nobody keeps can be garbage-collected
before it finishes. Every ``app/web`` site that starts a task it does not
otherwise store goes through ``spawn`` instead, which parks a strong reference
until the task completes (plans/14).

Tasks that are already owned by something (``RptTailer._task``,
``WebNotifier._tasks``) do not need this. The review check is a grep for
``asyncio.create_task`` under ``app/web/``: every match either lives here or
stores its result.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine

log = logging.getLogger(__name__)

# Strong references to in-flight fire-and-forget tasks. Entries remove
# themselves on completion, so this never grows without bound.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def spawn(coro: Coroutine[Any, Any, Any], *, name: str | None = None) -> asyncio.Task:
    """Start *coro* as a task and keep a strong reference until it finishes.

    Failures are logged rather than surfacing as a never-retrieved-exception
    warning at interpreter shutdown; callers of this helper are fire-and-forget
    by construction and have nowhere to propagate to.
    """
    task = asyncio.create_task(coro, name=name)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_on_done)
    return task


def _on_done(task: asyncio.Task) -> None:
    _BACKGROUND_TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.warning("Background task %r failed", task.get_name(), exc_info=exc)


def pending_count() -> int:
    """Number of retained in-flight tasks (test/diagnostic helper)."""
    return len(_BACKGROUND_TASKS)
