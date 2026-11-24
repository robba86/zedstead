"""`zsm web`'s zedstead.com heartbeat (TODO F7, plans/57 B4).

One loop, started from the lifespan **whether or not `web_notifier_enabled` is
on**: that setting exists to stop a second process doubling Discord posts, and
an admin who turned it off has not thereby withdrawn a listing they opted
into. Each tick is `site_ops.heartbeat` in a thread — the ping at most
once a day, the listing every 15 minutes while the server runs, and a
published listing removed once its switch is off. With both switches off, or
no endpoint set, a tick sends nothing.

**An install that never runs `zsm web` sends nothing.** Accepted in plans/57
and said in both switches' descriptions, rather than building a second
scheduler.
"""

from __future__ import annotations

import asyncio
import logging

from app.ops import site_ops
from app.shared.config import DayzConfig

log = logging.getLogger(__name__)

# A change to any of these wakes the loop, so a switch turned on publishes now
# and one turned off removes the listing now, rather than a tick later.
_FIELDS = (
    "site_ping",
    "site_listing",
    "site_address",
    "site_install_id",
)


class SiteHeartbeat:
    # The first tick waits for the panel to settle, like WebNotifier's loops;
    # a switch turned on meanwhile wakes it at once.
    FIRST_TICK_DELAY = 60

    def __init__(self, config: DayzConfig) -> None:
        self._config = config
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._warned = False

    def set_config(self, config: DayzConfig) -> None:
        """Adopt a new config (registered with WebState.on_config_change)."""
        old, self._config = self._config, config
        if any(getattr(old, f) != getattr(config, f) for f in _FIELDS):
            self._wake.set()

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="zsm-app-heartbeat")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def tick(self) -> None:
        # Read from disk, not only from memory: `zsm settings set
        # site_listing false` in a shell is an opt-out too, and a CLI edit
        # stays invisible to this process until the web's next own write
        # (app/web/CLAUDE.md). The in-memory copy covers a missing file, and
        # it is this config's own file: the panel's instance, not the default.
        config = self._config.reloaded() or self._config
        site_ops.heartbeat(config)

    async def _loop(self) -> None:
        delay = self.FIRST_TICK_DELAY
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            delay = site_ops.LISTING_INTERVAL
            try:
                await asyncio.to_thread(self.tick)
            except Exception:
                # The ops never raise for a backend that is down; this is a
                # bug, so say it once and then keep quiet (uvicorn runs at
                # warning, and this repeats every 15 minutes).
                log.log(
                    logging.DEBUG if self._warned else logging.WARNING,
                    "zedstead.com heartbeat tick failed", exc_info=True,
                )
                self._warned = True
