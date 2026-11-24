"""Platform detection."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_ROOT = (not IS_WINDOWS) and hasattr(os, "geteuid") and os.geteuid() == 0
IS_DOCKER = Path("/.dockerenv").exists() or os.environ.get("ZSM_DOCKER") == "1"


def default_bind_host(config=None, env=None) -> str:
    """Default bind host for local web servers (spawn editor, ``zsm web``).

    Explicit ``config.web_bind_host`` wins — set it (e.g. ``"0.0.0.0"``) to make
    the choice durable across restarts and env-stripped shells (tmux, systemd,
    sudo) where SSH auto-detect fails. Otherwise ``0.0.0.0`` in Docker or over
    SSH (so a remote/containerized admin can reach it), ``127.0.0.1`` otherwise
    — don't expose a freshly-started dev server to the LAN by default.

    *env* defaults to this process's environment. ``panel_unit`` passes ``{}``
    to ask what a systemd-started ``zsm web`` will pick, since a unit has no
    SSH session.
    """
    if config is not None and getattr(config, "web_bind_host", ""):
        return config.web_bind_host
    return "0.0.0.0" if auto_public_bind(config, env) else "127.0.0.1"


def auto_public_bind(config=None, env=None) -> str:
    """Why ``default_bind_host`` picks ``0.0.0.0`` by itself: ``"docker"``,
    ``"ssh"``, or ``""`` when it does not (``web_bind_host`` is set, or the
    answer is loopback). ``zsm web`` reads it to turn on self-signed TLS for a
    bind nobody asked for (plans/68 NET-1)."""
    if config is not None and getattr(config, "web_bind_host", ""):
        return ""
    if IS_DOCKER:
        return "docker"
    env = os.environ if env is None else env
    if env.get("SSH_CLIENT") or env.get("SSH_TTY") or env.get("SSH_CONNECTION"):
        return "ssh"
    return ""


def has_sudo() -> bool:
    """True if a ``sudo`` binary is on PATH."""
    return shutil.which("sudo") is not None


def has_systemd() -> bool:
    """True if systemd is the running init system.

    ``/run/systemd/system`` exists only when the host was booted with systemd,
    so installing a unit file + ``systemctl daemon-reload`` is meaningful.
    False in containers / non-systemd boxes, where unit install should be
    skipped rather than attempted (it would just fail writing to
    ``/etc/systemd/system``).
    """
    return (not IS_WINDOWS) and Path("/run/systemd/system").is_dir()


def sudo_prefix() -> list[str]:
    """Command prefix for privileged operations.

    Empty on Windows, when already running as root, or when ``sudo`` is not
    available — in those cases privileged commands are run directly (best
    effort; a non-root user without sudo will get a permission error from the
    command itself rather than a crash on a missing ``sudo``).
    """
    if IS_WINDOWS or IS_ROOT or not has_sudo():
        return []
    return ["sudo"]


def needs_sudo_auth() -> bool:
    """True only when we will actually invoke ``sudo`` (a password may be needed)."""
    return sudo_prefix() == ["sudo"]


def sudo_available_noninteractive() -> bool:
    """True if a privileged command can run without prompting for a password.

    Always true when no sudo is actually needed (``sudo_prefix()`` empty).
    Otherwise probes ``sudo -n true`` — the check web endpoints must use
    before shelling out to a privileged command, since a web process has no
    tty for sudo to prompt on (it would just fail, or hang if an askpass
    helper is misconfigured).
    """
    if not needs_sudo_auth():
        return True
    try:
        return subprocess.run(
            ["sudo", "-n", "true"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ).returncode == 0
    except FileNotFoundError:
        return False
