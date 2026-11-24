"""Background job engine.

JobManager tracks long-running server-mutating operations (update pipeline,
later: backups, reinstalls, map switch, bundles, timer install) with a
ring-buffered log, WS broadcast on every status/log change, and a single
exclusive-job policy so two mutating operations can never run at once.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import signal
import subprocess
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.shared.platform import IS_WINDOWS
from app.web.tasks import spawn
from app.web.ws import WsHub

log = logging.getLogger(__name__)

_MAX_JOBS = 50
_LOG_MAXLEN = 2000
# Rich's own rule for a tag: "[" then a lower-case letter, "#", "/" or "@".
# Any other bracket is text: "[1/3]" (a counter), "[  0%]" (SteamCMD), "[U:1:…]".
# A backslash before the "[" escapes it (`rich.markup.escape`).
_MARKUP_RE = re.compile(r"(\\*)\[([a-z#/@][^\[\]]*?)\]")


def _markup_tag(m: re.Match) -> str:
    slashes, tag = m.group(1), m.group(2)
    if len(slashes) % 2:
        return slashes[: len(slashes) // 2] + f"[{tag}]"
    return slashes[: len(slashes) // 2]


def strip_markup(text: str) -> str:
    """Strip Rich markup tags (e.g. ``[red]...[/red]``) for plain-text sinks
    (the job log, rendered as-is in the browser's JobConsole). Only what Rich
    reads as a tag goes: the old "any [...]" rule took the "[1/3]" counters out
    of the update log (user test in 2026 Q4)."""
    return _MARKUP_RE.sub(_markup_tag, text)


# Terminal escape sequences: CSI (colours, cursor moves: ESC [ ... final
# byte), OSC (titles, links: ESC ] ... BEL or ESC \), the two-byte escapes,
# and a lone ESC (a sequence cut short).
_ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])?")


def strip_ansi(text: str) -> str:
    """Strip terminal escape sequences, which the browser's job log shows as
    text: SteamCMD colours its output (``ESC[0m@ShutdownOnFailedCommand``)."""
    return _ANSI_RE.sub("", text)


class JobConflictError(Exception):
    """Raised by JobManager.start() when an exclusive job is already running."""

    def __init__(self, active_job_id: str) -> None:
        super().__init__(f"Another job is already running: {active_job_id}")
        self.active_job_id = active_job_id


class JobFailed(Exception):
    """Ends a job as failed and keeps *result* as its result.

    For a job whose coroutine collects its own outcome, such as the update
    pipeline's step results: returning them ended the job as "success", so the
    toast said "finished" over a failed server install (browser test
    2026-11-17, F6), while the page still needs the results to show them.
    """

    def __init__(self, message: str, result: dict | None = None) -> None:
        super().__init__(message)
        self.result = result


def conflict_response(exc: JobConflictError) -> JSONResponse:
    """The flat 409 body routers return on JobConflictError."""
    return JSONResponse(
        status_code=409,
        content={"detail": str(exc), "active_job_id": exc.active_job_id},
    )


@dataclass
class Job:
    id: str
    name: str
    kind: str
    hub: WsHub
    status: str = "pending"  # pending | running | success | failed | cancelled
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    result: dict | None = None
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    log: "deque[tuple[int, str]]" = field(default_factory=lambda: deque(maxlen=_LOG_MAXLEN))
    seq: int = 0
    loop: asyncio.AbstractEventLoop | None = None
    # The asyncio task running the job — what JobManager.kill cancels.
    task: "asyncio.Task | None" = None
    kill_requested: bool = False
    # Set when the job ran SteamCMD (stream_subprocess sees it go by). What
    # the job did to the login is then worth re-measuring once it is done;
    # its `status` alone can't say (a dry run, a mod already cached).
    ran_steamcmd: bool = False
    # The instance the job works on (plans/62); None for a manager nobody
    # views per instance. One slot is shared by every instance (D3), so the
    # drawer names it when it is not the page's own.
    instance: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "instance": self.instance,
            "status": self.status,
            # A cancel is a *request*: the job keeps running until its next
            # cancellation checkpoint (between files, or the subprocess's 10s
            # grace). The UI disables its Cancel button off this flag, so it
            # must be part of the broadcast status, not client-local state.
            "cancel_requested": self.cancel_event.is_set(),
            "kill_requested": self.kill_requested,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "result": self.result,
        }

    def log_line(self, line: str) -> None:
        """Append a log line and broadcast it. Event-loop-thread only — call
        log_line_threadsafe from code running inside asyncio.to_thread.

        Every job-log line is recorded here, so this is where terminal escape
        sequences come off (`strip_ansi`). A `stream_subprocess` transform
        (secret redaction) has already run on the line; the CLI prints to its
        terminal without coming here and keeps its colours.
        """
        line = strip_ansi(line)
        self.seq += 1
        seq = self.seq
        self.log.append((seq, line))
        spawn(
            self.hub.broadcast(
                {"type": "job_log", "job_id": self.id, "seq": seq, "line": line}
            ),
            name=f"job-log-{self.id}",
        )

    def log_line_threadsafe(self, line: str) -> None:
        """Safe to call from any thread (e.g. a sync op running inside
        asyncio.to_thread) — schedules log_line back on the owning loop."""
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.log_line, line)

    def logs_after(self, seq: int) -> list[tuple[int, str]]:
        """Log lines with seq > *seq* — used for WS-reconnect catch-up."""
        return [(s, line) for s, line in self.log if s > seq]

    async def broadcast_status(self) -> None:
        await self.hub.broadcast({"type": "job_status", "job": self.to_dict()})


class JobManager:
    def __init__(self, hub: WsHub) -> None:
        self.hub = hub
        self.jobs: dict[str, Job] = {}
        self.mutating_lock = asyncio.Lock()
        self._order: list[str] = []
        # The currently pending/running exclusive job. Set synchronously in
        # start() (not in _run) so two start() calls in the same event-loop
        # window can't both pass the conflict check — the lock itself is only
        # acquired later, inside the created task.
        self._active_exclusive: Job | None = None
        # Sync callbacks fired once a job reaches a terminal state, after the
        # exclusive slot is released (so `busy` already reads False). Used by
        # WebNotifier to re-run its doctor check against whatever the job left
        # behind. A raising callback must never break job completion.
        self.on_finished: list[Callable[[Job], None]] = []
        # Awaited by an exclusive job once it holds the lock, before it runs.
        # WebState waits out a Steam-login probe already in flight: `busy`
        # keeps a probe from starting beside a job, and this keeps a job from
        # starting beside a probe (two SteamCMDs on one STEAMROOT corrupt the
        # cached login and the workshop manifest). plans/51 D.
        self.before_exclusive: Callable[[], Awaitable[None]] | None = None

    @property
    def busy(self) -> bool:
        """True while an exclusive job is pending/running.

        Read-only endpoints use this to skip probes that would spawn a second
        SteamCMD against the same STEAMROOT (concurrent instances corrupt the
        cached login token and the workshop manifest).
        """
        return self._active_exclusive is not None

    def refuse_while_busy(self) -> None:
        """409 for a write made outside the job slot while an exclusive job
        runs: the job rewrites the same files or starts and stops the server,
        and the two writes would interleave."""
        active = self._active_exclusive
        if active is not None:
            raise HTTPException(status_code=409, detail=str(JobConflictError(active.id)))

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def list_jobs(self) -> list[Job]:
        return [self.jobs[jid] for jid in self._order if jid in self.jobs]

    def _register(self, job: Job) -> None:
        self.jobs[job.id] = job
        self._order.append(job.id)
        while len(self._order) > _MAX_JOBS:
            oldest = self._order.pop(0)
            self.jobs.pop(oldest, None)

    def start(
        self,
        name: str,
        kind: str,
        coro_fn: Callable[[Job], Awaitable[dict | None]],
        *,
        exclusive: bool = True,
        instance: str | None = None,
    ) -> Job:
        """Create and launch a job.

        Raises JobConflictError if `exclusive` and another exclusive job is
        currently pending/running — whichever instance started it — so
        callers should catch it and return conflict_response(exc) (409).
        """
        if exclusive and self._active_exclusive is not None:
            raise JobConflictError(self._active_exclusive.id)

        job = Job(id=secrets.token_hex(8), name=name, kind=kind, hub=self.hub, instance=instance)
        self._register(job)
        if exclusive:
            self._active_exclusive = job
        # spawn, not a bare create_task: the registry evicts past _MAX_JOBS, so
        # a long non-exclusive job can outlive the last reference to its Job.
        job.task = spawn(self._run(job, coro_fn, exclusive=exclusive), name=f"job-{job.id}")
        return job

    async def _run(
        self,
        job: Job,
        coro_fn: Callable[[Job], Awaitable[dict | None]],
        *,
        exclusive: bool,
    ) -> None:
        job.loop = asyncio.get_running_loop()
        if exclusive:
            await self.mutating_lock.acquire()
        try:
            if exclusive and self.before_exclusive is not None:
                await self.before_exclusive()
            job.status = "running"
            job.started_at = time.time()
            await job.broadcast_status()
            try:
                job.result = await coro_fn(job)
                job.status = "cancelled" if job.cancel_event.is_set() else "success"
            except asyncio.CancelledError:
                job.status = "cancelled"
            except Exception as e:
                # A step that fails *because* it was cancelled (a download that
                # returned False, a killed subprocess) is a cancel, not a failure.
                job.status = "cancelled" if job.cancel_event.is_set() else "failed"
                job.error = str(e)
                if isinstance(e, JobFailed):
                    job.result = e.result
                # The log is what every page shows of a job; the error field
                # is not rendered anywhere. A job that raised before it logged
                # (the update button refusing a checkout with local changes)
                # showed only "failed" and "No log output yet."
                last = job.log[-1][1] if job.log else ""
                if job.status == "failed" and job.error and job.error not in last:
                    job.log_line(job.error)
        finally:
            job.finished_at = time.time()
            # Release the exclusive slot BEFORE broadcasting: a raising
            # broadcast must never leave the lock held (every later job
            # would 409 until restart).
            if exclusive:
                self._active_exclusive = None
                if self.mutating_lock.locked():
                    self.mutating_lock.release()
            for callback in self.on_finished:
                try:
                    callback(job)
                except Exception:
                    log.debug("Job-finished callback failed", exc_info=True)
            try:
                await job.broadcast_status()
            except Exception:
                log.debug("Final job status broadcast failed", exc_info=True)

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None or job.status not in ("pending", "running"):
            return False
        job.cancel_event.set()
        return True

    def kill(self, job_id: str) -> bool:
        """Stop a running job now, for when a cancel is stuck.

        Cancels the job's task, so the coroutine unwinds at its current await
        and the exclusive slot is released; a subprocess it was streaming has
        its whole tree SIGKILLed on the way out (stream_subprocess). Work already
        handed to a thread (``asyncio.to_thread`` — a file copy, an XML merge)
        cannot be interrupted and finishes on its own.

        Only a *running* job: its ``_run`` is inside the try whose finally frees
        the slot. A task cancelled before it starts never enters that try.
        """
        job = self.jobs.get(job_id)
        if job is None or job.status != "running":
            return False
        job.kill_requested = True
        job.cancel_event.set()
        if job.task is not None and not job.task.done():
            job.task.cancel()
        return True



class InstanceJobs:
    """One instance's view of the panel's ``JobManager`` (plans/62 §2.4).

    A bundle's ``jobs`` is one of these, so a job a router starts is
    labelled with the bundle's instance without the router passing it. The
    slot, the log, cancel and kill are the shared manager's: a job on any
    instance makes every instance's next one answer 409 (D3).
    """

    def __init__(self, manager: JobManager, instance: str) -> None:
        object.__setattr__(self, "manager", manager)
        object.__setattr__(self, "instance", instance)

    def start(
        self,
        name: str,
        kind: str,
        coro_fn: Callable[[Job], Awaitable[dict | None]],
        *,
        exclusive: bool = True,
    ) -> Job:
        return self.manager.start(name, kind, coro_fn, exclusive=exclusive, instance=self.instance)

    def __getattr__(self, name: str):
        return getattr(self.manager, name)

    def __setattr__(self, name: str, value) -> None:
        setattr(self.manager, name, value)

def _process_tree(pid: int) -> list[int]:
    """*pid* followed by every live descendant of it.

    Windows returns just *pid* — ``taskkill /T`` walks the tree itself.
    """
    if IS_WINDOWS:
        return [pid]
    children: dict[int, list[int]] = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return [pid]
    for name in entries:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as fh:
                stat = fh.read()
        except OSError:
            continue
        # The command name is parenthesised and may contain spaces or ")" —
        # ppid is the second field after the *last* ")".
        try:
            ppid = int(stat.rsplit(b")", 1)[1].split()[1])
        except (IndexError, ValueError):
            continue
        children.setdefault(ppid, []).append(int(name))
    tree, stack = [pid], [pid]
    while stack:
        for child in children.get(stack.pop(), []):
            tree.append(child)
            stack.append(child)
    return tree


def _signal_process_tree(pids: list[int], *, force: bool) -> None:
    """SIGTERM (SIGKILL when *force*) every pid from ``_process_tree``.

    Best effort: a pid that already exited, or a root-owned child of ``sudo``,
    is skipped.
    """
    if IS_WINDOWS:
        # TerminateProcess is already a hard kill; /T takes the children too.
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pids[0])],
            capture_output=True, timeout=15, check=False,
        )
        return
    sig = signal.SIGKILL if force else signal.SIGTERM
    for pid in pids:
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass


async def stream_subprocess(
    job: Job,
    cmd: list[str],
    cwd: str | None = None,
    transform: Callable[[str], str] | None = None,
) -> int:
    """Run cmd, streaming each output line into the job log.

    The SteamCMD streaming seam: used by JobRunner.run_command. Terminates
    the process *tree* (10s grace before kill) if job.cancel_event gets set
    mid-run. *transform* filters each line before logging (secret redaction).

    The tree, not just the direct child: on Linux the command is
    ``steamcmd.sh``, a bash wrapper that runs the real SteamCMD binary as a
    child. Terminating only the wrapper orphaned that binary, which kept
    downloading and held stdout open — so the log reader never hit EOF and a
    Cancel waited for the whole download to finish.
    """
    from app.shared import steamcmd_lock

    if not steamcmd_lock.is_steamcmd(cmd):
        return await _stream(job, cmd, cwd, transform)
    job.ran_steamcmd = True
    # Another zsm process (a scheduled update, another instance's panel)
    # may be running SteamCMD on the same STEAMROOT: wait for it, in the log.
    try:
        async with steamcmd_lock.hold_async(
            cmd, on_wait=job.log_line, cancelled=job.cancel_event.is_set
        ):
            return await _stream(job, cmd, cwd, transform, say_when_quiet=True)
    except steamcmd_lock.SteamcmdBusy as exc:
        job.log_line(str(exc))
        return -15  # as if stopped by the cancel, which is what happened


async def _stream(
    job: Job,
    cmd: list[str],
    cwd: str | None,
    transform: Callable[[str], str] | None,
    say_when_quiet: bool = False,
) -> int:
    started = time.monotonic()
    last_line = [started]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
    )

    async def _read_output() -> None:
        assert proc.stdout is not None
        async for raw_line in proc.stdout:
            line = raw_line.decode(errors="replace").rstrip("\n")
            if transform is not None:
                line = transform(line)
            job.log_line(line)
            last_line[0] = time.monotonic()

    async def _watch_quiet() -> None:
        # SteamCMD writes to this pipe in blocks, so a download can leave its
        # banner "-- type 'quit' to exit --" as the last line for minutes. The
        # notice goes straight to the job log, past `transform`, so it never
        # reaches the tail that explains a failure (role 1 user test, 2026 Q4).
        from app.shared import steam

        while True:
            await asyncio.sleep(steam._QUIET_POLL_SECONDS)
            now = time.monotonic()
            if now - last_line[0] >= steam._QUIET_SECONDS:
                job.log_line(steam._quiet_notice(now - started))
                last_line[0] = now

    async def _watch_cancel() -> None:
        await job.cancel_event.wait()
        # Snapshot the tree first: once the wrapper exits, its children are
        # re-parented and a later walk would no longer find them.
        pids = await asyncio.to_thread(_process_tree, proc.pid)
        await asyncio.to_thread(_signal_process_tree, pids, force=False)
        # Done means the wrapper exited AND every pipe holder is gone (EOF).
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if proc.returncode is not None and read_task.done():
                return
            await asyncio.sleep(0.1)
        await asyncio.to_thread(_signal_process_tree, pids, force=True)

    read_task = asyncio.create_task(_read_output())
    watch_task = asyncio.create_task(_watch_cancel())
    quiet_task = asyncio.create_task(_watch_quiet()) if say_when_quiet else None
    try:
        returncode = await proc.wait()
        await read_task
    except asyncio.CancelledError:
        # The job's task was cancelled (JobManager.kill): kill the tree now,
        # and don't wait on a pipe a process we could not signal may still hold.
        watch_task.cancel()
        read_task.cancel()
        pids = await asyncio.to_thread(_process_tree, proc.pid)
        await asyncio.to_thread(_signal_process_tree, pids, force=True)
        raise
    finally:
        watch_task.cancel()
        if quiet_task is not None:
            quiet_task.cancel()

    return returncode


# Lines of a command's output JobRunner keeps as `last_output`. SteamCMD says
# why it failed on its last lines ("No subscription").
_RUNNER_TAIL_LINES = 200


class JobRunner:
    """Duck-types the `runner` param used by steam.download_mod_async and the
    update pipeline: run_command streams a subprocess into the job log and
    keeps its last lines in `last_output` (read by
    steam.runner_no_subscription_error), write() emits a plain-text line
    (Rich markup stripped)."""

    def __init__(self, job: Job) -> None:
        self.job = job
        self.last_output = ""

    @property
    def cancelled(self) -> bool:
        """Read by download_mod_async so a cancel stops its retry loop."""
        return self.job.cancel_event.is_set()

    async def run_command(self, cmd: list[str]) -> int:
        tail: deque[str] = deque(maxlen=_RUNNER_TAIL_LINES)

        def _keep(line: str) -> str:
            tail.append(line)
            return line

        returncode = await stream_subprocess(self.job, cmd, transform=_keep)
        self.last_output = "\n".join(tail)
        return returncode

    def write(self, text: str) -> None:
        self.job.log_line(strip_markup(text))
