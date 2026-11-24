"""WebNotifier — the app's background notification loops, headless.

Runs 7 loops (restart warnings, doctor checks, crash detection, log cleanup,
metrics collection, mod-update checks, Discord status) against the pure ops
modules; toasts become `{"type": "notification", ...}` WS broadcasts.

Only one process should run these against a given config at a time — two
duplicate Discord posts and metrics rows. See `web_notifier_enabled` on
DayzConfig and the "Web app" section of app/web/CLAUDE.md.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

from app.shared.settings_registry import METRICS_INTERVAL_MIN
from app.web.tasks import spawn
from app.web.ws import WsHub

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.rcon.rcon_manager import RconManager as _RconRef
    from app.rcon.rcon_scheduler import RestartWarningState as _RestartWarningState
    from app.shared.config import DayzConfig
    from app.web.jobs import Job, JobManager
else:
    _RconRef = object
    _RestartWarningState = object


class WebNotifier:
    """Background notification loops for the headless web server."""

    def __init__(
        self,
        config: "DayzConfig",
        hub: WsHub,
        config_saver: "Callable[[Callable[[DayzConfig], None]], Awaitable[object]] | None" = None,
    ) -> None:
        self._config = config
        self.hub = hub
        # WebState.mutate_config in the app — the only sanctioned config write.
        self._config_saver = config_saver
        self._tasks: list[asyncio.Task] = []

        self._rcon_warning_state: _RestartWarningState | None = None
        self._rcon_manager: _RconRef = None

        self._last_failures: frozenset[tuple[str, str]] = frozenset()
        self._busy: bool = False
        # Loop-failure keys already warned about — see _log_loop_failure.
        self._warned_failures: set[str] = set()
        self._busy_check: Callable[[], bool] | None = None
        self._jobs: "JobManager | None" = None
        # Set by _on_job_finished; wakes the doctor loop early so a finished
        # job's effect on the server is re-checked instead of waited out.
        self._doctor_recheck = asyncio.Event()
        self._known_dmp_files: set[str] | None = None

    def set_rcon_manager(self, rcon) -> None:
        self._rcon_manager = rcon

    def _log_loop_failure(self, key: str, message: str) -> None:
        """Log a swallowed background-loop failure.

        Warn the first time each *key* fails so a broken loop is visible in the
        `zsm web` console (uvicorn runs at log_level="warning"), then demote to
        debug — a persistently failing 30-second loop must not flood the log.
        """
        if key in self._warned_failures:
            log.debug(message, exc_info=True)
            return
        self._warned_failures.add(key)
        log.warning("%s (further occurrences logged at debug level)", message, exc_info=True)

    def set_config(self, config: "DayzConfig") -> None:
        """Adopt a new config (registered with WebState.on_config_change).

        Every loop reads self._config each pass, so the running tasks pick the
        new values up on their next tick. The restart-warning state is the one
        thing built from the config once, so its thresholds and template are
        carried over here — what it has already sent this cycle is kept.
        """
        self._config = config
        state = self._rcon_warning_state
        if state is not None:
            state.thresholds = sorted(config.restart_warning_minutes or state.thresholds, reverse=True)
            state.template = config.restart_warning_template

    # -- job awareness ------------------------------------------------------

    @property
    def busy(self) -> bool:
        """True while a server-mutating job is pending/running.

        The doctor loop is gated on this: an install/uninstall/reinstall
        deletes and re-adds server + workshop files, so checks run mid-job
        report missing files that the admin is in the middle of replacing —
        a toast saying so is misleading, not helpful.
        """
        if self._busy:
            return True
        return bool(self._busy_check and self._busy_check())

    def attach_jobs(self, jobs: "JobManager") -> None:
        """Pause the doctor loop while *jobs* runs an exclusive job, and
        re-check shortly after each one finishes."""
        self._jobs = jobs
        self._busy_check = lambda: jobs.busy
        jobs.on_finished.append(self._on_job_finished)

    def _detach_jobs(self) -> None:
        if self._jobs is not None:
            try:
                self._jobs.on_finished.remove(self._on_job_finished)
            except ValueError:
                pass
        self._jobs = None
        self._busy_check = None

    def _on_job_finished(self, job: "Job") -> None:
        """A job just reached a terminal state — schedule the deferred doctor
        re-check so the admin learns whether the change broke something."""
        self._doctor_recheck.set()

    @property
    def _log_cleanup_enabled(self) -> bool:
        c = self._config
        return c.log_cleanup_days > 0 or c.log_cleanup_keep > 0

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        self._tasks.append(asyncio.create_task(self._restart_warning_loop()))
        self._tasks.append(asyncio.create_task(self._doctor_check_loop()))
        self._tasks.append(asyncio.create_task(self._crash_detection_loop()))
        self._tasks.append(asyncio.create_task(self._log_cleanup_loop()))
        self._tasks.append(asyncio.create_task(self._metrics_collection_loop()))
        self._tasks.append(asyncio.create_task(self._mod_update_check_loop()))
        self._tasks.append(asyncio.create_task(self._discord_status_loop()))
        self._tasks.append(asyncio.create_task(self._start_snapshot_loop()))

    async def stop(self) -> None:
        self._detach_jobs()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()

    # -- notification sink -------------------------------------------------

    async def _notify(
        self,
        message: str,
        severity: str = "information",
        timeout: int = 5,
        link: str | None = None,
    ) -> None:
        frame = {"type": "notification", "message": message, "severity": severity, "timeout": timeout}
        if link:
            frame["link"] = link
        await self.hub.broadcast(frame)

    # -- player events (callback from RconManager) --------------------------

    def on_player_event(self, event_type: str, guid: str, name: str) -> None:
        if not self._config.toast_player_connect:
            return
        display = name or guid[:8]
        if event_type == "connect":
            spawn(self._notify(f"Player Connected: {display}", "information", 5), name="toast-connect")
        elif event_type == "disconnect":
            spawn(self._notify(f"Player Disconnected: {display}", "information", 5), name="toast-disconnect")

    # -- restart warning loop -------------------------------------------------

    async def _restart_warning_loop(self) -> None:
        from app.rcon.rcon_scheduler import RestartWarningState

        state = RestartWarningState(
            thresholds=self._config.restart_warning_minutes,
            template=self._config.restart_warning_template,
        )
        self._rcon_warning_state = state

        while True:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                raise
            await self._restart_warning_tick(state)

    async def _restart_warning_tick(self, state) -> None:
        # toast_restart_warning is the panel's toast only: the in-game
        # warnings and the join lock run whatever it says.
        try:
            running = await asyncio.to_thread(_check_server_running, self._config)
        except Exception:
            running = False
        if not running:
            return

        try:
            remaining, deadline = await asyncio.to_thread(self._get_remaining_and_deadline)
        except Exception:
            return
        if remaining is None:
            return

        uptime_secs = await asyncio.to_thread(self._get_uptime_secs)
        if uptime_secs is not None:
            state.check_restart_detected(uptime_secs)

        from app.monitoring.discord_hooks import notify_restart_warning
        from app.rcon.rcon_scheduler import run_warning_cycle

        def _toast(threshold: int) -> None:
            spawn(
                self._notify(f"Server restart in ~{threshold} min", "warning", 10),
                name="toast-restart-warning",
            )

        await run_warning_cycle(
            state=state,
            remaining_minutes=remaining,
            rcon=self._rcon_manager,
            config=self._config,
            notify_toast=_toast if self._config.toast_restart_warning else None,
            notify_discord=notify_restart_warning,
            restart_interval=deadline,
        )

    def _get_remaining_and_deadline(self) -> tuple[int | None, int | None]:
        """Minutes until the next restart, and how long this cycle is.

        **The scheduler is asked first (TODO C3b).** The countdown used to come
        only from ``get_shutdown_deadline`` — messages.xml's
        ``<shutdown>1</shutdown>`` + ``<deadline>``, i.e. the *engine's* own
        timer — which is not what restarts the server on a zsm install. A
        ``restart`` task at 06:00 fired its systemd timer and every player got
        no warning at all, unless messages.xml happened to carry a matching
        deadline. The vanilla countdown stays as the fallback, for a server
        that really does restart itself that way.

        The warnings come from here, and not from the task's own unit, because
        they have to start ~15 minutes *before* it: a oneshot that slept that
        long would mean the timer's stated time is not when the restart
        happens. This runs in ``zsm web``, which is where the old countdown
        ran too — no new process is required that wasn't already.
        """
        from app.server.server_status import get_next_restart, get_server_uptime

        found = get_next_restart(self._config, get_server_uptime(self._config))
        return found if found is not None else (None, None)

    def _get_uptime_secs(self) -> float | None:
        from app.server.server_status import get_server_uptime

        uptime = get_server_uptime(self._config)
        if uptime is None:
            return None
        return uptime.total_seconds()

    # -- doctor check loop -----------------------------------------------

    _DOCTOR_INTERVAL = 60
    # Grace period after a job finishes before re-checking: the pipeline's last
    # step may still be flushing files, and a check that races it would report
    # failures the very next tick contradicts.
    _DOCTOR_SETTLE_DELAY = 10

    async def _doctor_check_loop(self) -> None:
        while True:
            woken_by_job = await self._wait_doctor_tick()
            if woken_by_job:
                try:
                    await asyncio.sleep(self._DOCTOR_SETTLE_DELAY)
                except asyncio.CancelledError:
                    raise
            await self._doctor_check_tick()

    async def _wait_doctor_tick(self) -> bool:
        """Wait for the next check. Returns True when a finished job woke us
        early, False on the ordinary interval."""
        try:
            await asyncio.wait_for(self._doctor_recheck.wait(), timeout=self._DOCTOR_INTERVAL)
        except asyncio.TimeoutError:
            return False
        self._doctor_recheck.clear()
        return True

    async def _doctor_check_tick(self) -> None:
        # Skipping while busy also freezes _last_failures, so the comparison
        # after the job is against the pre-job baseline — the admin is told
        # what the job changed, not what it was mid-way through changing.
        if not self._config.toast_doctor_check or self.busy:
            return

        try:
            failures = await asyncio.to_thread(self._run_doctor_checks)
        except Exception:
            self._log_loop_failure("doctor_check", "Doctor check tick failed")
            return

        failure_set = frozenset((label, detail) for label, _ok, detail in failures)
        new_failures = failure_set - self._last_failures
        self._last_failures = failure_set

        if new_failures:
            count = len(new_failures)
            # sorted() makes the sampled failure deterministic
            label, detail = sorted(new_failures)[0]
            short = detail.split("\n", 1)[0].strip()
            if len(short) > 80:
                short = short[:77] + "…"
            if count == 1:
                msg = f"Doctor: {label} — {short}"
            else:
                msg = f"Doctor: {count} issues — {label}: {short} (+{count - 1} more)"
            await self._notify(msg, "warning", 8, link="/doctor")

    def _run_doctor_checks(self) -> list[tuple[str, bool, str]]:
        from app.commands.doctor import get_failed_checks

        return get_failed_checks(self._config)

    # -- crash detection loop ---------------------------------------------

    async def _crash_detection_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                raise

            if not self._config.discord_hook_crash:
                continue

            try:
                new_files = await asyncio.to_thread(self._check_for_crash_dumps)
            except Exception:
                self._log_loop_failure("crash_detection", "Crash-dump check tick failed")
                continue
            if not new_files:
                continue

            await self._notify(f"Crash detected: {len(new_files)} new dump(s)", "error", 10)

            from app.monitoring.discord_hooks import notify_crash

            names = ", ".join(f.name for f in new_files[:3])
            if len(new_files) > 3:
                names += f" (+{len(new_files) - 3} more)"
            await asyncio.to_thread(notify_crash, self._config, details=names)

    def _check_for_crash_dumps(self) -> list[Path]:
        profiles_dir = self._config.server_dir_path / self._config.profiles_path
        if not profiles_dir.is_dir():
            return []

        current = set()
        dmp_files: dict[str, Path] = {}
        for f in profiles_dir.iterdir():
            if f.suffix.lower() == ".dmp" and f.is_file():
                current.add(f.name)
                dmp_files[f.name] = f

        if self._known_dmp_files is None:
            self._known_dmp_files = current
            return []

        new_names = current - self._known_dmp_files
        self._known_dmp_files = current
        return [dmp_files[n] for n in sorted(new_names)]

    # -- start snapshot loop -----------------------------------------------

    _START_SNAPSHOT_INTERVAL = 30

    async def _start_snapshot_loop(self) -> None:
        """Snapshot a run that started without zsm (plans/56 D).

        A scheduled restart and systemd's restart after a crash start the
        server with no zsm code in the way, so Analysis → "Since last start"
        compared against an older run. Skipped while a job runs: a job that
        starts the server takes its own snapshot.
        """
        while True:
            try:
                await asyncio.sleep(self._START_SNAPSHOT_INTERVAL)
            except asyncio.CancelledError:
                raise
            if self.busy:
                continue
            try:
                await asyncio.to_thread(self._snapshot_unrecorded_start)
            except Exception:
                self._log_loop_failure("start_snapshot", "Start-snapshot check failed")

    def _snapshot_unrecorded_start(self) -> bool:
        from app.ops.changeset_ops import snapshot_unrecorded_start
        from app.server.server_status import get_server_start_time

        return snapshot_unrecorded_start(self._config, get_server_start_time(self._config))

    # -- log cleanup loop --------------------------------------------------

    _LOG_CLEANUP_INTERVAL = 86400

    async def _log_cleanup_loop(self) -> None:
        while True:
            if self._log_cleanup_enabled:
                try:
                    deleted = await asyncio.to_thread(self._run_log_cleanup)
                except Exception:
                    deleted = 0
                if deleted > 0:
                    await self._notify(f"Log cleanup: removed {deleted} file(s)", "information", 6)

            try:
                await asyncio.sleep(self._LOG_CLEANUP_INTERVAL)
            except asyncio.CancelledError:
                raise

    def _run_log_cleanup(self) -> int:
        from app.ops.cleanup_ops import auto_cleanup_logs

        return auto_cleanup_logs(self._config)

    # -- metrics collection loop -------------------------------------------

    async def _metrics_collection_loop(self) -> None:
        while True:
            try:
                # Clamped: a sleep of <= 0 returns at once, and a hand-edited
                # config.json never meets settings_registry's floor.
                await asyncio.sleep(max(METRICS_INTERVAL_MIN, self._config.metrics_interval))
            except asyncio.CancelledError:
                raise

            if not self._config.metrics_enabled:
                continue

            try:
                await asyncio.to_thread(self._collect_and_record_metrics)
            except Exception:
                self._log_loop_failure("metrics", "Metrics collection tick failed")
                continue
            # The Dashboard's Performance tile reloads on this, since a tile
            # never runs its own timer (webui/CLAUDE.md "Dashboard").
            await self.hub.broadcast({"type": "metrics_recorded"})

    def _collect_and_record_metrics(self) -> None:
        import time

        from app.monitoring.metrics_db import MetricSnapshot, record_snapshot
        from app.server.server_status import read_server_cpu, read_server_fps, read_server_memory

        profiles = self._config.server_dir_path / self._config.profiles_path
        fps_raw = read_server_fps(profiles)
        mem_raw = read_server_memory(profiles, self._config)
        cpu_raw = read_server_cpu(self._config)

        snap = MetricSnapshot(
            timestamp=time.time(),
            fps=_parse_float(fps_raw),
            memory_mb=_parse_memory_mb(mem_raw),
            players=len(self._rcon_manager.known_players)
            if self._rcon_manager and hasattr(self._rcon_manager, "known_players")
            else None,
            cpu_pct=_parse_float(cpu_raw),
        )
        record_snapshot(snap, db_path=self._config.paths.metrics_db)

    # -- mod update check loop ---------------------------------------------

    async def _mod_update_check_loop(self) -> None:
        while True:
            interval = self._config.mod_update_check_interval
            if interval <= 0 or not self._config.steam_api_key:
                try:
                    await asyncio.sleep(300)
                except asyncio.CancelledError:
                    raise
                continue

            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise

            try:
                check = await asyncio.to_thread(self._check_mod_updates)
            except Exception:
                # Includes UpdateCheckError: a failed check leaves the last
                # good answer on disk untouched.
                self._log_loop_failure("mod_update_check", "Mod-update check tick failed")
                continue

            # Saved on every successful check, empty or not, so a check that
            # finds nothing clears a stale list (plans/50 B).
            try:
                from app.modmgr.workshop_api import save_detected_updates

                await asyncio.to_thread(
                    save_detected_updates, self._config.paths.mod_updates, check,
                )
            except Exception:
                log.debug("Failed to persist detected mod updates", exc_info=True)

            await self._announce_triage_updates(check)

            outdated = check.updates
            if outdated:
                count = len(outdated)
                sample = outdated[0].title
                await self._notify(
                    f"{count} mod update{'s' if count != 1 else ''} available ({sample})", "warning", 10
                )
                try:
                    from app.monitoring.discord_hooks import notify_mod_updates

                    tuples = [(u.mod_id, u.title, u.age_display) for u in outdated]
                    await asyncio.to_thread(notify_mod_updates, self._config, tuples)
                except Exception:
                    self._log_loop_failure(
                        "discord_mod_updates", "Discord mod-update notification failed"
                    )

    async def _announce_triage_updates(self, check) -> None:
        """Mods set aside after an update whose author has published since
        (plans/73 D): a toast and a Discord line each, once per update."""
        from app.ops import post_update

        remote = {u.mod_id: u.remote_updated for u in check.updates}
        try:
            fresh = await asyncio.to_thread(post_update.newly_updated, self._config, remote)
        except Exception:
            self._log_loop_failure("triage_updates", "Set-aside mod check failed")
            return
        if not fresh:
            return
        lines = [post_update.triage_update_line(e) for e in fresh]
        for line in lines:
            await self._notify(line, "information", 15, link="/mods")
        try:
            from app.monitoring.discord_hooks import notify_triage_updated

            await asyncio.to_thread(notify_triage_updated, self._config, lines)
        except Exception:
            self._log_loop_failure("discord_triage_updates", "Discord set-aside notification failed")
        if self._config_saver is None:
            post_update.mark_notified(self._config, fresh)
        else:
            await self._config_saver(lambda cfg: post_update.mark_notified(cfg, fresh))

    def _check_mod_updates(self):
        from app.modmgr.mod_ops import detect_mod_updates

        return detect_mod_updates(self._config)

    # -- Discord status embed loop ------------------------------------------

    _DISCORD_STATUS_INTERVAL = 120

    async def _discord_status_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._DISCORD_STATUS_INTERVAL)
            except asyncio.CancelledError:
                raise

            if not self._config.discord_hook_status or not self._config.discord_webhook_url:
                continue

            try:
                embed = await asyncio.to_thread(self._build_discord_status_embed)
                msg_id = await asyncio.to_thread(self._send_discord_status, embed)
                if msg_id and msg_id != self._config.discord_status_message_id:
                    await self._persist_status_message_id(msg_id)
            except Exception:
                self._log_loop_failure("discord_status", "Discord status update failed")

    async def _persist_status_message_id(self, msg_id: str) -> None:
        """Persist the Discord status message id through the owning WebState.

        Never self._config.save() here: DayzConfig.save() writes the whole
        dataclass, so saving the object this notifier holds rolled config.json
        back to whatever it held — the startup snapshot, before plans/11 — and
        discarded every setting changed in the web UI since.
        """
        if self._config_saver is None:
            # Standalone/test construction: keep the value in memory only.
            self._config.discord_status_message_id = msg_id
            return
        await self._config_saver(
            lambda cfg: setattr(cfg, "discord_status_message_id", msg_id)
        )

    def _build_discord_status_embed(self) -> dict:
        from app.commands.service import is_server_running
        from app.modmgr import modlist
        from app.monitoring.discord_hooks import build_status_embed
        from app.parsers.servercfg import read_cfg_values
        from app.server.server_status import (
            format_next_restart,
            format_uptime,
            get_next_restart,
            get_server_uptime,
            read_server_fps,
        )

        online = False
        try:
            online = is_server_running(self._config)
        except Exception:
            pass

        server_name = self._config.server_name or "DayZ Server"
        player_count = 0
        max_players = 0
        map_name = ""
        uptime_str = ""
        restart_str = ""
        fps_str = ""
        mod_count = 0

        try:
            vals = read_cfg_values(self._config.server_cfg_path, ["hostname", "maxPlayers"])
            if vals.get("hostname"):
                server_name = vals["hostname"]
            max_players = int(vals.get("maxPlayers", 0))
        except Exception:
            pass

        if self._rcon_manager and hasattr(self._rcon_manager, "known_players"):
            try:
                player_count = len(self._rcon_manager.known_players)
            except Exception:
                pass

        try:
            from app.maps.map_ops import get_active_map_name

            map_name = get_active_map_name(self._config) or ""
        except Exception:
            pass

        if online:
            uptime = None
            try:
                uptime = get_server_uptime(self._config)
                if uptime is not None:
                    uptime_str = format_uptime(uptime)
            except Exception:
                pass
            try:
                found = get_next_restart(self._config, uptime)
                if found is not None:
                    restart_str = format_next_restart(found[0])
            except Exception:
                pass
            try:
                profiles = self._config.server_dir_path / self._config.profiles_path
                fps_raw = read_server_fps(profiles)
                if fps_raw:
                    fps_str = fps_raw
            except Exception:
                pass

        try:
            # The mods the server loads: the mod list minus the disabled ones.
            # Before plans/77 this read the old `@Name=ID` lines of mods.txt,
            # which zsm never wrote, so the count was always 0.
            mod_count = len(modlist.active(self._config))
        except Exception:
            pass

        return build_status_embed(
            server_name=server_name,
            online=online,
            player_count=player_count,
            max_players=max_players,
            map_name=map_name,
            uptime=uptime_str,
            next_restart=restart_str,
            server_fps=fps_str,
            mod_count=mod_count,
        )

    def _send_discord_status(self, embed: dict) -> str | None:
        from app.monitoring.discord_hooks import send_or_update_status

        return send_or_update_status(self._config, embed)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_server_running(config) -> bool:
    """This panel's instance's server. Without the config it asked about
    `dayz-server.service`, main's, so a second instance's panel warned of
    restarts whenever main was up (plans/62 step 0)."""
    from app.commands.service import is_server_running

    return is_server_running(config)


def _parse_memory_mb(value: str | None) -> float | None:
    if not value:
        return None
    try:
        parts = value.split()
        num = float(parts[0].replace(",", ""))
        if len(parts) > 1 and parts[1].upper() == "GB":
            num *= 1024
        return num
    except (ValueError, IndexError):
        return None


def _parse_float(value: str | None) -> float | None:
    if not value:
        return None
    try:
        cleaned = value.split()[0].replace(",", "").rstrip("%")
        return float(cleaned)
    except (ValueError, IndexError):
        return None
