"""RCON restart warning system — countdown messages before server restarts.

Sends configurable warnings to all players via RCON at threshold minutes
before a scheduled restart (e.g., 15m, 10m, 5m, 3m, 1m). Also supports
server lock before shutdown and optional Discord webhook notifications.

Runs as an asyncio task inside the web notifier (``app/web/notifier.py``) and
``zsm supervise``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.shared.config import DayzConfig
    from app.rcon.rcon_manager import RconManager


log = logging.getLogger(__name__)

DEFAULT_THRESHOLDS = [15, 10, 5, 3, 1]
DEFAULT_TEMPLATE = "Server restart in {minutes} minute(s)!"


async def send_restart_warning(
    rcon: RconManager,
    minutes: int,
    template: str = DEFAULT_TEMPLATE,
) -> bool:
    """Send a restart warning message to all players via RCON.

    Returns True if the message was sent successfully.

    A template that can't be formatted (``{mins}``, a lone ``{``) falls back
    to the default wording rather than raising: the exception used to escape
    ``run_warning_cycle``, so players, the toast and Discord all got nothing.
    ``settings_registry`` refuses such a template on save; a hand-edited
    config.json does not go through it.
    """
    try:
        msg = template.format(minutes=minutes)
    except Exception:  # noqa: BLE001 - any failure means "use the default"
        log.warning("restart_warning_template %r can't be formatted; sending the default.", template)
        msg = DEFAULT_TEMPLATE.format(minutes=minutes)
    try:
        await rcon.send_message(msg)
        return True
    except Exception:
        return False


async def send_lock_and_shutdown(rcon: RconManager) -> bool:
    """Lock server (prevent new joins) then shutdown via RCON.

    Returns True if commands were sent successfully.
    """
    try:
        await rcon.send_command("#lock")
        await asyncio.sleep(2)
        await rcon.send_command("#shutdown")
        return True
    except Exception:
        return False


class RestartWarningState:
    """Tracks which warning thresholds have been sent for the current cycle.

    Reset when a restart is detected (uptime drops) or when all thresholds
    have been sent and the restart window has passed.
    """

    def __init__(
        self,
        thresholds: list[int] | None = None,
        template: str = DEFAULT_TEMPLATE,
    ):
        self.thresholds = sorted(thresholds or DEFAULT_THRESHOLDS, reverse=True)
        self.template = template
        self._sent: set[int] = set()
        self._last_uptime_secs: float = 0.0
        self._last_remaining: int | None = None
        # True from a #lock until the #unlock (or a restart, which unlocks).
        self.locked = False

    def reset(self) -> None:
        """Reset all sent thresholds (e.g., after a restart)."""
        self._sent.clear()

    def check_cycle_rollover(self, remaining_minutes: int) -> bool:
        """Reset when the countdown climbs, i.e. a new cycle has begun.

        Within a cycle the remaining time only ever falls, so an increase is
        the restart window having passed. ``check_restart_detected`` covers the
        normal case (the server really did go down), but not the one where it
        didn't — a restart that failed, or a task disabled mid-countdown, left
        every threshold marked sent and the *next* cycle silent.
        """
        previous = self._last_remaining
        self._last_remaining = remaining_minutes
        if previous is not None and remaining_minutes > previous and self._sent:
            self.reset()
            return True
        return False

    def check_restart_detected(self, uptime_secs: float) -> bool:
        """Detect if server restarted (uptime wrapped around)."""
        if uptime_secs < self._last_uptime_secs:
            self._last_uptime_secs = uptime_secs
            self.reset()
            self.locked = False  # a fresh server starts unlocked
            return True
        self._last_uptime_secs = uptime_secs
        return False

    def get_pending_warnings(
        self,
        remaining_minutes: int,
        restart_interval: int | None = None,
    ) -> list[int]:
        """Get thresholds that should fire now but haven't yet.

        Returns the thresholds (in descending order) that:
        - Are >= remaining_minutes (time has passed that threshold)
        - Are <= restart_interval (skip thresholds beyond the cycle length)
        - Haven't been sent yet this cycle
        """
        pending: list[int] = []
        for threshold in self.thresholds:
            if restart_interval is not None and threshold > restart_interval:
                continue
            if remaining_minutes <= threshold and threshold not in self._sent:
                pending.append(threshold)
        return pending

    def mark_sent(self, threshold: int) -> None:
        """Record that a threshold has been sent."""
        self._sent.add(threshold)

    @property
    def all_sent(self) -> bool:
        return self._sent >= set(self.thresholds)


async def run_warning_cycle(
    state: RestartWarningState,
    remaining_minutes: int,
    rcon: RconManager | None,
    config: DayzConfig,
    notify_toast: callable | None = None,
    notify_discord: callable | None = None,
    restart_interval: int | None = None,
) -> list[int]:
    """Process one warning cycle. Returns list of thresholds that fired.

    Called periodically (e.g., every 30s) from the notification manager.
    ``restart_interval`` is the total cycle length in minutes so that
    thresholds beyond the interval are silently skipped.
    """
    state.check_cycle_rollover(remaining_minutes)
    await _update_join_lock(state, remaining_minutes, rcon, config, restart_interval)
    pending = state.get_pending_warnings(remaining_minutes, restart_interval)
    if not pending:
        return []

    # One warning per tick, for the nearest threshold. A countdown that starts
    # late (a task due in 7 minutes, the panel restarted inside the window,
    # since the sent set lives in memory) used to tell players "15 minutes"
    # and "10 minutes" in the same second. The larger
    # ones are marked sent unsaid. A threshold reached on time says its own
    # number (the tick runs every 30 s and the minutes are floored, so 14
    # left is the 15-minute warning); one passed long ago says what is left.
    nearest = min(pending)
    minutes = nearest if remaining_minutes >= nearest - 1 else max(remaining_minutes, 1)

    # RCON broadcast. `is_connected` is a method: tested bare it was always
    # true, so a warning sent while the panel's connection was still coming
    # up (two minutes after a start) went nowhere and was marked sent.
    # While RCon is down the warning waits for the next
    # tick, nothing marked, and then says the minutes left; past the last
    # minute it goes to the toast and Discord without the game.
    if rcon is not None:
        delivered = rcon.is_connected() and await send_restart_warning(rcon, minutes, state.template)
        if not delivered and remaining_minutes > 1:
            return []

    # Panel toast
    if notify_toast:
        notify_toast(minutes)

    # Discord webhook
    if notify_discord:
        try:
            await asyncio.to_thread(notify_discord, config, minutes)
        except Exception:
            pass

    for threshold in pending:
        state.mark_sent(threshold)
    return [nearest]


def _lock_window(config: DayzConfig, restart_interval: int | None) -> int:
    """Minutes before the restart that joins stay locked; 0 means never.

    A window as long as the cycle would lock the server for all of it, so it
    is skipped there, as a warning threshold longer than the cycle is.
    """
    minutes = config.restart_lock_at_minutes or 0
    if minutes <= 0:
        return 0
    if restart_interval is not None and minutes >= restart_interval:
        return 0
    return minutes


async def _update_join_lock(
    state: RestartWarningState,
    remaining_minutes: int,
    rcon: RconManager | None,
    config: DayzConfig,
    restart_interval: int | None,
) -> None:
    """Send #lock inside the window before a restart, #unlock outside it.

    The unlock is the half that matters: a restart that does not happen (an
    update deferred for online players, a failed restart, a task disabled
    mid-countdown) moves the countdown back out of the window, and without it
    the server would refuse every join until someone noticed. A restart that
    does happen clears ``state.locked`` in ``check_restart_detected``, since
    the new server starts unlocked. A failed send is tried again next tick.
    """
    window = _lock_window(config, restart_interval)
    want_locked = window > 0 and remaining_minutes <= window
    if want_locked == state.locked:
        return
    if rcon is None or not rcon.is_connected():
        return
    command = "#lock" if want_locked else "#unlock"
    try:
        await rcon.send_command(command)
    except Exception:  # noqa: BLE001 - retried on the next tick
        log.warning("Could not send %s over RCon; trying again next tick.", command)
        return
    state.locked = want_locked
    log.info("Sent %s (%d min to restart).", command, remaining_minutes)
