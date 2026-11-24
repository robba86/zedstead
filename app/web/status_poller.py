"""StatusPoller — periodic server-telemetry snapshots pushed on the ``status``
WS channel. Runs only while >=1 client is subscribed (wired to
WsHub.on_first_subscriber / on_last_unsubscriber by create_app), mirroring
RptTailer.
"""

from __future__ import annotations

import asyncio
import logging
import time

from app.shared.config import DayzConfig
from app.web.ws import WsHub

log = logging.getLogger(__name__)

STATUS_POLL_SECONDS = 5.0


def collect_status(config: DayzConfig) -> dict:
    """Assemble the 9 info-bar fields (+ hostname/template/mod_count).

    Shared by the REST endpoint (GET /api/status) and the WS poller so there is
    one builder (DRY). Imports stay function-local so importing this module
    stays cheap.
    """
    from app.maps.map_ops import get_active_map_name
    from app.modmgr.mods import get_current_mission
    from app.modmgr import modlist
    from app.parsers.servercfg import read_cfg_values
    from app.rcon.rcon_ops import is_remote_rcon
    from app.server import server_status, service_control
    from app.shared.platform import IS_WINDOWS
    from app.shared.steam import server_branch

    profiles_dir = config.server_dir_path / config.profiles_path
    cfg_values = read_cfg_values(config.server_cfg_path, ["hostname", "maxPlayers", "logAverageFps"])
    running = service_control.is_server_running(config)
    # On Windows each process reading is a process start, so a stopped server
    # is not asked about at all (2026 Q4, a Windows 11 VM was slow).
    ask = running or not IS_WINDOWS
    uptime = server_status.get_server_uptime(config) if ask else None
    restarts = service_control.restart_stats(config)
    next_restart = server_status.get_next_restart(config, uptime)

    return {
        "running": running,
        "version": server_status.read_server_version(profiles_dir),
        # "stable" | "experimental" (plans/66): the Dashboard badges the latter.
        "server_branch": server_branch(config),
        "uptime_seconds": uptime.total_seconds() if uptime is not None else None,
        "fps": server_status.read_server_fps(profiles_dir),
        # DayZ writes the FPS line only when serverDZ.cfg sets logAverageFps,
        # so the FPS tile can say why it shows a dash (doctor.py has the row).
        # None without a serverDZ.cfg: nothing is installed yet to blame.
        "fps_logging": (
            (cfg_values.get("logAverageFps") or "0").strip() not in ("", "0")
            if config.server_cfg_path.is_file()
            else None
        ),
        "memory": server_status.read_server_memory(profiles_dir, config) if ask else None,
        "cpu": server_status.read_server_cpu(config) if ask else None,
        "map": get_active_map_name(config),
        "mod_count": len(modlist.installed(config)),
        "hostname": cfg_values.get("hostname"),
        "max_players": cfg_values.get("maxPlayers"),
        # Not a root key: DayZ reads it inside class Missions { class DayZ },
        # so read_cfg_values never found it. The same reading as `map`.
        "template": get_current_mission(config.server_cfg_path),
        # Minutes until the next restart: the soonest scheduled restart or
        # update task, else messages.xml's countdown. The same answer the
        # restart warnings count down to (get_next_restart).
        "next_restart_minutes": next_restart[0] if next_restart is not None else None,
        # How the watchdog is behaving. `running: false` alone cannot tell a
        # stopped server from one crash-looping every 30 seconds, and until
        # this the panel showed the same grey box for both. `{}` where there
        # is no systemd to ask.
        "restarts": restarts,
        # Flattened for the Start button, which counts down to systemd's next
        # retry. Deliberately NOT folded into `next_restart_minutes` — that is
        # the *scheduler's* restart and means something else entirely.
        "restart_pending": bool(restarts.get("looping")),
        "restart_in_seconds": restarts.get("seconds_left"),
        # RCon on another host: `running` is this machine's unit and says
        # nothing about whether that server answers, so the Players page must
        # not grey out Broadcast/Console on it (rcon_ops skips the check too).
        "rcon_remote": is_remote_rcon(config),
    }


class StatusPoller:
    def __init__(self, config: DayzConfig, hub: WsHub) -> None:
        self.config = config
        self.hub = hub
        self._task: asyncio.Task | None = None
        self._failures = 0

    def set_config(self, config: DayzConfig) -> None:
        """Adopt a new config (registered with WebState.on_config_change)."""
        self.config = config

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
                    self._failures = 0
                except Exception:
                    self._note_failure()
                await asyncio.sleep(STATUS_POLL_SECONDS)
        except asyncio.CancelledError:
            pass

    def _note_failure(self) -> None:
        """Report a failed tick loudly enough to be found.

        A tick that fails every time leaves the UI showing a stale (or
        never-populated) status with no other symptom, so debug level hides the
        one clue that explains it. Warn on the first failure and once a minute
        after that — enough to diagnose, not enough to flood a log for a
        server that is merely down.
        """
        self._failures += 1
        first = self._failures == 1
        every_minute = (self._failures * STATUS_POLL_SECONDS) % 60 < STATUS_POLL_SECONDS
        if first or every_minute:
            log.warning(
                "status poll tick failed (%d in a row) — the dashboard will show "
                "stale or empty status until this recovers",
                self._failures,
                exc_info=True,
            )
        else:
            log.debug("status poll tick failed", exc_info=True)

    async def _tick(self) -> None:
        data = await asyncio.to_thread(collect_status, self.config)
        await self.hub.broadcast(
            {"type": "status", "data": data, "ts": time.time()}, channel="status"
        )
