"""Move a pre-instances install into `instances/main` (plans/44 §2 Q3, §4a).

Runs once, from the root group, before any subcommand: when a legacy
`config.json` exists and `instances.json` names no instance. It **copies**, so
the originals stay as they were until a later release removes them: a
migration that failed after deleting its source is the one outcome nobody can
recover from. The one change is a mode: both copies of `config.json` end up
0600 (`_owner_only`).

- **What**: the `InstancePaths` members that exist in the legacy root. Nothing
  machine-wide, and no file zsm does not know.
- **How**: the write-once directories are hardlinked (a backup zip is never
  rewritten, and its sidecar is replaced atomically, which gives it a new
  inode), everything else is copied. `backups/` alone was 1.8 GB of a 1.9 GB
  config dir on the dev box. A member that is a symlink stays one, pointing
  where it did.
- **Commit point**: everything is staged in `instances/.main.migrating`, moved
  to `instances/main` with one `os.replace`, and only then is the registry
  written. Until the registry names `main`, every command reads the legacy
  config, so a leftover staging dir or an `instances/main` with no registry is
  an interrupted run: it is removed and the migration is done again.
- **Never fatal**: any failure removes what was staged, writes no registry and
  lets the command run on the legacy layout. Q3 rejected "refuse to start"
  because it leaves the panel down.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Callable

from app.server.instances import (
    DEFAULT_INSTANCE,
    InstanceRegistry,
    ServerInstance,
    add_instance,
    load_registry,
    save_registry,
)
from app.shared import paths
from app.shared.paths import InstancePaths
from app.shared.platform import IS_WINDOWS

STAGING_NAME = f".{DEFAULT_INSTANCE}.migrating"
LOCK_NAME = ".migrate.lock"
# A lock this old belongs to a process that died; nothing else holds it this long.
STALE_LOCK_SECONDS = 600
# Hardlinked rather than copied: every file in them is written once.
LINKED_MEMBERS = frozenset({"backups_dir", "bundles_dir", "mod_backups_dir", "spawn_test_dir"})
# Free space asked for on top of the bytes that will be copied.
SPACE_MARGIN = 0.10


@dataclass(frozen=True)
class Outcome:
    """What happened. `message` is empty when there was nothing to say."""

    status: str  # "not_needed" | "done" | "busy" | "refused" | "failed"
    message: str = ""


NOT_NEEDED = Outcome("not_needed")


def members() -> list[str]:
    """Every per-instance file, by its `InstancePaths` name."""
    return [name for name, value in vars(InstancePaths).items() if isinstance(value, property)]


def legacy_paths() -> InstancePaths:
    return InstancePaths(paths.instance_root(""))


def pending() -> bool:
    """A legacy `config.json` exists and no instance has been registered."""
    return legacy_paths().config_file.is_file() and not load_registry().instances


def migrate(*, panel_running: Callable[[], bool] | None = None) -> Outcome:
    """Move the legacy install into `instances/main` if it has not been yet.

    *panel_running* is injectable for tests; by default it looks for another
    `zsm web` in the process table.
    """
    if not pending():
        return NOT_NEEDED
    instances = paths.instances_dir()
    lock = instances / LOCK_NAME
    try:
        instances.mkdir(parents=True, exist_ok=True)
        acquired = _acquire(lock)
    except OSError as exc:  # a read-only config dir: never fatal either
        return _failed(exc, instances / DEFAULT_INSTANCE, legacy_paths().root)
    if not acquired:
        # Another process is migrating right now. The registry is not written
        # yet, so the legacy layout is still the consistent one to run on.
        return Outcome("busy", "Another Zedstead process is moving this install into "
                               f"{instances / DEFAULT_INSTANCE}; this run uses the old location.")
    try:
        return _migrate_locked(instances, panel_running or other_panel_running)
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def _migrate_locked(instances: Path, panel_running: Callable[[], bool]) -> Outcome:
    if not pending():  # finished by the process that held the lock before us
        return NOT_NEEDED
    legacy = legacy_paths()
    if panel_running():
        # An old-build panel keeps writing the legacy files after they are
        # copied, and those writes would be lost when it restarts (D3).
        return Outcome("refused", "zsm web is still running from before the upgrade, so this "
                                  f"install stays in {legacy.root} for now. Restart the app "
                                  "to finish the upgrade; it moves the install when it starts.")
    staging = instances / STAGING_NAME
    target = instances / DEFAULT_INSTANCE
    try:
        # Only an interrupted run leaves these: nothing else writes there
        # before the registry exists.
        for leftover in (staging, target):
            _remove(leftover)
        plan = _plan(legacy, staging)
        _check_space(plan, instances)
        staging.mkdir()
        for src, dst, link in plan:
            _place(src, dst, link)
        _owner_only(InstancePaths(staging).config_file)
        os.replace(staging, target)
        save_registry(add_instance(InstanceRegistry(), ServerInstance(DEFAULT_INSTANCE)))
    except Exception as exc:  # noqa: BLE001 — never fatal, see the module docstring
        for partial in (staging, target):
            try:
                _remove(partial)
            except OSError:
                pass  # the next run removes it: no registry means interrupted
        return _failed(exc, target, legacy.root)
    # Nothing saves the original again, so it would keep an old 0644 for good.
    _owner_only(legacy.config_file)
    return Outcome("done", f"Moved this install into {target} (copied; the originals in "
                           f"{legacy.root} stay until a later release removes them).")


def _failed(exc: BaseException, target: Path, legacy_root: Path) -> Outcome:
    return Outcome("failed", f"Could not move this install into {target} ({exc}). It keeps "
                             f"running from {legacy_root}; the next start tries again.")


# --- the copy -----------------------------------------------------------------


def _plan(legacy: InstancePaths, staging: Path) -> list[tuple[Path, Path, bool]]:
    """(source, destination, hardlink?) for every member the legacy root has."""
    into = InstancePaths(staging)
    return [
        (getattr(legacy, name), getattr(into, name), name in LINKED_MEMBERS)
        for name in members()
        if os.path.lexists(getattr(legacy, name))
    ]


def _place(src: Path, dst: Path, link: bool) -> None:
    if src.is_symlink():
        os.symlink(os.readlink(src), dst, target_is_directory=src.is_dir())
    elif src.is_dir():
        shutil.copytree(src, dst, symlinks=True, copy_function=_link_or_copy if link else shutil.copy2)
    else:
        shutil.copy2(src, dst)


def _owner_only(path: Path) -> None:
    """``config.json`` at 0600 (plans/68 SEC-11).

    It holds the RCon password, the Discord webhook URL, the Steam API key,
    the zedstead.com listing key and the panel's password hash. ``DayzConfig.save``
    has written it 0600 since ``c081c521``, but a file last saved before that
    is 0644, and ``copy2`` keeps the mode. Only the mode changes, never the
    contents. Best effort: a filesystem without modes must not stop the move,
    and a symlink is left alone (its target is not zsm's to change).
    """
    if IS_WINDOWS or path.is_symlink():
        return
    try:
        if path.stat().st_mode & 0o077:
            os.chmod(path, 0o600)
    except OSError:
        pass


def _link_or_copy(src: str, dst: str) -> None:
    """A hardlink shares the bytes and costs no space; a filesystem without
    them, or a file on another one, gets a copy instead."""
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _check_space(plan: list[tuple[Path, Path, bool]], instances: Path) -> None:
    """Refuse before copying anything when the copies would not fit."""
    device = os.stat(instances).st_dev
    needed = 0
    for src, _dst, link in plan:
        if src.is_symlink():
            continue
        for path in _files(src):
            st = path.lstat()
            if not (link and st.st_dev == device):
                needed += st.st_size
    needed = int(needed * (1 + SPACE_MARGIN))
    free = shutil.disk_usage(instances).free
    if needed > free:
        raise OSError(f"{_mb(needed)} MB needed, {_mb(free)} MB free")


def _files(root: Path):
    if not root.is_dir():
        yield root
        return
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            path = Path(dirpath) / name
            if not path.is_symlink():
                yield path


def _mb(n: int) -> int:
    return -(-n // (1024 * 1024))


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


# --- one at a time --------------------------------------------------------------


def _acquire(lock: Path) -> bool:
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime < STALE_LOCK_SECONDS:
                    return False
                lock.unlink()
            except FileNotFoundError:
                pass
            continue
        with os.fdopen(fd, "w") as f:
            f.write(f"{os.getpid()}\n")
        return True
    return False


# --- an old panel ---------------------------------------------------------------

# Options the root group takes before the subcommand, and whether each takes a value.
_ROOT_OPTIONS = {"-y": False, "--yes": False, "-v": False, "--verbose": False, "--instance": True}
_ZSM_NAMES = ("zsm", "zsm.exe", "zsm-script.py")


def _launched_args(argv: list[str]) -> list[str] | None:
    """What follows `zsm` in *argv*, or None when it is not a zsm process.

    Only the executable, or the script or `-m app` an interpreter was given,
    counts: an editor or a `cd` with a path ending in `zsm` is not one.
    """
    if not argv:
        return None
    name = _basename(argv[0])
    if name in _ZSM_NAMES:
        return argv[1:]
    if not name.startswith("python"):
        return None
    i = 1
    while i < len(argv) and argv[i].startswith("-") and argv[i] != "-m":
        if argv[i] == "-c":
            return None
        i += 1
    if argv[i:i + 2] == ["-m", "app"]:
        return argv[i + 2:]
    if i < len(argv) and _basename(argv[i]) in _ZSM_NAMES:
        return argv[i + 1:]
    return None


def _basename(arg: str) -> str:
    # Either separator: the Windows process table is read on Windows, but
    # the tests that pin it run everywhere.
    return PureWindowsPath(arg).name.lower()


def is_panel_argv(argv: list[str]) -> bool:
    """True for `zsm web`, a bare `zsm` (which opens the panel) and
    `python -m app web` (the panel's unit), with the root group's options
    anywhere before the subcommand."""
    rest = _launched_args(argv)
    if rest is None:
        return False
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg in ("--help", "--version"):
            return False
        if arg.startswith("--instance="):
            i += 1
        elif arg in _ROOT_OPTIONS:
            i += 2 if _ROOT_OPTIONS[arg] else 1
        else:
            return arg == "web"
    return True


def other_panel_running() -> bool:
    """Another `zsm web` is alive. Our own process and its ancestors (a `uv
    run`, the Windows launcher) never count."""
    try:
        table = _process_table()
    except Exception:  # noqa: BLE001 — cannot tell: do not block the upgrade on it
        return False
    mine = _ancestry(os.getpid(), {pid: ppid for pid, ppid, _argv in table})
    return any(pid not in mine and is_panel_argv(argv) for pid, _ppid, argv in table)


def _ancestry(pid: int, parents: dict[int, int]) -> set[int]:
    seen: set[int] = set()
    while pid and pid not in seen:
        seen.add(pid)
        pid = parents.get(pid, 0)
    return seen


def _process_table() -> list[tuple[int, int, list[str]]]:
    return _windows_processes() if IS_WINDOWS else _linux_processes()


def _linux_processes() -> list[tuple[int, int, list[str]]]:
    rows = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
            stat = (entry / "stat").read_text()
        except OSError:
            continue  # exited, or not ours to read
        # The name in `stat` is parenthesised and may itself contain spaces.
        ppid = int(stat.rsplit(")", 1)[1].split()[1])
        rows.append((int(entry.name), ppid, [a.decode(errors="replace") for a in raw.split(b"\0") if a]))
    return rows


def _windows_processes() -> list[tuple[int, int, list[str]]]:
    import json
    import shlex

    ps = ("Get-CimInstance Win32_Process | "
          "Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress")
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True, text=True, timeout=30,
    ).stdout
    data = json.loads(out or "[]")
    rows = []
    for row in data if isinstance(data, list) else [data]:
        line = row.get("CommandLine") or ""
        try:
            argv = shlex.split(line, posix=False)
        except ValueError:
            argv = line.split()
        rows.append((int(row["ProcessId"]), int(row.get("ParentProcessId") or 0),
                     [a.strip('"') for a in argv]))
    return rows


def report(outcome: Outcome) -> None:
    """One line on stderr, so a command's own output (JSON included) stays clean."""
    if outcome.message:
        print(outcome.message, file=sys.stderr)
