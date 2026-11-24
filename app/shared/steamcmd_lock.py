"""One SteamCMD at a time, across every zsm process of this user.

Two SteamCMD processes sharing one STEAMROOT corrupt the cached login token
and the workshop manifest (root CLAUDE.md). The web panel already refuses to
run two of its own (`WebState.steam_probe`, `JobManager.busy`), but that guard
lives in one process. Nothing stopped a second process: a scheduled
`zsm update` beside a panel job, two panels (one per instance, the way to
run a second instance until E2), or two instances' update tasks on the same
cron, which `zsm instances clone` sets up by copying the schedule.

So every spawn of the SteamCMD binary holds this lock: an advisory `flock` on
`<config dir>/steamcmd.lock`. On Linux every SteamCMD of a user shares
`~/Steam` whatever its install dir, and the config dir is per user too, so
one lock per config dir is the right scope. The kernel drops a `flock` when
its process dies, so a crash can never leave it held.

The lock is taken around one SteamCMD run, never around a whole operation:
one process never holds it twice, so it cannot deadlock itself, and another
process may run its SteamCMD between two of ours. That is safe; only two at
the same moment are not.

Windows: not locked (no `fcntl`). Instances on Windows are not separate yet
anyway (app/server/CLAUDE.md).
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import time
from collections.abc import Callable
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

log = logging.getLogger(__name__)

LOCK_NAME = "steamcmd.lock"
POLL_SECONDS = 1.0
# How long a timed-out run gets to exit on SIGTERM before SIGKILL, and then
# how long SIGKILL gets before the caller goes on (a process stuck in the
# kernel cannot be waited out).
END_GRACE_SECONDS = 10.0


class SteamcmdBusy(RuntimeError):
    """Another process is running SteamCMD and the caller would not wait."""


def lock_path() -> Path:
    from app.shared import paths

    return paths.config_dir() / LOCK_NAME


def is_steamcmd(cmd: list[str]) -> bool:
    """Is *cmd* a SteamCMD run? `steamcmd.sh` on Linux, `steamcmd.exe` on Windows."""
    return bool(cmd) and Path(str(cmd[0])).name.lower().startswith("steamcmd")


def holder() -> str:
    """Who holds the lock, as its holder wrote it: ``pid N: <command>``."""
    try:
        return lock_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def busy_message() -> str:
    who = holder()
    return "Another SteamCMD is running" + (f" ({who})" if who else "") + "."


def _open() -> int | None:
    """The lock file's descriptor, or None to run unlocked: no `fcntl`, or a
    config dir that cannot be written. The lock is a safety net; failing to
    make one must not stop an install that worked before it existed."""
    if fcntl is None:
        return None
    path = lock_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        log.warning("Cannot open %s; running SteamCMD without the lock", path, exc_info=True)
        return None


def _try(fd: int) -> bool:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _claim(fd: int, label: str) -> None:
    """Say who holds it, for a waiter's message. Best effort."""
    try:
        os.ftruncate(fd, 0)
        os.pwrite(fd, f"pid {os.getpid()}: {label}\n".encode(), 0)
    except OSError:
        pass


def _release(fd: int) -> None:
    try:
        os.ftruncate(fd, 0)
    except OSError:
        pass
    os.close(fd)  # closing the descriptor drops the flock


def _label(cmd: list[str] | None) -> str:
    """The binary and its ``+`` commands, nothing else: the arguments carry
    the Steam account name. (The web login's password and Guard code are in
    its ``+runscript`` file, never in the arguments.) A waiting job prints
    this, and a viewer can read job logs."""
    if not cmd:
        return "zsm"
    return " ".join([Path(str(cmd[0])).name, *(str(c) for c in cmd[1:] if str(c).startswith("+"))])


@contextmanager
def hold(
    cmd: list[str] | None = None,
    *,
    wait: float | None = None,
    on_wait: Callable[[str], None] | None = None,
):
    """Hold the lock for one SteamCMD run.

    *wait* None waits as long as it takes; a number of seconds raises
    `SteamcmdBusy` after that long (0: do not wait at all). *on_wait* is told
    once, with who holds it, when there is a wait.
    """
    fd = _open()
    if fd is None:
        yield
        return
    try:
        if not _try(fd):
            if wait is not None and wait <= 0:
                raise SteamcmdBusy(busy_message())
            if on_wait is not None:
                on_wait(f"Waiting for another SteamCMD to finish ({holder() or 'unknown'})...")
            deadline = None if wait is None else time.monotonic() + wait
            while not _try(fd):
                if deadline is not None and time.monotonic() >= deadline:
                    raise SteamcmdBusy(busy_message())
                time.sleep(POLL_SECONDS)
    except BaseException:
        os.close(fd)
        raise
    _claim(fd, _label(cmd))
    try:
        yield
    finally:
        _release(fd)


@asynccontextmanager
async def hold_async(
    cmd: list[str] | None = None,
    *,
    on_wait: Callable[[str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
):
    """`hold` for the event loop: waits without blocking it, and gives up with
    `SteamcmdBusy` when *cancelled* says so (a web job's Cancel). Not
    `CancelledError`: nobody cancelled the task, and asyncio code tells a
    real cancellation from a foreign one (`rcon_ops.is_foreign_cancel`)."""
    fd = _open()
    if fd is None:
        yield
        return
    try:
        if not _try(fd):
            if on_wait is not None:
                on_wait(f"Waiting for another SteamCMD to finish ({holder() or 'unknown'})...")
            while not _try(fd):
                if cancelled is not None and cancelled():
                    raise SteamcmdBusy("Cancelled while waiting for another SteamCMD.")
                await asyncio.sleep(POLL_SECONDS)
    except BaseException:
        os.close(fd)
        raise
    _claim(fd, _label(cmd))
    try:
        yield
    finally:
        _release(fd)


def run_to_end(
    cmd: list[str], *, timeout: float, capture: bool = False
) -> subprocess.CompletedProcess:
    """`subprocess.run(cmd, stdin=DEVNULL, timeout=...)` for a SteamCMD run held under `hold`.

    ``steamcmd.sh`` starts the real SteamCMD as its child, and a timeout in
    `subprocess.run` kills only the wrapper: the child kept running after the
    caller released the lock, so a second SteamCMD could start beside it. Here
    the run gets its own process group, and a timeout (or any exception)
    ends the whole group and returns only once the group is gone. Raises
    ``subprocess.TimeoutExpired`` with the output so far, as `subprocess.run`
    does. *capture* pipes stdout and stderr; otherwise both go to DEVNULL.

    Windows: no process group is made and only the wrapper is killed, as
    before; the lock does not exist there either.
    """
    from app.shared.platform import IS_WINDOWS

    out = subprocess.PIPE if capture else subprocess.DEVNULL
    proc = subprocess.Popen(
        cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
        start_new_session=not IS_WINDOWS,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        end_group(proc, IS_WINDOWS)
        stdout, stderr = proc.communicate()
        raise subprocess.TimeoutExpired(cmd, timeout, output=stdout, stderr=stderr) from None
    except BaseException:
        end_group(proc, IS_WINDOWS)
        proc.communicate()
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def end_group(proc: subprocess.Popen, windows: bool) -> None:
    """SIGTERM *proc*'s process group, SIGKILL it after `END_GRACE_SECONDS`,
    and wait until no member is left."""
    if windows:
        proc.kill()
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        if _group_gone(proc, END_GRACE_SECONDS):
            return
    log.warning("SteamCMD process group %s did not exit after SIGKILL", proc.pid)


def _group_gone(proc: subprocess.Popen, within: float) -> bool:
    deadline = time.monotonic() + within
    while True:
        proc.poll()  # reaps the wrapper, which would stay in the group as a zombie
        if not _group_alive(proc.pid):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _group_alive(pgid: int) -> bool:
    """Is any process of group *pgid* still running? An orphaned member that
    has exited stays a zombie until init reaps it, and `killpg(pgid, 0)`
    still finds it; it holds no file, so where /proc says so it counts as gone."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    proc_dir = Path("/proc")
    if not proc_dir.is_dir():
        return True
    for entry in proc_dir.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            # The comm field is in parentheses and may hold spaces.
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        if fields[0] != "Z" and fields[2] == str(pgid):
            return True
    return False
