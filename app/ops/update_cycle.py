"""An update that is safe to run unattended (TODO C3).

``pipeline.build_update_steps`` is the *transformation* — update the server,
fetch mods, copy them, merge the XML. This module is the *operation* around
it, and it exists because of everything an operator at a keyboard does
implicitly and a 04:00 systemd timer does not:

- **Stop the server first.** The pipeline contains no ``stop_server``,
  ``start_server`` or ``is_server_running`` at all; the only guard anywhere
  was ``POST /api/update/run``'s 409, i.e. in the *web layer*, so the CLI path
  a scheduled task takes had none. Running it against a live server rewrites
  mod directories and merges mission XML underneath a ``DayZServer`` that
  still has the old PBOs mapped and the XML it read at boot.
- **Not do it while people are playing.** See ``check_players``.
- **Put the server back the way it was found.** Started if it was running,
  left alone if it was already stopped — and started **even when the pipeline
  failed or was cancelled**, which is why that lives in a ``finally``. An
  unattended update that fails must not also take the server offline until
  someone notices.
- **Undo a bad update rather than leave it half-applied.** ``copy_mods`` and
  ``merge_xml`` already carry ``undo``; nothing called them automatically.
  A failed run now rolls back before the server comes up, so it comes up on
  the mods it had. That is also the bridge to C5: ``remove_old_mods``' undo
  puts back the versions it set aside, after ``copy_mods``' undo removed
  their replacements.
- **Check that the server came back** (plans/73). ``post_update.check_boot``
  watches the boot it started and names the mods behind a failed one. This
  path only reports: it never disables a mod, because a server that boots
  without a mod loses that mod's saved objects for good.

Async, because the pipeline is. The CLI drives it with ``asyncio.run`` and
the web awaits it inside a job.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.ops.pipeline import StepResult

# How long to wait for the unit to actually report stopped before giving up.
# `systemctl stop` is synchronous, so this is insurance for the Windows path
# and for a unit that takes its time — not the normal case.
STOP_TIMEOUT = 120.0
_STOP_POLL = 2.0


@dataclass
class PlayerGate:
    """Whether an unattended update may go ahead, and why."""

    proceed: bool
    reason: str
    #: Players counted, or None when the question could not be asked at all.
    players: int | None = None


@dataclass
class UpdateCycleResult:
    ran: bool
    deferred_reason: str | None = None
    results: list[StepResult] = field(default_factory=list)
    rolled_back: bool = False
    was_running: bool = False
    server_started: bool = False
    error: str | None = None
    #: post_update's report on the boot after the update, when the cycle
    #: started the server.
    boot: dict | None = None

    @property
    def failed_steps(self) -> list[StepResult]:
        return [r for r in self.results if r.status == "failed"]


async def check_players(config, *, log: Callable[[str], None] | None = None) -> PlayerGate:
    """Decide whether enough of the server is idle to update it.

    ``config.update_max_players`` is the limit; **-1 disables the check**.
    The three "cannot tell" cases are deliberately not treated alike, because
    they do not mean the same thing:

    - **RCon is not configured** → proceed. The admin has opted out of zsm
      being able to see players at all, and deferring forever would mean the
      scheduled update never runs once — a silent no-op is worse than an
      update at a quiet hour.
    - **The server is not running** → proceed. Nobody is online, and this is
      the normal state for a scheduled update on a box that restarts first.
    - **RCon is configured, the server is up, and the query failed** → defer.
      Here zsm genuinely could not tell, and something is already wrong; the
      same discipline ``firewall_ops`` uses, where "could not tell" never
      renders as "safe to proceed" for a destructive action.
    """
    emit = log or (lambda _line: None)
    limit = int(getattr(config, "update_max_players", 0))
    if limit < 0:
        return PlayerGate(True, "Player check disabled (update_max_players = -1).")

    from app.rcon import rcon_ops
    from app.server import service_control

    if not rcon_ops.is_rcon_configured(config):
        return PlayerGate(True, "RCon is not configured, so Zedstead cannot count players.")

    if not await asyncio.to_thread(service_control.is_server_running, config):
        return PlayerGate(True, "Server is not running — nobody is online.", 0)

    try:
        players = await rcon_ops.get_rcon_players(config)
    except Exception as exc:
        emit(f"Could not read the player list over RCon: {exc}")
        return PlayerGate(
            False,
            f"the player list could not be read over RCon ({exc}) — refusing to "
            "update a running server blind",
        )

    count = len(players)
    if count > limit:
        return PlayerGate(
            False, f"{count} player(s) online, limit is {limit}", count
        )
    return PlayerGate(True, f"{count} player(s) online, limit is {limit}", count)


async def _wait_until_stopped(config, emit: Callable[[str], None]) -> bool:
    from app.server import service_control

    deadline = time.monotonic() + STOP_TIMEOUT
    while time.monotonic() < deadline:
        if not await asyncio.to_thread(service_control.is_server_running, config):
            return True
        await asyncio.sleep(_STOP_POLL)
    emit(f"The server was still running {STOP_TIMEOUT:.0f}s after the stop command.")
    return False


async def stop_and_wait(config, emit: Callable[[str], None]) -> tuple[str, str] | None:
    """Stop the server and wait until it is down.

    ``None`` when it stopped, else ``(why it did not, short error)``. The work
    that needed it down must not run then.
    """
    from app.server import service_control

    emit("Stopping the server...")
    stop = await asyncio.to_thread(service_control.stop_server, config)
    if not stop.ok:
        emit(f"Could not stop the server: {stop.message}")
        return f"the server could not be stopped ({stop.message})", stop.message
    if not await _wait_until_stopped(config, emit):
        return "the server did not stop", "stop timed out"
    emit("Server stopped.")
    return None


async def start_again(config, emit: Callable[[str], None]):
    """Start the server a maintenance run stopped, and say how it went.

    ``anyway``: an unattended start must not stop at the runtime check.
    """
    from app.server import service_control

    emit("Starting the server...")
    start = await asyncio.to_thread(service_control.start_server, config, anyway=True)
    if start.ok:
        emit("Server started.")
    else:
        emit(f"Could not start the server: {start.message}")
    return start


async def run_while_stopped(config, work: Callable[[], Any], *, emit: Callable[[str], None]) -> str | None:
    """Stop a running server, run *work* in a thread, start the server again.

    Started again even when *work* raises, so a failed run does not leave the
    server down. A server that was already stopped is left stopped. Returns
    why the server could not be stopped (then *work* did not run) or started
    again, else ``None``.
    """
    from app.server import service_control

    was_running = await asyncio.to_thread(service_control.is_server_running, config)
    if was_running:
        refused = await stop_and_wait(config, emit)
        if refused:
            return refused[0]
    problem = None
    try:
        await asyncio.to_thread(work)
    finally:
        if was_running:
            start = await start_again(config, emit)
            if not start.ok:
                problem = f"the server could not be started again ({start.message})"
    return problem


async def _pre_update_backup(config, emit: Callable[[str], None]) -> None:
    """Quick backup before an unattended update, if that setting is on.

    The pipeline's own undo covers mods and XML. What it cannot cover is a mod
    update that corrupts persistence on the next boot, and that is what a
    scheduled update risks while nobody is watching — so the insurance is the
    persistence data, not the server directory.
    """
    if not getattr(config, "auto_backup_before_restart", False):
        return
    from app.ops import backup_ops

    emit("Taking a quick backup before updating...")
    try:
        path = await asyncio.to_thread(
            backup_ops.create_backup, config, backup_ops.BackupContents(), "before scheduled update", emit
        )
    except Exception as exc:
        # Not fatal: a mission with no storage folder yet is a normal state, and
        # refusing to update because of it would block the first update on a
        # fresh install.
        emit(f"Pre-update backup failed ({exc}) — continuing with the update.")
        return
    if path is None:
        emit("Nothing to back up yet — continuing.")


async def run_update_cycle(
    config,
    *,
    emit: Callable[[str], None],
    runner: Any,
    cancel_event: "asyncio.Event | None" = None,
    dry_run: bool = False,
    force: bool = False,
) -> UpdateCycleResult:
    """Stop → back up → update → roll back on failure → start.

    ``force`` skips the player gate only. It does not skip the stop, which is
    not a policy but a correctness requirement.
    """
    from app.ops import pipeline, post_update
    from app.server import service_control

    if force:
        gate = PlayerGate(True, "Player check skipped (--force).")
    else:
        gate = await check_players(config, log=emit)
    emit(f"Player check: {gate.reason}")
    if not gate.proceed:
        emit("Update deferred — it will be tried again at the next scheduled time.")
        return UpdateCycleResult(ran=False, deferred_reason=gate.reason)

    was_running = await asyncio.to_thread(service_control.is_server_running, config)
    result = UpdateCycleResult(ran=True, was_running=was_running)

    if dry_run:
        emit("[DRY RUN] Would stop the server, run the update pipeline, then start it again.")

    before = None
    if was_running and not dry_run:
        # Before the backup, so the backup counts as taken during this update.
        before = await asyncio.to_thread(post_update.snapshot, config)
        await _pre_update_backup(config, emit)
        refused = await stop_and_wait(config, emit)
        if refused:
            result.ran = False
            result.deferred_reason, result.error = refused
            return result

    steps = pipeline.build_update_steps(config)
    ctx = pipeline.PipelineContext(
        config=config,
        emit=emit,
        runner=runner,
        should_run=lambda _key: True,
        dry_run=dry_run,
        cancel_event=cancel_event,
    )

    rollback_failed: list[str] = []
    try:
        results, completed_undoable = await pipeline.run_pipeline(steps, ctx)
        result.results = results

        if result.failed_steps and completed_undoable and not dry_run:
            names = ", ".join(r.label for r in result.failed_steps)
            emit(f"Update failed ({names}) — rolling back so the server comes up as it was.")
            rollback_failed = await pipeline.rollback_pipeline(completed_undoable, ctx)
            result.rolled_back = True
            if rollback_failed:
                result.error = result.error or pipeline.rollback_failed_message(rollback_failed)
    finally:
        # In a finally on purpose: a failed pipeline, a cancel, or an
        # unexpected exception must not leave the server down until somebody
        # notices. Restarting a server that was already stopped would be a
        # surprise, so only `was_running` starts one. A failed rollback is
        # the exception: the old mods may not be back, and a boot without a
        # mod deletes its saved objects, so the server stays stopped.
        if rollback_failed and was_running and not dry_run:
            emit("The rollback failed, so the server stays stopped: a boot without its mods deletes their saved objects.")
        elif was_running and not dry_run:
            since = time.time()
            start = await start_again(config, emit)
            result.server_started = start.ok
            if not start.ok:
                result.error = result.error or start.message

    # After the finally, so a cancel or an exception does not wait out a boot.
    if result.server_started and before is not None:
        result.boot = await post_update.check_boot(config, before, since=since, emit=emit)
    return result
