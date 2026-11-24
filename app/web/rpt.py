"""RptTailer — pure-python poll tail of the newest *.RPT server log.

Deliberately not `tail -F` (a subprocess): must work identically on Windows.
Runs only while >=1 WS client is subscribed to the `rpt` channel — wired to
WsHub.on_first_subscriber/on_last_unsubscriber by create_app.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.ops.backup_ops import _format_size
from app.ops.log_analysis import stock_line_checker
from app.shared.config import DayzConfig
from app.web.ws import WsHub

log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 1.0
MAX_LINES_PER_TICK = 100
REOPEN_TAIL_BYTES = 4096
SNAPSHOT_MAX_BYTES = 262144
# A backlog larger than this is skipped, not replayed. The tick sends at most
# MAX_LINES_PER_TICK lines, so a mod that floods the log (one wrote about a
# million lines in five minutes of shutdown) kept the Activity drawer replaying
# it for hours after the server stopped.
BACKLOG_SKIP_BYTES = 262144


def _profiles_dir(config: DayzConfig) -> Path:
    return config.server_dir_path / config.profiles_path


def _newest_rpt(config: DayzConfig) -> Path | None:
    profiles_dir = _profiles_dir(config)
    if not profiles_dir.is_dir():
        return None
    rpt_files = list(profiles_dir.glob("*.RPT"))
    if not rpt_files:
        return None
    return max(rpt_files, key=lambda p: p.stat().st_mtime)


def read_rpt_tail(config: DayzConfig, lines: int = 200) -> list[str]:
    """Snapshot the last *lines* lines of the newest .RPT file.

    Used by GET /api/logs/rpt (Dashboard's initial LogTail render, and WS
    reconnect catch-up). Reads at most SNAPSHOT_MAX_BYTES from the tail of
    the file rather than the whole thing — RPT files can be very large.
    """
    newest = _newest_rpt(config)
    if newest is None:
        return []
    try:
        size = newest.stat().st_size
        with open(newest, "r", encoding="utf-8", errors="replace") as f:
            if size > SNAPSHOT_MAX_BYTES:
                f.seek(size - SNAPSHOT_MAX_BYTES)
                f.readline()  # discard partial line
            return f.read().splitlines()[-lines:]
    except OSError:
        return []


class RptTailer:
    def __init__(self, config: DayzConfig, hub: WsHub) -> None:
        self.config = config
        self.hub = hub
        self._task: asyncio.Task | None = None
        self._path: Path | None = None
        self._pos: int = 0
        self._is_stock = lambda line: False  # set per log file in _tick

    def set_config(self, config: DayzConfig) -> None:
        """Adopt a new config (registered with WebState.on_config_change).

        The tick reads self.config each pass, so no task restart is needed —
        but drop the cached path/offset, since a changed server_dir or
        profiles_path points at a different .RPT file.
        """
        self.config = config
        self._path = None
        self._pos = 0

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _run(self) -> None:
        try:
            while True:
                try:
                    await self._tick()
                except OSError:
                    log.debug("RPT tail tick failed", exc_info=True)
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            pass

    async def _tick(self) -> None:
        """Broadcast the lines appended to the newest .RPT since the last tick."""
        newest = await asyncio.to_thread(_newest_rpt, self.config)
        if newest is None:
            return

        if newest != self._path:
            self._path = newest
            self._pos = await asyncio.to_thread(_initial_seek_pos, newest)
            # A new log is a new boot: the map, version or mods may have changed.
            self._is_stock = await asyncio.to_thread(stock_line_checker, self.config)

        size = (await asyncio.to_thread(newest.stat)).st_size
        if size - self._pos > BACKLOG_SKIP_BYTES:
            resume = await asyncio.to_thread(_initial_seek_pos, newest)
            note = (
                f"[Zedstead] Skipped {_format_size(resume - self._pos)} of server log"
                f" to catch up. The full log is {newest.name}."
            )
            self._pos = resume
            await self.hub.broadcast({"type": "rpt_line", "line": note}, channel="rpt")

        new_lines, new_pos = await asyncio.to_thread(
            _read_new_lines, newest, self._pos, MAX_LINES_PER_TICK
        )
        self._pos = new_pos
        for line in new_lines:
            await self.hub.broadcast(
                {"type": "rpt_line", "line": line, "stock": self._is_stock(line)}, channel="rpt"
            )


def _initial_seek_pos(path: Path) -> int:
    """Byte offset of the first whole line in the last REOPEN_TAIL_BYTES of *path*."""
    size = path.stat().st_size
    start = size - REOPEN_TAIL_BYTES
    if start <= 0:
        return 0
    with open(path, "rb") as f:
        f.seek(start - 1)
        chunk = f.read(REOPEN_TAIL_BYTES + 1)
    newline = chunk.find(b"\n")
    return start + newline if newline != -1 else start


def _read_new_lines(
    path: Path, pos: int, max_lines: int | None = None
) -> tuple[list[str], int]:
    """Read complete lines appended since *pos* (exact byte offset — binary
    mode avoids any text-mode seek/tell ambiguity). Returns (lines, new_pos);
    a trailing partial line is left unread and picked up whole next tick.

    With *max_lines* set, new_pos only advances past the returned lines, so
    a burst larger than the cap carries over to the next tick instead of
    being silently dropped."""
    with open(path, "rb") as f:
        f.seek(pos)
        data = f.read()

    if not data:
        return [], pos

    last_newline = data.rfind(b"\n")
    if last_newline == -1:
        return [], pos

    complete = data[: last_newline + 1]
    raw_lines = complete.split(b"\n")[:-1]  # drop the '' after the final \n
    if max_lines is not None and len(raw_lines) > max_lines:
        raw_lines = raw_lines[:max_lines]
        new_pos = pos + sum(len(ln) + 1 for ln in raw_lines)
    else:
        new_pos = pos + len(complete)
    lines = [ln.decode("utf-8", errors="replace") for ln in raw_lines if ln != b""]
    return lines, new_pos
