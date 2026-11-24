"""Update zsm itself: the checkout the installer made, then ``uv``.

The same two steps ``install.sh`` runs when it finds an existing install
(step 4, ``git fetch`` + ``git reset --hard origin/<branch>``, and step 6,
``uv tool install --force --reinstall``), so an update from the panel or
``zsm self-update`` lands exactly where a re-run of the installer would.
The OS-package steps are left out: they need root, and a release that needs a
new package says so in its notes.

It follows the branch the checkout is on, which is the one ``install.sh
--branch`` cloned, not GitHub releases (decided in 2026 Q4). Three installs
cannot update this way and are told how they do instead: Docker (zsm is built
into the image), Windows (``uv`` cannot replace ``zsm.exe`` while the panel
runs it) and an install with no checkout (an editable dev install).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from app.shared.platform import IS_DOCKER, IS_WINDOWS

Log = Callable[[str], None]

GIT_TIMEOUT = 120
UV_TIMEOUT = 600
MAX_SUBJECTS = 20

LINUX_INSTALL = (
    "curl -fsSL https://raw.githubusercontent.com/robba86/zedstead/main/install.sh "
    "-o /tmp/zsm-install.sh && sh /tmp/zsm-install.sh"
)
WINDOWS_INSTALL = (
    'powershell -ExecutionPolicy ByPass -c "irm '
    'https://raw.githubusercontent.com/robba86/zedstead/main/install.ps1 | iex"'
)
DOCKER_UPDATE = "git pull && docker compose build && docker compose up -d"
DOCKER_DOCS = "https://zedstead.com/docs/docker/#updating-zsm"

# Why an install cannot update itself, and what it runs instead.
HOW = {
    "docker": (
        "Zedstead is built into the Docker image. Pull the new code and rebuild the image; "
        "the data volumes are kept.",
        DOCKER_UPDATE,
    ),
    "windows": (
        "On Windows Zedstead cannot replace itself while the app runs. Close the app, "
        "then run the installer again in PowerShell.",
        WINDOWS_INSTALL,
    ),
    "no_checkout": (
        "This install was not made by the installer, so there is no checkout to update. "
        "Run the installer to switch to one, or update your own checkout.",
        LINUX_INSTALL,
    ),
}

# The installer may clone with credentials in the URL (a private repo cloned
# unattended); install.sh's redact_urls() hides them the same way.
_URL_CREDENTIALS = re.compile(r"://[^/@\s]*@")


class SelfUpdateError(RuntimeError):
    """A step failed or was refused; the message is the sentence to show."""


@dataclass
class UpdateInfo:
    supported: bool
    reason: str = ""  # a key of HOW when not supported
    message: str = ""
    command: str = ""
    source_dir: str = ""
    branch: str = ""
    current: str = ""
    latest: str = ""
    behind: int = 0
    subjects: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def source_dir() -> Path:
    """Where the installer keeps its checkout on this platform."""
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "zsm" / "source"
    return Path.home() / ".local" / "share" / "zsm"


def redact(text: str) -> str:
    return _URL_CREDENTIALS.sub("://***@", text)


def _git_env() -> dict[str, str]:
    """git must fail rather than ask: there is no terminal behind a job."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    return env


def _git(directory: Path, *args: str, timeout: int = GIT_TIMEOUT) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(directory), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_git_env(),
        )
    except FileNotFoundError as exc:
        raise SelfUpdateError("git is not installed on this machine.") from exc
    except subprocess.TimeoutExpired as exc:
        raise SelfUpdateError(f"git {args[0]} did not finish in {timeout} seconds.") from exc
    if proc.returncode != 0:
        detail = redact((proc.stderr or proc.stdout).strip()) or f"exit {proc.returncode}"
        raise SelfUpdateError(f"git {args[0]} failed: {detail}")
    return proc.stdout.strip()


def _unsupported(reason: str, directory: Path) -> UpdateInfo:
    message, command = HOW[reason]
    return UpdateInfo(
        supported=False, reason=reason, message=message, command=command,
        source_dir=str(directory),
    )


def _blocker(directory: Path) -> str:
    """The HOW key that stops this install updating itself, or ""."""
    if IS_DOCKER:
        return "docker"
    if not (directory / ".git").is_dir():
        return "no_checkout"
    return ""


def check(fetch: bool = True) -> UpdateInfo:
    """What an update would bring. Fetches from the remote unless told not to.

    A Windows install is still checked, so its card can say whether there is
    anything new; it only cannot apply it.
    """
    directory = source_dir()
    reason = _blocker(directory)
    if reason:
        return _unsupported(reason, directory)
    info = _unsupported("windows", directory) if IS_WINDOWS else UpdateInfo(
        supported=True, source_dir=str(directory)
    )
    try:
        info.branch = _git(directory, "rev-parse", "--abbrev-ref", "HEAD")
        if fetch:
            _git(directory, "fetch", "origin", info.branch)
        upstream = f"origin/{info.branch}"
        info.current = _git(directory, "rev-parse", "--short", "HEAD")
        info.latest = _git(directory, "rev-parse", "--short", upstream)
        info.behind = int(_git(directory, "rev-list", "--count", f"HEAD..{upstream}") or 0)
        if info.behind:
            log = _git(directory, "log", "--format=%s", f"-n{MAX_SUBJECTS}", f"HEAD..{upstream}")
            info.subjects = [line for line in log.splitlines() if line.strip()]
    except SelfUpdateError as exc:
        info.error = str(exc)
    return info


def _uv() -> str:
    found = shutil.which("uv")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "uv"
    if fallback.is_file():
        return str(fallback)
    raise SelfUpdateError(
        "uv was not found. Run the installer once more; it installs uv and Zedstead together."
    )


def _stream(argv: list[str], log: Log, timeout: int) -> None:
    """Run *argv*, handing each output line to *log*; raise on a failure."""
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=_git_env(),
        )
    except FileNotFoundError as exc:
        raise SelfUpdateError(f"{argv[0]} is not installed on this machine.") from exc
    assert proc.stdout is not None
    for line in proc.stdout:
        line = redact(line.rstrip())
        if line:
            log(line)
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        raise SelfUpdateError(f"{Path(argv[0]).name} did not finish in {timeout} seconds.") from exc
    if code != 0:
        raise SelfUpdateError(f"{Path(argv[0]).name} {argv[1]} failed (exit {code}).")


def apply(log: Log) -> dict:
    """Bring the checkout to its branch's newest commit and reinstall.

    Returns ``{before, after, changed}``; ``changed`` is False when there was
    nothing new, in which case nothing is reinstalled.
    """
    directory = source_dir()
    reason = _blocker(directory) or ("windows" if IS_WINDOWS else "")
    if reason:
        message, command = HOW[reason]
        raise SelfUpdateError(f"{message} {command}")
    branch = _git(directory, "rev-parse", "--abbrev-ref", "HEAD")
    if _git(directory, "status", "--porcelain", "--untracked-files=no"):
        raise SelfUpdateError(
            f"{directory} has local changes, and updating would throw them away. "
            "Commit or remove them first, or run the installer, which overwrites them."
        )
    before = _git(directory, "rev-parse", "--short", "HEAD")
    log(f"Fetching {branch} into {directory}")
    _git(directory, "fetch", "origin", branch)
    after = _git(directory, "rev-parse", "--short", f"origin/{branch}")
    if after == before:
        log(f"Already up to date ({before}).")
        return {"before": before, "after": after, "changed": False}
    _git(directory, "reset", "--hard", f"origin/{branch}")
    log(f"Checkout moved from {before} to {after}.")
    log("Reinstalling with uv")
    _stream([_uv(), "tool", "install", "--force", "--reinstall", str(directory)], log, UV_TIMEOUT)
    log(f"Zedstead updated to {after}.")
    return {"before": before, "after": after, "changed": True}
